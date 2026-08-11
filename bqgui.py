#!/usr/bin/env python3
"""
IO Center für Linux -- Oberfläche für die be quiet! Dark Mount.

Drei Bereiche:

  Tasten        die acht Display Keys über dem Numblock -- Bild, virtuelle
                Taste (F13..F24) und Kommando je Taste
  Media-Dock    Leerlaufgrafik, Menüfarbe, Anzeigemodus, Zeitlimits
  Beleuchtung   Onboard-Effekte, die auch ohne Rechner weiterlaufen

SICHERHEIT
    Alle Gerätezugriffe laufen über bqkeyd, bqimage, bqdock, bqdevice und
    bqlight.
    Jedes dieser Module führt eine eigene Whitelist erlaubter Kommandos,
    die durch Mitschnitte, den offenen HID-Standard oder einen zusätzlich am
    Gerät verifizierten reinen Leseweg belegt sind.
    Firmware-Funktionen sind nicht implementiert und über diesen Weg auch
    nicht erreichbar -- der Bootloader meldet sich als eigenes USB-Gerät.
    Für Firmware-Updates bleibt https://iocenter.bequiet.com/ zuständig.
"""

import configparser
import glob
import importlib.metadata
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QTimer, QUrl, QSize,
                          QBuffer, QIODevice, QSettings)
from PyQt6.QtGui import (QPalette, QColor, QDesktopServices, QIcon, QPixmap,
                         QTransform, QFont)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QLineEdit, QComboBox, QFormLayout,
    QMessageBox, QStatusBar, QSizePolicy, QDialog, QListWidget, QListWidgetItem,
    QDialogButtonBox, QFileDialog, QSlider, QTabWidget, QColorDialog,
    QSpinBox, QCheckBox, QButtonGroup, QRadioButton, QProgressBar, QListView,
    QScrollArea, QPlainTextEdit,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqkeyd   # noqa: E402  -- HID-Logik wird geteilt, nicht dupliziert
import bqimage  # noqa: E402  -- Tastenbilder (Whitelist 0x20 0x02/0x03)
import bqdock   # noqa: E402  -- Media-Dock (Whitelist 0x21 ...)
import bqdevice  # noqa: E402  -- Geräteinformationen (nur 0x03 0x01/0x02)
import bqlight  # noqa: E402  -- Beleuchtung (Whitelist 0x10 0x06)
import bqlamp   # noqa: E402  -- Einzeltasten über HID LampArray
import bqmeter  # noqa: E402  -- CPU/GPU-Meter über HID LampArray
import bqconfig  # noqa: E402  -- config.toml verlustfrei/atomar schreiben
import bqmeta  # noqa: E402  -- App-ID, Version und Lizenz
import bqpaths  # noqa: E402  -- schreibbare XDG-Pfade und Migration
from bqui import (SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL, ACCENT,
                  heading, hint, caption, separator, muted,
                  border, primary_button, status_badge,
                  BusyOverlay)  # noqa: E402
import bqi18n  # noqa: E402
from bqi18n import tr  # noqa: E402

CONFIG_PATH = bqkeyd.DEFAULT_CONFIG
SERVICE = "bqkeyd.service"
NO_VKEY = "(keine)"          # interner Schlüssel, Anzeige läuft über tr()
VKEY_CHOICES = [NO_VKEY] + ["F%d" % n for n in range(13, 25)]
BACKUP_DIR = bqpaths.BACKUP_DIR
DOCK_BACKUP_DIR = bqpaths.DOCK_BACKUP_DIR
SAFETY_NOTICE_VERSION = 1
SAFETY_NOTICE_KEY = "first_run/safety_notice_version"
DISCORD_USER = "fechyyyyy"
UDEV_RULE_NAME = "70-iocenter-dark-mount.rules"
UDEV_RULE_DIRS = ("/etc/udev/rules.d", "/usr/lib/udev/rules.d",
                  "/lib/udev/rules.d")


def app_icon():
    icon = QIcon.fromTheme(bqmeta.APP_ID)
    if not icon.isNull():
        return icon
    candidates = (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                     bqmeta.APP_ID + ".svg"),
        os.path.join("/usr/share/icons/hicolor/scalable/apps",
                     bqmeta.APP_ID + ".svg"),
        os.path.join(bqpaths.DATA_HOME, "icons", "hicolor", "scalable",
                     "apps", bqmeta.APP_ID + ".svg"),
    )
    return next((QIcon(path) for path in candidates if os.path.isfile(path)),
                QIcon())


def license_text():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "LICENSE"),
        "/usr/share/licenses/iocenter-linux/LICENSE",
        "/usr/share/doc/iocenter-linux/copyright",
    ]
    try:
        distribution = importlib.metadata.distribution("iocenter-linux")
        candidates.extend(
            str(distribution.locate_file(item))
            for item in (distribution.files or ())
            if str(item).endswith("/licenses/LICENSE"))
    except importlib.metadata.PackageNotFoundError:
        pass
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            continue
    return tr("Der vollständige Lizenztext wurde nicht gefunden. Siehe %s") \
        % bqmeta.PROJECT_URL


def app_settings():
    """Stabile Benutzereinstellungen, unabhängig von Sprache und Startpfad."""
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                     "iocenter-linux", "iocenter-linux")


def safety_notice_accepted(settings):
    """Nur die aktuelle oder eine neuere Fassung gilt als bestätigt."""
    try:
        version = int(settings.value(SAFETY_NOTICE_KEY, 0))
    except (TypeError, ValueError):
        return False
    return version >= SAFETY_NOTICE_VERSION


def remember_safety_notice(settings):
    settings.setValue(SAFETY_NOTICE_KEY, SAFETY_NOTICE_VERSION)
    settings.sync()


def udev_diagnostics():
    """Prüft die wirksamen Rechte; ein bestimmter Regelname allein genügt nicht."""
    vendor_nodes = bqkeyd.find_vendor_node()
    lamp_nodes = bqlamp.find_lamparray_node()
    nodes = sorted(set(vendor_nodes + lamp_nodes))
    inaccessible = [node for node in nodes
                    if not os.access("/dev/" + node, os.R_OK | os.W_OK)]
    installed_rules = [os.path.join(directory, UDEV_RULE_NAME)
                       for directory in UDEV_RULE_DIRS
                       if os.path.isfile(os.path.join(directory,
                                                      UDEV_RULE_NAME))]
    uinput_exists = os.path.exists("/dev/uinput")
    return {
        "vendor_nodes": vendor_nodes,
        "lamp_nodes": lamp_nodes,
        "nodes": nodes,
        "inaccessible": inaccessible,
        "hidraw_ready": bool(vendor_nodes) and bool(lamp_nodes)
                            and not inaccessible,
        "rule_paths": installed_rules,
        "uinput_exists": uinput_exists,
        "uinput_ready": uinput_exists and os.access("/dev/uinput", os.W_OK),
    }


def udev_install_command():
    """Expliziter, prüfbarer Terminalbefehl statt versteckter Root-Aktion."""
    source = udev_rule_source()
    target = "/etc/udev/rules.d/" + UDEV_RULE_NAME
    return "\n".join((
        "sudo install -Dm644 %s %s" % (shlex.quote(source),
                                        shlex.quote(target)),
        "sudo udevadm control --reload-rules",
        "sudo udevadm trigger",
    ))


def udev_rule_source():
    candidates = (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), UDEV_RULE_NAME),
        os.path.join(bqpaths.DATA_DIR, UDEV_RULE_NAME),
        os.path.join("/usr/share/iocenter-linux", UDEV_RULE_NAME),
        os.path.join("/usr/lib/udev/rules.d", UDEV_RULE_NAME),
        os.path.join("/lib/udev/rules.d", UDEV_RULE_NAME),
    )
    return next((path for path in candidates if os.path.isfile(path)),
                candidates[0])


def udev_remove_command():
    """Entfernt ausschließlich die von der Quellinstallation benannte Regel."""
    target = "/etc/udev/rules.d/" + UDEV_RULE_NAME
    return "\n".join((
        "sudo rm -- %s" % shlex.quote(target),
        "sudo udevadm control --reload-rules",
        "sudo udevadm trigger",
    ))


def access_setup_reasons(status, uinput_required=False):
    """Probleme, die eine sichtbare Einrichtungshilfe rechtfertigen."""
    reasons = []
    if status["nodes"] and not status["hidraw_ready"]:
        reasons.append("hidraw")
    if uinput_required and not status["uinput_ready"]:
        reasons.append("uinput")
    return reasons


def systemd_quote(value):
    """Ein einzelnes Argument für ExecStart/WorkingDirectory maskieren."""
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    # Prozentzeichen leiten in Units systemd-Specifier ein.
    return '"%s"' % value.replace("%", "%%")


def systemd_path(value):
    """Pfad für direktive Werte wie WorkingDirectory= maskieren."""
    value = str(value).replace("\\", "\\x5c")
    value = value.replace(" ", "\\x20").replace("\t", "\\x09")
    return value.replace("%", "%%")


def service_unit_text():
    """Portable User-Unit für genau diese Projektkopie erzeugen."""
    project = os.path.dirname(os.path.abspath(__file__))
    daemon = os.path.join(project, "bqkeyd.py")
    return """[Unit]
Description=be quiet! Dark Mount Display-Key-Daemon

[Service]
Type=simple
WorkingDirectory={project}
ExecStart={python} {daemon} -c {config}
Restart=always
RestartSec=3

# Der Daemon öffnet Vendor-HID nur lesend. Desktop-Kommandos starten über
# systemd-run --user in einer frischen, nicht geerbten Unit.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX
MemoryDenyWriteExecute=true

[Install]
WantedBy=default.target
""".format(project=systemd_path(project),
           python=systemd_quote(sys.executable),
           daemon=systemd_quote(daemon),
           config=systemd_quote(CONFIG_PATH))


# ------------------------------------------------------- Anwendungs-Auswahl ---

# Feldcodes aus der Desktop Entry Specification (%f, %U, %i, ...) -- die
# ersetzt sonst der Dateimanager beim Start; hier sind sie nur Müll.
FIELD_CODES = re.compile(r"\s*%[fFuUdDnNickvm]")


def terminal_command():
    """Terminal-Emulator für Einträge mit Terminal=true."""
    for candidate, args in (("konsole", ["-e"]), ("alacritty", ["-e"]),
                            ("foot", []), ("xterm", ["-e"])):
        if shutil.which(candidate):
            return [candidate] + args
    return None


def load_desktop_apps():
    """Liest installierte Anwendungen aus den .desktop-Verzeichnissen."""
    data_dirs = os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = os.environ.get(
        "XDG_DATA_HOME", os.path.expanduser("~/.local/share"))

    apps, seen = [], set()
    term = terminal_command()

    for base in [home] + data_dirs:
        pattern = os.path.join(base, "applications", "**", "*.desktop")
        for path in glob.glob(pattern, recursive=True):
            parser = configparser.RawConfigParser(strict=False)
            parser.optionxform = str
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, OSError, UnicodeDecodeError):
                continue

            if entry.get("Type") != "Application":
                continue
            if entry.get("NoDisplay", "false").lower() == "true":
                continue
            if entry.get("Hidden", "false").lower() == "true":
                continue

            name = entry.get("Name", "").strip()
            command = FIELD_CODES.sub("", entry.get("Exec", "")).strip()
            if not name or not command:
                continue

            if entry.get("Terminal", "false").lower() == "true":
                if term is None:
                    continue
                command = " ".join(term) + " " + command

            key = (name.lower(), command)
            if key in seen:
                continue
            seen.add(key)
            apps.append({"name": name, "command": command,
                         "icon": entry.get("Icon", ""),
                         "comment": entry.get("Comment", "").strip()})

    apps.sort(key=lambda a: a["name"].lower())
    return apps


_ICON_INDEX = None


def system_icon_index():
    """Anwendungsicons der lokalen Icon-Themes, nach Namen indiziert.

    Es werden nur ``apps``-Verzeichnisse berücksichtigt. So umfasst die Suche
    auch große installierte Icon-Pakete, ohne tausende Toolbar- und
    Statussymbole in die Auswahl zu mischen.
    """
    global _ICON_INDEX
    if _ICON_INDEX is not None:
        return _ICON_INDEX

    roots = [
        os.path.expanduser("~/.local/share/icons"),
        os.path.expanduser("~/.icons"),
        os.path.expanduser("~/.local/share/flatpak/exports/share/icons"),
        "/usr/local/share/icons",
        "/usr/share/icons",
        "/var/lib/flatpak/exports/share/icons",
    ]
    index = {}

    def quality(path):
        lower = path.lower()
        extension = os.path.splitext(lower)[1]
        score = 300 if extension == ".svg" else 0
        match = re.search(r"/(\d+)x\d+/", lower)
        if match:
            score += min(1024, int(match.group(1)))
        if "/scalable/" in lower:
            score += 1500
        if "/hicolor/" in lower:
            score += 200
        if "/char-white/" in lower:
            score -= 800
        if "-symbolic" in lower:
            score -= 200
        return score

    for root in roots:
        if not os.path.isdir(root):
            continue
        for directory, _subdirectories, filenames in os.walk(root):
            if "apps" not in directory.split(os.sep):
                continue
            for filename in filenames:
                if not filename.lower().endswith((".svg", ".png", ".xpm")):
                    continue
                path = os.path.join(directory, filename)
                name = os.path.splitext(os.path.basename(path))[0]
                key = name.casefold()
                previous = index.get(key)
                if previous is None or quality(path) > quality(previous):
                    index[key] = path
    _ICON_INDEX = index
    return index


def application_icon(specification):
    """Theme-Namen und absolute Icon-Pfade zuverlässig auflösen."""
    if not specification:
        return QIcon()
    specification = specification.strip()
    if os.path.isfile(specification):
        return QIcon(specification)
    icon = QIcon.fromTheme(specification)
    if not icon.isNull():
        return icon
    key = os.path.splitext(os.path.basename(specification))[0].casefold()
    path = system_icon_index().get(key)
    return QIcon(path) if path else QIcon()


class AppPickerDialog(QDialog):
    """Auswahlliste der installierten Anwendungen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Anwendung auswählen"))
        self.resize(460, 520)
        self.chosen = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Suchen…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_items)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        self.list.setSpacing(1)
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        self.list.currentItemChanged.connect(
            lambda item, _: self.ok_button.setEnabled(item is not None))

        for app in load_desktop_apps():
            item = QListWidgetItem(app["name"])
            if app["icon"]:
                item.setIcon(application_icon(app["icon"]))
            tooltip = app["command"]
            if app["comment"]:
                tooltip = "%s\n\n%s" % (app["comment"], app["command"])
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.UserRole, app["command"])
            self.list.addItem(item)

        self.search.setFocus()

    def filter_items(self, text):
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(not (needle in item.text().lower()
                                or needle in item.toolTip().lower()))

    def accept(self):
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        super().accept()


class IconPickerDialog(QDialog):
    """Galerie installierter Apps plus Suche in lokalen Icon-Paketen."""

    MAX_RESULTS = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("App-Icon auswählen"))
        self.resize(680, 560)
        self.chosen_icon = None
        self.chosen_name = ""
        self.apps = load_desktop_apps()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_M)
        layout.addWidget(heading(tr("Icon-Galerie")))
        layout.addWidget(hint(tr(
            "Installierte Anwendungen werden direkt angezeigt. Die Suche "
            "findet zusätzlich Symbole aus deinen lokalen Icon-Paketen.")))

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Apps und Symbole suchen …"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.populate)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setIconSize(QSize(56, 56))
        self.list.setGridSize(QSize(118, 94))
        self.list.setWordWrap(True)
        self.list.setSpacing(3)
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list, 1)

        self.result_label = hint("")
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        self.list.currentItemChanged.connect(
            lambda item, _old: self.ok_button.setEnabled(item is not None))
        layout.addWidget(buttons)

        self.populate("")
        self.search.setFocus()

    @staticmethod
    def _friendly_name(name):
        name = re.sub(r"-symbolic$", "", name, flags=re.IGNORECASE)
        return name.replace("_", " ").replace("-", " ").strip()

    def _add(self, name, icon, source, seen):
        identity = source.casefold()
        label_identity = "label:" + re.sub(r"\W+", "", name.casefold())
        if icon.isNull() or identity in seen or label_identity in seen:
            return False
        item = QListWidgetItem(icon, name)
        item.setData(Qt.ItemDataRole.UserRole, source)
        item.setToolTip(source)
        self.list.addItem(item)
        seen.update((identity, label_identity))
        return True

    def populate(self, text):
        query = text.strip().casefold()
        self.list.clear()
        if hasattr(self, "ok_button"):
            self.ok_button.setEnabled(False)
        seen = set()
        count = 0

        for app in self.apps:
            haystack = "%s %s" % (app["name"], app["icon"])
            if query and query not in haystack.casefold():
                continue
            if self._add(app["name"], application_icon(app["icon"]),
                         app["icon"] or app["name"], seen):
                count += 1
            if count >= self.MAX_RESULTS:
                break

        if len(query) >= 2 and count < self.MAX_RESULTS:
            normalized_query = re.sub(r"[-_.]+", " ", query)
            for key, path in sorted(system_icon_index().items()):
                normalized_key = re.sub(r"[-_.]+", " ", key)
                if (query not in key and
                        normalized_query not in normalized_key):
                    continue
                if self._add(self._friendly_name(key), QIcon(path), key, seen):
                    count += 1
                if count >= self.MAX_RESULTS:
                    break

        self.result_label.setText(
            tr("%d Icons gefunden") % count if count
            else tr("Keine passenden Icons gefunden"))

    def accept(self):
        item = self.list.currentItem()
        if item is None:
            return
        self.chosen_icon = item.icon()
        self.chosen_name = item.text()
        super().accept()


# --------------------------------------------------------------- HID-Threads ---

class HidListener(QThread):
    """Liest den Vendor-Kanal passiv und meldet Tastendrücke."""

    pressed = pyqtSignal(int)
    status = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self._running = True

    def run(self):
        import select
        fd = node = None
        while self._running:
            if fd is None:
                nodes = bqkeyd.find_vendor_node()
                if not nodes:
                    self.status.emit(tr("Keine Tastatur gefunden"), False)
                    self.msleep(2000)
                    continue
                try:
                    fd = os.open("/dev/" + nodes[0], os.O_RDONLY | os.O_NONBLOCK)
                    node = nodes[0]
                except OSError as exc:
                    self.status.emit(tr("Kein Zugriff auf /dev/%s (%s)")
                                     % (nodes[0], exc), False)
                    self.msleep(3000)
                    continue
                self.status.emit(tr("Verbunden über /dev/%s") % node, True)

            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                data = os.read(fd, 512)
            except BlockingIOError:
                continue
            except OSError:
                os.close(fd)
                fd = None
                self.status.emit(tr("Tastatur getrennt"), False)
                continue
            if not data:
                continue
            event = bqkeyd.parse_event(data)
            if event and event[0] in bqkeyd.DISPLAY_KEYS:
                self.pressed.emit(event[0])

        if fd is not None:
            os.close(fd)

    def stop(self):
        self._running = False
        return self.wait(3500)


class ImageLoader(QThread):
    """Liest die Tastenbilder -- sendet ausschließlich 0x20 0x03."""

    loaded = pyqtSignal(int, bytes)
    progress = pyqtSignal(str)
    progress_value = pyqtSignal(int, int)
    finished_all = pyqtSignal(int, str)

    def __init__(self, key_ids):
        super().__init__()
        self.key_ids = list(key_ids)
        self._cancel = False

    def run(self):
        try:
            kb = bqimage.Keyboard()
        except (SystemExit, OSError) as exc:
            self.finished_all.emit(0, tr("Gerät nicht verfügbar: %s") % exc)
            return

        ok, error = 0, ""
        try:
            total = len(self.key_ids)
            steps_per_image = 1000
            total_steps = total * steps_per_image
            for index, key_id in enumerate(self.key_ids, start=1):
                if self._cancel:
                    break
                slot = key_id - bqkeyd.FIRST_KEY + 1
                self.progress.emit(tr("Lese Bild von Taste %d …") % slot)
                base = (index - 1) * steps_per_image
                self.progress_value.emit(base, total_steps)

                def image_progress(done, image_total, start=base):
                    # Jedes Bild bekommt denselben Anteil am Gesamtbalken.
                    # Innerhalb dieses Anteils folgen wir den gelesenen Bytes,
                    # statt nur einmal pro fertiger Taste weiterzuspringen.
                    fraction = min(1.0, done / max(1, image_total))
                    value = start + round(fraction * steps_per_image)
                    self.progress_value.emit(value, total_steps)

                try:
                    jpeg, _w, _h, _f = kb.read_image(
                        key_id, progress=image_progress)
                except (TimeoutError, IOError, OSError) as exc:
                    error = "Taste %d: %s" % (slot, exc)
                    continue
                self.loaded.emit(key_id, jpeg)
                ok += 1
                self.progress_value.emit(index * steps_per_image, total_steps)
        finally:
            kb.close()
        self.finished_all.emit(ok, error)

    def cancel(self):
        self._cancel = True


class ImageWriter(QThread):
    """Schreibt ein Tastenbild -- sichert vorher das bisherige."""

    progress = pyqtSignal(str)
    done = pyqtSignal(bool, str, bytes)

    def __init__(self, key_id, jpeg):
        super().__init__()
        self.key_id = key_id
        self.jpeg = jpeg

    def run(self):
        slot = self.key_id - bqkeyd.FIRST_KEY + 1
        try:
            kb = bqimage.Keyboard()
        except (SystemExit, OSError) as exc:
            self.done.emit(False, tr("Gerät nicht verfügbar: %s") % exc, b"")
            return
        try:
            self.progress.emit(tr("Sichere bisheriges Bild von Taste %d …") % slot)
            old, _w, _h, _f = kb.read_image(self.key_id)
            os.makedirs(BACKUP_DIR, exist_ok=True)
            path = os.path.join(BACKUP_DIR, "key%d-%s.jpg"
                                % (slot, time.strftime("%Y%m%d-%H%M%S")))
            with open(path, "wb") as fh:
                fh.write(old)

            self.progress.emit(tr("Schreibe auf Taste %d …") % slot)
            kb.write_image(self.key_id, self.jpeg)

            self.progress.emit(tr("Prüfe durch Zurücklesen …"))
            check, _w, _h, _f = kb.read_image(self.key_id)
            if check == self.jpeg:
                self.done.emit(True, tr("Taste %d geändert und geprüft.") % slot,
                               check)
            else:
                self.done.emit(False, tr("Abweichung beim Zurücklesen. Sicherung: "
                                      "%s") % path, check)
        except (TimeoutError, IOError, OSError, ValueError) as exc:
            self.done.emit(False, tr("Fehlgeschlagen: %s") % exc, b"")
        finally:
            kb.close()


class DockImageWriter(QThread):
    """Sichert, schreibt und verifiziert die Leerlaufgrafik des Docks."""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(bool, str)

    def __init__(self, rgb565):
        super().__init__()
        self.rgb565 = rgb565

    def run(self):
        try:
            dock = bqdock.Dock()
        except (SystemExit, OSError) as exc:
            self.done.emit(False, tr("Gerät nicht verfügbar: %s") % exc)
            return
        backup = None
        total_work = bqdock.IMAGE_BYTES * 3
        try:
            old = dock.read_image(
                lambda done, _total: self.progress.emit(done, total_work))
            os.makedirs(DOCK_BACKUP_DIR, exist_ok=True)
            backup = os.path.join(
                DOCK_BACKUP_DIR,
                "dock-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
            bqdock.from_rgb565(old).save(backup, format="PNG")

            dock.write_image(
                self.rgb565,
                lambda done, _total: self.progress.emit(
                    bqdock.IMAGE_BYTES + done, total_work))
            check = dock.read_image(
                lambda done, _total: self.progress.emit(
                    bqdock.IMAGE_BYTES * 2 + done, total_work))
            if check != self.rgb565:
                self.done.emit(
                    False,
                    tr("Abweichung beim Zurücklesen. Sicherung: %s") % backup)
                return
            self.done.emit(
                True,
                tr("Leerlaufgrafik gesichert, übertragen und geprüft. "
                   "Sicherung: %s") % backup)
        except (TimeoutError, IOError, OSError, ValueError, RuntimeError) as exc:
            suffix = (tr(" Sicherung: %s") % backup) if backup else ""
            self.done.emit(False, "%s%s" % (exc, suffix))
        finally:
            dock.close()


class DockImageReader(QThread):
    """Liest die aktuelle Leerlaufgrafik des Docks (nur 0x21 0x06)."""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(bool, str, bytes)

    def run(self):
        try:
            dock = bqdock.Dock()
        except (SystemExit, OSError) as exc:
            self.done.emit(False, tr("Gerät nicht verfügbar: %s") % exc, b"")
            return
        try:
            rgb565 = dock.read_image(
                lambda done, total: self.progress.emit(done, total))
            self.done.emit(True, tr("Leerlaufgrafik vom Dock gelesen."), rgb565)
        except (TimeoutError, IOError, OSError, ValueError) as exc:
            self.done.emit(False, str(exc), b"")
        finally:
            dock.close()


class Worker(QThread):
    """Kurze Geräteaufgaben, die die Oberfläche nicht blockieren sollen."""

    done = pyqtSignal(bool, str, object)

    def __init__(self, function, message="Fertig."):
        super().__init__()
        self.function = function
        self.message = message

    def run(self):
        try:
            result = self.function()
        except (TimeoutError, IOError, OSError, ValueError, SystemExit,
                RuntimeError) as exc:
            self.done.emit(False, str(exc), None)
        else:
            self.done.emit(True, self.message, result)


class LoadMeterThread(QThread):
    """Laufender CPU/GPU-Monitor; gibt LampArray beim Stoppen zurück."""

    ready = pyqtSignal(str, str)
    sampled = pyqtSignal(float, float)
    done = pyqtSignal(bool, str)

    def __init__(self, mode="dot", interval=1.0, swap=False):
        super().__init__()
        self.mode = mode
        self.interval = interval
        self.swap = swap
        self._stop = threading.Event()

    def run(self):
        meter = bqmeter.LoadMeter(interval=self.interval, mode=self.mode,
                                  swap=self.swap)
        try:
            meter.run(
                stop_event=self._stop,
                on_ready=lambda path, source, _gpu, _cpu:
                    self.ready.emit(path, source),
                on_sample=lambda gpu, cpu: self.sampled.emit(gpu, cpu))
        except (OSError, ValueError, RuntimeError, SystemExit) as exc:
            self.done.emit(False, str(exc))
        else:
            self.done.emit(True, tr("Monitor beendet; Onboard-Beleuchtung "
                                    "wieder aktiv."))

    def stop(self):
        self._stop.set()
        return self.wait(5000)


# ------------------------------------------------------------------ Dialoge ---

class ImageDialog(QDialog):
    """Bild für eine Display-Taste wählen, zuschneiden und ansehen."""

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Bild für Taste %d") % slot)
        self.source = None
        self.jpeg = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_L)
        layout.setSpacing(SPACE_M)

        self.preview = QLabel(tr("Noch kein Bild gewählt"))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(280, 280)
        self.preview.setStyleSheet(
            "border: 1px solid %s; border-radius: 6px; color: %s;"
            % (border(), muted()))
        layout.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption(tr("So erscheint das Bild auf der Taste "
                                 "(120 × 120)")))

        layout.addSpacing(SPACE_S)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(SPACE_S)
        pick = QPushButton(tr("Bilddatei wählen …"))
        pick.clicked.connect(self.choose_file)
        pick_row.addWidget(pick, 1)
        pick_icon = QPushButton(tr("App-Icon auswählen …"))
        pick_icon.clicked.connect(self.choose_app_icon)
        pick_row.addWidget(pick_icon, 1)
        layout.addLayout(pick_row)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(SPACE_M)
        zoom_row.addWidget(QLabel(tr("Ausschnitt")))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(10, 40)
        self.zoom.setValue(10)
        self.zoom.setEnabled(False)
        self.zoom.valueChanged.connect(self.update_preview)
        zoom_row.addWidget(self.zoom, 1)
        self.zoom_label = QLabel("1,0×")
        self.zoom_label.setMinimumWidth(40)
        zoom_row.addWidget(self.zoom_label)
        layout.addLayout(zoom_row)

        self.info = hint(" ")
        layout.addWidget(self.info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr("Auf Taste schreiben"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Bild wählen"), os.path.expanduser("~"),
            tr("Bilder (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;Alle Dateien (*)"))
        if not path:
            return
        self.source = path
        self.zoom.setEnabled(True)
        self.update_preview()

    def choose_app_icon(self):
        dialog = IconPickerDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen_icon is None:
            return

        pixmap = dialog.chosen_icon.pixmap(QSize(384, 384))
        if pixmap.isNull():
            self.info.setText(tr("Icon kann nicht gelesen werden."))
            return
        qt_buffer = QBuffer()
        qt_buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(qt_buffer, "PNG"):
            self.info.setText(tr("Icon kann nicht gelesen werden."))
            return

        from PIL import Image
        foreground = Image.open(io.BytesIO(bytes(qt_buffer.data()))).convert("RGBA")
        foreground.thumbnail((360, 360), Image.Resampling.LANCZOS)
        # Transparente Theme-Icons bekommen einen ruhigen, dunklen Grund. Das
        # hält auch helle/weiße Symbole auf dem Tastendisplay klar lesbar.
        canvas = Image.new("RGBA", (512, 512), (25, 26, 29, 255))
        position = ((canvas.width - foreground.width) // 2,
                    (canvas.height - foreground.height) // 2)
        canvas.alpha_composite(foreground, position)
        self.source = canvas.convert("RGB")
        self.zoom.setValue(10)
        self.zoom.setEnabled(True)
        self.update_preview()

    def update_preview(self):
        if not self.source:
            return
        zoom = self.zoom.value() / 10.0
        self.zoom_label.setText(("%.1f×" % zoom).replace(".", ","))
        try:
            image = bqimage.render_for_key(self.source, zoom)
            self.jpeg, quality = bqimage.encode_for_key(self.source, zoom)
        except Exception as exc:
            self.info.setText(tr("Bild kann nicht gelesen werden: %s") % exc)
            self.ok_button.setEnabled(False)
            return

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue(), "PNG")
        self.preview.setPixmap(pixmap.scaled(
            260, 260, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation))
        self.preview.setStyleSheet("border-radius: 6px;")
        self.info.setText(tr("%d Byte bei Qualität %d — Grenze %d Byte")
                          % (len(self.jpeg), quality, bqimage.MAX_IMAGE_BYTES))
        self.ok_button.setEnabled(True)


# ------------------------------------------------------------------- Kachel ---

class KeyTile(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, slot, key_id):
        super().__init__()
        self.slot = slot
        self.key_id = key_id
        self._active = False
        self._hovered = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("Taste %d") % slot)
        self.setMinimumSize(168, 116)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

        self.thumb = QLabel(str(slot))
        self.thumb.setFixedSize(64, 64)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(
            "border: 1px solid %s; border-radius: 5px; color: %s;"
            % (border(), muted()))

        self.title = QLabel(tr("Taste %d") % slot)
        font = self.title.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)

        self.binding = QLabel(tr("nicht belegt"))
        self.binding.setWordWrap(True)
        self.binding.setStyleSheet("color: %s;" % muted())
        small = self.binding.font()
        small.setPointSizeF(max(8.0, small.pointSizeF() - 0.5))
        self.binding.setFont(small)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        text_column.addWidget(self.title)
        text_column.addWidget(self.binding)
        text_column.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        layout.setSpacing(SPACE_M)
        layout.addWidget(self.thumb, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_column, 1)

        self._paint(False)

    def _paint(self, active):
        if active:
            colour = self.palette().color(QPalette.ColorRole.Highlight)
            self.setStyleSheet(
                "KeyTile { border: 1px solid %s; border-radius: 7px;"
                " background: rgba(%d,%d,%d,0.13); }"
                % (colour.name(), colour.red(), colour.green(), colour.blue()))
        elif self._hovered or self.hasFocus():
            colour = self.palette().color(QPalette.ColorRole.Highlight)
            self.setStyleSheet(
                "KeyTile { border: 1px solid rgba(%d,%d,%d,0.58);"
                " border-radius: 7px; background: rgba(%d,%d,%d,0.06); }"
                % (colour.red(), colour.green(), colour.blue(),
                   colour.red(), colour.green(), colour.blue()))
        else:
            self.setStyleSheet(
                "KeyTile { border: 1px solid %s; border-radius: 7px; }"
                % border())

    def set_selected(self, selected):
        self._active = selected
        self._paint(selected)

    def flash(self):
        self.setStyleSheet(
            "KeyTile { border: 1px solid %s; border-radius: 7px;"
            " background: rgba(%d,%d,%d,0.22); }"
            % (ACCENT.name(), ACCENT.red(), ACCENT.green(), ACCENT.blue()))
        QTimer.singleShot(280, lambda: self._paint(self._active))

    def set_image(self, jpeg):
        """JPEG vom Gerät anzeigen -- die Displays sind gedreht verbaut."""
        pixmap = QPixmap()
        if not pixmap.loadFromData(jpeg, "JPEG"):
            return
        pixmap = pixmap.transformed(QTransform().rotate(-90),
                                    Qt.TransformationMode.SmoothTransformation)
        self.thumb.setPixmap(pixmap.scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self.thumb.setStyleSheet("border-radius: 5px;")

    def set_summary(self, vkey, command, label):
        self.title.setText(label or tr("Taste %d") % self.slot)
        parts = []
        if vkey:
            parts.append(vkey)
        if command:
            text = command if isinstance(command, str) else " ".join(command)
            parts.append(text if len(text) <= 30 else text[:29] + "…")
        self.binding.setText("  ·  ".join(parts) if parts else tr("nicht belegt"))
        self.setAccessibleName("%s, %s" % (self.title.text(),
                                            self.binding.text()))

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.clicked.emit(self.key_id)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.key_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._paint(self._active)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._paint(self._active)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._paint(self._active)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._paint(self._active)
        super().focusOutEvent(event)


# ------------------------------------------------------------- Seite: Tasten ---

class KeysPage(QWidget):
    status = pyqtSignal(str, int)
    busy = pyqtSignal(bool, str)
    progress_changed = pyqtSignal(int, int)

    def __init__(self, window):
        super().__init__()
        self.window = window
        (self.commands, self.vkeys, self.labels,
         self.uinput_enabled) = bqkeyd.load_config(CONFIG_PATH)
        # Früher wurden übersetzte Standardnamen dauerhaft gespeichert. Sie
        # sind keine echten Benutzernamen und sollen der UI-Sprache folgen.
        for slot, key_id in enumerate(bqkeyd.DISPLAY_KEYS, start=1):
            if self.labels.get(key_id) in {
                    "Taste %d" % slot, "Key %d" % slot, "key%d" % slot}:
                self.labels.pop(key_id, None)
        self.selected = None
        self.tiles = {}
        self._loader = None
        self._writer = None
        self._pending_images = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        outer.setSpacing(SPACE_L)

        header = QHBoxLayout()
        header.setSpacing(SPACE_L)
        intro = QVBoxLayout()
        intro.setSpacing(2)
        intro.addWidget(heading(tr("Display Keys")))
        intro.addWidget(hint(tr("Drücke eine Taste — die zugehörige Kachel "
                             "leuchtet auf. Zum Belegen anklicken.")))
        header.addLayout(intro, 1)

        self.load_images_button = QPushButton(tr("Tastenbilder laden"))
        self.load_images_button.setToolTip(
            tr("Liest die Bilder von der Tastatur (nur lesend, 0x20 0x03)."))
        self.load_images_button.clicked.connect(self.load_images)
        header.addWidget(self.load_images_button,
                         alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(SPACE_M)
        for slot, key_id in enumerate(bqkeyd.DISPLAY_KEYS, start=1):
            tile = KeyTile(slot, key_id)
            tile.clicked.connect(self.select_key)
            grid.addWidget(tile, (slot - 1) // 4, (slot - 1) % 4)
            self.tiles[key_id] = tile
        outer.addLayout(grid)

        outer.addWidget(separator())
        outer.addWidget(self._build_editor())
        outer.addStretch(1)

        self.refresh_tiles()
        self.set_editor_enabled(False)

    # ---- Aufbau ----

    def _build_editor(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_M)

        title_row = QHBoxLayout()
        self.editor_title = heading(tr("Keine Taste ausgewählt"))
        title_row.addWidget(self.editor_title, 1)
        self.change_image_button = QPushButton(tr("Bild ändern …"))
        self.change_image_button.setToolTip(
            tr("Bild dieser Taste ersetzen. Das bisherige wird gesichert und das "
            "neue nach dem Schreiben zurückgelesen und geprüft."))
        self.change_image_button.clicked.connect(self.change_image)
        title_row.addWidget(self.change_image_button)
        layout.addLayout(title_row)

        form = QFormLayout()
        form.setHorizontalSpacing(SPACE_L)
        form.setVerticalSpacing(SPACE_M)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(tr("z. B. Screenshot"))
        form.addRow(tr("Name"), self.label_edit)

        self.vkey_combo = QComboBox()
        self.vkey_combo.addItems([tr(NO_VKEY)] + VKEY_CHOICES[1:])
        self.vkey_combo.setToolTip(
            tr("Erzeugt beim Druck eine virtuelle Taste. Die lässt sich dann in "
            "den Systemeinstellungen unter Kurzbefehle aufzeichnen."))
        form.addRow(tr("Virtuelle Taste"), self.vkey_combo)

        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText(tr("optional, z. B. spectacle --region"))
        self.pick_app_button = QPushButton(tr("Anwendung …"))
        self.pick_app_button.clicked.connect(self.pick_application)
        self.pick_file_button = QPushButton(tr("Datei …"))
        self.pick_file_button.clicked.connect(self.pick_file)

        command_row = QHBoxLayout()
        command_row.setSpacing(SPACE_S)
        command_row.addWidget(self.command_edit, 1)
        command_row.addWidget(self.pick_app_button)
        command_row.addWidget(self.pick_file_button)
        form.addRow(tr("Kommando"), command_row)
        layout.addLayout(form)

        self.conflict_hint = hint(
            tr("Virtuelle Taste und Kommando sind beide belegt — beim Druck "
            "passiert beides. Für „nur Programm starten“ die virtuelle Taste "
            "auf „(keine)“ setzen."))
        self.conflict_hint.setVisible(False)
        layout.addWidget(self.conflict_hint)

        self.vkey_combo.currentTextChanged.connect(self.update_conflict_hint)
        self.command_edit.textChanged.connect(self.update_conflict_hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_S)
        self.apply_button = QPushButton(tr("Übernehmen"))
        self.apply_button.setDefault(True)
        primary_button(self.apply_button)
        self.apply_button.clicked.connect(self.apply_editor)
        self.clear_button = QPushButton(tr("Belegung löschen"))
        self.clear_button.clicked.connect(self.clear_binding)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        return box

    # ---- Bilder ----

    def load_images(self):
        if self._loader is not None and self._loader.isRunning():
            return
        self.load_images_button.setEnabled(False)
        self._pending_images = {}
        self.busy.emit(True, tr("Lese Tastenbilder …"))
        self._loader = ImageLoader(bqkeyd.DISPLAY_KEYS)
        self._loader.loaded.connect(self.on_image_loaded)
        self._loader.progress.connect(lambda t: self.status.emit(t, 0))
        self._loader.progress_value.connect(self.progress_changed)
        self._loader.finished_all.connect(self.on_images_finished)
        self._loader.start()

    def on_image_loaded(self, key_id, jpeg):
        # Erst gesammelt einblenden, wenn alle Antworten da sind. Dadurch
        # springen die Bilder nicht während der modalen Ladeanzeige ins Raster.
        self._pending_images[key_id] = jpeg

    def on_images_finished(self, count, error):
        for key_id, jpeg in self._pending_images.items():
            tile = self.tiles.get(key_id)
            if tile:
                tile.set_image(jpeg)
        self._pending_images = {}
        self.busy.emit(False, "")
        self.load_images_button.setEnabled(True)
        if error:
            self.status.emit(tr("%d von 8 Bildern geladen — %s") % (count, error), 8000)
        else:
            self.status.emit(tr("%d Tastenbilder geladen.") % count, 4000)

    def change_image(self):
        if self.selected is None:
            return
        key_id = self.selected
        slot = key_id - bqkeyd.FIRST_KEY + 1
        dialog = ImageDialog(slot, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.jpeg:
            return
        self.change_image_button.setEnabled(False)
        self.busy.emit(True, tr("Schreibe Bild auf Taste %d …") % slot)
        self._writer = ImageWriter(key_id, dialog.jpeg)
        self._writer.progress.connect(lambda t: self.status.emit(t, 0))
        self._writer.done.connect(
            lambda ok, msg, jpeg, kid=key_id: self.on_image_written(
                ok, msg, jpeg, kid))
        self._writer.start()

    def on_image_written(self, ok, message, jpeg, key_id):
        self.busy.emit(False, "")
        self.change_image_button.setEnabled(self.selected is not None)
        self.status.emit(message, 8000)
        if ok and jpeg:
            tile = self.tiles.get(key_id)
            if tile:
                tile.set_image(jpeg)
        elif not ok:
            QMessageBox.warning(self, tr("Bild schreiben"), message)

    # ---- Tastendruck ----

    def on_key_pressed(self, key_id, service_running):
        tile = self.tiles.get(key_id)
        if tile:
            tile.flash()
        if service_running:
            self.status.emit(tr("Taste %d gedrückt") % (tile.slot if tile else 0), 2500)
            return False
        return True

    # ---- Bearbeiten ----

    def select_key(self, key_id):
        self.selected = key_id
        for kid, tile in self.tiles.items():
            tile.set_selected(kid == key_id)

        tile = self.tiles[key_id]
        self.editor_title.setText(tr("Taste %d") % tile.slot)
        self.label_edit.setText(self.labels.get(key_id, ""))
        code = self.vkeys.get(key_id)
        name = {v: k for k, v in bqkeyd.KEY_NAMES.items()}.get(code, tr(NO_VKEY))
        self.vkey_combo.setCurrentText(name)
        command = self.commands.get(key_id, "")
        if isinstance(command, list):
            command = " ".join(command)
        self.command_edit.setText(command)
        self.update_conflict_hint()
        self.set_editor_enabled(True)

    def set_editor_enabled(self, enabled):
        for widget in (self.label_edit, self.vkey_combo, self.command_edit,
                       self.pick_app_button, self.pick_file_button,
                       self.apply_button, self.clear_button,
                       self.change_image_button):
            widget.setEnabled(enabled)

    def update_conflict_hint(self, *_):
        self.conflict_hint.setVisible(
            self.vkey_combo.currentText() != tr(NO_VKEY)
            and bool(self.command_edit.text().strip()))

    def pick_application(self):
        dialog = AppPickerDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen:
            self.command_edit.setText(dialog.chosen)
            if not self.label_edit.text().strip():
                item = dialog.list.currentItem()
                if item is not None:
                    self.label_edit.setText(item.text())
            # Wer ein Programm auswählt, will es starten -- nicht zusätzlich
            # eine Taste auslösen, die im Programm selbst belegt sein kann.
            self.vkey_combo.setCurrentText(tr(NO_VKEY))

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Programm oder Skript auswählen"), os.path.expanduser("~"))
        if not path:
            return
        self.command_edit.setText('"%s"' % path if " " in path else path)
        if not self.label_edit.text().strip():
            self.label_edit.setText(os.path.basename(path))
        self.vkey_combo.setCurrentText(tr(NO_VKEY))

    def apply_editor(self):
        if self.selected is None:
            return
        key_id = self.selected
        label = self.label_edit.text().strip()
        if label:
            self.labels[key_id] = label
        else:
            self.labels.pop(key_id, None)

        name = self.vkey_combo.currentText()
        if name == tr(NO_VKEY):
            self.vkeys.pop(key_id, None)
        else:
            self.vkeys[key_id] = bqkeyd.KEY_NAMES[name]

        command = self.command_edit.text().strip()
        if command:
            self.commands[key_id] = command
        else:
            self.commands.pop(key_id, None)
        self.save()

    def clear_binding(self):
        if self.selected is None:
            return
        self.vkeys.pop(self.selected, None)
        self.commands.pop(self.selected, None)
        self.labels.pop(self.selected, None)
        self.save()
        self.select_key(self.selected)

    def refresh_tiles(self):
        names = {v: k for k, v in bqkeyd.KEY_NAMES.items()}
        for key_id, tile in self.tiles.items():
            tile.set_summary(names.get(self.vkeys.get(key_id)),
                             self.commands.get(key_id),
                             self.labels.get(key_id))

    def save(self):
        names = {v: k for k, v in bqkeyd.KEY_NAMES.items()}
        lines = ["[uinput]",
                 "enabled = %s" % ("true" if self.uinput_enabled else "false"),
                 ""]
        for slot, key_id in enumerate(bqkeyd.DISPLAY_KEYS, start=1):
            lines.append("[keys.key%d]" % slot)
            label = self.labels.get(key_id)
            if label:
                lines.append('label = "%s"' % self._escape(label))
            code = self.vkeys.get(key_id)
            lines.append("key = false" if code is None
                         else 'key = "%s"' % names[code])
            command = self.commands.get(key_id)
            if command:
                if isinstance(command, list):
                    command = " ".join(command)
                lines.append('command = "%s"' % self._escape(command))
            lines.append("")

        try:
            try:
                with open(CONFIG_PATH, encoding="utf-8") as handle:
                    previous = handle.read()
            except FileNotFoundError:
                previous = (
                    "# Belegung der 8 Display Keys (be quiet! Dark Mount).\n"
                    "# Von bqgui.py geschrieben -- Handbearbeitung bleibt möglich.\n")
            managed = ["uinput"] + ["keys.key%d" % slot
                                     for slot in range(1, 9)]
            updated = bqconfig.replace_sections(
                previous, managed, "\n".join(lines))
            bqconfig.atomic_write_text(CONFIG_PATH, updated)
        except OSError as exc:
            QMessageBox.critical(self, tr("Speichern fehlgeschlagen"), str(exc))
            return

        self.refresh_tiles()
        self.status.emit(tr("Belegung gespeichert."), 3000)
        self.window.bindings_changed()

    @staticmethod
    def _escape(text):
        return text.replace("\\", "\\\\").replace('"', '\\"')


# --------------------------------------------------------- Seite: Media-Dock ---

class DockPage(QWidget):
    status = pyqtSignal(str, int)
    busy = pyqtSignal(bool, str)
    progress_changed = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.settings = None
        self._rgb565 = None
        self._reader = None
        self._writer = None
        self._worker = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        outer.setSpacing(SPACE_L)

        intro = QVBoxLayout()
        intro.setSpacing(2)
        intro.addWidget(heading(tr("Media-Dock")))
        intro.addWidget(hint(tr("Das Modul mit eigenem Display, 320 × 240. Die "
                             "Menüfarbe gilt für die Ansichten auf dem Gerät.")))
        outer.addLayout(intro)

        columns = QHBoxLayout()
        columns.setSpacing(SPACE_XL)

        # Linke Spalte: Leerlaufgrafik
        left = QVBoxLayout()
        left.setSpacing(SPACE_S)
        self.preview = QLabel(tr("Kein Bild gewählt"))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(320, 240)
        self.preview.setStyleSheet(
            "border: 1px solid %s; border-radius: 6px; color: %s;"
            % (border(), muted()))
        left.addWidget(self.preview)
        left.addWidget(caption(tr("Leerlaufgrafik, 320 × 240")))

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        left.addWidget(self.progress)

        image_buttons = QHBoxLayout()
        image_buttons.setSpacing(SPACE_S)
        self.read_image_button = QPushButton(tr("Vom Dock lesen"))
        self.read_image_button.clicked.connect(self.read_image)
        self.pick_image_button = QPushButton(tr("Bild wählen …"))
        self.pick_image_button.clicked.connect(self.choose_image)
        image_buttons.addWidget(self.read_image_button)
        self.write_image_button = QPushButton(tr("Auf das Dock schreiben"))
        self.write_image_button.setEnabled(False)
        self.write_image_button.clicked.connect(self.write_image)
        image_buttons.addWidget(self.pick_image_button, 1)
        left.addLayout(image_buttons)
        left.addWidget(self.write_image_button)
        left.addWidget(hint(tr("Vor dem Schreiben wird das aktuelle Bild "
                            "gesichert; danach wird das neue zurückgelesen und "
                            "geprüft. Es landet im Flash — häufiges "
                            "Überschreiben nutzt ihn ab.")))
        left.addStretch(1)
        columns.addLayout(left)

        # Rechte Spalte: Einstellungen
        right = QVBoxLayout()
        right.setSpacing(SPACE_M)

        form = QFormLayout()
        form.setHorizontalSpacing(SPACE_L)
        form.setVerticalSpacing(SPACE_M)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        self.colour_button = QPushButton()
        self.colour_button.setFixedHeight(28)
        self.colour_button.clicked.connect(self.choose_colour)
        form.addRow(tr("Menüfarbe"), self.colour_button)

        self.display_group = QButtonGroup(self)
        self.radio_clock = QRadioButton(tr("Uhr"))
        self.radio_image = QRadioButton(tr("Bild"))
        self.display_group.addButton(self.radio_clock, bqdock.DISPLAY_CLOCK)
        self.display_group.addButton(self.radio_image, bqdock.DISPLAY_IMAGE)
        display_row = QHBoxLayout()
        display_row.setSpacing(SPACE_L)
        display_row.addWidget(self.radio_clock)
        display_row.addWidget(self.radio_image)
        display_row.addStretch(1)
        form.addRow(tr("Leerlaufanzeige"), display_row)

        self.clock_group = QButtonGroup(self)
        self.radio_24 = QRadioButton(tr("24 Stunden"))
        self.radio_12 = QRadioButton(tr("12 Stunden"))
        self.clock_group.addButton(self.radio_24, bqdock.CLOCK_24H)
        self.clock_group.addButton(self.radio_12, bqdock.CLOCK_12H)
        clock_row = QHBoxLayout()
        clock_row.setSpacing(SPACE_L)
        clock_row.addWidget(self.radio_24)
        clock_row.addWidget(self.radio_12)
        clock_row.addStretch(1)
        form.addRow(tr("Uhrzeit"), clock_row)

        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(0, 3600)
        self.idle_spin.setSuffix(" s")
        form.addRow(tr("Leerlaufgrafik nach"), self.idle_spin)

        off_row = QHBoxLayout()
        off_row.setSpacing(SPACE_S)
        self.off_check = QCheckBox(tr("nach"))
        self.off_spin = QSpinBox()
        self.off_spin.setRange(1, 3600)
        self.off_spin.setSuffix(" s")
        self.off_check.toggled.connect(self.off_spin.setEnabled)
        off_row.addWidget(self.off_check)
        off_row.addWidget(self.off_spin)
        off_row.addStretch(1)
        form.addRow(tr("Display ausschalten"), off_row)
        right.addLayout(form)
        right.addWidget(hint(tr(
            "Sobald ein Rechner mit dem Dock spricht, zeigt es sein Menü. "
            "Uhr und Bild erscheinen erst wieder als Leerlaufanzeige, nach "
            "der eingestellten Wartezeit ohne Bedienung. Zum Ausprobieren "
            "die Wartezeit kurz auf wenige Sekunden stellen.")))

        settings_buttons = QHBoxLayout()
        settings_buttons.setSpacing(SPACE_S)
        self.time_button = QPushButton(tr("Uhr stellen"))
        self.time_button.setToolTip(
            tr("Die Uhr des Docks läuft ohne Abgleich mit der Zeit davon."))
        self.time_button.clicked.connect(self.set_clock)
        settings_buttons.addWidget(self.time_button)
        self.reload_button = QPushButton(tr("Vom Gerät lesen"))
        self.reload_button.clicked.connect(self.load_settings)
        self.save_button = QPushButton(tr("Einstellungen übernehmen"))
        primary_button(self.save_button)
        self.save_button.clicked.connect(self.save_settings)
        settings_buttons.addStretch(1)
        settings_buttons.addWidget(self.reload_button)
        settings_buttons.addWidget(self.save_button)
        right.addLayout(settings_buttons)
        right.addStretch(1)
        columns.addLayout(right, 1)

        outer.addLayout(columns)
        outer.addStretch(1)
        self.set_settings_enabled(False)

    # ---- Einstellungen ----

    def set_settings_enabled(self, enabled):
        for widget in (self.colour_button, self.radio_clock, self.radio_image,
                       self.radio_24, self.radio_12, self.idle_spin,
                       self.off_check, self.save_button):
            widget.setEnabled(enabled)
        self.off_spin.setEnabled(enabled and self.off_check.isChecked())

    def load_settings(self):
        self.reload_button.setEnabled(False)
        self.busy.emit(True, tr("Lese Einstellungen …"))

        def read():
            dock = bqdock.Dock()
            try:
                return dock.read_settings()
            finally:
                dock.close()

        self._worker = Worker(read, tr("Einstellungen gelesen."))
        self._worker.done.connect(self.on_settings_loaded)
        self._worker.start()

    def on_settings_loaded(self, ok, message, result):
        self.busy.emit(False, "")
        self.reload_button.setEnabled(True)
        if not ok:
            self.status.emit(tr("Dock nicht erreichbar: %s") % message, 6000)
            return
        self.settings = result
        self.apply_to_form(result)
        self.set_settings_enabled(True)
        self.status.emit(message, 3000)

    def set_clock(self):
        self.time_button.setEnabled(False)
        self.busy.emit(True, tr("Stelle die Uhr …"))

        def run():
            dock = bqdock.Dock()
            try:
                stamp = dock.set_time()
                return (stamp, dock.confirmed_time())
            finally:
                dock.close()

        self._worker = Worker(run)
        self._worker.done.connect(self.on_clock_set)
        self._worker.start()

    def on_clock_set(self, ok, message, result):
        self.busy.emit(False, "")
        self.time_button.setEnabled(True)
        if not ok:
            self.status.emit(tr("Fehlgeschlagen: %s") % message, 6000)
            return
        stamp, confirmed = result
        shown = time.strftime("%H:%M:%S", time.gmtime(stamp))
        if confirmed == stamp:
            # Am Display ist nichts zu sehen -- das Dock zeigt waehrend der
            # Verbindung sein Menue. Die Bestaetigung ist der Beleg.
            self.status.emit(tr("Uhr auf %s gestellt, vom Gerät bestätigt.")
                             % shown, 8000)
        elif confirmed is None:
            self.status.emit(tr("Uhr auf %s gestellt — keine Bestätigung "
                                "erhalten.") % shown, 8000)
        else:
            self.status.emit(
                tr("Uhr auf %s gestellt, Gerät meldet %s.")
                % (shown, time.strftime("%H:%M:%S", time.gmtime(confirmed))),
                8000)

    def apply_to_form(self, settings):
        self.update_colour_button(settings.color)
        (self.radio_image if settings.display == bqdock.DISPLAY_IMAGE
         else self.radio_clock).setChecked(True)
        (self.radio_12 if settings.clock_format == bqdock.CLOCK_12H
         else self.radio_24).setChecked(True)
        self.idle_spin.setValue(settings.idle_seconds)
        self.off_check.setChecked(bool(settings.off_seconds))
        if settings.off_seconds:
            self.off_spin.setValue(settings.off_seconds)

    def update_colour_button(self, colour):
        self.colour_button.setText(colour)
        text = "#000000" if QColor(colour).lightness() > 140 else "#ffffff"
        self.colour_button.setStyleSheet(
            "QPushButton { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 4px; }" % (colour, text, border()))

    def choose_colour(self):
        if self.settings is None:
            return
        colour = QColorDialog.getColor(QColor(self.settings.color), self,
                                       tr("Menüfarbe"))
        if colour.isValid():
            self.settings.color = colour.name()
            self.update_colour_button(colour.name())

    def save_settings(self):
        if self.settings is None:
            return
        self.settings.display = self.display_group.checkedId()
        self.settings.clock_format = self.clock_group.checkedId()
        self.settings.idle_seconds = self.idle_spin.value()
        self.settings.off_seconds = (self.off_spin.value()
                                     if self.off_check.isChecked() else 0)
        self.save_button.setEnabled(False)
        self.busy.emit(True, tr("Übertrage Einstellungen …"))
        settings = self.settings

        def write():
            dock = bqdock.Dock()
            try:
                dock.write_settings(settings)
                return dock.read_settings()
            finally:
                dock.close()

        self._worker = Worker(write, tr("Einstellungen übernommen."))
        self._worker.done.connect(self.on_settings_saved)
        self._worker.start()

    def on_settings_saved(self, ok, message, result):
        self.busy.emit(False, "")
        self.save_button.setEnabled(True)
        if ok and result is not None:
            self.settings = result
            self.apply_to_form(result)
        self.status.emit(message if ok else tr("Fehlgeschlagen: %s") % message,
                         6000)

    # ---- Leerlaufgrafik ----

    def show_preview(self, rgb565):
        image = bqdock.from_rgb565(rgb565)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue(), "PNG")
        self.preview.setPixmap(pixmap)
        self.preview.setStyleSheet("border-radius: 6px;")

    def read_image(self):
        self.read_image_button.setEnabled(False)
        self.pick_image_button.setEnabled(False)
        self.write_image_button.setEnabled(False)
        self.busy.emit(True, tr("Lese Leerlaufgrafik vom Dock …"))
        self.progress.setVisible(True)
        self.progress.setRange(0, bqdock.IMAGE_BYTES)
        self._reader = DockImageReader()
        self._reader.progress.connect(self.on_image_progress)
        self._reader.done.connect(self.on_image_read)
        self._reader.start()

    def on_image_read(self, ok, message, rgb565):
        self.busy.emit(False, "")
        self.progress.setVisible(False)
        self.read_image_button.setEnabled(True)
        self.pick_image_button.setEnabled(True)
        self.write_image_button.setEnabled(self._rgb565 is not None)
        if ok:
            try:
                # Die Vorschau zeigt jetzt den Gerätestand. Ein zuvor lokal
                # gewähltes Bild darf danach nicht unsichtbar schreibbereit
                # bleiben.
                self._rgb565 = None
                self.write_image_button.setEnabled(False)
                self.show_preview(rgb565)
            except Exception as exc:
                self.status.emit(tr("Bild kann nicht gelesen werden: %s") % exc,
                                 6000)
                return
        self.status.emit(message if ok else tr("Fehlgeschlagen: %s") % message,
                         6000)

    def on_image_progress(self, done, total):
        self.progress.setValue(done)
        self.progress_changed.emit(done, total)

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Bild für das Dock wählen"), os.path.expanduser("~"),
            tr("Bilder (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;Alle Dateien (*)"))
        if not path:
            return
        try:
            self._rgb565 = bqdock.to_rgb565(path)
            self.show_preview(self._rgb565)
        except Exception as exc:
            self.status.emit(tr("Bild kann nicht gelesen werden: %s") % exc, 6000)
            return
        self.write_image_button.setEnabled(True)
        self.status.emit(tr("Vorschau zeigt exakt die Farben des Displays "
                         "(RGB565)."), 5000)

    def write_image(self):
        if self._rgb565 is None:
            return
        if self.settings is not None and self.settings.display != bqdock.DISPLAY_IMAGE:
            answer = QMessageBox.question(
                self, tr("Anzeige umschalten?"),
                tr("Das Dock zeigt gerade die Uhr. Soll nach dem Übertragen auf "
                "„Bild“ umgeschaltet werden?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            self._switch_after = answer == QMessageBox.StandardButton.Yes
        else:
            self._switch_after = False

        self.write_image_button.setEnabled(False)
        self.pick_image_button.setEnabled(False)
        self.read_image_button.setEnabled(False)
        self.busy.emit(True, tr("Sichere, übertrage und prüfe die "
                                "Leerlaufgrafik …"))
        self.progress.setVisible(True)
        self.progress.setRange(0, bqdock.IMAGE_BYTES * 3)
        self._writer = DockImageWriter(self._rgb565)
        self._writer.progress.connect(self.on_image_progress)
        self._writer.done.connect(self.on_image_written)
        self._writer.start()

    def on_image_written(self, ok, message):
        self.busy.emit(False, "")
        self.progress.setVisible(False)
        self.write_image_button.setEnabled(True)
        self.pick_image_button.setEnabled(True)
        self.read_image_button.setEnabled(True)
        self.status.emit(message, 6000)
        if ok and getattr(self, "_switch_after", False):
            self.radio_image.setChecked(True)
            self.save_settings()


# -------------------------------------------------------- Seite: Beleuchtung ---

def apply_onboard_effect(effect, rgb, brightness, speed):
    """Onboard-Effekt setzen und eine alte LampArray-Übernahme beenden."""
    lighting = bqlight.Lighting()
    try:
        lighting.set_effect(effect, rgb, brightness, speed)
    finally:
        lighting.close()

    # Ein vorher gesetztes LampArray-Muster lässt die Tastatur im Host-Modus.
    # Der QLink-Effekt wird dann zwar gespeichert, bleibt aber unsichtbar.
    # Nach erfolgreichem Setzen deshalb immer zur autonomen Beleuchtung zurück.
    array = bqlamp.LampArray()
    try:
        array.release()
    finally:
        array.close()


class LightingPage(QWidget):
    status = pyqtSignal(str, int)
    busy = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.colours = [QColor(ACCENT)]
        self.pc_colours = [QColor(ACCENT), QColor("#0091ff")]
        self._worker = None
        self._meter = None
        self._lamp_action = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        outer.setSpacing(SPACE_L)

        intro_row = QHBoxLayout()
        intro = QVBoxLayout()
        intro.setSpacing(2)
        intro.addWidget(heading(tr("Beleuchtung")))
        intro.addWidget(hint(tr("Die Effekte laufen im Gerät selbst — sie bleiben "
                             "aktiv, auch wenn der Rechner aus ist.")))
        intro_row.addLayout(intro, 1)
        self.active_mode = status_badge(tr("Aktiver Modus nicht ausgelesen"))
        intro_row.addWidget(self.active_mode,
                            alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(intro_row)

        columns = QHBoxLayout()
        columns.setSpacing(SPACE_XL)

        self.effect_list = QListWidget()
        self.effect_list.setMaximumWidth(220)
        for number in sorted(bqlight.EFFECT_NAMES):
            item = QListWidgetItem(tr(bqlight.EFFECT_NAMES[number]))
            item.setData(Qt.ItemDataRole.UserRole, number)
            self.effect_list.addItem(item)
        self.effect_list.setCurrentRow(0)
        columns.addWidget(self.effect_list)

        right = QVBoxLayout()
        right.setSpacing(SPACE_M)

        form = QFormLayout()
        form.setHorizontalSpacing(SPACE_L)
        form.setVerticalSpacing(SPACE_M)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        # Farbliste: eine Farbe = Modus 0, zwei = Modus 1, mehr = Palette.
        self.colour_row = QHBoxLayout()
        self.colour_row.setSpacing(SPACE_S)
        self.colour_row.addStretch(1)
        colours_box = QWidget()
        colours_box.setLayout(self.colour_row)
        form.addRow(tr("Farben"), colours_box)

        palette_buttons = QHBoxLayout()
        palette_buttons.setSpacing(SPACE_S)
        self.add_colour_button = QPushButton(tr("Farbe hinzufügen"))
        self.add_colour_button.clicked.connect(self.add_colour)
        self.remove_colour_button = QPushButton(tr("Letzte entfernen"))
        self.remove_colour_button.clicked.connect(self.remove_colour)
        palette_buttons.addWidget(self.add_colour_button)
        palette_buttons.addWidget(self.remove_colour_button)
        palette_buttons.addStretch(1)
        form.addRow("", palette_buttons)

        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(100)
        self.brightness_label = QLabel("100")
        self.brightness_label.setMinimumWidth(32)
        self.brightness.valueChanged.connect(
            lambda v: self.brightness_label.setText(str(v)))
        brightness_row = QHBoxLayout()
        brightness_row.setSpacing(SPACE_M)
        brightness_row.addWidget(self.brightness, 1)
        brightness_row.addWidget(self.brightness_label)
        form.addRow(tr("Helligkeit"), brightness_row)

        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(0, 100)
        self.speed.setValue(50)
        self.speed_label = QLabel("50")
        self.speed_label.setMinimumWidth(32)
        self.speed.valueChanged.connect(
            lambda v: self.speed_label.setText(str(v)))
        speed_row = QHBoxLayout()
        speed_row.setSpacing(SPACE_M)
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(self.speed_label)
        form.addRow(tr("Tempo"), speed_row)
        right.addLayout(form)

        self.mode_hint = hint("")
        right.addWidget(self.mode_hint)
        right.addWidget(hint(tr("Je nach Effekt verwendet die Tastatur eine "
                             "Farbe, zwei Farben oder die gesamte Palette.")))

        apply_row = QHBoxLayout()
        self.apply_button = QPushButton(tr("Auf die Tastatur anwenden"))
        primary_button(self.apply_button)
        self.apply_button.clicked.connect(self.apply_effect)
        apply_row.addStretch(1)
        apply_row.addWidget(self.apply_button)
        right.addLayout(apply_row)
        right.addStretch(1)
        columns.addLayout(right, 1)

        outer.addLayout(columns)
        outer.addWidget(separator())
        outer.addLayout(self._build_lamparray())
        outer.addWidget(separator())
        outer.addLayout(self._build_meter())
        outer.addStretch(1)
        self.rebuild_colours()

    def _build_lamparray(self):
        """Zweiter Abschnitt: temporäre PC-Beleuchtung über LampArray."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACE_M)

        intro = QVBoxLayout()
        intro.setSpacing(2)
        intro.addWidget(heading(tr("PC-Beleuchtung")))
        self.lamp_hint = hint(tr("Temporär vom Rechner gesteuert und nicht in "
                                 "den Flash geschrieben. Beim Wiederherstellen "
                                 "kehrt der Onboard-Effekt zurück."))
        intro.addWidget(self.lamp_hint)
        layout.addLayout(intro)

        colour_row = QHBoxLayout()
        colour_row.setSpacing(SPACE_S)
        colour_row.addWidget(QLabel(tr("Farbe")))
        self.pc_colour_buttons = []
        for index in range(2):
            button = QPushButton()
            button.setFixedSize(54, 28)
            button.setAccessibleName(
                tr("Farbe") if index == 0 else tr("Zweite Farbe"))
            button.clicked.connect(
                lambda _checked, i=index: self.choose_pc_colour(i))
            self.pc_colour_buttons.append(button)
            colour_row.addWidget(button)
            if index == 0:
                colour_row.addWidget(QLabel(tr("Zweite Farbe")))
        colour_row.addStretch(1)
        layout.addLayout(colour_row)
        self.update_pc_colour_buttons()

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_S)
        self.lamp_solid_button = QPushButton(tr("Einfarbig anzeigen"))
        self.lamp_solid_button.clicked.connect(
            lambda: self.run_lamparray("solid"))
        self.lamp_gradient_button = QPushButton(tr("Verlauf anzeigen"))
        self.lamp_gradient_button.clicked.connect(
            lambda: self.run_lamparray("gradient"))
        self.lamp_release_button = QPushButton(
            tr("Onboard-Effekt wiederherstellen"))
        self.lamp_release_button.clicked.connect(
            lambda: self.run_lamparray("release"))
        buttons.addWidget(self.lamp_solid_button)
        buttons.addWidget(self.lamp_gradient_button)
        buttons.addStretch(1)
        buttons.addWidget(self.lamp_release_button)
        layout.addLayout(buttons)
        return layout

    def _build_meter(self):
        """Live-Auslastung auf F- und Zahlenreihe, komplett flashfrei."""
        layout = QVBoxLayout()
        layout.setSpacing(SPACE_S)
        layout.addWidget(heading(tr("Auslastungsanzeige")))
        layout.addWidget(hint(tr(
            "F1–F10 zeigen GPU 10–100 %, die Tasten 1–0 CPU 10–100 %. "
            "Nur LampArray im RAM; beim Stoppen kehrt der Onboard-Effekt "
            "zurück.")))

        controls = QHBoxLayout()
        controls.setSpacing(SPACE_S)
        self.meter_mode = QComboBox()
        self.meter_mode.addItem(tr("Nur aktuelle Stufe"), "dot")
        self.meter_mode.addItem(tr("Balken"), "bar")
        controls.addWidget(self.meter_mode)

        self.meter_swap = QCheckBox(tr("Reihen tauschen"))
        self.meter_swap.setToolTip(
            tr("GPU auf 1–0 und CPU auf F1–F10 anzeigen."))
        controls.addWidget(self.meter_swap)

        self.meter_button = QPushButton(tr("Monitor starten"))
        self.meter_button.clicked.connect(self.toggle_meter)
        controls.addWidget(self.meter_button)
        controls.addSpacing(SPACE_M)

        self.gpu_value = QLabel("GPU —")
        self.gpu_value.setStyleSheet("color: #ff2800; font-weight: 600;")
        self.cpu_value = QLabel("CPU —")
        self.cpu_value.setStyleSheet("color: #0091ff; font-weight: 600;")
        controls.addWidget(self.gpu_value)
        controls.addWidget(self.cpu_value)
        controls.addStretch(1)
        layout.addLayout(controls)
        return layout

    def toggle_meter(self):
        if self._meter is not None and self._meter.isRunning():
            self.meter_button.setEnabled(False)
            self._meter.stop()
            return
        if self._worker is not None and self._worker.isRunning():
            self.status.emit(tr("Bitte laufende Beleuchtungsoperation abwarten."),
                             4000)
            return

        self._meter = LoadMeterThread(
            self.meter_mode.currentData(), 1.0,
            self.meter_swap.isChecked())
        self._meter.ready.connect(self.on_meter_ready)
        self._meter.sampled.connect(self.on_meter_sample)
        self._meter.done.connect(self.on_meter_done)
        self.set_meter_running(True)
        self.status.emit(tr("Starte GPU-/CPU-Monitor …"), 3000)
        self._meter.start()

    def set_meter_running(self, running):
        self.meter_mode.setEnabled(not running)
        self.meter_swap.setEnabled(not running)
        self.meter_button.setEnabled(True)
        self.meter_button.setText(
            tr("Monitor stoppen") if running else tr("Monitor starten"))
        for button in (self.lamp_solid_button, self.lamp_gradient_button,
                       self.lamp_release_button, self.apply_button):
            button.setEnabled(not running)
        for button in self.pc_colour_buttons:
            button.setEnabled(not running)

    def on_meter_ready(self, path, source):
        self.set_active_mode(tr("PC · Auslastungsanzeige"))
        self.status.emit(tr("Monitor aktiv über %s; GPU-Quelle: %s")
                         % (path, source), 6000)

    def on_meter_sample(self, gpu, cpu):
        self.gpu_value.setText("GPU %3.0f %%" % gpu)
        self.cpu_value.setText("CPU %3.0f %%" % cpu)

    def on_meter_done(self, ok, message):
        self.set_meter_running(False)
        self._meter = None
        if ok:
            self.set_active_mode(tr("Onboard-Effekt aktiv"))
        self.status.emit(message if ok else tr("Monitor fehlgeschlagen: %s")
                         % message, 7000)

    def stop_meter(self):
        if self._meter is not None and self._meter.isRunning():
            return self._meter.stop()
        return True

    def run_lamparray(self, what):
        colours = [(c.red(), c.green(), c.blue()) for c in self.pc_colours]
        self._lamp_action = what
        for button in (self.lamp_solid_button, self.lamp_gradient_button,
                       self.lamp_release_button):
            button.setEnabled(False)
        self.busy.emit(True, tr("Spreche die Lampen an …"))

        def run():
            array = bqlamp.LampArray()
            try:
                if what == "release":
                    array.release()
                    return tr("Steuerung an das Gerät zurückgegeben.")
                array.take_control()
                if what == "solid":
                    array.solid(colours[0])
                else:
                    array.gradient(colours[0], colours[-1])
                array._owned = False        # Farbe stehen lassen
                return tr("%d Lampen gesetzt.") % array.lamp_count
            finally:
                array.close()

        self._worker = Worker(run)
        self._worker.done.connect(self.on_lamparray_done)
        self._worker.start()

    def on_lamparray_done(self, ok, message, result):
        self.busy.emit(False, "")
        for button in (self.lamp_solid_button, self.lamp_gradient_button,
                       self.lamp_release_button):
            button.setEnabled(True)
        self.status.emit(result if ok and result
                         else (message if ok else tr("Fehlgeschlagen: %s")
                               % message), 5000)
        if ok:
            labels = {
                "solid": tr("PC · Einfarbig"),
                "gradient": tr("PC · Verlauf"),
                "release": tr("Onboard-Effekt aktiv"),
            }
            self.set_active_mode(labels.get(self._lamp_action, ""))

    def set_active_mode(self, text):
        self.active_mode.setText(text)

    def update_pc_colour_buttons(self):
        for button, colour in zip(self.pc_colour_buttons, self.pc_colours):
            button.setToolTip("%s — %s" %
                              (colour.name(), tr("zum Ändern klicken")))
            button.setStyleSheet(
                "QPushButton { background: %s; border: 1px solid %s;"
                " border-radius: 6px; }" % (colour.name(), border()))

    def choose_pc_colour(self, index):
        colour = QColorDialog.getColor(self.pc_colours[index], self,
                                       tr("Farbe der PC-Beleuchtung"))
        if colour.isValid():
            self.pc_colours[index] = colour
            self.update_pc_colour_buttons()

    def rebuild_colours(self):
        """Baut die Farbfelder neu auf -- ein Feld je Farbe."""
        while self.colour_row.count() > 1:          # der Stretch bleibt
            item = self.colour_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, colour in enumerate(self.colours):
            button = QPushButton()
            button.setFixedSize(48, 28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip("%s — %s" % (colour.name(), tr("zum Ändern klicken")))
            button.setStyleSheet(
                "QPushButton { background: %s; border: 1px solid %s;"
                " border-radius: 4px; }" % (colour.name(), border()))
            button.clicked.connect(lambda _checked, i=index: self.choose_colour(i))
            self.colour_row.insertWidget(index, button)

        self.add_colour_button.setEnabled(len(self.colours) < bqlight.MAX_PALETTE)
        self.remove_colour_button.setEnabled(len(self.colours) > 1)
        self.update_mode_hint()

    def update_mode_hint(self):
        count = len(self.colours)
        if count == 1:
            text = tr("Eine Farbe")
        elif count == 2:
            text = tr("Zwei Farben")
        else:
            text = tr("Palette mit %d Farben") % count
        self.mode_hint.setText(text)

    def choose_colour(self, index):
        colour = QColorDialog.getColor(self.colours[index], self,
                                       tr("Farbe der Beleuchtung"))
        if colour.isValid():
            self.colours[index] = colour
            self.rebuild_colours()

    def add_colour(self):
        if len(self.colours) >= bqlight.MAX_PALETTE:
            return
        self.colours.append(QColor(self.colours[-1]))
        self.rebuild_colours()

    def remove_colour(self):
        if len(self.colours) > 1:
            self.colours.pop()
            self.rebuild_colours()

    def apply_effect(self):
        item = self.effect_list.currentItem()
        if item is None:
            return
        effect = item.data(Qt.ItemDataRole.UserRole)
        rgb = [(c.red(), c.green(), c.blue()) for c in self.colours]
        brightness = self.brightness.value()
        speed = self.speed.value()
        self.apply_button.setEnabled(False)
        self.busy.emit(True, tr("Setze Effekt …"))

        def run():
            apply_onboard_effect(effect, rgb, brightness, speed)

        self._worker = Worker(
            run, tr("%s gesetzt.") % tr(bqlight.EFFECT_NAMES[effect]))
        self._worker.done.connect(self.on_applied)
        self._worker.start()

    def on_applied(self, ok, message, _result):
        self.busy.emit(False, "")
        self.apply_button.setEnabled(True)
        self.status.emit(message if ok else tr("Fehlgeschlagen: %s") % message, 5000)
        if ok:
            item = self.effect_list.currentItem()
            if item is not None:
                self.set_active_mode(tr("Onboard · %s") % item.text())


# ---------------------------------------------------------- Sicherheitshinweis ---

class SafetyNoticeDialog(QDialog):
    """Einmalige, aktive Zustimmung vor dem ersten Gerätezugriff."""

    def __init__(self, parent=None, review_only=False):
        super().__init__(parent)
        self.review_only = review_only
        self.setWindowTitle(tr("Wichtiger Sicherheitshinweis"))
        self.setModal(True)
        self.resize(680, 690)
        self.setMinimumSize(560, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_L)

        eyebrow = QLabel(tr("INOFFIZIELLES COMMUNITY-PROJEKT"))
        eyebrow.setStyleSheet(
            "color: #ff2800; font-weight: 700; letter-spacing: 1px;")
        layout.addWidget(eyebrow)

        title = heading(tr("Bevor es losgeht"))
        font = title.font()
        font.setPointSizeF(font.pointSizeF() + 3)
        title.setFont(font)
        layout.addWidget(title)
        layout.addWidget(hint(tr(
            "Diese Software kommuniziert direkt mit deiner Hardware. Bitte "
            "lies die folgenden Hinweise vollständig, bevor du fortfährst.")))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, SPACE_S, 0)
        content_layout.setSpacing(SPACE_M)

        sections = (
            (tr("Nutzung auf eigene Verantwortung"), tr(
                "IO Center für Linux wird ohne Garantie oder Gewährleistung "
                "bereitgestellt. Du entscheidest selbst, die Software zu "
                "verwenden. Soweit gesetzlich zulässig, übernehmen die "
                "Mitwirkenden keine Haftung für Geräteschäden, Datenverlust, "
                "Fehlkonfigurationen, Ausfallzeiten oder sonstige direkte "
                "und indirekte Schäden.")),
            (tr("Unabhängig entwickelt"), tr(
                "Dieses Projekt ist nicht mit be quiet! verbunden und wird "
                "nicht von be quiet! unterstützt. Die Gerätekommunikation "
                "wurde unabhängig für kompatible Dark-Mount-Hardware "
                "nachvollzogen. Andere Modelle oder zukünftige Firmware "
                "können sich anders verhalten.")),
            (tr("Keine Firmware-Eingriffe"), tr(
                "Die Anwendung implementiert keine Firmware-Updates und "
                "keine Bootloader-Funktionen. Verwende für Firmware-Updates "
                "ausschließlich die offizielle Web-App von be quiet!. Ein "
                "vollständig risikofreier Hardwarezugriff kann trotzdem "
                "nicht zugesichert werden.")),
            (tr("Schreibvorgänge niemals unterbrechen"), tr(
                "Trenne während des Übertragens von Bildern, Einstellungen "
                "oder Beleuchtungseffekten weder die Tastatur noch das "
                "Media-Dock. Beende die Anwendung nicht und schalte den "
                "Rechner nicht aus, solange ein Fortschrittsfenster sichtbar "
                "ist. Ein Abbruch kann Daten auf dem Gerät unvollständig "
                "hinterlassen.")),
            (tr("Berechtigungen, Befehle und Sicherungen"), tr(
                "Die udev-Regel erlaubt deinem Benutzer den direkten Zugriff "
                "auf das Gerät und auf uinput. Hinterlegte Tastenbefehle "
                "laufen mit deinen Benutzerrechten — verwende deshalb nur "
                "vertrauenswürdige Kommandos und Dateien. Automatische "
                "Sicherungen können bei der Wiederherstellung helfen, sind "
                "aber keine Garantie für eine erfolgreiche Rettung.")),
        )

        background = self.palette().color(QPalette.ColorRole.Base)
        for section_title, section_text in sections:
            card = QFrame()
            card.setObjectName("SafetyCard")
            card.setStyleSheet(
                "QFrame#SafetyCard { background: rgba(%d,%d,%d,0.72);"
                " border: 1px solid %s; border-radius: 10px; }"
                % (background.red(), background.green(), background.blue(),
                   border()))
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(SPACE_L, SPACE_M, SPACE_L, SPACE_M)
            card_layout.setSpacing(SPACE_S)
            section_heading = QLabel(section_title)
            section_font = section_heading.font()
            section_font.setWeight(QFont.Weight.DemiBold)
            section_heading.setFont(section_font)
            card_layout.addWidget(section_heading)
            description = QLabel(section_text)
            description.setWordWrap(True)
            description.setStyleSheet("color: %s;" % muted())
            description.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            card_layout.addWidget(description)
            content_layout.addWidget(card)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        contact_row = QHBoxLayout()
        contact_row.setSpacing(SPACE_M)
        contact_text = QVBoxLayout()
        contact_text.setSpacing(0)
        contact_title = QLabel(tr("Fragen oder etwas Auffälliges entdeckt?"))
        contact_font = contact_title.font()
        contact_font.setWeight(QFont.Weight.DemiBold)
        contact_title.setFont(contact_font)
        contact_text.addWidget(contact_title)
        self.contact_feedback = hint(tr(
            "Discord-Namen kopieren und eine Nachricht senden."))
        contact_text.addWidget(self.contact_feedback)
        contact_row.addLayout(contact_text, 1)
        self.contact_button = QPushButton("Discord · %s" % DISCORD_USER)
        self.contact_button.setToolTip(tr("Discord-Benutzernamen kopieren"))
        self.contact_button.clicked.connect(self.copy_discord_user)
        contact_row.addWidget(self.contact_button)
        layout.addLayout(contact_row)

        layout.addWidget(separator())
        self.confirmation = QCheckBox(tr(
            "Ich habe die Hinweise gelesen und akzeptiere die Nutzung auf "
            "eigenes Risiko."))
        layout.addWidget(self.confirmation)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("Beenden"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.continue_button = primary_button(
            QPushButton(tr("Verstanden und fortfahren")))
        self.continue_button.setEnabled(False)
        self.continue_button.setDefault(True)
        self.continue_button.clicked.connect(self.accept)
        self.confirmation.toggled.connect(self.continue_button.setEnabled)
        buttons.addWidget(self.continue_button)
        layout.addLayout(buttons)

        if self.review_only:
            self.confirmation.hide()
            cancel.hide()
            self.continue_button.setText(tr("Schließen"))
            self.continue_button.setEnabled(True)

    def copy_discord_user(self):
        QApplication.clipboard().setText(DISCORD_USER)
        self.contact_feedback.setText(
            tr("Kopiert — in Discord unter „Freunde hinzufügen“ einfügen."))

    def accept(self):
        if self.review_only or self.confirmation.isChecked():
            super().accept()


class LicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Lizenz"))
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_M)
        layout.addWidget(heading(tr("Lizenz und Urheberrecht")))
        layout.addWidget(hint(
            tr("Copyright © 2026 %s · Veröffentlicht unter %s")
            % (bqmeta.AUTHOR, bqmeta.LICENSE_ID)))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(license_text())
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(text, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = primary_button(QPushButton(tr("Schließen")))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


class HelpDialog(QDialog):
    """Ruhiger zentraler Ort für Kontakt, Sicherheit und Systemzugriff."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Hilfe & Kontakt"))
        self.setModal(True)
        self.resize(620, 570)
        self.setMinimumSize(540, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_L)
        layout.addWidget(heading(tr("Hilfe & Kontakt")))
        layout.addWidget(hint(tr(
            "Systemzugriff prüfen, Hinweise nachlesen oder direkt Kontakt "
            "aufnehmen.")))

        contact = QFrame()
        contact.setObjectName("HelpCard")
        contact.setStyleSheet(self._card_style())
        contact_layout = QVBoxLayout(contact)
        contact_layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        contact_layout.setSpacing(SPACE_M)
        contact_layout.addWidget(self._card_heading(tr("Kontakt")))
        contact_layout.addWidget(hint(tr(
            "Bei Fragen, Fehlern oder ungewöhnlichem Geräteverhalten erreichst "
            "du den Entwickler über Discord.")))
        contact_row = QHBoxLayout()
        self.discord_feedback = QLabel("Discord · %s" % DISCORD_USER)
        self.discord_feedback.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        contact_row.addWidget(self.discord_feedback, 1)
        copy_discord = QPushButton(tr("Benutzernamen kopieren"))
        copy_discord.clicked.connect(self.copy_discord_user)
        contact_row.addWidget(copy_discord)
        contact_layout.addLayout(contact_row)
        contact_layout.addWidget(hint(
            tr("Version %s · %s") % (bqmeta.VERSION, bqmeta.LICENSE_ID)))
        layout.addWidget(contact)

        access = QFrame()
        access.setObjectName("HelpCard")
        access.setStyleSheet(self._card_style())
        access_layout = QVBoxLayout(access)
        access_layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        access_layout.setSpacing(SPACE_M)
        access_header = QHBoxLayout()
        access_header.addWidget(self._card_heading(tr("Geräte-Zugriff")), 1)
        self.access_badge = status_badge("")
        access_header.addWidget(self.access_badge)
        access_layout.addLayout(access_header)
        access_layout.addWidget(hint(tr(
            "Die App läuft ohne root. Eine eng begrenzte udev-Regel gibt dem "
            "aktiven Benutzer Zugriff auf Dark Mount und optional uinput.")))

        self.rule_status = QLabel()
        self.rule_status.setWordWrap(True)
        self.device_status = QLabel()
        self.device_status.setWordWrap(True)
        self.uinput_status = QLabel()
        self.uinput_status.setWordWrap(True)
        access_layout.addWidget(self.rule_status)
        access_layout.addWidget(self.device_status)
        access_layout.addWidget(self.uinput_status)

        access_buttons = QHBoxLayout()
        self.copy_udev_button = QPushButton(tr("Einrichtungsbefehl kopieren"))
        self.copy_udev_button.setToolTip(tr(
            "Kopiert die drei transparenten sudo-Befehle für das Terminal."))
        self.copy_udev_button.clicked.connect(self.copy_udev_command)
        access_buttons.addWidget(self.copy_udev_button)
        refresh = QPushButton(tr("Status aktualisieren"))
        refresh.clicked.connect(self.refresh_access_status)
        access_buttons.addWidget(refresh)
        self.remove_udev_button = QPushButton(tr("Regel entfernen …"))
        self.remove_udev_button.clicked.connect(self.copy_udev_remove_command)
        access_buttons.addWidget(self.remove_udev_button)
        access_buttons.addStretch(1)
        access_layout.addLayout(access_buttons)
        self.access_hint = hint(tr(
            "Nach der Einrichtung die Tastatur einmal trennen und wieder "
            "verbinden. AUR-, DEB- und RPM-Pakete installieren die Regel "
            "später automatisch."))
        access_layout.addWidget(self.access_hint)
        layout.addWidget(access)

        layout.addStretch(1)
        bottom = QHBoxLayout()
        safety = QPushButton(tr("Sicherheitshinweis anzeigen"))
        safety.clicked.connect(self.show_safety_notice)
        bottom.addWidget(safety)
        license_button = QPushButton(tr("Lizenz anzeigen"))
        license_button.clicked.connect(self.show_license)
        bottom.addWidget(license_button)
        bottom.addStretch(1)
        close = primary_button(QPushButton(tr("Schließen")))
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)

        self.refresh_access_status()

    def _card_style(self):
        background = self.palette().color(QPalette.ColorRole.Base)
        return (
            "QFrame#HelpCard { background: rgba(%d,%d,%d,0.72);"
            " border: 1px solid %s; border-radius: 10px; }"
            % (background.red(), background.green(), background.blue(),
               border()))

    @staticmethod
    def _card_heading(text):
        label = QLabel(text)
        font = label.font()
        font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
        return label

    def copy_discord_user(self):
        QApplication.clipboard().setText(DISCORD_USER)
        self.discord_feedback.setText(
            tr("Kopiert: %s") % DISCORD_USER)

    def copy_udev_command(self):
        QApplication.clipboard().setText(udev_install_command())
        self.access_hint.setText(tr(
            "Befehl kopiert. Im Terminal einfügen und anschließend die "
            "Tastatur neu verbinden."))

    def copy_udev_remove_command(self):
        answer = QMessageBox.question(
            self, tr("udev-Regel entfernen"),
            tr("Den Entfernungsbefehl für die manuell installierte udev-Regel "
               "kopieren? Danach verliert die App beim nächsten Verbinden den "
               "Gerätezugriff."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        QApplication.clipboard().setText(udev_remove_command())
        self.access_hint.setText(tr(
            "Entfernungsbefehl kopiert. Er löscht nur die Projektregel unter "
            "/etc und lädt udev neu."))

    def show_safety_notice(self):
        SafetyNoticeDialog(self, review_only=True).exec()

    def show_license(self):
        LicenseDialog(self).exec()

    def refresh_access_status(self):
        status = udev_diagnostics()
        connected = bool(status["nodes"])
        self.copy_udev_button.setVisible(not bool(status["rule_paths"]))
        manual_rule = "/etc/udev/rules.d/" + UDEV_RULE_NAME
        self.remove_udev_button.setVisible(manual_rule in status["rule_paths"])

        if status["hidraw_ready"] and status["uinput_ready"]:
            self.access_badge.setText(tr("Alles bereit"))
        elif status["hidraw_ready"]:
            self.access_badge.setText(tr("Grundfunktionen bereit"))
        elif connected:
            self.access_badge.setText(tr("Berechtigung fehlt"))
        elif status["rule_paths"]:
            self.access_badge.setText(tr("Regel installiert"))
        else:
            self.access_badge.setText(tr("Einrichtung erforderlich"))

        if status["rule_paths"]:
            self.rule_status.setText(
                tr("✓ Projektregel installiert: %s")
                % status["rule_paths"][0])
        elif status["hidraw_ready"]:
            self.rule_status.setText(tr(
                "✓ Zugriff funktioniert über eine andere lokale udev-Regel."))
        else:
            self.rule_status.setText(tr("○ Projektregel nicht installiert"))

        if not connected:
            self.device_status.setText(tr("○ Dark Mount nicht verbunden"))
        elif status["hidraw_ready"]:
            self.device_status.setText(
                tr("✓ Dark Mount erkannt — HID-Zugriff funktioniert"))
        else:
            names = ", ".join(status["inaccessible"])
            self.device_status.setText(
                tr("! Dark Mount erkannt — kein Zugriff auf %s") % names)

        if status["uinput_ready"]:
            self.uinput_status.setText(
                tr("✓ Virtuelle F13–F24-Tasten über uinput verfügbar"))
        elif status["uinput_exists"]:
            self.uinput_status.setText(
                tr("! uinput vorhanden, aber nicht beschreibbar"))
        else:
            self.uinput_status.setText(
                tr("○ uinput nicht verfügbar — virtuelle Tasten sind optional"))


# --------------------------------------------------------------- Hauptfenster ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("IO Center für Linux"))
        self.resize(860, 720)

        self._service_running = False
        self._vkbd = None
        self._busy_count = 0
        self._info_worker = None
        self._access_warning_dismissed = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())
        layout.addWidget(separator())
        self.access_warning = self._build_access_warning()
        layout.addWidget(self.access_warning)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.keys_page = KeysPage(self)
        self.dock_page = DockPage()
        self.lighting_page = LightingPage()
        for page, title in ((self.keys_page, tr("Tasten")),
                            (self.dock_page, tr("Media-Dock")),
                            (self.lighting_page, tr("Beleuchtung"))):
            page.status.connect(self.show_status)
            page.busy.connect(self.set_busy)
            self.tabs.addTab(page, title)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self.busy_overlay = BusyOverlay(root)
        self.busy_overlay.setGeometry(root.rect())
        self.keys_page.progress_changed.connect(self.busy_overlay.set_progress)
        self.dock_page.progress_changed.connect(self.busy_overlay.set_progress)

        self.setStatusBar(QStatusBar())
        # Ein einzelnes & ist bei QAbstractButton ein Mnemonic-Steuerzeichen.
        self.help_button = QPushButton(tr("Hilfe & Kontakt").replace("&", "&&"))
        self.help_button.setFlat(True)
        self.help_button.setToolTip(tr(
            "Kontakt, Sicherheitshinweis und Geräte-Zugriff"))
        self.help_button.clicked.connect(self.show_help)
        self.statusBar().addPermanentWidget(self.help_button)
        self.header_actions += (self.help_button,)
        self.statusBar().showMessage(tr("Verbinde …"))

        self.listener = HidListener()
        self.listener.pressed.connect(self.on_key_pressed)
        self.listener.status.connect(self.on_connection_status)
        self.listener.start()

        self.refresh_service_state()
        self.service_timer = QTimer(self)
        self.service_timer.timeout.connect(self.refresh_service_state)
        self.service_timer.start(4000)

        self.access_timer = QTimer(self)
        self.access_timer.timeout.connect(self.refresh_access_warning)
        self.access_timer.start(3000)
        QTimer.singleShot(0, self.refresh_access_warning)

    def _build_header(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(SPACE_XL, SPACE_L, SPACE_XL, SPACE_L)
        layout.setSpacing(SPACE_L)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Dark Mount")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() + 3)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        titles.addWidget(title)
        self.connection_label = hint(tr("Verbinde …"))
        titles.addWidget(self.connection_label)
        layout.addLayout(titles, 1)

        self.service_label = hint("")
        self.service_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.service_label)

        self.service_button = QPushButton(tr("Autostart aktivieren"))
        self.service_button.clicked.connect(self.toggle_service)
        layout.addWidget(self.service_button)

        self.info_button = QPushButton(tr("Geräteinfo"))
        self.info_button.clicked.connect(self.show_device_info)
        layout.addWidget(self.info_button)

        self.language_combo = QComboBox()
        self.language_combo.setToolTip(tr("Sprache"))
        for code, name in bqi18n.LANGUAGES.items():
            self.language_combo.addItem(name, code)
        index = self.language_combo.findData(bqi18n.language())
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        self.language_combo.currentIndexChanged.connect(self.change_language)
        layout.addWidget(self.language_combo)

        self.web_button = QPushButton(tr("Web-App"))
        self.web_button.setToolTip(tr("Öffnet iocenter.bequiet.com — dort laufen "
                                    "Firmware-Updates."))
        self.web_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://iocenter.bequiet.com/")))
        layout.addWidget(self.web_button)
        self.header_actions = (self.service_button, self.info_button,
                               self.language_combo, self.web_button)
        return widget

    def _build_access_warning(self):
        banner = QFrame()
        banner.setObjectName("AccessWarning")
        banner.setStyleSheet(
            "QFrame#AccessWarning { background: rgba(255, 166, 0, 0.10);"
            " border-bottom: 1px solid rgba(255, 166, 0, 0.42); }"
            "QLabel#AccessWarningIcon { background: rgba(255, 166, 0, 0.20);"
            " border: 1px solid rgba(255, 166, 0, 0.62);"
            " border-radius: 14px; font-weight: 700; }"
        )
        row = QHBoxLayout(banner)
        row.setContentsMargins(SPACE_XL, SPACE_M, SPACE_XL, SPACE_M)
        row.setSpacing(SPACE_M)

        icon = QLabel("!")
        icon.setObjectName("AccessWarningIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(28, 28)
        row.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(SPACE_XS)
        self.access_warning_title = QLabel()
        title_font = self.access_warning_title.font()
        title_font.setWeight(QFont.Weight.DemiBold)
        self.access_warning_title.setFont(title_font)
        text.addWidget(self.access_warning_title)
        self.access_warning_text = hint("")
        text.addWidget(self.access_warning_text)
        row.addLayout(text, 1)

        self.access_setup_button = primary_button(
            QPushButton(tr("Zugriff einrichten")))
        self.access_setup_button.clicked.connect(self.show_help)
        row.addWidget(self.access_setup_button,
                      alignment=Qt.AlignmentFlag.AlignVCenter)
        self.access_later_button = QPushButton(tr("Später"))
        self.access_later_button.setFlat(True)
        self.access_later_button.clicked.connect(
            self.dismiss_access_warning)
        row.addWidget(self.access_later_button,
                      alignment=Qt.AlignmentFlag.AlignVCenter)
        self.header_actions += (self.access_setup_button,
                                self.access_later_button)
        banner.hide()
        return banner

    # ---- Status ----

    def show_status(self, text, timeout=0):
        self.statusBar().showMessage(text, timeout)

    def show_help(self):
        HelpDialog(self).exec()
        self.refresh_access_warning()

    def dismiss_access_warning(self):
        self._access_warning_dismissed = True
        self.access_warning.hide()

    def refresh_access_warning(self):
        status = udev_diagnostics()
        reasons = access_setup_reasons(
            status, uinput_required=self.keys_page.uinput_enabled)
        if not reasons:
            self.access_warning.hide()
            return
        if self._access_warning_dismissed:
            return

        if "hidraw" in reasons:
            self.access_warning_title.setText(
                tr("Gerätezugriff noch nicht eingerichtet"))
            if "uinput" in reasons:
                message = tr(
                    "Dark Mount wurde erkannt, aber Linux blockiert den "
                    "HID-Zugriff. Auch virtuelle Tasten benötigen noch "
                    "Zugriff auf uinput.")
            else:
                message = tr(
                    "Dark Mount wurde erkannt, aber Linux blockiert den "
                    "direkten HID-Zugriff. Bilder, Einstellungen und "
                    "Beleuchtung funktionieren erst nach der Einrichtung.")
        else:
            self.access_warning_title.setText(
                tr("Virtuelle Tasten benötigen eine Berechtigung"))
            message = tr(
                "Der Gerätezugriff funktioniert, aber Linux blockiert "
                "/dev/uinput. F13–F24 können erst nach der Einrichtung "
                "erzeugt werden.")
        self.access_warning_text.setText(message)
        self.access_warning.show()

    def set_busy(self, busy, text=""):
        """Während einer Geräteoperation die komplette Bedienung sperren.

        Das Sperren der Reiter verhindert, dass nebenher eine zweite
        Operation gestartet wird -- die Tastatur beantwortet immer nur
        eine Anfrage zur Zeit.

        Gezählt wird mit, weil sich Operationen überlappen können, etwa ein
        Reiterwechsel während noch Bilder geladen werden. setOverrideCursor
        stapelt sich; ohne Zähler bliebe der Wartecursor hängen.
        """
        if busy:
            self._busy_count += 1
            if self._busy_count == 1:
                QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
                self.tabs.tabBar().setEnabled(False)
                self._busy_page = self.tabs.currentWidget()
                if self._busy_page is not None:
                    self._busy_page.setEnabled(False)
                for action in self.header_actions:
                    action.setEnabled(False)
            if text:
                self.statusBar().showMessage(text)
                self.busy_overlay.show_message(text)
        else:
            self._busy_count = max(0, self._busy_count - 1)
            if self._busy_count == 0:
                QApplication.restoreOverrideCursor()
                self.tabs.tabBar().setEnabled(True)
                page = getattr(self, "_busy_page", None)
                if page is not None:
                    page.setEnabled(True)
                for action in self.header_actions:
                    action.setEnabled(True)
                self.busy_overlay.finish()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "busy_overlay"):
            self.busy_overlay.setGeometry(self.centralWidget().rect())

    def has_active_device_jobs(self):
        """Erfasst alle schreibenden/abfragenden Threads vor Schließen/Neustart."""
        jobs = [
            self._info_worker,
            self.keys_page._loader, self.keys_page._writer,
            self.dock_page._reader, self.dock_page._writer,
            self.dock_page._worker, self.lighting_page._worker,
        ]
        return any(job is not None and job.isRunning() for job in jobs)

    def on_connection_status(self, text, connected):
        self.connection_label.setText(text)

    def show_device_info(self):
        if self._busy_count:
            return
        self.set_busy(True, tr("Lese Geräteinformationen …"))

        def read():
            device = bqdevice.Device()
            try:
                return device.read_info()
            finally:
                device.close()

        self._info_worker = Worker(read, tr("Geräteinformationen gelesen."))
        self._info_worker.done.connect(self.on_device_info)
        self._info_worker.start()

    def on_device_info(self, ok, message, info):
        self.set_busy(False, "")
        if not ok:
            QMessageBox.warning(
                self, tr("Geräteinfo"),
                tr("Geräteinformationen nicht verfügbar: %s") % message)
            return
        firmware = "\n".join(
            "MCU%d: %s" % (index, version)
            for index, version in enumerate(info["versions"]))
        QMessageBox.information(
            self, tr("Geräteinfo"),
            "%s: %d\n%s: %d\n%s: %s\n\n%s:\n%s\n\n%s"
            % (tr("Modell"), info["model"],
               tr("Hardware-Revision"), info["revision"],
               tr("Seriennummer"), info["serial"],
               tr("Firmware"), firmware, info["path"]))

    def change_language(self, index):
        code = self.language_combo.itemData(index)
        if code == bqi18n.language():
            return
        if self._busy_count or self.has_active_device_jobs():
            back = self.language_combo.findData(bqi18n.language())
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(back)
            self.language_combo.blockSignals(False)
            self.show_status(tr("Bitte warten, bis der laufende Vorgang beendet ist."),
                             5000)
            return

        # Die Oberfläche baut ihre Texte beim Erzeugen auf; statt jedes
        # Widget nachträglich umzubeschriften, startet die Anwendung neu.
        # Das ist der ehrlichere Weg -- so bleibt garantiert nichts stehen.
        answer = QMessageBox.question(
            self, tr("Sprache umstellen"),
            tr("Die Anwendung startet neu, um die Sprache zu übernehmen. "
               "Fortfahren?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            back = self.language_combo.findData(bqi18n.language())
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(back)
            self.language_combo.blockSignals(False)
            return

        if not self.lighting_page.stop_meter():
            back = self.language_combo.findData(bqi18n.language())
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(back)
            self.language_combo.blockSignals(False)
            self.show_status(tr("Der Monitor wird noch beendet …"), 5000)
            return
        if not self.listener.stop():
            back = self.language_combo.findData(bqi18n.language())
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(back)
            self.language_combo.blockSignals(False)
            self.show_status(tr("Die Geräteverbindung wird noch beendet …"),
                             5000)
            return
        bqi18n.save_language(code)
        if self._vkbd is not None:
            self._vkbd.close()
            self._vkbd = None
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def on_tab_changed(self, index):
        # Einstellungen erst lesen, wenn die Seite wirklich gebraucht wird.
        if self.tabs.widget(index) is self.dock_page and self.dock_page.settings is None:
            self.dock_page.load_settings()

    # ---- Tastendruck ----

    def on_key_pressed(self, key_id):
        if self.keys_page.on_key_pressed(key_id, self._service_running):
            self.trigger_locally(key_id)

    def trigger_locally(self, key_id):
        """Führt die Belegung aus, solange kein Dienst läuft."""
        page = self.keys_page
        done = []

        code = page.vkeys.get(key_id)
        if code is not None:
            try:
                if self._vkbd is None:
                    self._vkbd = bqkeyd.VirtualKeyboard(
                        set(page.vkeys.values()) or {bqkeyd.KEY_F13})
                self._vkbd.tap(code)
                done.append({v: k for k, v in bqkeyd.KEY_NAMES.items()}[code])
            except OSError as exc:
                self.show_status(tr("Virtuelle Taste nicht möglich: %s") % exc, 4000)

        command = page.commands.get(key_id)
        if command:
            try:
                if isinstance(command, list):
                    subprocess.Popen(command, start_new_session=True)
                else:
                    subprocess.Popen(command, shell=True, start_new_session=True,
                                     executable="/bin/sh")
            except Exception as exc:
                self.show_status(tr("Start fehlgeschlagen: %s") % exc, 5000)
                return
            done.append(command if isinstance(command, str)
                        else " ".join(command))

        slot = key_id - bqkeyd.FIRST_KEY + 1
        self.show_status(tr("Taste %d  →  %s") % (slot, "  ·  ".join(done))
                         if done else tr("Taste %d — nicht belegt") % slot, 3000)

    # ---- Dienst ----

    @staticmethod
    def service_path():
        return os.path.expanduser("~/.config/systemd/user/" + SERVICE)

    @staticmethod
    def service_installed():
        return os.path.isfile(MainWindow.service_path())

    @staticmethod
    def service_active():
        systemctl = shutil.which("systemctl")
        if systemctl is None:
            return False
        try:
            result = subprocess.run(
                [systemctl, "--user", "is-active", SERVICE],
                capture_output=True, text=True, timeout=4)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.stdout.strip() == "active"

    @staticmethod
    def systemd_user_available():
        """Prüfen, ob nicht nur systemctl, sondern auch der User-Bus läuft."""
        systemctl = shutil.which("systemctl")
        if systemctl is None:
            return False, tr("systemctl wurde nicht gefunden.")
        try:
            result = subprocess.run(
                [systemctl, "--user", "show-environment"],
                capture_output=True, text=True, timeout=4)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if result.returncode:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, ""

    def refresh_service_state(self):
        self._service_running = self.service_installed() and self.service_active()

        if not self.service_installed():
            self.service_label.setText(tr("Tasten wirken nur bei offenem Fenster"))
            self.service_button.setText(tr("Autostart aktivieren"))
            return

        if self._service_running:
            self.service_label.setText(tr("Dienst läuft"))
            self.service_button.setText(tr("Autostart entfernen"))
            if self._vkbd is not None:
                self._vkbd.close()
                self._vkbd = None
        else:
            self.service_label.setText(tr("Dienst angehalten"))
            self.service_button.setText(tr("Autostart entfernen"))

    def toggle_service(self):
        if self.service_installed():
            self.remove_service()
            return
        self.install_service()

    def install_service(self):
        available, error = self.systemd_user_available()
        if not available:
            QMessageBox.warning(
                self, tr("Autostart"),
                tr("Der systemd-Userdienst ist nicht verfügbar: %s") % error)
            return

        target_dir = os.path.dirname(self.service_path())
        try:
            os.makedirs(target_dir, exist_ok=True)
            bqconfig.atomic_write_text(self.service_path(), service_unit_text())
        except OSError as exc:
            QMessageBox.critical(self, tr("Autostart"), str(exc))
            return
        systemctl = shutil.which("systemctl")
        for args in (["daemon-reload"], ["enable", "--now", SERVICE]):
            try:
                result = subprocess.run(
                    [systemctl, "--user"] + args,
                    capture_output=True, text=True, timeout=8)
            except (OSError, subprocess.SubprocessError) as exc:
                QMessageBox.warning(self, "systemd", str(exc))
                self.refresh_service_state()
                return
            if result.returncode:
                QMessageBox.warning(self, "systemd", result.stderr)
                self.refresh_service_state()
                return
        self.refresh_service_state()
        self.show_status(tr("Autostart eingerichtet — die Tasten wirken jetzt "
                         "dauerhaft."), 5000)

    def remove_service(self):
        answer = QMessageBox.question(
            self, tr("Autostart entfernen"),
            tr("Den Tastendienst anhalten und aus dem Autostart entfernen?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        available, error = self.systemd_user_available()
        if not available:
            QMessageBox.warning(
                self, tr("Autostart"),
                tr("Der systemd-Userdienst ist nicht verfügbar: %s") % error)
            return
        systemctl = shutil.which("systemctl")
        try:
            result = subprocess.run(
                [systemctl, "--user", "disable", "--now", SERVICE],
                capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            QMessageBox.warning(self, "systemd", str(exc))
            return
        if result.returncode:
            QMessageBox.warning(self, "systemd", result.stderr)
            return
        try:
            os.unlink(self.service_path())
        except FileNotFoundError:
            pass
        except OSError as exc:
            QMessageBox.warning(self, tr("Autostart"), str(exc))
            return
        try:
            subprocess.run([systemctl, "--user", "daemon-reload"],
                           capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            QMessageBox.warning(self, "systemd", str(exc))
        self._service_running = False
        self.refresh_service_state()
        self.show_status(tr("Autostart entfernt."), 5000)

    def restart_service_if_running(self):
        if self._service_running:
            subprocess.run(["systemctl", "--user", "restart", SERVICE],
                           capture_output=True)
            self.show_status(tr("Gespeichert — Dienst neu gestartet."), 3000)

    def bindings_changed(self):
        """Neue Belegung lokal und im optionalen Dienst wirksam machen."""
        # uinput akzeptiert nur die beim Erzeugen freigegebenen Keycodes.
        # Nach einer geänderten F-Taste muss das lokale Gerät neu entstehen.
        if self._vkbd is not None:
            self._vkbd.close()
            self._vkbd = None
        self.restart_service_if_running()

    # ---- Ende ----

    def closeEvent(self, event):
        if self._busy_count or self.has_active_device_jobs():
            event.ignore()
            text = tr("Bitte warten, bis der laufende Vorgang beendet ist.")
            self.show_status(text, 5000)
            if self._busy_count:
                self.busy_overlay.show_message(text)
            return
        if not self.lighting_page.stop_meter():
            event.ignore()
            self.show_status(tr("Der Monitor wird noch beendet …"), 5000)
            return
        if not self.listener.stop():
            event.ignore()
            self.show_status(tr("Die Geräteverbindung wird noch beendet …"),
                             5000)
            return
        if self._vkbd is not None:
            self._vkbd.close()
            self._vkbd = None
        super().closeEvent(event)


def fix_placeholder_contrast(app):
    """Platzhaltertexte lesbar halten.

    Nicht jedes Design belegt die Rolle PlaceholderText; fehlt sie, bleibt
    sie schwarz und verschwindet in dunklen Oberflächen. Nur wenn der
    Kontrast zum Eingabefeld zu gering ist, wird sie aus der Textfarbe
    abgeleitet -- ein sauber gesetztes Design bleibt unangetastet.
    """
    palette = app.palette()
    base = palette.color(QPalette.ColorRole.Base)
    placeholder = palette.color(QPalette.ColorRole.PlaceholderText)
    if abs(placeholder.lightness() - base.lightness()) >= 60:
        return
    faded = QColor(palette.color(QPalette.ColorRole.Text))
    faded.setAlphaF(0.55)
    palette.setColor(QPalette.ColorRole.PlaceholderText, faded)
    app.setPalette(palette)


def main():
    if sys.argv[1:] == ["--version"]:
        print("iocenter-linux %s" % bqmeta.VERSION)
        return 0

    bqpaths.ensure_user_config()
    bqpaths.migrate_legacy_backups()
    bqi18n.set_language(bqi18n.detect_language())

    app = QApplication(sys.argv)
    app.setOrganizationName(bqmeta.AUTHOR)
    app.setOrganizationDomain("github.com")
    app.setApplicationName(bqmeta.APP_ID)
    app.setApplicationDisplayName(tr("IO Center für Linux"))
    app.setApplicationVersion(bqmeta.VERSION)
    app.setDesktopFileName(bqmeta.APP_ID)
    app.setWindowIcon(app_icon())
    fix_placeholder_contrast(app)

    settings = app_settings()
    if not safety_notice_accepted(settings):
        notice = SafetyNoticeDialog()
        if notice.exec() != QDialog.DialogCode.Accepted:
            return 0
        remember_safety_notice(settings)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
