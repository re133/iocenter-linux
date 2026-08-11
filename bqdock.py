#!/usr/bin/env python3
"""
bqdock -- Media-Dock der be quiet! Dark Mount ansteuern.

Das Dock ist das Modul links oben mit eigenem 320x240-Display und den
Firmware-Ansichten CLOCK, ILLUMINATION, BRIGHTNESS, PROFILE, MEDIA, CUSTOM.
Dieses Modul kann:

  * die Leerlaufgrafik lesen und setzen (eigenes Bild auf dem Display)
  * Menuefarbe, Anzeigemodus (Bild/Uhr), Uhrzeitformat und die beiden
    Zeitlimits lesen und schreiben

SICHERHEIT
    Whitelist mit sechs eng begrenzten Kommandos. Fünf sind aus Mitschnitten
    der offiziellen Web-App belegt. Der zusätzliche reine Leseweg 21 06 ist
    durch die Firmware-Analyse beschrieben und am Gerät verifiziert:

        21 01   Modulinformation abfragen
        21 02   Einstellungen lesen
        21 03   Einstellungen schreiben
        21 05   Uhr stellen
        21 06   Leerlaufgrafik lesen
        21 07   Leerlaufgrafik schreiben

    Jedes andere Kommando weist _send() ab, bevor ein Byte das Programm
    verlaesst. Firmware ist ueber diesen Weg nicht erreichbar: der
    Bootloader meldet sich als eigenes USB-Geraet (PID 0x0009).

HINWEIS ZUM VERSCHLEISS
    Das Display nimmt unkomprimiertes RGB565 entgegen -- ein Bild sind
    153609 Byte in 3136 Reports und dauert rund fuenf Sekunden. Es landet
    als Leerlaufgrafik im Flash. Haeufiges Ueberschreiben ist deshalb
    nicht ratsam; write_image() erzwingt eine Mindestpause.

VERWENDUNG
    ./bqdock.py --settings                 aktuelle Einstellungen anzeigen
    ./bqdock.py --read-image /tmp/dock.png Leerlaufgrafik lesen
    ./bqdock.py --image ~/bild.png         Leerlaufgrafik setzen
    ./bqdock.py --color '#00ff00'          Menuefarbe setzen
    ./bqdock.py --display image|clock      Anzeigemodus umschalten
"""

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqlink  # noqa: E402
import bqconfig  # noqa: E402
import bqpaths  # noqa: E402

# --- Whitelist -------------------------------------------------------------
CMD_INFO = (0x21, 0x01)
CMD_SETTINGS_READ = (0x21, 0x02)
CMD_SETTINGS_WRITE = (0x21, 0x03)
CMD_SET_TIME = (0x21, 0x05)
CMD_IMAGE_READ = (0x21, 0x06)
CMD_IMAGE_WRITE = (0x21, 0x07)
ALLOWED_COMMANDS = {CMD_INFO, CMD_SETTINGS_READ, CMD_SETTINGS_WRITE,
                    CMD_SET_TIME, CMD_IMAGE_READ, CMD_IMAGE_WRITE}

DISPLAY_SIZE = (320, 240)
DISPLAY_FORMAT = 1            # rohes RGB565
HEADER_SIZE = 9
WRITE_CHUNK = 49
READ_CHUNK = 54
TIMEOUT = 3.0

# 320 * 240 * 2 + 9 Byte Kopf. Exakt diese Groesse hat die offizielle
# Software geschrieben; mehr darf es nicht werden.
IMAGE_BYTES = DISPLAY_SIZE[0] * DISPLAY_SIZE[1] * 2 + HEADER_SIZE

# Mindestabstand zwischen zwei Bildschreibvorgaengen, in Sekunden.
MIN_WRITE_INTERVAL = 20.0
_last_write = [0.0]
WRITE_STAMP_PATH = bqpaths.DOCK_WRITE_STAMP_PATH


def _recorded_write_time():
    """Letzten Flash-Schreibzeitpunkt auch über Prozessgrenzen erhalten."""
    try:
        with open(WRITE_STAMP_PATH, encoding="ascii") as handle:
            persisted = float(handle.read().strip())
    except (OSError, ValueError):
        persisted = 0.0
    return max(_last_write[0], persisted)


def _record_write_time(stamp):
    _last_write[0] = stamp
    try:
        bqconfig.atomic_write_text(WRITE_STAMP_PATH, "%.6f" % stamp)
    except OSError:
        # Die laufende Instanz bleibt auch ohne beschreibbaren Cache geschützt.
        pass

# Byte 11 waehlt, was im Leerlauf erscheint; Byte 10 das Uhrzeitformat.
# Diese Zuordnung ist mit dock_test.py belegt: bei festgehaltenem Byte 10
# wechselte die Anzeige mit Byte 11, nicht umgekehrt.
DISPLAY_CLOCK = 1          # Byte 11
DISPLAY_IMAGE = 2          # Byte 11
CLOCK_24H = 1              # Byte 10
CLOCK_12H = 0              # Byte 10


class Settings:
    """Die neun Nutzbytes von 0x21 0x02 / 0x21 0x03."""

    __slots__ = ("red", "green", "blue", "display", "clock_format",
                 "idle_seconds", "off_seconds")

    def __init__(self, red=0xDC, green=0x4D, blue=0x00, display=DISPLAY_CLOCK,
                 clock_format=CLOCK_24H, idle_seconds=30, off_seconds=0):
        # Reihenfolge der Argumente bleibt wie gehabt; im Report steht das
        # Uhrzeitformat aber VOR der Anzeige, siehe pack().
        self.red, self.green, self.blue = red, green, blue
        self.display = display
        self.clock_format = clock_format
        self.idle_seconds = idle_seconds
        self.off_seconds = off_seconds

    @classmethod
    def parse(cls, payload):
        if len(payload) < 9:
            raise ValueError("Einstellungen zu kurz: %d Byte" % len(payload))
        red, green, blue, clock_format, display = payload[:5]
        idle, off = struct.unpack_from("<HH", payload, 5)
        return cls(red, green, blue, display, clock_format, idle, off)

    def pack(self):
        return (bytes((self.red, self.green, self.blue, self.clock_format,
                       self.display))
                + struct.pack("<HH", self.idle_seconds, self.off_seconds))

    @property
    def color(self):
        return "#%02x%02x%02x" % (self.red, self.green, self.blue)

    @color.setter
    def color(self, value):
        value = value.lstrip("#")
        if len(value) != 6:
            raise ValueError("Farbe muss #rrggbb sein, nicht %r" % value)
        self.red, self.green, self.blue = (int(value[i:i + 2], 16)
                                           for i in (0, 2, 4))

    def __str__(self):
        return ("Menüfarbe %s, Anzeige %s, Uhrzeit %s, Leerlaufgrafik nach "
                "%d s, Display aus nach %s"
                % (self.color,
                   "Bild" if self.display == DISPLAY_IMAGE else "Uhr",
                   "12 h" if self.clock_format == CLOCK_12H else "24 h",
                   self.idle_seconds,
                   "%d s" % self.off_seconds if self.off_seconds else "nie"))


class Dock:
    def __init__(self):
        self.link = bqlink.QLink(ALLOWED_COMMANDS, timeout=TIMEOUT)
        self.path = self.link.path

    def close(self):
        self.link.close()

    def _request(self, command, payload=b"", offset=None):
        """Baut einen Report und sendet ihn.

        offset=None  -> Nutzlast beginnt bei Byte 7 (Info und Einstellungen)
        offset=n     -> Byte 8..11 tragen den Offset, Nutzlast ab Byte 12
                        (so ueberträgt die Web-App das Bild)
        """
        # Erst pruefen, dann bauen -- kein Zustand aendert sich, wenn etwas
        # unzulaessig ist.
        if command not in ALLOWED_COMMANDS:
            raise ValueError(
                "Kommando %02x %02x steht nicht auf der Whitelist -- "
                "dieses Programm sendet es nicht." % command)
        limit = 54 if offset is None else WRITE_CHUNK
        if len(payload) > limit:
            raise ValueError("Nutzlast zu groß: %d Byte (erlaubt %d)"
                             % (len(payload), limit))
        if offset is not None and not 0 <= offset <= IMAGE_BYTES:
            raise ValueError("Offset %d außerhalb des Bildbereichs" % offset)

        if offset is None:
            raw = payload
        else:
            # Byte 7 ist der Bildtyp (derzeit ausschließlich 0), danach
            # folgen Offset und die eigentlichen Daten.
            raw = bytes((0,)) + struct.pack("<I", offset) + payload
        return self.link.request(command, raw)

    # ---- Einstellungen ----

    def read_settings(self):
        return Settings.parse(self._request(CMD_SETTINGS_READ))

    def write_settings(self, settings):
        self._request(CMD_SETTINGS_WRITE, settings.pack())

    # ---- Uhrzeit ----

    def set_time(self, when=None):
        """Stellt die Uhr des Docks.

        Die Web-App sendet beim Verbinden dieselbe Nachricht -- ohne sie
        läuft die CLOCK-Ansicht mit der Zeit davon. Übertragen wird die
        lokale Zeit als Sekunden seit 1970, denn das Gerät kennt keine
        Zeitzone und zeigt den Wert unverändert an.
        """
        if when is None:
            when = time.time()
        stamp = int(when) + int(-time.timezone if not time.localtime().tm_isdst
                                else -time.altzone)
        self._request(CMD_SET_TIME, struct.pack("<I", stamp))
        return stamp

    def confirmed_time(self, timeout=2.0):
        """Wartet auf die Bestätigung, die das Gerät von sich aus schickt.

        Nach 0x21 0x05 meldet die Tastatur unaufgefordert 0x21 0x03 mit
        einer 4 im ersten Byte und der übernommenen Zeit dahinter. Weil die
        Meldung nicht angefordert ist, trägt sie Sequenznummer 0 und lässt
        sich nicht über _response() abholen.

        Nützlich, weil am Display nichts zu sehen ist: Das Dock zeigt
        während der Verbindung sein Menü, nicht die Uhr.
        """
        payload, _frame = self.link.read_notification(
            CMD_SETTINGS_WRITE,
            predicate=lambda frame: frame[4] == 0 and frame[7] == 0x04,
            timeout=timeout)
        if payload is None or len(payload) < 5:
            return None
        return struct.unpack_from("<I", payload, 1)[0]

    # ---- Leerlaufgrafik ----

    def read_image(self, progress=None, image_type=0):
        """Liest die aktuelle Screensaver-Grafik (Typ 0) als RGB565.

        0x21 0x06 ist ein reines Lesekommando. Das Antwortformat ist durch
        die Media-Dock-Firmware und einen zweiten Linux-Client belegt; die
        strikten Größen- und Formatprüfungen verhindern, dass eine fehlerhafte
        Antwort als Bild weiterverarbeitet wird.
        """
        if image_type != 0:
            raise ValueError("Nur Screensaver-Bildtyp 0 ist erlaubt.")

        def read_chunk(offset, length):
            payload = bytes((image_type,)) + struct.pack("<II", offset, length)
            return self._request(CMD_IMAGE_READ, payload)

        header = read_chunk(0, HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            raise IOError("Bildkopf zu kurz: %d Byte" % len(header))
        total, width, height, fmt = struct.unpack_from("<IHHB", header, 0)
        if (total, width, height, fmt) != (
                IMAGE_BYTES, DISPLAY_SIZE[0], DISPLAY_SIZE[1], DISPLAY_FORMAT):
            raise IOError(
                "Unerwarteter Bildkopf: %d Byte, %dx%d, Format %d"
                % (total, width, height, fmt))

        rgb565 = bytearray()
        offset = HEADER_SIZE
        while offset < total:
            length = min(READ_CHUNK, total - offset)
            chunk = read_chunk(offset, length)
            if not chunk:
                raise IOError("Leere Bildantwort bei Offset %d" % offset)
            chunk = chunk[:length]
            rgb565.extend(chunk)
            offset += len(chunk)
            if progress:
                progress(offset, total)

        expected = DISPLAY_SIZE[0] * DISPLAY_SIZE[1] * 2
        if len(rgb565) != expected:
            raise IOError("Bilddaten unvollständig: %d statt %d Byte"
                          % (len(rgb565), expected))
        return bytes(rgb565)

    def write_image(self, rgb565, progress=None, force=False):
        if len(rgb565) != DISPLAY_SIZE[0] * DISPLAY_SIZE[1] * 2:
            raise ValueError(
                "Erwartet werden %d Byte RGB565 für %dx%d, erhalten %d."
                % (DISPLAY_SIZE[0] * DISPLAY_SIZE[1] * 2, DISPLAY_SIZE[0],
                   DISPLAY_SIZE[1], len(rgb565)))

        now = time.time()
        waited = max(0.0, now - _recorded_write_time())
        if not force and waited < MIN_WRITE_INTERVAL:
            raise RuntimeError(
                "Zu kurz nach dem letzten Schreibvorgang (%.0f s). Das Bild "
                "landet im Flash; bitte %.0f s warten."
                % (waited, MIN_WRITE_INTERVAL - waited))

        blob = struct.pack("<IHHB", IMAGE_BYTES, DISPLAY_SIZE[0],
                           DISPLAY_SIZE[1], DISPLAY_FORMAT) + rgb565
        assert len(blob) == IMAGE_BYTES

        offset = 0
        while offset < len(blob):
            chunk = blob[offset:offset + WRITE_CHUNK]
            self._request(CMD_IMAGE_WRITE, chunk, offset)
            offset += len(chunk)
            if progress:
                progress(offset, len(blob))
        _record_write_time(time.time())
        return len(blob)


def to_rgb565(path_or_image):
    """Bild auf 320x240 bringen und in rohes RGB565 wandeln."""
    from PIL import Image

    image = (path_or_image if hasattr(path_or_image, "convert")
             else Image.open(path_or_image))
    image = image.convert("RGB")

    # Auf 320x240 füllen, mittig zuschneiden, Seitenverhältnis wahren.
    target_ratio = DISPLAY_SIZE[0] / DISPLAY_SIZE[1]
    ratio = image.width / image.height
    if ratio > target_ratio:
        width = int(image.height * target_ratio)
        left = (image.width - width) // 2
        image = image.crop((left, 0, left + width, image.height))
    elif ratio < target_ratio:
        height = int(image.width / target_ratio)
        top = (image.height - height) // 2
        image = image.crop((0, top, image.width, top + height))
    image = image.resize(DISPLAY_SIZE, Image.LANCZOS)

    out = bytearray()
    for r, g, b in image.getdata():
        out += struct.pack("<H", ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3))
    return bytes(out)


def from_rgb565(data):
    """Gegenstueck zu to_rgb565 -- fuer Vorschau und Kontrolle."""
    from PIL import Image
    return Image.frombytes("RGB", DISPLAY_SIZE, data, "raw", "BGR;16")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", action="store_true",
                    help="aktuelle Einstellungen anzeigen")
    ap.add_argument("--image", metavar="DATEI", help="Leerlaufgrafik setzen")
    ap.add_argument("--read-image", metavar="DATEI",
                    help="aktuelle Leerlaufgrafik als PNG lesen")
    ap.add_argument("--color", metavar="#RRGGBB", help="Menüfarbe setzen")
    ap.add_argument("--display", choices=("image", "clock"),
                    help="Anzeigemodus umschalten")
    ap.add_argument("--clock-format", choices=("12", "24"),
                    help="Uhrzeitformat")
    ap.add_argument("--idle", type=int, metavar="SEK",
                    help="Leerlaufgrafik nach … Sekunden")
    ap.add_argument("--set-time", action="store_true",
                    help="Uhr des Docks auf die Systemzeit stellen")
    args = ap.parse_args()

    dock = Dock()
    try:
        settings = dock.read_settings()
        print("Aktuell: %s" % settings)

        changed = False
        if args.color:
            settings.color = args.color
            changed = True
        if args.display:
            settings.display = (DISPLAY_IMAGE if args.display == "image"
                                else DISPLAY_CLOCK)
            changed = True
        if args.clock_format:
            settings.clock_format = (CLOCK_12H if args.clock_format == "12"
                                     else CLOCK_24H)
            changed = True
        if args.idle is not None:
            settings.idle_seconds = args.idle
            changed = True

        if args.image:
            data = to_rgb565(args.image)
            print("Schreibe Leerlaufgrafik (%d Byte, dauert etwa 5 s)…"
                  % IMAGE_BYTES)

            def show(done, total):
                print("\r  %6d / %6d Byte" % (done, total), end="", flush=True)

            backup_dir = bqpaths.DOCK_BACKUP_DIR
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(
                backup_dir,
                "dock-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
            previous = dock.read_image()
            from_rgb565(previous).save(backup, format="PNG")
            dock.write_image(data, show)
            print("\n  prüfe durch Zurücklesen …")
            if dock.read_image() != data:
                raise IOError("Abweichung beim Zurücklesen; Sicherung: %s"
                              % backup)
            print("  gesichert und geprüft; Sicherung: %s" % backup)

        if args.read_image:
            print("Lese Leerlaufgrafik (%d Byte)…" % IMAGE_BYTES)

            def show_read(done, total):
                print("\r  %6d / %6d Byte" % (done, total), end="", flush=True)

            data = dock.read_image(show_read)
            output = os.path.abspath(os.path.expanduser(args.read_image))
            from_rgb565(data).save(output, format="PNG")
            print("\n  gespeichert: %s" % output)

        if args.set_time:
            stamp = dock.set_time()
            confirmed = dock.confirmed_time()
            print("Uhr gestellt auf %s."
                  % time.strftime("%d.%m.%Y %H:%M:%S", time.gmtime(stamp)))
            if confirmed is None:
                print("  (keine Bestätigung vom Gerät erhalten)")
            elif confirmed == stamp:
                print("  vom Gerät bestätigt.")
            else:
                print("  Gerät meldet abweichend: %s"
                      % time.strftime("%d.%m.%Y %H:%M:%S",
                                      time.gmtime(confirmed)))

        if changed:
            dock.write_settings(settings)
            print("Neu:     %s" % dock.read_settings())
    finally:
        dock.close()


if __name__ == "__main__":
    main()
