"""
traffic_core.py
===============
Framework-unabhängiger Kern der Live-Traffic-Anwendung.

Enthält Theme-/Sprachladung, Hilfsfunktionen und die ``TrafficEngine``:
einen Background-Thread, der ADS-B-Daten pollt, filtert, auswählt,
Trails und Routen verwaltet und pro Tick einen JSON-fähigen State-Dict
erzeugt.  Die UI-Schicht (Qt in ``main.py``) konsumiert diesen State nur –
dadurch ist die gesamte Logik ohne GUI-Framework testbar.
"""

import json
import locale
import email.utils
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

import requests

from app_logging import ErrorStateTracker, log_action, response_debug_payload
from routes import RouteCache, is_on_ground, haversine_km

API_USER_AGENT = "MyExtremeFlightTracker/1.0"

if getattr(sys, "frozen", False):
    # PyInstaller-Bundle: Daten liegen neben dem Entpackpunkt
    APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


# =========================
# THEME / SPRACHE
# =========================

def load_color_scheme():
    default_scheme = {
        "map_settings": {
            "tile_server_url": "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "tile_attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            "tile_max_zoom": 19,
        },
        "altitude_gradient": {"low": [0, 0, 255], "mid": [255, 255, 0], "high": [255, 0, 0]},
        "gui_theme": {
            "main_bg": "#f0f0f0", "panel_bg": "#e0e0e0", "text_color": "#000000",
            "entry_bg": "#ffffff", "entry_fg": "#000000", "btn_bg": "#d0d0d0",
            "btn_fg": "#000000", "btn_disabled_bg": "#aaaaaa",
            "label_color": "#101018", "emergency_color": "#ff2d2d",
            "accent_color": "#4da3ff",
        },
        "aircraft_icons": {"size": 28, "selected_size": 34},
    }
    try:
        json_path = os.path.join(APP_DIR, "colors.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                scheme = json.load(f)
            for key, default in default_scheme.items():
                if key not in scheme:
                    scheme[key] = default
                elif isinstance(default, dict):
                    for sub, subval in default.items():
                        scheme[key].setdefault(sub, subval)
            return scheme
    except Exception as e:
        print(f"[Theme] Fehler beim Laden der JSON, nutze Defaults: {e}")
    return default_scheme


COLOR_SCHEME = load_color_scheme()


def detect_language():
    try:
        lang, _ = locale.getlocale()
        if not lang:
            lang = locale.getdefaultlocale()[0]
        lang = lang.lower()
        if lang.startswith("de"):
            return "de"
        if lang.startswith("en"):
            return "en"
        return "en"
    except Exception:
        return "en"


LANG = detect_language()


# =========================
# TEXT / KONSTANTEN
# =========================

TEXT = {
    "title": {"de": "MSFS Live Traffic Utility", "en": "MSFS Live Traffic Utility"},
    "startup_brand": {"de": "LIVE TRAFFIC", "en": "LIVE TRAFFIC"},
    "startup_tagline": {"de": "Echtzeit-Verkehr · Microsoft Flight Simulator",
                         "en": "Real-time traffic · Microsoft Flight Simulator"},
    "startup_detect_community": {"de": "Suche den MSFS-Community-Ordner …",
                                  "en": "Locating the MSFS Community folder …"},
    "startup_read_aircraft": {"de": "Lese Flugzeugkonfigurationen …",
                               "en": "Reading aircraft configurations …"},
    "startup_read_count": {"de": "Lese aircraft.cfg-Dateien ({count}/{total}) …",
                            "en": "Reading aircraft.cfg files ({count}/{total}) …"},
    "startup_initialize": {"de": "Initialisiere Live-Karte …",
                            "en": "Preparing the live map …"},
    "startup_build_gui": {"de": "Baue die Programmoberfläche …",
                           "en": "Building the application window …"},
    "startup_build_map": {"de": "Lade die Live-Karte …",
                           "en": "Loading the live map …"},
    "startup_ready": {"de": "Starte Live Traffic …",
                       "en": "Starting Live Traffic …"},
    "community_multiple_title": {"de": "MSFS-Community-Ordner auswählen",
                                  "en": "Choose an MSFS Community folder"},
    "community_multiple_prompt": {"de": "Mehrere Simulator-Installationen gefunden. Welchen Community-Ordner soll Live Traffic verwenden?",
                                   "en": "Multiple simulator installations were found. Which Community folder should Live Traffic use?"},
    "community_missing_title": {"de": "Community-Ordner nicht gefunden",
                                 "en": "Community folder not found"},
    "community_missing_prompt": {"de": "Der MSFS-Community-Ordner wurde nicht automatisch gefunden. Wähle den Community-Ordner oder den übergeordneten Packages-Ordner aus. Ohne Auswahl läuft die Live-Karte weiter; Flugzeugtitel werden aber nicht automatisch geladen.",
                                  "en": "The MSFS Community folder could not be found automatically. Choose the Community folder or its parent Packages folder. The live map continues without a selection, but aircraft titles will not load automatically."},
    "community_invalid": {"de": "Der ausgewählte Ordner ist kein gültiger Community- oder Packages-Ordner.",
                          "en": "The selected folder is not a valid Community or Packages folder."},
    "community_browse": {"de": "Anderen Ordner auswählen …",
                          "en": "Browse for another folder …"},
    "community_none_title": {"de": "Flugzeugtitel nicht verfügbar",
                              "en": "Aircraft titles unavailable"},
    "community_none_message": {"de": "Ohne ausgewählten Community-Ordner läuft die Live-Karte weiter; Flugzeugtitel können dann nicht automatisch aus aircraft.cfg gelesen werden.",
                                "en": "The live map will continue without a selected Community folder; aircraft titles cannot then be read automatically from aircraft.cfg."},
    "community_no_aircraft": {"de": "Im ausgewählten Ordner wurden keine aircraft.cfg-Dateien mit Flugzeugtiteln gefunden. Die Live-Karte läuft weiter.",
                               "en": "No aircraft.cfg files with aircraft titles were found in the selected folder. The live map will continue."},
    "flight_label": {"de": "Flugnummer:", "en": "Flight number:"},
    "start": {"de": "Start", "en": "Start"},
    "stop": {"de": "Stop", "en": "Stop"},
    "show_all": {"de": "Alle Flieger", "en": "All aircraft"},
    "info_title": {"de": "Fluginformationen", "en": "Flight Information"},
    "status_ready": {"de": "Bereit.", "en": "Ready."},
    "tracking_started": {"de": "Tracking gestartet für", "en": "Tracking started for"},
    "tracking_stopped": {"de": "Tracking gestoppt.", "en": "Tracking stopped."},
    "follow": {"de": "Folgen", "en": "Follow"},
    "labels": {"de": "Labels", "en": "Labels"},
    "route_show": {"de": "Route", "en": "Route"},
    "hide_ground": {"de": "Bodenverkehr ausblenden", "en": "Hide ground traffic"},
    "min_alt": {"de": "Min. Höhe (ft):", "en": "Min. altitude (ft):"},
    "clear_trail": {"de": "Trail leeren", "en": "Clear trail"},
    "map_style": {"de": "Kartenstil:", "en": "Map style:"},
    "map_custom_style": {"de": "Benutzerdefiniert", "en": "Custom"},
    "msfs_ai": {"de": "MSFS AI-Traffic", "en": "MSFS AI traffic"},
    "msfs_ai_tooltip": {
        "de": "Pausiert oder aktiviert nur die KI-Verkehrsinjektion. Die SimConnect-Spielerposition bleibt für TCAS verfügbar.",
        "en": "Pauses or enables AI traffic injection only. The SimConnect player position remains available for TCAS.",
    },
    "msfs_on": {"de": "MSFS: verbunden", "en": "MSFS: connected"},
    "msfs_off": {"de": "MSFS: nicht verbunden · Live-Karte aktiv",
                 "en": "MSFS: not connected · live map active"},
    "msfs_check": {"de": "MSFS: prüfe …", "en": "MSFS: checking …"},
    "list_title": {"de": "Luftfahrzeuge", "en": "Aircraft"},
    "col_call": {"de": "Rufzeichen", "en": "Callsign"},
    "col_type": {"de": "Typ", "en": "Type"},
    "col_alt": {"de": "Höhe", "en": "Altitude"},
    "col_gs": {"de": "GS", "en": "GS"},
    "live_mode": {"de": "Live-Modus: gesamter Traffic", "en": "Live mode: all traffic"},
    "map_load_failed": {"de": "Karte konnte nicht geladen werden – bitte Logdatei prüfen.",
                         "en": "Map could not be loaded — check the log file."},
    "status_tracking": {"de": "verfolge", "en": "tracking"},
    "emergency": {"de": "NOTFALL", "en": "EMERGENCY"},
    "on_ground": {"de": "am Boden", "en": "on ground"},
    "hint_click": {"de": "Tipp: Flugzeug-Icon anklicken, um es auszuwählen.",
                   "en": "Tip: click an aircraft icon to select it."},
    "zoom_in": {"de": "Zoom +", "en": "Zoom +"},
    "zoom_out": {"de": "Zoom −", "en": "Zoom −"},
    "search": {"de": "Suche:", "en": "Search:"},
    "search_ph": {"de": "Callsign / Hex / Typ …", "en": "callsign / hex / type …"},
    "search_hit": {"de": "Ausgewählt:", "en": "Selected:"},
    "search_hit_global": {"de": "Weltweiter Treffer:", "en": "Worldwide match:"},
    "search_none": {"de": "Kein Treffer für", "en": "No match for"},
    "search_global_loading": {"de": "Weltweite Suche läuft …", "en": "Searching worldwide …"},
    "search_global_error": {"de": "Globale Suche nicht erreichbar; bitte später erneut versuchen.",
                             "en": "Global search unavailable; try again later."},
    "airline_filter": {"de": "Airline:", "en": "Airline:"},
    "type_filter": {"de": "Typ:", "en": "Type:"},
    "trails_show": {"de": "Spuren", "en": "Trails"},
    "tcas": {"de": "TCAS (MSFS)", "en": "TCAS (MSFS)"},
    "tcas_tooltip": {
        "de": "Benötigt ein laufendes MSFS mit SimConnect. Zentriert die Live-Traffic-Abfrage auf deine Spielerposition (80 km) und zeigt relative Daten zum ausgewählten Flugzeug.",
        "en": "Requires MSFS to be running with SimConnect. Centers live traffic polling on your player position (80 km) and shows relative data for the selected aircraft.",
    },
    "alerts": {"de": "Notfall-Alarm", "en": "Emergency alert"},
    "csv_export": {"de": "CSV-Export", "en": "CSV export"},
    "csv_saved": {"de": "CSV gespeichert:", "en": "CSV saved:"},
    "csv_rows": {"de": "Zeilen", "en": "rows"},
    "emergency_toast": {"de": "⚠ NOTFALL", "en": "⚠ EMERGENCY"},
    "tcas_title": {"de": "TCAS (relativ zu dir)", "en": "TCAS (relative to you)"},
    "clock_pos": {"de": "Uhr-Position", "en": "Clock position"},
    "rel_distance": {"de": "Distanz", "en": "Distance"},
    "alt_diff": {"de": "Höhendifferenz", "en": "Altitude difference"},
    "trend": {"de": "Tendenz", "en": "Trend"},
    "climbing": {"de": "steigend", "en": "climbing"},
    "descending": {"de": "sinkend", "en": "descending"},
    "level_off": {"de": "Level", "en": "level"},
    "role_client": {"de": "Client", "en": "client"},
    "role_candidate": {"de": "Kandidat", "en": "candidate"},
    "role_ersatz": {"de": "Ersatz-Proxy (du!)", "en": "fallback proxy (you!)"},
    "mesh_off": {"de": "Mesh: aus", "en": "mesh: off"},
    "mesh_ersatz_peer": {"de": "Ersatz-Proxy: {name}", "en": "Fallback proxy: {name}"},
    "mesh_peers": {"de": "{n} Clients im Mesh", "en": "{n} clients in mesh"},
}

# Kartenstile: Name -> (Tile-URL, max_zoom, erforderliche Attribution)
# Keine undokumentierten Google-Tile-Endpunkte: alle voreingestellten Anbieter
# haben sichtbare Quellenangaben im Leaflet-Attributionsfeld.
TILE_SERVERS = {
    "OpenStreetMap": (
        "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png", 19,
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    ),
    "Dunkel (Carto)": (
        "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", 20,
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors '
        '&copy; <a href="https://carto.com/attributions">CARTO</a>',
    ),
    "Topo (OpenTopoMap)": (
        "https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 17,
        'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
        'contributors, SRTM &middot; Map style: &copy; '
        '<a href="https://opentopomap.org/">OpenTopoMap</a> '
        '(<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
    ),
}

_custom_map = COLOR_SCHEME.get("map_settings", {})
_custom_url = str(_custom_map.get("tile_server_url") or "").strip()
_custom_attribution = str(_custom_map.get("tile_attribution") or "").strip()
if _custom_url and _custom_url not in {entry[0] for entry in TILE_SERVERS.values()}:
    if _custom_attribution:
        try:
            _custom_zoom = max(1, min(22, int(_custom_map.get("tile_max_zoom", 19))))
        except (TypeError, ValueError):
            _custom_zoom = 19
        TILE_SERVERS[TEXT["map_custom_style"][LANG]] = (
            _custom_url, _custom_zoom, _custom_attribution
        )
    else:
        print("[Map] Custom tile URL ignored: map_settings.tile_attribution is required")

# ICAO-Callsign-Präfix -> Airline-Name
AIRLINES = {
    "AAL": "American Airlines", "ABY": "Air Arabia", "ACA": "Air Canada",
    "AFR": "Air France", "AFL": "Aeroflot", "AEA": "Air Europa",
    "AIC": "Air India", "AJT": "Amerijet", "AKJ": "Akasa Air",
    "ALK": "SriLankan Airlines", "AMC": "Air Malta", "ANA": "All Nippon Airways",
    "ANZ": "Air New Zealand", "AUA": "Austrian Airlines", "AXM": "AirAsia",
    "AZA": "ITA Airways", "BAW": "British Airways", "BEL": "Brussels Airlines",
    "BER": "Air Berlin", "BTK": "Batik Air", "CFG": "Condor",
    "CCA": "Air China", "CES": "China Eastern", "CSN": "China Southern",
    "CTN": "Croatia Airlines", "DAL": "Delta Air Lines",
    "DLH": "Lufthansa", "EIN": "Aer Lingus", "EJU": "easyJet Europe",
    "EZY": "easyJet", "EWG": "Eurowings", "EXS": "Jet2",
    "FDB": "flydubai", "FDX": "FedEx Express", "FIN": "Finnair",
    "GAF": "German Air Force", "GWI": "Eurowings Europe", "HAL": "Hawaiian Airlines",
    "IBE": "Iberia", "ICE": "Icelandair", "JAL": "Japan Airlines",
    "JIA": "PSA Airlines", "JTG": "Jet Time", "JZR": "Jazeera Airways",
    "KAC": "Kuwait Airways", "KLM": "KLM", "KQA": "Kenya Airways",
    "LAN": "LATAM", "LOT": "LOT Polish Airlines", "MAS": "Malaysia Airlines",
    "MSR": "EgyptAir", "NAX": "Norwegian", "NLY": "Lauda Europe",
    "NOZ": "Norwegian Air Shuttle", "QTR": "Qatar Airways", "RJA": "Royal Jordanian",
    "RYR": "Ryanair", "SAS": "Scandinavian Airlines", "SIA": "Singapore Airlines",
    "SVA": "Saudia", "SWR": "Swiss", "TAP": "TAP Air Portugal",
    "THY": "Turkish Airlines", "TSC": "Air Transat", "UAE": "Emirates",
    "UAL": "United Airlines", "UPS": "UPS Airlines", "VLG": "Vueling",
    "VJT": "VistaJet", "VOE": "Volotea", "WZZ": "Wizz Air",
}

# ADS-B Emitter-Kategorie -> Beschreibung
CATEGORY_NAMES = {
    "A1": "Light", "A2": "Small", "A3": "Large", "A4": "High vortex (large)",
    "A5": "Heavy", "A6": "High performance", "A7": "Rotorcraft (Helicopter)",
    "B1": "Glider / sailplane", "B2": "Lighter than air", "B3": "Parachute",
    "B4": "Ultralight", "B6": "UAV / drone", "B7": "Space",
    "C1": "Ground emergency", "C2": "Ground service", "C3": "Point obstacle",
}

_CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def cardinal(deg):
    try:
        d = float(deg) % 360.0
    except (TypeError, ValueError):
        return "-"
    return _CARDINALS[int((d + 11.25) / 22.5) % 16]


def bearing_deg(lat1, lon1, lat2, lon2):
    """Anfangspeilung (Grad, 0=Nord, 90=Ost) von Punkt 1 nach Punkt 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def clock_position(rel_brg):
    """Relativpeilung (0-360, bezogen auf eigenen Kurs) -> Uhr-Position 1..12.
    0°=12 Uhr (voraus), 90°=3 Uhr (rechts), 180°=6 Uhr, 270°=9 Uhr."""
    c = int((float(rel_brg) % 360.0 + 15.0) // 30.0) % 12
    return c if c else 12


def _search_haystack(call, info):
    """Alles, worauf die Suche matcht, als ein uppercase-String."""
    return " ".join(str(x or "").upper() for x in (
        call, info.get("hex"), info.get("type"), info.get("reg"),
        AIRLINES.get((call or "")[:3], "")))


def _in_bounds(info, bounds):
    try:
        n, s, e, w = (float(x) for x in bounds)
        return s <= float(info["lat"]) <= n and w <= float(info["lon"]) <= e
    except (TypeError, ValueError, KeyError):
        return True


def is_emergency(info):
    """True bei Squawk 7500/7600/7700 oder gesetztem Emergency-Feld."""
    em = str(info.get("emergency") or "").strip().lower()
    if em and em not in ("none", "null", "false", "0"):
        return True
    return str(info.get("squawk") or "") in ("7500", "7600", "7700")


def numeric_alt(info):
    alt = info.get("alt_geom") or info.get("alt_baro")
    if alt is None or alt == "ground":
        return 0.0
    try:
        return float(alt)
    except (TypeError, ValueError):
        return 0.0


def fmt_alt(info):
    alt = info.get("alt_baro")
    if alt is None:
        alt = info.get("alt_geom")
    if alt is None or alt == "ground":
        return "GND"
    try:
        return f"{int(float(alt)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(alt)


# =========================
# FARBEN (Höhen-Gradient)
# =========================

DEFAULT_GRADIENT = {"low": (0, 140, 255), "mid": (255, 215, 0), "high": (255, 40, 40)}
MAX_ALT_FT = 45000.0
GROUND_MIX_COLOR = (150, 155, 165)


def normalize_gradient(gradient):
    out = {}
    src = gradient or {}
    for key, fallback in DEFAULT_GRADIENT.items():
        val = src.get(key, fallback)
        try:
            rgb = tuple(int(c) for c in val)
            if len(rgb) != 3:
                raise ValueError
            out[key] = tuple(max(0, min(255, c)) for c in rgb)
        except (TypeError, ValueError):
            out[key] = fallback
    return out


ALT_GRADIENT = normalize_gradient(COLOR_SCHEME.get("altitude_gradient"))


def _lerp(a, b, t):
    return int(round(a + (b - a) * t))


def mix_rgb(c1, c2, t):
    return tuple(_lerp(a, b, t) for a, b in zip(c1, c2))


def altitude_to_rgb(alt_ft, gradient=None):
    grad = gradient or ALT_GRADIENT
    try:
        alt = max(0.0, min(MAX_ALT_FT, float(alt_ft)))
    except (TypeError, ValueError):
        alt = 0.0
    t = alt / MAX_ALT_FT
    if t <= 0.5:
        c1, c2, k = grad["low"], grad["mid"], t * 2.0
    else:
        c1, c2, k = grad["mid"], grad["high"], (t - 0.5) * 2.0
    return tuple(_lerp(a, b, k) for a, b in zip(c1, c2))


def rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def altitude_color(alt_ft):
    return rgb_hex(altitude_to_rgb(alt_ft))


def emergency_rgb():
    hexcol = COLOR_SCHEME["gui_theme"].get("emergency_color", "#ff2d2d")
    return tuple(int(hexcol.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def great_circle_path(lat1, lon1, lat2, lon2, samples=48):
    """Great-Circle-Interpolation für Routenlinien."""
    try:
        p1 = (math.radians(lat1), math.radians(lon1))
        p2 = (math.radians(lat2), math.radians(lon2))
    except (TypeError, ValueError):
        return []
    d = 2 * math.asin(min(1.0, math.sqrt(
        math.sin((p2[0] - p1[0]) / 2) ** 2 +
        math.cos(p1[0]) * math.cos(p2[0]) * math.sin((p2[1] - p1[1]) / 2) ** 2)))
    if d <= 1e-9:
        return [(lat1, lon1), (lat2, lon2)]
    pts = []
    for i in range(samples + 1):
        f = i / samples
        a = math.sin((1 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)
        x = a * math.cos(p1[0]) * math.cos(p1[1]) + b * math.cos(p2[0]) * math.cos(p2[1])
        y = a * math.cos(p1[0]) * math.sin(p1[1]) + b * math.cos(p2[0]) * math.sin(p2[1])
        z = a * math.sin(p1[0]) + b * math.sin(p2[0])
        pts.append((math.degrees(math.atan2(z, math.hypot(x, y))),
                    math.degrees(math.atan2(y, x))))
    return pts


# =========================
# ADS-B
# =========================

def _adsb_dst_km(dst_nm):
    """Convert readsb-compatible ADS-B ``dst`` (nautical miles) to km."""
    if dst_nm is None:
        return None
    try:
        distance_nm = float(dst_nm)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(distance_nm) or distance_nm < 0:
        return None
    return distance_nm * 1.852


def parse_adsb(ac: dict):
    flight = str(ac.get("flight") or "").strip().upper()
    hex_code = str(ac.get("hex") or "").strip().upper()
    dst_nm = ac.get("dst")
    return {
        # ADS-B often has no callsign for GA/military/unknown traffic. Keep
        # positioned aircraft visible under their ICAO hex instead of dropping them.
        "call": flight or hex_code,
        "hex": ac.get("hex"),
        "reg": ac.get("r"),
        "type": ac.get("t"),
        "category": ac.get("category"),
        "lat": ac.get("lat"),
        "lon": ac.get("lon"),
        "alt_baro": ac.get("alt_baro"),
        "alt_geom": ac.get("alt_geom"),
        "gs": ac.get("gs"),
        "track": ac.get("track"),
        "baro_rate": ac.get("baro_rate"),
        "nav_qnh": ac.get("nav_qnh"),
        "nav_alt_mcp": ac.get("nav_altitude_mcp"),
        "squawk": ac.get("squawk"),
        "emergency": ac.get("emergency"),
        "seen": ac.get("seen"),
        "seen_pos": ac.get("seen_pos"),
        "rssi": ac.get("rssi"),
        # Keep the raw nautical-mile value for diagnostics, while the
        # established internal ``dst`` field is always kilometers.
        "dst": _adsb_dst_km(dst_nm),
        "dst_nm": dst_nm,
        "dir": ac.get("dir"),
        "messages": ac.get("messages"),
    }


_last_adsb_data = []

# Global OpenSky snapshots are requested only on submitted searches and fetched
# by the configured proxy's collector, where OAuth credentials remain private.
# This client-side gate complements the proxy-wide 120-second quota guard.
GLOBAL_SEARCH_MIN_INTERVAL = 120.0
GLOBAL_SEARCH_CACHE_TTL = 180.0
GLOBAL_SEARCH_TIMEOUT = 60.0
GLOBAL_SEARCH_POLL_INTERVAL = 3.0

# Anonymous OpenSky /states/all access has a 400-credit daily bucket; a query
# can cost up to 4 credits. One direct request every 15 minutes caps this app
# at 96 worst-case requests (384 credits) per 24 hours, leaving a small margin.
DIRECT_OPENSKY_MIN_INTERVAL = 15.0 * 60.0
_global_search_lock = threading.Lock()
_global_search_inflight = False
_global_search_last_request = 0.0
_global_search_snapshot = []
_global_search_snapshot_at = 0.0
_HISTORY_ERROR_TRACKER = ErrorStateTracker()

# peer_net.PeerNet-Instanz (von main.py gesetzt) – Mesh/Ersatz-Proxy-Anbindung
MESH = None


class SourceRegistry:
    """Gesundheitsstatus aller Datenquellen (GUI-LEDs, Statusseite, Mesh-Wahl)."""

    COOLDOWN_429 = 60.0     # Sekunden: Quelle nach HTTP 429 überspringen
    DIRECT_SOURCES = ("adsb.lol", "adsb.fi", "opensky")

    def __init__(self, clock=None, sleeper=None):
        self._lock = threading.Lock()
        self._src = {}
        self._rate_locks = {}
        self._rate_last = {}
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep

    @contextmanager
    def request_slot(self, name, min_interval=1.0):
        """Serializes requests and enforces spacing for rate-limited providers."""
        with self._lock:
            provider_lock = self._rate_locks.setdefault(name, threading.Lock())
        with provider_lock:
            now = self._clock()
            with self._lock:
                last = self._rate_last.get(name)
            if last is not None:
                delay = max(0.0, float(min_interval) - (now - last))
                if delay:
                    self._sleep(delay)
            started = self._clock()
            with self._lock:
                self._rate_last[name] = started
            yield

    def try_request_slot(self, name, min_interval=1.0):
        """Reserve a spaced request without sleeping; return wait seconds if busy."""
        now = self._clock()
        with self._lock:
            last = self._rate_last.get(name)
            if last is not None:
                delay = max(0.0, float(min_interval) - (now - last))
                if delay > 0.0:
                    return delay
            self._rate_last[name] = now
        return 0.0

    def report(self, name, ok, code=None, detail="", now=None, retry_after=None):
        now = now if now is not None else time.time()
        detail_text = str(detail)[:80]
        recovery = None
        with self._lock:
            e = self._src.setdefault(name, {
                "name": name, "ok": None, "code": None, "detail": "",
                "last": 0.0, "cooldown_until": 0.0,
                "_failure_since": None, "_failure_status_code": None,
                "_failure_detail": None,
            })
            e.setdefault("_failure_since", None)
            e.setdefault("_failure_status_code", None)
            e.setdefault("_failure_detail", None)
            previous_status = (e["ok"], e["code"], e["detail"])
            failure_since = e["_failure_since"]
            if ok:
                if failure_since is not None:
                    recovery = {
                        "failure_duration_sec": round(max(0.0, now - failure_since), 1),
                        "previous_status_code": e["_failure_status_code"],
                        "previous_detail": e["_failure_detail"],
                    }
                    e["_failure_since"] = None
                    e["_failure_status_code"] = None
                    e["_failure_detail"] = None
            elif failure_since is None:
                e["_failure_since"] = now
                e["_failure_status_code"] = code
                e["_failure_detail"] = detail_text
            e["ok"] = bool(ok)
            e["code"] = code
            e["detail"] = detail_text
            e["last"] = now
            if code == 429:
                delay = self.COOLDOWN_429 if retry_after is None else retry_after
                e["cooldown_until"] = now + max(1.0, min(86400.0, float(delay)))
            elif ok:
                e["cooldown_until"] = 0.0
            current_status = (e["ok"], e["code"], e["detail"])
        if current_status != previous_status:
            log_action("source.status_changed", source=name, ok=bool(ok),
                       status_code=code, detail=detail_text)
        if recovery is not None:
            log_action("source.error_recovered", source=name,
                       failure_duration_sec=recovery["failure_duration_sec"],
                       previous_status_code=recovery["previous_status_code"],
                       previous_detail=recovery["previous_detail"],
                       status_code=code, detail=detail_text)

    def cooling(self, name, now=None):
        now = now if now is not None else time.time()
        with self._lock:
            e = self._src.get(name)
            return bool(e and e["cooldown_until"] > now)

    def snapshot(self):
        with self._lock:
            return {
                name: {key: value for key, value in entry.items()
                       if not key.startswith("_")}
                for name, entry in self._src.items()
            }

    def direct_available(self, now=None):
        """Mindestens eine Direktquelle ist nicht im 429-Cooldown."""
        now = now if now is not None else time.time()
        with self._lock:
            for n in self.DIRECT_SOURCES:
                e = self._src.get(n)
                if e is None or e["cooldown_until"] <= now:
                    return True
            return False

    def proxy_down(self):
        """True, wenn der letzte Proxy-Versuch fehlgeschlagen ist (inkl. 429
    oder veraltetem Collector). Unbekannt (noch nie probiert) = nicht down."""
        with self._lock:
            e = self._src.get("proxy")
            return bool(e and e["ok"] is False)


SOURCES = SourceRegistry()
_TRAFFIC_SOURCE_ERRORS = ErrorStateTracker()
_DIRECT_OPENSKY_GATE_LOG_LOCK = threading.Lock()
_DIRECT_OPENSKY_GATE_SKIP_LOGGED = False


def _direct_opensky_gate_log_once(skipped):
    """Avoid writing the same long anonymous-quota wait to every poll log."""
    global _DIRECT_OPENSKY_GATE_SKIP_LOGGED
    with _DIRECT_OPENSKY_GATE_LOG_LOCK:
        if not skipped:
            _DIRECT_OPENSKY_GATE_SKIP_LOGGED = False
            return False
        if _DIRECT_OPENSKY_GATE_SKIP_LOGGED:
            return False
        _DIRECT_OPENSKY_GATE_SKIP_LOGGED = True
        return True


# Proxy-Backoff: nach wiederholten Fehlern nicht jeden Tick neu probieren
_proxy_state = {"fails": 0, "next_probe": 0.0}


def _proxy_backoff_active():
    st = _proxy_state
    return st["fails"] >= 2 and time.time() < st["next_probe"]


def _proxy_fail():
    st = _proxy_state
    st["fails"] += 1
    st["next_probe"] = time.time() + min(30.0, 2.0 * st["fails"])


def _proxy_ok():
    _proxy_state["fails"] = 0
    _proxy_state["next_probe"] = 0.0


def _http_code(exc):
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def _retry_after_seconds(exc, default=60, now=None):
    """Read Retry-After as delta-seconds or an HTTP date."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("X-Rate-Limit-Retry-After-Seconds")
    if value is None:
        value = headers.get("Retry-After")
    if value is None:
        return default
    value = str(value).strip()
    try:
        seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        try:
            target = email.utils.parsedate_to_datetime(value).timestamp()
            seconds = int(target - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError, OSError):
            return default
    if seconds <= 0:
        return default
    return min(86400, seconds)


PAGE_FILE = "page.txt"


def _page_txt_url():
    """URL der Webseite aus page.txt (erste Nicht-Kommentarzeile).

    page.txt liegt neben dem Programm (Script oder exe) bzw. im Arbeits-
    verzeichnis und ist gitignoriert – so muss die Proxy-Adresse nicht im
    Repo stehen und kann pro Installation gesetzt werden.
    """
    dirs = []
    try:
        if getattr(sys, "frozen", False):
            dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(str(meipass))
    except Exception:
        pass
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    dirs.append(os.getcwd())
    seen = set()
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        path = os.path.join(d, PAGE_FILE)
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            return line.rstrip("/")
        except OSError:
            continue
    return ""


def proxy_config():
    """Proxy-URL/-Token: Umgebungsvariable → page.txt → colors.json ("proxy")."""
    cfg = COLOR_SCHEME.get("proxy", {}) or {}
    url = (os.environ.get("LTU_PROXY_URL") or _page_txt_url()
           or cfg.get("url") or "").strip()
    token = (os.environ.get("LTU_PROXY_TOKEN") or cfg.get("token") or "").strip()
    return url.rstrip("/"), token


def bounds_to_radius_km(bounds, center):
    """Radius (km), der den sichtbaren Kartenausschnitt abdeckt (gedeckelt 25..250)."""
    try:
        n, s, e, w = (float(x) for x in bounds)
        clat, clon = float(center[0]), float(center[1])
    except (TypeError, ValueError):
        return None
    h_km = haversine_km(n, clon, s, clon)
    w_km = haversine_km(clat, w, clat, e)
    return min(250.0, max(25.0, math.hypot(h_km, w_km) / 2.0))


def _opensky_bounds(lat, lon, radius_km):
    """Return a valid OpenSky BBox around a point, capped at a 250 km radius.

    A raw map viewport can cover most of the world when zoomed out. Direct
    fallback requests deliberately use this bounded circle instead; a viewport
    crossing the date line or a cap reaching a pole widens longitude safely.
    """
    lat, lon, radius_km = float(lat), float(lon), float(radius_km)
    if not all(math.isfinite(value) for value in (lat, lon, radius_km)):
        raise ValueError("OpenSky center/radius must be finite")
    if not -90.0 <= lat <= 90.0:
        raise ValueError("OpenSky latitude is outside geographic range")
    if radius_km < 0.0:
        raise ValueError("OpenSky radius cannot be negative")

    lat = max(-90.0, min(90.0, lat))
    lon = (lon + 180.0) % 360.0 - 180.0
    radius_km = min(250.0, radius_km)
    if radius_km == 0.0:
        return lat, lat, lon, lon

    earth_radius_km = 6371.0088
    angular_radius = radius_km / earth_radius_km
    dlat = math.degrees(angular_radius)
    south = max(-90.0, lat - dlat)
    north = min(90.0, lat + dlat)

    # A spherical cap touching a pole spans every longitude. Otherwise use the
    # exact longitude delta for the circle's angular radius at this latitude.
    cos_lat = math.cos(math.radians(lat))
    ratio = math.sin(angular_radius) / cos_lat if cos_lat > 0.0 else 2.0
    if south <= -90.0 or north >= 90.0 or ratio >= 1.0:
        west, east = -180.0, 180.0
    else:
        dlon = math.degrees(math.asin(max(0.0, ratio)))
        west, east = lon - dlon, lon + dlon
        if west < -180.0 or east > 180.0:
            west, east = -180.0, 180.0
    return north, south, east, west


def _opensky_bbox_credit_cost(bounds):
    """Estimate /states/all credits from the official BBox-area schedule."""
    north, south, east, west = (float(value) for value in bounds)
    area = max(0.0, north - south) * max(0.0, east - west)
    if area <= 25.0:
        return 1
    if area <= 100.0:
        return 2
    if area <= 400.0:
        return 3
    return 4


def _opensky_convert(states, response=None):
    """OpenSky-states-Array -> adsb.lol-ähnliche dicts (Feldnamen/Einheiten)."""
    out = []
    rejected = []
    rejected_count = 0
    for index, s in enumerate(states or []):
        try:
            if not isinstance(s, (list, tuple)) or len(s) < 15:
                raise ValueError("state row is not a valid OpenSky array")
            if s[5] is None or s[6] is None:
                continue
            lat, lon = float(s[6]), float(s[5])
            if (not math.isfinite(lat) or not math.isfinite(lon)
                    or not -90.0 <= lat <= 90.0
                    or not -180.0 <= lon <= 180.0):
                raise ValueError("position is outside geographic range")
            on_ground = bool(s[8])
            baro_alt_m = s[7]
            geo_alt_m = s[13]
            if baro_alt_m is not None:
                alt_baro = int(round(float(baro_alt_m) * 3.28084))
            else:
                alt_baro = "ground" if on_ground else None
            alt_geom = (int(round(float(geo_alt_m) * 3.28084))
                        if geo_alt_m is not None else None)
            out.append({
                "hex": str(s[0] or "").strip(),
                "flight": str(s[1] or "").strip().upper(),
                "lat": lat,
                "lon": lon,
                "alt_baro": alt_baro,
                "alt_geom": alt_geom,
                "gs": round(float(s[9]) * 1.94384, 1) if s[9] is not None else None,
                "track": round(float(s[10]), 1) if s[10] is not None else None,
                "baro_rate": int(round(float(s[11]) * 196.85)) if s[11] is not None else None,
                "squawk": str(s[14]) if s[14] is not None else None,
                "src": "opensky",
            })
        except (TypeError, ValueError, IndexError, OverflowError):
            rejected_count += 1
            if len(rejected) < 5:
                rejected.append({"index": index, "value": s})
    if rejected_count:
        log_action("traffic.opensky_invalid_state_rows",
                   rejected_count=rejected_count, rejected_samples=rejected,
                   response=response_debug_payload(response))
    return out


def fetch_direct(lat, lon, eff_radius, bounds=None):
    """Direktquellen-Kette: adsb.lol → adsb.fi → OpenSky. None = alles fehlgeschlagen.
    Quellen im 429-Cooldown werden übersprungen; jeder Versuch wird in
    ``SOURCES`` protokolliert (Statusanzeige/Mesh)."""
    global _last_adsb_data
    headers = {"User-Agent": API_USER_AGENT, "Accept": "application/json"}

    # Die Distanz wird in NM angegeben. adsb.fi fordert fuer neue
    # Integrationen v3 und begrenzt oeffentliche Abfragen auf 250 NM.
    radius_nm = max(1, min(250, int(round(eff_radius * 0.539957))))
    for name, base_url in (("adsb.lol", "https://api.adsb.lol/v2"),
                           ("adsb.fi", "https://opendata.adsb.fi/api/v3")):
        if SOURCES.cooling(name):
            log_action("traffic.source_request_skipped", source=name,
                       reason="429_cooldown")
            print(f"[ADS-B] {name}: 429-Cooldown aktiv → überspringe")
            continue
        url = f"{base_url}/lat/{lat}/lon/{lon}/dist/{radius_nm}"
        log_action("traffic.source_request_started", source=name)
        response = None
        try:
            if name == "adsb.fi":
                with SOURCES.request_slot(name, 1.0):
                    response = requests.get(url, headers=headers, timeout=15)
            else:
                response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("ac"), list):
                raise ValueError(f"{name} response has no aircraft list 'ac'")
            ac = payload["ac"]
            status_code = (response.status_code
                           if hasattr(response, "status_code") else 200)
            if not ac:
                # HTTP 200 with an empty list is not useful traffic data. Keep
                # the provider marked reachable, but try the next source.
                recovered = _TRAFFIC_SOURCE_ERRORS.record_recovery(
                    f"direct:{name}", "traffic.source_request_recovered",
                    source=name, status_code=status_code, aircraft_received=0,
                )
                if recovered:
                    print(f"[ADS-B] {name} recovered after a previous error")
                SOURCES.report(name, True, status_code, "ok; 0 aircraft")
                log_action("traffic.source_request_completed", source=name,
                           status_code=status_code, aircraft_received=0,
                           empty_response=True)
                print(f"[ADS-B] {name} returned 0 aircraft → try next source")
                continue
            recovered = _TRAFFIC_SOURCE_ERRORS.record_recovery(
                f"direct:{name}", "traffic.source_request_recovered",
                source=name, status_code=status_code, aircraft_received=len(ac),
            )
            if recovered:
                print(f"[ADS-B] {name} recovered after a previous error")
            SOURCES.report(name, True, status_code, "ok")
            log_action("traffic.source_request_completed", source=name,
                       status_code=status_code, aircraft_received=len(ac),
                       empty_response=False)
            _last_adsb_data = ac
            return ac
        except Exception as e:
            code = _http_code(e)
            response_for_log = getattr(e, "response", None)
            if response_for_log is None:
                response_for_log = response
            first_failure = _TRAFFIC_SOURCE_ERRORS.record_failure(
                f"direct:{name}", "traffic.source_request_failed", e,
                source=name, status_code=code,
                response=response_debug_payload(response_for_log),
            )
            SOURCES.report(name, False, code, str(e),
                           retry_after=_retry_after_seconds(e) if code == 429 else None)
            if first_failure:
                print(f"[ADS-B] {name} failed ({code or 'n/a'}): {e}")

    # OpenSky has a smaller anonymous credit bucket than the configured
    # collector. Ignore the raw map viewport here: it can be world-sized when
    # zoomed out, whereas eff_radius is already capped to a local 250 km circle.
    if SOURCES.cooling("opensky"):
        log_action("traffic.source_request_skipped", source="opensky",
                   reason="429_cooldown")
        print("[ADS-B] opensky: 429-Cooldown aktiv → überspringe")
    else:
        try:
            n, s, e, w = _opensky_bounds(lat, lon, eff_radius)
        except (TypeError, ValueError, OverflowError) as exc:
            log_action("traffic.source_request_skipped", source="opensky",
                       reason="invalid_bounding_box", error=str(exc))
        else:
            retry_after = SOURCES.try_request_slot(
                "opensky-direct", DIRECT_OPENSKY_MIN_INTERVAL
            )
            if retry_after > 0.0:
                if _direct_opensky_gate_log_once(True):
                    log_action("traffic.source_request_skipped", source="opensky",
                               reason="anonymous_credit_budget",
                               retry_after_sec=round(retry_after, 1),
                               min_interval_sec=DIRECT_OPENSKY_MIN_INTERVAL)
                    print("[ADS-B] opensky: anonymer Credit-Schutz aktiv; "
                          f"Retry in {retry_after:.0f} s")
            else:
                _direct_opensky_gate_log_once(False)
                url = ("https://opensky-network.org/api/states/all"
                       f"?lamin={s:.4f}&lomin={w:.4f}&lamax={n:.4f}&lomax={e:.4f}")
                log_action(
                    "traffic.source_request_started", source="opensky",
                    bbox=[n, s, e, w],
                    bbox_area_sqdeg=round(max(0.0, n - s) * max(0.0, e - w), 2),
                    estimated_credits=_opensky_bbox_credit_cost((n, s, e, w)),
                )
                response = None
                try:
                    response = requests.get(url, headers=headers, timeout=15)
                    response.raise_for_status()
                    payload = response.json()
                    if (not isinstance(payload, dict) or "states" not in payload
                            or (payload["states"] is not None
                                and not isinstance(payload["states"], list))):
                        raise ValueError(
                            "OpenSky response is missing a valid 'states' array"
                        )
                    ac = _opensky_convert(payload["states"], response=response)
                    status_code = (response.status_code
                                   if hasattr(response, "status_code") else 200)
                    recovered = _TRAFFIC_SOURCE_ERRORS.record_recovery(
                        "direct:opensky", "traffic.source_request_recovered",
                        source="opensky", status_code=status_code,
                        aircraft_received=len(ac),
                    )
                    if recovered:
                        print("[ADS-B] opensky recovered after a previous error")
                    SOURCES.report("opensky", True, status_code,
                                   "ok" if ac else "ok; 0 aircraft")
                    log_action("traffic.source_request_completed", source="opensky",
                               status_code=status_code, aircraft_received=len(ac),
                               empty_response=not bool(ac))
                    if ac:
                        _last_adsb_data = ac
                        return ac
                except Exception as e:
                    code = _http_code(e)
                    response_for_log = getattr(e, "response", None)
                    if response_for_log is None:
                        response_for_log = response
                    first_failure = _TRAFFIC_SOURCE_ERRORS.record_failure(
                        "direct:opensky", "traffic.source_request_failed", e,
                        source="opensky", status_code=code,
                        response=response_debug_payload(response_for_log),
                    )
                    SOURCES.report(
                        "opensky", False, code, str(e),
                        retry_after=_retry_after_seconds(e) if code == 429 else None,
                    )
                    if first_failure:
                        print(f"[ADS-B] opensky failed ({code or 'n/a'}): {e}")
    return None


def fetch_adsb_traffic(lat, lon, radius_km=250, bounds=None):
    """
    Holt Traffic bevorzugt vom eigenen Proxy (index.php), sonst direkt
    über die Failover-Kette adsb.lol → adsb.fi → OpenSky → letzter Cache.
    ``bounds`` = sichtbarer Kartenausschnitt (n, s, e, w):
      * Proxy-Modus:  wird als Bounding Box angefragt (?bbox=…) – der Proxy
        entpackt dann nur die Regionen, die diesen Bereich schneiden.
      * Direkt-Modus: Radius wird aus dem Ausschnitt berechnet (max. 250 km).
    """
    global _last_adsb_data
    url, token = proxy_config()
    if url and _proxy_backoff_active():
        log_action("traffic.proxy_request_skipped", reason="backoff")
        print("[ADS-B] Proxy im Backoff (zuletzt down/429) → Ersatz/Direkt")
    elif url:
        log_action("traffic.proxy_request_started")
        response = None
        demand_recorded = None
        try:
            headers = {"User-Agent": API_USER_AGENT, "Accept": "application/json"}
            if token:
                headers["X-LTU-Token"] = token
            query = f"{url}?action=traffic&lat={lat:.5f}&lon={lon:.5f}&dist={radius_km}"
            if bounds:
                try:
                    n, s, e, w = (float(x) for x in bounds)
                    query += f"&bbox={n:.4f},{s:.4f},{e:.4f},{w:.4f}"
                except (TypeError, ValueError):
                    pass
            response = requests.get(query, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("proxy traffic response is not a JSON object")
            demand_recorded = data.get("demand_recorded")
            if demand_recorded is False:
                _TRAFFIC_SOURCE_ERRORS.record_failure(
                    "proxy:demand-record", "traffic.proxy_demand_not_recorded",
                    RuntimeError("PHP client-demand store rejected the traffic request"),
                    status_code=getattr(response, "status_code", None),
                    reason="php_demand_store_failed",
                )
            elif demand_recorded is True:
                _TRAFFIC_SOURCE_ERRORS.record_recovery(
                    "proxy:demand-record", "traffic.proxy_demand_recorded_again",
                    status_code=getattr(response, "status_code", None),
                )
            if not (data.get("proxy") or {}).get("online", False):
                raise RuntimeError("collector-offline: Daten veraltet")
            ac = data.get("ac", [])
            if not isinstance(ac, list):
                raise ValueError("proxy response has no aircraft list 'ac'")
            recovered = _TRAFFIC_SOURCE_ERRORS.record_recovery(
                "proxy:traffic", "traffic.proxy_request_recovered",
                status_code=200, aircraft_received=len(ac),
            )
            if recovered:
                print("[ADS-B] Proxy recovered after a previous error")
            SOURCES.report("proxy", True, 200,
                           "ok" if ac else "ok; 0 aircraft")
            _proxy_ok()
            log_action("traffic.proxy_request_completed", status_code=200,
                       aircraft_received=len(ac), empty_response=not bool(ac),
                       demand_recorded=demand_recorded)
            if ac:
                _last_adsb_data = ac
                return ac
            log_action("traffic.proxy_empty_response", center=[lat, lon],
                       radius_km=radius_km, bounds=bounds)
            print("[ADS-B] Proxy online, but returned no aircraft → Ersatz/Direkt")
        except Exception as e:
            code = _http_code(e)
            response_for_log = getattr(e, "response", None)
            if response_for_log is None:
                response_for_log = response
            detail = "429 rate-limited" if code == 429 else str(e)
            first_failure = _TRAFFIC_SOURCE_ERRORS.record_failure(
                "proxy:traffic", "traffic.proxy_request_failed", e,
                status_code=code, demand_recorded=demand_recorded,
                response=response_debug_payload(response_for_log),
            )
            SOURCES.report("proxy", False, code, detail)
            _proxy_fail()
            if first_failure:
                print(f"[ADS-B] Proxy {url} failed ({code or 'n/a'}): {e} → Ersatz/Direkt")

    eff_radius = int(round(radius_km))
    if bounds:
        br = bounds_to_radius_km(bounds, (lat, lon))
        if br:
            eff_radius = int(round(br))

    # Ersatz-Proxy über das Web-Relay der Seite (falls der Server einen gewählt hat)
    if MESH is not None:
        try:
            ac = MESH.client_fetch(lat, lon, eff_radius, bounds)
            if ac:
                _last_adsb_data = ac
                return ac
            if ac is not None:
                log_action("traffic.relay_empty_response", center=[lat, lon],
                           radius_km=eff_radius)
        except Exception as e:
            SOURCES.report("ersatz", False, None, str(e))
            print(f"[ADS-B] Ersatz-Proxy failed: {e}")

    ac = fetch_direct(lat, lon, eff_radius, bounds)
    if ac is not None:
        if MESH is not None:
            try:
                MESH.relay_offer(lat, lon, eff_radius, bounds, ac)
            except Exception:
                pass
        return ac
    if _last_adsb_data:
        log_action("traffic.cached_data_used", aircraft_received=len(_last_adsb_data))
        print("[ADS-B] Using cached traffic data")
        return _last_adsb_data
    log_action("traffic.no_data_available")
    return []


def fetch_global_traffic():
    """Fetch one global OpenSky snapshot through the configured proxy.

    The GUI calls this function from a worker thread. The PHP proxy enqueues a
    short-lived search demand; only the collector uses its private OpenSky OAuth
    credentials. This client never receives or sends those credentials. The
    proxy and collector enforce a shared OpenSky quota, while this layer
    coalesces local searches and caches a recent result.
    """
    global _global_search_inflight, _global_search_last_request
    global _global_search_snapshot, _global_search_snapshot_at

    now = time.monotonic()
    with _global_search_lock:
        snapshot = list(_global_search_snapshot)
        snapshot_at = _global_search_snapshot_at
        age = max(0.0, now - snapshot_at) if snapshot_at else None
        fresh = bool(snapshot_at and age <= GLOBAL_SEARCH_CACHE_TTL)
        if _global_search_inflight:
            log_action("traffic.global_search_reused", reason="request_in_flight",
                       cached_aircraft=len(snapshot), cache_age_sec=age)
            return {
                "ok": bool(fresh), "aircraft": snapshot if fresh else [],
                "cached": bool(fresh), "stale": False, "rate_limited": False,
                "in_flight": True, "error": None,
            }

        since_request = now - _global_search_last_request
        if (_global_search_last_request
                and since_request < GLOBAL_SEARCH_MIN_INTERVAL):
            retry_after = GLOBAL_SEARCH_MIN_INTERVAL - since_request
            log_action("traffic.global_search_rate_limited",
                       retry_after_sec=round(retry_after, 1),
                       cached_aircraft=len(snapshot), cache_age_sec=age)
            return {
                "ok": bool(fresh), "aircraft": snapshot if fresh else [],
                "cached": bool(fresh), "stale": False, "rate_limited": True,
                "in_flight": False, "error": None,
            }

        _global_search_inflight = True
        _global_search_last_request = now

    url, _ = proxy_config()
    if not url:
        error = "No proxy is configured for global OpenSky search"
        log_action("traffic.global_search_failed", error=error,
                   reason="no_proxy_configured")
        with _global_search_lock:
            _global_search_inflight = False
        return {
            "ok": False, "aircraft": [], "cached": False, "stale": False,
            "rate_limited": False, "in_flight": False, "error": error,
        }

    endpoint = f"{url}?action=global_search"
    headers = {"User-Agent": API_USER_AGENT, "Accept": "application/json"}
    deadline = time.monotonic() + GLOBAL_SEARCH_TIMEOUT
    log_action("traffic.global_search_started", source="opensky", endpoint=endpoint)
    response_received = False
    upstream_error = False
    response_status = 0
    request_rate_limited = False
    rate_limit_retry_after = GLOBAL_SEARCH_MIN_INTERVAL
    response = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("global OpenSky snapshot is still pending")
            response = requests.get(endpoint, headers=headers,
                                    timeout=min(10.0, max(1.0, remaining)))
            response_received = True
            status_code = getattr(response, "status_code", 200)
            response_status = status_code
            try:
                payload = response.json()
            except Exception:
                response.raise_for_status()
                raise ValueError("invalid global-search response")
            if not isinstance(payload, dict):
                raise ValueError("global-search response is not an object")

            if status_code == 429 or payload.get("rate_limited"):
                request_rate_limited = True
                retry_after = payload.get("retry_after")
                try:
                    retry_after = float(retry_after)
                except (TypeError, ValueError):
                    retry_after = GLOBAL_SEARCH_MIN_INTERVAL
                rate_limit_retry_after = retry_after
                SOURCES.report("opensky", False, 429, "global search rate-limited",
                               retry_after=retry_after)
                upstream_error = True
                raise RuntimeError(payload.get("error") or "global OpenSky search rate-limited")
            if status_code >= 400:
                upstream_error = status_code in (401, 403, 429, 503)
                raise RuntimeError(payload.get("error") or
                                   f"global OpenSky proxy returned HTTP {status_code}")

            if payload.get("ok") and isinstance(payload.get("ac"), list):
                aircraft = []
                invalid_items = []
                invalid_item_count = 0
                for index, item in enumerate(payload["ac"]):
                    if not isinstance(item, dict):
                        invalid_item_count += 1
                        if len(invalid_items) < 5:
                            invalid_items.append({"index": index, "value": item})
                        continue
                    try:
                        lat, lon = float(item.get("lat")), float(item.get("lon"))
                    except (TypeError, ValueError, OverflowError):
                        invalid_item_count += 1
                        if len(invalid_items) < 5:
                            invalid_items.append({"index": index, "value": item})
                        continue
                    if (not math.isfinite(lat) or not math.isfinite(lon)
                            or not -90.0 <= lat <= 90.0
                            or not -180.0 <= lon <= 180.0):
                        invalid_item_count += 1
                        if len(invalid_items) < 5:
                            invalid_items.append({"index": index, "value": item})
                        continue
                    aircraft.append(item)
                if invalid_item_count:
                    log_action(
                        "traffic.global_search_invalid_aircraft",
                        rejected_count=invalid_item_count,
                        received_count=len(payload["ac"]),
                        rejected_samples=invalid_items,
                        response=response_debug_payload(response),
                    )

                SOURCES.report("proxy", True, 200, "global OpenSky search endpoint")
                SOURCES.report("opensky", True, status_code,
                               f"global search; {len(aircraft)} aircraft")
                with _global_search_lock:
                    _global_search_snapshot = aircraft
                    _global_search_snapshot_at = time.monotonic()
                    _global_search_inflight = False
                log_action("traffic.global_search_completed", source="opensky",
                           status_code=status_code, aircraft_received=len(payload["ac"]),
                           aircraft_with_position=len(aircraft),
                           cached=bool(payload.get("cached")))
                return {
                    "ok": True, "aircraft": list(aircraft),
                    "cached": bool(payload.get("cached")), "stale": False,
                    "rate_limited": False, "in_flight": False, "error": None,
                }

            if payload.get("pending"):
                SOURCES.report("proxy", True, 200, "global OpenSky search pending")
                delay = payload.get("retry_after", GLOBAL_SEARCH_POLL_INTERVAL)
                try:
                    delay = float(delay)
                except (TypeError, ValueError):
                    delay = GLOBAL_SEARCH_POLL_INTERVAL
                delay = max(0.1, min(GLOBAL_SEARCH_POLL_INTERVAL, delay,
                                     max(0.0, deadline - time.monotonic())))
                if delay <= 0:
                    raise TimeoutError("global OpenSky snapshot is still pending")
                time.sleep(delay)
                continue

            upstream_error = bool(payload.get("source") == "opensky")
            raise RuntimeError(payload.get("error") or
                               "global OpenSky snapshot is unavailable")
    except Exception as exc:
        code = response_status or _http_code(exc)
        if response_received:
            if code == 429:
                SOURCES.report("proxy", False, 429, str(exc),
                               retry_after=rate_limit_retry_after)
                _proxy_fail()
            else:
                SOURCES.report("proxy", True, 200, "global OpenSky search response")
            if upstream_error and code != 429:
                SOURCES.report("opensky", False, code, str(exc))
        else:
            SOURCES.report("proxy", False, code, str(exc))
            _proxy_fail()
        with _global_search_lock:
            _global_search_inflight = False
            cached_snapshot = list(_global_search_snapshot)
            cached_at = _global_search_snapshot_at
        cache_age = max(0.0, time.monotonic() - cached_at) if cached_at else None
        use_cache = bool(cached_at and cache_age <= GLOBAL_SEARCH_CACHE_TTL)
        response_for_log = getattr(exc, "response", None)
        if response_for_log is None:
            response_for_log = response
        log_action("traffic.global_search_failed", source="opensky",
                   status_code=code, error=str(exc),
                   response=response_debug_payload(response_for_log),
                   cached_aircraft=len(cached_snapshot), cache_age_sec=cache_age,
                   cached_fallback=use_cache)
        return {
            "ok": bool(use_cache),
            "aircraft": cached_snapshot if use_cache else [],
            "cached": bool(use_cache), "stale": bool(use_cache),
            "rate_limited": bool(request_rate_limited or code == 429),
            "in_flight": False,
            "error": str(exc),
        }


def fetch_aircraft_history(hex_code, callsign=None):
    """Fetch the proxy's last-hour trail by ICAO hex or known callsign."""
    hex_code = str(hex_code or "").strip().lower()
    flight = str(callsign or "").strip().upper()
    if re.fullmatch(r"[a-f0-9]{1,16}", hex_code):
        identity = f"hex={hex_code}"
    elif re.fullmatch(r"[A-Z0-9-]{1,16}", flight):
        identity = f"flight={flight}"
    else:
        log_action("traffic.history_fetch_skipped", reason="invalid_aircraft_id")
        return []
    url, token = proxy_config()
    if not url:
        log_action("traffic.history_fetch_skipped", reason="no_proxy_configured",
                   hex=hex_code or None, callsign=flight or None)
        return []

    headers = {"User-Agent": API_USER_AGENT, "Accept": "application/json"}
    if token:
        headers["X-LTU-Token"] = token
    query = f"{url}?action=track&{identity}"
    log_action("traffic.history_fetch_started", hex=hex_code or None,
               callsign=flight or None)
    response = None
    try:
        response = requests.get(query, headers=headers, timeout=8)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise ValueError("invalid proxy track response")
        points = payload.get("points")
        if not isinstance(points, list):
            raise ValueError("proxy track response has no 'points' array")
        history = []
        invalid_points = []
        invalid_point_count = 0
        for index, point in enumerate(points):
            if not isinstance(point, dict):
                invalid_point_count += 1
                if len(invalid_points) < 5:
                    invalid_points.append({"index": index, "value": point})
                continue
            try:
                lat, lon = float(point["lat"]), float(point["lon"])
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid_point_count += 1
                if len(invalid_points) < 5:
                    invalid_points.append({"index": index, "value": point})
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)
                    and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                invalid_point_count += 1
                if len(invalid_points) < 5:
                    invalid_points.append({"index": index, "value": point})
                continue
            history.append({
                "lat": lat,
                "lon": lon,
                "alt": point.get("alt"),
                "track": point.get("track"),
                "t": point.get("t"),
            })
        if invalid_point_count:
            log_action("traffic.history_invalid_points",
                       hex=hex_code or None, callsign=flight or None,
                       rejected_count=invalid_point_count,
                       rejected_samples=invalid_points,
                       response=response_debug_payload(response))
        log_action("traffic.history_fetch_completed", hex=hex_code or None,
                   callsign=flight or None, point_count=len(history),
                   found=bool(payload.get("found")))
        _HISTORY_ERROR_TRACKER.record_recovery(
            identity, "traffic.history_fetch_recovered",
            callsign=flight or None, hex=hex_code or None,
            status_code=getattr(response, "status_code", None),
        )
        return history
    except Exception as exc:
        response_for_log = getattr(exc, "response", None)
        if response_for_log is None:
            response_for_log = response
        _HISTORY_ERROR_TRACKER.record_failure(
            identity, "traffic.history_fetch_failed", exc,
            hex=hex_code or None, callsign=flight or None,
            response=response_debug_payload(response_for_log),
        )
        return []


# =========================
# ENGINE
# =========================

class TrafficEngine:
    """Pollt ADS-B-Daten und erzeugt pro Tick einen UI-State-Dict."""

    REFRESH_SEC = 2.0
    STALE_TIMEOUT = 10.0
    MAX_AIRCRAFT = 400
    MAX_TRAIL_POINTS = 400
    TRAIL_POINTS_ALL = 120      # Punkte pro Flugzeug im "Spuren"-Modus
    OWN_MAX_AGE = 10.0          # eigene Position gilt max. 10 s (TCAS)
    TCAS_RADIUS_KM = 80         # Sammelradius um das eigene Flugzeug

    def __init__(self, fetcher=None, history_fetcher=None):
        self.fetcher = fetcher or fetch_adsb_traffic
        self.history_fetcher = history_fetcher or fetch_aircraft_history
        self.route_cache = RouteCache()

        self.options = {
            "show_all": False,
            "follow": True,
            "labels": True,
            "route": True,
            "hide_ground": False,
            "min_alt": None,
            "center": [50.033, 8.570],
            "zoom": 7,
            "bounds": None,          # sichtbarer Kartenausschnitt (n, s, e, w)
            "search": "",            # Substring-Filter: Callsign/Hex/Typ/Reg/Airline
            "airline": "",           # Airline-Filter (Name oder ICAO-Präfix)
            "type_filter": "",       # ICAO-Typ-Filter (z. B. "A359")
            "trails": False,         # Spuren aller Flugzeuge zeichnen
            "tcas": False,           # eigenes Flugzeug (MSFS) als Zentrum + Relativdaten
            "alerts": True,          # Notfall-Benachrichtigung (Toast + Ton)
        }
        self.selected = None
        self.running = False
        self._thread = None
        self._lock = threading.RLock()
        self._poll_error_tracker = ErrorStateTracker()

        self._markers = {}       # call -> info (aktuell sichtbar)
        self._last_seen = {}
        self._last_info = {}
        self._trail = []         # [(lat, lon, hexcolor)]  (aktuelle Session)
        self._history_trail = [] # Proxy-Positionen der letzten Stunde
        self._history_generation = 0
        self._history_requested_key = None
        self._history_request_inflight = False
        self._history_request_started = 0.0
        self._history_suppressed_selection = None
        self._trails = {}        # call -> [(lat, lon)]    (alle, bei Option "trails")
        self._route = None
        self._route_for = None
        self._seq = 0
        self._zoom_cmd = 0
        self._center_cmd = None
        self._emerg_seen = set() # Notfälle, für die schon alarmiert wurde
        self._alerts = []        # neue Alarme (werden im State geleert)
        self._own = None         # {"lat","lon","alt","hdg","t"} aus MSFS

        self.on_state = None     # Callback: fn(state_dict) – von der UI gesetzt

    # ---------------- Steuerung ----------------
    def start(self, callsign=None):
        target = callsign or None
        with self._lock:
            already_running = self.running
            self.selected = target
            if not target:
                self.options["show_all"] = True
            self._trail = []
            self._history_trail = []
            self._history_generation += 1
            self._history_requested_key = None
            self._history_request_inflight = False
            self._history_request_started = 0.0
            self._history_suppressed_selection = None
            self._route = None
            self._route_for = None
        log_action("traffic_engine.start", callsign=target,
                   show_all=self.options.get("show_all"), already_running=already_running)
        if target:
            self._maybe_request_history(target)
        if not already_running:
            self.running = True
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="ADS-B-map-poller")
            self._thread.start()
        else:
            self._emit()   # sofort Feedback

    def stop(self):
        was_running = self.running
        self.running = False
        log_action("traffic_engine.stop", was_running=was_running)

    def select(self, callsign):
        target = callsign or None
        with self._lock:
            if target == self.selected:
                log_action("traffic_engine.select_unchanged", callsign=target)
                return
            self.selected = target
            self._trail = []
            self._history_trail = []
            self._history_generation += 1
            self._history_requested_key = None
            self._history_request_inflight = False
            self._history_request_started = 0.0
            self._history_suppressed_selection = None
            self._route = None
            self._route_for = None
        log_action("traffic_engine.select", callsign=target)
        self._emit()
        if target:
            self._maybe_request_history(target)

    def clear_trail(self):
        with self._lock:
            self._trail = []
            self._history_trail = []
            self._history_generation += 1
            self._history_request_inflight = False
            self._history_request_started = time.monotonic()
            self._history_suppressed_selection = self.selected
            info = self._last_info.get(self.selected) if self.selected else None
            hex_code = str((info or {}).get("hex") or "").strip().lower()
            self._history_requested_key = ((self.selected, hex_code)
                                           if hex_code else None)
        log_action("traffic_engine.clear_trail")

    def _maybe_request_history(self, callsign=None):
        with self._lock:
            selected = callsign or self.selected
            if (not selected or selected != self.selected
                    or selected == self._history_suppressed_selection):
                return
            info = self._last_info.get(selected) or self._markers.get(selected)
            hex_code = str((info or {}).get("hex") or "").strip().lower()
            has_hex = bool(re.fullmatch(r"[a-f0-9]{1,16}", hex_code))
            flight_code = selected.strip().upper()
            if not has_hex and not re.fullmatch(r"[A-Z0-9-]{1,16}", flight_code):
                return
            identity = "hex:" + hex_code if has_hex else "flight:" + flight_code
            key = (selected, identity)
            now = time.monotonic()
            if self._history_trail and key != self._history_requested_key:
                return
            if key == self._history_requested_key:
                if (self._history_request_inflight or self._history_trail
                        or now - self._history_request_started < 60.0):
                    return
            self._history_requested_key = key
            self._history_request_inflight = True
            self._history_request_started = now
            generation = self._history_generation

        log_action("traffic_engine.history_fetch_requested", callsign=selected,
                   hex=hex_code or None, identity=identity)
        threading.Thread(target=self._load_history,
                         args=(selected, hex_code if has_hex else "", generation),
                         daemon=True, name="Aircraft-history").start()

    def _load_history(self, callsign, hex_code, generation):
        try:
            source_points = (self.history_fetcher(hex_code) if hex_code else
                             self.history_fetcher("", callsign=callsign)) or []
        except Exception as exc:
            source_points = []
            log_action("traffic_engine.history_fetch_failed", callsign=callsign,
                       hex=hex_code or None, error=str(exc))

        history = []
        for point in source_points:
            if isinstance(point, dict):
                lat, lon, alt = point.get("lat"), point.get("lon"), point.get("alt")
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                lat, lon = point[0], point[1]
                alt = point[2] if len(point) > 2 else None
            else:
                continue
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)
                    and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                continue
            history.append((lat, lon, altitude_color(alt)))

        with self._lock:
            if generation != self._history_generation or callsign != self.selected:
                return
            self._history_request_inflight = False
            self._history_trail = history[-self.MAX_TRAIL_POINTS:]
        log_action("traffic_engine.history_loaded", callsign=callsign,
                   hex=hex_code or None, point_count=len(history))
        self._emit()

    def zoom_by(self, delta):
        self._zoom_cmd = delta
        log_action("traffic_engine.zoom", delta=delta)
        self._emit()

    def set_center(self, lat, lon):
        self._center_cmd = [lat, lon]
        log_action("traffic_engine.set_center", center=[lat, lon])
        self._emit()

    def nearest_callsign(self, lat, lon, max_km=80):
        best, best_d = None, max_km
        for call, info in self._last_info.items():
            if info.get("lat") is None:
                continue
            d = haversine_km(lat, lon, info["lat"], info["lon"])
            if d < best_d:
                best, best_d = call, d
        return best

    # ---------------- Eigene Position (MSFS/SimConnect) ----------------
    def set_own_position(self, lat, lon, alt=None, hdg=None):
        """Thread-sicher vom MSFS-Worker aufgerufen."""
        try:
            own = {
                "lat": float(lat), "lon": float(lon),
                "alt": float(alt) if alt is not None else None,
                "hdg": float(hdg) if hdg is not None else None,
                "t": time.time(),
            }
        except (TypeError, ValueError):
            return
        with self._lock:
            self._own = own

    def _own_fresh(self):
        with self._lock:
            own = dict(self._own) if self._own else None
        if own and (time.time() - own["t"]) <= self.OWN_MAX_AGE:
            return own
        return None

    def _rel_for(self, call):
        """TCAS-Relativdaten: Uhr-Position, Distanz, Höhendifferenz, Tendenz."""
        own = self._own_fresh()
        info = self._last_info.get(call)
        if not own or not info or info.get("lat") is None or info.get("lon") is None:
            return None
        brg = bearing_deg(own["lat"], own["lon"], info["lat"], info["lon"])
        rel = (brg - (own.get("hdg") or 0.0)) % 360.0
        dist_km = haversine_km(own["lat"], own["lon"], info["lat"], info["lon"])
        alt_diff = None
        try:
            if own.get("alt") is not None:
                alt_diff = numeric_alt(info) - float(own["alt"])
        except (TypeError, ValueError):
            alt_diff = None
        trend = "level"
        try:
            vr = info.get("baro_rate")
            if vr is not None:
                trend = "climbing" if float(vr) > 300 else (
                    "descending" if float(vr) < -300 else "level")
        except (TypeError, ValueError):
            pass
        return {
            "clock": clock_position(rel),
            "brg": round(brg, 1),
            "rel_brg": round(rel, 1),
            "dist_km": round(dist_km, 1),
            "dist_nm": round(dist_km * 0.539957, 1),
            "alt_diff_ft": int(round(alt_diff)) if alt_diff is not None else None,
            "trend": trend,
        }

    # ---------------- Suche ----------------
    def _select_search_hit(self, info, source):
        """Pin a positioned search result, select it, and center the map."""
        call = str(info.get("call") or "").strip().upper()
        try:
            lat, lon = float(info.get("lat")), float(info.get("lon"))
        except (TypeError, ValueError, OverflowError):
            return None
        if (not call or not math.isfinite(lat) or not math.isfinite(lon)
                or not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0):
            return None
        info = dict(info)
        info["call"] = call
        info["lat"], info["lon"] = lat, lon
        with self._lock:
            self._last_info[call] = info
            self._last_seen[call] = time.time()
            self._markers[call] = info
            self.options["center"] = [lat, lon]
            # Do not let a stale pre-search viewport restrict the first poll
            # after the map has been moved to this worldwide result.
            self.options["bounds"] = None
        log_action("traffic_engine.search_match", callsign=call,
                   source=source, position=[lat, lon])
        self.select(call)
        self.set_center(lat, lon)
        return call

    def search_select(self, query):
        """Select the first positioned local match and center the map there."""
        q = (query or "").strip().upper()
        if not q:
            return None
        with self._lock:
            candidates = list(self._last_info.items())
        for call, info in candidates:
            if q in _search_haystack(call, info):
                return self._select_search_hit(info, "local")
        return None

    def search_snapshot_select(self, query, snapshot):
        """Select a positioned worldwide-snapshot match and center the map."""
        q = (query or "").strip().upper()
        if not q:
            return None
        for raw in snapshot or []:
            if not isinstance(raw, dict):
                continue
            try:
                info = parse_adsb(raw)
            except Exception:
                continue
            call = info.get("call")
            if (call and q in _search_haystack(call, info)
                    and info.get("lat") is not None and info.get("lon") is not None):
                return self._select_search_hit(info, "global")
        return None

    # ---------------- CSV-Export ----------------
    def csv_rows(self):
        """Snapshot des aktuellen Traffic als CSV-Zeilen (erste Zeile = Header)."""
        header = ["callsign", "hex", "registration", "type", "airline",
                  "lat", "lon", "alt_ft", "on_ground", "gs_kt", "track_deg",
                  "baro_rate_fpm", "squawk", "emergency", "dist_km", "dir_deg",
                  "time_utc"]
        rows = [header]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            items = [(call, dict(info)) for call, info in self._markers.items()]
        for call, info in sorted(items):
            alt = info.get("alt_baro")
            if alt is None:
                alt = info.get("alt_geom")
            rows.append([
                call, info.get("hex") or "", info.get("reg") or "",
                info.get("type") or "", AIRLINES.get(call[:3], call[:3]),
                info.get("lat"), info.get("lon"),
                "GND" if alt in (None, "ground") else alt,
                int(is_on_ground(info)),
                "" if info.get("gs") is None else info.get("gs"),
                "" if info.get("track") is None else info.get("track"),
                "" if info.get("baro_rate") is None else info.get("baro_rate"),
                info.get("squawk") or "", int(is_emergency(info)),
                "" if info.get("dst") is None else info.get("dst"),
                "" if info.get("dir") is None else info.get("dir"),
                now,
            ])
        return rows

    # ---------------- Loop ----------------
    def _loop(self):
        while self.running:
            try:
                self._tick()
            except Exception as e:
                first_failure = self._poll_error_tracker.record_failure(
                    "poll", "traffic_engine.poll_failed", e
                )
                if first_failure:
                    print(f"[Engine] Fehler: {e}")
            else:
                recovered = self._poll_error_tracker.record_recovery(
                    "poll", "traffic_engine.poll_recovered"
                )
                if recovered:
                    print("[Engine] Polling nach vorübergehendem Fehler wiederhergestellt")
            time.sleep(self.REFRESH_SEC)

    def _tick(self):
        opts = self.options
        center = opts.get("center") or [50.033, 8.570]
        own = self._own_fresh()
        if opts.get("tcas") and own:
            # TCAS-Modus: um das eigene Flugzeug herum sammeln
            query_center = [own["lat"], own["lon"]]
            radius_km = self.TCAS_RADIUS_KM
            bounds = None
        else:
            query_center = center
            radius_km = 250
            bounds = opts.get("bounds")

        started = time.monotonic()
        log_action("traffic_engine.poll_started", center=query_center,
                   radius_km=radius_km, bounds=bounds,
                   tcas=bool(opts.get("tcas") and own))
        raw = self.fetcher(query_center[0], query_center[1], radius_km, bounds)
        aircraft_count = len(raw) if raw is not None else 0
        infos = [parse_adsb(ac) for ac in (raw or []) if isinstance(ac, dict)]
        positioned_count = sum(info["lat"] is not None and info["lon"] is not None
                               for info in infos)
        identified_count = sum(bool(info["call"]) for info in infos)
        shown_count = self._process(infos)
        self._emit()
        log_action("traffic_engine.poll_completed", center=query_center,
                   aircraft_received=aircraft_count,
                   aircraft_parsed=len(infos),
                   aircraft_with_position=positioned_count,
                   aircraft_identified=identified_count,
                   aircraft_shown=shown_count,
                   elapsed_ms=round((time.monotonic() - started) * 1000, 1))

    def _process(self, infos):
        now = time.time()
        opts = self.options
        selected = self.selected
        q = (opts.get("search") or "").strip().upper()
        air = (opts.get("airline") or "").strip().upper()
        typ = (opts.get("type_filter") or "").strip().upper()

        visible = []
        for info in infos:
            call = info["call"]
            if not call or info["lat"] is None or info["lon"] is None:
                continue
            if not opts["show_all"] and call != selected:
                continue
            on_g = is_on_ground(info)
            if opts["hide_ground"] and on_g and call != selected:
                continue
            if opts["min_alt"] is not None and call != selected:
                try:
                    if float(info["alt_geom"] or info["alt_baro"] or 0) < opts["min_alt"]:
                        continue
                except (TypeError, ValueError):
                    pass
            if q and call != selected and q not in _search_haystack(call, info):
                continue
            if air and call != selected:
                name = AIRLINES.get(call[:3], call[:3]).upper()
                if air not in name and air not in call:
                    continue
            if typ and call != selected:
                if typ not in str(info.get("type") or "").upper():
                    continue
            self._last_seen[call] = now
            self._last_info[call] = info
            visible.append(info)

        if len(visible) > self.MAX_AIRCRAFT:
            c = self.options.get("center") or [0, 0]
            visible.sort(key=lambda i: haversine_km(c[0], c[1], i["lat"], i["lon"]))
            visible = visible[:self.MAX_AIRCRAFT]
        visible_calls = {i["call"] for i in visible}

        with self._lock:
            # nicht mehr sichtbare/empfangene Flüge entfernen
            for call in list(self._markers):
                gone = call not in visible_calls
                if gone and call in {i["call"] for i in infos}:
                    gone = True
                elif gone and now - self._last_seen.get(call, 0) < self.STALE_TIMEOUT:
                    gone = False
                if gone:
                    del self._markers[call]
                    self._last_info.pop(call, None)
                    self._last_seen.pop(call, None)

            for info in visible:
                self._markers[info["call"]] = info

            # Trail für Auswahl fortschreiben
            if selected and selected in self._markers:
                info = self._markers[selected]
                lat, lon = info["lat"], info["lon"]
                alt = numeric_alt(info)
                last_point = (self._trail[-1] if self._trail else
                              self._history_trail[-1] if self._history_trail else None)
                if (last_point is None or abs(last_point[0] - lat) > 1e-9
                        or abs(last_point[1] - lon) > 1e-9):
                    self._trail.append((lat, lon, altitude_color(alt)))
                    while len(self._trail) > self.MAX_TRAIL_POINTS:
                        self._trail.pop(0)

            # Spuren aller sichtbaren Flugzeuge (Option "trails")
            if opts.get("trails"):
                for info in visible:
                    pts = self._trails.setdefault(info["call"], [])
                    lat, lon = info["lat"], info["lon"]
                    if not pts or pts[-1][0] != lat or pts[-1][1] != lon:
                        pts.append((lat, lon))
                        while len(pts) > self.TRAIL_POINTS_ALL:
                            pts.pop(0)
                for call in list(self._trails):
                    if call not in self._markers:
                        del self._trails[call]
            elif self._trails:
                self._trails = {}

            # Notfall-Alarme (nur innerhalb des sichtbaren Bereichs, ohne Duplikate)
            if opts.get("alerts"):
                bounds = opts.get("bounds")
                for info in visible:
                    call = info["call"]
                    if is_emergency(info):
                        if call in self._emerg_seen:
                            continue
                        if bounds is not None and not _in_bounds(info, bounds):
                            continue
                        self._emerg_seen.add(call)
                        self._alerts.append({
                            "call": call,
                            "hex": info.get("hex"),
                            "lat": info.get("lat"),
                            "lon": info.get("lon"),
                            "squawk": str(info.get("squawk") or ""),
                            "dist_km": info.get("dst"),
                            "t": time.time(),
                        })
                        while len(self._alerts) > 20:
                            self._alerts.pop(0)
                    else:
                        self._emerg_seen.discard(call)

            # Route für Auswahl laden (Cache, blockiert ggf. kurz im Worker)
            if selected and opts["route"]:
                if self._route_for != selected:
                    try:
                        self._route = self.route_cache.get(selected)
                    except Exception:
                        self._route = None
                    self._route_for = selected
            else:
                self._route = None
                self._route_for = None

        if selected:
            self._maybe_request_history(selected)
        return len(visible)

    def _emit(self):
        if self.on_state is None:
            return
        try:
            self.on_state(self.state())
        except Exception as e:
            print(f"[Engine] on_state Fehler: {e}")

    # ---------------- State ----------------
    def state(self):
        opts = self.options
        with self._lock:
            selected = self.selected
            aircraft = []
            for call, info in self._markers.items():
                on_g = is_on_ground(info)
                emerg = is_emergency(info)
                alt = numeric_alt(info)
                rgb = altitude_to_rgb(round(alt / 500) * 500)
                if emerg:
                    rgb = emergency_rgb()
                elif on_g:
                    rgb = mix_rgb(rgb, GROUND_MIX_COLOR, 0.6)
                aircraft.append({
                    "call": call,
                    "lat": info["lat"],
                    "lon": info["lon"],
                    "hdg": float(info["track"] or 0),
                    "color": rgb_hex(rgb),
                    "kind": "heli" if str(info.get("category") or "").upper() == "A7" else "plane",
                    "ground": on_g,
                    "emerg": emerg,
                    "selected": call == selected,
                    "label": self._label(info, on_g, emerg),
                    "alt": alt,
                    "gs": info["gs"],
                    "type": info["type"],
                })
            aircraft.sort(key=lambda a: a["call"])

            all_trail_points = (self._history_trail + self._trail)[-self.MAX_TRAIL_POINTS:]
            trail = [[p[0], p[1], p[2]] for p in all_trail_points]
            trails = {}
            if opts.get("trails"):
                for call, pts in self._trails.items():
                    if call in self._markers and pts:
                        trails[call] = [[p[0], p[1]] for p in pts]
            route = None
            if selected and opts["route"] and self._route:
                r = self._route
                route = {
                    "origin_icao": r.get("origin_icao"), "dest_icao": r.get("dest_icao"),
                    "origin_name": r.get("origin_name"), "dest_name": r.get("dest_name"),
                    "path": great_circle_path(r["origin_lat"], r["origin_lon"],
                                              r["dest_lat"], r["dest_lon"], 48)
                    if None not in (r.get("origin_lat"), r.get("origin_lon"),
                                    r.get("dest_lat"), r.get("dest_lon")) else [],
                }

            own = self._own_fresh()
            own_out = None
            if own:
                own_out = {"lat": own["lat"], "lon": own["lon"],
                           "alt": own["alt"], "hdg": own["hdg"], "valid": True}

            selected_rel = None
            if selected and own:
                selected_rel = self._rel_for(selected)

            alerts = self._alerts
            self._alerts = []

            center_cmd = None
            if opts.get("tcas") and own:
                center_cmd = [own["lat"], own["lon"]]   # TCAS folgt dem eigenen Flugzeug
            elif selected and opts["follow"] and selected in self._markers:
                m = self._markers[selected]
                center_cmd = [m["lat"], m["lon"]]
            elif self._center_cmd is not None:
                center_cmd = self._center_cmd
            if not (opts.get("tcas") and own):
                self._center_cmd = None

            zoom_cmd = self._zoom_cmd
            self._zoom_cmd = 0

            self._seq += 1
            return {
                "seq": self._seq,
                "running": self.running,
                "selected": selected,
                "aircraft": aircraft,
                "trail": trail,
                "trails": trails,
                "route": route,
                "own": own_out,
                "selected_rel": selected_rel,
                "alerts": alerts,
                "center": center_cmd,
                "zoom_cmd": zoom_cmd,
                "labels": opts["labels"],
                "counts": {"shown": len(aircraft), "recv": len(self._last_info)},
                "time": time.strftime("%H:%M:%S"),
                "status": SOURCES.snapshot(),
                "net": (MESH.role_view() if MESH is not None else None),
            }

    def _label(self, info, on_g, emerg):
        if not self.options["labels"]:
            return None
        call = info["call"]
        if on_g or fmt_alt(info) == "GND":
            label = f"{call} · GND"
        else:
            gs = int(float(info["gs"])) if info["gs"] is not None else "-"
            label = f"{call} · {fmt_alt(info)} ft · {gs} kt"
        if emerg:
            label = f"⚠ {label}"
        return label

    # ---------------- Info-Panel Daten ----------------
    def info_lines(self, callsign):
        """Strukturierte Info-Daten für das Detail-Panel."""
        info = self._last_info.get(callsign)
        if not info:
            return None
        route = self._route if (self.selected == callsign and self._route) else None
        return {
            "call": callsign,
            "airline": AIRLINES.get(callsign[:3], callsign[:3]),
            "type": info.get("type"),
            "category": CATEGORY_NAMES.get(str(info.get("category") or "").upper(),
                                            info.get("category")),
            "reg": info.get("reg"),
            "hex": info.get("hex"),
            "ground": is_on_ground(info),
            "emergency": is_emergency(info),
            "squawk": info.get("squawk"),
            "lat": info.get("lat"),
            "lon": info.get("lon"),
            "dst": info.get("dst"),
            "dir": info.get("dir"),
            "alt_baro": info.get("alt_baro"),
            "alt_geom": info.get("alt_geom"),
            "gs": info.get("gs"),
            "track": info.get("track"),
            "baro_rate": info.get("baro_rate"),
            "nav_qnh": info.get("nav_qnh"),
            "nav_alt_mcp": info.get("nav_alt_mcp"),
            "seen": info.get("seen"),
            "rssi": info.get("rssi"),
            "messages": info.get("messages"),
            "route": route,
            "rel": self._rel_for(callsign),   # TCAS-Relativdaten (oder None)
        }
