"""
Mock-Test der Qt-GUI-Schicht (main.py) OHNE Qt/libGL/Display:

    python tests/mock_qt_test.py

Ersetzt PySide6 durch minimale Fake-Module (Signale, Widgets, Tabelle,
WebEngine-Stub) und prüft das Wiring: State -> Tabelle/Info/Status,
Bridge-JSON für map.js, Aktionen (Start/Stop, Filter, Stil, MSFS-Toggle).
"""

import json
import os
import sys
import tempfile
import threading
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ["LTU_MESH"] = "off"   # Peer-Mesh in Tests deaktiviert (kein Thread/HTTP)

import traffic_core as _tc       # noqa: E402  (UI-frei, vor den PySide6-Fakes sicher)
_tc._page_txt_url = lambda: ""   # lokales page.txt darf Test-URLs nicht ueberschreiben


# =========================================================
# Mini-Signal-Implementierung
# =========================================================
class BoundSignal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *args):
        for s in list(self.slots):
            s(*args)


class Signal:
    def __init__(self, *types):
        self.types = types

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.setdefault("_sig_" + self.name, BoundSignal())


def Slot(*types):
    def deco(fn):
        return fn
    return deco


class QObject:
    def __init__(self, parent=None):
        self.parent = parent


# =========================================================
# Fake-Widgets
# =========================================================
class Widget:
    def __init__(self, *a, **kw):
        self.enabled = True
        self.stylesheet = ""
        self.visible = False

    def setEnabled(self, v):
        self.enabled = bool(v)

    def isEnabled(self):
        return self.enabled

    def setStyleSheet(self, s):
        self.stylesheet = s

    def setToolTip(self, t):
        self.tooltip = t

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def raise_(self):
        pass

    def isVisible(self):
        return self.visible

    def width(self):
        return getattr(self, "_width", 0)

    def height(self):
        return getattr(self, "_height", 0)

    def move(self, *a):
        pass

    def adjustSize(self):
        pass

    def deleteLater(self):
        pass

    def setFixedWidth(self, w):
        pass

    def setMinimumWidth(self, w):
        pass

    def setMinimumHeight(self, h):
        pass

    def update(self):
        self.updated = True

    def setMaximumWidth(self, w):
        pass

    def resize(self, *a):
        if len(a) >= 2:
            self._width, self._height = a[0], a[1]


class QLabel(Widget):
    def __init__(self, text="", parent=None, **kw):
        super().__init__()
        self._text = text
        self.parent = parent

    def setAlignment(self, alignment):
        self.alignment = alignment

    def setWordWrap(self, enabled):
        self.word_wrap = bool(enabled)

    def setMargin(self, margin):
        self.margin = margin

    def setObjectName(self, name):
        self.object_name = name

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text


class QLineEdit(Widget):
    class EchoMode:
        Normal = 0
        Password = 1

    def __init__(self, text="", parent=None, **kw):
        super().__init__()
        self.parent = parent
        self._text = text
        self.returnPressed = BoundSignal()
        self.textChanged = BoundSignal()
        self.placeholder = ""
        self.echo_mode = self.EchoMode.Normal

    def text(self):
        return self._text

    def setText(self, t):
        if t != self._text:
            self._text = t
            self.textChanged.emit(t)

    def setPlaceholderText(self, t):
        self.placeholder = t

    def setEchoMode(self, mode):
        self.echo_mode = mode


class QPushButton(Widget):
    def __init__(self, text="", parent=None, **kw):
        super().__init__()
        self.parent = parent
        self._text = text
        self.clicked = BoundSignal()

    def text(self):
        return self._text


class QCheckBox(Widget):
    def __init__(self, text="", parent=None, **kw):
        super().__init__()
        self.parent = parent
        self._text = text
        self._checked = False
        self.toggled = BoundSignal()

    def setChecked(self, v):
        v = bool(v)
        if v != self._checked:
            self._checked = v
            self.toggled.emit(v)

    def isChecked(self):
        return self._checked


class QComboBox(Widget):
    def __init__(self, parent=None, **kw):
        super().__init__()
        self.parent = parent
        self.items = []
        self._current = ""
        self.currentTextChanged = BoundSignal()

    def addItems(self, items):
        self.items.extend(items)
        if not self._current and self.items:
            self._current = self.items[0]

    def currentText(self):
        return self._current

    def setCurrentText(self, t):
        self._current = t
        self.currentTextChanged.emit(t)


class QSpinBox(Widget):
    def __init__(self, parent=None, **kw):
        super().__init__()
        self.parent = parent
        self._value = 0
        self.valueChanged = BoundSignal()

    def setRange(self, a, b):
        pass

    def setSingleStep(self, s):
        pass

    def setSpecialValueText(self, s):
        pass

    def setSuffix(self, suffix):
        self.suffix = suffix

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = v
        self.valueChanged.emit(v)


class QDoubleSpinBox(QSpinBox):
    def setDecimals(self, decimals):
        self.decimals = decimals

    def setSingleStep(self, step):
        self.step = step


class QFormLayout:
    def __init__(self, *a, **kw):
        self.items = []

    def addRow(self, *items):
        self.items.append(items)


class QTabWidget(Widget):
    def __init__(self, parent=None, **kw):
        super().__init__()
        self.parent = parent
        self.tabs = []

    def addTab(self, page, title):
        self.tabs.append((page, title))


class QDialog(Widget):
    class DialogCode:
        Rejected = 0
        Accepted = 1

    def __init__(self, parent=None, **kw):
        super().__init__(parent)
        self.parent = parent
        self._accepted = False
        self._title = ""

    def setWindowTitle(self, title):
        self._title = title

    def accept(self):
        self._accepted = True

    def reject(self):
        self._accepted = False

    def exec(self):
        return self.DialogCode.Accepted if self._accepted else self.DialogCode.Rejected


class QDialogButtonBox(Widget):
    class StandardButton:
        Ok = 1
        Cancel = 2

    def __init__(self, buttons=0, parent=None, **kw):
        super().__init__()
        self.buttons = buttons
        self.parent = parent
        self.accepted = BoundSignal()
        self.rejected = BoundSignal()


class QColorDialog:
    @staticmethod
    def getColor(initial, parent=None):
        return initial


class QTableWidgetItem:
    def __init__(self, text=""):
        self._text = text
        self.fg = None

    def text(self):
        return self._text

    def setForeground(self, color):
        self.fg = color


class _Header:
    def setSectionResizeMode(self, *a):
        pass

    def setVisible(self, v):
        pass

    def setDefaultSectionSize(self, s):
        pass


class _SelRow:
    def __init__(self, row):
        self._row = row

    def row(self):
        return self._row


class _SelectionModel:
    def __init__(self, table):
        self.table = table

    def selectedRows(self):
        if self.table._sel >= 0:
            return [_SelRow(self.table._sel)]
        return []


class QTableWidget(Widget):
    def __init__(self, rows=0, cols=0, **kw):
        super().__init__()
        self.cols = cols
        self.items = {}
        self.row_count = rows
        self._sel = -1
        self.headers = []
        self._signals_blocked = False
        self.mutation_signal_states = []
        self.itemSelectionChanged = BoundSignal()
        self.doubleClicked = BoundSignal()

    def setRowCount(self, n):
        self.mutation_signal_states.append(self._signals_blocked)
        self.row_count = n
        for (r, c) in list(self.items):
            if r >= n:
                del self.items[(r, c)]

    def rowCount(self):
        return self.row_count

    def setItem(self, r, c, item):
        self.mutation_signal_states.append(self._signals_blocked)
        self.items[(r, c)] = item

    def item(self, r, c):
        return self.items.get((r, c))

    def setHorizontalHeaderLabels(self, labels):
        self.headers = labels

    def horizontalHeader(self):
        return _Header()

    def verticalHeader(self):
        return _Header()

    def setEditTriggers(self, *a):
        pass

    def setSelectionBehavior(self, *a):
        pass

    def setSelectionMode(self, *a):
        pass

    def setAlternatingRowColors(self, *a):
        pass

    def selectionModel(self):
        return _SelectionModel(self)

    def selectRow(self, r):
        self.mutation_signal_states.append(self._signals_blocked)
        self._sel = r

    def blockSignals(self, v):
        previous = self._signals_blocked
        self._signals_blocked = bool(v)
        return previous


class QTextEdit(Widget):
    def __init__(self, **kw):
        super().__init__()
        self.html = ""

    def setReadOnly(self, v):
        pass

    def setHtml(self, h):
        self.html = h


class QSplitter(Widget):
    def __init__(self, *a, **kw):
        super().__init__()
        self.children = []

    def addWidget(self, w):
        self.children.append(w)

    def setStretchFactor(self, *a):
        pass


class _Layout:
    def __init__(self, *a, **kw):
        self.items = []

    def addWidget(self, w, *a, **kw):
        self.items.append(w)

    def addLayout(self, l, *a, **kw):
        self.items.append(l)

    def addStretch(self, *a):
        pass

    def setContentsMargins(self, *a):
        pass

    def setSpacing(self, s):
        pass


class QVBoxLayout(_Layout):
    pass


class QHBoxLayout(_Layout):
    pass


class QStackedLayout(_Layout):
    class StackingMode:
        StackAll = 1

    def setStackingMode(self, mode):
        self.stacking_mode = mode

    def setCurrentWidget(self, widget):
        self.current_widget = widget


class _StatusBar:
    def __init__(self):
        self.widgets = []

    def addWidget(self, w, *a):
        self.widgets.append(w)

    def addPermanentWidget(self, w, *a):
        self.widgets.append(w)


class QWidget(Widget):
    def __init__(self, *a, **kw):
        super().__init__()
        self.layout = None

    def setCentralWidget(self, w):
        self.central = w


class QMainWindow(QWidget):
    def __init__(self, *a, **kw):
        super().__init__()
        self._title = ""
        self._status = _StatusBar()
        self._geo = None

    def setWindowTitle(self, t):
        self._title = t

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def saveGeometry(self):
        return self._geo or b"fake-geometry"

    def restoreGeometry(self, g):
        self._geo = g
        return True

    def resize(self, *a):
        if len(a) >= 2:
            self._width, self._height = a[0], a[1]

    def showEvent(self, event):
        pass

    def statusBar(self):
        return self._status

    def closeEvent(self, e):
        pass


class QApplication:
    beep_count = 0

    def __init__(self, argv=None):
        pass

    def setApplicationName(self, n):
        pass

    def setApplicationDisplayName(self, n):
        pass

    @staticmethod
    def beep():
        QApplication.beep_count += 1

    @staticmethod
    def processEvents():
        pass

    def exec(self):
        return 0


class QFileDialog:
    """Fake: static dialog helpers return deterministic, empty selections."""
    result = ("", "")
    directory_result = ""

    @staticmethod
    def getSaveFileName(*a, **kw):
        return QFileDialog.result

    @staticmethod
    def getExistingDirectory(*a, **kw):
        return QFileDialog.directory_result

    @staticmethod
    def getOpenFileName(*a, **kw):
        return QFileDialog.result


class QInputDialog:
    result = ("", False)

    @staticmethod
    def getItem(*a, **kw):
        return QInputDialog.result


class QMessageBox:
    messages = []

    @staticmethod
    def information(parent, title, text):
        QMessageBox.messages.append(("info", title, text))

    @staticmethod
    def warning(parent, title, text):
        QMessageBox.messages.append(("warning", title, text))


class QSettings:
    """Fake mit klassenweitem Store (wie echte QSettings pro App)."""
    store = {}

    def __init__(self, *a, **kw):
        pass

    def setValue(self, key, value):
        QSettings.store[key] = value

    def value(self, key, default=None):
        return QSettings.store.get(key, default)

    def sync(self):
        pass


SHORTCUTS = []


class QShortcut:
    def __init__(self, seq, parent=None, **kw):
        self.activated = BoundSignal()
        self.seq = seq
        SHORTCUTS.append(self)


class QKeySequence:
    def __init__(self, key):
        self.key = key


class QColor:
    def __init__(self, name=""):
        self._name = str(name)

    def name(self):
        return self._name.lower()

    def isValid(self):
        return (len(self._name) == 7 and self._name.startswith("#")
                and all(char in "0123456789abcdefABCDEF" for char in self._name[1:]))


class QPen:
    def __init__(self, *args, **kwargs):
        pass


class QPainter:
    class RenderHint:
        Antialiasing = 1


class Qt:
    Horizontal = 1
    Vertical = 2
    AlignCenter = 0x84
    Key_Escape = 0x1000000
    Key_Plus = 0x1000021
    Key_Minus = 0x1000022


class QUrl:
    def __init__(self, s=""):
        self.s = s

    @classmethod
    def fromLocalFile(cls, path):
        return cls(path)

    def setQuery(self, query):
        self.s += "?" + str(query)

    def setFragment(self, fragment):
        self.s += "#" + str(fragment)


class _WebPage:
    def __init__(self):
        self.channel = None
        self.js_log = []

    def setWebChannel(self, ch):
        self.channel = ch

    def runJavaScript(self, code, cb=None):
        self.js_log.append(code)
        if cb:
            cb(None)


class QWebEngineSettings:
    class WebAttribute:
        LocalContentCanAccessFileUrls = "LocalContentCanAccessFileUrls"
        LocalContentCanAccessRemoteUrls = "LocalContentCanAccessRemoteUrls"


class QWebEnginePage(_WebPage):
    class JavaScriptConsoleMessageLevel:
        InfoMessageLevel = 0
        WarningMessageLevel = 1
        ErrorMessageLevel = 2

    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent


class _WebSettings:
    def __init__(self):
        self.attributes = {}

    def setAttribute(self, name, value):
        self.attributes[name] = bool(value)


class QWebEngineView(Widget):
    def __init__(self, **kw):
        super().__init__()
        self._page = _WebPage()
        self._settings = _WebSettings()
        self.loadFinished = BoundSignal()
        self.url = None

    def page(self):
        return self._page

    def setPage(self, page):
        self._page = page

    def settings(self):
        return self._settings

    def setUrl(self, url):
        self.url = url


class QWebChannel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.objects = {}

    def registerObject(self, name, obj):
        self.objects[name] = obj


class QHeaderView:
    Stretch = 1


class QAbstractItemView:
    NoEditTriggers = 0
    SelectRows = 1
    SingleSelection = 2


def install_fake_pyside():
    core = types.ModuleType("PySide6.QtCore")
    core.QObject = QObject
    core.Qt = Qt
    core.QUrl = QUrl
    core.Signal = Signal
    core.Slot = Slot
    core.QSettings = QSettings
    core.QTimer = types.SimpleNamespace(singleShot=lambda ms, fn: fn())

    gui = types.ModuleType("PySide6.QtGui")
    gui.QKeySequence = QKeySequence
    gui.QShortcut = QShortcut
    gui.QColor = QColor
    gui.QPainter = QPainter
    gui.QPen = QPen

    widgets = types.ModuleType("PySide6.QtWidgets")
    widgets.QApplication = QApplication
    widgets.QMainWindow = QMainWindow
    widgets.QWidget = QWidget
    widgets.QVBoxLayout = QVBoxLayout
    widgets.QHBoxLayout = QHBoxLayout
    widgets.QFormLayout = QFormLayout
    widgets.QStackedLayout = QStackedLayout
    widgets.QTabWidget = QTabWidget
    widgets.QDialog = QDialog
    widgets.QDialogButtonBox = QDialogButtonBox
    widgets.QDoubleSpinBox = QDoubleSpinBox
    widgets.QColorDialog = QColorDialog
    widgets.QLabel = QLabel
    widgets.QLineEdit = QLineEdit
    widgets.QPushButton = QPushButton
    widgets.QCheckBox = QCheckBox
    widgets.QComboBox = QComboBox
    widgets.QSpinBox = QSpinBox
    widgets.QTableWidget = QTableWidget
    widgets.QTableWidgetItem = QTableWidgetItem
    widgets.QHeaderView = QHeaderView
    widgets.QTextEdit = QTextEdit
    widgets.QSplitter = QSplitter
    widgets.QAbstractItemView = QAbstractItemView
    widgets.QFileDialog = QFileDialog
    widgets.QInputDialog = QInputDialog
    widgets.QMessageBox = QMessageBox

    web = types.ModuleType("PySide6.QtWebEngineWidgets")
    web.QWebEngineView = QWebEngineView

    webcore = types.ModuleType("PySide6.QtWebEngineCore")
    webcore.QWebEnginePage = QWebEnginePage
    webcore.QWebEngineSettings = QWebEngineSettings

    wc = types.ModuleType("PySide6.QtWebChannel")
    wc.QWebChannel = QWebChannel

    pkg = types.ModuleType("PySide6")
    pkg.QtCore = core
    pkg.QtGui = gui
    pkg.QtWidgets = widgets
    pkg.QtWebEngineCore = webcore

    sys.modules["PySide6"] = pkg
    sys.modules["PySide6.QtCore"] = core
    sys.modules["PySide6.QtGui"] = gui
    sys.modules["PySide6.QtWidgets"] = widgets
    sys.modules["PySide6.QtWebEngineWidgets"] = web
    sys.modules["PySide6.QtWebEngineCore"] = webcore
    sys.modules["PySide6.QtWebChannel"] = wc


# =========================================================
# Test
# =========================================================
def make_traffic(t0):
    dt = t0
    return [
        {"flight": "EWG51Y", "hex": "3c6444", "r": "D-AEWD", "t": "A20N",
         "category": "A3", "lat": 50.03 + dt * 0.01, "lon": 8.57 + dt * 0.02,
         "alt_baro": 24000, "alt_geom": 24500, "gs": 420, "track": 90,
         "baro_rate": 1200, "squawk": "1000", "seen": 0.4, "messages": 500},
        {"flight": "DLH123", "hex": "3c0001", "r": "D-AIXA", "t": "A359",
         "category": "A5", "lat": 50.5, "lon": 8.2,
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
    ]


class FakeRouteCache:
    def __init__(self):
        self._cache = {}

    def get(self, callsign):
        route = {"origin_icao": "EDDF", "dest_icao": "LEPA",
                 "origin_lat": 50.03, "origin_lon": 8.57,
                 "dest_lat": 39.55, "dest_lon": 2.73,
                 "origin_name": "Frankfurt Main", "dest_name": "Palma"}
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
    install_fake_pyside()
    import main as appmod
    from scripts import verify_map_bundle
    import traffic_core as tc
    from startup_splash import FlightPathWidget
    from settings_dialog import SettingsDialog

    logged_actions = []
    appmod.log_action = lambda action, **details: logged_actions.append(action)

    path_widget = FlightPathWidget()
    path_widget.set_progress(0.42)
    check(path_widget._progress == 0.42 and path_widget.updated,
          "Flugmarker folgt dem gemeldeten Ladefortschritt")
    path_widget.set_progress(1.5)
    check(path_widget._progress == 1.0, "Flugmarker begrenzt Fortschritt auf das Streckenende")

    print("== Community folder selection ==")
    real_discover = appmod.discover_community_folders
    old_community_env = os.environ.pop("MSFS_COMMUNITY_PATH", None)
    old_packages_env = os.environ.pop("MSFS_PACKAGES_PATH", None)
    with tempfile.TemporaryDirectory() as temp_dir:
        first = os.path.join(temp_dir, "sim2020", "Community")
        second = os.path.join(temp_dir, "sim2024", "Community")
        third = os.path.join(temp_dir, "custom", "Community")
        for folder in (first, second, third):
            os.makedirs(folder)
        QSettings.store.pop(appmod.COMMUNITY_SETTING_KEY, None)
        appmod.discover_community_folders = lambda: [first, second]
        QInputDialog.result = (second, True)
        chosen = appmod._choose_community_folder(None, QSettings())
        check(chosen == second and
              QSettings.store.get(appmod.COMMUNITY_SETTING_KEY) == second,
              "Mehrere Community-Ordner werden zur Auswahl angeboten und gespeichert")

        QSettings.store.pop(appmod.COMMUNITY_SETTING_KEY, None)
        chosen = appmod._choose_community_folder(None, QSettings(), defer_prompts=True)
        check(chosen is None and appmod.COMMUNITY_SETTING_KEY not in QSettings.store,
              "Mehrere Simulator-Installationen blockieren den Kartenstart nicht")

        QSettings.store.pop(appmod.COMMUNITY_SETTING_KEY, None)
        appmod.discover_community_folders = lambda: []
        QFileDialog.directory_result = third
        chosen = appmod._choose_community_folder(None, QSettings())
        check(chosen == third and QMessageBox.messages,
              "Fehlender Community-Ordner öffnet einen Hinweis und Ordnerdialog")

        QSettings.store.pop(appmod.COMMUNITY_SETTING_KEY, None)
        messages_before = len(QMessageBox.messages)
        chosen = appmod._choose_community_folder(None, QSettings(), defer_prompts=True)
        check(chosen is None and len(QMessageBox.messages) == messages_before,
              "Fehlender Community-Ordner blockiert den Kartenstart nicht")
        appmod.discover_community_folders = real_discover
        QFileDialog.directory_result = ""
        QInputDialog.result = ("", False)
        QSettings.store.pop(appmod.COMMUNITY_SETTING_KEY, None)

    if old_community_env is not None:
        os.environ["MSFS_COMMUNITY_PATH"] = old_community_env
    if old_packages_env is not None:
        os.environ["MSFS_PACKAGES_PATH"] = old_packages_env

    global_fetch_started = threading.Event()
    global_fetch_release = threading.Event()
    global_search_calls = []
    original_global_fetcher = appmod.traffic_core.fetch_global_traffic
    global_search_snapshot = [{
        "flight": "WIDE42", "hex": "abc123", "r": "D-TEST",
        "t": "A320", "lat": -33.8688, "lon": 151.2093,
        "alt_baro": 31000, "gs": 430, "track": 270,
    }]

    def fake_global_fetch():
        global_search_calls.append(time.monotonic())
        global_fetch_started.set()
        if len(global_search_calls) == 1:
            global_fetch_release.wait(2.0)
        return {
            "ok": True, "aircraft": global_search_snapshot,
            "cached": False, "stale": False, "rate_limited": False,
            "in_flight": False, "error": None,
        }

    appmod.traffic_core.fetch_global_traffic = fake_global_fetch
    win = appmod.MainWindow()
    win.engine.fetcher = lambda lat, lon, r=250, bounds=None: make_traffic(time.time() % 100)
    win.engine.route_cache = FakeRouteCache()
    win.engine.REFRESH_SEC = 0.05
    win.start_live_map()
    check(win.engine.running and win.chk_all.isChecked(),
          "Live-Karte startet unabhängig von MSFS mit gesamtem Traffic")

    js_states = []
    win.bridge.stateUpdated.connect(lambda s: js_states.append(json.loads(s)))

    print("== Webview / Bridge ==")
    map_page_url = win.web.url.s.split("#", 1)[0].split("?", 1)[0]
    check(map_page_url.endswith(os.path.join("gui_web", "map.html"))
          and "#lang=" in win.web.url.s, "map.html mit App-Sprache geladen")
    check(isinstance(win.web.page(), appmod.MapDiagnosticPage),
          "JavaScript-Konsole wird für die Kartendiagnose protokolliert")
    win.web.page().javaScriptConsoleMessage(2, "test map error", 7, "file:///map.js")
    check("map.javascript_console_message" in logged_actions,
          "JavaScript-Fehler landen im Aktionsprotokoll")
    check("map.assets_checked" in logged_actions,
          "lokale Karten- und Leaflet-Dateien werden geprüft")
    check("JSON.stringify" in appmod.MAP_RUNTIME_DIAGNOSTICS_JS
          and "webchannel_transport" in appmod.MAP_RUNTIME_DIAGNOSTICS_JS
          and "map_script_stage" in appmod.MAP_RUNTIME_DIAGNOSTICS_JS,
          "Map-Diagnostik wird als JSON-String zur Qt-Seite übertragen")
    runtime_probe = {
        "map_script_stage": "waiting-for-webchannel",
        "qwebchannel_constructor": False,
        "tile_fallback_used": True,
        "tile_last_error": {"url": "https://tile.invalid/1/1/1.png"},
    }
    win._on_map_runtime_diagnostics(json.dumps(runtime_probe))
    check(win._last_map_runtime_diagnostics == runtime_probe
          and "map.runtime_diagnostics" in logged_actions,
          "JSON-String mit Map-/Tile-Diagnose wird geparst und gespeichert")
    with tempfile.TemporaryDirectory() as bundle_root:
        app_data = os.path.join(bundle_root, "app-data")
        executable = os.path.join(bundle_root, "bundle")
        incomplete_assets = os.path.join(app_data, "gui_web")
        complete_assets = os.path.join(executable, "_internal", "gui_web")
        os.makedirs(incomplete_assets)
        os.makedirs(complete_assets)
        open(os.path.join(incomplete_assets, "map.html"), "w").close()
        all_required_assets = set(appmod.MAP_REQUIRED_ASSETS)
        all_required_assets.update(verify_map_bundle.REQUIRED_MAP_FILES)
        for relative_path in all_required_assets:
            asset_path = os.path.join(complete_assets, relative_path)
            os.makedirs(os.path.dirname(asset_path), exist_ok=True)
            open(asset_path, "w").close()
        resolved = appmod.resolve_gui_web_dir(app_data, executable)
        verified, _ = verify_map_bundle.find_map_assets(os.path.join(bundle_root, "bundle"))
        os.remove(os.path.join(complete_assets, "map.js"))
        partial_resolved = appmod.resolve_gui_web_dir(app_data, executable)
        rejected, checks = verify_map_bundle.find_map_assets(os.path.join(bundle_root, "bundle"))
    check(resolved == complete_assets and str(verified) == complete_assets
          and partial_resolved == complete_assets,
          "Map-Assets wählen auch bei mehreren Ordnern den vollständigsten _internal-Bundle-Pfad")
    check(rejected is None and any("map.js" in missing for _, missing in checks),
          "Build-Pruefung erkennt ein fehlendes map.js im fertigen Paket")
    win._on_map_runtime_diagnostics(None)
    check("map.runtime_diagnostics_unavailable" in logged_actions,
          "fehlender WebEngine-Callbackwert wird samt Typ protokolliert")
    with open(os.path.join(REPO, "gui_web", "map.js"), encoding="utf-8") as map_file:
        map_script = map_file.read()
    with open(os.path.join(REPO, "gui_web", "map.html"), encoding="utf-8") as html_file:
        map_html = html_file.read()
    check("function connectQtBridge(attempt)" in map_script
          and "webchannel-callback-timeout" in map_script
          and "tile-fallback-loading" in map_script
          and "tile_fallback_used" in map_script
          and "waitForMapViewport" in map_script
          and "viewport_failed" in map_html
          and "bridge_failed" in map_html
          and "asset_failed" in map_html
          and "leaflet/leaflet.css" in map_html
          and "this.src||'map.js'" in map_html
          and "window.addEventListener(\"error\"" in map_html
          and "map-notice" in map_html
          and "window.location.hash" in map_html
          and "map_notice_key" in appmod.MAP_RUNTIME_DIAGNOSTICS_JS,
          "Kartenstart meldet lokale Script-Pfade und Map-Fehler sichtbar in Deutsch/Englisch")
    win.web.loadFinished.emit(False)
    check(win._map_load_finished and win._map_load_success is False
          and "map.page_load_finished" in logged_actions
          and win.map_error_overlay.isVisible()
          and win.map_error_overlay.text() == appmod.TEXT["map_load_failed"][appmod.LANG],
          "fehlgeschlagener Karten-Load wird protokolliert und direkt auf der Karte angezeigt")
    win._last_map_runtime_diagnostics = {
        "map_notice_visible": True,
        "map_notice_key": "asset_failed",
        "map_notice_text": "A local map component failed: file:///app/gui_web/map.js",
    }
    win.show_map_load_fallback()
    check(not win.map_error_overlay.isVisible(),
          "spezifischer HTML-Asset-Fehler wird nicht vom generischen Qt-Overlay verdeckt")
    win.web.loadFinished.emit(True)
    check(win._map_load_success is True and not win.map_error_overlay.isVisible(),
          "erfolgreicher Karten-Load blendet den Qt-Fehlerhinweis aus")
    win.bridge.uiReady()
    check(win._map_js_ready and "map.javascript_ready" in logged_actions,
          "Splash-Ende kann an die initialisierte Karten-JavaScript-Brücke gekoppelt werden")
    check(win._title == "MSFS Live Traffic Utility", "Fenstertitel verwendet den Produktnamen")
    win.refresh_map_layout()
    check(any("ltuRefreshMapLayout" in code for code in win.web.page().js_log),
          "Leaflet-Viewport wird nach dem Anzeigen des Fensters neu berechnet")
    local_access = appmod.QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls
    remote_access = appmod.QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls
    check(win.web.settings().attributes.get(local_access) is True
          and win.web.settings().attributes.get(remote_access) is True,
          "lokale Karten-Assets und externe Tile-Server sind fuer QtWebEngine explizit freigegeben")
    check(win.channel.objects.get("bridge") is win.bridge, "Bridge als 'bridge' registriert")
    win.show()
    win.showEvent(None)
    check("window.show_event" in logged_actions
          and any("ltuGetMapDiagnostics" in code for code in win.web.page().js_log),
          "Webview-Sichtbarkeit und Leaflet-Viewport werden nach Show diagnostiziert/aktualisiert")

    print("== Settings-Push ==")
    win.push_settings()
    settings_json = win.bridge.settingsUpdated  # BoundSignal
    captured = []
    settings_json.connect(lambda s: captured.append(json.loads(s)))
    win.push_settings()
    check(captured and captured[-1].get("tile_url"), "Settings mit Tile-URL")
    check("OpenStreetMap" in captured[-1].get("tile_attribution", ""),
          "Kartenanbieter-Attribution wird an Leaflet uebergeben")
    check(all(len(v) == 3 for v in appmod.TILE_SERVERS.values()),
          "Jeder Kartenstil definiert URL, Zoomlimit und Attribution")
    check(not any("google.com" in v[0] for v in appmod.TILE_SERVERS.values()),
          "Keine undokumentierten Google-Tile-Endpunkte im Kartenmenue")
    check(captured[-1].get("icon_size", 0) > 0, "Icon-Größe in Settings")
    with open(os.path.join(REPO, "gui_web", "map.js"), encoding="utf-8") as map_js_file:
        map_js_for_color = map_js_file.read()
    check(captured[-1].get("emergency_color") == appmod.THEME.get("emergency_color")
          and "--emergency-color" in map_js_for_color,
          "Notfallfarbe wird an die Leaflet-Karte übergeben")

    print("== Einstellungsdialog ==")
    default_style = appmod.TILE_SERVERS["OpenStreetMap"]
    dialog = SettingsDialog({
        "planetrack_api_key": "local-test-key",
        "planetrack_radius_nm": 40,
        "proxy_enabled": True,
        "proxy_url": "https://proxy.example/traffic",
        "proxy_token": "proxy-token",
        "peer_net_enabled": True,
        "peer_net_name": "test-client",
        "map_style": "OpenStreetMap",
        "tile_url": default_style[0],
        "tile_max_zoom": default_style[1],
        "tile_attribution": default_style[2],
        "center_lat": 50.0,
        "center_lon": 8.0,
        "zoom": 7,
        "theme": {key: appmod.THEME[key] for key in (
            "main_bg", "panel_bg", "text_color", "entry_bg", "entry_fg", "btn_bg",
            "btn_fg", "btn_disabled_bg", "label_color", "emergency_color", "accent_color",
        )},
        "gradient": tc.ALT_GRADIENT,
        "icon_size": 28,
        "selected_icon_size": 34,
        "filters": {"show_all": True, "follow": True, "labels": True, "route": True,
                    "hide_ground": False, "trails": False, "tcas": False, "alerts": True},
    }, appmod.TILE_SERVERS, language="de")
    dialog_values = dialog.values()
    check(dialog._title == "Einstellungen"
          and "traffic_provider" not in dialog_values
          and dialog_values["planetrack_api_key"] == "local-test-key"
          and "traffic_provider" not in dialog._widgets,
          "Dialog bietet keine separat bevorzugte Quelle; der lokale Fallback-Key bleibt verfügbar")
    check(dialog._widgets["planetrack_api_key"].echo_mode == QLineEdit.EchoMode.Password,
          "PlaneTrack-API-Key wird im Eingabefeld verborgen")
    english_dialog = SettingsDialog({}, appmod.TILE_SERVERS, language="en")
    check(english_dialog._title == "Settings"
          and english_dialog.tabs.tabs[0][1] == "Data & network"
          and english_dialog.standard_button.text() == "Load defaults",
          "Einstellungsdialog verwendet auch die englischen Texte")
    english_dialog.map_style.setCurrentText(appmod.TEXT["map_custom_style"]["en"])
    english_dialog._widgets["tile_url"].setText("https://tiles.example/{z}/{x}/{y}.png")
    english_dialog._widgets["tile_attribution"].setText("Test map data")
    english_dialog._accept_if_valid()
    check(english_dialog._accepted
          and english_dialog.values()["map_style"] == appmod.TEXT["map_custom_style"]["en"],
          "Englische benutzerdefinierte Kartenquelle verwendet den App-Kartenstil")

    standard_values = win._settings_dialog_values()["standard_values"]
    custom_profile_values = dialog.values()
    custom_profile_values.update({
        "standard_values": standard_values,
        "planetrack_api_key": "keep-this-key",
        "proxy_enabled": False,
        "proxy_url": "https://proxy.example/keep",
        "proxy_token": "keep-this-token",
        "peer_net_name": "PW-Laptop",
        "map_style": "Topo (OpenTopoMap)",
        "center_lat": 51.665,
        "center_lon": 13.118,
        "zoom": 6,
        "community_directory": "C:/MSFS/Community",
        "simconnect_dll": "C:/SimConnect.dll",
    })
    standard_dialog = SettingsDialog(custom_profile_values, appmod.TILE_SERVERS,
                                     language="de")
    check(standard_dialog.standard_button.isEnabled()
          and standard_dialog.profile_status.text() == "Profil: Benutzerdefiniert",
          "Benutzerdefinierte Werte werden als eigenes Profil erkannt")
    check("Standard: OpenStreetMap" in standard_dialog._field_label(
              "Kartenstil", "map_style", "Topo (OpenTopoMap)"),
          "Abweichender Kartenstil zeigt den bisherigen Standardwert")
    standard_dialog.standard_button.clicked.emit()
    standardized = standard_dialog.values()
    check(standard_dialog.profile_status.text() == "Profil: Standard"
          and standardized["map_style"] == standard_values["map_style"]
          and standardized["center_lat"] == 50.033
          and standardized["zoom"] == 7
          and standardized["filters"]["trails"] is False,
          "Schaltfläche lädt das frühere Standardprofil")
    check(standardized["planetrack_api_key"] == "keep-this-key"
          and standardized["proxy_token"] == "keep-this-token"
          and standardized["community_directory"] == "C:/MSFS/Community"
          and standardized["simconnect_dll"] == "C:/SimConnect.dll",
          "Standardprofil bewahrt API-/Proxy-Zugangsdaten und lokale Simulatorpfade")

    dialog.map_style.setCurrentText(appmod.TEXT["map_custom_style"][appmod.LANG])
    dialog._widgets["tile_url"].setText("https://tiles.example/{z}/{x}/{y}.png")
    dialog._widgets["tile_attribution"].setText("Test map data")
    dialog._accept_if_valid()
    check(dialog._accepted and dialog.values()["map_style"] == appmod.TEXT["map_custom_style"][appmod.LANG],
          "Benutzerdefinierter Kartenstil wird mit Tile-URL und Attribution validiert")

    print("== Start / Tabelle ==")
    win.chk_all.setChecked(True)
    win.start_tracking()
    check(wait_for(lambda: win.table.rowCount() >= 4), f"Tabelle gefüllt ({win.table.rowCount()} Zeilen)")
    check(win.table.headers[0] != "", "Tabellen-Header gesetzt")
    calls = [win.table.item(r, 0).text() for r in range(win.table.rowCount())]
    check(calls == sorted(calls), "Tabelle sortiert")
    emerg_rows = [r for r in range(win.table.rowCount())
                  if win.table.item(r, 0).text() == "BAW456"]
    check(emerg_rows and win.table.item(emerg_rows[0], 0).fg is appmod.QCOLOR_EMERG,
          "Notfall-Zeile rot eingefärbt")

    print("== Bridge-JSON für map.js ==")
    check(wait_for(lambda: js_states and js_states[-1]["aircraft"]), "State-JSON emittiert")
    st = js_states[-1]
    check({"call", "lat", "lon", "hdg", "color", "kind"} <= set(st["aircraft"][0]),
          "Aircraft-Felder für JS-Renderer")

    print("== Auswahl per Tabelle ==")
    row = calls.index("DLH123")
    win.table.selectRow(row)
    table_mutations_before = len(win.table.mutation_signal_states)
    original_single_shot = appmod.QTimer.singleShot
    queued_selection = []
    appmod.QTimer.singleShot = lambda _ms, callback: queued_selection.append(callback)
    try:
        win.on_table_select()
        check(win.engine.selected != "DLH123" and len(queued_selection) == 1,
              "Engine-Auswahl wird nach dem Qt-Selection-Callback ausgeführt")
        queued_selection.pop(0)()
    finally:
        appmod.QTimer.singleShot = original_single_shot
    table_mutations = win.table.mutation_signal_states[table_mutations_before:]
    check(win.engine.selected == "DLH123", "Tabellenauswahl → Engine")
    check(table_mutations and all(table_mutations)
          and not win.table._signals_blocked and not win._updating_table,
          "Tabellen-Neuaufbau blockiert Signale vollständig und stellt sie wieder her")
    check(wait_for(lambda: "DLH123" in win.info.html), "Info-Panel zeigt Auswahl")
    check(wait_for(lambda: "LEPA" in win.info.html), "Info-Panel zeigt Route")

    print("== Robustheit bei unvollständigen ADS-B-Werten ==")
    detail = win.engine.info_lines("DLH123")
    detail["baro_rate"] = "not-a-number"
    safe_html = appmod.info_html(detail)
    check("V/S:</span>" not in safe_html,
          "Ungültige Steigrate kann die Detailansicht nicht abbrechen")
    check(appmod._display_int("125.9") == "125"
          and appmod._display_int("NaN") == "-",
          "Tabellenwerte behandeln numerische Strings und NaN sicher")
    win.apply_state(None)
    check("ui.state_apply_failed" in logged_actions,
          "Unerwartete UI-Fehler werden protokolliert statt die App zu beenden")

    print("== Karten-Klick (Bridge-Slot) ==")
    win.bridge.aircraftClicked("EWG51Y")
    check(win.engine.selected == "EWG51Y", "aircraftClicked wählt aus")

    print("== Kontextmenü: nearest ==")
    win.bridge.mapContextMenu(50.05, 8.59)
    check(win.engine.selected == "RYR789", "mapContextMenu wählt nächstes Flugzeug")

    print("== mapMoved -> Engine-Zentrum + Bereich ==")
    win.bridge.mapMoved(48.0, 11.0, 9.0, 49.0, 47.0, 12.0, 10.0)
    check(win.engine.options["center"] == [48.0, 11.0], "Kartenzentrum übernommen")
    check(win.engine.options["zoom"] == 9.0, "Zoom übernommen")
    check(win.engine.options["bounds"] == [49.0, 47.0, 12.0, 10.0],
          "Sichtbarer Bereich (Bounds) übernommen")

    print("== Filter-Widgets ==")
    win.spin_alt.setValue(30000)
    check(win.engine.options["min_alt"] == 30000, "Min-Höhe aus SpinBox")
    win.spin_alt.setValue(0)
    check(win.engine.options["min_alt"] is None, "Min-Höhe zurückgesetzt")
    win.chk_ground.setChecked(True)
    check(win.engine.options["hide_ground"] is True, "hide_ground gesetzt")

    print("== Kartenstil ==")
    win.combo_style.setCurrentText("Dunkel (Carto)")
    check(captured and "cartocdn" in captured[-1]["tile_url"], "Tile-URL gewechselt")

    print("== Status-LEDs & Mesh-Rolle ==")
    check({"proxy", "adsb.lol", "adsb.fi", "opensky", "ersatz", "msfs"}
          <= set(win.status_leds), "LED-Leiste in der Statusbar")
    tc.SOURCES.report("proxy", True, 200, "ok")
    tc.SOURCES.report("adsb.lol", False, 429, "rate-limited")
    check(wait_for(lambda: "#3ddc84" in win.status_leds["proxy"].stylesheet),
          "Proxy-LED grün bei ok")
    check(wait_for(lambda: "#ffb020" in win.status_leds["adsb.lol"].stylesheet),
          "adsb.lol-LED orange bei 429")
    check("Mesh" in win.lbl_role.text() or "mesh" in win.lbl_role.text(),
          "Mesh-Rolle angezeigt (hier: aus)")
    st = js_states[-1]
    check("status" in st and "net" in st, "status/net im Bridge-JSON für map.js")
    check(st["net"] and st["net"]["enabled"] is False, "net.enabled=false (LTU_MESH=off)")
    tc.SOURCES._src.clear()

    print("== Suche & Filter (Zeile 3) ==")
    win.chk_ground.setChecked(False)   # aus "Filter-Widgets" noch aktiv
    win.engine.select(None)
    win.entry_search.setText("DLH")
    check(win.engine.options["search"] == "DLH", "Suchtext → Engine-Option")
    check(not global_search_calls,
          "Globale Suche startet erst beim Absenden, nicht bei jedem Tastendruck")
    check(wait_for(lambda: win.table.rowCount() == 1 and
                   win.table.item(0, 0).text() == "DLH123"),
          "Suche filtert Tabelle auf DLH123")
    win.entry_search.setText("")
    win.entry_airline.setText("Lufthansa")
    check(wait_for(lambda: win.table.rowCount() == 1 and
                   win.table.item(0, 0).text() == "DLH123"),
          "Airline-Filter (Lufthansa → DLH123)")
    win.entry_airline.setText("")
    win.entry_type.setText("B738")
    check(wait_for(lambda: win.table.rowCount() == 1 and
                   win.table.item(0, 0).text() == "RYR789"),
          "Typ-Filter (B738 → RYR789)")
    win.entry_type.setText("")
    check(wait_for(lambda: win.table.rowCount() >= 4), "Filter geleert → alle wieder da")

    print("== Suche: lokaler + weltweiter Treffer (Enter) ==")
    win.entry_search.setText("3c0001")     # Hex von DLH123
    win.search_jump()
    check(win.engine.selected == "DLH123", "search_select findet lokalen Hex-Treffer sofort")
    check(global_fetch_started.wait(1.0) and win._global_search_inflight,
          "Globaler Snapshot läuft asynchron neben der GUI")
    check("DLH123" in win.status_left.text(), "Lokaler Treffer ist sofort sichtbar")

    # Enter a second query while the generic global snapshot is still loading;
    # only the newest still-active query should consume its result.
    win.entry_search.setText("WIDE42")
    win.search_jump()
    check(len(global_search_calls) == 1 and win._global_search_inflight,
          "Überlappende Suche wird an den laufenden Snapshot angehängt")
    global_fetch_release.set()
    check(wait_for(lambda: not win._global_search_inflight),
          "Globaler Snapshot wird an die GUI zurückgemeldet")
    check(wait_for(lambda: win.engine.selected == "WIDE42"
                   and "WIDE42" in win.status_left.text()),
          "Weltweiter Treffer der aktuellen Suche wird ausgewählt")
    check(win.engine.options["center"] == [-33.8688, 151.2093],
          "Karte wird auf Position des weltweiten Treffers zentriert")
    check(len(global_search_calls) == 1,
          "Zwei überlappende Enter-Suchen erzeugen nur einen Global-Request")
    win.entry_search.setText("")
    win.engine.select(None)

    print("== Spuren-Toggle (trails im Bridge-JSON) ==")
    win.chk_trails.setChecked(True)
    check(win.engine.options["trails"] is True, "trails aktiviert")
    check(wait_for(lambda: bool(js_states[-1].get("trails"))), "State enthält trails")
    tr = js_states[-1]["trails"]
    check(len(tr) >= 4, f"Trail je Flugzeug ({len(tr)} Einträge)")
    check(all(len(p) == 2 for pts in tr.values() for p in pts), "Trail-Punkte als [lat, lon]")
    win.chk_trails.setChecked(False)
    check(wait_for(lambda: js_states[-1].get("trails") == {}), "trails aus → leer")

    print("== Notfall-Alarm (Toast + Beep) ==")
    win.engine.options["bounds"] = None    # BAW456 (49.9/8.9) lag außerhalb der Test-BBox
    win.engine._emerg_seen.clear()
    before = QApplication.beep_count
    check(wait_for(lambda: QApplication.beep_count > before),
          "Beep bei Notfall im Sichtbereich")
    check(win._toast_last is not None and "BAW456" in win._toast_last.text(),
          "Toast nennt das Notfall-Flugzeug")
    n_beeps = QApplication.beep_count
    time.sleep(0.3)
    check(QApplication.beep_count == n_beeps, "Kein Dauer-Alarm für denselben Notfall")

    print("== CSV-Export (Engine gestoppt, kein Status-Race) ==")
    win.stop_tracking()
    out = os.path.join(tempfile.mkdtemp(), "export.csv")
    QFileDialog.result = (out, "CSV (*.csv)")
    win.btn_csv.clicked.emit()
    check(os.path.exists(out), "CSV-Datei geschrieben")
    with open(out, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    check(lines and lines[0].split(";")[0] == "callsign", "CSV-Header (Semikolon)")
    check(any("DLH123" in ln for ln in lines), "DLH123-Zeile in CSV")
    check("export.csv" in win.status_left.text(), "Statusmeldung mit Dateiname")
    QFileDialog.result = ("", "")

    print("== TCAS: eigene Position + Relativdaten ==")
    win.engine.set_own_position(50.0, 8.5, 20000, 360)
    win.chk_tcas.setChecked(True)
    check(win.engine.options["tcas"] is True, "tcas aktiviert")
    win.bridge.aircraftClicked("DLH123")   # triggert State-Emission (Engine steht)
    st = js_states[-1]
    check(st.get("own") and abs(st["own"]["lat"] - 50.0) < 1e-6,
          "Eigene Position im State-JSON")
    rel = st.get("selected_rel")
    check(rel is not None, "selected_rel im State-JSON")
    if rel:
        check(1 <= rel["clock"] <= 12 and rel["dist_km"] > 0, "Clock/Distanz plausibel")
        check(rel["clock"] == (int((rel["rel_brg"] + 15) // 30) % 12 or 12),
              "Clock-Formel konsistent")
    check(st["center"] == [50.0, 8.5], "TCAS zentriert eigenes Flugzeug")
    check("TCAS" in win.info.html.upper(), "TCAS-Block im Info-Panel")
    win.chk_tcas.setChecked(False)

    print("== Stop ==")
    win.stop_tracking()
    check(win.engine.running is False, "Engine gestoppt")
    check(win.btn_start.isEnabled() and not win.btn_stop.isEnabled(), "Button-States")

    print("== Shortcuts ==")
    check(len(SHORTCUTS) >= 3, "Esc/+/- Shortcuts registriert")
    win.engine.select("DLH123")
    SHORTCUTS[0].activated.emit()
    check(win.engine.selected is None, "Esc hebt Auswahl auf")

    print("== MSFS-Status (ohne SimConnect) ==")
    win.msfs_ready(False)
    check("nicht verbunden" in win.lbl_msfs.text() or "not connected" in win.lbl_msfs.text(),
          "MSFS-Status ohne Verbindung")
    check("Karte aktiv" in win.lbl_msfs.text() or "map active" in win.lbl_msfs.text(),
          "Live-Kartenmodus wird ohne MSFS angezeigt")
    check(not win.chk_msfs.isEnabled(), "MSFS-Checkbox disabled")
    win.start_live_map()
    check(win.engine.running, "Karten-Traffic läuft trotz getrennter MSFS-Verbindung")
    check(win.engine.set_own_position is win.runner.on_own_position or
          win.runner.on_own_position is not None,
          "MSFS-Runner liefert Own-Position an Engine")

    print("== MSFS-Verbindungswiederholung ==")

    class _RetryStopEvent:
        def __init__(self):
            self.delays = []

        def is_set(self):
            return False

        def wait(self, delay):
            self.delays.append(delay)
            return False

    class _RetryRunner:
        def __init__(self):
            self.attempts = 0

        def connect(self):
            self.attempts += 1
            return object() if self.attempts == 3 else None

    class _RetryBridge:
        def __init__(self):
            self.msfsStatus = BoundSignal()

    probe = object.__new__(appmod.MainWindow)
    probe._closing = False
    probe._msfs_probe_stop = _RetryStopEvent()
    probe.runner = _RetryRunner()
    probe.bridge = _RetryBridge()
    statuses = []
    probe.bridge.msfsStatus.connect(statuses.append)
    captured_probe_logs = []
    saved_log_action = appmod.log_action
    saved_simconnect_available = appmod.SIMCONNECT_AVAILABLE
    appmod.log_action = lambda action, **details: captured_probe_logs.append(
        (action, details)
    )
    appmod.SIMCONNECT_AVAILABLE = True
    try:
        probe._msfs_probe()
    finally:
        appmod.log_action = saved_log_action
        appmod.SIMCONNECT_AVAILABLE = saved_simconnect_available
    check(probe.runner.attempts == 3 and statuses == [False, True],
          "fehlgeschlagene Startprobe wird bis zur MSFS-Verbindung wiederholt")
    check(probe._msfs_probe_stop.delays == [
              appmod.MSFS_RECONNECT_INITIAL_SEC,
              min(appmod.MSFS_RECONNECT_INITIAL_SEC * 2.0,
                  appmod.MSFS_RECONNECT_MAX_SEC),
          ], "Wiederholungsintervall verwendet exponentiellen Backoff")
    check(any(action == "msfs.connection_probe_completed"
              and details.get("connected") is True and details.get("attempt") == 3
              for action, details in captured_probe_logs),
          "erfolgreicher Wiederverbindungsversuch wird protokolliert")

    print("== Einstellungen merken (QSettings) ==")
    win.combo_style.setCurrentText("Dunkel (Carto)")
    win.chk_trails.setChecked(True)
    win.engine.options["center"] = [51.5, 13.5]
    win.engine.options["zoom"] = 9
    apply_dialog = SettingsDialog(win._settings_dialog_values(), appmod.TILE_SERVERS,
                                  language=appmod.LANG)
    apply_values = apply_dialog.values()
    apply_values.update({
        "planetrack_api_key": "local-pref-key",
        "planetrack_radius_nm": 37,
        "proxy_enabled": False,
        "peer_net_enabled": False,
        "ai_injection_enabled": False,
    })
    apply_values["theme"]["emergency_color"] = "#ff7133"
    applied = win._apply_user_settings(apply_values)
    check(applied and "traffic/provider" not in QSettings.store
          and QSettings.store.get("traffic/planetrack_api_key") == "local-pref-key",
          "Einstellungsdialog speichert nur den lokalen Direkt-Fallback-Key, keine Anbieterwahl")

    class _Ev:
        def accept(self):
            pass

    win.closeEvent(_Ev())
    check(QSettings.store.get("map_style") == "Dunkel (Carto)", "Kartenstil gespeichert")
    check(QSettings.store.get("center") == [51.5, 13.5], "Zentrum gespeichert")
    check(QSettings.store.get("trails") is True, "Trails-Option gespeichert")
    win2 = appmod.MainWindow()
    check(win2.combo_style.currentText() == "Dunkel (Carto)", "Kartenstil wiederhergestellt")
    check(win2.engine.options["center"] == [51.5, 13.5], "Zentrum wiederhergestellt")
    check(win2.engine.options["zoom"] == 9, "Zoom wiederhergestellt")
    check(win2.chk_trails.isChecked(), "Trails-Checkbox wiederhergestellt")
    check(appmod.THEME.get("emergency_color") == "#ff7133"
          and appmod.QCOLOR_EMERG.name() == "#ff7133",
          "Notfallfarbe aus QSettings wird beim Start wiederhergestellt")
    restored_source = appmod.traffic_core.traffic_source_config()
    check(restored_source["provider"] == "auto"
          and restored_source["planetrack_api_key"] == "local-pref-key"
          and restored_source["planetrack_radius_nm"] == 37,
          "Automatische Quellenkette sowie lokaler Fallback-Key und Radius werden geladen")
    check(not appmod.traffic_core.proxy_settings()[0]
          and win2._ai_injection_preference is False,
          "Proxy- und MSFS-AI-Präferenzen werden wiederhergestellt")
    win2._msfs_probe_stop.set()
    win2.engine.stop()
    win2.runner.stop()
    appmod.traffic_core.fetch_global_traffic = original_global_fetcher

    print("== Explizite Aktionsprotokollierung ==")
    required_actions = (
        "live_map.autostart", "settings.restored", "settings.saved",
        "msfs.connection_probe_started", "msfs.status_received",
        "ui.table_aircraft_selected", "application.window_close_requested",
    )
    check(wait_for(lambda: all(action in logged_actions for action in required_actions)),
          "Start, Einstellungen, MSFS-Probe/Status, Tabellenauswahl und Fensterende geloggt")

    print()
    if ERRORS:
        print(f"{len(ERRORS)} FEHLGESCHLAGENE CHECKS:")
        for e in ERRORS:
            print(" -", e)
        sys.exit(1)
    print("ALLE CHECKS BESTANDEN ✔")


if __name__ == "__main__":
    main()
