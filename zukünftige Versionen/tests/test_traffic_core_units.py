"""Regression tests for normalized ADS-B fields and useful error diagnostics."""

import math
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import app_logging
import peer_net
import traffic_core


def test_tcas_live_poll_uses_fresh_player_position_and_regular_poll_uses_map_view():
    requests = []

    def fetcher(lat, lon, radius_km, bounds=None):
        requests.append((lat, lon, radius_km, bounds))
        return []

    engine = traffic_core.TrafficEngine(fetcher=fetcher)
    engine.options["center"] = [64.774125, 148.359375]
    engine.options["bounds"] = [65.0, 64.0, 149.0, 147.0]
    engine.set_own_position(52.5, 13.4, 1200.0, 90.0)

    engine.options["tcas"] = True
    engine._tick()
    assert requests[-1] == (52.5, 13.4, engine.TCAS_RADIUS_KM, None)

    engine.options["tcas"] = False
    engine._tick()
    assert requests[-1] == (
        64.774125, 148.359375, 250.0,
        [65.0, 64.0, 149.0, 147.0],
    )


def test_adsb_dst_is_exposed_in_kilometers_and_keeps_source_nautical_miles():
    info = traffic_core.parse_adsb({"flight": "DLH123", "dst": 12.0})

    assert math.isclose(info["dst"], 12.0 * 1.852, rel_tol=0.0, abs_tol=1e-9)
    assert info["dst_nm"] == 12.0


def test_invalid_adsb_dst_does_not_become_a_distance():
    for invalid in ("not-a-number", float("nan"), float("inf"), -1):
        info = traffic_core.parse_adsb({"flight": "DLH123", "dst": invalid})
        assert info["dst"] is None
        assert info["dst_nm"] is invalid


def test_opensky_bbox_is_local_valid_and_uses_documented_credit_tiers():
    equatorial = traffic_core._opensky_bounds(0.0, 0.0, 250.0)
    european = traffic_core._opensky_bounds(50.0, 8.0, 250.0)
    polar = traffic_core._opensky_bounds(89.0, 179.0, 250.0)

    assert traffic_core._opensky_bbox_credit_cost(equatorial) == 1
    assert traffic_core._opensky_bbox_credit_cost(european) == 2
    assert traffic_core._opensky_bbox_credit_cost((80.0, -80.0, 180.0, -180.0)) == 4
    assert -90.0 <= polar[1] <= polar[0] <= 90.0
    assert polar[2:] == (180.0, -180.0)
    assert traffic_core._opensky_bounds(50.0, 8.0, 10_000.0) == european


def test_error_state_tracker_logs_first_failure_and_recovery_once():
    clock = SimpleNamespace(now=10.0)
    tracker = app_logging.ErrorStateTracker(clock=lambda: clock.now)

    with patch.object(app_logging, "log_action") as log_action:
        assert tracker.record_failure("map", "map.poll_failed", "offline")
        clock.now = 15.0
        assert not tracker.record_failure("map", "map.poll_failed", "still offline")
        clock.now = 22.0
        assert tracker.record_recovery("map", "map.poll_recovered")
        assert not tracker.record_recovery("map", "map.poll_recovered")

    assert [call.args[0] for call in log_action.call_args_list] == [
        "map.poll_failed", "map.poll_recovered"
    ]
    recovered = log_action.call_args_list[-1]
    assert recovered.kwargs["failure_duration_sec"] == 12.0
    assert recovered.kwargs["failure_count"] == 2
    assert recovered.kwargs["first_error"] == "offline"
    assert recovered.kwargs["last_error"] == "still offline"


def test_error_state_tracker_captures_first_response_after_transport_error():
    tracker = app_logging.ErrorStateTracker(clock=lambda: 1.0)
    first_response = {"status_code": 503, "body_excerpt": "service down"}

    with patch.object(app_logging, "log_action") as log_action:
        assert tracker.record_failure("proxy", "proxy.failed", "connection reset")
        assert not tracker.record_failure(
            "proxy", "proxy.failed", "HTTP 503",
            response=first_response,
        )
        assert not tracker.record_failure(
            "proxy", "proxy.failed", "HTTP 503 again",
            response={"status_code": 503, "body_excerpt": "new body"},
        )
        assert tracker.record_recovery("proxy", "proxy.recovered")

    failures = [call for call in log_action.call_args_list
                if call.args and call.args[0] == "proxy.failed"]
    assert len(failures) == 2
    assert failures[0].kwargs["error"] == "connection reset"
    assert "response" not in failures[0].kwargs
    assert failures[1].kwargs["response"] == first_response


def test_source_registry_logs_when_failed_source_recovers():
    registry = traffic_core.SourceRegistry()
    with patch.object(traffic_core, "log_action") as log_action:
        registry.report("test-source", False, 503, "upstream down", now=100.0)
        registry.report("test-source", False, 503, "still down", now=105.0)
        registry.report("test-source", True, 200, "ok", now=112.0)

    recoveries = [call for call in log_action.call_args_list
                  if call.args and call.args[0] == "source.error_recovered"]
    assert len(recoveries) == 1
    assert recoveries[0].kwargs["failure_duration_sec"] == 12.0
    assert recoveries[0].kwargs["previous_status_code"] == 503
    assert recoveries[0].kwargs["previous_detail"] == "upstream down"
    assert not any(key.startswith("_")
                   for key in registry.snapshot()["test-source"])


def test_source_registry_try_request_slot_is_nonblocking_and_spaced():
    clock = SimpleNamespace(now=100.0)
    registry = traffic_core.SourceRegistry(clock=lambda: clock.now)

    assert registry.try_request_slot("opensky-direct", 900.0) == 0.0
    clock.now = 103.0
    assert registry.try_request_slot("opensky-direct", 900.0) == 897.0
    clock.now = 1000.0
    assert registry.try_request_slot("opensky-direct", 900.0) == 0.0
    assert traffic_core.DIRECT_OPENSKY_MIN_INTERVAL == 900.0


def test_response_debug_payload_is_bounded_and_contains_received_body():
    response = SimpleNamespace(status_code=502, text="upstream-error-body")
    details = app_logging.response_debug_payload(response, max_chars=8)

    assert details == {
        "status_code": 502,
        "body_excerpt": "upstream",
        "body_chars": 19,
        "truncated": True,
    }


def test_invalid_opensky_rows_are_sampled_with_the_received_response():
    valid_state = [
        "abc123", "DLH123", None, 0, 1_700_000_000, 8.6, 50.1, 1000.0,
        False, 100.0, 90.0, 0.0, None, 1100.0, "1234", False, 0,
    ]
    geometric_only = list(valid_state)
    geometric_only[0] = "def456"
    geometric_only[7] = None
    geometric_only[13] = 900.0
    ground_without_altitudes = list(valid_state)
    ground_without_altitudes[0] = "ghi789"
    ground_without_altitudes[7] = None
    ground_without_altitudes[8] = True
    ground_without_altitudes[13] = None
    invalid_state = list(valid_state)
    invalid_state[5] = 500.0
    response = SimpleNamespace(
        status_code=200,
        text='{"states":[["abc123","DLH123", ...], ["bad-position"]]}',
    )

    with patch.object(traffic_core, "log_action") as log_action:
        aircraft = traffic_core._opensky_convert(
            [valid_state, geometric_only, ground_without_altitudes,
             invalid_state, ["too-short"]], response=response
        )

    assert len(aircraft) == 3
    assert aircraft[0]["flight"] == "DLH123"
    assert aircraft[0]["alt_baro"] == 3281
    assert aircraft[0]["alt_geom"] == 3609
    assert aircraft[1]["alt_baro"] is None
    assert aircraft[1]["alt_geom"] == 2953
    assert aircraft[2]["alt_baro"] == "ground"
    assert aircraft[2]["alt_geom"] is None
    invalid_events = [call for call in log_action.call_args_list
                      if call.args and call.args[0] == "traffic.opensky_invalid_state_rows"]
    assert len(invalid_events) == 1
    details = invalid_events[0].kwargs
    assert details["rejected_count"] == 2
    assert [sample["index"] for sample in details["rejected_samples"]] == [3, 4]
    assert details["response"]["body_excerpt"] == response.text


def test_invalid_adsb_response_logs_the_body_received_by_the_client():
    body = '{"aircraft":[{"flight":"unexpected"}]}'

    class BadResponse:
        status_code = 200
        text = body

        def json(self):
            return {"aircraft": [{"flight": "unexpected"}]}

        def raise_for_status(self):
            return None

    class FakeSources:
        @staticmethod
        def cooling(_name):
            return False

        @staticmethod
        @contextmanager
        def request_slot(_name, _min_interval=1.0):
            yield

        @staticmethod
        def try_request_slot(_name, _min_interval=1.0):
            return 0.0

        @staticmethod
        def report(*_args, **_kwargs):
            return None

    class FakeRequests:
        @staticmethod
        def get(*_args, **_kwargs):
            return BadResponse()

    with patch.object(traffic_core, "SOURCES", FakeSources()):
        with patch.object(traffic_core, "requests", FakeRequests()):
            with patch.object(app_logging, "log_action") as log_action:
                result = traffic_core.fetch_direct(50.0, 8.0, 80)

    assert result is None


def _restore_runtime_config(previous, blocked_until):
    with traffic_core._RUNTIME_CONFIG_LOCK:
        traffic_core._RUNTIME_CONFIG.clear()
        traffic_core._RUNTIME_CONFIG.update(previous)
    with traffic_core._PLANETRACK_BLOCK_LOCK:
        traffic_core._PLANETRACK_BLOCKED_UNTIL = blocked_until


def test_disabled_proxy_preferences_keep_the_saved_endpoint_for_the_settings_dialog():
    previous = dict(traffic_core._RUNTIME_CONFIG)
    try:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LTU_PROXY_URL", None)
            os.environ.pop("LTU_PROXY_TOKEN", None)
            traffic_core.configure_runtime_settings(
                proxy_enabled=False, proxy_url="https://relay.example/traffic",
                proxy_token="local-token",
            )
            preferences = traffic_core.proxy_preferences()
            assert preferences == {
                "enabled": False,
                "url": "https://relay.example/traffic",
                "token": "local-token",
            }
            assert traffic_core.proxy_settings() == ("", "")
            assert traffic_core.proxy_config() == ("", "")
            assert peer_net._relay_proxy_config() == (
                "https://relay.example/traffic", "local-token"
            )
    finally:
        _restore_runtime_config(previous, traffic_core._PLANETRACK_BLOCKED_UNTIL)


def test_planetrack_without_key_skips_its_request():
    previous = dict(traffic_core._RUNTIME_CONFIG)
    blocked_until = traffic_core._PLANETRACK_BLOCKED_UNTIL

    class FakeSources:
        @staticmethod
        def cooling(_name):
            return False

        @staticmethod
        def try_request_slot(_name, _min_interval=1.0):
            return 0.0

    try:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MSFS_TRAFFIC_SOURCE", None)
            os.environ.pop("PLANETRACK_API_KEY", None)
            traffic_core.configure_runtime_settings(planetrack_api_key="")
            with patch.object(traffic_core, "SOURCES", FakeSources()):
                with patch.object(traffic_core.requests, "get") as get:
                    assert traffic_core._fetch_planetrack_traffic(50.0, 8.0, 250) is None
                    get.assert_not_called()
    finally:
        _restore_runtime_config(previous, blocked_until)


def test_planetrack_is_fourth_direct_fallback_not_selectable():
    api_key = "private-test-key"
    previous = dict(traffic_core._RUNTIME_CONFIG)
    blocked_until = traffic_core._PLANETRACK_BLOCKED_UNTIL
    requests_seen = []
    slot_requests = []

    class AdsbEmptyResponse:
        status_code = 200
        headers = {}
        text = '{"ac":[]}'

        @staticmethod
        def json():
            return {"ac": []}

        @staticmethod
        def raise_for_status():
            return None

    class OpenSkyEmptyResponse:
        status_code = 200
        headers = {}
        text = '{"states":[]}'

        @staticmethod
        def json():
            return {"states": []}

        @staticmethod
        def raise_for_status():
            return None

    class PlaneTrackResponse:
        status_code = 200
        headers = {}
        text = '{"ac":[{"flight":"PTX51"}]}'

        @staticmethod
        def json():
            return {"ac": [{"flight": "PTX51"}]}

        @staticmethod
        def raise_for_status():
            return None

    class FakeSources:
        reports = []

        @staticmethod
        def cooling(_name):
            return False

        @staticmethod
        def try_request_slot(name, min_interval=1.0):
            slot_requests.append((name, min_interval))
            return 0.0

        @staticmethod
        @contextmanager
        def request_slot(_name, _min_interval=1.0):
            yield

        def report(self, *args, **kwargs):
            self.reports.append((args, kwargs))

    def fake_get(url, headers=None, timeout=None):
        requests_seen.append((url, headers, timeout))
        if "planetrack.ai" in url:
            return PlaneTrackResponse()
        if "opensky-network.org" in url:
            return OpenSkyEmptyResponse()
        return AdsbEmptyResponse()

    sources = FakeSources()
    try:
        with patch.dict(os.environ, {"MSFS_TRAFFIC_SOURCE": "planetrack"}):
            os.environ.pop("PLANETRACK_API_KEY", None)
            traffic_core.configure_runtime_settings(
                planetrack_api_key=api_key, planetrack_radius_nm=40,
            )
            assert traffic_core.traffic_source_config()["provider"] == "auto"
            with patch.object(traffic_core, "SOURCES", sources):
                with patch.object(traffic_core.requests, "get", side_effect=fake_get):
                    result = traffic_core.fetch_direct(50.033, 8.570, 250)
    finally:
        _restore_runtime_config(previous, blocked_until)

    assert result == [{"flight": "PTX51"}]
    assert [url.split("/")[2] for url, _headers, _timeout in requests_seen] == [
        "api.adsb.lol", "opendata.adsb.fi", "opensky-network.org", "planetrack.ai",
    ]
    url, headers, timeout = requests_seen[-1]
    assert url == "https://planetrack.ai/v1/sim/v2/point/50.03300/8.57000/40"
    assert headers["api-auth"] == api_key
    assert timeout == 15
    assert slot_requests == [
        ("opensky-direct", traffic_core.DIRECT_OPENSKY_MIN_INTERVAL),
        ("planetrack", traffic_core.PLANETRACK_MIN_INTERVAL_SEC),
    ]
    assert sources.reports[-1][0][:3] == ("planetrack", True, 200)

def test_planetrack_fourth_source_auth_failure_is_redacted():
    api_key = "secret-that-must-not-be-logged"
    previous = dict(traffic_core._RUNTIME_CONFIG)
    blocked_until = traffic_core._PLANETRACK_BLOCKED_UNTIL
    requests_seen = []
    tracker = app_logging.ErrorStateTracker()

    class FakeHttpError(Exception):
        def __init__(self, response):
            super().__init__("PlaneTrack rejected api-auth")
            self.response = response

    class ErrorResponse:
        status_code = 401
        headers = {}
        text = '{"detail":"invalid key: secret-that-must-not-be-logged"}'

        def json(self):
            return {"detail": f"invalid key: {api_key}"}

        def raise_for_status(self):
            raise FakeHttpError(self)

    class AdsbEmptyResponse:
        status_code = 200
        headers = {}
        text = '{"ac":[]}'

        @staticmethod
        def json():
            return {"ac": []}

        @staticmethod
        def raise_for_status():
            return None

    class OpenSkyEmptyResponse:
        status_code = 200
        headers = {}
        text = '{"states":[]}'

        @staticmethod
        def json():
            return {"states": []}

        @staticmethod
        def raise_for_status():
            return None

    class FakeSources:
        reports = []

        @staticmethod
        def cooling(_name):
            return False

        @staticmethod
        def try_request_slot(_name, _min_interval=1.0):
            return 0.0

        @staticmethod
        @contextmanager
        def request_slot(_name, _min_interval=1.0):
            yield

        def report(self, *args, **kwargs):
            self.reports.append((args, kwargs))

    def fake_get(url, **_kwargs):
        requests_seen.append(url)
        if "planetrack.ai" in url:
            return ErrorResponse()
        if "opensky-network.org" in url:
            return OpenSkyEmptyResponse()
        return AdsbEmptyResponse()

    sources = FakeSources()
    try:
        with patch.dict(os.environ, {"MSFS_TRAFFIC_SOURCE": "planetrack"}):
            os.environ.pop("PLANETRACK_API_KEY", None)
            traffic_core.configure_runtime_settings(
                planetrack_api_key=api_key, planetrack_radius_nm=40,
            )
            with patch.object(traffic_core, "SOURCES", sources):
                with patch.object(traffic_core, "_TRAFFIC_SOURCE_ERRORS", tracker):
                    with patch.object(traffic_core.requests, "get", side_effect=fake_get):
                        with patch.object(traffic_core, "log_action") as local_log:
                            with patch.object(app_logging, "log_action") as tracker_log:
                                result = traffic_core.fetch_direct(50.0, 8.0, 100)
    finally:
        _restore_runtime_config(previous, blocked_until)

    assert result is None
    assert [url.split("/")[2] for url in requests_seen] == [
        "api.adsb.lol", "opendata.adsb.fi", "opensky-network.org", "planetrack.ai",
    ]
    assert any(args[0][0] == "planetrack" and args[0][1] is False
               for args in sources.reports)
    logged_text = repr(local_log.call_args_list) + repr(tracker_log.call_args_list)
    assert api_key not in logged_text

def test_direct_opensky_anonymous_gate_skips_without_sleep_or_log_flood():
    class Response:
        status_code = 200
        text = '{"unexpected":[]}'

        def json(self):
            return {"unexpected": []}

        def raise_for_status(self):
            return None

    class FakeSources:
        @staticmethod
        def cooling(_name):
            return False

        @staticmethod
        @contextmanager
        def request_slot(_name, _min_interval=1.0):
            yield

        @staticmethod
        def try_request_slot(name, min_interval=1.0):
            assert name == "opensky-direct"
            assert min_interval == traffic_core.DIRECT_OPENSKY_MIN_INTERVAL
            return 899.0

        @staticmethod
        def report(*_args, **_kwargs):
            return None

    class FakeRequests:
        calls = []

        @classmethod
        def get(cls, url, **_kwargs):
            cls.calls.append(url)
            return Response()

    with patch.object(traffic_core, "SOURCES", FakeSources()):
        with patch.object(traffic_core, "requests", FakeRequests()):
            with patch.object(traffic_core, "_DIRECT_OPENSKY_GATE_SKIP_LOGGED", False):
                with patch.object(traffic_core, "log_action") as log_action:
                    traffic_core.fetch_direct(50.0, 8.0, 80.0)
                    traffic_core.fetch_direct(50.0, 8.0, 80.0)

    assert not any("opensky-network.org" in url for url in FakeRequests.calls)
    skips = [call for call in log_action.call_args_list
             if call.args and call.args[0] == "traffic.source_request_skipped"
             and call.kwargs.get("reason") == "anonymous_credit_budget"]
    assert len(skips) == 1
    assert skips[0].kwargs["retry_after_sec"] == 899.0


def test_direct_opensky_ignores_world_sized_viewport_and_sends_bounded_bbox():
    class Response:
        status_code = 200
        text = '{"states":[]}'

        def __init__(self, payload):
            self.payload = payload
            self.text = str(payload)

        def json(self):
            return self.payload

        def raise_for_status(self):
            return None

    class FakeSources:
        @staticmethod
        def cooling(_name):
            return False

        @staticmethod
        @contextmanager
        def request_slot(_name, _min_interval=1.0):
            yield

        @staticmethod
        def try_request_slot(name, min_interval=1.0):
            assert name == "opensky-direct"
            assert min_interval == traffic_core.DIRECT_OPENSKY_MIN_INTERVAL
            return 0.0

        @staticmethod
        def report(*_args, **_kwargs):
            return None

    class FakeRequests:
        calls = []

        @classmethod
        def get(cls, url, **_kwargs):
            cls.calls.append(url)
            if "opensky-network.org" in url:
                return Response({"states": []})
            # Invalid ADS-B schema forces the normal failover chain onward.
            return Response({"aircraft": []})

    with patch.object(traffic_core, "SOURCES", FakeSources()):
        with patch.object(traffic_core, "requests", FakeRequests()):
            with patch.object(traffic_core, "log_action") as log_action:
                result = traffic_core.fetch_direct(
                    50.0, 8.0, 80.0, bounds=(90.0, -90.0, 180.0, -180.0)
                )

    assert result is None
    url = next(url for url in FakeRequests.calls if "opensky-network.org" in url)
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(url).query)
    north = float(query["lamax"][0])
    south = float(query["lamin"][0])
    east = float(query["lomax"][0])
    west = float(query["lomin"][0])
    assert 49.2 < south < 50.0 < north < 50.8
    assert 6.7 < west < 8.0 < east < 9.3
    assert (north - south) < 2.0 and (east - west) < 3.0
    starts = [call for call in log_action.call_args_list
              if call.args and call.args[0] == "traffic.source_request_started"
              and call.kwargs.get("source") == "opensky"]
    assert len(starts) == 1
    assert starts[0].kwargs["estimated_credits"] == 1
