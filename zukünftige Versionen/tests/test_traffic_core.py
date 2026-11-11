"""
Testet die framework-unabhängige TrafficEngine ohne Qt, ohne Netzwerk:

    python tests/test_traffic_core.py
"""

import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import traffic_core as tc

# page.txt (lokal vorhanden, gitignoriert) darf die Test-URLs nicht ueberschreiben:
_REAL_PAGE_TXT = tc._page_txt_url
tc._page_txt_url = lambda: ""


def make_traffic(t0):
    dt = t0
    return [
        {"flight": "EWG51Y", "hex": "3c6444", "r": "D-AEWD", "t": "A20N",
         "category": "A3", "lat": 50.03 + dt * 0.01, "lon": 8.57 + dt * 0.02,
         "alt_baro": 24000, "alt_geom": 24500, "gs": 420, "track": 90,
         "baro_rate": 1200, "squawk": "1000", "seen": 0.4, "messages": 500,
         "dst": 12.0, "dir": 180},
        {"flight": "DLH123", "hex": "3c0001", "r": "D-AIXA", "t": "A359",
         "category": "A5", "lat": 50.5 + dt * 0.02, "lon": 8.2,
         "alt_baro": 36000, "alt_geom": 36400, "gs": 480, "track": 270,
         "baro_rate": 0, "squawk": "2001", "seen": 0.2, "messages": 900},
        {"flight": "BAW456", "hex": "400002", "r": "G-XWBA", "t": "B789",
         "category": "A5", "lat": 49.9, "lon": 8.9,
         "alt_baro": 12000, "alt_geom": 12400, "gs": 300, "track": 180,
         "baro_rate": -1800, "squawk": "7700", "seen": 0.1, "messages": 700},
        {"flight": "RYR789", "hex": "4ca003", "r": "EI-EBX", "t": "B738",
         "category": "A3", "lat": 50.05, "lon": 8.59,
         "alt_baro": "ground", "alt_geom": None, "gs": 12, "track": 45,
         "baro_rate": 0, "squawk": "1000", "seen": 1.0, "messages": 100},
        {"flight": "DRF11", "hex": "3e0004", "r": "D-HDRZ", "t": "EC45",
         "category": "A7", "lat": 50.2, "lon": 8.4,
         "alt_baro": 1500, "alt_geom": 1600, "gs": 120, "track": 10,
         "baro_rate": 0, "squawk": "1000", "seen": 0.8, "messages": 200},
        {"flight": "AFR900", "hex": "390005", "r": "F-HPJA", "t": "A359",
         "category": "A5", "lat": 51.0, "lon": 7.0,
         "alt_baro": 5000, "alt_geom": 5200, "gs": 250, "track": 320,
         "baro_rate": 2000, "squawk": "1000", "seen": 0.6, "messages": 300},
    ]


class FakeRouteCache:
    def __init__(self):
        self._cache = {}

    def get(self, callsign):
        route = {
            "origin_icao": "EDDF", "dest_icao": "LEPA",
            "origin_lat": 50.03, "origin_lon": 8.57,
            "dest_lat": 39.55, "dest_lon": 2.73,
            "origin_name": "Frankfurt Main", "dest_name": "Palma de Mallorca",
        }
        self._cache[callsign] = (route, time.time())
        return route


ERRORS = []


def check(cond, msg):
    if cond:
        print(f"  [OK] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        ERRORS.append(msg)


def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def main():
    engine = tc.TrafficEngine(fetcher=lambda lat, lon, r=250, bounds=None: make_traffic(time.time() % 100))
    engine.route_cache = FakeRouteCache()
    engine.REFRESH_SEC = 0.05
    states = []
    engine.on_state = states.append

    print("== Start / Auswahl ==")
    engine.start("EWG51Y")
    check(wait_for(lambda: states and states[-1]["aircraft"]), "State mit Aircraft erzeugt")
    st = states[-1]
    check(st["selected"] == "EWG51Y", "Auswahl gesetzt")
    check(len(st["aircraft"]) == 1, "Ohne show_all nur Auswahl sichtbar")

    print("== show_all ==")
    engine.options["show_all"] = True
    check(wait_for(lambda: len(states[-1]["aircraft"]) == 6), "6 Flugzeuge sichtbar")
    st = states[-1]
    calls = {a["call"] for a in st["aircraft"]}
    check(calls == {"EWG51Y", "DLH123", "BAW456", "RYR789", "DRF11", "AFR900"}, "Alle Calls vorhanden")

    print("== Flugzeuge ohne Rufzeichen ==")
    hex_engine = tc.TrafficEngine(fetcher=lambda *args: [])
    hex_engine.options["show_all"] = True
    hex_engine._process([tc.parse_adsb({"hex": "3c1234", "lat": 50.1, "lon": 8.2})])
    hex_aircraft = hex_engine.state()["aircraft"]
    check(len(hex_aircraft) == 1 and hex_aircraft[0]["call"] == "3C1234",
          "Flugzeug ohne Rufzeichen wird mit ICAO-Hex angezeigt")

    print("== Icon-Daten (für SVG-Renderer) ==")
    by_call = {a["call"]: a for a in st["aircraft"]}
    check(by_call["DRF11"]["kind"] == "heli", "Kategorie A7 → Heli-Icon")
    check(by_call["DLH123"]["kind"] == "plane", "Airliner-Icon")
    check(by_call["BAW456"]["emerg"] and by_call["BAW456"]["color"] == tc.rgb_hex(tc.emergency_rgb()),
          "Notfall: Squawk 7700 → rote Farbe")
    check(by_call["RYR789"]["ground"], "Bodenverkehr erkannt")
    check(by_call["RYR789"]["color"] != by_call["DLH123"]["color"], "Bodenfarbe gedimmt")
    check(abs(by_call["DLH123"]["hdg"] - 270) < 1, "Heading im State")
    check(all(a["color"].startswith("#") for a in st["aircraft"]), "Farben als Hex")
    check(by_call["DLH123"]["label"] and "36.000" in by_call["DLH123"]["label"], "Label mit Höhe")

    print("== Filter ==")
    engine.options["min_alt"] = 10000
    check(wait_for(lambda: "AFR900" not in {a["call"] for a in states[-1]["aircraft"]}),
          "Min-Höhe filtert AFR900 aus")
    engine.options["min_alt"] = None
    engine.options["hide_ground"] = True
    check(wait_for(lambda: "RYR789" not in {a["call"] for a in states[-1]["aircraft"]}),
          "Bodenverkehr ausgeblendet")
    engine.options["hide_ground"] = False

    print("== Labels aus ==")
    engine.options["labels"] = False
    check(wait_for(lambda: all(a["label"] is None for a in states[-1]["aircraft"])),
          "Labels deaktiviert")
    engine.options["labels"] = True

    print("== Trail ==")
    engine.select("DLH123")
    check(wait_for(lambda: len(states[-1]["trail"]) >= 2), "Trail wächst für Auswahl")
    trail = states[-1]["trail"]
    check(all(len(p) == 3 and p[2].startswith("#") for p in trail), "Trail-Punkte mit Farbe")

    print("== Route ==")
    check(wait_for(lambda: states[-1]["route"] is not None), "Route für Auswahl geladen")
    route = states[-1]["route"]
    check(route["origin_icao"] == "EDDF" and route["dest_icao"] == "LEPA", "Origin/Dest korrekt")
    check(len(route["path"]) == 49, "Great-Circle-Pfad (49 Punkte)")
    engine.options["route"] = False
    check(wait_for(lambda: states[-1]["route"] is None), "Route abschaltbar")
    engine.options["route"] = True

    print("== Folge-Modus / Zoom ==")
    engine.options["follow"] = True
    check(wait_for(lambda: states[-1]["center"] is not None), "Follow liefert Karten-Zentrum")
    engine.zoom_by(2)
    check(wait_for(lambda: any(s["zoom_cmd"] == 2 for s in states)), "Zoom-Kommando im State")

    print("== Info-Daten ==")
    info = engine.info_lines("DLH123")
    check(info and info["airline"] == "Lufthansa", "Airline-Name aufgelöst")
    check(wait_for(lambda: (engine.info_lines("DLH123") or {}).get("route")),
          "Route in Info-Daten")
    info = engine.info_lines("DLH123")
    check(info["route"]["dest_icao"] == "LEPA", "Destination korrekt")
    check(info["emergency"] is False and info["ground"] is False, "Status-Flags")

    print("== Nearest ==")
    check(engine.nearest_callsign(50.05, 8.59) == "RYR789", "Nächstes Flugzeug gefunden")

    print("== Auswahl aufheben ==")
    engine.select(None)
    check(wait_for(lambda: states[-1]["selected"] is None and states[-1]["trail"] == []),
          "Auswahl + Trail zurückgesetzt")

    print("== Suche / Airline / Typ (Engine-Filter) ==")
    engine.options["search"] = "ewg"
    check(wait_for(lambda: {a["call"] for a in states[-1]["aircraft"]} == {"EWG51Y"}),
          "Suchfilter matcht Callsign (case-insensitiv)")
    engine.options["search"] = "3c0001"
    check(wait_for(lambda: {a["call"] for a in states[-1]["aircraft"]} == {"DLH123"}),
          "Suchfilter matcht Hex")
    engine.options["search"] = ""
    engine.options["airline"] = "ryan"
    check(wait_for(lambda: {a["call"] for a in states[-1]["aircraft"]} == {"RYR789"}),
          "Airline-Filter (Name)")
    engine.options["airline"] = ""
    engine.options["type_filter"] = "a359"
    check(wait_for(lambda: {a["call"] for a in states[-1]["aircraft"]} == {"DLH123", "AFR900"}),
          "Typ-Filter (A359)")
    engine.options["type_filter"] = ""
    check(wait_for(lambda: len(states[-1]["aircraft"]) == 6), "Filter leer → alle 6")

    print("== Such-Sprung (search_select) ==")
    check(engine.search_select("BAW") == "BAW456", "search_select findet BAW456")
    check(engine.selected == "BAW456", "Treffer wird ausgewählt")
    check(wait_for(lambda: states[-1]["center"] is not None), "Karte zentriert auf Treffer")
    check(engine.search_select("zzzz9999") is None, "Kein Treffer → None")
    engine.select(None)

    print("== Weltweiter Such-Snapshot ==")
    global_engine = tc.TrafficEngine(fetcher=lambda *args: [])
    global_engine.route_cache = FakeRouteCache()
    global_engine.options["follow"] = False
    worldwide = [{
        "flight": "WIDE42", "hex": "abc123", "r": "D-TEST",
        "t": "A320", "lat": -33.8688, "lon": 151.2093,
        "alt_baro": 31000, "gs": 430, "track": 270,
    }]
    check(global_engine.search_snapshot_select("wide42", worldwide) == "WIDE42",
          "Globaler Snapshot findet Callsign ohne lokalen Treffer")
    global_state = global_engine.state()
    global_marker = next((a for a in global_state["aircraft"]
                          if a["call"] == "WIDE42"), None)
    check(global_state["selected"] == "WIDE42" and global_marker
          and global_marker["selected"], "Globaler Treffer wird markiert und ausgewählt")
    check(global_state["center"] == [-33.8688, 151.2093]
          and global_engine.options["center"] == [-33.8688, 151.2093],
          "Karte und künftige regionale Abfrage werden auf den Global-Treffer zentriert")
    check(global_engine.search_snapshot_select("wide42", [
        {"flight": "WIDE42", "lat": 95, "lon": 151},
    ]) is None, "Snapshot-Treffer ohne gültige Position wird ignoriert")

    print("== Spuren aller Flugzeuge (trails) ==")
    engine.options["trails"] = True
    check(wait_for(lambda: len(states[-1]["trails"]) >= 6), "Trail-Eintrag je Flugzeug")
    tr = states[-1]["trails"]
    check(all(len(p) == 2 for pts in tr.values() for p in pts), "Punkte als [lat, lon]")
    engine.options["trails"] = False
    check(wait_for(lambda: states[-1]["trails"] == {}), "Trails deaktiviert → leer")

    print("== Notfall-Alarme ==")
    engine.options["bounds"] = None
    n_before = sum(len(x.get("alerts", [])) for x in states)
    engine._emerg_seen.clear()
    check(wait_for(lambda: sum(len(x.get("alerts", [])) for x in states) > n_before),
          "Alert für BAW456 (Squawk 7700)")
    al = next(a for x in states for a in x.get("alerts", []) if a["call"] == "BAW456")
    check(al["squawk"] == "7700" and al["lat"] is not None, "Alert-Inhalt korrekt")
    n_after = sum(len(x.get("alerts", [])) for x in states)
    time.sleep(0.3)
    check(sum(len(x.get("alerts", [])) for x in states) == n_after, "Kein Doppel-Alert")
    engine.options["bounds"] = [49.0, 48.0, 9.0, 8.0]   # BAW456 (49.9) liegt außerhalb
    engine._emerg_seen.clear()
    time.sleep(0.3)
    check(sum(len(x.get("alerts", [])) for x in states) == n_after,
          "Alert nur im sichtbaren Bereich")
    engine.options["bounds"] = None

    print("== TCAS: eigene Position & Relativdaten ==")
    check(abs(tc.bearing_deg(0, 0, 0, 1) - 90) < 0.01, "bearing_deg: Ost = 90°")
    check(abs(tc.bearing_deg(0, 0, 1, 0)) < 0.01, "bearing_deg: Nord = 0°")
    check(tc.clock_position(0) == 12 and tc.clock_position(90) == 3 and
          tc.clock_position(180) == 6 and tc.clock_position(270) == 9,
          "clock_position: 12/3/6/9 Uhr")
    engine.set_own_position(50.0, 8.5, 20000, 360)
    engine.options["tcas"] = True
    check(wait_for(lambda: states[-1]["own"] and states[-1]["own"]["valid"]), "own im State")
    engine.select("DLH123")
    check(wait_for(lambda: states[-1]["selected_rel"]), "selected_rel berechnet")
    rel = states[-1]["selected_rel"]
    check(1 <= rel["clock"] <= 12 and rel["dist_km"] > 10, "Clock/Distanz plausibel")
    check(abs(rel["dist_km"] * 0.539957 - rel["dist_nm"]) < 0.5, "km/nm konsistent")
    check(wait_for(lambda: states[-1]["center"] == [50.0, 8.5]),
          "TCAS folgt dem eigenen Flugzeug")
    info = engine.info_lines("DLH123")
    check(info and info["rel"] and info["rel"]["clock"] == rel["clock"],
          "rel auch in info_lines")
    engine.options["tcas"] = False
    engine.select(None)

    print("== CSV-Daten ==")
    rows = engine.csv_rows()
    check(rows[0][0] == "callsign" and rows[0][4] == "airline", "CSV-Header")
    check(len(rows) == 7, f"6 Datenzeilen + Header ({len(rows)})")
    check(any(r[0] == "BAW456" and r[13] == 1 for r in rows[1:]), "Notfall-Flag in CSV")

    engine.stop()
    time.sleep(0.2)

    print("== State-Schema (Vertrag mit map.js) ==")
    need_state = {"seq", "running", "selected", "aircraft", "trail", "trails",
                  "route", "own", "selected_rel", "alerts",
                  "center", "zoom_cmd", "labels", "counts", "time",
                  "status", "net"}
    check(need_state <= set(st.keys()), f"State-Keys vollständig: {sorted(need_state - set(st.keys())) or 'ok'}")
    need_ac = {"call", "lat", "lon", "hdg", "color", "kind", "ground", "emerg",
               "selected", "label", "alt", "gs", "type"}
    check(need_ac <= set(by_call["DLH123"].keys()), "Aircraft-Keys vollständig")

    print("== Provider-Rate-Limit und Retry-After ==")

    class FakeClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps = []

        def __call__(self):
            return self.now

        def sleep(self, delay):
            self.sleeps.append(delay)
            self.now += delay

    fake_clock = FakeClock()
    gate = tc.SourceRegistry(clock=fake_clock, sleeper=fake_clock.sleep)
    with gate.request_slot("adsb.fi", 1.0):
        pass
    with gate.request_slot("adsb.fi", 1.0):
        pass
    check(fake_clock.sleeps == [1.0], "Client-Rate-Gate trennt adsb.fi-Abfragen um mindestens 1 s")

    from email.utils import formatdate

    class RetryResponse:
        status_code = 429
        headers = {"Retry-After": "25"}

    class RetryError(Exception):
        response = RetryResponse()

    check(tc._retry_after_seconds(RetryError(), now=1700000000) == 25,
          "Client liest Retry-After delta-seconds")
    RetryError.response.headers["X-Rate-Limit-Retry-After-Seconds"] = "40"
    check(tc._retry_after_seconds(RetryError(), now=1700000000) == 40,
          "Client bevorzugt OpenSky-Rate-Limit-Retry-After")
    del RetryError.response.headers["X-Rate-Limit-Retry-After-Seconds"]
    RetryError.response.headers["Retry-After"] = formatdate(1700000075, usegmt=True)
    check(tc._retry_after_seconds(RetryError(), now=1700000000) == 75,
          "Client liest Retry-After HTTP-Datum")

    print("== Proxy-Modus (index.php) ==")
    orig_requests = tc.requests
    orig_proxy = tc.COLOR_SCHEME.get("proxy")
    orig_direct_opensky_interval = tc.DIRECT_OPENSKY_MIN_INTERVAL
    # Keep this deterministic end-to-end mock independent of the production
    # anonymous-credit wait; a focused unit test covers that gate separately.
    tc.DIRECT_OPENSKY_MIN_INTERVAL = 0.0

    class FakeResp:
        def __init__(self, payload):
            self._p = payload
            self.status_code = 200

        def json(self):
            return self._p

        def raise_for_status(self):
            pass

    class FakeHTTPError(Exception):
        """Wie requests.HTTPError: hat .response.status_code."""
        def __init__(self, code):
            super().__init__(f"HTTP {code}")

            class _R:
                status_code = code
            self.response = _R()

    class FakeRequests:
        def __init__(self):
            self.calls = []
            self.fail_proxy = False
            self.fail_lol = False
            self.fail_fi = False
            self.bad_fi_schema = False
            self.fail_osk = False
            self.proxy_429 = False
            self.lol_429 = False
            self.empty_proxy = False
            self.empty_fi = False
            self.proxy_online = True
            self.demand_recorded = None
            self.global_pending_responses = 0
            self.global_payload = {"ac": [
                {"flight": "WIDE42", "lat": -33.86, "lon": 151.20},
                {"flight": "NO-POS"},
                {"flight": "BAD-POS", "lat": 95, "lon": 151},
            ]}

        def get(self, url, headers=None, timeout=None):
            self.calls.append((url, headers or {}))
            if "action=global_search" in url:
                if self.global_pending_responses:
                    self.global_pending_responses -= 1
                    return FakeResp({"ok": False, "pending": True,
                                     "retry_after": 0.1, "source": "opensky"})
                return FakeResp({"ok": True, "pending": False,
                                 "cached": False, "source": "opensky",
                                 "ac": self.global_payload["ac"]})
            if "proxy.test" in url:
                if self.proxy_429:
                    raise FakeHTTPError(429)
                if self.fail_proxy:
                    raise RuntimeError("proxy unreachable")
                proxy_ac = [] if self.empty_proxy else [
                    {"flight": "PROX1", "lat": 50.0, "lon": 8.0}]
                payload = {"ac": proxy_ac,
                           "proxy": {"online": self.proxy_online}}
                if self.demand_recorded is not None:
                    payload["demand_recorded"] = self.demand_recorded
                return FakeResp(payload)
            if "adsb.lol" in url:
                if self.lol_429:
                    raise FakeHTTPError(429)
                if self.fail_lol:
                    raise RuntimeError("lol down")
                return FakeResp({"ac": [{"flight": "DIR1", "lat": 50.0, "lon": 8.0}]})
            if "adsb.fi" in url:
                if self.fail_fi:
                    raise RuntimeError("fi down")
                if self.empty_fi:
                    return FakeResp({"ac": []})
                if self.bad_fi_schema:
                    return FakeResp({"aircraft": [{"flight": "FI1"}]})
                return FakeResp({"ac": [{"flight": "FI1", "lat": 50.1, "lon": 8.1}]})
            if "opensky" in url:
                if self.fail_osk:
                    raise RuntimeError("osk down")
                return FakeResp({"states": [[
                    "3c0001", "DLH9X ", "Germany", None, None,
                    8.5, 50.0, 10000.0, False, 230.0, 90.0, 5.0,
                    None, 10200.0, "2001", False, 0]]})
            return FakeResp({"ac": []})

    fr = FakeRequests()
    tc.requests = fr
    global_state_before = (
        tc._global_search_inflight, tc._global_search_last_request,
        list(tc._global_search_snapshot), tc._global_search_snapshot_at,
    )
    try:
        tc.COLOR_SCHEME["proxy"] = {"url": "http://proxy.test/ltu/index.php",
                                    "token": "sekret"}
        tc._global_search_inflight = False
        tc._global_search_last_request = 0.0
        tc._global_search_snapshot = []
        tc._global_search_snapshot_at = 0.0
        tc.SOURCES._src.pop("opensky", None)
        fr.global_pending_responses = 1
        before_global_calls = len(fr.calls)
        global_result = tc.fetch_global_traffic()
        check(global_result["ok"] and len(global_result["aircraft"]) == 1
              and global_result["aircraft"][0]["flight"] == "WIDE42",
              "Global-Request pollt den Collector und filtert gültige Weltpositionen")
        check(len(fr.calls) == before_global_calls + 2
              and "action=global_search" in fr.calls[-1][0],
              "Globaler Snapshot wird ausschließlich über die Proxy-API geliefert")
        global_headers = fr.calls[-1][1]
        check("Authorization" not in global_headers
              and "X-LTU-Token" not in global_headers,
              "Globaler Such-Client sendet weder Proxy- noch OpenSky-Credentials")
        cached_global = tc.fetch_global_traffic()
        check(cached_global["ok"] and cached_global["cached"]
              and len(fr.calls) == before_global_calls + 2,
              "Weitere Suche innerhalb des Limits nutzt Snapshot-Cache ohne neuen Request")
    finally:
        (tc._global_search_inflight, tc._global_search_last_request,
         tc._global_search_snapshot, tc._global_search_snapshot_at) = global_state_before
    fr.calls.clear()

    try:
        tc.COLOR_SCHEME["proxy"] = {"url": "http://proxy.test/ltu/index.php",
                                    "token": "sekret"}
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(len(fr.calls) == 1 and
              fr.calls[0][0].startswith("http://proxy.test/ltu/index.php?action=traffic"),
              "Proxy-URL wird benutzt")
        check("lat=50.00000" in fr.calls[0][0] and "dist=250" in fr.calls[0][0],
              "Zentrum/Radius im Query")

        tc.fetch_adsb_traffic(50.0, 8.0, 250, bounds=(52.0, 48.0, 12.0, 4.0))
        check("bbox=52.0000,48.0000,12.0000,4.0000" in fr.calls[-1][0],
              "Kartenausschnitt wird als BBox angefragt")
        check(fr.calls[0][1].get("X-LTU-Token") == "sekret", "Token-Header gesetzt")
        check(ac and ac[0]["flight"] == "PROX1", "Daten kommen vom Proxy")

        proxy_action_logs = []
        import app_logging as app_logging_module
        original_log_action = tc.log_action
        original_app_log_action = app_logging_module.log_action
        fr.demand_recorded = False
        fr.proxy_online = True
        fr.calls.clear()
        capture_proxy_log = lambda action, **details: proxy_action_logs.append((action, details))
        tc.log_action = capture_proxy_log
        app_logging_module.log_action = capture_proxy_log
        try:
            tc.fetch_adsb_traffic(50.0, 8.0, 250)
            tc.fetch_adsb_traffic(50.0, 8.0, 250)
            fr.demand_recorded = True
            tc.fetch_adsb_traffic(50.0, 8.0, 250)
        finally:
            tc.log_action = original_log_action
            app_logging_module.log_action = original_app_log_action
            fr.demand_recorded = None
            fr.proxy_online = True
        demand_failures = [details for action, details in proxy_action_logs
                           if action == "traffic.proxy_demand_not_recorded"]
        demand_recoveries = [details for action, details in proxy_action_logs
                             if action == "traffic.proxy_demand_recorded_again"]
        check(len(demand_failures) == 1
              and demand_failures[0].get("reason") == "php_demand_store_failed"
              and len(demand_recoveries) == 1,
              "PHP-Demand-Fehler wird bis zum Client-Log gemeldet, entdoppelt und bei Erholung quittiert")

        fr.empty_proxy = True
        fr.calls.clear()
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(any("proxy.test" in u for u, _ in fr.calls)
              and any("adsb.lol" in u for u, _ in fr.calls),
              "Leerer, aber online gemeldeter Proxy löst Direkt-Fallback aus")
        check(ac and ac[0]["flight"] == "DIR1",
              "Direktdaten werden bei leerer Proxy-Antwort genutzt")
        fr.empty_proxy = False

        fr.fail_proxy = True
        fr.calls.clear()
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250, bounds=(52.0, 48.0, 12.0, 4.0))
        check(len(fr.calls) == 2 and "adsb.lol" in fr.calls[1][0],
              "Fallback auf Direkt-Fetch bei Proxy-Fehler")
        check("/dist/135" in fr.calls[1][0],
              "250 km werden fuer den ADS-B-Radius in etwa 135 NM umgerechnet")
        fr.calls.clear()
        fr.fail_proxy = False
        tc.COLOR_SCHEME["proxy"] = {"url": "", "token": ""}
        tc.fetch_adsb_traffic(50.0, 8.0, 250, bounds=(50.4, 49.9, 9.0, 8.0))
        check("/dist/24" in fr.calls[-1][0],
              "Direkt-Modus: Kartenausschnitt (~45 km) wird in ~24 NM umgerechnet")
        tc.COLOR_SCHEME["proxy"] = {"url": "http://proxy.test/ltu/index.php",
                                    "token": "sekret"}
        fr.fail_proxy = True
        fr.calls.clear()
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(ac and ac[0]["flight"] == "DIR1", "Direktdaten im Fallback")
    finally:
        tc.requests = orig_requests
        if orig_proxy is None:
            tc.COLOR_SCHEME.pop("proxy", None)
        else:
            tc.COLOR_SCHEME["proxy"] = orig_proxy

    print("== Failover-Kette (adsb.lol → adsb.fi → OpenSky → Cache) ==")
    tc.requests = fr
    fr.fail_proxy = True
    fr.fail_lol = True
    fr.calls.clear()
    tc._last_adsb_data = []
    try:
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check("adsb.lol" in fr.calls[0][0] and "adsb.fi" in fr.calls[-1][0],
              "Reihenfolge: adsb.lol → adsb.fi")
        check("opendata.adsb.fi/api/v3/lat/" in fr.calls[-1][0],
              "adsb.fi wird ueber die offizielle v3-Route abgefragt")
        check(ac and ac[0]["flight"] == "FI1", "adsb.fi-Daten genutzt")

        fr.bad_fi_schema = True
        fr.calls.clear()
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(any("opensky-network.org" in u for u, _ in fr.calls),
              "Unerwartete adsb.fi-Antwort gilt nicht als erfolgreicher Leer-Feed")
        fr.bad_fi_schema = False

        fr.empty_fi = True
        fr.calls.clear()
        empty_fallback = tc.fetch_direct(50.0, 8.0, 250)
        check(any("opensky-network.org" in u for u, _ in fr.calls),
              "HTTP 200 mit leerer adsb.fi-Liste faellt auf OpenSky durch")
        check(empty_fallback and empty_fallback[0]["flight"] == "DLH9X",
              "OpenSky-Flugzeuge werden nach leerer adsb.fi-Antwort angezeigt")
        fr.empty_fi = False

        fr.fail_fi = True
        fr.calls.clear()
        ac = tc.fetch_adsb_traffic(
            50.0, 8.0, 250, bounds=(90.0, -90.0, 180.0, -180.0)
        )
        check(any("opensky-network.org" in u for u, _ in fr.calls),
              "OpenSky als dritte Quelle")
        check(any("opendata.adsb.fi/api/v3/lat/" in u for u, _ in fr.calls),
              "adsb.fi-Client-Fallback verwendet die offizielle v3-Route")
        check(ac and ac[0]["flight"] == "DLH9X" and ac[0]["lat"] == 50.0,
              "OpenSky-Konvertierung (Callsign/Position)")
        check(ac[0]["alt_baro"] == 32808 and ac[0]["alt_geom"] == 33465
              and ac[0]["gs"] == 447.1,
              "OpenSky-Felder/Einheiten (baro und geo getrennt; m→ft, m/s→kt)")
        check("lamin=" in fr.calls[-1][0] and "lomax=" in fr.calls[-1][0],
              "OpenSky-BBox im Query")
        from urllib.parse import parse_qs, urlsplit
        opensky_query = parse_qs(urlsplit(fr.calls[-1][0]).query)
        check(47.0 < float(opensky_query["lamin"][0]) < 50.0
              < float(opensky_query["lamax"][0]) < 53.0,
              "Direkte OpenSky-BBox bleibt auch bei weltweitem Karten-Viewport auf 250 km begrenzt")

        fr.fail_osk = True
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(ac and ac[0]["flight"] == "DLH9X", "Cache als letzte Stufe")
    finally:
        tc.requests = orig_requests
        tc._last_adsb_data = []

    print("== HTTP 429: Cooldowns + Proxy-Backoff ==")
    tc.requests = fr
    fr.fail_proxy = False
    fr.fail_lol = False
    fr.fail_fi = False
    fr.fail_osk = False
    fr.proxy_429 = False
    fr.lol_429 = False
    tc.SOURCES._src.clear()
    tc._proxy_ok()
    try:
        fr.lol_429 = True
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(ac and ac[0]["flight"] == "FI1", "429 auf adsb.lol → adsb.fi liefert")
        e = tc.SOURCES.snapshot().get("adsb.lol") or {}
        check(e.get("code") == 429 and e.get("cooldown_until", 0) > time.time(),
              "429 im Statusregister mit Cooldown")
        fr.calls.clear()
        fr.lol_429 = False
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(not any("adsb.lol" in u for u, _ in fr.calls),
              "adsb.lol wird im Cooldown übersprungen")
        check(ac and ac[0]["flight"] == "FI1", "adsb.fi liefert weiter")

        # Proxy-429 -> down + Backoff
        tc.COLOR_SCHEME["proxy"] = {"url": "http://proxy.test/ltu/index.php",
                                    "token": "sekret"}
        fr.proxy_429 = True
        tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(tc.SOURCES.proxy_down(), "Proxy-429 → proxy_down")
        tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(tc._proxy_state["fails"] >= 2 and tc._proxy_backoff_active(),
              "Backoff nach 2 Fehlern aktiv")
        fr.calls.clear()
        tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(not any("proxy.test" in u for u, _ in fr.calls),
              "Proxy wird im Backoff nicht mehr angefragt")
        fr.proxy_429 = False
        tc._proxy_ok()
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(ac and ac[0]["flight"] == "PROX1" and not tc.SOURCES.proxy_down(),
              "Erfolgreicher Probe-Fetch setzt Proxy zurück")
    finally:
        fr.lol_429 = False
        fr.proxy_429 = False
        tc.SOURCES._src.clear()
        tc._proxy_ok()
        tc.requests = orig_requests
        if orig_proxy is None:
            tc.COLOR_SCHEME.pop("proxy", None)
        else:
            tc.COLOR_SCHEME["proxy"] = orig_proxy

    print("== Peer-Mesh: Ersatz-Proxy liefert Traffic ==")

    class FakeMesh:
        """Spiegelt peer_net.PeerNet: Web-Relay-Pull + Relay-Offer."""
        def __init__(self):
            self.calls = []
            self.offers = []
            self.ac = [{"flight": "MESH1", "lat": 50.0, "lon": 8.0}]

        def ersatz_name(self):
            return "peer-7"

        def client_fetch(self, lat, lon, dist, bounds=None):
            self.calls.append((lat, lon, dist, bounds))
            if self.ac is not None:
                tc.SOURCES.report("ersatz", True, 200, "web-relay: peer-7")
            else:
                tc.SOURCES.report("ersatz", False, None, "stale")
            return self.ac

        def relay_offer(self, lat, lon, dist, bounds, ac):
            self.offers.append((lat, lon, dist, bounds, ac))
            return True

        def role_view(self):
            return {"enabled": True, "role": "client", "mesh_active": True,
                    "ersatz": {"name": "peer-7", "uid": "peer7"},
                    "peers": 2, "uid": "self1", "via": "web"}

    fm = FakeMesh()
    tc.MESH = fm
    tc.requests = fr
    tc.SOURCES._src.clear()
    tc._proxy_state["fails"] = 5
    tc._proxy_state["next_probe"] = time.time() + 60
    tc.COLOR_SCHEME["proxy"] = {"url": "http://proxy.test/ltu/index.php",
                                "token": "sekret"}
    try:
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250, bounds=(51.0, 49.0, 9.0, 7.0))
        check(len(fm.calls) == 1 and fm.calls[0][3] == (51.0, 49.0, 9.0, 7.0),
              "Ersatz-Proxy wird mit BBox angefragt")
        check(ac and ac[0]["flight"] == "MESH1", "Daten vom Ersatz-Proxy")
        check((tc.SOURCES.snapshot().get("ersatz") or {}).get("ok") is True,
              "Ersatz-Status ok")
        fm.ac = None
        fm.calls.clear()
        ac = tc.fetch_adsb_traffic(50.0, 8.0, 250)
        check(ac and ac[0]["flight"] == "DIR1",
              "Ersatz ohne Antwort → Direktkette zieht trotzdem")
        check(len(fm.offers) == 1 and fm.offers[0][4] is ac,
              "Direkt-Fetch wird dem Mesh als Relay-Offer angeboten")
    finally:
        tc.MESH = None
        tc.requests = orig_requests
        tc.SOURCES._src.clear()
        tc._proxy_ok()
        if orig_proxy is None:
            tc.COLOR_SCHEME.pop("proxy", None)
        else:
            tc.COLOR_SCHEME["proxy"] = orig_proxy

    print("== proxy_config: env → page.txt → colors.json ==")
    orig_env = os.environ.pop("LTU_PROXY_URL", None)
    orig_cfg = tc.COLOR_SCHEME.get("proxy")
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "page.txt"), "w", encoding="utf-8") as f:
                f.write("# Kommentarzeile\nhttps://seite.test/ltu/index.php/\n")
            orig_file = tc.__file__
            tc.__file__ = os.path.join(td, "traffic_core.py")
            try:
                check(_REAL_PAGE_TXT() == "https://seite.test/ltu/index.php",
                      "page.txt: Kommentar uebersprungen, URL gefunden, / gekappt")
            finally:
                tc.__file__ = orig_file
        tc.COLOR_SCHEME["proxy"] = {"url": "http://cfg.test/p", "token": ""}
        tc._page_txt_url = lambda: "http://page.test/p/"
        u, _t = tc.proxy_config()
        check(u == "http://page.test/p", "page.txt schlaegt colors.json")
        os.environ["LTU_PROXY_URL"] = "http://env.test/p/"
        u, _t = tc.proxy_config()
        check(u == "http://env.test/p", "LTU_PROXY_URL schlaegt page.txt")
        del os.environ["LTU_PROXY_URL"]
        tc._page_txt_url = lambda: ""
        u, _t = tc.proxy_config()
        check(u == "http://cfg.test/p", "ohne page.txt: colors.json greift")
    finally:
        tc._page_txt_url = lambda: ""
        if orig_env is not None:
            os.environ["LTU_PROXY_URL"] = orig_env
        if orig_cfg is None:
            tc.COLOR_SCHEME.pop("proxy", None)
        else:
            tc.COLOR_SCHEME["proxy"] = orig_cfg

    tc.DIRECT_OPENSKY_MIN_INTERVAL = orig_direct_opensky_interval
    print()
    if ERRORS:
        print(f"{len(ERRORS)} FEHLGESCHLAGENE CHECKS:")
        for e in ERRORS:
            print(" -", e)
        sys.exit(1)
    print("ALLE CHECKS BESTANDEN ✔")


if __name__ == "__main__":
    main()
