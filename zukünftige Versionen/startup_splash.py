"""Small animated startup window for the desktop application."""

import locale

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def _detect_language():
    try:
        language = locale.getlocale()[0] or locale.getdefaultlocale()[0] or "en"
        return "de" if language.lower().startswith("de") else "en"
    except Exception:
        return "en"


_STARTUP_TEXT = {
    "brand": {"de": "MSFS LIVE TRAFFIC UTILITY", "en": "MSFS LIVE TRAFFIC UTILITY"},
    "tagline": {
        "de": "Echtzeit-Verkehr · Microsoft Flight Simulator",
        "en": "Real-time traffic · Microsoft Flight Simulator",
    },
    "loading": {
        "de": "Lade Programmkomponenten …",
        "en": "Loading application components …",
    },
}


class FlightPathWidget(QWidget):
    """Move a marker along a climb, level-flight, descent path as loading advances."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._progress = 0.0
        self.setMinimumHeight(94)
        self.setMinimumWidth(300)

    def set_progress(self, progress):
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def paintEvent(self, _event):
        width = max(1, self.width())
        height = max(1, self.height())
        left, right = 18.0, float(width - 18)
        top, bottom = 16.0, float(height - 17)
        span = max(1.0, right - left)
        points = (
            (left, bottom),
            (left + span * 0.20, top),
            (right - span * 0.20, top),
            (right, bottom),
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#25354b"), 4))
        for start, end in zip(points, points[1:]):
            painter.drawLine(round(start[0]), round(start[1]), round(end[0]), round(end[1]))

        segment_lengths = [
            ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
            for start, end in zip(points, points[1:])
        ]
        remaining = self._progress * sum(segment_lengths)
        marker = points[0]
        painter.setPen(QPen(QColor("#4da3ff"), 4))
        for start, end, length in zip(points, points[1:], segment_lengths):
            if remaining >= length:
                painter.drawLine(round(start[0]), round(start[1]), round(end[0]), round(end[1]))
                remaining -= length
                marker = end
                continue
            fraction = max(0.0, remaining / length) if length else 0.0
            marker = (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )
            painter.drawLine(
                round(start[0]), round(start[1]), round(marker[0]), round(marker[1])
            )
            break

        painter.setPen(QPen(QColor("#c7e6ff"), 2))
        painter.setBrush(QColor("#4da3ff"))
        painter.drawEllipse(round(marker[0] - 6), round(marker[1] - 6), 12, 12)
        painter.end()


class StartupSplash(QWidget):
    """Frameless splash screen with a progress-driven flight profile."""

    def __init__(self, parent=None, language=None):
        language = language or _detect_language()
        language = language if language in ("de", "en") else "en"
        self.language = language
        self.progress = 0.0
        flags = (
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setFixedSize(500, 280)
        self.setStyleSheet(
            "StartupSplash { background:#0c1422; border:1px solid #263852; "
            "border-radius:14px; }"
            "QLabel { color:#e7eef8; background:transparent; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 26, 32, 24)
        layout.setSpacing(8)

        brand = QLabel(_STARTUP_TEXT["brand"][language])
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet(
            "font-size:18px; font-weight:800; letter-spacing:2px; color:#f4f8ff;"
        )
        layout.addWidget(brand)

        tagline = QLabel(_STARTUP_TEXT["tagline"][language])
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet("font-size:12px; color:#9aabc0;")
        layout.addWidget(tagline)

        layout.addSpacing(6)
        self.flight_path = FlightPathWidget(self)
        layout.addWidget(self.flight_path)

        self.message = QLabel(_STARTUP_TEXT["loading"][language])
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet("font-size:12px; color:#b8c8dc;")
        layout.addWidget(self.message)

        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(geometry.center() - self.rect().center())

    def set_progress(self, progress, message=None):
        self.progress = max(0.0, min(1.0, float(progress)))
        self.flight_path.set_progress(self.progress)
        if message is not None:
            self.message.setText(str(message))
        QApplication.processEvents()

    def set_message(self, message):
        self.message.setText(str(message))
        QApplication.processEvents()
