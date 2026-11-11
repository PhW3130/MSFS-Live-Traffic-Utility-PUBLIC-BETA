"""Pure-Python regression tests for MSFS SimConnect AI traffic handling."""

import math
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import msfs_ai


class FakeSpawnManager:
    _has_parked_api = True

    def __init__(self):
        self.jobs = []
        self.removed = []

    def queue_spawn(self, **kwargs):
        self.jobs.append(kwargs)

    def remove_aircraft(self, object_id):
        self.removed.append(object_id)
        return True


class FakeAircraftRequests:
    instances = []

    def __init__(self, sm, _time=10, _attemps=10):
        self.sm = sm
        self.options = (_time, _attemps)
        self.values = {
            "PLANE_LATITUDE": 52.5,
            "PLANE_LONGITUDE": 13.4,
            "PLANE_ALTITUDE": 1200.0,
            "PLANE_HEADING_DEGREES_TRUE": math.pi / 2,
        }
        self.instances.append(self)

    def get(self, name):
        return self.values.get(name)


class FakeCFunction:
    def __init__(self):
        self.argtypes = None
        self.restype = None
        self.last_args = None

    def __call__(self, *args):
        self.last_args = args
        return 0


class MsfsAiTests(unittest.TestCase):
    def test_player_position_reuses_requests_and_converts_heading(self):
        FakeAircraftRequests.instances.clear()
        sim = SimpleNamespace()
        with patch.object(msfs_ai, "AircraftRequests", FakeAircraftRequests, create=True):
            position = msfs_ai.get_player_position(sim)
            second_position = msfs_ai.get_player_position(sim)

        self.assertEqual(position, (52.5, 13.4, 1200.0, 90.0))
        self.assertEqual(second_position, position)
        self.assertEqual(len(FakeAircraftRequests.instances), 1)
        self.assertEqual(FakeAircraftRequests.instances[0].options,
                         (msfs_ai.PLAYER_SIMVAR_CACHE_MS,
                          msfs_ai.PLAYER_SIMVAR_ATTEMPTS))

    def test_paused_ai_injection_keeps_player_position_polling_active(self):
        runner = msfs_ai.MsfsAiRunner()
        runner.sm = SimpleNamespace()
        positions = []
        runner.on_own_position = lambda *position: positions.append(position)
        self.assertTrue(runner.ai_injection_enabled)
        self.assertTrue(runner.set_ai_injection_enabled(False))
        self.assertFalse(runner.ai_injection_enabled)

        class StopAfterOnePositionPoll:
            def __init__(self):
                self.checks = 0

            def is_set(self):
                self.checks += 1
                return self.checks > 1

        with patch.object(msfs_ai, "get_player_position",
                          return_value=(52.5, 13.4, 1200.0, 90.0)):
            with patch.object(msfs_ai, "fetch_adsb_traffic") as fetch_traffic:
                with patch.object(msfs_ai, "get_player_on_ground") as get_on_ground:
                    with patch.object(msfs_ai.time, "sleep", return_value=None):
                        with patch.object(msfs_ai, "log_action"):
                            runner._run(StopAfterOnePositionPoll())

        self.assertEqual(positions, [(52.5, 13.4, 1200.0, 90.0)])
        fetch_traffic.assert_not_called()
        get_on_ground.assert_not_called()
        self.assertTrue(runner.set_ai_injection_enabled(True))
        self.assertTrue(runner.ai_injection_enabled)

    def test_invalid_player_position_records_which_simvars_were_available(self):
        sim = SimpleNamespace()
        with patch.object(msfs_ai, "AircraftRequests", FakeAircraftRequests, create=True):
            requests = FakeAircraftRequests(sim)
            requests.values["PLANE_LATITUDE"] = None
            with patch.object(msfs_ai, "AircraftRequests", lambda *_args, **_kwargs: requests,
                              create=True):
                position = msfs_ai.get_player_position(sim)

        self.assertEqual(position, (None, None, 1200.0, None))
        self.assertEqual(sim._ltu_player_position_diagnostics["reason"],
                         "missing_or_out_of_range_position")
        self.assertFalse(sim._ltu_player_position_diagnostics["latitude_available"])
        self.assertTrue(sim._ltu_player_position_diagnostics["longitude_available"])
        self.assertEqual(
            sim._ltu_player_position_diagnostics["received_simvars"],
            {
                "PLANE_LATITUDE": None,
                "PLANE_LONGITUDE": 13.4,
                "PLANE_ALTITUDE": 1200.0,
                "PLANE_HEADING_DEGREES_TRUE": math.pi / 2,
            },
        )
        self.assertEqual(
            sim._ltu_player_position_diagnostics["coordinate_errors"],
            {"PLANE_LATITUDE": "missing"},
        )
        self.assertEqual(
            sim._ltu_player_position_diagnostics["raw_position"],
            {"latitude": None, "longitude": 13.4, "altitude_ft": 1200.0,
             "heading_raw_rad": math.pi / 2},
        )
        self.assertIsNone(
            sim._ltu_player_position_diagnostics["normalized_position"]
        )

    def test_player_position_diagnostics_identify_non_numeric_non_finite_and_range_errors(self):
        for raw_latitude, expected_error in (
            ("not-a-coordinate", "not_numeric"),
            (float("nan"), "non_finite"),
            (91.0, "out_of_range"),
        ):
            sim = SimpleNamespace()
            requests = FakeAircraftRequests(sim)
            requests.values["PLANE_LATITUDE"] = raw_latitude
            with patch.object(msfs_ai, "AircraftRequests", lambda *_args, **_kwargs: requests,
                              create=True):
                position = msfs_ai.get_player_position(sim)

            self.assertIsNone(position[0])
            self.assertEqual(
                sim._ltu_player_position_diagnostics["coordinate_errors"],
                {"PLANE_LATITUDE": expected_error},
            )
            logged_latitude = sim._ltu_player_position_diagnostics["raw_position"]["latitude"]
            if expected_error == "non_finite":
                self.assertTrue(math.isnan(logged_latitude))
            else:
                self.assertEqual(logged_latitude, raw_latitude)

    def test_zero_zero_is_a_valid_geographic_position_not_assumed_to_be_a_spawn_error(self):
        sim = SimpleNamespace()
        requests = FakeAircraftRequests(sim)
        requests.values["PLANE_LATITUDE"] = 0.0
        requests.values["PLANE_LONGITUDE"] = 0.0
        with patch.object(msfs_ai, "AircraftRequests", lambda *_args, **_kwargs: requests,
                          create=True):
            position = msfs_ai.get_player_position(sim)

        self.assertEqual(position[:2], (0.0, 0.0))
        self.assertTrue(sim._ltu_player_position_diagnostics["coordinates_in_range"])
        self.assertEqual(sim._ltu_player_position_diagnostics["coordinate_errors"], {})

    def test_waiting_for_player_position_logs_raw_values_and_throttles_repeats(self):
        runner = msfs_ai.MsfsAiRunner()
        runner.sm = SimpleNamespace()
        diagnostics = {
            "reason": "missing_or_out_of_range_position",
            "raw_position": {
                "latitude": float("nan"),
                "longitude": 13.4,
                "altitude_ft": 1200.0,
                "heading_raw_rad": math.pi / 2,
            },
            "coordinate_errors": {"PLANE_LATITUDE": "non_finite"},
        }

        with patch.object(msfs_ai, "log_action") as log_action:
            with patch("builtins.print") as output:
                runner._report_player_position_unavailable(diagnostics, now=100.0)
                runner._report_player_position_unavailable(diagnostics, now=105.0)
                runner._report_player_position_unavailable(diagnostics, now=110.0)

        lost = [call for call in log_action.call_args_list
                if call.args[0] == "msfs.player_position_lost"]
        waiting = [call for call in log_action.call_args_list
                   if call.args[0] == "msfs.player_position_unavailable"]
        self.assertEqual(len(lost), 1)
        self.assertEqual(len(waiting), 2)
        self.assertEqual(waiting[0].kwargs["wait_sec"], 0.0)
        self.assertEqual(waiting[1].kwargs["wait_sec"], 10.0)
        self.assertEqual(waiting[0].kwargs["raw_position"], diagnostics["raw_position"])
        self.assertEqual(len(output.call_args_list), 2)
        self.assertIn("lat=nan [non_finite]", output.call_args_list[0].args[0])
        self.assertIn("heading_raw_rad=1.5707963267948966", output.call_args_list[0].args[0])

    def test_player_position_recovery_logs_previous_invalid_and_current_valid_position_once(self):
        previous_diagnostics = {
            "reason": "missing_or_out_of_range_position",
            "raw_position": {"latitude": None, "longitude": 13.4},
        }
        current_diagnostics = {
            "reason": "ok",
            "normalized_position": {"latitude": 52.5, "longitude": 13.4},
        }
        runner = msfs_ai.MsfsAiRunner()
        runner.sm = SimpleNamespace(
            _ltu_player_position_diagnostics=current_diagnostics
        )
        runner._position_unavailable_since = 100.0
        runner._position_unavailable_diagnostics = previous_diagnostics

        with patch.object(msfs_ai.time, "time", return_value=112.34):
            with patch.object(msfs_ai, "log_action") as log_action:
                with patch("builtins.print") as output:
                    self.assertTrue(
                        runner._report_player_position_available(52.5, 13.4, 1234.0, 90.0)
                    )
                    self.assertFalse(
                        runner._report_player_position_available(52.5, 13.4, 1234.0, 90.0)
                    )

        log_action.assert_called_once_with(
            "msfs.player_position_recovered",
            unavailable_sec=12.3,
            position={"latitude": 52.5, "longitude": 13.4,
                      "altitude_ft": 1234.0, "heading_deg": 90.0},
            previous_diagnostics=previous_diagnostics,
            diagnostics=current_diagnostics,
        )
        output.assert_called_once()
        self.assertIn("valid again after 12.3s", output.call_args.args[0])
        self.assertIn("alt_ft=1234.0", output.call_args.args[0])
        self.assertIsNone(runner._position_unavailable_since)
        self.assertIsNone(runner._position_unavailable_diagnostics)

    def test_first_valid_player_position_is_reported_once(self):
        runner = msfs_ai.MsfsAiRunner()
        runner.sm = SimpleNamespace(
            _ltu_player_position_diagnostics={
                "reason": "ok",
                "normalized_position": {"latitude": 52.5, "longitude": 13.4},
            }
        )

        with patch.object(msfs_ai, "log_action") as log_action:
            with patch("builtins.print") as output:
                self.assertTrue(
                    runner._report_player_position_available(52.5, 13.4, 1200.0, 90.0)
                )
                self.assertFalse(
                    runner._report_player_position_available(52.5, 13.4, 1200.0, 90.0)
                )

        log_action.assert_called_once_with(
            "msfs.player_position_available",
            position={"latitude": 52.5, "longitude": 13.4,
                      "altitude_ft": 1200.0, "heading_deg": 90.0},
            diagnostics=runner.sm._ltu_player_position_diagnostics,
        )
        output.assert_called_once()
        self.assertIn("Valid player position received", output.call_args.args[0])

    def test_spawn_clamp_uses_coordinates_and_rejects_invalid_positions(self):
        class FakeInitPosition:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        manager = msfs_ai.AISpawnManager.__new__(msfs_ai.AISpawnManager)
        manager.player_lat = 52.0
        manager.player_lon = 13.0
        manager.player_alt = 1000.0
        job = {
            "lat": 53.0, "lon": 13.0, "alt": 20000.0,
            "ground_speed": 250.0, "heading": 90.0,
        }
        with patch.object(msfs_ai, "SIMCONNECT_DATA_INITPOSITION", FakeInitPosition,
                          create=True):
            with patch.object(msfs_ai, "INITPOSITION_AIRSPEED_CRUISE",
                              SimpleNamespace(value=320), create=True):
                position = manager._build_init_position(job)

        self.assertAlmostEqual(
            msfs_ai._haversine_km(manager.player_lat, manager.player_lon,
                                  position.Latitude, position.Longitude),
            msfs_ai.MAX_SPAWN_DISTANCE_KM,
            delta=0.1,
        )

        # Crossing +180/-180 degrees must not interpolate toward Greenwich.
        dateline_lat, dateline_lon = msfs_ai._clamp_near_player(
            0.0, -179.0, 0.0, 179.0
        )
        self.assertGreater(abs(dateline_lon), 178.0)
        self.assertAlmostEqual(
            msfs_ai._haversine_km(0.0, 179.0, dateline_lat, dateline_lon),
            msfs_ai.MAX_SPAWN_DISTANCE_KM,
            delta=0.1,
        )

        with patch.object(msfs_ai, "SIMCONNECT_DATA_INITPOSITION", FakeInitPosition,
                          create=True):
            with patch.object(msfs_ai, "INITPOSITION_AIRSPEED_CRUISE",
                              SimpleNamespace(value=320), create=True):
                with self.assertRaisesRegex(ValueError, "out of range"):
                    manager._build_init_position({**job, "lat": 95.0})

    def test_deferred_community_selection_blocks_modelmap_only_spawns(self):
        runner = msfs_ai.MsfsAiRunner(require_aircraft_catalog=True)
        self.assertEqual(runner._catalog_block_reason(None, False),
                         "awaiting_community_selection")

        runner.community_directory = r"C:\MSFS\Community"
        self.assertEqual(runner._catalog_block_reason(None, False), "catalog_loading")
        empty_catalog = SimpleNamespace(titles=[], models={})
        self.assertEqual(runner._catalog_block_reason(empty_catalog, False),
                         "catalog_empty")
        self.assertIsNone(runner._catalog_block_reason(
            SimpleNamespace(titles=["installed"], models={"A320": ["installed"]}), True
        ))

        # Existing no-selection behavior remains available when the startup
        # flow did not defer a Community-folder decision.
        fallback_runner = msfs_ai.MsfsAiRunner()
        self.assertIsNone(fallback_runner._catalog_block_reason(None, False))

    def test_ex1_timeout_retries_once_with_legacy_and_retires_old_ids(self):
        manager = msfs_ai.AISpawnManager.__new__(msfs_ai.AISpawnManager)
        manager._has_legacy_api = True
        manager._prefer_legacy_api = False
        manager.pending_by_request = {}
        manager.pending_by_send_id = {}
        manager._retired_request_ids = set()
        manager._retired_object_ids = {}
        manager._retired_send_ids = {}
        manager.spawn_queue = []
        manager.spawning = True
        manager.current_req_id = 100
        manager.spawn_started_at = 123.0
        retry_calls = []
        removed = []
        manager._try_spawn_next = lambda: retry_calls.append(True)
        manager.remove_aircraft = removed.append
        job = {
            "_spawn_api": "ex1", "_legacy_retry_attempted": False,
            "tail": "TEST1", "title": "Test Aircraft",
            "dummy": SimpleNamespace(ai_id=None),
        }
        manager.spawn_queue.append(job)
        manager.pending_by_request[100] = job
        manager.pending_by_send_id.update({501: 100, 502: 100})

        with patch.object(msfs_ai, "log_action"):
            self.assertTrue(manager._retry_legacy_spawn(
                job, "timeout waiting for object id", 100
            ))

        self.assertTrue(job["_legacy_retry_attempted"])
        self.assertTrue(job["_force_legacy_api"])
        self.assertTrue(manager._prefer_legacy_api)
        self.assertFalse(manager.spawning)
        self.assertIsNone(manager.current_req_id)
        self.assertNotIn(100, manager.pending_by_request)
        self.assertEqual(manager.pending_by_send_id, {})
        self.assertEqual(manager._retired_request_ids, {100})
        self.assertEqual(manager._retired_send_ids, {501: 100, 502: 100})
        self.assertEqual(retry_calls, [True])

        # A late EX1 assignment must be removed without consuming the new
        # legacy request or disturbing its queue entry.
        manager.current_req_id = 101
        manager.spawning = True
        manager.pending_by_request[101] = job
        with patch.object(msfs_ai, "log_action"):
            manager._on_object_id(100, 7001)
        self.assertEqual(removed, [7001])
        self.assertEqual(manager.current_req_id, 101)
        self.assertIn(101, manager.pending_by_request)

    def test_duplicate_assignment_keeps_the_confirmed_object(self):
        manager = msfs_ai.AISpawnManager.__new__(msfs_ai.AISpawnManager)
        manager.pending_by_request = {}
        manager.pending_by_send_id = {}
        manager._retired_request_ids = set()
        manager._retired_object_ids = {}
        manager._retired_send_ids = {}
        manager.current_req_id = 100
        manager.spawning = True
        manager.spawn_queue = []
        removed = []
        manager.remove_aircraft = removed.append
        manager._notify_spawn_result = lambda *_args: None
        manager._finish_current_spawn = lambda *_args: None
        dummy = SimpleNamespace(ai_id=None, spawn_confirmed=False)
        job = {"dummy": dummy, "tail": "TEST1", "spawn_mode": "airborne"}
        manager.spawn_queue.append(job)
        manager.pending_by_request[100] = job

        with patch.object(msfs_ai, "log_action"):
            manager._on_object_id(100, 7001)
            manager._on_object_id(100, 7001)
            manager._on_object_id(100, 7002)

        self.assertEqual(dummy.ai_id, 7001)
        self.assertEqual(manager._retired_object_ids[100], 7001)
        self.assertEqual(removed, [7002])

    def test_late_ex1_exception_does_not_fail_current_legacy_request(self):
        manager = msfs_ai.AISpawnManager.__new__(msfs_ai.AISpawnManager)
        manager.pending_by_request = {101: {"tail": "CURRENT", "title": "Current"}}
        manager.pending_by_send_id = {601: 101}
        manager._retired_send_ids = {501: 100}
        manager.current_req_id = 101
        manager.spawning = True
        exception = SimpleNamespace(dwException=1, UNKNOWN_SENDID=501, dwSendID=599)

        with patch.object(msfs_ai, "log_action"):
            manager._on_exception(exception)

        self.assertEqual(manager.current_req_id, 101)
        self.assertIn(101, manager.pending_by_request)
        self.assertEqual(manager.pending_by_send_id, {601: 101})
        self.assertEqual(manager._retired_send_ids, {})

    def test_unmatched_simconnect_exception_does_not_fail_current_spawn(self):
        manager = msfs_ai.AISpawnManager.__new__(msfs_ai.AISpawnManager)
        manager.pending_by_request = {101: {"tail": "CURRENT", "title": "Current"}}
        manager.pending_by_send_id = {601: 101}
        manager._retired_send_ids = {}
        manager.current_req_id = 101
        manager.spawning = True
        exception = SimpleNamespace(dwException=1, UNKNOWN_SENDID=0, dwSendID=999)

        with patch.object(msfs_ai, "log_action"):
            manager._on_exception(exception)

        self.assertEqual(manager.current_req_id, 101)
        self.assertIn(101, manager.pending_by_request)
        self.assertEqual(manager.pending_by_send_id, {601: 101})

    def test_spawn_api_passes_init_position_by_value(self):
        class FakeInitPosition:
            pass

        for api_name, binder_name, wrapper_name, call_args in (
            ("SimConnect_AICreateNonATCAircraft_EX1", "_ensure_ex1_api",
             "AICreateNonATCAircraft_EX1", ("handle", b"model", b"", b"tail")),
            ("SimConnect_AICreateNonATCAircraft", "_ensure_legacy_spawn_api",
             "AICreateNonATCAircraft", ("handle", b"model", b"tail")),
        ):
            with self.subTest(api=api_name):
                native_fn = FakeCFunction()
                sim = SimpleNamespace(dll=SimpleNamespace(
                    SimConnect=SimpleNamespace(**{api_name: native_fn})
                ))
                with patch.object(msfs_ai, "SIMCONNECT_DATA_INITPOSITION",
                                  FakeInitPosition, create=True):
                    self.assertTrue(getattr(msfs_ai, binder_name)(sim))
                    init_pos = FakeInitPosition()
                    getattr(sim.dll, wrapper_name)(*call_args, init_pos, 17)

                self.assertIs(native_fn.argtypes[-2], FakeInitPosition)
                self.assertIs(native_fn.last_args[-2], init_pos)

    def test_ex1_parked_api_binds_title_livery_tail_airport(self):
        native_fn = FakeCFunction()
        sim = SimpleNamespace(dll=SimpleNamespace(SimConnect=SimpleNamespace(
            SimConnect_AICreateParkedATCAircraft_EX1=native_fn
        )))

        self.assertTrue(msfs_ai._ensure_parked_ex1_api(sim))
        sim.dll.AICreateParkedATCAircraft_EX1(
            "handle", b"model", b"", b"tail", b"EDDF", 19
        )

        self.assertEqual(native_fn.last_args,
                         ("handle", b"model", b"", b"tail", b"EDDF", 19))

    def test_spawn_manager_keeps_position_path_when_ai_spawn_is_unavailable(self):
        sim = SimpleNamespace(
            dll=SimpleNamespace(SimConnect=SimpleNamespace(),
                                DispatchProc=lambda callback: callback),
            my_dispatch_proc=lambda *_args: None,
        )
        recv_ids = SimpleNamespace(
            SIMCONNECT_RECV_ID_ASSIGNED_OBJECT_ID=1,
            SIMCONNECT_RECV_ID_EXCEPTION=2,
        )
        with patch.object(msfs_ai, "SIMCONNECT_RECV_ID", recv_ids, create=True):
            manager = msfs_ai.AISpawnManager(sim)

        self.assertFalse(manager._has_ex1_api)
        self.assertFalse(manager._has_legacy_api)
        self.assertIs(sim.my_dispatch_proc_rd, sim.my_dispatch_proc)

    def test_player_on_ground_fallback_converts_ground_altitude_meters_to_feet(self):
        sim = SimpleNamespace()
        requests = FakeAircraftRequests(sim)
        requests.values.update({
            "SIM_ON_GROUND": "1",
            "GROUND_ALTITUDE": 1200.0,
            "GROUND_VELOCITY": 0.0,
        })
        sim._ltu_aircraft_requests = requests
        self.assertTrue(msfs_ai.get_player_on_ground(sim))

        requests.values["SIM_ON_GROUND"] = None
        # 1,200 ft in PLANE_ALTITUDE is about 365.76 m in GROUND_ALTITUDE.
        # Comparing the raw numbers would misclassify both of these cases.
        self.assertFalse(msfs_ai.get_player_on_ground(sim))
        requests.values["GROUND_ALTITUDE"] = 365.76
        self.assertTrue(msfs_ai.get_player_on_ground(sim))

    def test_airport_and_simvar_value_normalization(self):
        self.assertEqual(msfs_ai.normalize_airport_icao(b" eddf "), "EDDF")
        self.assertEqual(msfs_ai.normalize_airport_icao("kjfk"), "KJFK")
        self.assertEqual(msfs_ai.normalize_airport_icao("Frankfurt"), "")
        self.assertTrue(msfs_ai.parse_sim_bool("1"))
        self.assertFalse(msfs_ai.parse_sim_bool(0))
        self.assertIsNone(msfs_ai.parse_sim_bool("unknown"))

    def test_only_fills_on_ground_and_stops_after_failed_parked_spawn(self):
        manager = FakeSpawnManager()
        filler = msfs_ai.ParkedFillerController(manager, max_aircraft=3)
        filler.set_airport("EDDF")

        filler.tick(on_ground=False, lat=50.0, lon=8.0)
        self.assertEqual(manager.jobs, [])

        filler.tick(on_ground=True, lat=50.0, lon=8.0)
        self.assertEqual(len(manager.jobs), 1)
        first = manager.jobs[0]
        self.assertEqual(first["spawn_mode"], "parked")
        self.assertEqual(first["airport_icao"], "EDDF")
        self.assertTrue(first["tail"].startswith("LTUPARK"))
        first["dummy"].ai_id = 101
        first["on_result"](True, "")

        # Process success and request the next static aircraft.
        filler.tick(on_ground=True, lat=50.0, lon=8.0)
        self.assertEqual(filler.spawned_count, 1)
        self.assertEqual(len(manager.jobs), 2)

        second = manager.jobs[1]
        second["on_result"](False, "no free parking position")
        filler.tick(on_ground=True, lat=50.0, lon=8.0)
        self.assertTrue(filler.exhausted)
        self.assertEqual(len(manager.jobs), 2)

    def test_airport_change_removes_previous_fillers_and_discards_stale_success(self):
        manager = FakeSpawnManager()
        filler = msfs_ai.ParkedFillerController(manager, max_aircraft=2)
        filler.set_airport("EDDF")
        filler.tick(on_ground=True, lat=50.0, lon=8.0)

        pending = manager.jobs[0]
        filler.set_airport("KJFK")
        pending["dummy"].ai_id = 202
        pending["on_result"](True, "")
        self.assertEqual(manager.removed, [202])

        filler.tick(on_ground=True, lat=40.6, lon=-73.8)
        self.assertEqual(len(manager.jobs), 2)
        self.assertEqual(manager.jobs[1]["airport_icao"], "KJFK")

        current = manager.jobs[1]
        current["dummy"].ai_id = 303
        current["on_result"](True, "")
        filler.tick(on_ground=True, lat=40.6, lon=-73.8)
        filler.set_airport("KLAX")
        self.assertIn(303, manager.removed)

    def test_safety_cap_prevents_unbounded_fill(self):
        manager = FakeSpawnManager()
        filler = msfs_ai.ParkedFillerController(manager, max_aircraft=1)
        filler.set_airport("EDDF")
        filler.tick(on_ground=True, lat=50.0, lon=8.0)
        job = manager.jobs[0]
        job["dummy"].ai_id = 404
        job["on_result"](True, "")
        filler.tick(on_ground=True, lat=50.0, lon=8.0)
        filler.tick(on_ground=True, lat=50.0, lon=8.0)
        self.assertEqual(filler.spawned_count, 1)
        self.assertEqual(len(manager.jobs), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
