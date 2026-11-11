"""Tabbed, user-facing settings editor for the Qt desktop application."""

import math
import re

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SettingsDialog(QDialog):
    """Collect validated app preferences; applying them is owned by MainWindow."""

    def __init__(self, values, map_styles, language="de", parent=None):
        super().__init__(parent)
        self._language = "de" if str(language).lower().startswith("de") else "en"
        self._values = dict(values or {})
        self._standard_values = dict(self._values.get("standard_values") or {})
        self._map_styles = dict(map_styles or {})
        self._custom_style = self._tr("Benutzerdefiniert", "Custom")
        self._widgets = {}
        self._loading_standard = False

        self.setWindowTitle(self._tr("Einstellungen", "Settings"))
        self.resize(880, 720)
        root = QVBoxLayout(self)
        profile_row = QHBoxLayout()
        self.profile_status = QLabel(self)
        self.profile_status.setStyleSheet("font-weight: 700; color: #4da3ff;")
        profile_row.addWidget(self.profile_status)
        profile_row.addStretch(1)
        self.standard_button = QPushButton(
            self._tr("Standardwerte laden", "Load defaults"), self
        )
        self.standard_button.setToolTip(self._tr(
            "Lädt die früheren App-Standardwerte. API-Key, Proxy-Zugangsdaten, Mesh-Name "
            "und lokale MSFS-Pfade bleiben erhalten.",
            "Loads the previous app defaults. API keys, proxy credentials, mesh name, "
            "and local MSFS paths are kept.",
        ))
        self.standard_button.setEnabled(bool(self._standard_values))
        self.standard_button.clicked.connect(
            lambda _checked=False: self._load_standard_values()
        )
        profile_row.addWidget(self.standard_button)
        root.addLayout(profile_row)

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs)
        self._build_connection_tab()
        self._build_map_tab()
        self._build_appearance_tab()
        self._build_filters_tab()
        self._build_simulator_tab()
        for widget in self._widgets.values():
            for signal_name in ("textChanged", "toggled", "currentTextChanged", "valueChanged"):
                signal = getattr(widget, signal_name, None)
                if signal is not None:
                    signal.connect(self._update_profile_status)
                    break
        self._update_profile_status()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _tr(self, de, en):
        return de if self._language == "de" else en

    @staticmethod
    def _rgb_hex(value):
        try:
            red, green, blue = (max(0, min(255, int(channel))) for channel in value)
            return f"#{red:02x}{green:02x}{blue:02x}"
        except (TypeError, ValueError):
            return "#000000"

    def _standard_for_key(self, key):
        if not self._standard_values:
            return None
        if key in {"planetrack_api_key", "proxy_url", "proxy_token", "peer_net_name",
                   "tile_url", "tile_attribution"}:
            return None
        if key.startswith("theme_"):
            return (self._standard_values.get("theme") or {}).get(key[len("theme_"):])
        if key.startswith("gradient_"):
            value = (self._standard_values.get("gradient") or {}).get(
                key[len("gradient_"):]
            )
            return self._rgb_hex(value) if value is not None else None
        if key.startswith("filter_"):
            return (self._standard_values.get("filters") or {}).get(key[len("filter_"):])
        if key == "community_directory":
            return ""
        if key == "simconnect_dll":
            return ""
        return self._standard_values.get(key)

    def _format_standard_value(self, key, value):
        if key in {"community_directory", "simconnect_dll"}:
            return self._tr("automatisch", "automatic")
        if isinstance(value, bool):
            return self._tr("Ja" if value else "Nein", "Yes" if value else "No")
        if value is None:
            return "—"
        if isinstance(value, float):
            formatted = f"{value:g}"
        elif isinstance(value, str):
            formatted = value or self._tr("leer", "empty")
        else:
            formatted = str(value)
        units = {
            "planetrack_radius_nm": "NM",
            "center_lat": "°",
            "center_lon": "°",
            "min_alt": "ft",
            "refresh_interval_sec": "s",
            "icon_size": "px",
            "selected_icon_size": "px",
            "spawn_radius_km": "km",
        }
        unit = units.get(key)
        return f"{formatted} {unit}" if unit else formatted

    def _same_value(self, key, current, expected):
        if key == "min_alt":
            current = current or 0
        if isinstance(current, str) and isinstance(expected, str):
            return current.strip().lower() == expected.strip().lower()
        if isinstance(current, (int, float)) and not isinstance(current, bool) \
                and isinstance(expected, (int, float)) and not isinstance(expected, bool):
            return math.isclose(float(current), float(expected), rel_tol=0.0, abs_tol=1e-6)
        return current == expected

    def _field_label(self, label, key, _current=None):
        expected = self._standard_for_key(key)
        if expected is None:
            return label
        marker = self._tr("Standard", "Standard")
        value = self._format_standard_value(key, expected)
        return f"{label}  [{marker}: {value}]"

    def _profile_matches_standard(self):
        current = self.values()
        keys = (
            "planetrack_radius_nm", "proxy_enabled", "peer_net_enabled",
            "map_style", "tile_max_zoom", "center_lat", "center_lon", "zoom", "icon_size",
            "selected_icon_size", "min_alt", "refresh_interval_sec", "airline_filter",
            "type_filter", "ai_injection_enabled", "max_active_ai", "spawn_radius_km",
            "parked_fillers",
        )
        for key in keys:
            expected = self._standard_values.get(key)
            if expected is not None and not self._same_value(key, current.get(key), expected):
                return False
        for key in ("tile_url", "tile_attribution"):
            expected = self._standard_values.get(key)
            if expected is not None and not self._same_value(key, current.get(key), expected):
                return False
        for group in ("theme", "filters"):
            expected_group = self._standard_values.get(group) or {}
            current_group = current.get(group) or {}
            for key, expected in expected_group.items():
                if not self._same_value(key, current_group.get(key), expected):
                    return False
        standard_gradient = self._standard_values.get("gradient") or {}
        current_gradient = current.get("gradient_hex") or {}
        for key, expected in standard_gradient.items():
            if not self._same_value(
                    key, current_gradient.get(key), self._rgb_hex(expected)):
                return False
        return True

    def _update_profile_status(self, *_args):
        if self._loading_standard:
            return
        if not self._standard_values:
            self.profile_status.setText("")
            return
        text = (self._tr("Profil: Standard", "Profile: Standard")
                if self._profile_matches_standard()
                else self._tr("Profil: Benutzerdefiniert", "Profile: Custom"))
        self.profile_status.setText(text)

    def _load_standard_values(self):
        if not self._standard_values:
            return
        values = self._standard_values
        self._loading_standard = True
        try:
            self._widgets["planetrack_radius_nm"].setValue(
                int(values.get("planetrack_radius_nm", 40))
            )
            for key in ("proxy_enabled", "peer_net_enabled"):
                if key in values:
                    self._widgets[key].setChecked(bool(values[key]))
            self.map_style.setCurrentText(values.get("map_style", self._custom_style))
            for key in ("tile_url", "tile_attribution"):
                if key in values:
                    self._widgets[key].setText(str(values[key]))
            if "tile_max_zoom" in values:
                self._widgets["tile_max_zoom"].setValue(int(values["tile_max_zoom"]))
            for key in ("center_lat", "center_lon", "refresh_interval_sec"):
                if key in values:
                    self._widgets[key].setValue(float(values[key]))
            for key in ("zoom", "min_alt", "icon_size", "selected_icon_size",
                        "max_active_ai", "spawn_radius_km", "parked_fillers"):
                if key in values:
                    self._widgets[key].setValue(int(values[key]))
            for key, color in (values.get("theme") or {}).items():
                widget = self._widgets.get(f"theme_{key}")
                if widget is not None:
                    widget.setText(str(color))
            for key, rgb in (values.get("gradient") or {}).items():
                widget = self._widgets.get(f"gradient_{key}")
                if widget is not None:
                    widget.setText(self._rgb_hex(rgb))
            for key, checked in (values.get("filters") or {}).items():
                widget = self._widgets.get(f"filter_{key}")
                if widget is not None:
                    widget.setChecked(bool(checked))
            self._widgets["airline_filter"].setText(str(values.get("airline_filter", "")))
            self._widgets["type_filter"].setText(str(values.get("type_filter", "")))
            if "ai_injection_enabled" in values:
                self._widgets["ai_injection_enabled"].setChecked(
                    bool(values["ai_injection_enabled"])
                )
        finally:
            self._loading_standard = False
        self._update_profile_status()

    def _form_tab(self, title):
        page = QWidget(self)
        form = QFormLayout(page)
        self.tabs.addTab(page, title)
        return form

    def _line(self, form, key, label, value="", password=False):
        edit = QLineEdit(str(value or ""), self)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._widgets[key] = edit
        form.addRow(self._field_label(label, key, value), edit)
        return edit

    def _check(self, form, key, label, checked=False):
        widget = QCheckBox(self._field_label(label, key, bool(checked)), self)
        widget.setChecked(bool(checked))
        self._widgets[key] = widget
        form.addRow(widget)
        return widget

    def _spin(self, form, key, label, value, minimum, maximum, step=1, suffix=""):
        widget = QSpinBox(self)
        widget.setRange(minimum, maximum)
        widget.setSingleStep(step)
        if suffix:
            widget.setSuffix(suffix)
        try:
            widget.setValue(int(float(value)))
        except (TypeError, ValueError, OverflowError):
            widget.setValue(minimum)
        self._widgets[key] = widget
        form.addRow(self._field_label(label, key, widget.value()), widget)
        return widget

    def _double(self, form, key, label, value, minimum, maximum, decimals=2, step=0.5,
                suffix=""):
        widget = QDoubleSpinBox(self)
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setSingleStep(step)
        if suffix:
            widget.setSuffix(suffix)
        try:
            widget.setValue(float(value))
        except (TypeError, ValueError, OverflowError):
            widget.setValue(minimum)
        self._widgets[key] = widget
        form.addRow(self._field_label(label, key, widget.value()), widget)
        return widget

    def _color(self, form, key, label, value):
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(str(value or "#000000"), row)
        button = QPushButton(self._tr("Farbe …", "Choose color …"), row)
        button.clicked.connect(lambda _checked=False, target=edit: self._choose_color(target))
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        self._widgets[key] = edit
        form.addRow(self._field_label(label, key, edit.text()), row)
        return edit

    def _choose_color(self, edit):
        try:
            initial = QColor(edit.text())
            picked = QColorDialog.getColor(initial, self)
            if picked.isValid():
                edit.setText(picked.name())
        except Exception:
            # The hex field remains available if a platform has no native picker.
            return

    def _build_connection_tab(self):
        form = self._form_tab(self._tr("Daten & Netzwerk", "Data & network"))

        self._line(
            form, "planetrack_api_key",
            self._tr("PlaneTrack.ai-Key (lokaler Direkt-Fallback)",
                     "PlaneTrack.ai key (local direct fallback)"),
            self._values.get("planetrack_api_key", ""), password=True,
        )
        self._spin(
            form, "planetrack_radius_nm",
            self._tr("Max. Suchradius für PlaneTrack.ai", "PlaneTrack.ai maximum search radius"),
            self._values.get("planetrack_radius_nm", 40), 1, 250,
            suffix=" NM",
        )
        note = QLabel(self._tr(
            "Die Proxy-Quelle PlaneTrack.ai wird ausschließlich auf dem Collector konfiguriert "
            "(lokale planetrack_credentials.json); dieser Schlüssel wird weder an Clients noch "
            "in den öffentlichen Status übertragen. Der hier gespeicherte Schlüssel gilt nur "
            "für direkte lokale Abfragen, wenn Proxy und Mesh ausfallen. Automatische Reihenfolge: "
            "adsb.lol → adsb.fi → OpenSky → PlaneTrack.ai. Der lokale Schlüssel bleibt in "
            "QSettings und wird nicht in colors.json gespeichert.",
            "The proxy's PlaneTrack.ai source is configured only on the collector "
            "(local planetrack_credentials.json); its key is never sent to clients or exposed "
            "in public status. The key entered here is only for this app's direct fallback "
            "when both proxy and mesh are unavailable. Automatic order: adsb.lol → adsb.fi "
            "→ OpenSky → PlaneTrack.ai. The local key stays in QSettings and is not written "
            "to colors.json.",
        ), self)
        note.setWordWrap(True)
        form.addRow(note)

        self._check(form, "proxy_enabled",
                    self._tr("Web-Proxy verwenden", "Use web proxy"),
                    self._values.get("proxy_enabled", True))
        self._line(form, "proxy_url", self._tr("Proxy-URL", "Proxy URL"),
                   self._values.get("proxy_url", ""))
        self._line(form, "proxy_token", self._tr("Proxy-Token", "Proxy token"),
                   self._values.get("proxy_token", ""), password=True)
        self._check(form, "peer_net_enabled",
                    self._tr("Peer-Mesh / Ersatz-Proxy verwenden", "Use peer mesh / relay"),
                    self._values.get("peer_net_enabled", True))
        self._line(form, "peer_net_name", self._tr("Name im Mesh", "Mesh display name"),
                   self._values.get("peer_net_name", ""))

    def _build_map_tab(self):
        form = self._form_tab(self._tr("Karte", "Map"))
        self.map_style = QComboBox(self)
        style_names = list(self._map_styles)
        if self._custom_style not in style_names:
            style_names.append(self._custom_style)
        self.map_style.addItems(style_names)
        current_style = str(self._values.get("map_style") or "")
        if current_style not in style_names:
            current_style = self._custom_style
        self.map_style.setCurrentText(current_style)
        self._widgets["map_style"] = self.map_style
        form.addRow(self._field_label(
            self._tr("Kartenstil", "Map style"), "map_style", current_style,
        ), self.map_style)

        self._line(form, "tile_url", self._tr("Tile-URL", "Tile URL"),
                   self._values.get("tile_url", ""))
        self._line(form, "tile_attribution", self._tr("Karten-Attribution", "Map attribution"),
                   self._values.get("tile_attribution", ""))
        self._spin(form, "tile_max_zoom", self._tr("Maximaler Zoom", "Maximum zoom"),
                   self._values.get("tile_max_zoom", 19), 1, 22)
        self.map_style.currentTextChanged.connect(self._map_style_changed)
        self._map_style_changed(current_style)

        self._double(form, "center_lat", self._tr("Start-/Live-Zentrum Breitengrad", "Map center latitude"),
                     self._values.get("center_lat", 50.033), -90.0, 90.0,
                     decimals=6, step=0.01)
        self._double(form, "center_lon", self._tr("Start-/Live-Zentrum Längengrad", "Map center longitude"),
                     self._values.get("center_lon", 8.570), -180.0, 180.0,
                     decimals=6, step=0.01)
        self._spin(form, "zoom", self._tr("Zoomstufe", "Zoom level"),
                   self._values.get("zoom", 7), 2, 20)

    def _map_style_changed(self, style):
        entry = self._map_styles.get(style)
        if entry:
            url, max_zoom, attribution = entry
            self._widgets["tile_url"].setText(url)
            self._widgets["tile_max_zoom"].setValue(max_zoom)
            self._widgets["tile_attribution"].setText(attribution)

    def _build_appearance_tab(self):
        form = self._form_tab(self._tr("Darstellung", "Appearance"))
        values = self._values.get("theme") or {}
        labels = {
            "main_bg": self._tr("Fenster-Hintergrund", "Window background"),
            "panel_bg": self._tr("Panel-Hintergrund", "Panel background"),
            "text_color": self._tr("Textfarbe", "Text color"),
            "entry_bg": self._tr("Eingabefeld-Hintergrund", "Input background"),
            "entry_fg": self._tr("Eingabefeld-Text", "Input text"),
            "btn_bg": self._tr("Button-Hintergrund", "Button background"),
            "btn_fg": self._tr("Button-Text", "Button text"),
            "btn_disabled_bg": self._tr("Deaktivierter Button", "Disabled button"),
            "label_color": self._tr("Karten-Label", "Map labels"),
            "emergency_color": self._tr("Notfall-Farbe", "Emergency color"),
            "accent_color": self._tr("Akzentfarbe", "Accent color"),
        }
        defaults = {
            "main_bg": "#1a1a2e", "panel_bg": "#16213e", "text_color": "#f2f4fa",
            "entry_bg": "#0f3460", "entry_fg": "#ffffff", "btn_bg": "#e94560",
            "btn_fg": "#ffffff", "btn_disabled_bg": "#53354a", "label_color": "#f2f4fa",
            "emergency_color": "#ff2d2d", "accent_color": "#4da3ff",
        }
        for key, label in labels.items():
            self._color(form, f"theme_{key}", label, values.get(key, defaults[key]))

        gradient = self._values.get("gradient") or {}
        for key, label in (
            ("low", self._tr("Gradient: niedrige Höhe", "Gradient: low altitude")),
            ("mid", self._tr("Gradient: mittlere Höhe", "Gradient: mid altitude")),
            ("high", self._tr("Gradient: große Höhe", "Gradient: high altitude")),
        ):
            rgb = gradient.get(key, (0, 140, 255) if key == "low"
                                else (255, 215, 0) if key == "mid"
                                else (255, 40, 40))
            try:
                hex_color = "#%02x%02x%02x" % tuple(int(c) for c in rgb)
            except (TypeError, ValueError):
                hex_color = "#000000"
            self._color(form, f"gradient_{key}", label, hex_color)

        self._spin(form, "icon_size", self._tr("Flugzeug-Icon-Größe", "Aircraft icon size"),
                   self._values.get("icon_size", 28), 12, 64, suffix=" px")
        self._spin(form, "selected_icon_size",
                   self._tr("Ausgewähltes Icon", "Selected aircraft icon"),
                   self._values.get("selected_icon_size", 34), 16, 80, suffix=" px")

    def _build_filters_tab(self):
        form = self._form_tab(self._tr("Verkehr & Filter", "Traffic & filters"))
        filters = self._values.get("filters") or {}
        labels = {
            "show_all": self._tr("Alle Flugzeuge anzeigen", "Show all aircraft"),
            "follow": self._tr("Ausgewähltem Flugzeug folgen", "Follow selected aircraft"),
            "labels": self._tr("Kartenlabels anzeigen", "Show map labels"),
            "route": self._tr("Route anzeigen", "Show route"),
            "hide_ground": self._tr("Bodenverkehr ausblenden", "Hide ground traffic"),
            "trails": self._tr("Spuren anzeigen", "Show trails"),
            "tcas": self._tr("TCAS / MSFS-Spielerposition verwenden", "Use TCAS / MSFS player position"),
            "alerts": self._tr("Notfallalarme aktivieren", "Enable emergency alerts"),
        }
        defaults = {"show_all": True, "follow": True, "labels": True, "route": True,
                    "hide_ground": False, "trails": False, "tcas": False, "alerts": True}
        for key, label in labels.items():
            self._check(form, f"filter_{key}", label, filters.get(key, defaults[key]))
        self._spin(form, "min_alt", self._tr("Mindesthöhe", "Minimum altitude"),
                   self._values.get("min_alt", 0), 0, 45000, step=500, suffix=" ft")
        self._double(form, "refresh_interval_sec",
                     self._tr("Abfrageintervall", "Polling interval"),
                     self._values.get("refresh_interval_sec", 2.0), 1.0, 30.0,
                     decimals=1, step=0.5, suffix=" s")
        self._line(form, "airline_filter", self._tr("Airline-Filter", "Airline filter"),
                   self._values.get("airline_filter", ""))
        self._line(form, "type_filter", self._tr("Flugzeugtyp-Filter", "Aircraft type filter"),
                   self._values.get("type_filter", ""))

    def _build_simulator_tab(self):
        form = self._form_tab(self._tr("Simulator", "Simulator"))
        community_row = QWidget(self)
        community_layout = QHBoxLayout(community_row)
        community_layout.setContentsMargins(0, 0, 0, 0)
        community = QLineEdit(str(self._values.get("community_directory") or ""), community_row)
        browse = QPushButton(self._tr("Durchsuchen …", "Browse …"), community_row)
        browse.clicked.connect(lambda _checked=False: self._browse_directory(community))
        community_layout.addWidget(community, 1)
        community_layout.addWidget(browse)
        self._widgets["community_directory"] = community
        form.addRow(self._field_label(
            self._tr("MSFS-Community-Ordner", "MSFS Community folder"),
            "community_directory", community.text(),
        ), community_row)

        self._check(form, "ai_injection_enabled",
                    self._tr("AI-Verkehr in MSFS injizieren", "Inject AI traffic into MSFS"),
                    self._values.get("ai_injection_enabled", True))
        self._spin(form, "max_active_ai", self._tr("Maximale Live-AI-Flugzeuge", "Maximum live AI aircraft"),
                   self._values.get("max_active_ai", 25), 0, 200)
        self._spin(form, "spawn_radius_km", self._tr("AI-Radius um das eigene Flugzeug", "AI radius around player"),
                   self._values.get("spawn_radius_km", 35), 5, 80, suffix=" km")
        self._spin(form, "parked_fillers", self._tr("Geparkte Füllflugzeuge, maximal", "Maximum parked filler aircraft"),
                   self._values.get("parked_fillers", 50), 0, 200)

        dll_row = QWidget(self)
        dll_layout = QHBoxLayout(dll_row)
        dll_layout.setContentsMargins(0, 0, 0, 0)
        dll = QLineEdit(str(self._values.get("simconnect_dll") or ""), dll_row)
        dll_browse = QPushButton(self._tr("DLL …", "Browse DLL …"), dll_row)
        dll_browse.clicked.connect(lambda _checked=False: self._browse_file(dll))
        dll_layout.addWidget(dll, 1)
        dll_layout.addWidget(dll_browse)
        self._widgets["simconnect_dll"] = dll
        form.addRow(self._field_label(
            self._tr("SimConnect-DLL (optional)", "SimConnect DLL (optional)"),
            "simconnect_dll", dll.text(),
        ), dll_row)
        note = QLabel(self._tr(
            "Änderungen am Community-Ordner oder an der SimConnect-DLL benötigen einen Neustart. "
            "Andere Werte gelten sofort für neue Abfragen/AI-Spawns; bereits erzeugte "
            "Parkflugzeuge werden durch ein niedrigeres Limit nicht entfernt.",
            "Other values apply immediately to new polls/AI spawns; lowering the parked-aircraft "
            "limit does not remove aircraft already spawned.",
        ), self)
        note.setWordWrap(True)
        form.addRow(note)

    def _browse_directory(self, edit):
        selected = QFileDialog.getExistingDirectory(
            self, self._tr("Ordner auswählen", "Choose folder"), edit.text()
        )
        if selected:
            edit.setText(selected)

    def _browse_file(self, edit):
        selected, _filter = QFileDialog.getOpenFileName(
            self, self._tr("SimConnect-DLL auswählen", "Choose SimConnect DLL"),
            edit.text(), self._tr("DLL-Dateien (*.dll);;Alle Dateien (*.*)",
                                  "DLL files (*.dll);;All files (*.*)"),
        )
        if selected:
            edit.setText(selected)

    def _accept_if_valid(self):
        values = self.values()
        bad_color = next((key for key, value in values["theme"].items()
                          if not _COLOR_RE.fullmatch(value)), None)
        if bad_color is None:
            bad_color = next((key for key, value in values["gradient_hex"].items()
                              if not _COLOR_RE.fullmatch(value)), None)
        custom = values["map_style"] == self._custom_style
        if bad_color is not None:
            QMessageBox.warning(
                self, self._tr("Ungültige Farbe", "Invalid color"),
                self._tr("Bitte verwende das Format #RRGGBB.",
                         "Enter colors in #RRGGBB format."),
            )
            return
        if custom and (not values["tile_url"].startswith(("https://", "http://"))
                       or not all(token in values["tile_url"] for token in ("{z}", "{x}", "{y}"))
                       or not values["tile_attribution"].strip()):
            QMessageBox.warning(
                self, self._tr("Kartenquelle prüfen", "Check map source"),
                self._tr("Für einen eigenen Tile-Server sind eine HTTP(S)-URL mit {z}, {x}, {y} "
                         "und eine Quellenangabe erforderlich.",
                         "A custom tile server needs an HTTP(S) URL with {z}, {x}, {y} "
                         "and attribution."),
            )
            return
        super().accept()

    def values(self):
        filters = {
            key: self._widgets[f"filter_{key}"].isChecked()
            for key in ("show_all", "follow", "labels", "route", "hide_ground",
                        "trails", "tcas", "alerts")
        }
        theme = {
            key: self._widgets[f"theme_{key}"].text().strip()
            for key in ("main_bg", "panel_bg", "text_color", "entry_bg", "entry_fg",
                        "btn_bg", "btn_fg", "btn_disabled_bg", "label_color",
                        "emergency_color", "accent_color")
        }
        gradient_hex = {
            key: self._widgets[f"gradient_{key}"].text().strip()
            for key in ("low", "mid", "high")
        }
        return {
            "planetrack_api_key": self._widgets["planetrack_api_key"].text().strip(),
            "planetrack_radius_nm": self._widgets["planetrack_radius_nm"].value(),
            "proxy_enabled": self._widgets["proxy_enabled"].isChecked(),
            "proxy_url": self._widgets["proxy_url"].text().strip(),
            "proxy_token": self._widgets["proxy_token"].text().strip(),
            "peer_net_enabled": self._widgets["peer_net_enabled"].isChecked(),
            "peer_net_name": self._widgets["peer_net_name"].text().strip(),
            "map_style": self.map_style.currentText(),
            "tile_url": self._widgets["tile_url"].text().strip(),
            "tile_attribution": self._widgets["tile_attribution"].text().strip(),
            "tile_max_zoom": self._widgets["tile_max_zoom"].value(),
            "center_lat": self._widgets["center_lat"].value(),
            "center_lon": self._widgets["center_lon"].value(),
            "zoom": self._widgets["zoom"].value(),
            "theme": theme,
            "gradient_hex": gradient_hex,
            "icon_size": self._widgets["icon_size"].value(),
            "selected_icon_size": self._widgets["selected_icon_size"].value(),
            "filters": filters,
            "min_alt": self._widgets["min_alt"].value() or None,
            "refresh_interval_sec": self._widgets["refresh_interval_sec"].value(),
            "airline_filter": self._widgets["airline_filter"].text().strip(),
            "type_filter": self._widgets["type_filter"].text().strip(),
            "community_directory": self._widgets["community_directory"].text().strip(),
            "ai_injection_enabled": self._widgets["ai_injection_enabled"].isChecked(),
            "max_active_ai": self._widgets["max_active_ai"].value(),
            "spawn_radius_km": self._widgets["spawn_radius_km"].value(),
            "parked_fillers": self._widgets["parked_fillers"].value(),
            "simconnect_dll": self._widgets["simconnect_dll"].text().strip(),
        }
