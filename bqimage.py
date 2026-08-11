#!/usr/bin/env python3
"""
bqimage -- liest die Bilder der Display Keys von der be quiet! Dark Mount.

Eng begrenztes Werkzeug für die Display-Key-Bilder:

  * Erlaubt sind ausschließlich 0x20 0x03 (lesen) und 0x20 0x02 (schreiben).
    Jedes andere Kommando wird vor dem HID-Zugriff abgewiesen.
  * Vor jedem Schreiben wird gesichert und danach byteweise zurückgelesen.
  * Firmware-Funktionalitaet ist nicht implementiert und wird es nicht.
    Fuer Firmware-Updates ist ausschliesslich die offizielle Web-App unter
    https://iocenter.bequiet.com/ zustaendig.

VERWENDUNG
    ./bqimage.py --verify 2      Taste 2 lesen und mit dem aus dem Mitschnitt
                                 extrahierten JPEG vergleichen (empfohlener
                                 erster Test)
    ./bqimage.py --key 3 -o /tmp/k3.jpg
    ./bqimage.py --all -d ~/.local/state/iocenter-linux/images
"""

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqkeyd  # noqa: E402
import bqlink  # noqa: E402
import bqpaths  # noqa: E402

# --- Whitelist -------------------------------------------------------------
# Nur diese Kommandos darf dieses Programm erzeugen. Beide sind vollstaendig
# aus einem Mitschnitt der offiziellen Web-App belegt, nicht geraten.
CMD_READ_IMAGE = (0x20, 0x03)
CMD_WRITE_IMAGE = (0x20, 0x02)
ALLOWED_COMMANDS = {CMD_READ_IMAGE, CMD_WRITE_IMAGE}

HEADER_SIZE = 9          # uint32 Gesamtlaenge, uint16 Breite, uint16 Hoehe, uint8 Format
READ_CHUNK = 54          # Nutzbytes pro Antwort
WRITE_CHUNK = 49         # Nutzbytes pro Anfrage
TIMEOUT = 2.0

# Geraeteeigenschaften aus dem Mitschnitt.
IMAGE_SIZE = (120, 120)
IMAGE_FORMAT = 3

# Wie gross der Speicherbereich je Taste wirklich ist, ist nicht bekannt.
# Das groesste beobachtete Bild war 17144 Byte; diese Grenze liegt bewusst
# knapp darueber, damit ein fehlerhaft berechnetes Bild nicht ueber den
# Bildbereich hinausschreiben kann.
MAX_IMAGE_BYTES = 32 * 1024

crc16_modbus = bqlink.crc16_modbus


class Keyboard:
    def __init__(self):
        self.link = bqlink.QLink(ALLOWED_COMMANDS)
        self.path = self.link.path

    def close(self):
        self.link.close()

    def _request(self, command, key_id, offset, payload):
        """Baut einen Report und sendet ihn. payload steht ab Byte 13."""
        if command not in ALLOWED_COMMANDS:
            raise ValueError(
                "Kommando %02x %02x steht nicht auf der Whitelist -- "
                "dieses Programm sendet es nicht." % command)
        if key_id not in bqkeyd.DISPLAY_KEYS:
            raise ValueError("Unzulässige Tasten-ID 0x%02x" % key_id)
        if not 0 <= offset <= MAX_IMAGE_BYTES:
            raise ValueError("Offset %d außerhalb des Bildbereichs" % offset)
        if len(payload) > WRITE_CHUNK:
            raise ValueError("Nutzlast zu groß: %d Byte" % len(payload))

        raw = bytes((key_id, 0)) + struct.pack("<I", offset) + payload
        return self.link.request(command, raw)

    def read_image(self, key_id, progress=None):
        header = self._request(
            CMD_READ_IMAGE, key_id, 0, bytes((HEADER_SIZE,)))
        if len(header) < HEADER_SIZE:
            raise IOError("Kopf zu kurz: %d Byte" % len(header))
        total, width, height, fmt = struct.unpack_from("<IHHB", header, 0)
        if not HEADER_SIZE < total <= MAX_IMAGE_BYTES:
            raise IOError("Unplausible Bildgröße: %d" % total)
        if (width, height, fmt) != (IMAGE_SIZE[0], IMAGE_SIZE[1], IMAGE_FORMAT):
            raise IOError("Unerwarteter Bildkopf: %dx%d, Format %d"
                          % (width, height, fmt))

        blob = bytearray()
        offset = HEADER_SIZE
        while offset < total:
            length = min(READ_CHUNK, total - offset)
            chunk = self._request(
                CMD_READ_IMAGE, key_id, offset, bytes((length,)))
            if not chunk:
                raise IOError("Leere Antwort bei Offset %d" % offset)
            blob.extend(chunk[:length])
            offset += len(chunk[:length])
            if progress:
                progress(offset, total)
        return bytes(blob), width, height, fmt


    def write_image(self, key_id, jpeg, progress=None):
        """Schreibt ein JPEG auf eine Taste. Ruft KEIN Backup auf -- das macht
        der Aufrufer, siehe cmd_write()."""
        if jpeg[:2] != b"\xff\xd8" or jpeg[-2:] != b"\xff\xd9":
            raise ValueError("Kein vollständiges JPEG (SOI/EOI fehlt).")
        total = HEADER_SIZE + len(jpeg)
        if total > MAX_IMAGE_BYTES:
            raise ValueError(
                "Bild zu groß: %d Byte, erlaubt sind %d. Stärker komprimieren."
                % (total, MAX_IMAGE_BYTES))

        blob = struct.pack("<IHHB", total, IMAGE_SIZE[0], IMAGE_SIZE[1],
                           IMAGE_FORMAT) + jpeg

        offset = 0
        while offset < len(blob):
            chunk = blob[offset:offset + WRITE_CHUNK]
            self._request(CMD_WRITE_IMAGE, key_id, offset, chunk)
            offset += len(chunk)
            if progress:
                progress(offset, len(blob))
        return total


def render_for_key(path_or_image, zoom=1.0):
    """Bild auf 120x120 bringen -- aufrecht, wie es auf der Taste erscheint.

    zoom > 1 schneidet enger auf die Bildmitte zu, damit kleine Motive die
    Taste ausfuellen. Rueckgabe ist ein PIL-Bild fuer die Vorschau.
    """
    from PIL import Image

    if hasattr(path_or_image, "convert") and hasattr(path_or_image, "copy"):
        image = path_or_image.copy()
    else:
        image = Image.open(path_or_image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (25, 26, 29, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")
    side = int(min(image.size) / max(1.0, zoom))
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    return image.resize(IMAGE_SIZE, Image.LANCZOS)


def encode_for_key(path_or_image, zoom=1.0):
    """Wie render_for_key, zusaetzlich fuer das Geraet gedreht und als JPEG.

    Die Qualitaet wird so weit gesenkt, bis das Ergebnis sicher unter der
    Groessengrenze liegt -- angestrebt wird die Haelfte von MAX_IMAGE_BYTES.
    """
    import io

    image = render_for_key(path_or_image, zoom)
    # Gegenstueck zu upright(): im Uhrzeigersinn hineindrehen.
    image = image.rotate(-90, expand=True)

    for quality in (95, 90, 85, 80, 70, 60, 50):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        data = buffer.getvalue()
        if HEADER_SIZE + len(data) <= MAX_IMAGE_BYTES // 2:
            return data, quality
    return data, quality


def upright(jpeg_bytes):
    """Die Displays sind gedreht verbaut -- 90 Grad gegen den Uhrzeigersinn."""
    try:
        from PIL import Image
        import io
        image = Image.open(io.BytesIO(jpeg_bytes)).rotate(90, expand=True)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=95)
        return out.getvalue()
    except ImportError:
        return jpeg_bytes


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--key", type=int, metavar="1-8", help="eine Taste lesen")
    group.add_argument("--all", action="store_true", help="alle acht lesen")
    group.add_argument("--verify", type=int, metavar="1-8",
                       help="Taste lesen und mit dem Mitschnitt vergleichen")
    group.add_argument("--write", nargs=2, metavar=("1-8", "BILD"),
                       help="Bild auf eine Taste schreiben (sichert vorher das "
                            "bisherige und prüft danach durch Zurücklesen)")
    group.add_argument("--restore", nargs=2, metavar=("1-8", "JPEG"),
                       help="ein zuvor gesichertes JPEG unverändert "
                            "zurückschreiben")
    ap.add_argument("-o", "--output", help="Zieldatei fuer --key")
    ap.add_argument("-d", "--directory",
                    default=bqpaths.IMAGE_DIR,
                    help="Zielverzeichnis fuer --all")
    ap.add_argument("--raw", action="store_true",
                    help="nicht aufrichten, so speichern wie gespeichert")
    ap.add_argument("--zoom", type=float, default=1.0, metavar="FAKTOR",
                    help="beim Schreiben enger auf die Bildmitte zuschneiden "
                         "(z. B. 2.5 für kleine Motive)")
    args = ap.parse_args()

    kb = Keyboard()
    print("Gerät: %s (lesend und schreibend geöffnet)" % kb.path)
    print("Erlaubte Kommandos: %s"
          % ", ".join("%02x %02x" % c for c in sorted(ALLOWED_COMMANDS)))

    def show(done, total):
        print("\r  %5d / %5d Byte" % (done, total), end="", flush=True)

    try:
        if args.verify is not None:
            slot = args.verify
            key_id = bqkeyd.FIRST_KEY + slot - 1
            jpeg, w, h, fmt = kb.read_image(key_id, show)
            print()
            reference = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "extracted", "key%d.jpg" % slot)
            print("Gelesen: %d Byte, %dx%d, Format %d" % (len(jpeg), w, h, fmt))
            if not os.path.exists(reference):
                print("Kein Vergleichsbild unter %s" % reference)
                return
            expected = open(reference, "rb").read()
            if jpeg == expected:
                print("ÜBEREINSTIMMUNG: byteidentisch mit %s" % reference)
                print("Die Implementierung entspricht exakt der offiziellen "
                      "Software.")
            else:
                print("ABWEICHUNG: %d Byte gelesen, %d Byte erwartet."
                      % (len(jpeg), len(expected)))
                print("Kann daran liegen, dass das Bild seit dem Mitschnitt "
                      "geändert wurde.")
            return

        if args.write or args.restore:
            raw_mode = bool(args.restore)
            slot_text, source = args.restore if raw_mode else args.write
            slot = int(slot_text)
            if not 1 <= slot <= 8:
                raise SystemExit("Taste muss zwischen 1 und 8 liegen.")
            key_id = bqkeyd.FIRST_KEY + slot - 1

            # 1) Sicherung des bisherigen Bildes -- immer, nicht abschaltbar.
            backup_dir = bqpaths.BACKUP_DIR
            os.makedirs(backup_dir, exist_ok=True)
            print("Sichere bisheriges Bild von Taste %d…" % slot)
            old, _w, _h, _f = kb.read_image(key_id, show)
            print()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = os.path.join(backup_dir, "key%d-%s.jpg" % (slot, stamp))
            with open(backup, "wb") as fh:
                fh.write(old)
            print("  gesichert: %s (%d Byte)" % (backup, len(old)))

            # 2) Bild vorbereiten.
            if raw_mode:
                jpeg = open(source, "rb").read()
                print("Schreibe %s unverändert (%d Byte)." % (source, len(jpeg)))
            else:
                jpeg, quality = encode_for_key(source, args.zoom)
                print("Aufbereitet: %dx%d, Zoom %.1fx, JPEG-Qualität %d, %d Byte"
                      % (IMAGE_SIZE[0], IMAGE_SIZE[1], args.zoom, quality,
                         len(jpeg)))

            # 3) Schreiben.
            print("Schreibe auf Taste %d…" % slot)
            kb.write_image(key_id, jpeg, show)
            print()

            # 4) Zurücklesen und vergleichen.
            time.sleep(0.3)
            check, _w, _h, _f = kb.read_image(key_id, show)
            print()
            if check == jpeg:
                print("ERFOLG: Zurückgelesenes Bild ist byteidentisch.")
            else:
                print("ABWEICHUNG: %d Byte geschrieben, %d Byte zurückgelesen."
                      % (len(jpeg), len(check)))
                print("Bisheriges Bild wiederherstellen mit:")
                print("  %s --restore %d %s" % (sys.argv[0], slot, backup))
            return

        slots = range(1, 9) if args.all else [args.key]
        if args.all:
            os.makedirs(args.directory, exist_ok=True)

        for slot in slots:
            key_id = bqkeyd.FIRST_KEY + slot - 1
            print("Taste %d (0x%02x):" % (slot, key_id))
            jpeg, w, h, fmt = kb.read_image(key_id, show)
            print()
            if not args.raw:
                jpeg = upright(jpeg)
            target = (args.output if args.key and args.output
                      else os.path.join(args.directory, "key%d.jpg" % slot))
            with open(target, "wb") as fh:
                fh.write(jpeg)
            print("  %dx%d, Format %d -> %s" % (w, h, fmt, target))
    finally:
        kb.close()


if __name__ == "__main__":
    main()
