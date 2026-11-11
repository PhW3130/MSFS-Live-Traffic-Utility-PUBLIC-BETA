"""
MSFS Live Traffic Utility – moderne Qt-Desktop-GUI (PySide6).

Architektur
-----------
* ``traffic_core.TrafficEngine``  : UI-unabhängige Logik (ADS-B-Polling,
  Filter, Auswahl, Trail, Route, State-Snapshots)
* ``msfs_ai.MsfsAiRunner``        : SimConnect/AI-Traffic-Injection (Thread)
* ``main.py``                     : Qt-View (Dark Theme, Tabelle, Info-Panel)
  mit ``QWebEngineView`` + Leaflet-Karte (``gui_web/``), angebunden über
  ``QWebChannel`` (Signal/Slot-Bridge ``bridge``).
"""

# Show the splash before importing QtWebEngine, SimConnect and the traffic stack.
# These imports account for most of the cold-start delay in the packaged app.
import sys as _bootstrap_sys

_bootstrap_app = None
_bootstrap_splash = None
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication as _BootstrapApplication
    from startup_splash import StartupSplash as _BootstrapSplash

    _bootstrap_app = _BootstrapApplication(_bootstrap_sys.argv)
    _bootstrap_app.setApplicationName("MSFS Live Traffic Utility")
    _bootstrap_app.setApplicationDisplayName("MSFS Live Traffic Utility")
    _bootstrap_splash = _BootstrapSplash()
    _bootstrap_splash.show()
    _bootstrap_splash.raise_()
    _bootstrap_app.processEvents()


def _bootstrap_progress(progress, german, english):
    if _bootstrap_splash is not None:
        message = german if _bootstrap_splash.language == "de" else english
        _bootstrap_splash.set_progress(progress, message)


import csv
import faulthandler
import json
import math
import os
import sys
import threading
import time
import traceback

from app_logging import log_action, start_file_logging
from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot, QSettings, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QSplitter,
    QAbstractItemView, QFileDialog, QInputDialog, QMessageBox, QStackedLayout,
)
_bootstrap_progress(0.15, "Lade grafische Oberfläche …", "Loading desktop UI …")

from PySide6.QtWebEngineWidgets import QWebEngineView
_bootstrap_progress(0.22, "Lade Kartenanzeige …", "Loading map view …")
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
_bootstrap_progress(0.26, "Initialisiere Kartenkern …", "Initializing map engine …")
from PySide6.QtWebChannel import QWebChannel
_bootstrap_progress(0.30, "Kartenkomponente bereit …", "Map component ready …")

from traffic_core import (
    TrafficEngine, TEXT, LANG, TILE_SERVERS, COLOR_SCHEME, APP_DIR, SOURCES,
)
_bootstrap_progress(0.37, "Lade Traffic-Datenkern …", "Loading traffic data core …")
import traffic_core
import peer_net
_bootstrap_progress(0.43, "Live-Traffic-Dienste bereit …", "Traffic services ready …")

import msfs_ai
from msfs_ai import MsfsAiRunner, SIMCONNECT_AVAILABLE
_bootstrap_progress(0.48, "Lade MSFS-Anbindung …", "Loading MSFS integration …")
from aircraft_config import (
    AircraftTitleCatalog,
    discover_community_folders,
    normalize_community_folder,
)
from startup_splash import StartupSplash
_bootstrap_progress(0.52, "Bereite Startvorgang vor …", "Preparing startup …")

MAP_REQUIRED_ASSETS = (
    "map.html",
    "map.js",
    os.path.join("leaflet", "leaflet.js"),
    os.path.join("leaflet", "leaflet.css"),
    os.path.join("leaflet", "images", "layers.png"),
    os.path.join("leaflet", "images", "layers-2x.png"),
    os.path.join("leaflet", "images", "marker-icon.png"),
    os.path.join("leaflet", "images", "marker-icon-2x.png"),
    os.path.join("leaflet", "images", "marker-shadow.png"),
)

# Keep the map usable if MSFS starts after the app. Retry SimConnect with an
# exponential backoff rather than requiring a full application restart.
MSFS_RECONNECT_INITIAL_SEC = 5.0
MSFS_RECONNECT_MAX_SEC = 60.0


def resolve_gui_web_dir(app_dir, executable_dir=None):
    """Find a complete map asset folder in source and PyInstaller layouts."""
    roots = [app_dir]
    if executable_dir:
        roots.extend((executable_dir, os.path.join(executable_dir, "_internal")))
    candidates = []
    seen = set()
    for root in roots:
        candidate = os.path.abspath(os.path.join(root, "gui_web"))
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    for candidate in candidates:
        if all(os.path.isfile(os.path.join(candidate, asset))
               for asset in MAP_REQUIRED_ASSETS):
            return candidate
    # Prefer the least-incomplete map directory over the first directory that
    # merely contains map.html. This avoids selecting a stale shell page when a
    # better PyInstaller _internal/gui_web bundle is present beside it.
    ranked = []
    for index, candidate in enumerate(candidates):
        present = sum(os.path.isfile(os.path.join(candidate, asset))
                      for asset in MAP_REQUIRED_ASSETS)
        if os.path.isfile(os.path.join(candidate, "map.html")):
            ranked.append((present, -index, candidate))
    if ranked:
        return max(ranked)[2]
    return candidates[0] if candidates else os.path.join(app_dir, "gui_web")


_executable_dir = (os.path.dirname(os.path.abspath(sys.executable))
                   if getattr(sys, "frozen", False) else None)
GUI_WEB_DIR = resolve_gui_web_dir(APP_DIR, _executable_dir)
MAP_HTML = os.path.join(GUI_WEB_DIR, "map.html")
MAP_RUNTIME_DIAGNOSTICS_JS = """JSON.stringify((function () {
  const qtGlobal = typeof qt !== "undefined";
  const details = {};
  let diagnosticsFunction = false;
  let diagnosticsError = null;
  try {
    if (typeof window.ltuGetMapDiagnostics === "function") {
      const runtime = window.ltuGetMapDiagnostics();
      if (runtime && typeof runtime === "object") Object.assign(details, runtime);
      diagnosticsFunction = true;
    } else {
      const container = document.getElementById("map");
      const rect = container ? container.getBoundingClientRect() : null;
      details.document_ready = document.readyState;
      details.container_size = rect ? [rect.width, rect.height] : null;
    }
  } catch (error) {
    diagnosticsError = String(error && (error.stack || error.message) || error);
  }
  details.diagnostics_function = diagnosticsFunction;
  details.diagnostics_error = diagnosticsError;
  details.leaflet = typeof L !== "undefined";
  details.qwebchannel_constructor = typeof QWebChannel === "function";
  details.qt_global = qtGlobal;
  details.webchannel_transport = qtGlobal && !!qt.webChannelTransport;
  details.map_script_stage = window.ltuMapStartupStage || "map-js-not-started";
  details.leaflet_created = !!window.ltuLeafletCreated;
  details.map_visual_ready = !!window.ltuMapVisualReady;
  details.bridge_error = window.ltuMapBridgeError || null;
  details.map_runtime_error = window.ltuMapRuntimeError || null;
  const mapNotice = document.getElementById("map-notice");
  details.map_notice_visible = !!mapNotice && !mapNotice.hidden;
  details.map_notice_key = window.ltuMapNoticeKey || null;
  details.map_notice_text = details.map_notice_visible
    ? String(mapNotice.textContent || "").trim().slice(0, 600) : null;
  details.qwebchannel_script_error = window.ltuQWebChannelLoadError || null;
  details.map_asset_error = window.ltuMapAssetError || null;
  return details;
})())"""
COMMUNITY_SETTING_KEY = "sim/community_directory"
THEME = COLOR_SCHEME["gui_theme"]
ICON_CFG = COLOR_SCHEME.get("aircraft_icons", {})

# Snapshot the previous app defaults before QSettings restores a user's values.
# The Settings dialog uses these as its named "Standard" preset and reference.
STANDARD_THEME_KEYS = (
    "main_bg", "panel_bg", "text_color", "entry_bg", "entry_fg", "btn_bg",
    "btn_fg", "btn_disabled_bg", "label_color", "emergency_color", "accent_color",
)
STANDARD_THEME = {key: THEME.get(key, "#000000") for key in STANDARD_THEME_KEYS}
STANDARD_ICONS = {
    "size": max(12, min(64, int(ICON_CFG.get("size", 28)))),
    "selected_size": max(16, min(80, int(ICON_CFG.get("selected_size", 34)))),
}
STANDARD_GRADIENT = {
    key: tuple(value) for key, value in traffic_core.ALT_GRADIENT.items()
}
STANDARD_MAP_STYLE = (
    "OpenStreetMap" if "OpenStreetMap" in TILE_SERVERS else next(iter(TILE_SERVERS))
)
STANDARD_PEER_NET_ENABLED = bool(
    (COLOR_SCHEME.get("peer_net") or {}).get("enabled", True)
)
STANDARD_MSFS = {
    "ai_injection_enabled": True,
    "max_active_ai": max(0, min(200, int(msfs_ai.MAX_ACTIVE_AI))),
    "spawn_radius_km": max(5, min(80, int(round(float(msfs_ai.MAX_SPAWN_DISTANCE_KM))))),
    "parked_fillers": max(0, min(200, int(msfs_ai.MAX_PARKED_FILLERS))),
}


class MapDiagnosticPage(QWebEnginePage):
    """Persist QtWebEngine JavaScript warnings/errors to the application log."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        try:
            level_value = int(level)
        except (TypeError, ValueError):
            level_value = str(level)
        if isinstance(level_value, int) and level_value < 1:
            return
        source = os.path.basename(str(source_id).split("?", 1)[0])
        log_action(
            "map.javascript_console_message",
            level=level_value,
            message=" ".join(str(message).splitlines())[:1200],
            line=int(line_number),
            source=source[:160],
        )


# =========================
# BRIDGE (Python <-> JavaScript)
# =========================

class Bridge(QObject):
    # Python -> JS
    stateUpdated = Signal(str)
    settingsUpdated = Signal(str)
    # intern (Worker -> Main-Thread)
    stateReceived = Signal(object)
    msfsStatus = Signal(bool)
    mapReady = Signal()
    globalSearchResult = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.window = None     # wird von MainWindow gesetzt

    def deliver(self, state_dict):
        """Vom Engine-Thread aufgerufen: leitet den State weiter."""
        self.stateReceived.emit(state_dict)
        self.stateUpdated.emit(json.dumps(state_dict, default=str))

    # ---- Slots (JS -> Python) ----
    @Slot()
    def uiReady(self):
        log_action("map.ui_ready")
        if self.window:
            self.window.push_settings()
            self.deliver(self.window.engine.state())
        self.mapReady.emit()

    @Slot(str)
    def aircraftClicked(self, callsign):
        log_action("map.aircraft_clicked", callsign=callsign)
        if self.window:
            self.window.engine.select(callsign)

    @Slot(float, float)
    def mapContextMenu(self, lat, lon):
        if self.window:
            call = self.window.engine.nearest_callsign(lat, lon)
            log_action("map.context_menu", lat=lat, lon=lon, nearest_callsign=call)
            if call:
                self.window.engine.select(call)

    @Slot(float, float, float, float, float, float, float)
    def mapMoved(self, lat, lon, zoom, north, south, east, west):
        log_action("map.view_changed", center=[lat, lon], zoom=zoom,
                   bounds=[north, south, east, west])
        if self.window:
            self.window.engine.options["center"] = [lat, lon]
            self.window.engine.options["zoom"] = zoom
            # sichtbarer Kartenausschnitt -> Engine fragt genau diesen Bereich ab
            self.window.engine.options["bounds"] = [north, south, east, west]


def _choose_community_folder(parent, settings, *, defer_prompts=False):
    """Select an MSFS Community folder.

    When ``defer_prompts`` is true, interactive selection dialogs wait until
    the main map is visible, so no simulator installation is required to start
    the map. ``None`` means the caller should retry after showing the window; an
    empty string means the user explicitly skipped or cancelled selection.
    """
    saved = normalize_community_folder(settings.value(COMMUNITY_SETTING_KEY, ""))
    explicit_override = normalize_community_folder(os.environ.get("MSFS_COMMUNITY_PATH"))
    if not explicit_override:
        explicit_override = normalize_community_folder(os.environ.get("MSFS_PACKAGES_PATH"))
    if explicit_override:
        candidates = [explicit_override]
    else:
        candidates = discover_community_folders()
        if saved and saved not in candidates:
            candidates.append(saved)
    candidates = sorted(set(candidates), key=str.casefold)
    log_action("community.discovery_completed", candidates=candidates, saved_path=saved)

    selected = ""
    if len(candidates) > 1:
        if defer_prompts:
            log_action("community.multiple_candidates_prompt_deferred",
                       candidate_count=len(candidates))
            return None
        items = list(candidates) + [TEXT["community_browse"][LANG]]
        current = candidates.index(saved) if saved in candidates else 0
        choice, accepted = QInputDialog.getItem(
            parent,
            TEXT["community_multiple_title"][LANG],
            TEXT["community_multiple_prompt"][LANG],
            items,
            current,
            False,
        )
        log_action("community.multiple_candidates_dialog", candidate_count=len(candidates),
                   accepted=bool(accepted))
        if accepted:
            if choice == TEXT["community_browse"][LANG]:
                raw_path = QFileDialog.getExistingDirectory(
                    parent,
                    TEXT["community_multiple_title"][LANG],
                    saved or candidates[current],
                )
                selected = normalize_community_folder(raw_path)
                if raw_path and not selected:
                    QMessageBox.warning(
                        parent,
                        TEXT["community_missing_title"][LANG],
                        TEXT["community_invalid"][LANG],
                    )
            else:
                selected = normalize_community_folder(choice)
        else:
            selected = saved
            if not selected:
                QMessageBox.information(
                    parent,
                    TEXT["community_none_title"][LANG],
                    TEXT["community_none_message"][LANG],
                )
    elif candidates:
        selected = candidates[0]
    else:
        if defer_prompts:
            log_action("community.folder_prompt_deferred")
            return None
        QMessageBox.information(
            parent,
            TEXT["community_missing_title"][LANG],
            TEXT["community_missing_prompt"][LANG],
        )
        raw_path = QFileDialog.getExistingDirectory(
            parent,
            TEXT["community_missing_title"][LANG],
            os.path.expanduser("~"),
        )
        selected = normalize_community_folder(raw_path)
        if raw_path and not selected:
            QMessageBox.warning(
                parent,
                TEXT["community_missing_title"][LANG],
                TEXT["community_invalid"][LANG],
            )

    if selected:
        settings.setValue(COMMUNITY_SETTING_KEY, selected)
        settings.sync()
        log_action("community.folder_selected", path=selected, candidates=len(candidates))
    else:
        log_action("community.folder_selection_skipped", candidates=len(candidates))
    return selected


# =========================
# MAIN WINDOW
# =========================

class MainWindow(QMainWindow):
    def __init__(self, community_directory=None, aircraft_catalog=None,
                 startup_callback=None, require_aircraft_catalog=False):
        super().__init__()
        self.community_directory = community_directory or ""
        self.aircraft_catalog = aircraft_catalog
        self.require_aircraft_catalog = bool(require_aircraft_catalog)
        self._startup_callback = startup_callback
        self._map_load_finished = False
        self._map_load_success = None
        self._map_js_ready = False
        self._last_map_runtime_diagnostics = None
        self._global_search_generation = 0
        self._active_global_search = None
        self._global_search_inflight = False
        self._closing = False
        self._updating_table = False
        self._msfs_probe_stop = threading.Event()
        self.setWindowTitle(TEXT["title"][LANG])
        self.resize(1500, 900)

        self.engine = TrafficEngine()
        self.runner = MsfsAiRunner(
            community_directory=self.community_directory,
            aircraft_catalog=self.aircraft_catalog,
            require_aircraft_catalog=self.require_aircraft_catalog,
        )
        self.bridge = Bridge(self)
        self.bridge.window = self
        self.bridge.mapReady.connect(self._on_map_javascript_ready)
        self.bridge.globalSearchResult.connect(self._on_global_search_result)
        self.engine.on_state = self.bridge.deliver
        # MSFS liefert die eigene Position an die Engine (TCAS/Karte)
        self.runner.on_own_position = self.engine.set_own_position
        # Peer-Mesh: Statusaustausch + Ersatz-Proxy-Wahl (LAN/Unicast)
        self.peernet = peer_net.PeerNet()
        traffic_core.MESH = self.peernet
        self.settings = QSettings("LiveTraffic", "MSFSLiveTrafficUtility")
        self._ai_injection_preference = True
        self._restore_saved_appearance()
        self._toast_last = None
        self._is_restoring_settings = True

        self._set_startup_status(0.82, TEXT["startup_build_gui"][LANG])
        self._build_ui()
        self._set_startup_status(0.88, TEXT["startup_build_map"][LANG])
        self._build_webview()
        self._build_shortcuts()
        self._restore_settings()
        self._is_restoring_settings = False
        self._build_status_leds()
        self._set_startup_status(0.93, TEXT["startup_ready"][LANG])
        self.peernet.start()

        self.bridge.stateReceived.connect(self.apply_state)
        self.bridge.msfsStatus.connect(self.msfs_ready)
        self._set_startup_status(0.96, TEXT["startup_ready"][LANG])

        log_action(
            "window.initialized",
            simconnect_available=SIMCONNECT_AVAILABLE,
            community_directory=self.community_directory,
            aircraft_title_count=(len(self.aircraft_catalog.titles)
                                  if self.aircraft_catalog is not None else 0),
            aircraft_catalog_required=self.require_aircraft_catalog,
        )
        threading.Thread(target=self._msfs_probe, daemon=True, name="MSFS-probe").start()

    def _set_startup_status(self, progress, message):
        if self._startup_callback is not None:
            self._startup_callback(progress, str(message))

    @staticmethod
    def _valid_hex_color(value):
        return (isinstance(value, str) and len(value) == 7 and value.startswith("#")
                and all(char in "0123456789abcdefABCDEF" for char in value[1:]))

    @staticmethod
    def _hex_color_rgb(value):
        if not MainWindow._valid_hex_color(value):
            return None
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))

    def _restore_saved_appearance(self):
        """Load user-level visual preferences before the widgets are created."""
        global QCOLOR_EMERG
        saved_theme = self.settings.value("appearance/theme", {})
        if isinstance(saved_theme, dict):
            for key, value in saved_theme.items():
                if key in THEME and self._valid_hex_color(value):
                    THEME[key] = value
        QCOLOR_EMERG = QColor(THEME.get("emergency_color", "#ff5252"))
        saved_icons = self.settings.value("appearance/icons", {})
        if isinstance(saved_icons, dict):
            for key in ("size", "selected_size"):
                try:
                    if key in saved_icons:
                        ICON_CFG[key] = max(12, min(80, int(saved_icons[key])))
                except (TypeError, ValueError, OverflowError):
                    pass
        saved_gradient = self.settings.value("appearance/gradient", {})
        if isinstance(saved_gradient, dict):
            gradient = {}
            for key in ("low", "mid", "high"):
                value = saved_gradient.get(key)
                if isinstance(value, str):
                    value = self._hex_color_rgb(value)
                if value is not None:
                    gradient[key] = value
            if gradient:
                traffic_core.ALT_GRADIENT = traffic_core.normalize_gradient(gradient)

        custom_style = TEXT["map_custom_style"][LANG]
        tile_url = str(self.settings.value("map/custom_tile_url", "") or "").strip()
        tile_attribution = str(
            self.settings.value("map/custom_tile_attribution", "") or ""
        ).strip()
        if tile_url and tile_attribution:
            try:
                max_zoom = max(1, min(22, int(self.settings.value(
                    "map/custom_tile_max_zoom", 19
                ))))
            except (TypeError, ValueError, OverflowError):
                max_zoom = 19
            TILE_SERVERS[custom_style] = (tile_url, max_zoom, tile_attribution)

    # ---------------- UI Aufbau ----------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(6)

        # --- Zeile 1: Tracking ---
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(TEXT["flight_label"][LANG]))
        self.entry = QLineEdit("EWG51Y")
        self.entry.setFixedWidth(110)
        self.entry.returnPressed.connect(self.start_tracking)
        row1.addWidget(self.entry)

        self.btn_start = QPushButton(TEXT["start"][LANG])
        self.btn_start.clicked.connect(self.start_tracking)
        row1.addWidget(self.btn_start)

        self.btn_stop = QPushButton(TEXT["stop"][LANG])
        self.btn_stop.clicked.connect(self.stop_tracking)
        self.btn_stop.setEnabled(False)
        row1.addWidget(self.btn_stop)

        self.chk_all = QCheckBox(TEXT["show_all"][LANG])
        self.chk_all.toggled.connect(lambda v: self.set_opt("show_all", v))
        row1.addWidget(self.chk_all)

        self.chk_follow = QCheckBox(TEXT["follow"][LANG])
        self.chk_follow.setChecked(True)
        self.chk_follow.toggled.connect(lambda v: self.set_opt("follow", v))
        row1.addWidget(self.chk_follow)

        self.chk_labels = QCheckBox(TEXT["labels"][LANG])
        self.chk_labels.setChecked(True)
        self.chk_labels.toggled.connect(lambda v: self.set_opt("labels", v))
        row1.addWidget(self.chk_labels)

        self.chk_route = QCheckBox(TEXT["route_show"][LANG])
        self.chk_route.setChecked(True)
        self.chk_route.toggled.connect(lambda v: self.set_opt("route", v))
        row1.addWidget(self.chk_route)
        row1.addStretch(1)
        root.addLayout(row1)

        # --- Zeile 2: Filter / Stil / MSFS ---
        row2 = QHBoxLayout()
        self.chk_ground = QCheckBox(TEXT["hide_ground"][LANG])
        self.chk_ground.toggled.connect(lambda v: self.set_opt("hide_ground", v))
        row2.addWidget(self.chk_ground)

        row2.addWidget(QLabel(TEXT["min_alt"][LANG]))
        self.spin_alt = QSpinBox()
        self.spin_alt.setRange(0, 45000)
        self.spin_alt.setSingleStep(500)
        self.spin_alt.setSpecialValueText("–")
        self.spin_alt.setFixedWidth(90)
        self.spin_alt.valueChanged.connect(
            lambda v: self.set_opt("min_alt", v if v > 0 else None))
        row2.addWidget(self.spin_alt)

        self.btn_trail = QPushButton(TEXT["clear_trail"][LANG])
        self.btn_trail.clicked.connect(self.clear_trail)
        row2.addWidget(self.btn_trail)

        row2.addWidget(QLabel(TEXT["zoom_out"][LANG]))
        btn_zo = QPushButton("−")
        btn_zo.setFixedWidth(34)
        btn_zo.clicked.connect(lambda: self.zoom_map(-1))
        row2.addWidget(btn_zo)
        btn_zi = QPushButton("+")
        btn_zi.setFixedWidth(34)
        btn_zi.clicked.connect(lambda: self.zoom_map(1))
        row2.addWidget(btn_zi)

        row2.addWidget(QLabel(TEXT["map_style"][LANG]))
        self.combo_style = QComboBox()
        self.combo_style.addItems(list(TILE_SERVERS.keys()))
        self.combo_style.currentTextChanged.connect(self.apply_map_style)
        row2.addWidget(self.combo_style)

        self.chk_msfs = QCheckBox(TEXT["msfs_ai"][LANG])
        self.chk_msfs.setToolTip(TEXT["msfs_ai_tooltip"][LANG])
        self.chk_msfs.setEnabled(False)
        self.chk_msfs.toggled.connect(self.toggle_msfs)
        row2.addWidget(self.chk_msfs)
        self.lbl_msfs = QLabel(TEXT["msfs_check"][LANG])
        row2.addWidget(self.lbl_msfs)
        row2.addStretch(1)
        root.addLayout(row2)

        # --- Zeile 3: Suche / Filter / Spuren / TCAS / Alarm / CSV ---
        row3 = QHBoxLayout()
        row3.addWidget(QLabel(TEXT["search"][LANG]))
        self.entry_search = QLineEdit()
        self.entry_search.setPlaceholderText(TEXT["search_ph"][LANG])
        self.entry_search.setFixedWidth(170)
        self.entry_search.textChanged.connect(self.on_search_text_changed)
        self.entry_search.returnPressed.connect(self.search_jump)
        row3.addWidget(self.entry_search)

        row3.addWidget(QLabel(TEXT["airline_filter"][LANG]))
        self.entry_airline = QLineEdit()
        self.entry_airline.setFixedWidth(110)
        self.entry_airline.textChanged.connect(lambda t: self.set_opt("airline", t))
        row3.addWidget(self.entry_airline)

        row3.addWidget(QLabel(TEXT["type_filter"][LANG]))
        self.entry_type = QLineEdit()
        self.entry_type.setFixedWidth(70)
        self.entry_type.textChanged.connect(lambda t: self.set_opt("type_filter", t))
        row3.addWidget(self.entry_type)

        self.chk_trails = QCheckBox(TEXT["trails_show"][LANG])
        self.chk_trails.toggled.connect(lambda v: self.set_opt("trails", v))
        row3.addWidget(self.chk_trails)

        self.chk_tcas = QCheckBox(TEXT["tcas"][LANG])
        self.chk_tcas.setToolTip(TEXT["tcas_tooltip"][LANG])
        self.chk_tcas.toggled.connect(lambda v: self.set_opt("tcas", v))
        row3.addWidget(self.chk_tcas)

        self.chk_alerts = QCheckBox(TEXT["alerts"][LANG])
        self.chk_alerts.setChecked(True)
        self.chk_alerts.toggled.connect(lambda v: self.set_opt("alerts", v))
        row3.addWidget(self.chk_alerts)

        self.btn_csv = QPushButton(TEXT["csv_export"][LANG])
        self.btn_csv.clicked.connect(self.export_csv)
        row3.addWidget(self.btn_csv)

        self.btn_settings = QPushButton("⚙ " + TEXT["settings"][LANG])
        self.btn_settings.clicked.connect(self.open_settings)
        row3.addWidget(self.btn_settings)
        row3.addStretch(1)
        root.addLayout(row3)

        # --- Mitte: Liste | Karte | Info ---
        split = QSplitter(Qt.Horizontal)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            TEXT["col_call"][LANG], TEXT["col_type"][LANG],
            TEXT["col_alt"][LANG], TEXT["col_gs"][LANG]])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.itemSelectionChanged.connect(self.on_table_select)
        self.table.doubleClicked.connect(self.on_table_double_click)
        self.table.setMinimumWidth(260)
        self.table.setMaximumWidth(420)
        split.addWidget(self.table)

        self.map_surface = QWidget()
        self.map_stack = QStackedLayout(self.map_surface)
        self.map_stack.setContentsMargins(0, 0, 0, 0)
        self.map_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.web = QWebEngineView()
        self.map_stack.addWidget(self.web)
        self.map_error_overlay = QLabel("", self.map_surface)
        self.map_error_overlay.setAlignment(Qt.AlignCenter)
        self.map_error_overlay.setWordWrap(True)
        self.map_error_overlay.setMargin(24)
        self.map_error_overlay.setObjectName("mapErrorOverlay")
        self.map_error_overlay.setStyleSheet(
            "QLabel#mapErrorOverlay { background-color: rgba(16,19,28,238); "
            "border: 1px solid #ff5252; border-radius: 10px; "
            "color: #f2f4fa; font-size: 15px; font-weight: 600; }"
        )
        self.map_error_overlay.hide()
        self.map_stack.addWidget(self.map_error_overlay)
        split.addWidget(self.map_surface)

        self.info = QTextEdit()
        self.info.setReadOnly(True)
        self.info.setMinimumWidth(300)
        self.info.setMaximumWidth(420)
        split.addWidget(self.info)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        root.addWidget(split, 1)

        # --- Statusleiste ---
        self.status_left = QLabel(TEXT["status_ready"][LANG])
        self.status_right = QLabel("")
        sb = self.statusBar()
        sb.addWidget(self.status_left, 1)
        sb.addPermanentWidget(self.status_right)

        self.setStyleSheet(build_qss())

    def showEvent(self, event):
        super().showEvent(event)
        log_action("window.show_event", **self._map_view_details())
        # QWebEngine/Leaflet can calculate a zero-sized viewport if its parent
        # was shown behind the splash before the first Qt event-loop turn.
        QTimer.singleShot(0, self.refresh_map_layout)
        QTimer.singleShot(150, self.refresh_map_layout)

    def _map_view_details(self):
        def dimensions(widget):
            try:
                return [int(widget.width()), int(widget.height())]
            except Exception:
                return None

        try:
            main_visible = bool(self.isVisible())
        except Exception:
            main_visible = None
        try:
            web_visible = bool(self.web.isVisible())
        except Exception:
            web_visible = None
        return {
            "main_visible": main_visible,
            "web_visible": web_visible,
            "web_size": dimensions(self.web),
        }

    def show_map_error_overlay(self, message):
        """Show a Qt-side error above the map even when map.html did not load."""
        text = str(message)
        was_visible = self.map_error_overlay.isVisible()
        changed = self.map_error_overlay.text() != text
        self.map_error_overlay.setText(text)
        self.map_stack.setCurrentWidget(self.map_error_overlay)
        self.map_error_overlay.show()
        self.map_error_overlay.raise_()
        if not was_visible or changed:
            log_action("map.screen_error_shown", message=text[:500],
                       **self._map_view_details())

    def hide_map_error_overlay(self):
        self.map_stack.setCurrentWidget(self.web)
        self.map_error_overlay.hide()

    def show_map_load_fallback(self):
        """Use the native fallback only when the page has no specific notice."""
        diagnostics = self._last_map_runtime_diagnostics or {}
        notice_key = diagnostics.get("map_notice_key")
        notice_visible = bool(diagnostics.get("map_notice_visible"))
        if notice_visible and notice_key not in (None, "", "loading"):
            # map.html has a more useful diagnosis (for example the exact
            # file:// URL of a failed local script); never cover it with Qt's
            # generic page-load message.
            self.hide_map_error_overlay()
            return False
        self.show_map_error_overlay(TEXT["map_load_failed"][LANG])
        return True

    def _on_map_runtime_diagnostics(self, result):
        """Decode and retain QWebEngine's serialized JavaScript startup probe."""
        result_type = type(result).__name__
        diagnostics = result
        decode_error = None
        if not isinstance(diagnostics, dict) and hasattr(diagnostics, "toVariant"):
            try:
                diagnostics = diagnostics.toVariant()
            except Exception as exc:
                decode_error = str(exc)
        if isinstance(diagnostics, str):
            try:
                diagnostics = json.loads(diagnostics)
            except (TypeError, ValueError) as exc:
                decode_error = str(exc)

        details = self._map_view_details()
        if not isinstance(diagnostics, dict):
            details["result_type"] = result_type
            details["result_excerpt"] = repr(result)[:1000] if result is not None else None
            details["decode_error"] = decode_error
            log_action("map.runtime_diagnostics_unavailable", **details)
            return

        self._last_map_runtime_diagnostics = dict(diagnostics)
        details["runtime"] = self._last_map_runtime_diagnostics
        container_size = diagnostics.get("container_size")
        if (isinstance(container_size, (list, tuple)) and len(container_size) == 2
                and (container_size[0] <= 0 or container_size[1] <= 0)):
            log_action("map.viewport_zero_size", **details)
        log_action("map.runtime_diagnostics", **details)
        if self._map_load_finished and not self._map_load_success:
            self.show_map_load_fallback()

    def _build_webview(self):
        self.web.setPage(MapDiagnosticPage(self.web))
        map_dir = GUI_WEB_DIR
        assets = {
            "map_html": MAP_HTML,
            "map_js": os.path.join(map_dir, "map.js"),
            "leaflet_js": os.path.join(map_dir, "leaflet", "leaflet.js"),
            "leaflet_css": os.path.join(map_dir, "leaflet", "leaflet.css"),
        }
        asset_status = {name: os.path.isfile(path) for name, path in assets.items()}
        log_action("map.assets_checked", paths=assets, **asset_status)
        missing_assets = [name for name, exists in asset_status.items() if not exists]
        if missing_assets:
            print(f"[Map] Missing local map assets: {', '.join(missing_assets)}")

        # HTML, Leaflet und map.js liegen nebeneinander bzw. im lokalen
        # gui_web-Unterordner; Kacheln kommen zusaetzlich von Tile-Servern.
        # Beide Zugriffsarten explizit erlauben, statt von Qt-Defaults oder
        # globalen Profil-Einstellungen abzuhaengen.
        self.web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.web.loadFinished.connect(self._on_map_load_finished)
        map_exists = os.path.isfile(MAP_HTML)
        log_action("map.page_load_requested", path=MAP_HTML, file_exists=map_exists,
                   independent_of_msfs=True)
        if not map_exists:
            print(f"[Map] Local map page was not found: {MAP_HTML}")
        map_url = QUrl.fromLocalFile(MAP_HTML)
        # Keep app-language metadata out of the file URL's query. The fragment
        # is client-side only and cannot affect local resource resolution.
        map_url.setFragment(f"lang={LANG}")
        self.web.setUrl(map_url)

    def _on_map_load_finished(self, success):
        self._map_load_finished = True
        self._map_load_success = bool(success)
        log_action("map.page_load_finished", success=self._map_load_success,
                   path=MAP_HTML, file_exists=os.path.isfile(MAP_HTML),
                   **self._map_view_details())
        if not self._map_load_success:
            self.status_left.setText(TEXT["map_load_failed"][LANG])
            print(f"[Map] QtWebEngine could not load the local map page: {MAP_HTML}")
            self.web.page().runJavaScript(
                MAP_RUNTIME_DIAGNOSTICS_JS,
                self._on_map_runtime_diagnostics,
            )
            # Give the page-side diagnostic a chance to arrive before showing
            # the generic Qt fallback. The timeout below also covers a failed
            # main document where JavaScript cannot answer at all.
            QTimer.singleShot(250, self.show_map_load_fallback)
        else:
            self.hide_map_error_overlay()
            self.web.page().runJavaScript(
                MAP_RUNTIME_DIAGNOSTICS_JS,
                self._on_map_runtime_diagnostics,
            )
            QTimer.singleShot(4_000, self._request_map_runtime_diagnostics)
            QTimer.singleShot(15_000, self._request_map_runtime_diagnostics)
            if self.engine.running:
                self.status_left.setText(TEXT["live_mode"][LANG])

    def _on_map_javascript_ready(self):
        self._map_js_ready = True
        self.hide_map_error_overlay()
        log_action("map.javascript_ready", **self._map_view_details())

    def _request_map_runtime_diagnostics(self):
        if not self._map_load_finished or not self._map_load_success:
            return
        self.web.page().runJavaScript(
            MAP_RUNTIME_DIAGNOSTICS_JS,
            self._on_map_runtime_diagnostics,
        )

    def refresh_map_layout(self):
        """Recalculate Leaflet's viewport after the main window becomes visible."""
        self.web.page().runJavaScript(
            "if (window.ltuRefreshMapLayout) window.ltuRefreshMapLayout();"
        )
        self.web.page().runJavaScript(
            MAP_RUNTIME_DIAGNOSTICS_JS,
            self._on_map_runtime_diagnostics,
        )
        log_action("map.layout_refresh_requested", **self._map_view_details())

    def _build_shortcuts(self):
        sc_esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        sc_esc.activated.connect(self.clear_selection)
        sc_plus = QShortcut(QKeySequence(Qt.Key_Plus), self)
        sc_plus.activated.connect(lambda: self.zoom_map(1, source="keyboard"))
        sc_minus = QShortcut(QKeySequence(Qt.Key_Minus), self)
        sc_minus.activated.connect(lambda: self.zoom_map(-1, source="keyboard"))

    # ---------------- Status-LEDs (welcher Teil läuft / ist down) ----------------
    LED_COMPONENTS = (("proxy", "Proxy"), ("adsb.lol", "lol"), ("adsb.fi", "fi"),
                      ("opensky", "OSKY"), ("planetrack", "PlaneTrack"),
                      ("ersatz", "Ersatz"), ("msfs", "MSFS"))

    def _build_status_leds(self):
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.status_leds = {}
        for key, label in self.LED_COMPONENTS:
            led = QLabel(f"● {label}")
            led.setStyleSheet("color:#8b93a7;font-weight:600;")
            row.addWidget(led)
            self.status_leds[key] = led
        self.lbl_role = QLabel("")
        self.lbl_role.setStyleSheet("color:#4da3ff;font-weight:600;")
        row.addWidget(self.lbl_role)
        self.statusBar().addPermanentWidget(wrap)

    def _update_status_leds(self, st):
        status = st.get("status") or {}
        for key, _label in self.LED_COMPONENTS:
            led = self.status_leds.get(key)
            if led is None:
                continue
            e = status.get(key)
            if e is None or e.get("ok") is None:
                color, tip = "#8b93a7", "unbekannt"
            elif e.get("ok"):
                color, tip = "#3ddc84", "ok"
            elif e.get("code") == 429:
                color, tip = "#ffb020", "429 rate-limited (Cooldown)"
            else:
                color, tip = "#ff5252", str(e.get("detail") or "down")[:70]
            led.setStyleSheet(f"color:{color};font-weight:600;")
            led.setToolTip(f"{key}: {tip}")
        net = st.get("net")
        if not net or not net.get("enabled"):
            self.lbl_role.setText(TEXT["mesh_off"][LANG])
            return
        role = net.get("role") or "client"
        parts = [TEXT.get("role_" + role, {"de": role, "en": role})[LANG]]
        er = net.get("ersatz")
        if er and role != "ersatz":
            parts.append(TEXT["mesh_ersatz_peer"][LANG].format(name=er.get("name") or "?"))
        parts.append(TEXT["mesh_peers"][LANG].format(n=net.get("peers", 0)))
        self.lbl_role.setText(" · ".join(parts))

    # ---------------- Aktionen ----------------
    def start_live_map(self):
        """Keep live ADS-B map mode running regardless of MSFS availability."""
        show_all = self.chk_all.isChecked()
        self.engine.start(None)
        # TrafficEngine.start(None) defaults the live map to all aircraft; retain
        # the user's explicit preference restored from QSettings instead.
        self.engine.options["show_all"] = show_all
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(True)
        status_key = ("map_load_failed" if self._map_load_success is False
                      else "live_mode")
        self.status_left.setText(TEXT[status_key][LANG])
        log_action("live_map.autostart", show_all=show_all,
                   msfs_available=SIMCONNECT_AVAILABLE)

    def set_opt(self, key, value):
        self.engine.options[key] = value
        if not self._is_restoring_settings:
            log_action("ui.option_changed", option=key, value=value)

    def clear_selection(self):
        log_action("ui.selection_cleared", source="keyboard")
        self.engine.select(None)

    def clear_trail(self):
        log_action("ui.trail_cleared")
        self.engine.clear_trail()

    def zoom_map(self, delta, source="button"):
        log_action("ui.map_zoom", delta=delta, source=source)
        self.engine.zoom_by(delta)

    def start_tracking(self):
        call = self.entry.text().strip().upper() or None
        log_action("ui.tracking_start", callsign=call,
                   show_all=self.chk_all.isChecked())
        if call is None:
            self.chk_all.setChecked(True)
        self.engine.start(call)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def stop_tracking(self):
        log_action("ui.tracking_stop")
        self.engine.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_left.setText(TEXT["tracking_stopped"][LANG])

    def apply_map_style(self, name):
        entry = TILE_SERVERS.get(name)
        if entry:
            if not self._is_restoring_settings:
                log_action("ui.map_style_changed", style=name)
            url, max_zoom, attribution = entry
            self.bridge.settingsUpdated.emit(json.dumps({
                "tile_url": url,
                "max_zoom": max_zoom,
                "tile_attribution": attribution,
            }))

    def push_settings(self):
        style = self.combo_style.currentText()
        url, max_zoom, attribution = TILE_SERVERS.get(style, list(TILE_SERVERS.values())[0])
        self.bridge.settingsUpdated.emit(json.dumps({
            "tile_url": url,
            "max_zoom": max_zoom,
            "tile_attribution": attribution,
            "center": self.engine.options["center"],
            "zoom": self.engine.options["zoom"],
            "icon_size": int(ICON_CFG.get("size", 26)),
            "selected_size": int(ICON_CFG.get("selected_size", 34)),
            "label_color": THEME.get("label_color", "#f2f4fa"),
            "accent": THEME.get("accent_color", "#4da3ff"),
            "emergency_color": THEME.get("emergency_color", "#ff5252"),
        }))

    def _settings_dialog_values(self):
        """Build the editable view without writing secrets to the action log."""
        source = traffic_core.traffic_source_config()
        proxy = traffic_core.proxy_preferences()
        mesh = peer_net.mesh_config()
        map_style = self.combo_style.currentText()
        tile_url, tile_max_zoom, tile_attribution = TILE_SERVERS.get(
            map_style, next(iter(TILE_SERVERS.values()))
        )
        options = self.engine.options
        theme_keys = STANDARD_THEME_KEYS
        values = {
            "planetrack_api_key": source["planetrack_api_key"],
            "planetrack_radius_nm": source["planetrack_radius_nm"],
            "proxy_enabled": bool(proxy["enabled"]),
            "proxy_url": proxy["url"],
            "proxy_token": proxy["token"],
            "peer_net_enabled": mesh["enabled"],
            "peer_net_name": mesh["name"],
            "map_style": map_style,
            "tile_url": tile_url,
            "tile_max_zoom": tile_max_zoom,
            "tile_attribution": tile_attribution,
            "center_lat": (options.get("center") or [50.033, 8.570])[0],
            "center_lon": (options.get("center") or [50.033, 8.570])[1],
            "zoom": options.get("zoom", 7),
            "theme": {key: THEME.get(key, "#000000") for key in theme_keys},
            "gradient": dict(traffic_core.ALT_GRADIENT),
            "icon_size": ICON_CFG.get("size", 28),
            "selected_icon_size": ICON_CFG.get("selected_size", 34),
            "filters": {key: options.get(key) for key in (
                "show_all", "follow", "labels", "route", "hide_ground", "trails",
                "tcas", "alerts",
            )},
            "min_alt": options.get("min_alt") or 0,
            "refresh_interval_sec": options.get("refresh_interval_sec", self.engine.REFRESH_SEC),
            "airline_filter": options.get("airline", ""),
            "type_filter": options.get("type_filter", ""),
            "community_directory": self.community_directory,
            "ai_injection_enabled": self._ai_injection_preference,
            "max_active_ai": msfs_ai.MAX_ACTIVE_AI,
            "spawn_radius_km": msfs_ai.MAX_SPAWN_DISTANCE_KM,
            "parked_fillers": msfs_ai.MAX_PARKED_FILLERS,
            "simconnect_dll": os.environ.get("MSFS_SIMCONNECT_DLL", ""),
        }
        default_style = (STANDARD_MAP_STYLE if STANDARD_MAP_STYLE in TILE_SERVERS
                         else next(iter(TILE_SERVERS)))
        default_tile_url, default_max_zoom, default_attribution = TILE_SERVERS[default_style]
        values["standard_values"] = {
            "planetrack_api_key": "",
            "planetrack_radius_nm": 40,
            "proxy_enabled": True,
            "proxy_url": "",
            "proxy_token": "",
            "peer_net_enabled": STANDARD_PEER_NET_ENABLED,
            "peer_net_name": "",
            "map_style": default_style,
            "tile_url": default_tile_url,
            "tile_max_zoom": default_max_zoom,
            "tile_attribution": default_attribution,
            "center_lat": 50.033,
            "center_lon": 8.570,
            "zoom": 7,
            "theme": dict(STANDARD_THEME),
            "gradient": dict(STANDARD_GRADIENT),
            "icon_size": STANDARD_ICONS["size"],
            "selected_icon_size": STANDARD_ICONS["selected_size"],
            "filters": {
                "show_all": True, "follow": True, "labels": True, "route": True,
                "hide_ground": False, "trails": False, "tcas": False, "alerts": True,
            },
            "min_alt": 0,
            "refresh_interval_sec": self.engine.REFRESH_SEC,
            "airline_filter": "",
            "type_filter": "",
            "community_directory": "",
            **STANDARD_MSFS,
            "simconnect_dll": "",
        }
        return values

    def open_settings(self):
        from settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            self._settings_dialog_values(), TILE_SERVERS, language=LANG, parent=self
        )
        if not dialog.exec():
            return
        self._apply_user_settings(dialog.values())

    def _apply_user_settings(self, values):
        """Persist and apply a complete preferences snapshot from the settings dialog."""
        global QCOLOR_EMERG
        community_raw = str(values.get("community_directory") or "").strip()
        community_path = normalize_community_folder(community_raw) if community_raw else ""
        if community_raw and not community_path:
            QMessageBox.warning(
                self, TEXT["settings"][LANG], TEXT["community_invalid"][LANG]
            )
            return False
        simconnect_dll = str(values.get("simconnect_dll") or "").strip()
        old_simconnect_dll = os.environ.get("MSFS_SIMCONNECT_DLL", "")
        if (simconnect_dll and simconnect_dll != old_simconnect_dll
                and not os.path.isfile(os.path.expandvars(os.path.expanduser(simconnect_dll)))):
            QMessageBox.warning(
                self, TEXT["settings"][LANG],
                ("Die ausgewählte SimConnect-DLL wurde nicht gefunden."
                 if LANG == "de" else "The selected SimConnect DLL was not found."),
            )
            return False

        style_name = str(values.get("map_style") or self.combo_style.currentText())
        custom_style = TEXT["map_custom_style"][LANG]
        if style_name == custom_style:
            TILE_SERVERS[style_name] = (
                str(values.get("tile_url") or "").strip(),
                max(1, min(22, int(values.get("tile_max_zoom", 19)))),
                str(values.get("tile_attribution") or "").strip(),
            )
        if style_name not in TILE_SERVERS:
            QMessageBox.warning(
                self, TEXT["settings"][LANG],
                ("Der gewählte Kartenstil ist nicht verfügbar."
                 if LANG == "de" else "The selected map style is unavailable."),
            )
            return False

        theme = dict(values.get("theme") or {})
        theme = {key: value for key, value in theme.items()
                 if key in THEME and self._valid_hex_color(value)}
        gradient_rgb = {
            key: self._hex_color_rgb(color)
            for key, color in (values.get("gradient_hex") or {}).items()
            if key in ("low", "mid", "high")
        }
        if len(gradient_rgb) != 3 or any(color is None for color in gradient_rgb.values()):
            QMessageBox.warning(
                self, TEXT["settings"][LANG],
                ("Bitte prüfe die Höhen-Gradient-Farben."
                 if LANG == "de" else "Please check the altitude gradient colors."),
            )
            return False

        community_changed = community_path != self.community_directory
        dll_changed = simconnect_dll != old_simconnect_dll
        s = self.settings
        s.setValue("traffic/planetrack_api_key", values["planetrack_api_key"])
        s.setValue("traffic/planetrack_radius_nm", values["planetrack_radius_nm"])
        s.setValue("traffic/refresh_interval_sec", values["refresh_interval_sec"])
        s.setValue("network/proxy_enabled", bool(values["proxy_enabled"]))
        s.setValue("network/proxy_url", values["proxy_url"])
        s.setValue("network/proxy_token", values["proxy_token"])
        s.setValue("network/peer_net_enabled", bool(values["peer_net_enabled"]))
        s.setValue("network/peer_net_name", values["peer_net_name"])
        s.setValue("map_style", style_name)
        if style_name == custom_style:
            s.setValue("map/custom_tile_url", values["tile_url"])
            s.setValue("map/custom_tile_attribution", values["tile_attribution"])
            s.setValue("map/custom_tile_max_zoom", values["tile_max_zoom"])
        s.setValue("center", [float(values["center_lat"]), float(values["center_lon"])])
        s.setValue("zoom", int(values["zoom"]))
        s.setValue("appearance/theme", theme)
        s.setValue("appearance/gradient", gradient_rgb)
        s.setValue("appearance/icons", {
            "size": int(values["icon_size"]),
            "selected_size": int(values["selected_icon_size"]),
        })
        filters = dict(values.get("filters") or {})
        for key in ("show_all", "follow", "labels", "route", "hide_ground",
                    "trails", "tcas", "alerts"):
            s.setValue(key, bool(filters.get(key)))
        s.setValue("min_alt", int(values.get("min_alt") or 0))
        s.setValue("search", str(self.entry_search.text() or ""))
        s.setValue("airline_filter", values["airline_filter"])
        s.setValue("type_filter", values["type_filter"])
        s.setValue("sim/community_directory", community_path)
        s.setValue("sim/ai_injection_enabled", bool(values["ai_injection_enabled"]))
        s.setValue("sim/max_active_ai", int(values["max_active_ai"]))
        s.setValue("sim/spawn_radius_km", float(values["spawn_radius_km"]))
        s.setValue("sim/parked_fillers", int(values["parked_fillers"]))
        s.setValue("sim/simconnect_dll", simconnect_dll)
        s.sync()

        traffic_core.configure_runtime_settings(
            planetrack_api_key=values["planetrack_api_key"],
            planetrack_radius_nm=int(values["planetrack_radius_nm"]),
            proxy_enabled=bool(values["proxy_enabled"]),
            proxy_url=str(values["proxy_url"] or ""),
            proxy_token=str(values["proxy_token"] or ""),
            peer_net_enabled=bool(values["peer_net_enabled"]),
            peer_net_name=str(values["peer_net_name"] or ""),
        )
        mesh_settings = peer_net.mesh_config()
        if self.peernet._running and not mesh_settings["enabled"]:
            threading.Thread(
                target=self.peernet.reconfigure, args=(mesh_settings,), daemon=True,
                name="Peer-mesh-reconfigure",
            ).start()
        else:
            self.peernet.reconfigure(mesh_settings)

        THEME.update(theme)
        QCOLOR_EMERG = QColor(THEME.get("emergency_color", "#ff5252"))
        ICON_CFG["size"] = int(values["icon_size"])
        ICON_CFG["selected_size"] = int(values["selected_icon_size"])
        traffic_core.ALT_GRADIENT = traffic_core.normalize_gradient(gradient_rgb)
        self.setStyleSheet(build_qss())

        self.engine.options.update({
            key: bool(filters.get(key))
            for key in ("show_all", "follow", "labels", "route", "hide_ground",
                        "trails", "tcas", "alerts")
        })
        self.engine.options["min_alt"] = values.get("min_alt") or None
        self.engine.options["airline"] = str(values.get("airline_filter") or "")
        self.engine.options["type_filter"] = str(values.get("type_filter") or "")
        self.engine.options["refresh_interval_sec"] = float(values["refresh_interval_sec"])
        self.engine.options["center"] = [float(values["center_lat"]), float(values["center_lon"])]
        self.engine.options["zoom"] = int(values["zoom"])
        self.engine.options["bounds"] = None
        self.engine._center_cmd = None
        self.engine._zoom_cmd = 0
        self._is_restoring_settings = True
        try:
            for key, widget in (
                ("show_all", self.chk_all), ("follow", self.chk_follow),
                ("labels", self.chk_labels), ("route", self.chk_route),
                ("hide_ground", self.chk_ground), ("trails", self.chk_trails),
                ("tcas", self.chk_tcas), ("alerts", self.chk_alerts),
            ):
                widget.setChecked(bool(filters.get(key)))
            self.spin_alt.setValue(int(values.get("min_alt") or 0))
            self.entry_airline.setText(str(values.get("airline_filter") or ""))
            self.entry_type.setText(str(values.get("type_filter") or ""))
            self.combo_style.setCurrentText(style_name)
        finally:
            self._is_restoring_settings = False

        self._ai_injection_preference = bool(values["ai_injection_enabled"])
        msfs_ai.MAX_ACTIVE_AI = max(0, min(200, int(values["max_active_ai"])))
        msfs_ai.MAX_SPAWN_DISTANCE_KM = max(5.0, min(80.0, float(values["spawn_radius_km"])))
        msfs_ai.MAX_PARKED_FILLERS = max(0, min(200, int(values["parked_fillers"])))
        if simconnect_dll:
            os.environ["MSFS_SIMCONNECT_DLL"] = simconnect_dll
        else:
            os.environ.pop("MSFS_SIMCONNECT_DLL", None)
        self.runner.set_ai_injection_enabled(self._ai_injection_preference)
        self.chk_msfs.setChecked(self._ai_injection_preference)
        if self.runner.available and self._ai_injection_preference:
            self.runner.start()

        if style_name == custom_style:
            self.combo_style.setCurrentText(style_name)
        self.push_settings()
        try:
            self.bridge.deliver(self.engine.state())
        except Exception:
            pass
        log_action(
            "settings.user_preferences_applied",
            map_style=style_name,
            peer_net_enabled=bool(values["peer_net_enabled"]),
            ai_injection_enabled=self._ai_injection_preference,
            community_path_changed=community_changed,
            simconnect_dll_changed=dll_changed,
        )
        if community_changed or dll_changed:
            QMessageBox.information(
                self, TEXT["settings"][LANG],
                ("Einstellungen gespeichert. Änderungen am Community-Ordner oder an der "
                 "SimConnect-DLL werden nach einem Neustart wirksam."
                 if LANG == "de" else
                 "Settings saved. Community folder or SimConnect DLL changes take effect "
                 "after restarting the app."),
            )
        else:
            self.status_left.setText(TEXT["settings"][LANG])
        return True

    def toggle_msfs(self, on):
        enabled = bool(on)
        self._ai_injection_preference = enabled
        log_action("ui.msfs_toggle", enabled=enabled)
        self.runner.set_ai_injection_enabled(enabled)
        # Keep the SimConnect worker alive for own-position/TCAS updates even
        # while AI injection is paused. It still stops on application close.
        if self.runner.available:
            self.runner.start()

    # ---------------- Suche / Export / Alarme ----------------
    def on_search_text_changed(self, text):
        self.set_opt("search", text)
        active = self._active_global_search
        if (active is not None
                and str(text or "").strip().upper() != active["query"]):
            self._active_global_search = None
            self._global_search_generation += 1
            log_action("ui.global_search_invalidated", reason="query_changed",
                       query=active["query"])

    def search_jump(self):
        """Submit a search: select local data now, then search one global snapshot."""
        query = self.entry_search.text().strip()
        self._global_search_generation += 1
        if not query:
            self._active_global_search = None
            log_action("ui.search_submitted", query="", match=None,
                       global_requested=False)
            return

        local_hit = self.engine.search_select(query)
        self._active_global_search = {
            "generation": self._global_search_generation,
            "query": query.upper(),
            "local_hit": local_hit,
        }
        log_action("ui.search_submitted", query=query, match=local_hit,
                   global_requested=True)
        if local_hit:
            self.status_left.setText(f"{TEXT['search_hit'][LANG]} {local_hit}")
        else:
            self.status_left.setText(TEXT["search_global_loading"][LANG])

        if self._global_search_inflight:
            # The current request already fetches a query-independent global
            # snapshot. Its completion will be matched against the newest active
            # search, so rapid Enter presses do not create more upstream loads.
            log_action("ui.global_search_coalesced",
                       generation=self._global_search_generation, query=query)
            return
        self._start_global_search()

    def _start_global_search(self):
        if self._closing or self._global_search_inflight:
            return
        self._global_search_inflight = True
        generation = self._global_search_generation
        query = (self._active_global_search or {}).get("query")
        log_action("ui.global_search_worker_started", generation=generation,
                   query=query)
        threading.Thread(target=self._global_search_worker, daemon=True,
                         name="Global-aircraft-search").start()

    def _global_search_worker(self):
        try:
            result = traffic_core.fetch_global_traffic()
        except Exception as exc:
            result = {
                "ok": False, "aircraft": [], "cached": False,
                "stale": False, "rate_limited": False,
                "in_flight": False, "error": str(exc),
            }
            log_action("ui.global_search_worker_failed", error=str(exc))
        if not self._closing:
            self.bridge.globalSearchResult.emit(result)

    @Slot(object)
    def _on_global_search_result(self, result):
        self._global_search_inflight = False
        active = self._active_global_search
        if active is None:
            log_action("ui.global_search_result_ignored", reason="no_active_search")
            return

        query = active["query"]
        if self.entry_search.text().strip().upper() != query:
            self._active_global_search = None
            log_action("ui.global_search_result_ignored", reason="query_changed",
                       query=query)
            return

        result = result if isinstance(result, dict) else {}
        snapshot = result.get("aircraft") or []
        hit = (self.engine.search_snapshot_select(query, snapshot)
               if result.get("ok") else None)
        if hit:
            self.status_left.setText(f"{TEXT['search_hit_global'][LANG]} {hit}")
        elif active.get("local_hit"):
            self.status_left.setText(
                f"{TEXT['search_hit'][LANG]} {active['local_hit']}")
        elif not result.get("ok"):
            self.status_left.setText(TEXT["search_global_error"][LANG])
        else:
            self.status_left.setText(f"{TEXT['search_none'][LANG]} '{query}'")

        self._active_global_search = None
        log_action("ui.global_search_completed", query=query, match=hit,
                   snapshot_aircraft=len(snapshot), cached=bool(result.get("cached")),
                   stale=bool(result.get("stale")),
                   rate_limited=bool(result.get("rate_limited")),
                   ok=bool(result.get("ok")))

    def export_csv(self):
        log_action("ui.csv_export_opened")
        default = os.path.join(
            os.path.expanduser("~"),
            f"live_traffic_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        path, _sel = QFileDialog.getSaveFileName(
            self, TEXT["csv_export"][LANG], default, "CSV (*.csv)")
        if not path:
            log_action("ui.csv_export_cancelled")
            return
        rows = self.engine.csv_rows()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f, delimiter=";").writerows(rows)
        except OSError as e:
            log_action("ui.csv_export_failed", path=path, error=str(e))
            self.status_left.setText(f"CSV-Fehler: {e}")
            return
        log_action("ui.csv_export_saved", path=path, row_count=len(rows) - 1)
        self.status_left.setText(
            f"{TEXT['csv_saved'][LANG]} {path} ({len(rows) - 1} {TEXT['csv_rows'][LANG]})")

    def emergency_alert(self, alert):
        """Notfall im Sichtbereich: Toast + Ton."""
        try:
            QApplication.beep()
        except Exception:
            pass
        call = alert.get("call") or alert.get("hex") or "?"
        log_action("traffic.emergency_alert", callsign=call,
                   squawk=alert.get("squawk"), distance_km=alert.get("dist_km"))
        sq = f" · Squawk {alert['squawk']}" if alert.get("squawk") else ""
        dist = ""
        if isinstance(alert.get("dist_km"), (int, float)):
            dist = f" · {alert['dist_km']:.0f} km"
        self.show_toast(f"{TEXT['emergency_toast'][LANG]}: {call}{sq}{dist}")

    def show_toast(self, text, ms=7000):
        log_action("ui.toast_shown", message=text, duration_ms=ms)
        lbl = QLabel(text, self)
        lbl.setStyleSheet(
            "background:#e94560;color:#ffffff;padding:10px 16px;border-radius:8px;"
            "font-weight:700;font-size:14px;")
        lbl.adjustSize()
        lbl.move(18, 18)
        lbl.show()
        self._toast_last = lbl
        QTimer.singleShot(ms, lbl.deleteLater)

    # ---------------- Einstellungen (QSettings) ----------------
    @staticmethod
    def _as_bool(v, default=False):
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        return str(v).lower() in ("true", "1", "yes")

    def _restore_settings(self):
        s = self.settings
        geo = s.value("geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

        # Apply user-owned direct-fallback/network preferences before the mesh
        # starts. Secrets stay in QSettings and are never included in logs.
        runtime = {}
        mappings = (
            ("traffic/planetrack_api_key", "planetrack_api_key", str),
            ("traffic/planetrack_radius_nm", "planetrack_radius_nm", int),
            ("network/proxy_url", "proxy_url", str),
            ("network/proxy_token", "proxy_token", str),
            ("network/peer_net_name", "peer_net_name", str),
        )
        for key, runtime_key, converter in mappings:
            value = s.value(key, None)
            if value is not None:
                try:
                    runtime[runtime_key] = converter(value)
                except (TypeError, ValueError, OverflowError):
                    pass
        for key, runtime_key in (("network/proxy_enabled", "proxy_enabled"),
                                 ("network/peer_net_enabled", "peer_net_enabled")):
            value = s.value(key, None)
            if value is not None:
                runtime[runtime_key] = self._as_bool(value, True)
        traffic_core.configure_runtime_settings(**runtime)
        self.peernet.reconfigure(peer_net.mesh_config())

        center = s.value("center")
        if isinstance(center, (list, tuple)) and len(center) == 2:
            try:
                self.engine.options["center"] = [float(center[0]), float(center[1])]
            except (TypeError, ValueError):
                pass
        try:
            zoom = s.value("zoom")
            if zoom is not None:
                self.engine.options["zoom"] = max(2, min(20, int(float(zoom))))
        except (TypeError, ValueError, OverflowError):
            pass
        style = s.value("map_style")
        if style in TILE_SERVERS:
            self.combo_style.setCurrentText(style)

        self.chk_all.setChecked(self._as_bool(s.value("show_all"), True))
        self.chk_follow.setChecked(self._as_bool(s.value("follow"), True))
        self.chk_labels.setChecked(self._as_bool(s.value("labels"), True))
        self.chk_route.setChecked(self._as_bool(s.value("route"), True))
        self.chk_ground.setChecked(self._as_bool(s.value("hide_ground")))
        self.chk_trails.setChecked(self._as_bool(s.value("trails")))
        self.chk_tcas.setChecked(self._as_bool(s.value("tcas")))
        self.chk_alerts.setChecked(self._as_bool(s.value("alerts"), True))
        try:
            min_alt = int(float(s.value("min_alt") or 0))
            if min_alt > 0:
                self.spin_alt.setValue(min_alt)
        except (TypeError, ValueError, OverflowError):
            pass
        self.entry_search.setText(str(s.value("search", "") or ""))
        self.entry_airline.setText(str(s.value("airline_filter", "") or ""))
        self.entry_type.setText(str(s.value("type_filter", "") or ""))
        try:
            interval = float(s.value("traffic/refresh_interval_sec", 2.0))
            self.engine.options["refresh_interval_sec"] = max(1.0, min(30.0, interval))
        except (TypeError, ValueError, OverflowError):
            self.engine.options["refresh_interval_sec"] = self.engine.REFRESH_SEC

        self._ai_injection_preference = self._as_bool(
            s.value("sim/ai_injection_enabled", True), True
        )
        self.runner.set_ai_injection_enabled(self._ai_injection_preference)
        try:
            msfs_ai.MAX_ACTIVE_AI = max(
                0, min(200, int(s.value("sim/max_active_ai", msfs_ai.MAX_ACTIVE_AI)))
            )
            msfs_ai.MAX_SPAWN_DISTANCE_KM = max(
                5.0, min(80.0, float(s.value(
                    "sim/spawn_radius_km", msfs_ai.MAX_SPAWN_DISTANCE_KM
                )))
            )
            msfs_ai.MAX_PARKED_FILLERS = max(
                0, min(200, int(s.value("sim/parked_fillers", msfs_ai.MAX_PARKED_FILLERS)))
            )
        except (TypeError, ValueError, OverflowError):
            pass
        saved_dll = s.value("sim/simconnect_dll", None)
        if saved_dll is not None:
            saved_dll = str(saved_dll or "").strip()
            if saved_dll:
                os.environ["MSFS_SIMCONNECT_DLL"] = saved_dll
            else:
                os.environ.pop("MSFS_SIMCONNECT_DLL", None)

        log_action("settings.restored", map_style=self.combo_style.currentText(),
                   options={key: self.engine.options.get(key) for key in (
                       "center", "zoom", "show_all", "follow", "labels", "route",
                       "hide_ground", "trails", "tcas", "alerts", "min_alt",
                       "refresh_interval_sec")})

    def _save_settings(self):
        log_action("settings.save_started")
        s = self.settings
        try:
            s.setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        opts = self.engine.options
        s.setValue("center", list(opts.get("center") or []))
        s.setValue("zoom", opts.get("zoom"))
        s.setValue("map_style", self.combo_style.currentText())
        for key in ("show_all", "follow", "labels", "route",
                    "hide_ground", "trails", "tcas", "alerts"):
            s.setValue(key, bool(opts.get(key)))
        s.setValue("min_alt", int(opts.get("min_alt") or 0))
        s.setValue("search", str(opts.get("search") or ""))
        s.setValue("airline_filter", str(opts.get("airline") or ""))
        s.setValue("type_filter", str(opts.get("type_filter") or ""))
        s.setValue("traffic/refresh_interval_sec",
                    float(opts.get("refresh_interval_sec", self.engine.REFRESH_SEC)))
        s.setValue("sim/ai_injection_enabled", bool(self._ai_injection_preference))
        s.sync()
        log_action("settings.saved", map_style=self.combo_style.currentText(),
                   options={key: opts.get(key) for key in (
                       "center", "zoom", "show_all", "follow", "labels", "route",
                       "hide_ground", "trails", "tcas", "alerts", "min_alt",
                       "refresh_interval_sec")})

    def _msfs_probe(self):
        """Connect to MSFS now, and retry in the background if it is not open yet."""
        log_action("msfs.connection_probe_started")
        retry_delay = MSFS_RECONNECT_INITIAL_SEC
        attempt = 0
        status_reported = False

        while not self._closing and not self._msfs_probe_stop.is_set():
            attempt += 1
            try:
                ok = self.runner.connect() is not None
            except Exception as exc:
                ok = False
                log_action("msfs.connection_probe_error", attempt=attempt,
                           error=str(exc))
            if self._closing or self._msfs_probe_stop.is_set():
                log_action("msfs.connection_probe_cancelled", attempt=attempt,
                           connected=ok)
                return
            log_action(
                "msfs.connection_probe_completed",
                connected=ok,
                attempt=attempt,
                retry_after_sec=(None if ok or not SIMCONNECT_AVAILABLE else retry_delay),
            )
            if ok:
                self.bridge.msfsStatus.emit(True)
                return

            if not status_reported:
                self.bridge.msfsStatus.emit(False)
                status_reported = True

            # A missing Python SimConnect package cannot recover during this
            # session. Otherwise keep probing: users commonly launch the map
            # before starting the simulator.
            if not SIMCONNECT_AVAILABLE:
                log_action("msfs.connection_retry_disabled",
                           reason="simconnect_package_unavailable")
                return

            if self._msfs_probe_stop.wait(retry_delay):
                log_action("msfs.connection_probe_cancelled", attempt=attempt)
                return
            retry_delay = min(retry_delay * 2.0, MSFS_RECONNECT_MAX_SEC)

    def msfs_ready(self, ok):
        log_action("msfs.status_received", connected=bool(ok))
        SOURCES.report("msfs", bool(ok), 200 if ok else None,
                       "SimConnect verbunden" if ok else "nicht verbunden")
        if ok:
            self.lbl_msfs.setText(TEXT["msfs_on"][LANG])
            self.chk_msfs.setEnabled(True)
            self.chk_msfs.setChecked(self._ai_injection_preference)
            self.runner.set_ai_injection_enabled(self._ai_injection_preference)
            if self._ai_injection_preference:
                self.runner.start()
        else:
            self.lbl_msfs.setText(TEXT["msfs_off"][LANG])

    def on_table_select(self):
        # apply_state rebuilds the model. Ignore any queued/reentrant selection
        # notification while that programmatic update is in progress.
        if self._updating_table:
            return
        rows = self.table.selectionModel().selectedRows()
        if rows:
            call = self.table.item(rows[0].row(), 0).text()
            log_action("ui.table_aircraft_selected", callsign=call,
                       changed=(call != self.engine.selected))
            if call != self.engine.selected:
                # Let Qt finish dispatching itemSelectionChanged before
                # select() re-renders the same QTableWidget model.
                QTimer.singleShot(0, lambda selected=call: self.engine.select(selected))

    def on_table_double_click(self):
        rows = self.table.selectionModel().selectedRows()
        call = self.table.item(rows[0].row(), 0).text() if rows else None
        log_action("ui.table_aircraft_double_clicked", callsign=call)
        self.chk_follow.setChecked(True)
        self.zoom_map(3, source="table_double_click")

    # ---------------- State -> UI ----------------
    def apply_state(self, st):
        """Render an engine snapshot without letting bad ADS-B data kill the UI."""
        try:
            self._apply_state(st)
        except Exception as exc:
            selected = st.get("selected") if isinstance(st, dict) else None
            aircraft = st.get("aircraft", []) if isinstance(st, dict) else []
            log_action(
                "ui.state_apply_failed",
                selected=selected,
                aircraft_count=len(aircraft) if isinstance(aircraft, (list, tuple)) else None,
                error=str(exc),
                traceback=traceback.format_exc(limit=8),
            )
            print(f"[UI] State update failed (selected={selected!r}): {exc}")

    def _apply_state(self, st):
        # Status-LEDs + Mesh-Rolle
        self._update_status_leds(st)

        # Notfall-Alarme (Toast + Ton)
        for alert in st.get("alerts", []):
            self.emergency_alert(alert)

        # Update the table as one signal-blocked operation. Rebuilding items
        # during itemSelectionChanged risks re-entering on_table_select and
        # recursively mutating Qt's model, so suppress notifications throughout.
        acs = st["aircraft"]
        previous_signals_blocked = self.table.blockSignals(True)
        was_updating_table = self._updating_table
        self._updating_table = True
        try:
            self.table.setRowCount(len(acs))
            selected_row = -1
            for r, ac in enumerate(acs):
                vals = (str(ac.get("call") or "-"), str(ac.get("type") or "-"),
                        "GND" if ac.get("ground") else _display_int(ac.get("alt"), grouped=True),
                        _display_int(ac.get("gs")))
                for c, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    if ac["emerg"]:
                        item.setForeground(QCOLOR_EMERG)
                    elif ac["ground"]:
                        item.setForeground(QCOLOR_GROUND)
                    self.table.setItem(r, c, item)
                if ac["selected"]:
                    selected_row = r
            if selected_row >= 0:
                self.table.selectRow(selected_row)
        finally:
            self._updating_table = was_updating_table
            self.table.blockSignals(previous_signals_blocked)

        # Info-Panel
        sel = st["selected"]
        if sel:
            data = self.engine.info_lines(sel)
            if data:
                self.info.setHtml(info_html(data))
        else:
            self.info.setHtml(
                f"<p style='color:{THEME['text_color']}'>{TEXT['hint_click'][LANG]}</p>")

        # Status
        if sel:
            left = f"{TEXT['status_tracking'][LANG]}: {sel}"
        else:
            left = TEXT["live_mode"][LANG]
        self.status_left.setText(left)
        c = st["counts"]
        self.status_right.setText(
            f"{c['shown']} / {c['recv']} ADS-B  |  {st['time']}")

    def closeEvent(self, event):
        self._closing = True
        self._msfs_probe_stop.set()
        self._active_global_search = None
        log_action("application.window_close_requested")
        self._save_settings()
        self.engine.stop()
        self.runner.stop()
        self.peernet.stop()
        event.accept()


# =========================
# INFO-HTML
# =========================

def _safe_float(value):
    """Convert untrusted numeric API fields without accepting NaN/Infinity."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _display_int(value, grouped=False):
    number = _safe_float(value)
    if number is None:
        return "-"
    integer = int(number)
    return f"{integer:,}".replace(",", ".") if grouped else str(integer)


def info_html(d):
    from traffic_core import cardinal
    fg = THEME["entry_fg"]
    head = THEME["text_color"]
    emerg = THEME.get("emergency_color", "#ff2d2d")
    parts = []

    def sec(title):
        parts.append(f"<h4 style='color:{head};margin:10px 0 2px 0;'>{title}</h4>")

    def kv(k, v):
        v = "–" if v in (None, "") else v
        parts.append(f"<div style='color:{fg};'><span style='color:#8b93a7;'>{k}:</span> {v}</div>")

    sec("── FLIGHT ──")
    kv("Flight", f"<b>{d['call']}</b>")
    kv("Airline", d["airline"])
    kv("ICAO Type", d["type"])
    kv("Category", d["category"])
    kv("Registration", d["reg"])
    kv("Hex", d["hex"])
    if d["ground"]:
        parts.append(f"<div style='color:{emerg};font-weight:600;'>"
                     f"{TEXT['on_ground'][LANG]}</div>")
    if d["emergency"]:
        parts.append(f"<div style='color:{emerg};font-weight:700;'>"
                     f"⚠ {TEXT['emergency'][LANG]} (Squawk {d['squawk']})</div>")

    sec("── POSITION ──")
    kv("Lat", round(d["lat"], 4) if isinstance(d["lat"], float) else d["lat"])
    kv("Lon", round(d["lon"], 4) if isinstance(d["lon"], float) else d["lon"])
    kv("Distance", f"{d['dst']:.1f} km" if isinstance(d["dst"], (int, float)) else None)
    kv("Bearing", f"{d['dir']}° ({cardinal(d['dir'])})" if d["dir"] is not None else None)

    rel = d.get("rel")
    if rel:
        sec(f"── {TEXT['tcas_title'][LANG].upper()} ──")
        kv(TEXT["clock_pos"][LANG], f"{rel['clock']} Uhr ({rel['rel_brg']:.0f}° rel)")
        kv(TEXT["rel_distance"][LANG], f"{rel['dist_km']:.1f} km / {rel['dist_nm']:.1f} nm")
        if rel.get("alt_diff_ft") is not None:
            sign = "+" if rel["alt_diff_ft"] >= 0 else "−"
            kv(TEXT["alt_diff"][LANG],
               f"{sign}{abs(rel['alt_diff_ft']):,} ft".replace(",", "."))
        trend_key = {"climbing": "climbing", "descending": "descending"}.get(rel.get("trend"))
        kv(TEXT["trend"][LANG], TEXT[trend_key or "level_off"][LANG])

    sec("── ALTITUDE / SPEED ──")
    kv("Alt (baro)", f"{d['alt_baro']} ft" if d["alt_baro"] is not None else None)
    kv("Alt (geom)", f"{d['alt_geom']} ft" if d["alt_geom"] is not None else None)
    kv("Speed", f"{d['gs']} kt" if d["gs"] is not None else None)
    kv("Track", f"{d['track']}° ({cardinal(d['track'])})" if d["track"] is not None else None)
    baro_rate = _safe_float(d.get("baro_rate"))
    if baro_rate is not None:
        arrow = "▲" if baro_rate > 200 else ("▼" if baro_rate < -200 else "→")
        kv("V/S", f"{arrow} {_display_int(baro_rate)} ft/min")

    sec("── NAV / STATUS ──")
    kv("Squawk", d["squawk"])
    kv("Nav QNH", d["nav_qnh"])
    kv("MCP Alt", f"{d['nav_alt_mcp']} ft" if d["nav_alt_mcp"] is not None else None)

    sec("── ROUTE ──")
    r = d["route"]
    if r:
        kv("From", f"{r.get('origin_icao')} ({r.get('origin_name') or '–'})")
        kv("To", f"{r.get('dest_icao')} ({r.get('dest_name') or '–'})")
    else:
        kv("Route", None)

    sec("── DATA SOURCE ──")
    kv("Seen", f"{d['seen']} s" if d["seen"] is not None else None)
    kv("RSSI", f"{d['rssi']} dB" if d["rssi"] is not None else None)
    kv("Messages", d["messages"])

    parts.append(f"<p style='color:#8b93a7;'>{TEXT['hint_click'][LANG]}</p>")
    return "".join(parts)


# =========================
# QSS (Dark Theme aus colors.json)
# =========================

def build_qss():
    t = THEME
    return f"""
    QMainWindow, QWidget {{ background: {t['main_bg']}; color: {t['text_color']};
        font-family: "Segoe UI", system-ui, sans-serif; }}
    QLabel {{ color: {t['text_color']}; }}
    QLineEdit, QSpinBox, QComboBox {{
        background: {t['entry_bg']}; color: {t['entry_fg']};
        border: 1px solid #2a3245; border-radius: 4px; padding: 3px 6px;
        selection-background-color: {t['btn_bg']}; }}
    QComboBox QAbstractItemView {{
        background: {t['entry_bg']}; color: {t['entry_fg']};
        selection-background-color: {t['btn_bg']}; border: 1px solid #2a3245; }}
    QPushButton {{
        background: {t['btn_bg']}; color: {t['btn_fg']};
        border: none; border-radius: 4px; padding: 4px 14px; font-weight: 600; }}
    QPushButton:hover {{ background: {t['accent_color']}; }}
    QPushButton:disabled {{ background: {t['btn_disabled_bg']}; color: #9aa4b2; }}
    QCheckBox {{ color: {t['text_color']}; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px; border: 1px solid #2a3245;
        border-radius: 3px; background: {t['entry_bg']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent_color']}; border-color: {t['accent_color']}; }}
    QTableWidget {{
        background: {t['entry_bg']}; alternate-background-color: {t['panel_bg']};
        color: {t['entry_fg']}; gridline-color: #232a3a; border: 1px solid #2a3245;
        border-radius: 4px; }}
    QTableWidget::item:selected {{ background: {t['btn_bg']}; color: #ffffff; }}
    QHeaderView::section {{
        background: {t['panel_bg']}; color: {t['text_color']};
        padding: 4px; border: none; font-weight: 600; }}
    QTextEdit {{
        background: {t['entry_bg']}; color: {t['entry_fg']};
        border: 1px solid #2a3245; border-radius: 4px;
        font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }}
    QStatusBar {{ background: {t['panel_bg']}; color: {t['text_color']}; }}
    QSplitter::handle {{ background: {t['panel_bg']}; width: 3px; }}
    QScrollBar:vertical {{ background: {t['panel_bg']}; width: 10px; }}
    QScrollBar::handle:vertical {{ background: #2a3245; border-radius: 4px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


QCOLOR_EMERG = QColor(THEME.get("emergency_color", "#ff5252"))
QCOLOR_GROUND = QColor("#8f9aa8")
_bootstrap_progress(0.56, "Interface bereit …", "Interface ready …")


# =========================
# MAIN
# =========================

def main(app=None, splash=None):
    log_session = start_file_logging("MSFS Live Traffic Utility")
    try:
        try:
            fault_file = getattr(log_session, "_log_file", sys.stderr)
            faulthandler.enable(file=fault_file, all_threads=True)
            log_action("application.faulthandler_enabled")
        except Exception as exc:
            log_action("application.faulthandler_unavailable", error=str(exc))
        print("Starte MSFS Live Traffic Utility (Qt)…")
        print(f"Sprache erkannt: {LANG}")
        if not SIMCONNECT_AVAILABLE:
            print("[SimConnect] nicht importierbar → reiner Kartenmodus")

        if app is None:
            app = QApplication(sys.argv)
        app.setApplicationName("MSFS Live Traffic Utility")
        app.setApplicationDisplayName("MSFS Live Traffic Utility")
        if splash is None:
            splash = StartupSplash(language=LANG)
            splash.show()
            splash.raise_()
            app.processEvents()

        settings = QSettings("LiveTraffic", "MSFSLiveTrafficUtility")
        splash.set_progress(0.58, TEXT["startup_detect_community"][LANG])
        community_selection = _choose_community_folder(
            splash, settings, defer_prompts=True
        )
        community_prompt_deferred = community_selection is None
        community_directory = community_selection or ""
        splash.set_progress(0.64, TEXT["startup_read_aircraft"][LANG])

        aircraft_catalog = None
        no_aircraft_notice_pending = False
        if community_directory:
            def catalog_progress(scanned, total, _config_path):
                fraction = scanned / total if total else 1.0
                progress = 0.64 + 0.18 * fraction
                message = TEXT["startup_read_count"][LANG].format(
                    count=scanned, total=total
                )
                splash.set_progress(progress, message)

            aircraft_catalog = AircraftTitleCatalog(
                community_directory, progress_callback=catalog_progress
            )
            log_action(
                "aircraft.catalog_loaded",
                community_directory=community_directory,
                config_title_count=len(aircraft_catalog.titles),
            )
            no_aircraft_notice_pending = not aircraft_catalog.titles
            if no_aircraft_notice_pending:
                log_action("aircraft.catalog_empty", community_directory=community_directory)

        splash.set_progress(0.82, TEXT["startup_initialize"][LANG])
        win = MainWindow(
            community_directory=community_directory,
            aircraft_catalog=aircraft_catalog,
            startup_callback=splash.set_progress,
            require_aircraft_catalog=community_prompt_deferred,
        )
        startup_notice_scheduled = False

        def scan_selected_community(path):
            """Load titles without blocking initial map display when a folder is chosen later."""
            win.community_directory = path
            win.runner.community_directory = path
            # Do not let the runner spawn against a stale catalog while a newly
            # selected Community folder is still being scanned.
            win.aircraft_catalog = None
            win.runner.aircraft_catalog = None
            log_action("aircraft.catalog_scan_started", community_directory=path,
                       deferred=True)
            win.status_left.setText(TEXT["startup_read_aircraft"][LANG])
            app.processEvents()

            def catalog_progress(scanned, total, _config_path):
                message = (
                    TEXT["startup_read_count"][LANG].format(count=scanned, total=total)
                    if total else TEXT["startup_read_aircraft"][LANG]
                )
                win.status_left.setText(message)
                app.processEvents()

            try:
                catalog = AircraftTitleCatalog(path, progress_callback=catalog_progress)
            except Exception as exc:
                log_action("aircraft.catalog_load_failed", community_directory=path,
                           error=str(exc), deferred=True)
                print(f"[Aircraft] Could not load aircraft.cfg titles: {exc}")
                win.status_left.setText(TEXT["live_mode"][LANG])
                return

            win.aircraft_catalog = catalog
            win.runner.aircraft_catalog = catalog
            log_action(
                "aircraft.catalog_loaded",
                community_directory=path,
                config_title_count=len(catalog.titles),
                deferred=True,
            )
            if not catalog.titles:
                QMessageBox.information(
                    win,
                    TEXT["community_none_title"][LANG],
                    TEXT["community_no_aircraft"][LANG],
                )
            win.status_left.setText(TEXT["live_mode"][LANG])

        def show_startup_notice():
            if community_prompt_deferred:
                selected = _choose_community_folder(win, settings)
                if selected:
                    scan_selected_community(selected)
            elif no_aircraft_notice_pending:
                QMessageBox.information(
                    win,
                    TEXT["community_none_title"][LANG],
                    TEXT["community_no_aircraft"][LANG],
                )

        def schedule_startup_notice(*_args):
            nonlocal startup_notice_scheduled
            if startup_notice_scheduled:
                return
            startup_notice_scheduled = True
            # Let Qt paint the main window and its local map before opening any
            # optional Community-folder dialogs over it.
            QTimer.singleShot(250, show_startup_notice)

        startup_finished = False
        startup_wait_timed_out = False

        def finish_startup(*_args):
            nonlocal startup_finished
            if startup_finished:
                return
            startup_finished = True
            if startup_wait_timed_out:
                splash.set_progress(0.98, TEXT["map_load_failed"][LANG])
            else:
                splash.set_progress(1.0, TEXT["startup_ready"][LANG])
            splash.close()
            win.show()
            win.raise_()
            win.activateWindow()
            app.processEvents()
            win.refresh_map_layout()
            log_action(
                "application.started",
                live_map=True,
                msfs_simconnect_available=SIMCONNECT_AVAILABLE,
                community_directory=community_directory,
                aircraft_title_count=(len(aircraft_catalog.titles)
                                      if aircraft_catalog is not None else 0),
                aircraft_catalog_required=win.require_aircraft_catalog,
                map_ready=bool(win._map_js_ready),
                map_load_success=win._map_load_success,
                map_runtime_diagnostics=win._last_map_runtime_diagnostics,
                **win._map_view_details(),
            )
            if community_prompt_deferred or no_aircraft_notice_pending:
                schedule_startup_notice()

        def finish_startup_timeout():
            nonlocal startup_wait_timed_out
            if startup_finished:
                return
            startup_wait_timed_out = True
            log_action(
                "map.startup_wait_timeout",
                javascript_ready=win._map_js_ready,
                page_load_finished=win._map_load_finished,
                page_load_success=win._map_load_success,
                map_file_exists=os.path.isfile(MAP_HTML),
                runtime_diagnostics=win._last_map_runtime_diagnostics,
            )
            if not win._map_js_ready:
                win.status_left.setText(TEXT["map_load_failed"][LANG])
                win.show_map_load_fallback()
            finish_startup()

        # Show the WebEngine widget under the always-on-top splash before
        # waiting for its page/JavaScript signals; Chromium may defer loading a
        # never-shown view, which otherwise leaves the splash waiting forever.
        win.show()
        log_action("window.shown_under_splash", map_load_finished=win._map_load_finished,
                   map_javascript_ready=win._map_js_ready,
                   **win._map_view_details())
        # Connect before starting the live engine so a fast uiReady() signal
        # cannot land between the readiness check and signal subscription.
        win.bridge.mapReady.connect(finish_startup)
        win.start_live_map()
        if win._map_js_ready:
            QTimer.singleShot(0, finish_startup)
        else:
            # A successful HTML load is not enough: the old Beta 3 log showed
            # page_load_finished=true while the JavaScript/QtWebChannel bridge
            # never became ready. Keep the splash up until uiReady(), but fail
            # open after the bounded wait rather than trapping the user there.
            QTimer.singleShot(15_000, finish_startup_timeout)
        splash.set_progress(0.98, TEXT["startup_ready"][LANG])

        exit_code = app.exec()
        log_action("application.stopped", exit_code=exit_code)
        return exit_code
    except BaseException:
        import traceback
        traceback.print_exc()
        raise
    finally:
        # Keep the tee active until interpreter shutdown so worker-thread output
        # produced while Qt is closing is included in the per-run log.
        log_session.flush()

if __name__ == "__main__":
    sys.exit(main(_bootstrap_app, _bootstrap_splash))
