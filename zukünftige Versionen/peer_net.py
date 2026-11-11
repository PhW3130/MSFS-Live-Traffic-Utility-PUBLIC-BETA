"""
peer_net.py
===========
Peer-Mesh für MSFS Live Traffic Utility – **komplett über die Webseite**
(index.php).  Es gibt keine direkten Verbindungen zwischen Clients,
keine UDP/TCP-Sockets, keine Ports: alles läuft als normale ausgehende
HTTP-Aufrufe über den Proxy (http oder https, je nach Server).  Router-/Firewall-Konfiguration entfällt.

Ablauf
------
1. **Heartbeat** (``?action=heartbeat``, alle 30 s bzw. 10 s im Mesh-Fall):
   jeder Client meldet ``proxy_ok`` / ``net_ok`` und seine Komponenten-
   Status.  **Der Server wählt** daraus *zufällig* einen Ersatz-Proxy,
   sobald der Proxy als down gilt – d. h. wenn der Collector keine
   frischen Daten liefert **oder** die Mehrheit der Clients ``proxy_ok=
   false`` meldet (Ausfall *oder HTTP 429*).  Die Wahl-Antwort kommt im
   Heartbeat-Response (``role``/``ersatz``/``mesh_active``/``hb``), alle
   Clients sehen dieselbe Wahrheit → sofort konvergent, auch über das
   Internet.
2. **Ketten-Failover**: fällt der gewählte Ersatz aus (Heartbeat > 45 s
   weg) oder meldet selbst ``net_ok=false`` (429-Sturm auf den Direkt-
   quellen), wählt der Server den **nächsten** Kandidaten; der Zurück-
   getretene pausiert 120 s (Yield).  Erholt sich der Proxy, werden alle
   wieder Client.
3. **Web-Relay**: der Ersatz-Proxy schiebt seine selbst gefetchten
   Traffic-Daten per ``?action=relay_push`` auf die Seite (BBox + AC-
   Liste, ~alle 5 s).  Alle anderen Clients fragen in der Fetch-Kette
   ``?action=relay_pull`` ab (BBox-/Radius-Filter serverseitig) und
   verbrauchen damit **ein gemeinsames Kontingent** statt jedes für sich.
4. **Status**: die öffentliche Statusseite ``?action=statuspage`` zeigt
   aus denselben Daten, welcher Teil läuft und welcher down ist.

Die URL der Webseite steht in ``page.txt`` (erste Zeile, gitignoriert)
bzw. ``colors.json`` → ``proxy.url`` / Umgebungsvariable ``LTU_PROXY_URL``.
``LTU_MESH=off`` deaktiviert das Mesh komplett.
"""

import hashlib
import json
import math
import os
import socket
import threading
import time

import requests

from app_logging import log_action, response_debug_payload
from traffic_core import SOURCES, proxy_config, proxy_preferences


def _relay_proxy_config():
    """Use the shared endpoint for mesh traffic even if direct proxying is disabled."""
    preferences = proxy_preferences()
    if not preferences["enabled"]:
        return preferences["url"], preferences["token"]
    return proxy_config()

HEARTBEAT_INTERVAL = 30.0    # s zwischen Heartbeats (Normalbetrieb)
MESH_HB_INTERVAL = 10.0      # s zwischen Heartbeats, solange Mesh aktiv
PUSH_INTERVAL = 5.0          # s Ziel-Abstand für Relay-Pushes
PUSH_MIN_GAP = 2.0           # s Mindest-Abstand (Rate-Limit, passt zur PHP)
PUSH_MAX_AGE = 20.0          # s: ältere Pending-Daten nicht mehr pushen
WORK_TICK = 1.0              # s Arbeits-Rhythmus des Mesh-Threads
MAX_AC_PUSH = 2000           # Obergrenze Flugzeuge pro Relay-Push
HTTP_TIMEOUT = 8
LOGOUT_TIMEOUT = 3


def make_uid():
    raw = f"{socket.gethostname()}|{os.getpid()}|{time.time()}|{id(object())}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def mesh_config():
    """peer_net-Konfiguration aus App-Einstellungen, colors.json und Env."""
    cfg = {}
    user_enabled = user_name = None
    try:
        from traffic_core import COLOR_SCHEME, _runtime_setting
        cfg = COLOR_SCHEME.get("peer_net") or {}
        user_enabled = _runtime_setting("peer_net_enabled")
        user_name = _runtime_setting("peer_net_name")
    except Exception:
        pass
    env_off = (os.environ.get("LTU_MESH") or "").strip().lower() in ("off", "0", "false", "no")
    enabled = bool(cfg.get("enabled", True)) if user_enabled is None else bool(user_enabled)
    enabled = enabled and not env_off
    try:
        name = (user_name if user_name is not None else cfg.get("name")) or socket.gethostname()
        name = str(name).strip() or socket.gethostname()
    except Exception:
        name = "client"
    return {"enabled": enabled, "name": name[:24]}


def bbox_from_area(lat, lon, dist_km, bounds=None):
    """[n, s, e, w] aus Kartenausschnitt, sonst aus Kreis (wie PHP ltu_bbox_from_circle)."""
    if bounds:
        try:
            n, s, e, w = (float(x) for x in bounds)
            return [n, s, e, w]
        except (TypeError, ValueError):
            pass
    d = max(1.0, float(dist_km or 250))
    dlat = d / 111.0
    dlon = d / max(1.0, 111.0 * math.cos(math.radians(lat)))
    return [min(90.0, lat + dlat), max(-90.0, lat - dlat),
            min(180.0, lon + dlon), max(-180.0, lon - dlon)]


class PeerNet:
    """Mesh-Knoten: Heartbeat an die Webseite, Rolle vom Server, Relay-Push/-Pull."""

    def __init__(self, config=None, uid=None, poster=None):
        self.config = config or mesh_config()
        self.enabled = bool(self.config.get("enabled"))
        self.uid = uid or make_uid()
        self.name = self.config.get("name") or self.uid
        self.poster = poster or requests
        self._lock = threading.Lock()
        self._role = "client"
        self._mesh_active = False
        self._ersatz = None          # {"name": …, "uid": …} | None
        self._peers = 0              # vom Server gemeldete Client-Anzahl
        self._hb_interval = HEARTBEAT_INTERVAL
        self._last_hb = 0.0
        self._pending = None         # {"ts", "bbox", "ac"} – wartet auf Relay-Push
        self._last_push = 0.0
        self._running = False
        self._thread = None

    # ---------------- Lifecycle ----------------
    def reconfigure(self, config=None):
        """Apply mesh preferences, stopping only when the relay is disabled."""
        config = config or mesh_config()
        was_running = self._running
        next_config = dict(config)
        next_enabled = bool(next_config.get("enabled"))
        if was_running and not next_enabled:
            self.stop()
        self.config = next_config
        self.enabled = next_enabled
        self.name = str(self.config.get("name") or self.uid)[:24]
        log_action("mesh.configuration_applied", enabled=self.enabled, name=self.name)
        if not was_running and self.enabled:
            self.start()

    def start(self):
        log_action("mesh.start_requested", enabled=self.enabled,
                   already_running=self._running)
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._work_loop, daemon=True,
                                        name="Web-relay-mesh")
        self._thread.start()
        print("[Mesh] Web-Relay gestartet (alles über die Webseite, keine Ports)")

    def stop(self):
        was_running = self._running or self._thread is not None
        log_action("mesh.stop_requested", was_running=was_running)
        self._running = False
        t = self._thread
        if t is not None:
            # Erst einen ggf. laufenden Heartbeat abschliessen, damit sein
            # Ergebnis den anschliessenden Logout nicht wieder ueberschreibt.
            t.join(timeout=HTTP_TIMEOUT + 1)
        self._thread = None
        if was_running:
            self.send_logout()

    def send_logout(self, poster=None):
        """Entfernt diesen Client sofort von der oeffentlichen Statusseite."""
        if not self.enabled:
            return False
        url, _token = _relay_proxy_config()
        if not url:
            log_action("mesh.logout_skipped", reason="no_proxy_url")
            return False
        poster = poster or self.poster
        log_action("mesh.logout_started")
        try:
            response = poster.post(
                f"{url}?action=logout",
                data=json.dumps({"uid": self.uid}),
                headers={"Content-Type": "application/json",
                         "User-Agent": "LTU-PeerNet/2"},
                timeout=LOGOUT_TIMEOUT)
            code = getattr(response, "status_code", 500)
            success = code < 300
            log_action("mesh.logout_completed", status_code=code, success=success)
            return success
        except Exception as exc:
            log_action("mesh.logout_failed", error=str(exc))
            return False

    def _sleep(self, seconds):
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(min(0.25, end - time.time()))

    def _work_loop(self):
        while self._running:
            now = time.time()
            if now - self._last_hb >= self._hb_interval:
                self._last_hb = now
                try:
                    self.send_heartbeat()
                except Exception:
                    pass
            with self._lock:
                is_ersatz = (self._role == "ersatz" and self._mesh_active)
            if is_ersatz:
                try:
                    self.push_pending()
                except Exception as e:
                    print(f"[Mesh] Relay-Push-Fehler: {e}")
            self._sleep(WORK_TICK)

    # ---------------- Heartbeat + Rolle vom Server ----------------
    def heartbeat_payload(self):
        with self._lock:
            role = self._role
        return {
            "uid": self.uid,
            "name": self.name,
            "role": role,
            "proxy_ok": not SOURCES.proxy_down(),
            "net_ok": SOURCES.direct_available(),
            "peers": 0,
            "statuses": SOURCES.snapshot(),
        }

    def send_heartbeat(self, poster=None):
        """Heartbeat POSTen; die Antwort des Servers setzt Rolle/Mesh-Zustand."""
        url, _token = _relay_proxy_config()
        if not url:
            log_action("mesh.heartbeat_skipped", reason="no_proxy_url")
            return None
        poster = poster or self.poster
        log_action("mesh.heartbeat_started")
        response = None
        try:
            response = poster.post(f"{url}?action=heartbeat",
                                   data=json.dumps(self.heartbeat_payload()),
                                   headers={"Content-Type": "application/json",
                                            "User-Agent": "LTU-PeerNet/2"},
                                   timeout=HTTP_TIMEOUT)
            code = getattr(response, "status_code", 500)
            data = None
            parse_error = None
            if code < 300:
                try:
                    data = response.json()
                except Exception as exc:
                    parse_error = str(exc)
            valid = (isinstance(data, dict) and bool(data)
                     and ("role" in data or "mesh_active" in data))
            ok = code < 300 and valid
            if ok:
                self.apply_server(data)
            elif code < 300:
                log_action("mesh.heartbeat_invalid_response", status_code=code,
                           error=parse_error or "missing role/mesh_active fields",
                           response=response_debug_payload(response))
            elif code >= 300:
                log_action("mesh.heartbeat_http_error", status_code=code,
                           response=response_debug_payload(response))
            SOURCES.report("heartbeat", ok,
                           code if hasattr(response, "status_code") else None,
                           "web-mesh" if ok else "invalid heartbeat response")
            data_for_log = data if isinstance(data, dict) else {}
            log_action("mesh.heartbeat_completed", status_code=code, success=ok,
                       role=data_for_log.get("role"),
                       mesh_active=data_for_log.get("mesh_active"))
            return data_for_log if ok else None
        except Exception as e:
            response_for_log = getattr(e, "response", None)
            if response_for_log is None:
                response_for_log = response
            SOURCES.report("heartbeat", False, None, str(e))
            log_action("mesh.heartbeat_failed", error=str(e),
                       response=response_debug_payload(response_for_log))
            return None

    def apply_server(self, data):
        """Server-Antwort auswerten: role/ersatz/mesh_active/hb/clients."""
        if not isinstance(data, dict) or not data:
            return
        role = "ersatz" if data.get("role") == "ersatz" else "client"
        with self._lock:
            was = self._role
            was_mesh_active = self._mesh_active
            self._mesh_active = bool(data.get("mesh_active"))
            self._role = role
            if role == "ersatz":
                self._ersatz = {"name": f"{self.name} (du)", "uid": self.uid}
            else:
                name = data.get("ersatz")
                self._ersatz = ({"name": name, "uid": data.get("ersatz_uid")}
                                if name else None)
            self._peers = int(data.get("clients") or 0)
            try:
                hb = float(data.get("hb") or 0)
            except (TypeError, ValueError):
                hb = 0.0
            self._hb_interval = hb if hb >= 5 else (
                MESH_HB_INTERVAL if self._mesh_active else HEARTBEAT_INTERVAL)
            if role != "ersatz" and was == "ersatz":
                self._pending = None       # Rolle verloren → nichts mehr pushen
        if role != was or self._mesh_active != was_mesh_active:
            log_action("mesh.server_state_applied", previous_role=was,
                       role=role, previous_mesh_active=was_mesh_active,
                       mesh_active=self._mesh_active,
                       peers=self._peers, replacement=self._ersatz)
        if role != was:
            print(f"[Mesh] Rolle: {role}"
                  + (f" (Ersatz: {self._ersatz.get('name')})"
                     if role == "client" and self._ersatz else ""))

    def role_view(self):
        with self._lock:
            return {
                "enabled": self.enabled,
                "uid": self.uid,
                "name": self.name,
                "role": self._role,
                "ersatz": dict(self._ersatz) if self._ersatz else None,
                "peers": self._peers,
                "mesh_active": self._mesh_active,
                "via": "web",
            }

    def ersatz_name(self):
        with self._lock:
            return (self._ersatz or {}).get("name")

    # ---------------- Ersatz-Proxy: Relay-Push ----------------
    def relay_offer(self, lat, lon, dist_km, bounds, ac):
        """traffic_core bietet frisch gefetchte Daten an; nur der gewählte
        Ersatz-Proxy legt sie für den nächsten Push bereit."""
        with self._lock:
            if self._role != "ersatz" or not self._mesh_active:
                return False
            queued_bbox = bbox_from_area(lat, lon, dist_km, bounds)
            self._pending = {
                "ts": time.time(),
                "bbox": queued_bbox,
                "ac": list(ac)[:MAX_AC_PUSH],
            }
            aircraft_count = len(self._pending["ac"])
        log_action("mesh.relay_data_queued", aircraft_count=aircraft_count,
                   bbox=queued_bbox)
        return True

    def push_pending(self, poster=None):
        """Bereitstehende Daten an ?action=relay_push schicken (Rate-Limit)."""
        now = time.time()
        with self._lock:
            if self._role != "ersatz" or not self._mesh_active:
                self._pending = None
                return False
            pending = self._pending
            if pending is None:
                return False
            if now - self._last_push < PUSH_MIN_GAP:
                return False                     # bleibt für nächsten Tick liegen
            if now - pending["ts"] > PUSH_MAX_AGE:
                self._pending = None             # zu alt → verwerfen
                return False
            self._pending = None
        url, _token = _relay_proxy_config()
        if not url:
            log_action("mesh.relay_push_skipped", reason="no_proxy_url")
            return False
        poster = poster or self.poster
        payload = {"uid": self.uid, "name": self.name,
                   "bbox": pending["bbox"], "ac": pending["ac"]}
        log_action("mesh.relay_push_started", aircraft_count=len(pending["ac"]),
                   bbox=pending["bbox"])
        response = None
        try:
            response = poster.post(f"{url}?action=relay_push",
                                   data=json.dumps(payload),
                                   headers={"Content-Type": "application/json",
                                            "User-Agent": "LTU-PeerNet/2"},
                                   timeout=HTTP_TIMEOUT)
            code = getattr(response, "status_code", 500)
            data = None
            parse_error = None
            try:
                data = response.json()
            except Exception as exc:
                parse_error = str(exc)
            valid = isinstance(data, dict) and isinstance(data.get("ok"), bool)
            ok = code < 300 and valid and data["ok"] is True
            if code < 300 and not valid:
                log_action("mesh.relay_push_invalid_response", status_code=code,
                           error=parse_error or "missing boolean 'ok' field",
                           response=response_debug_payload(response))
            elif code >= 300 or not ok:
                log_action("mesh.relay_push_rejected", status_code=code,
                           error=(data.get("error") if isinstance(data, dict) else None)
                           or "request was not accepted",
                           response=response_debug_payload(response))
            self._last_push = now
            data_error = data.get("error") if isinstance(data, dict) else None
            detail = (f"relay-push {len(pending['ac'])} AC" if ok
                      else f"relay-push: {data_error or 'failed'}")
            SOURCES.report("ersatz", ok,
                           code if hasattr(response, "status_code") else None, detail)
            log_action("mesh.relay_push_completed", status_code=code,
                       success=ok, aircraft_count=len(pending["ac"]))
            return ok
        except Exception as e:
            response_for_log = getattr(e, "response", None)
            if response_for_log is None:
                response_for_log = response
            SOURCES.report("ersatz", False, None, f"relay-push: {e}")
            log_action("mesh.relay_push_failed", error=str(e),
                       response=response_debug_payload(response_for_log),
                       aircraft_count=len(pending["ac"]))
            return False

    # ---------------- Client: Relay-Pull ----------------
    def client_fetch(self, lat, lon, dist, bounds=None):
        """Traffic aus dem Web-Relay holen.  None = kein frisches Relay
        (dann zieht die Direktkette).  Der Ersatz-Proxy selbst holt
        direkt (und pusht), um keinen Loopback zu fahren."""
        if not self.enabled:
            return None
        with self._lock:
            if self._role == "ersatz":
                return None
        url, _token = _relay_proxy_config()
        if not url:
            return None
        query = (f"{url}?action=relay_pull&lat={lat:.5f}&lon={lon:.5f}"
                 f"&dist={int(round(dist))}")
        if bounds:
            try:
                n, s, e, w = (float(x) for x in bounds)
                query += f"&bbox={n:.4f},{s:.4f},{e:.4f},{w:.4f}"
            except (TypeError, ValueError):
                pass
        log_action("mesh.relay_pull_started", center=[lat, lon],
                   radius_km=dist, has_bounds=bool(bounds))
        response = None
        try:
            response = self.poster.get(query, headers={"User-Agent": "LTU-PeerNet/2"},
                                       timeout=HTTP_TIMEOUT)
            code = getattr(response, "status_code", 500)
            if code >= 300:
                raise RuntimeError(f"HTTP {code}")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("relay-pull response is not a JSON object")
        except Exception as e:
            response_for_log = getattr(e, "response", None)
            if response_for_log is None:
                response_for_log = response
            SOURCES.report("ersatz", False,
                           getattr(response_for_log, "status_code", None), str(e))
            log_action("mesh.relay_pull_failed", center=[lat, lon], error=str(e),
                       response=response_debug_payload(response_for_log))
            return None
        if (not isinstance(data.get("ok"), bool)
                or (data["ok"] and not isinstance(data.get("ac"), list))):
            log_action("mesh.relay_pull_invalid_response",
                       error="missing boolean 'ok' or valid 'ac' array",
                       response=response_debug_payload(response))
            SOURCES.report("ersatz", False,
                           getattr(response, "status_code", None),
                           "invalid relay-pull response")
            return None
        if data.get("ok") and isinstance(data.get("ac"), list):
            who = data.get("ersatz") or "relay"
            aircraft = data["ac"]
            SOURCES.report("ersatz", True, 200,
                           f"web-relay: {who} ({data.get('age', '?')}s alt)")
            log_action("mesh.relay_pull_completed", center=[lat, lon],
                       aircraft_received=len(aircraft), replacement=who,
                       age_seconds=data.get("age"))
            return aircraft
        detail = str(data.get("error") or "kein frisches Relay")
        SOURCES.report("ersatz", False, None, detail)
        log_action("mesh.relay_pull_empty", center=[lat, lon], error=detail)
        return None
