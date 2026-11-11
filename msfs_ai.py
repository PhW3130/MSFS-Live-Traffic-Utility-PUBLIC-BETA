"""
msfs_ai.py
==========
SimConnect-/MSFS-Anbindung: Spielerposition und optionale AI-Traffic-Injection
(EX1/Legacy-Spawn, Parked-Spawn, Positions-Updates, Despawn). Bewusst UI-unabhängig
– die Qt-GUI startet den Worker als Thread und zeigt den Verbindungsstatus an.
"""

import ctypes
import math
import os
import queue
import re
import time
import threading
from ctypes import cast, POINTER, pointer, sizeof, c_char_p, byref

from app_logging import ErrorStateTracker, log_action
from ctypes.wintypes import HANDLE, DWORD
from ctypes import c_long

try:
    from ctypes.wintypes import HRESULT
except ImportError:
    HRESULT = c_long

SIMCONNECT_AVAILABLE = False
SIMCONNECT_IMPORT_ERROR = ""
try:
    from SimConnect import *
    from SimConnect.Enum import (
        SIMCONNECT_RECV_ID,
        SIMCONNECT_RECV_ASSIGNED_OBJECT_ID,
        SIMCONNECT_RECV_EXCEPTION,
        SIMCONNECT_DATA_INITPOSITION,
        SIMCONNECT_DATATYPE,
        SIMCONNECT_EXCEPTION,
    )
    from SimConnect.Constants import (
        SIMCONNECT_UNUSED,
        INITPOSITION_AIRSPEED_CRUISE,
    )
    SIMCONNECT_AVAILABLE = True
except Exception as _sc_err:  # pragma: no cover - platformabhängig
    SIMCONNECT_IMPORT_ERROR = str(_sc_err)

from model_map import get_model_resolver
from routes import (
    RouteCache,
    is_on_ground,
    classify_airport_relation,
    airport_near,
    AIRPORT_RADIUS_KM,
)
from traffic_core import parse_adsb, fetch_adsb_traffic

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_SIMCONNECT_DLL = os.path.join(APP_DIR, "SimConnect.dll")

# MSFS spawned AI zuverlässig nur innerhalb ~35 km um das User-Flugzeug.
MAX_SPAWN_DISTANCE_KM = float(os.environ.get("MSFS_SPAWN_RADIUS_KM", "35"))
MAX_ACTIVE_AI = int(os.environ.get("MSFS_MAX_AI_AIRCRAFT", "25"))
SPAWN_SETTLE_SEC = float(os.environ.get("MSFS_SPAWN_SETTLE_SEC", "0.35"))
PLAYER_AIRPORT_ICAO = os.environ.get("MSFS_AIRPORT_ICAO", "").strip().upper()
PARKED_DEPART_GS = float(os.environ.get("MSFS_PARKED_DEPART_GS", "25"))
MAX_PARKED_WAIT_SEC = float(os.environ.get("MSFS_MAX_PARKED_WAIT_SEC", "900"))
MAX_PARKED_FILLERS = max(0, int(os.environ.get("MSFS_MAX_PARKED_FILLERS", "50")))
DEFAULT_PARKED_FILLER_MODEL = "FSLTL_A20N_ZZZZ"
PARKED_FILLER_MODEL_OVERRIDE = os.environ.get("MSFS_PARKED_FILLER_MODEL", "").strip()
PARKED_FILLER_MODEL = PARKED_FILLER_MODEL_OVERRIDE or DEFAULT_PARKED_FILLER_MODEL

CREATE_OBJECT_FAILED = (
    int(SIMCONNECT_EXCEPTION.SIMCONNECT_EXCEPTION_CREATE_OBJECT_FAILED)
    if SIMCONNECT_AVAILABLE else None
)


def _validated_coordinates(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("latitude/longitude are not numeric") from exc
    if (not math.isfinite(lat) or not math.isfinite(lon)
            or not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0):
        raise ValueError(f"latitude/longitude out of range: {lat!r}, {lon!r}")
    return lat, lon


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1 = _validated_coordinates(lat1, lon1)
    lat2, lon2 = _validated_coordinates(lat2, lon2)
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _clamp_near_player(lat, lon, plat, plon, max_km=MAX_SPAWN_DISTANCE_KM):
    lat, lon = _validated_coordinates(lat, lon)
    plat, plon = _validated_coordinates(plat, plon)
    try:
        max_km = float(max_km)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("spawn radius must be a finite non-negative distance") from exc
    if not math.isfinite(max_km) or max_km < 0:
        raise ValueError("spawn radius must be a finite non-negative distance")
    dist = _haversine_km(plat, plon, lat, lon)
    if dist <= max_km or dist <= 0:
        return lat, lon

    # Interpolate along the great-circle bearing, not linearly in degrees.
    # Linear longitude interpolation can jump almost around the planet when
    # the player and target straddle the antimeridian.
    phi1, lam1 = math.radians(plat), math.radians(plon)
    phi2, lam2 = math.radians(lat), math.radians(lon)
    delta_lon = lam2 - lam1
    bearing = math.atan2(
        math.sin(delta_lon) * math.cos(phi2),
        math.cos(phi1) * math.sin(phi2)
        - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon),
    )
    angular_distance = max_km / 6371.0
    phi3 = math.asin(
        math.sin(phi1) * math.cos(angular_distance)
        + math.cos(phi1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lam3 = lam1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(phi1),
        math.cos(angular_distance) - math.sin(phi1) * math.sin(phi3),
    )
    return math.degrees(phi3), (math.degrees(lam3) + 540.0) % 360.0 - 180.0


def _sanitize_tail(callsign, registration=None):
    raw = (registration or callsign or "N12345").strip().upper()
    cleaned = "".join(c for c in raw if c.isalnum())
    return (cleaned or "N12345")[:12]


def _numeric_alt(alt):
    if alt is None or alt == "ground":
        return 0.0
    try:
        return float(alt)
    except (TypeError, ValueError):
        return 0.0


def resolve_simconnect_dll():
    """Resolve an explicit DLL override, then the copy bundled with the app."""
    override = os.environ.get("MSFS_SIMCONNECT_DLL", "").strip().strip('"')
    candidates = []
    if override:
        candidates.append(os.path.abspath(os.path.expandvars(os.path.expanduser(override))))
    if not override or os.path.abspath(override) != os.path.abspath(BUNDLED_SIMCONNECT_DLL):
        candidates.append(BUNDLED_SIMCONNECT_DLL)

    for path in candidates:
        if not os.path.isfile(path):
            if path == candidates[0]:
                print(f"[SimConnect] DLL not found: {path}")
            continue
        try:
            lib = ctypes.WinDLL(path)
            if not hasattr(lib, "SimConnect_Open"):
                print(f"[SimConnect] Not a compatible SimConnect DLL: {path}")
                continue
        except (OSError, AttributeError) as e:
            print(f"[SimConnect] Cannot load {path}: {e}")
            continue
        return path

    print("[SimConnect] No usable SimConnect.dll found")
    return None


def configure_simconnect_library():
    dll_path = resolve_simconnect_dll()
    if dll_path:
        import SimConnect.SimConnect as sc_module
        sc_module._library_path = dll_path
        print(f"[SimConnect] Using SimConnect DLL: {dll_path}")
        return dll_path
    print("[SimConnect] Place a compatible SimConnect.dll beside the program or set MSFS_SIMCONNECT_DLL.")
    return None


def _ensure_ex1_api(sm):
    """Bind the newer EX1 spawn API when the loaded DLL provides it."""
    dll = sm.dll
    if getattr(dll, "_ex1_bound", False):
        return getattr(dll, "AICreateNonATCAircraft_EX1", None) is not None

    raw = dll.SimConnect
    fn = getattr(raw, "SimConnect_AICreateNonATCAircraft_EX1", None)
    if fn is None:
        dll._ex1_bound = True
        return False

    fn.restype = HRESULT
    # The SDK declares InitPos by value; passing byref corrupts the native call.
    fn.argtypes = [
        HANDLE, c_char_p, c_char_p, c_char_p,
        SIMCONNECT_DATA_INITPOSITION, DWORD,
    ]

    def _ex1_create(h, title, livery, tail, init_pos, request_id):
        return fn(h, title, livery, tail, init_pos, request_id)

    dll.AICreateNonATCAircraft_EX1 = _ex1_create
    dll._ex1_bound = True
    print("[SimConnect] EX1 AI spawn API ready")
    return True


def _ensure_legacy_spawn_api(sm):
    """Bind the original spawn call for SimConnect runtimes without EX1."""
    dll = sm.dll
    if getattr(dll, "_legacy_spawn_bound", False):
        return getattr(dll, "AICreateNonATCAircraft", None) is not None

    raw = dll.SimConnect
    fn = getattr(raw, "SimConnect_AICreateNonATCAircraft", None)
    if fn is None:
        dll._legacy_spawn_bound = True
        return False

    fn.restype = HRESULT
    # The legacy SDK signature also passes InitPos by value.
    fn.argtypes = [
        HANDLE, c_char_p, c_char_p, SIMCONNECT_DATA_INITPOSITION, DWORD,
    ]

    def _legacy_create(h, title, tail, init_pos, request_id):
        return fn(h, title, tail, init_pos, request_id)

    dll.AICreateNonATCAircraft = _legacy_create
    dll._legacy_spawn_bound = True
    print("[SimConnect] Legacy AI spawn API ready")
    return True


def _ensure_parked_ex1_api(sm):
    """Bind EX1 parked spawning; modular models additionally accept a livery name."""
    dll = sm.dll
    if getattr(dll, "_parked_ex1_bound", False):
        return getattr(dll, "AICreateParkedATCAircraft_EX1", None) is not None

    fn = getattr(dll.SimConnect, "SimConnect_AICreateParkedATCAircraft_EX1", None)
    if fn is None:
        dll._parked_ex1_bound = True
        return False

    fn.restype = HRESULT
    fn.argtypes = [HANDLE, c_char_p, c_char_p, c_char_p, c_char_p, DWORD]

    def _parked_ex1_create(h, title, livery, tail, airport, request_id):
        return fn(h, title, livery, tail, airport, request_id)

    dll.AICreateParkedATCAircraft_EX1 = _parked_ex1_create
    dll._parked_ex1_bound = True
    print("[SimConnect] EX1 parked-aircraft API ready")
    return True


class AISpawnManager:
    """Sequential AI spawns with ASSIGNED_OBJECT_ID tracking and position updates."""

    REQUEST_ID_BASE = 10000
    SPAWN_TIMEOUT_SEC = 20.0

    def __init__(self, sm):
        self.sm = sm
        self._has_ex1_api = _ensure_ex1_api(sm)
        self._has_legacy_api = _ensure_legacy_spawn_api(sm)
        self._has_parked_ex1_api = _ensure_parked_ex1_api(sm)
        if self._has_ex1_api:
            print("[SimConnect] Using EX1 aircraft spawning")
        elif self._has_legacy_api:
            print("[SimConnect] EX1 unavailable; using legacy aircraft spawning")
        else:
            print("[SimConnect] AI spawn API unavailable; player-position tracking remains enabled")
        self.pending_by_request = {}
        self.pending_by_send_id = {}
        # Timed-out requests can still receive late assignments or exceptions.
        # Keep bounded tombstones so late events cannot corrupt the next spawn.
        self._retired_request_ids = set()
        self._retired_object_ids = {}
        self._retired_send_ids = {}
        self._dispatch_callback_count = 0
        self._dispatch_assignment_count = 0
        self._dispatch_exception_count = 0
        self._prefer_legacy_api = False
        self.spawn_queue = []
        self.spawning = False
        self.current_req_id = None
        self.spawn_started_at = 0.0
        self._next_request_id = self.REQUEST_ID_BASE
        self.pos_def_id = None
        self.player_lat = 0.0
        self.player_lon = 0.0
        self.player_alt = 3000.0
        self.player_airport_icao = PLAYER_AIRPORT_ICAO
        self._has_parked_legacy_api = hasattr(
            self.sm.dll.SimConnect, "SimConnect_AICreateParkedATCAircraft"
        )
        self._has_parked_api = (
            self._has_parked_ex1_api or self._has_parked_legacy_api
        )
        self._patch_dispatch()

    def set_player_position(self, lat, lon, alt):
        if lat is not None and lon is not None:
            self.player_lat = float(lat)
            self.player_lon = float(lon)
        if alt is not None:
            self.player_alt = float(alt)

    def _alloc_request_id(self):
        rid = self._next_request_id
        self._next_request_id += 1
        return rid

    def _remember_retired_request(self, request_id):
        if request_id is None:
            return
        self._retired_request_ids.add(int(request_id))
        while len(self._retired_request_ids) > 512:
            oldest_request_id = min(self._retired_request_ids)
            self._retired_request_ids.remove(oldest_request_id)
            self._retired_object_ids.pop(oldest_request_id, None)

    def _remember_retired_send_id(self, send_id, request_id):
        if send_id is None or request_id is None:
            return
        self._retired_send_ids[int(send_id)] = int(request_id)
        while len(self._retired_send_ids) > 512:
            self._retired_send_ids.pop(min(self._retired_send_ids), None)

    def _forget_request(self, request_id, remember_late=False):
        if request_id is None:
            return None
        request_id = int(request_id)
        job = self.pending_by_request.pop(request_id, None)
        for send_id, mapped_request_id in list(self.pending_by_send_id.items()):
            if mapped_request_id == request_id:
                self.pending_by_send_id.pop(send_id, None)
                if remember_late:
                    self._remember_retired_send_id(send_id, request_id)
        if remember_late:
            self._remember_retired_request(request_id)
        return job

    def _retry_legacy_spawn(self, job, reason, request_id=None):
        """Retry an EX1 airborne spawn once through the legacy SimConnect API."""
        if (job is None or not self._has_legacy_api
                or job.get("_spawn_api") != "ex1"
                or job.get("_legacy_retry_attempted")):
            return False

        request_id = self.current_req_id if request_id is None else request_id
        self._forget_request(request_id, remember_late=True)
        job["_legacy_retry_attempted"] = True
        job["_force_legacy_api"] = True
        self._prefer_legacy_api = True
        self.spawning = False
        self.current_req_id = None
        self.spawn_started_at = 0.0
        log_action("simconnect.ai_spawn_legacy_fallback",
                   request_id=request_id, callsign=job["tail"],
                   title=job["title"], reason=str(reason),
                   subsequent_requests_use_legacy=True)
        print(f"[Spawn] EX1 produced no object for {job['tail']}; retrying once with legacy API")
        self._try_spawn_next()
        return True

    def _patch_dispatch(self):
        original = self.sm.my_dispatch_proc
        recv_assigned = int(SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_ASSIGNED_OBJECT_ID)
        recv_exception = int(SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EXCEPTION)

        def patched(p_data, cb_data, p_context):
            try:
                self._dispatch_callback_count += 1
                dw_id = int(p_data.contents.dwID)
                if dw_id == recv_assigned:
                    evt = cast(p_data, POINTER(SIMCONNECT_RECV_ASSIGNED_OBJECT_ID)).contents
                    request_id = int(evt.dwRequestID)
                    object_id = int(evt.dwObjectID)
                    self._dispatch_assignment_count += 1
                    log_action("simconnect.dispatch_assignment_received",
                               request_id=request_id, object_id=object_id)
                    self._on_object_id(request_id, object_id)
                elif dw_id == recv_exception:
                    exc = cast(p_data, POINTER(SIMCONNECT_RECV_EXCEPTION)).contents
                    self._dispatch_exception_count += 1
                    log_action("simconnect.dispatch_exception_received",
                               exception_code=int(exc.dwException),
                               send_id=int(exc.dwSendID),
                               unknown_send_id=int(exc.UNKNOWN_SENDID))
                    self._on_exception(exc)
            except Exception as exc:
                log_action("simconnect.dispatch_hook_error", error=str(exc))
                print(f"[SimConnect] Dispatch hook failed: {exc}")
            return original(p_data, cb_data, p_context)

        self.sm.my_dispatch_proc = patched
        # SimConnect captures a ctypes callback when it is initialized. Rebuild
        # that callback after replacing the Python dispatcher, otherwise AI
        # object-assignment events never reach AISpawnManager.
        dispatch_factory = getattr(self.sm.dll, "DispatchProc", None)
        callback_rebuilt = False
        if dispatch_factory is not None:
            try:
                self.sm.my_dispatch_proc_rd = dispatch_factory(patched)
                callback_rebuilt = True
            except Exception as exc:
                log_action("simconnect.dispatch_callback_rebind_failed", error=str(exc))
                print(f"[SimConnect] Could not re-register dispatch callback: {exc}")
        log_action("simconnect.dispatch_hook_installed",
                   callback_rebuilt=callback_rebuilt,
                   ex1_api=self._has_ex1_api,
                   legacy_api=self._has_legacy_api)

    def _on_object_id(self, request_id, object_id):
        request_id = int(request_id)
        object_id = int(object_id)
        job = self.pending_by_request.get(request_id)
        if job is None:
            was_retired = request_id in self._retired_request_ids
            self._forget_request(request_id)
            if was_retired:
                known_object_id = self._retired_object_ids.get(request_id)
                if known_object_id == object_id:
                    log_action("simconnect.ai_spawn_duplicate_assignment",
                               request_id=request_id, object_id=object_id)
                    return
                if known_object_id is not None:
                    log_action("simconnect.ai_spawn_duplicate_object_assignment",
                               request_id=request_id, object_id=object_id,
                               known_object_id=known_object_id)
                else:
                    log_action("simconnect.ai_spawn_late_assignment",
                               request_id=request_id, object_id=object_id)
                print(f"[Spawn] Removing late orphan object {object_id} for request {request_id}")
                self.remove_aircraft(object_id)
            else:
                log_action("simconnect.ai_spawn_unmatched_assignment",
                           request_id=request_id, object_id=object_id)
            return
        self._forget_request(request_id, remember_late=True)
        if job["dummy"].ai_id is not None:
            if job["dummy"].ai_id != object_id:
                self.remove_aircraft(object_id)
            self._remember_retired_request(request_id)
            self._retired_object_ids[request_id] = job["dummy"].ai_id
            return
        self._remember_retired_request(request_id)
        self._retired_object_ids[request_id] = object_id
        job["dummy"].ai_id = object_id
        job["dummy"].spawn_confirmed = True
        mode = job.get("spawn_mode", "airborne")
        log_action("simconnect.ai_spawn_confirmed", request_id=request_id,
                   object_id=int(object_id), callsign=job["tail"], mode=mode,
                   position=job.get("_position_diagnostics"))
        print(f"[Spawn] {job['tail']} assigned object id {object_id} ({mode})")
        self._notify_spawn_result(job, True, "")
        self._finish_current_spawn(job)

    def _on_exception(self, exc):
        exc_code = int(exc.dwException)
        unknown_send_id = int(exc.UNKNOWN_SENDID)
        send_id = int(exc.dwSendID)
        req_id = self.pending_by_send_id.get(unknown_send_id)
        if req_id is None:
            req_id = self.pending_by_send_id.get(send_id)
        if req_id is None:
            retired_req_id = self._retired_send_ids.pop(unknown_send_id, None)
            if retired_req_id is None:
                retired_req_id = self._retired_send_ids.pop(send_id, None)
            if retired_req_id is not None:
                log_action("simconnect.ai_spawn_late_exception",
                           request_id=retired_req_id, exception_code=exc_code,
                           unknown_send_id=unknown_send_id, send_id=send_id)
                return
        job = self.pending_by_request.get(req_id) if req_id is not None else None
        if job is None:
            log_action("simconnect.unmatched_exception", request_id=req_id,
                       current_request_id=self.current_req_id,
                       unknown_send_id=unknown_send_id, send_id=send_id,
                       exception_code=exc_code)
            return

        try:
            name = SIMCONNECT_EXCEPTION(exc_code).name
        except ValueError:
            name = f"exception_{exc_code}"

        log_action("simconnect.ai_spawn_exception", request_id=req_id,
                   callsign=job["tail"], title=job["title"],
                   exception=name, exception_code=exc_code)
        if exc_code == CREATE_OBJECT_FAILED:
            print(f"[Spawn] {job['tail']}: {name} (title={job['title']!r})")
        if self._retry_legacy_spawn(job, name, req_id):
            return
        self._forget_request(req_id, remember_late=True)
        self._fail_current_spawn(name, job)

    @staticmethod
    def _notify_spawn_result(job, success, reason=""):
        callback = job.get("on_result")
        if callback is None:
            return
        job["on_result"] = None
        try:
            callback(bool(success), str(reason or ""))
        except Exception as exc:
            print(f"[Spawn] Result callback failed: {exc}")

    def _finish_current_spawn(self, job):
        if self.spawn_queue and self.spawn_queue[0] is job:
            self.spawn_queue.pop(0)
        self.spawning = False
        self.current_req_id = None
        if SPAWN_SETTLE_SEC > 0:
            time.sleep(SPAWN_SETTLE_SEC)
        self._try_spawn_next()

    def _fail_current_spawn(self, reason, job=None):
        if job is None and self.spawn_queue:
            job = self.spawn_queue[0]
        if self.current_req_id is not None:
            self._forget_request(self.current_req_id, remember_late=True)
        self.spawning = False
        self.current_req_id = None

        if job is not None:
            if job["dummy"].ai_id is not None:
                self._notify_spawn_result(job, True, "")
                self._finish_current_spawn(job)
                return
            self._notify_spawn_result(job, False, reason)
            print(f"[Spawn] Failed for {job['tail']}: {reason}")
            if self.spawn_queue and self.spawn_queue[0] is job:
                self.spawn_queue.pop(0)

        self._try_spawn_next()

    def tick(self):
        """Unblock the spawn queue if the sim never returns an object id."""
        if not self.spawning or self.spawn_started_at <= 0:
            return

        job = self.spawn_queue[0] if self.spawn_queue else None
        if job and job["dummy"].ai_id is not None:
            self._finish_current_spawn(job)
            return

        if (time.time() - self.spawn_started_at) > self.SPAWN_TIMEOUT_SEC:
            baseline = (job or {}).get("_dispatch_baseline", (0, 0, 0))
            log_action(
                "simconnect.ai_spawn_timeout",
                request_id=self.current_req_id,
                callsign=(job or {}).get("tail"),
                title=(job or {}).get("title"),
                api=(job or {}).get("_spawn_api"),
                elapsed_sec=time.time() - self.spawn_started_at,
                dispatch_callbacks=max(0, self._dispatch_callback_count - baseline[0]),
                assignment_events=max(0, self._dispatch_assignment_count - baseline[1]),
                exception_events=max(0, self._dispatch_exception_count - baseline[2]),
            )
            if self._retry_legacy_spawn(
                    job, "timeout waiting for object id", self.current_req_id):
                return
            self._fail_current_spawn("timeout waiting for object id")

    def queue_spawn(self, dummy, title, tail, lat, lon, alt, heading,
                    ground_speed=0, spawn_mode="airborne", airport_icao="",
                    on_result=None, source_dst_nm=None):
        self.spawn_queue.append({
            "dummy": dummy,
            "title": title,
            "tail": _sanitize_tail(tail),
            "lat": lat,
            "lon": lon,
            "alt": _numeric_alt(alt),
            "heading": float(heading or 0),
            "ground_speed": float(ground_speed or 0),
            "spawn_mode": spawn_mode,
            "airport_icao": (airport_icao or "").upper()[:8],
            "source_dst_nm": source_dst_nm,
            "on_result": on_result,
        })
        self._try_spawn_next()

    def _build_init_position(self, job):
        lat, lon = _clamp_near_player(job["lat"], job["lon"], self.player_lat, self.player_lon)
        alt = job["alt"]
        gs = float(job.get("ground_speed") or 0)
        if alt < 500 and gs > 40:
            alt = max(500.0, self.player_alt)
        elif alt < 50 and gs <= 40:
            alt = max(alt, 0.0)

        on_ground = 1 if gs < 30 and alt < 1000 else 0
        airspeed = 0 if on_ground else int(INITPOSITION_AIRSPEED_CRUISE.value)

        return SIMCONNECT_DATA_INITPOSITION(
            Latitude=lat, Longitude=lon, Altitude=alt,
            Pitch=0.0, Bank=0.0, Heading=job["heading"] % 360.0,
            OnGround=on_ground, Airspeed=airspeed,
        )

    def _try_spawn_next(self):
        if self.spawning or not self.spawn_queue:
            return

        job = self.spawn_queue[0]
        try:
            init_pos = self._build_init_position(job)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            log_action(
                "simconnect.ai_spawn_invalid_position",
                callsign=job.get("tail"),
                spawn_mode=job.get("spawn_mode", "airborne"),
                requested_position={
                    "latitude": job.get("lat"),
                    "longitude": job.get("lon"),
                    "altitude_ft": job.get("alt"),
                    "heading_deg": job.get("heading"),
                },
                player_position={
                    "latitude": self.player_lat,
                    "longitude": self.player_lon,
                    "altitude_ft": self.player_alt,
                },
                source_dst_nm=job.get("source_dst_nm"),
                error=str(exc),
            )
            self._fail_current_spawn(f"invalid spawn position: {exc}", job)
            return

        requested_lat, requested_lon = _validated_coordinates(job["lat"], job["lon"])
        player_lat, player_lon = _validated_coordinates(self.player_lat, self.player_lon)
        final_lat, final_lon = _validated_coordinates(
            init_pos.Latitude, init_pos.Longitude
        )
        requested_distance_km = _haversine_km(
            player_lat, player_lon, requested_lat, requested_lon
        )
        final_distance_km = _haversine_km(
            player_lat, player_lon, final_lat, final_lon
        )
        source_dst_nm = _finite_simvar(job.get("source_dst_nm"))
        job["_position_diagnostics"] = {
            "requested": {"lat": round(requested_lat, 6),
                          "lon": round(requested_lon, 6)},
            "player": {"lat": round(player_lat, 6),
                       "lon": round(player_lon, 6)},
            "final": {"lat": round(final_lat, 6),
                      "lon": round(final_lon, 6)},
            "requested_distance_km": round(requested_distance_km, 2),
            "final_distance_km": round(final_distance_km, 2),
            "position_clamped": not math.isclose(
                requested_lat, final_lat, abs_tol=1e-7
            ) or not math.isclose(requested_lon, final_lon, abs_tol=1e-7),
            "source_dst_nm": source_dst_nm,
            "source_dst_km": round(source_dst_nm * 1.852, 2)
            if source_dst_nm is not None else None,
            "requested_alt_ft": round(float(job["alt"]), 1),
            "spawn_alt_ft": round(float(init_pos.Altitude), 1),
            "coordinate_position_used": job.get("spawn_mode") != "parked",
        }
        self.spawning = True

        req_id = self._alloc_request_id()
        self.current_req_id = req_id
        self.spawn_started_at = time.time()
        job["_dispatch_baseline"] = (
            self._dispatch_callback_count,
            self._dispatch_assignment_count,
            self._dispatch_exception_count,
        )
        self.pending_by_request[req_id] = job
        mode = job.get("spawn_mode", "airborne")
        log_action("simconnect.ai_spawn_requested", request_id=req_id,
                   callsign=job["tail"], title=job["title"], mode=mode,
                   airport=job.get("airport_icao") or None,
                   position=job["_position_diagnostics"])

        job["_spawn_api"] = "unknown"
        try:
            # The current title catalog is based on legacy aircraft.cfg/VMR
            # entries. Passing an empty EX1 livery selects the default; resolving
            # a particular modular livery via enumeration is not implemented yet.
            if mode == "parked" and job.get("airport_icao") and self._has_parked_ex1_api:
                job["_spawn_api"] = "parked_ex1"
                hr = self.sm.dll.AICreateParkedATCAircraft_EX1(
                    self.sm.hSimConnect,
                    job["title"].encode("utf-8"),
                    b"",
                    job["tail"].encode("utf-8"),
                    job["airport_icao"].encode("utf-8"),
                    req_id,
                )
                mode_label = f"parked EX1@{job['airport_icao']}"
            elif (mode == "parked" and job.get("airport_icao")
                  and self._has_parked_legacy_api):
                job["_spawn_api"] = "parked_legacy"
                hr = self.sm.dll.AICreateParkedATCAircraft(
                    self.sm.hSimConnect,
                    job["title"].encode("utf-8"),
                    job["tail"].encode("utf-8"),
                    job["airport_icao"].encode("utf-8"),
                    req_id,
                )
                mode_label = f"parked@{job['airport_icao']}"
            elif (self._has_ex1_api and not self._prefer_legacy_api
                  and not job.get("_force_legacy_api")):
                job["_spawn_api"] = "ex1"
                hr = self.sm.dll.AICreateNonATCAircraft_EX1(
                    self.sm.hSimConnect,
                    job["title"].encode("utf-8"),
                    b"",
                    job["tail"].encode("utf-8"),
                    init_pos,
                    req_id,
                )
                mode_label = "airborne EX1"
            elif self._has_legacy_api:
                job["_spawn_api"] = "legacy"
                hr = self.sm.dll.AICreateNonATCAircraft(
                    self.sm.hSimConnect,
                    job["title"].encode("utf-8"),
                    job["tail"].encode("utf-8"),
                    init_pos,
                    req_id,
                )
                mode_label = "airborne legacy"
            else:
                raise RuntimeError("No supported SimConnect AI spawn API is available")

            send_id = DWORD(0)
            self.sm.dll.GetLastSentPacketID(self.sm.hSimConnect, byref(send_id))
            send_id_value = int(send_id.value)
            if self.pending_by_request.get(req_id) is job:
                self.pending_by_send_id[send_id_value] = req_id
            else:
                # A very fast dispatch callback may have completed or retired
                # this request before GetLastSentPacketID returned.
                self._remember_retired_send_id(send_id_value, req_id)

            if self.sm.IsHR(hr, 0):
                log_action("simconnect.ai_spawn_request_accepted", request_id=req_id,
                           callsign=job["tail"], mode=mode_label)
                print(f"[Spawn] {mode_label} request sent for {job['tail']} "
                      f"(title={job['title']!r}) req={req_id}")
            else:
                log_action("simconnect.ai_spawn_request_rejected", request_id=req_id,
                           callsign=job["tail"], mode=mode_label, hresult=str(hr))
                if not self._retry_legacy_spawn(job, f"HRESULT={hr}", req_id):
                    self._forget_request(req_id, remember_late=True)
                    self._fail_current_spawn(f"HRESULT={hr}", job)
        except Exception as e:
            log_action("simconnect.ai_spawn_request_error", request_id=req_id,
                       callsign=job["tail"], mode=mode, error=str(e))
            if not self._retry_legacy_spawn(job, str(e), req_id):
                self._forget_request(req_id, remember_late=True)
                self._fail_current_spawn(str(e), job)

    def _ensure_position_definition(self):
        if self.pos_def_id is not None:
            return True
        self.pos_def_id = self.sm.new_def_id()
        err = self.sm.dll.AddToDataDefinition(
            self.sm.hSimConnect, self.pos_def_id.value,
            b"Initial Position", b"",
            SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_INITPOSITION,
            0, SIMCONNECT_UNUSED,
        )
        return self.sm.IsHR(err, 0)

    def set_aircraft_position(self, object_id, lat, lon, alt, heading):
        if object_id is None or not self._ensure_position_definition():
            return False

        init_pos = SIMCONNECT_DATA_INITPOSITION(
            Latitude=lat, Longitude=lon, Altitude=_numeric_alt(alt),
            Pitch=0.0, Bank=0.0, Heading=float(heading or 0),
            OnGround=0, Airspeed=0,
        )
        hr = self.sm.dll.SetDataOnSimObject(
            self.sm.hSimConnect, self.pos_def_id.value, DWORD(object_id),
            0, 0, sizeof(init_pos), pointer(init_pos),
        )
        return self.sm.IsHR(hr, 0)

    def remove_aircraft(self, object_id):
        req_id = self._alloc_request_id()
        hr = self.sm.dll.AIRemoveObject(self.sm.hSimConnect, DWORD(object_id), req_id)
        success = self.sm.IsHR(hr, 0)
        log_action("simconnect.ai_remove", object_id=object_id,
                   request_id=req_id, success=success)
        return success


def msfs_connect():
    log_action("simconnect.connect_requested", package_available=SIMCONNECT_AVAILABLE)
    if not SIMCONNECT_AVAILABLE:
        log_action("simconnect.connect_unavailable", reason=SIMCONNECT_IMPORT_ERROR)
        print(f"[SimConnect] Python-Paket nicht verfügbar ({SIMCONNECT_IMPORT_ERROR}) → Kartenmodus")
        return None
    dll_path = configure_simconnect_library()
    if not dll_path:
        log_action("simconnect.connect_failed", reason="no_compatible_dll")
        return None
    log_action("simconnect.dll_selected", path=dll_path)
    try:
        sm = SimConnect(library_path=dll_path)
        sm._spawn_manager = AISpawnManager(sm)
        spawn_mode = (
            "EX1" if sm._spawn_manager._has_ex1_api else
            "legacy" if sm._spawn_manager._has_legacy_api else
            "disabled"
        )
        dll_name = getattr(sm.dll.SimConnect, "_name", "unknown")
        log_action("simconnect.connected", dll=dll_name, ai_spawn_mode=spawn_mode,
                   parked_api=sm._spawn_manager._has_parked_api)
        print(f"[SimConnect] Connected (AI spawn: {spawn_mode}) DLL={dll_name}")
        return sm
    except Exception as e:
        log_action("simconnect.connect_failed", error=str(e), dll=dll_path)
        print(f"[SimConnect] Connection failed: {e}")
        return None


PLAYER_SIMVAR_CACHE_MS = 500
PLAYER_SIMVAR_ATTEMPTS = 30


def _player_aircraft_requests(sm):
    """Keep one SimConnect request registry per connection.

    AircraftRequests registers every requested SimVar in SimConnect. Recreating
    it on each polling tick leaks data definitions/request IDs and can exhaust
    the simulator's finite request table after a short time.
    """
    aq = getattr(sm, "_ltu_aircraft_requests", None)
    if aq is None:
        aq = AircraftRequests(sm, _time=PLAYER_SIMVAR_CACHE_MS,
                              _attemps=PLAYER_SIMVAR_ATTEMPTS)
        setattr(sm, "_ltu_aircraft_requests", aq)
    return aq


def _finite_simvar(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _simvar_validation_error(value, minimum=None, maximum=None):
    """Return a concise diagnostic for one raw SimConnect SimVar value."""
    if value is None:
        return "missing"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "not_numeric"
    if not math.isfinite(number):
        return "non_finite"
    if ((minimum is not None and number < minimum)
            or (maximum is not None and number > maximum)):
        return "out_of_range"
    return None


def get_player_position(sm):
    """Read a validated, fresh player position from the persistent SimConnect reader."""
    received = {}
    try:
        aq = _player_aircraft_requests(sm)
        received["PLANE_LATITUDE"] = aq.get("PLANE_LATITUDE")
        received["PLANE_LONGITUDE"] = aq.get("PLANE_LONGITUDE")
        received["PLANE_ALTITUDE"] = aq.get("PLANE_ALTITUDE")
        received["PLANE_HEADING_DEGREES_TRUE"] = aq.get(
            "PLANE_HEADING_DEGREES_TRUE"
        )
        lat = _finite_simvar(received["PLANE_LATITUDE"])
        lon = _finite_simvar(received["PLANE_LONGITUDE"])
        alt = _finite_simvar(received["PLANE_ALTITUDE"])
        hdg = _finite_simvar(received["PLANE_HEADING_DEGREES_TRUE"])
    except Exception as exc:
        coordinate_errors = {
            "PLANE_LATITUDE": _simvar_validation_error(
                received.get("PLANE_LATITUDE"), -90.0, 90.0
            ),
            "PLANE_LONGITUDE": _simvar_validation_error(
                received.get("PLANE_LONGITUDE"), -180.0, 180.0
            ),
        }
        diagnostic = {
            "reason": "simvar_request_exception",
            "latitude_available": _finite_simvar(received.get("PLANE_LATITUDE")) is not None,
            "longitude_available": _finite_simvar(received.get("PLANE_LONGITUDE")) is not None,
            "altitude_available": _finite_simvar(received.get("PLANE_ALTITUDE")) is not None,
            "heading_available": _finite_simvar(
                received.get("PLANE_HEADING_DEGREES_TRUE")
            ) is not None,
            "coordinates_in_range": False,
            "coordinate_errors": {
                name: reason for name, reason in coordinate_errors.items() if reason
            },
            "raw_position": {
                "latitude": received.get("PLANE_LATITUDE"),
                "longitude": received.get("PLANE_LONGITUDE"),
                "altitude_ft": received.get("PLANE_ALTITUDE"),
                "heading_raw_rad": received.get("PLANE_HEADING_DEGREES_TRUE"),
            },
            "received_simvars": received,
            "error": str(exc),
        }
        try:
            sm._ltu_player_position_diagnostics = diagnostic
        except Exception:
            pass
        print(f"[SimConnect] Player position request failed: {exc}")
        return None, None, None, None

    coordinate_errors = {
        "PLANE_LATITUDE": _simvar_validation_error(
            received.get("PLANE_LATITUDE"), -90.0, 90.0
        ),
        "PLANE_LONGITUDE": _simvar_validation_error(
            received.get("PLANE_LONGITUDE"), -180.0, 180.0
        ),
    }
    coordinate_errors = {
        name: reason for name, reason in coordinate_errors.items() if reason
    }
    in_range = not coordinate_errors
    diagnostic = {
        "reason": "ok" if in_range else "missing_or_out_of_range_position",
        "latitude_available": lat is not None,
        "longitude_available": lon is not None,
        "altitude_available": alt is not None,
        "heading_available": hdg is not None,
        "coordinates_in_range": in_range,
        "coordinate_errors": coordinate_errors,
        "raw_position": {
            "latitude": received.get("PLANE_LATITUDE"),
            "longitude": received.get("PLANE_LONGITUDE"),
            "altitude_ft": received.get("PLANE_ALTITUDE"),
            "heading_raw_rad": received.get("PLANE_HEADING_DEGREES_TRUE"),
        },
        "normalized_position": ({"latitude": lat, "longitude": lon}
                                if in_range else None),
        "received_simvars": received,
    }
    try:
        sm._ltu_player_position_diagnostics = diagnostic
    except Exception:
        pass
    if not in_range:
        return None, None, alt, None

    # The Python SimConnect package requests this SimVar in radians even though
    # its name contains DEGREES; the rest of the application uses degrees.
    if hdg is not None:
        hdg = math.degrees(hdg) % 360.0
    return lat, lon, alt, hdg


def normalize_airport_icao(value):
    """Return a normalized four-character ICAO code, or an empty string."""
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="ignore")
    code = str(value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z0-9]{4}", code) else ""


def parse_sim_bool(value):
    """Parse SimConnect bool-like values without treating unknown as False."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="ignore")
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def get_player_airport_icao(sm):
    """Read the current airport from SimConnect, independent of live ADS-B."""
    if PLAYER_AIRPORT_ICAO:
        return normalize_airport_icao(PLAYER_AIRPORT_ICAO)
    try:
        aq = _player_aircraft_requests(sm)
    except Exception:
        return ""
    for simvar in ("ATC RUNWAY AIRPORT ICAO", "ATC CURRENT AIRPORT"):
        try:
            code = normalize_airport_icao(aq.get(simvar))
        except Exception:
            code = ""
        if code:
            return code
    return ""


def get_player_on_ground(sm):
    """Prefer MSFS's on-ground SimVar; use altitude/speed only as fallback."""
    try:
        aq = _player_aircraft_requests(sm)
    except Exception:
        return False

    try:
        on_ground = parse_sim_bool(aq.get("SIM_ON_GROUND"))
    except Exception:
        on_ground = None
    if on_ground is not None:
        return on_ground

    try:
        altitude = _finite_simvar(aq.get("PLANE_ALTITUDE"))  # feet
        ground_altitude_m = _finite_simvar(aq.get("GROUND_ALTITUDE"))  # meters
        ground_speed = _finite_simvar(aq.get("GROUND_VELOCITY"))  # knots
        if None in (altitude, ground_altitude_m, ground_speed):
            return False
        ground_altitude_ft = ground_altitude_m * 3.28084
        return abs(altitude - ground_altitude_ft) <= 25 and ground_speed <= 5
    except Exception:
        return False


class _ParkedFillerAircraft:
    """Small object used only to track a synthetic parked SimConnect object."""

    def __init__(self, tail):
        self.callsign = tail
        self.ai_id = None
        self.spawn_confirmed = False
        self.is_synthetic = True


class ParkedFillerController:
    """Best-effort static parking filler, kept separate from live traffic."""

    def __init__(self, spawn_mgr, max_aircraft=MAX_PARKED_FILLERS,
                 aircraft_title=PARKED_FILLER_MODEL):
        self.spawn_mgr = spawn_mgr
        self.max_aircraft = max(0, int(max_aircraft))
        self.aircraft_title = aircraft_title
        self.airport_icao = ""
        self.spawned_count = 0
        self.in_flight = False
        self.exhausted = False
        self.stopped = False
        self._generation = 0
        self._next_index = 1
        self._active = []
        self._results = queue.Queue()
        self._lock = threading.Lock()

    def set_airport(self, airport_icao):
        airport = normalize_airport_icao(airport_icao)
        if airport == self.airport_icao:
            return

        with self._lock:
            if self.stopped:
                return
            self._generation += 1
            generation = self._generation
            previous = self.airport_icao
            self.airport_icao = airport
            self.in_flight = False
            self.exhausted = False
            self.spawned_count = 0
            self._next_index = 1
            active, self._active = self._active, []

        for aircraft in active:
            if aircraft.ai_id is not None:
                self.spawn_mgr.remove_aircraft(aircraft.ai_id)
        if previous or airport:
            print(f"[Parking filler] Airport changed: {previous or '-'} -> {airport or '-'} "
                  f"(generation {generation})")

    def tick(self, on_ground, lat, lon):
        self._drain_results()
        if (self.stopped or not self.airport_icao or not on_ground
                or lat is None or lon is None or self.max_aircraft <= 0
                or self.in_flight or self.exhausted
                or self.spawned_count >= self.max_aircraft):
            return
        if not getattr(self.spawn_mgr, "_has_parked_api", False):
            self.exhausted = True
            print("[Parking filler] SimConnect parked-aircraft API unavailable")
            return

        tail = f"LTUPARK{self._next_index:04d}"[:12]
        self._next_index += 1
        aircraft = _ParkedFillerAircraft(tail)
        generation = self._generation
        self.in_flight = True

        def on_result(success, reason, generation=generation, aircraft=aircraft):
            with self._lock:
                stale = self.stopped or generation != self._generation
            if stale:
                if success and aircraft.ai_id is not None:
                    self.spawn_mgr.remove_aircraft(aircraft.ai_id)
                return
            self._results.put((generation, aircraft, success, reason))

        try:
            self.spawn_mgr.queue_spawn(
                dummy=aircraft,
                title=self.aircraft_title,
                tail=tail,
                lat=float(lat),
                lon=float(lon),
                alt=0,
                heading=0,
                ground_speed=0,
                spawn_mode="parked",
                airport_icao=self.airport_icao,
                on_result=on_result,
            )
            print(f"[Parking filler] Requesting {tail} at {self.airport_icao}")
        except Exception as exc:
            self.in_flight = False
            self.exhausted = True
            print(f"[Parking filler] Spawn request failed: {exc}")

    def _drain_results(self):
        while True:
            try:
                generation, aircraft, success, reason = self._results.get_nowait()
            except queue.Empty:
                return

            with self._lock:
                stale = self.stopped or generation != self._generation
            if stale:
                if success and aircraft.ai_id is not None:
                    self.spawn_mgr.remove_aircraft(aircraft.ai_id)
                continue

            self.in_flight = False
            if success:
                self._active.append(aircraft)
                self.spawned_count += 1
                print(f"[Parking filler] Spawned {aircraft.callsign} "
                      f"({self.spawned_count}/{self.max_aircraft})")
                if self.spawned_count >= self.max_aircraft:
                    print("[Parking filler] Configured safety cap reached")
            else:
                self.exhausted = True
                print("[Parking filler] Filling stopped after a parked-aircraft spawn failed "
                      f"at {self.airport_icao}: {reason or 'no further details'}")

    def stop(self):
        with self._lock:
            if self.stopped:
                return
            self.stopped = True
            self._generation += 1
            active, self._active = self._active, []
        for aircraft in active:
            if aircraft.ai_id is not None:
                self.spawn_mgr.remove_aircraft(aircraft.ai_id)


class DummyAircraft:
    STATE_PARKED = "parked"
    STATE_DEPARTING = "departing"
    STATE_AIRBORNE = "airborne"

    def __init__(self, sm, aircraft_title, info, route=None, spawn_mode="airborne", airport_icao=""):
        self.sm = sm
        self.spawn_mgr = getattr(sm, "_spawn_manager", None)
        self.aircraft_title = aircraft_title
        self.callsign = info["call"] or "NXXXX"
        self.route = route
        self.state = spawn_mode if spawn_mode == self.STATE_PARKED else self.STATE_AIRBORNE
        self.airport_icao = (airport_icao or "").upper()
        self.parked_since = time.time() if self.state == self.STATE_PARKED else None

        self.current_lat = info["lat"]
        self.current_lon = info["lon"]
        self.current_alt = _numeric_alt(info["alt_geom"] or info["alt_baro"])
        self.target_lat = self.current_lat
        self.target_lon = self.current_lon
        self.target_alt = self.current_alt

        if route and route.get("dest_lat") is not None:
            self.dest_lat = route["dest_lat"]
            self.dest_lon = route["dest_lon"]
            self.dest_icao = route.get("dest_icao", "")
            self.cruise_alt = max(25000.0, self.current_alt)
        else:
            self.dest_lat = None
            self.dest_lon = None
            self.dest_icao = ""
            self.cruise_alt = max(10000.0, self.current_alt)

        self.track = info["track"] or 0
        self.gs = info["gs"] or 0
        self.rate = info["baro_rate"] or 0
        self.last_seen = time.time()
        self.ai_id = None
        self.spawn_confirmed = False

        if self.spawn_mgr is None:
            print(f"[Dummy ERROR] No spawn manager for {self.callsign}")
            return

        self.spawn_mgr.queue_spawn(
            dummy=self, title=self.aircraft_title,
            tail=info.get("reg") or self.callsign,
            lat=self.current_lat, lon=self.current_lon, alt=self.current_alt,
            heading=self.track, ground_speed=info.get("gs") or 0,
            spawn_mode=self.state if self.state == self.STATE_PARKED else "airborne",
            airport_icao=self.airport_icao,
            source_dst_nm=info.get("dst_nm"),
        )

    def update_target(self, info, route=None):
        if route:
            self.route = route
            if route.get("dest_lat") is not None:
                self.dest_lat = route["dest_lat"]
                self.dest_lon = route["dest_lon"]
                self.dest_icao = route.get("dest_icao", self.dest_icao)

        self.target_lat = info["lat"]
        self.target_lon = info["lon"]
        self.target_alt = _numeric_alt(info["alt_geom"] or info["alt_baro"]) or self.target_alt
        self.track = info["track"] or self.track
        self.gs = float(info.get("gs") or self.gs or 0)
        self.rate = info.get("baro_rate") or self.rate
        self.last_seen = time.time()

        if self.state == self.STATE_PARKED and self._should_depart():
            self._begin_departure()

    def _should_depart(self):
        if not self.route or self.dest_lat is None:
            return False
        if self.gs >= PARKED_DEPART_GS:
            return True
        if self.rate and float(self.rate) > 200:
            return True
        return False

    def _begin_departure(self):
        self.state = self.STATE_DEPARTING
        self.target_alt = self.cruise_alt
        if self.dest_lat is not None and self.dest_lon is not None:
            self.target_lat = self.dest_lat
            self.target_lon = self.dest_lon
        print(f"[Traffic] {self.callsign} departing {self.airport_icao} "
              f"-> {self.dest_icao or 'destination'}")

    def _heading_to(self, lat, lon):
        dlon = math.radians(lon - self.current_lon)
        lat1 = math.radians(self.current_lat)
        lat2 = math.radians(lat)
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    def interpolate_and_update(self):
        if self.ai_id is None or self.spawn_mgr is None:
            return
        if self.state == self.STATE_PARKED:
            return
        if self.state == self.STATE_DEPARTING:
            self.target_lat = self.dest_lat if self.dest_lat is not None else self.target_lat
            self.target_lon = self.dest_lon if self.dest_lon is not None else self.target_lon
            self.target_alt = self.cruise_alt
            if self.current_alt >= self.cruise_alt * 0.85 and self.gs > 80:
                self.state = self.STATE_AIRBORNE

        alpha = 0.25
        self.current_lat += alpha * (self.target_lat - self.current_lat)
        self.current_lon += alpha * (self.target_lon - self.current_lon)
        self.current_alt += alpha * (self.target_alt - self.current_alt)

        if self.dest_lat is not None and self.state != self.STATE_PARKED:
            heading = self._heading_to(self.dest_lat, self.dest_lon)
        else:
            heading = self.track or 0

        if not self.spawn_mgr.set_aircraft_position(
            self.ai_id, self.current_lat, self.current_lon, self.current_alt, heading
        ):
            print(f"[Dummy ERROR] Update failed ({self.callsign})")

    def should_despawn(self, timeout=30):
        if self.state == self.STATE_PARKED and self.parked_since:
            if (time.time() - self.parked_since) > MAX_PARKED_WAIT_SEC:
                return True
        return (time.time() - self.last_seen) > timeout

    def despawn(self):
        if self.ai_id is None or self.spawn_mgr is None:
            return
        if self.spawn_mgr.remove_aircraft(self.ai_id):
            print(f"[Dummy] Removed {self.callsign}")
        else:
            print(f"[Dummy ERROR] Remove failed ({self.callsign})")


def _infer_player_airport(plat, plon, traffic, route_cache):
    """Guess player airport ICAO from grounded traffic near the user."""
    if PLAYER_AIRPORT_ICAO:
        return PLAYER_AIRPORT_ICAO

    votes = {}
    for ac in traffic:
        info = parse_adsb(ac)
        if (not is_on_ground(info) or info.get("lat") is None
                or info.get("lon") is None):
            continue
        # Provider ``dst`` can refer to a different query center. Infer the
        # player's airport from these coordinates, not that cached distance.
        dist = _haversine_km(plat, plon, info["lat"], info["lon"])
        if dist > AIRPORT_RADIUS_KM:
            continue
        route = route_cache.get(info["call"])
        if not route:
            continue
        o_icao = route["origin_icao"]
        if airport_near(plat, plon, route.get("origin_lat"), route.get("origin_lon")):
            votes[o_icao] = votes.get(o_icao, 0) + 1

    if not votes:
        return ""
    return max(votes, key=votes.get)


class MsfsAiRunner:
    """Pollt die Spielerposition und steuert optionale AI-Injektion im Hintergrund."""

    def __init__(self, community_directory=None, aircraft_catalog=None,
                 require_aircraft_catalog=False):
        self.sm = None
        self._thread = None
        self._stop = None
        self._ai_injection_enabled = threading.Event()
        self._ai_injection_enabled.set()
        self._last_position_warning = 0.0
        self._position_unavailable_since = None
        self._position_unavailable_diagnostics = None
        self._position_available_reported = False
        self._loop_error_tracker = ErrorStateTracker()
        self.community_directory = community_directory or ""
        self.aircraft_catalog = aircraft_catalog
        self.require_aircraft_catalog = bool(require_aircraft_catalog)
        # optionales Callback fn(lat, lon, alt_ft, hdg_deg) – eigene Position
        self.on_own_position = None

    def connect(self):
        """Blockierender Verbindungsversuch (im Worker-Thread aufrufen)."""
        self.sm = msfs_connect()
        return self.sm

    @property
    def available(self):
        return self.sm is not None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def ai_injection_enabled(self):
        return self._ai_injection_enabled.is_set()

    def set_ai_injection_enabled(self, enabled):
        enabled = bool(enabled)
        changed = self.ai_injection_enabled != enabled
        if enabled:
            self._ai_injection_enabled.set()
        else:
            self._ai_injection_enabled.clear()
        if changed:
            log_action("msfs.ai_injection_toggled", enabled=enabled)
        return changed

    def _catalog_block_reason(self, catalog, catalog_has_titles):
        """Explain why spawning must wait for the selected Community catalog."""
        if catalog_has_titles:
            return None
        if not (self.require_aircraft_catalog or self.community_directory):
            return None
        if self.require_aircraft_catalog and not self.community_directory:
            return "awaiting_community_selection"
        return "catalog_loading" if catalog is None else "catalog_empty"

    def _report_player_position_unavailable(self, diagnostics, now=None):
        """Log detailed invalid coordinates at transitions and at a calm interval."""
        now = time.time() if now is None else float(now)
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        raw_position = diagnostics.get("raw_position")
        if not isinstance(raw_position, dict):
            received = diagnostics.get("received_simvars")
            received = received if isinstance(received, dict) else {}
            raw_position = {
                "latitude": received.get("PLANE_LATITUDE"),
                "longitude": received.get("PLANE_LONGITUDE"),
                "altitude_ft": received.get("PLANE_ALTITUDE"),
                "heading_raw_rad": received.get("PLANE_HEADING_DEGREES_TRUE"),
            }
        coordinate_errors = diagnostics.get("coordinate_errors")
        coordinate_errors = coordinate_errors if isinstance(coordinate_errors, dict) else {}

        if self._position_unavailable_since is None:
            self._position_unavailable_since = now
            self._position_unavailable_diagnostics = dict(diagnostics)
            log_action(
                "msfs.player_position_lost",
                reason=diagnostics.get("reason", "unknown"),
                raw_position=raw_position,
                coordinate_errors=coordinate_errors,
                diagnostics=diagnostics,
            )

        wait_sec = max(0.0, now - self._position_unavailable_since)
        if now - self._last_position_warning >= 10.0:
            log_action(
                "msfs.player_position_unavailable",
                wait_sec=round(wait_sec, 1),
                reason=diagnostics.get("reason", "unknown"),
                raw_position=raw_position,
                coordinate_errors=coordinate_errors,
                diagnostics=diagnostics,
            )
            print(
                "[MSFS] Waiting for a valid player position from SimConnect "
                f"(wait={wait_sec:.1f}s; reason={diagnostics.get('reason', 'unknown')}; "
                f"lat={raw_position.get('latitude')!r} "
                f"[{coordinate_errors.get('PLANE_LATITUDE', 'ok')}], "
                f"lon={raw_position.get('longitude')!r} "
                f"[{coordinate_errors.get('PLANE_LONGITUDE', 'ok')}], "
                f"alt_ft={raw_position.get('altitude_ft')!r}, "
                f"heading_raw_rad={raw_position.get('heading_raw_rad')!r})"
            )
            self._last_position_warning = now

    def _report_player_position_available(self, lat, lon, alt=None, heading=None):
        unavailable_since = self._position_unavailable_since
        if unavailable_since is None and self._position_available_reported:
            return False

        diagnostics = getattr(
            self.sm, "_ltu_player_position_diagnostics", {}
        ) or {}
        alt_ft = _finite_simvar(alt)
        heading_deg = _finite_simvar(heading)
        position = {
            "latitude": round(float(lat), 6),
            "longitude": round(float(lon), 6),
            "altitude_ft": round(alt_ft, 1) if alt_ft is not None else None,
            "heading_deg": round(heading_deg, 1) if heading_deg is not None else None,
        }
        if unavailable_since is None:
            log_action(
                "msfs.player_position_available",
                position=position,
                diagnostics=diagnostics,
            )
            print("[MSFS] Valid player position received: "
                  f"lat={position['latitude']:.6f}, "
                  f"lon={position['longitude']:.6f}, "
                  f"alt_ft={position['altitude_ft']!r}, "
                  f"heading_deg={position['heading_deg']!r}")
        else:
            unavailable_sec = max(0.0, time.time() - unavailable_since)
            log_action(
                "msfs.player_position_recovered",
                unavailable_sec=round(unavailable_sec, 1),
                position=position,
                previous_diagnostics=self._position_unavailable_diagnostics,
                diagnostics=diagnostics,
            )
            print("[MSFS] Player position valid again after "
                  f"{unavailable_sec:.1f}s: lat={position['latitude']:.6f}, "
                  f"lon={position['longitude']:.6f}, "
                  f"alt_ft={position['altitude_ft']!r}, "
                  f"heading_deg={position['heading_deg']!r}")

        self._position_available_reported = True
        self._position_unavailable_since = None
        self._position_unavailable_diagnostics = None
        return True

    def start(self):
        log_action("msfs.ai_runner_start_requested", available=self.available,
                   already_running=self.running,
                   ai_injection_enabled=self.ai_injection_enabled,
                   require_aircraft_catalog=self.require_aircraft_catalog)
        if not self.available or self.running:
            return
        self._last_position_warning = 0.0
        self._position_unavailable_since = None
        self._position_unavailable_diagnostics = None
        self._position_available_reported = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,),
                                        daemon=True, name="MSFS-AI-traffic")
        self._thread.start()
        if self.ai_injection_enabled:
            print("[MSFS] AI-Traffic-Injection gestartet")
        else:
            print("[MSFS] Spielerpositions-Tracking gestartet; AI-Injektion pausiert")

    def stop(self):
        log_action("msfs.ai_runner_stop_requested", was_running=self.running)
        if self._stop is not None:
            self._stop.set()
        self._thread = None
        print("[MSFS] SimConnect-Worker gestoppt")

    def _run(self, stop_event):
        log_action("msfs.ai_runner_loop_started")
        print("MSFS erkannt → SimConnect-Spielerpositions-Worker")
        # Delay loading modelmap.txt until spawning is actually allowed. When
        # the Community prompt is deferred, no model should be selected from
        # that global rule map before the selected folder has been scanned.
        model_map = None
        catalog = None
        catalog_has_titles = False
        route_cache = RouteCache()
        dummies = {}
        unavailable_title_keys = set()
        catalog_warning_state = None
        parked_filler = None
        filler_checked = False
        last_injection_enabled = self.ai_injection_enabled

        while not stop_event.is_set():
            try:
                injection_enabled = self.ai_injection_enabled
                if injection_enabled != last_injection_enabled:
                    log_action("msfs.ai_injection_state_applied",
                               enabled=injection_enabled,
                               active_aircraft=len(dummies))
                    if not injection_enabled:
                        for call, dummy in list(dummies.items()):
                            try:
                                dummy.despawn()
                            except Exception as exc:
                                log_action("msfs.ai_despawn_error", callsign=call,
                                           error=str(exc))
                        dummies.clear()
                        if parked_filler is not None:
                            try:
                                parked_filler.stop()
                            except Exception as exc:
                                log_action("msfs.parked_filler_cleanup_failed",
                                           error=str(exc))
                            parked_filler = None
                        filler_checked = False
                    last_injection_enabled = injection_enabled

                selected_catalog = self.aircraft_catalog
                if selected_catalog is not catalog:
                    catalog = selected_catalog
                    catalog_has_titles = bool(catalog is not None and catalog.models)
                    catalog_warning_state = None
                    if catalog is not None:
                        log_action(
                            "msfs.aircraft_catalog_available",
                            community_directory=self.community_directory,
                            title_count=len(catalog.titles),
                        )
                catalog_block_reason = self._catalog_block_reason(
                    catalog, catalog_has_titles
                )
                catalog_blocked = catalog_block_reason is not None
                spawn_mgr = getattr(self.sm, "_spawn_manager", None)
                if (injection_enabled and spawn_mgr is not None and not filler_checked
                        and not catalog_blocked):
                    filler_checked = True
                    if MAX_PARKED_FILLERS > 0 and getattr(spawn_mgr, "_has_parked_api", False):
                        parked_title = PARKED_FILLER_MODEL
                        if catalog_has_titles and not PARKED_FILLER_MODEL_OVERRIDE:
                            parked_title = catalog.default_title(
                                preferred_title=DEFAULT_PARKED_FILLER_MODEL
                            ) or PARKED_FILLER_MODEL
                        parked_filler = ParkedFillerController(
                            spawn_mgr, aircraft_title=parked_title
                        )
                        log_action("msfs.parked_filler_model_selected", title=parked_title)
                        print(f"[Parking filler] Enabled (cap: {MAX_PARKED_FILLERS}, "
                              f"model: {parked_title or '(empty)'})")
                    elif MAX_PARKED_FILLERS > 0:
                        print("[Parking filler] Disabled: SimConnect parked-aircraft API unavailable")

                plat, plon, palt, phd = get_player_position(self.sm)
                if plat is None or plon is None:
                    if injection_enabled and spawn_mgr is not None:
                        spawn_mgr.tick()
                    diagnostics = getattr(
                        self.sm, "_ltu_player_position_diagnostics", {}
                    ) or {}
                    self._report_player_position_unavailable(diagnostics)
                    time.sleep(1)
                    continue
                self._report_player_position_available(plat, plon, palt, phd)
                self._last_position_warning = 0.0

                player_on_ground = (
                    get_player_on_ground(self.sm) if injection_enabled else False
                )
                if self.on_own_position is not None:
                    try:
                        self.on_own_position(plat, plon, palt, phd)
                    except Exception as e:
                        print(f"[MSFS] on_own_position Fehler: {e}")

                # SimConnect position polling is independent of AI spawning:
                # TCAS follow and the live map still need the player's position
                # while the user has paused simulator AI traffic.
                if not injection_enabled:
                    time.sleep(1)
                    continue

                if spawn_mgr is not None:
                    spawn_mgr.set_player_position(plat, plon, palt)
                    spawn_mgr.tick()

                traffic = fetch_adsb_traffic(plat, plon, 80)
                log_action("msfs.traffic_poll_completed",
                           aircraft_received=len(traffic) if traffic is not None else 0)
                player_airport = (get_player_airport_icao(self.sm)
                                  or _infer_player_airport(plat, plon, traffic, route_cache))
                if spawn_mgr is not None and player_airport:
                    if spawn_mgr.player_airport_icao != player_airport:
                        print(f"[Airport] Player airport: {player_airport}")
                    spawn_mgr.player_airport_icao = player_airport
                if parked_filler is not None:
                    if player_airport:
                        parked_filler.set_airport(player_airport)
                    parked_filler.tick(player_on_ground, plat, plon)

                seen_callsigns = set()
                for ac in traffic:
                    info = parse_adsb(ac)
                    call = info["call"]
                    if not call or info["lat"] is None or info["lon"] is None:
                        continue
                    try:
                        info["lat"], info["lon"] = _validated_coordinates(
                            info["lat"], info["lon"]
                        )
                    except ValueError as exc:
                        log_action("msfs.aircraft_position_invalid",
                                   callsign=call, latitude=info.get("lat"),
                                   longitude=info.get("lon"), error=str(exc))
                        continue
                    # Use the actual ADS-B and player coordinates; provider
                    # ``dst`` is a query-relative nautical-mile value and may
                    # be stale or centered on another client/collector.
                    dist_km = _haversine_km(plat, plon, info["lat"], info["lon"])
                    if dist_km > MAX_SPAWN_DISTANCE_KM:
                        continue

                    if call not in dummies and catalog_blocked:
                        reason = catalog_block_reason
                        warning_key = (self.community_directory, reason)
                        if warning_key != catalog_warning_state:
                            log_action(
                                "msfs.aircraft_catalog_not_spawnable",
                                community_directory=self.community_directory or None,
                                reason=reason,
                            )
                            if reason == "awaiting_community_selection":
                                print("[Aircraft] AI spawns paused until a Community "
                                      "folder is selected and scanned")
                            else:
                                print("[Aircraft] Waiting for a non-empty aircraft.cfg catalog "
                                      f"from {self.community_directory!r}; modelmap.txt alone "
                                      "does not verify that a model is installed")
                            catalog_warning_state = warning_key
                        continue
                    if not catalog_blocked:
                        catalog_warning_state = None

                    route = route_cache.get(call)
                    relation = classify_airport_relation(info, route, plat, plon, player_airport)

                    if is_on_ground(info):
                        if relation == "arriving":
                            if call in dummies:
                                dummies[call].despawn()
                                del dummies[call]
                            continue
                        if relation != "departing" or not player_airport:
                            continue

                    seen_callsigns.add(call)
                    if model_map is None:
                        model_map = get_model_resolver()
                    preferred_title = ""
                    if model_map.has_explicit_rule(info["type"], call):
                        preferred_title = model_map.resolve(info["type"], call)
                    if catalog_has_titles:
                        aircraft_title = catalog.resolve_title(
                            info["type"], callsign=call, preferred_title=preferred_title
                        )
                        if not aircraft_title:
                            unavailable_key = (call, info["type"])
                            if unavailable_key not in unavailable_title_keys:
                                unavailable_title_keys.add(unavailable_key)
                                log_action(
                                    "msfs.aircraft_title_unavailable",
                                    callsign=call,
                                    aircraft_type=info["type"],
                                )
                            continue
                        unavailable_title_keys.discard((call, info["type"]))
                    else:
                        if not model_map.is_spawnable(info["type"], call):
                            continue
                        aircraft_title = model_map.resolve(info["type"], call)
                    if call not in dummies and len(dummies) >= MAX_ACTIVE_AI:
                        continue
                    spawn_mode = "airborne"
                    airport_icao = ""
                    if is_on_ground(info) and relation == "departing" and player_airport:
                        spawn_mode = DummyAircraft.STATE_PARKED
                        airport_icao = player_airport

                    if call not in dummies:
                        log_action(
                            "msfs.aircraft_title_selected",
                            callsign=call,
                            aircraft_type=info["type"],
                            title=aircraft_title,
                            source=("aircraft.cfg" if catalog_has_titles
                                    else "modelmap.txt"),
                        )
                        dummies[call] = DummyAircraft(
                            self.sm, aircraft_title, info, route=route,
                            spawn_mode=spawn_mode, airport_icao=airport_icao,
                        )
                    else:
                        dummies[call].update_target(info, route=route)

                for call, dummy in list(dummies.items()):
                    if call in seen_callsigns:
                        dummy.interpolate_and_update()
                    elif dummy.should_despawn():
                        dummy.despawn()
                        del dummies[call]

                time.sleep(1)
            except Exception as e:
                first_failure = self._loop_error_tracker.record_failure(
                    "runner_loop", "msfs.ai_runner_error", e
                )
                if first_failure:
                    print("Fehler im MSFS‑Dummy‑Modus:", e)
                time.sleep(2)
            else:
                recovered = self._loop_error_tracker.record_recovery(
                    "runner_loop", "msfs.ai_runner_recovered"
                )
                if recovered:
                    print("[MSFS] AI-Traffic-Loop nach vorübergehendem Fehler wiederhergestellt")

        if parked_filler is not None:
            try:
                parked_filler.stop()
            except Exception as e:
                print(f"[Parking filler] Cleanup failed: {e}")
        for dummy in dummies.values():
            try:
                dummy.despawn()
            except Exception as exc:
                log_action("msfs.ai_despawn_error", callsign=dummy.callsign,
                           error=str(exc))
        log_action("msfs.ai_runner_loop_stopped")
        print("[MSFS] AI traffic mode beendet")
