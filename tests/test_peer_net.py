"""
Testet das Web-Relay-Mesh (peer_net.py) OHNE Sockets und ohne echtes HTTP:

    python tests/test_peer_net.py

Der Server (index.php) wird durch FakeServer gespiegelt: serverseitige
ZUFALLS-Wahl des Ersatz-Proxys, Ketten-Failover (Ersatz down -> naechster),
429-Yield (net_ok=false -> 120 s Pause), Erholung des Proxys, relay_push
(nur gewaehlte UID) und relay_pull (BBox-/Kreis-Filter, stale).

Geprueft wird das Client-Verhalten: Heartbeat-Payload/Antwort-Parsing,
Rollenwechsel, Relay-Push (Rate-Limit, nur als Ersatz), Relay-Pull in der
Fetch-Kette und die Status-Reports (SOURCES).
"""

import json
import os
import random
import sys
import time
from urllib.parse import urlparse, parse_qs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import peer_net as pn            # noqa: E402
import traffic_core as tc        # noqa: E402

ERRORS = []


def check(cond, msg):
    if cond:
        print(f"  [OK] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        ERRORS.append(msg)


URL = "http://web.test/ltu/index.php"

# peer_net soll immer auf die Fake-Webseite zeigen (page.txt/colors.json egal)
pn.proxy_config = lambda: (URL, "")
tc._page_txt_url = lambda: ""    # page.txt darf Tests nicht beeinflussen


# =========================================================
# Fake-Webseite (spiegelt index.php: Wahl + Relay)
# =========================================================

FRESH = 90          # LTU_CLIENT_FRESH
ERSATZ_STALE = 45   # LTU_ERSATZ_STALE
YIELD = 120         # LTU_YIELD_SEC
HB_NORMAL = 30      # LTU_HB_NORMAL
HB_MESH = 10        # LTU_HB_MESH
RELAY_MAX_AGE = 25  # LTU_RELAY_MAX_AGE


class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeServer:
    """Server-Seite: Heartbeat-Wahl (wie ltu_mesh_decide) + Relay-Speicher."""

    def __init__(self, collector_online=False, seed=None):
        self.now = 1000
        self.collector_online = collector_online
        self.clients = {}
        self.mesh = {"ersatz_uid": "", "elected_at": 0, "yields": {}}
        self.relay = None
        self.rng = random.Random(seed)

    def heartbeat(self, hb):
        uid = hb.get("uid")
        if not uid:
            return {"ok": False, "error": "bad uid"}
        self.clients[uid] = {
            "t": self.now, "name": hb.get("name", uid),
            "role": hb.get("role", "client"),
            "proxy_ok": bool(hb.get("proxy_ok")), "net_ok": bool(hb.get("net_ok")),
        }
        return self.decide(uid)

    def decide(self, uid):
        fresh = {u: c for u, c in self.clients.items() if self.now - c["t"] <= FRESH}
        down = sum(1 for c in fresh.values() if not c["proxy_ok"])
        unhealthy = (not self.collector_online) or (fresh and down * 2 >= len(fresh))
        base = {"ok": True, "t": self.now, "clients": len(fresh)}
        if not unhealthy:
            self.mesh = {"ersatz_uid": "", "elected_at": 0, "yields": {}}
            base.update(role="client", mesh_active=False, ersatz=None,
                        ersatz_uid="", hb=HB_NORMAL)
            return base
        yields = {u: t for u, t in self.mesh["yields"].items() if t > self.now}
        for u, c in fresh.items():
            if not c["net_ok"]:
                yields[u] = self.now + YIELD        # 429-Sturm -> Pause
        self.mesh["yields"] = yields
        cands = sorted(u for u, c in fresh.items() if c["net_ok"] and u not in yields)
        cur = self.mesh["ersatz_uid"]
        cur_ok = (cur in fresh and fresh[cur]["net_ok"] and cur not in yields
                  and self.now - fresh[cur]["t"] <= ERSATZ_STALE)
        if not cur_ok:
            cur = self.rng.choice(cands) if cands else ""   # ZUFALLS-Wahl
            self.mesh["ersatz_uid"] = cur
            self.mesh["elected_at"] = self.now
        name = fresh[cur]["name"] if cur in fresh else None
        my_role = "ersatz" if cur and cur == uid else "client"
        base.update(role=my_role, mesh_active=True,
                    ersatz=None if my_role == "ersatz" else name,
                    ersatz_uid=uid if my_role == "ersatz" else cur,
                    hb=HB_MESH)
        return base

    def logout(self, uid):
        removed = self.clients.pop(uid, None) is not None
        if self.mesh.get("ersatz_uid") == uid:
            self.mesh["ersatz_uid"] = ""
            self.mesh["elected_at"] = 0
        self.mesh.get("yields", {}).pop(uid, None)
        return {"ok": True, "removed": removed}

    def relay_push(self, p):
        if not p.get("uid") or p["uid"] != self.mesh["ersatz_uid"]:
            return {"ok": False, "error": "not-ersatz"}
        self.relay = {"ts": time.time(), "uid": p["uid"], "name": p.get("name"),
                      "bbox": p.get("bbox"), "ac": p.get("ac", [])}
        return {"ok": True, "count": len(p.get("ac", []))}

    def relay_pull(self, qs):
        if not self.relay or time.time() - self.relay["ts"] > RELAY_MAX_AGE:
            return {"ok": False, "error": "stale"}
        bbox = None
        circle = None
        if "bbox" in qs:
            n, s, e, w = (float(x) for x in qs["bbox"][0].split(","))
            bbox = (n, s, e, w)
        elif "lat" in qs:
            lat, lon, dist = float(qs["lat"][0]), float(qs["lon"][0]), float(qs["dist"][0])
            dlat = dist / 111.0
            dlon = dist / max(1.0, 111.0 * (1 - lat * lat / 16200.0))  # grob
            bbox = (lat + dlat, lat - dlat, lon + dlon, lon - dlon)
            circle = (lat, lon, dist)
        ac = []
        for a in self.relay["ac"]:
            la, lo = a.get("lat"), a.get("lon")
            if la is None or lo is None:
                continue
            if bbox and not (bbox[1] <= la <= bbox[0] and bbox[3] <= lo <= bbox[2]):
                continue
            if circle:
                d = tc.haversine_km(circle[0], circle[1], la, lo) if hasattr(tc, "haversine_km") else 0.0
                if d > circle[2]:
                    continue
            ac.append(a)
        return {"ok": True, "ac": ac, "count": len(ac), "src": "ersatz-relay",
                "age": round(time.time() - self.relay["ts"], 1),
                "ersatz": self.relay.get("name")}


class FakeWeb:
    """Fake-requests-Modul: routet auf FakeServer, merkt sich alle Aufrufe."""

    def __init__(self, server):
        self.server = server
        self.posts = []      # [(url, payload)]
        self.gets = []       # [url]

    def post(self, url, data=None, headers=None, timeout=None):
        payload = json.loads(data) if data else {}
        self.posts.append((url, payload))
        action = parse_qs(urlparse(url).query).get("action", [""])[0]
        if action == "heartbeat":
            return FakeResp(200, self.server.heartbeat(payload))
        if action == "logout":
            return FakeResp(200, self.server.logout(payload.get("uid")))
        if action == "relay_push":
            return FakeResp(200, self.server.relay_push(payload))
        return FakeResp(400, {"ok": False, "error": "unknown action"})

    def get(self, url, headers=None, timeout=None):
        self.gets.append(url)
        qs = parse_qs(urlparse(url).query)
        if qs.get("action", [""])[0] == "relay_pull":
            return FakeResp(200, self.server.relay_pull(qs))
        return FakeResp(404, {"ok": False, "error": "unknown"})


def mk(server, name, uid):
    web = FakeWeb(server)
    node = pn.PeerNet(config={"enabled": True, "name": name}, uid=uid, poster=web)
    return node, web


def hb(node, web, proxy_ok=False, net_ok=True):
    """Heartbeat senden (Server antwortet mit Rolle); Response zurueckgeben."""
    node._hb_interval = 0                     # sofort faellig
    orig = tc.SOURCES.proxy_down, tc.SOURCES.direct_available
    tc.SOURCES.proxy_down = lambda *a: not proxy_ok
    tc.SOURCES.direct_available = lambda *a: net_ok
    try:
        return node.send_heartbeat(poster=web)
    finally:
        tc.SOURCES.proxy_down, tc.SOURCES.direct_available = orig


AC = [{"hex": "3c0001", "callsign": "DLH123", "lat": 50.1, "lon": 8.6, "alt_baro": 35000},
      {"hex": "3c0002", "callsign": "BAW456", "lat": 50.2, "lon": 8.7, "alt_baro": 24000},
      {"hex": "3c0003", "callsign": "OUTSIDE", "lat": 40.0, "lon": 3.0, "alt_baro": 10000}]


# =========================================================
print("== Konfiguration & Basics ==")
os.environ["LTU_MESH"] = "off"
check(pn.mesh_config()["enabled"] is False, "LTU_MESH=off deaktiviert das Mesh")
os.environ.pop("LTU_MESH")
check(pn.mesh_config()["enabled"] is True, "ohne Env-Override: enabled")
node = pn.PeerNet(config={"enabled": True, "name": "Test"}, uid="uid0")
view = node.role_view()
check(view["role"] == "client" and view["enabled"] and view["via"] == "web",
      "Startzustand: Client, via web")
check(view["ersatz"] is None and view["peers"] == 0, "noch kein Ersatz/Peers bekannt")
check(pn.bbox_from_area(50.4, 49.9, 250, (50.4, 49.9, 9.0, 8.0)) == [50.4, 49.9, 9.0, 8.0],
      "bbox aus Kartenausschnitt (n,s,e,w)")
b = pn.bbox_from_area(50.0, 8.0, 111, None)
check(abs((b[0] - 50.0) - 1.0) < 0.05 and b[1] < 50.0 < b[0], "bbox aus Kreis ~ dist/111")
tc.SOURCES._src.clear()

print("== Heartbeat: Payload + Rollen-Antwort (Server waehlt) ==")
srv = FakeServer(collector_online=False, seed=7)
a, weba = mk(srv, "Anna", "uidA")
resp = hb(a, weba, proxy_ok=False)
check(weba.posts and weba.posts[0][0] == URL + "?action=heartbeat", "POST an ?action=heartbeat")
pl = weba.posts[0][1]
check(pl["uid"] == "uidA" and pl["name"] == "Anna" and "statuses" in pl
      and pl["proxy_ok"] is False, "Payload: uid/name/statuses/proxy_ok")
check(resp["mesh_active"] is True and resp["role"] == "ersatz",
      "Collector down + 1 Client -> Server macht ihn zum Ersatz")
check(a.role_view()["role"] == "ersatz" and a.role_view()["mesh_active"],
      "Client uebernimmt Rolle aus der Antwort")
check("(du)" in (a.ersatz_name() or ""), "Ersatz-Name zeigt (du)")
check(a._hb_interval == HB_MESH, "Mesh aktiv -> Heartbeat-Intervall 10 s")
st = tc.SOURCES.snapshot().get("heartbeat")
check(st and st["ok"] is True, "SOURCES-Report heartbeat ok")
tc.SOURCES._src.clear()

print("== Zwei Clients: genau einer ist Ersatz, der andere kennt ihn ==")
srv = FakeServer(collector_online=False, seed=3)
bnode, webb = mk(srv, "Ben", "uidB")
hb(bnode, webb, proxy_ok=False)                 # Ben kommt zuerst -> evtl. Ersatz
anode, weba = mk(srv, "Anna", "uidA")
hb(anode, weba, proxy_ok=False)
hb(bnode, webb, proxy_ok=False)                 # beide auf gleichem Stand
roles = {"uidA": anode.role_view()["role"], "uidB": bnode.role_view()["role"]}
check(sorted(roles.values()) == ["client", "ersatz"],
      f"genau einer Ersatz, einer Client ({roles})")
follower = anode if roles["uidA"] == "client" else bnode
leader = bnode if roles["uidA"] == "client" else anode
check(follower.role_view()["ersatz"]["name"] == leader.name,
      "Client zeigt den gewaehlten Ersatz mit Namen")
check(follower.role_view()["peers"] == 2, "Peers-Anzahl vom Server (2 Clients)")
check(follower._hb_interval == HB_MESH, "beide im schnellen Mesh-Takt")
tc.SOURCES._src.clear()

print("== Ketten-Failover: Ersatz meldet 429 (net_ok=false) -> naechster ==")
winner_uid = srv.mesh["ersatz_uid"]
loser_uid = "uidB" if winner_uid == "uidA" else "uidA"
winner = leader
loser = follower
hb(winner, weba if winner is anode else webb, proxy_ok=False, net_ok=False)  # 429-Sturm
check(srv.mesh["ersatz_uid"] == loser_uid, "Server waehlt den naechsten (Ketten-Failover)")
check(srv.mesh["yields"].get(winner_uid, 0) > srv.now, "Zurueckgetretener im Yield (120 s)")
hb(loser, webb if loser is bnode else weba, proxy_ok=False)
check(loser.role_view()["role"] == "ersatz", "Naechster Client weiss: er ist Ersatz")
# Yielder bleibt draussen, auch wenn er sich wieder gesund meldet:
srv.now += 10
hb(winner, weba if winner is anode else webb, proxy_ok=False, net_ok=True)
check(srv.mesh["ersatz_uid"] == loser_uid, "Yielder kandidiert 120 s nicht wieder")
tc.SOURCES._src.clear()

print("== Ketten-Failover: Ersatz verstummt (down) -> naechster ==")
# Anna (Ersatz) sendet keine Heartbeats mehr; Zeit ueber Yield- UND Fresh-Fenster:
srv.now += YIELD + FRESH + 5
hb(winner, weba if winner is anode else webb, proxy_ok=False)   # nur der andere lebt
check(srv.mesh["ersatz_uid"] == winner_uid,
      "Stiller Ersatz wird ersetzt: der Lebende uebernimmt")
tc.SOURCES._src.clear()

print("== Proxy erholt sich: alle wieder Client, Mesh aus ==")
srv.collector_online = True
hb(winner, weba if winner is anode else webb, proxy_ok=True)
check(srv.mesh["ersatz_uid"] == "" and srv.mesh["yields"] == {}, "Wahl-Zustand zurueckgesetzt")
vw = winner.role_view()
check(vw["role"] == "client" and not vw["mesh_active"] and vw["ersatz"] is None,
      "Ersatz wird wieder Client")
check(winner._hb_interval == HB_NORMAL, "Heartbeat wieder alle 30 s")
tc.SOURCES._src.clear()

print("== Mehrheit meldet 429 vom Proxy -> Mesh startet trotz frischem Collector ==")
srv2 = FakeServer(collector_online=True, seed=11)
n1, w1 = mk(srv2, "C1", "u1")
n2, w2 = mk(srv2, "C2", "u2")
hb(n1, w1, proxy_ok=True)
check(srv2.mesh["ersatz_uid"] == "", "Collector online + Proxy ok -> kein Mesh")
hb(n1, w1, proxy_ok=False)      # C1 bekommt 429
hb(n2, w2, proxy_ok=False)      # C2 auch -> Mehrheit down
check(srv2.mesh["ersatz_uid"] in ("u1", "u2"), "429-Mehrheit -> Ersatz gewaehlt")
check(n1.role_view()["mesh_active"] and n2.role_view()["mesh_active"],
      "beide Clients sehen mesh_active")
tc.SOURCES._src.clear()

print("== Ersatz-Proxy: relay_offer + Push (Rate-Limit, nur als Ersatz) ==")
srv3 = FakeServer(collector_online=False, seed=5)
e, webe = mk(srv3, "Ersatz", "uidE")
c, webc = mk(srv3, "Client", "uidC")
hb(e, webe, proxy_ok=False)
hb(c, webc, proxy_ok=False)
hb(e, webe, proxy_ok=False)
assert srv3.mesh["ersatz_uid"] == "uidE", srv3.mesh
check(e.role_view()["role"] == "ersatz", "uidE ist der gewaehlte Ersatz")
check(e.relay_offer(50.0, 8.0, 45, (50.4, 49.9, 9.0, 8.0), AC) is True,
      "relay_offer als Ersatz angenommen")
check(e.push_pending(poster=webe) is True, "erster Push ok")
u, p = webe.posts[-1]
check(u == URL + "?action=relay_push" and p["uid"] == "uidE"
      and p["bbox"] == [50.4, 49.9, 9.0, 8.0] and len(p["ac"]) == 3,
      "Push-Payload: uid + bbox + ac")
st = tc.SOURCES.snapshot().get("ersatz")
check(st and st["ok"] is True and "relay-push 3" in str(st["detail"]),
      "SOURCES-Report ersatz ok (relay-push)")
e.relay_offer(50.0, 8.0, 45, None, AC)
check(e.push_pending(poster=webe) is False, "Rate-Limit: 2. Push zu frueh -> bleibt liegen")
e._last_push -= pn.PUSH_MIN_GAP + 0.1
check(e.push_pending(poster=webe) is True, "nach Min-Pause: Push erfolgt")
check(c.relay_offer(50.0, 8.0, 45, None, AC) is False, "Client bietet nicht an")
check(c.push_pending(poster=webc) is False, "Client pusht nicht")
# Unbefugter Push wird vom Server abgelehnt:
rogue, webr = mk(srv3, "Rogue", "uidR")
resp = webr.post(URL + "?action=relay_push", data=json.dumps({"uid": "uidR", "ac": AC}))
check(resp.json().get("ok") is False and resp.json().get("error") == "not-ersatz",
      "Server: relay_push nur von gewaehlter UID")
check(rogue.push_pending(poster=webr) is False, "push_pending ohne Pending = False")
tc.SOURCES._src.clear()

print("== Client: relay_pull in der Fetch-Kette ==")
got = c.client_fetch(50.0, 8.0, 45, (50.4, 49.9, 9.0, 8.0))
check(got is not None and len(got) == 2, "Pull liefert 2 AC (OUTSIDE weggefiltert)")
check(all(x["callsign"] != "OUTSIDE" for x in got), "BBox-Filter serverseitig wirksam")
u = webc.gets[-1]
check("action=relay_pull" in u and "bbox=50.4000,49.9000,9.0000,8.0000" in u,
      "Pull-URL mit action + bbox")
st = tc.SOURCES.snapshot().get("ersatz")
check(st and st["ok"] is True and "web-relay: Ersatz" in str(st["detail"]),
      "SOURCES-Report ersatz ok (web-relay)")
got2 = c.client_fetch(50.15, 8.65, 20, None)
check(got2 is not None and len(got2) == 2, "Kreis-Filter (lat/lon/dist) liefert")
check(e.client_fetch(50.0, 8.0, 45, None) is None and not webe.gets,
      "Ersatz pulled nicht selbst (kein Loopback)")
srv3.relay["ts"] -= RELAY_MAX_AGE + 1
check(c.client_fetch(50.0, 8.0, 45, None) is None, "stale Relay -> None (Direktkette zieht)")
st = tc.SOURCES.snapshot().get("ersatz")
check(st and st["ok"] is False and "stale" in str(st["detail"]),
      "SOURCES-Report ersatz down (stale)")
tc.SOURCES._src.clear()

print("== Lifecycle: start/stop ohne Sockets ==")
srv4 = FakeServer(collector_online=True, seed=1)
n4, web4 = mk(srv4, "Life", "uidL")
n4.poster = web4
n4.start()
time.sleep(0.4)
n4.stop()
check(any(u.endswith("?action=heartbeat") for u, _ in web4.posts),
      "Thread sendet Heartbeat nach start()")
check(any(u.endswith("?action=logout") for u, _ in web4.posts),
      "stop() meldet den Logout sofort an den Proxy")
check("uidL" not in srv4.clients, "ausgeloggter Client wird direkt aus dem Status entfernt")
check(not n4._running and n4._thread is None, "stop() beendet den Thread")

print("== apply_server: robuste Auswertung ==")
n5 = pn.PeerNet(config={"enabled": True, "name": "X"}, uid="uidX")
n5.apply_server({})                                  # leere Antwort: keine Crash
check(n5.role_view()["role"] == "client", "leere Antwort aendert nichts")
n5.apply_server({"role": "ersatz", "mesh_active": True, "hb": 10, "clients": 4})
check(n5.role_view()["role"] == "ersatz" and n5.role_view()["peers"] == 4,
      "Rolle ersatz + peers uebernommen")
n5.relay_offer(50, 8, 45, None, AC)
n5.apply_server({"role": "client", "mesh_active": False, "ersatz": "Anna", "hb": 30})
check(n5.role_view()["role"] == "client" and n5._pending is None,
      "Rolle verloren -> Pending-Push verworfen")
check(n5._hb_interval == HB_NORMAL, "hb-Intervall aus der Antwort (30 s)")

print()
if ERRORS:
    print(f"{len(ERRORS)} FEHLGESCHLAGENE CHECKS:")
    for e in ERRORS:
        print(" -", e)
    sys.exit(1)
print("ALLE CHECKS BESTANDEN \u2714")
