"""Tests for loading proxy-stored aircraft paths into the selected trail."""

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import traffic_core as tc


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "ok": True,
            "found": True,
            "hex": "3c6444",
            "points": [
                {"t": 100, "lat": 50.0, "lon": 8.0, "alt": 20000, "track": 90},
                {"t": 110, "lat": 50.1, "lon": 8.2, "alt": 21000, "track": 95},
                {"t": 120, "lat": "invalid", "lon": 8.3},
            ],
        }


class AircraftHistoryTests(unittest.TestCase):
    def test_fetches_and_validates_history_from_proxy(self):
        with patch.object(tc, "proxy_config", return_value=(
                "http://proxy.test/ltu/index.php", "test-secret")), \
             patch.object(tc.requests, "get", return_value=FakeResponse()) as get:
            points = tc.fetch_aircraft_history("3C6444")

        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["lat"], 50.0)
        self.assertEqual(points[1]["lon"], 8.2)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "http://proxy.test/ltu/index.php?action=track&hex=3c6444")
        self.assertEqual(kwargs["headers"]["X-LTU-Token"], "test-secret")
        self.assertEqual(kwargs["timeout"], 8)

    def test_can_fetch_by_callsign_before_hex_is_known(self):
        with patch.object(tc, "proxy_config", return_value=(
                "http://proxy.test/ltu/index.php", "")), \
             patch.object(tc.requests, "get", return_value=FakeResponse()) as get:
            points = tc.fetch_aircraft_history("", callsign="EWG51Y")
        self.assertEqual(len(points), 2)
        self.assertEqual(get.call_args.args[0],
                         "http://proxy.test/ltu/index.php?action=track&flight=EWG51Y")

    def test_skips_request_without_proxy_or_with_invalid_hex(self):
        with patch.object(tc, "proxy_config", return_value=("", "")), \
             patch.object(tc.requests, "get") as get:
            self.assertEqual(tc.fetch_aircraft_history("3c6444"), [])
            self.assertEqual(tc.fetch_aircraft_history("../../index.php"), [])
        get.assert_not_called()

    def test_selected_aircraft_trail_is_seeded_from_history(self):
        loaded = threading.Event()

        def history_fetcher(hex_code):
            self.assertEqual(hex_code, "3c6444")
            loaded.set()
            return [
                {"lat": 49.9, "lon": 7.9, "alt": 18000},
                {"lat": 50.0, "lon": 8.0, "alt": 20000},
            ]

        engine = tc.TrafficEngine(history_fetcher=history_fetcher)
        engine.options["route"] = False
        engine.options["show_all"] = True
        engine._process([tc.parse_adsb({
            "flight": "EWG51Y", "hex": "3c6444", "lat": 50.01, "lon": 8.01,
            "alt_baro": 22000, "track": 90,
        })])
        engine.select("EWG51Y")

        self.assertTrue(loaded.wait(2.0))
        deadline = time.monotonic() + 2.0
        trail = []
        while time.monotonic() < deadline:
            trail = engine.state()["trail"]
            if len(trail) >= 2:
                break
            time.sleep(0.01)
        self.assertEqual([(p[0], p[1]) for p in trail], [(49.9, 7.9), (50.0, 8.0)])

    def test_typed_callsign_can_load_history_before_current_position_arrives(self):
        loaded = threading.Event()
        requested = []

        def history_fetcher(hex_code, callsign=None):
            requested.append((hex_code, callsign))
            loaded.set()
            return [{"lat": 49.8, "lon": 7.8, "alt": 15000}]

        engine = tc.TrafficEngine(history_fetcher=history_fetcher)
        engine.select("EWG51Y")
        self.assertTrue(loaded.wait(2.0))
        self.assertEqual(requested, [("", "EWG51Y")])
        self.assertTrue(any(point[:2] == [49.8, 7.8]
                            for point in engine.state()["trail"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
