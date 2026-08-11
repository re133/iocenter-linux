#!/usr/bin/env python3
"""
Gemeinsame Oberflaechenbausteine -- Abstaende, Typografie, kleine Helfer.

Ein Raster von 4 px, alle Abstaende sind Vielfache davon. Farben kommen
aus der Palette der Arbeitsumgebung, damit sich das Fenster in helles wie
dunkles Design einfuegt; die einzige eigene Farbe ist der Akzent des
Geraets (#ff2800 aus dem Geraetemanifest).
"""

from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtGui import QColor, QFont, QPalette, QPainter
from PyQt6.QtWidgets import (QLabel, QFrame, QSizePolicy, QApplication,
                             QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QProgressBar, QGraphicsDropShadowEffect)

# Abstandsraster
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24

ACCENT = QColor(0xFF, 0x28, 0x00)   # mainColor aus dem Geraetemanifest


def _from_text(alpha):
    """Farbe aus der Textfarbe der Palette, mit Deckkraft abgeschwaecht.

    Die Rolle Mid taugt dafuer nicht: in hellen Designs ist sie ein
    brauchbares Grau, in dunklen aber eine dunkle Rahmenfarbe -- Text darin
    verschwindet im Hintergrund. Aus WindowText abgeleitet stimmt der
    Kontrast dagegen in beiden Richtungen.
    """
    application = QApplication.instance()
    colour = (application.palette().color(QPalette.ColorRole.WindowText)
              if application else QColor(0, 0, 0))
    return "rgba(%d, %d, %d, %.2f)" % (colour.red(), colour.green(),
                                       colour.blue(), alpha)


def muted():
    """Zurueckhaltende Textfarbe fuer Erklaerungen und Nebenangaben."""
    return _from_text(0.62)


def border():
    """Linien und Rahmen."""
    return _from_text(0.28)


def heading(text):
    """Ueberschrift eines Abschnitts."""
    label = QLabel(text)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() + 1.5)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    return label


def hint(text):
    """Erklaerender Text unter einer Ueberschrift oder einem Feld."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: %s;" % muted())
    font = label.font()
    font.setPointSizeF(max(8.0, font.pointSizeF() - 0.5))
    label.setFont(font)
    return label


def caption(text):
    """Kleine Beschriftung, etwa unter einer Vorschau."""
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("color: %s;" % muted())
    font = label.font()
    font.setPointSizeF(max(8.0, font.pointSizeF() - 0.5))
    label.setFont(font)
    return label


def primary_button(button):
    """Hebt genau die primäre Aktion einer Ansicht zurückhaltend hervor."""
    button.setProperty("primary", True)
    button.setMinimumHeight(32)
    button.setStyleSheet(
        "QPushButton[primary=\"true\"] {"
        " background: #ff2800; color: white; border: none;"
        " border-radius: 7px; padding: 5px 14px; font-weight: 600; }"
        "QPushButton[primary=\"true\"]:hover { background: #e92300; }"
        "QPushButton[primary=\"true\"]:pressed { background: #c91f00; }"
        "QPushButton[primary=\"true\"]:disabled {"
        " background: rgba(127,127,127,0.34); color: rgba(255,255,255,0.72); }"
    )
    return button


def status_badge(text):
    """Kleine Statuskapsel für eine klare, aber nicht dominante Hierarchie."""
    label = QLabel(text)
    label.setObjectName("StatusBadge")
    label.setStyleSheet(
        "QLabel#StatusBadge { color: %s; background: %s;"
        " border: 1px solid %s; border-radius: 8px; padding: 2px 8px; }"
        % (_from_text(0.76), _from_text(0.06), _from_text(0.14)))
    return label


class Spinner(QWidget):
    """Kleiner rotierender Ladeanzeiger.

    Qt bringt keinen mit; ein unbestimmter Fortschrittsbalken wäre die
    Alternative, braucht aber viel Breite. Gezeichnet wird mit der Textfarbe
    der Palette, damit er in hellen wie dunklen Designs sitzt.
    """

    SEGMENTS = 8

    def __init__(self, diameter=16, parent=None):
        super().__init__(parent)
        self._angle = 0
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.setInterval(110)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def _advance(self):
        self._angle = (self._angle + 1) % self.SEGMENTS
        self.update()

    def start(self):
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self.setVisible(False)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)

        colour = self.palette().color(QPalette.ColorRole.WindowText)
        radius = self.width() / 2
        dot = max(1.4, radius * 0.22)

        for index in range(self.SEGMENTS):
            # Der zuletzt erreichte Punkt ist am kräftigsten, die davor
            # verblassen -- das ergibt den Eindruck einer Drehrichtung.
            distance = (index - self._angle) % self.SEGMENTS
            colour.setAlphaF(0.15 + 0.85 * (1.0 - distance / self.SEGMENTS))
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.save()
            painter.rotate(index * 360.0 / self.SEGMENTS)
            painter.drawEllipse(QPointF(0, -(radius - dot)), dot, dot)
            painter.restore()


class BusyOverlay(QWidget):
    """Modal wirkende Sperrebene für laufende Hardwareoperationen.

    Die kurze Überblendung vermittelt Tiefe, ohne den Inhalt mit Bewegung zu
    überlagern. Das Widget selbst nimmt alle Maus- und Tastatureingaben an und
    verhindert so konkurrierende Gerätebefehle.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        window = self.palette().color(QPalette.ColorRole.Window)
        scrim_alpha = 0.24 if window.lightness() < 128 else 0.12
        self.setStyleSheet(
            "BusyOverlay { background: rgba(0, 0, 0, %.2f); }"
            % scrim_alpha)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("BusyCard")
        background = self.palette().color(QPalette.ColorRole.Window)
        card.setStyleSheet(
            "QFrame#BusyCard { background: rgba(%d,%d,%d,0.97);"
            " border: 1px solid %s; border-radius: 12px; }"
            % (background.red(), background.green(), background.blue(),
               _from_text(0.16)))
        card.setMinimumWidth(360)
        card.setMaximumWidth(420)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        card_layout.setSpacing(SPACE_M)
        row = QHBoxLayout()
        row.setSpacing(SPACE_M)
        self.spinner = Spinner(20)
        row.addWidget(self.spinner)
        self.label = QLabel()
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        card_layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        card_layout.addWidget(self.progress)
        outer.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addStretch(1)

        self.hide()

    def show_message(self, text):
        self.label.setText(text)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.spinner.start()
        self.progress.hide()

    def set_progress(self, value, maximum):
        if maximum <= 0 or not self.isVisible():
            return
        self.spinner.stop()
        self.progress.setRange(0, maximum)
        self.progress.setValue(min(value, maximum))
        self.progress.show()
        self.raise_()

    def finish(self):
        if not self.isVisible():
            return
        self.spinner.stop()
        self.progress.hide()
        self.hide()


def separator():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet("color: %s;" % border())
    line.setMaximumHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line
