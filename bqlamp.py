#!/usr/bin/env python3
"""
bqlamp -- Einzeltastensteuerung über den HID-LampArray-Standard.

Die Dark Mount implementiert auf ihrem vierten HID-Interface die offene
HID-Spezifikation "Lighting And Illumination" (Usage Page 0x59, HID Usage
Tables 1.4). Hier wird also nichts nachgebaut und nichts geraten -- das ist
derselbe Standard, den auch Windows über die DirectX-LampArray-Schnittstelle
anspricht.

Der Unterschied zu bqlight.py:

    bqlight   sechs Effekte, die im Gerät gespeichert sind und ohne Rechner
              weiterlaufen -- über den herstellereigenen Kanal
    bqlamp    jede LED einzeln, in Echtzeit vom Rechner gerechnet; hört auf,
              sobald das Programm endet -- über den offenen Standard

Das Gerät verrät dabei selbst, wie viele Lampen es hat und wo jede sitzt
(Position in Mikrometern), sodass sich Farbverläufe geometrisch berechnen
lassen, ohne eine Tastaturbelegung fest zu verdrahten.

SICHERHEIT
    Anderer HID-Knoten als der Vendor-Kanal, ausschließlich die im
    Report-Deskriptor des Geräts angekündigten Feature-Reports 1 bis 6.
    Firmware ist hier nicht erreichbar.

VERWENDUNG
    ./bqlamp.py --info                    Lampen zählen und vermessen
    ./bqlamp.py --solid '#ff2800'         alles in einer Farbe
    ./bqlamp.py --gradient '#ff2800' '#0028ff'
    ./bqlamp.py --release                 Steuerung ans Gerät zurückgeben
"""

import argparse
import fcntl
import glob
import os
import struct
import sys
import time

# LampArray-Deskriptor am Anfang: 05 59 09 01 a1 01
LAMPARRAY_PREFIX = bytes.fromhex("05590901a101")

# Report-Kennungen aus dem Deskriptor des Geräts
REPORT_ATTRIBUTES = 1        # LampArrayAttributes          (lesen)
REPORT_LAMP_REQUEST = 2      # LampAttributesRequest        (schreiben)
REPORT_LAMP_RESPONSE = 3     # LampAttributesResponse       (lesen)
REPORT_MULTI_UPDATE = 4      # LampMultiUpdate              (schreiben)
REPORT_RANGE_UPDATE = 5      # LampRangeUpdate              (schreiben)
REPORT_CONTROL = 6           # LampArrayControl             (schreiben)

# Nutzlastgrößen ohne das führende Report-ID-Byte
SIZE_ATTRIBUTES = 22         # u16 LampCount + 5x u32
SIZE_LAMP_REQUEST = 2        # u16 LampId
SIZE_LAMP_RESPONSE = 27      # u16 LampId + 5x u32 + 5x u8
SIZE_MULTI_UPDATE = 42       # u8 Count + u8 Flags + 8x u16 Id + 24x u8 RGB
SIZE_RANGE_UPDATE = 8        # u8 Flags + 2x u16 + 3x u8 RGB
SIZE_CONTROL = 1             # u8 AutonomousMode

MULTI_UPDATE_MAX = 8         # so viele Lampen fasst ein LampMultiUpdate
FLAG_UPDATE_COMPLETE = 1     # ohne dieses Bit übernimmt das Gerät nichts

_IOC_WRITE, _IOC_READ = 1, 2
HIDIOCSFEATURE = lambda size: (3 << 30) | (size << 16) | (0x48 << 8) | 0x06
HIDIOCGFEATURE = lambda size: (3 << 30) | (size << 16) | (0x48 << 8) | 0x07


def find_lamparray_node():
    """Sucht den LampArray-Knoten über den Report-Deskriptor."""
    found = []
    for syspath in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(syspath, "device/report_descriptor"), "rb") as fh:
                descriptor = fh.read()
        except OSError:
            continue
        if descriptor.startswith(LAMPARRAY_PREFIX):
            found.append(os.path.basename(syspath))
    return found


class Lamp:
    """Eine einzelne Lampe mit ihrer Position im Gerät."""

    __slots__ = ("id", "x", "y", "z", "purposes")

    def __init__(self, lamp_id, x, y, z, purposes):
        self.id = lamp_id
        self.x, self.y, self.z = x, y, z
        self.purposes = purposes

    def __repr__(self):
        return "Lamp(%d, x=%d, y=%d)" % (self.id, self.x, self.y)


class LampArray:
    def __init__(self, path=None):
        if path is None:
            nodes = find_lamparray_node()
            if not nodes:
                raise SystemExit(
                    "Kein HID-LampArray gefunden. Ist die Tastatur "
                    "angeschlossen?")
            path = "/dev/" + nodes[0]
        self.path = path
        self.fd = os.open(path, os.O_RDWR)
        self._lamps = None
        self._owned = False

        buffer = self._get_feature(REPORT_ATTRIBUTES, SIZE_ATTRIBUTES)
        (self.lamp_count, self.width, self.height, self.depth,
         self.kind, self.min_update_interval) = struct.unpack("<HIIIII", buffer)

    def close(self):
        if self._owned:
            self.release()
        os.close(self.fd)

    # ---- Feature-Reports ----

    def _get_feature(self, report_id, size):
        buffer = bytearray(size + 1)
        buffer[0] = report_id
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(len(buffer)), buffer)
        return bytes(buffer[1:])

    def _set_feature(self, report_id, payload, size):
        if len(payload) > size:
            raise ValueError("Nutzlast zu groß: %d > %d" % (len(payload), size))
        buffer = bytearray(size + 1)
        buffer[0] = report_id
        buffer[1:1 + len(payload)] = payload
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buffer)), bytes(buffer))

    # ---- Lampen vermessen ----

    def lamps(self):
        """Fragt Position und Zweck jeder Lampe ab (einmalig, dann gemerkt)."""
        if self._lamps is not None:
            return self._lamps
        lamps = []
        for lamp_id in range(self.lamp_count):
            self._set_feature(REPORT_LAMP_REQUEST,
                              struct.pack("<H", lamp_id), SIZE_LAMP_REQUEST)
            data = self._get_feature(REPORT_LAMP_RESPONSE, SIZE_LAMP_RESPONSE)
            got_id, x, y, z, _latency, purposes = struct.unpack_from("<HIIIII",
                                                                    data, 0)
            lamps.append(Lamp(got_id, x, y, z, purposes))
        self._lamps = lamps
        return lamps

    # ---- Steuerung ----

    def take_control(self):
        """Host übernimmt -- das Gerät stellt seine eigenen Effekte ein."""
        self._set_feature(REPORT_CONTROL, bytes((0,)), SIZE_CONTROL)
        self._owned = True

    def release(self):
        """Steuerung zurückgeben; das Gerät zeigt wieder seinen Effekt."""
        self._set_feature(REPORT_CONTROL, bytes((1,)), SIZE_CONTROL)
        self._owned = False

    def set_range(self, first, last, colour, complete=True):
        """Einen zusammenhängenden Bereich einfärben."""
        red, green, blue = colour
        payload = struct.pack("<BHHBBB",
                              FLAG_UPDATE_COMPLETE if complete else 0,
                              first, last, red, green, blue)
        self._set_feature(REPORT_RANGE_UPDATE, payload, SIZE_RANGE_UPDATE)

    def set_lamps(self, items, complete=True):
        """Einzelne Lampen setzen: items = [(lamp_id, (r, g, b)), ...].

        Größere Listen werden automatisch auf mehrere Reports verteilt; nur
        der letzte trägt das Abschlussbit, damit das Gerät alles auf einmal
        übernimmt.
        """
        items = list(items)
        for index in range(0, len(items), MULTI_UPDATE_MAX):
            block = items[index:index + MULTI_UPDATE_MAX]
            is_last = index + MULTI_UPDATE_MAX >= len(items)
            flags = FLAG_UPDATE_COMPLETE if (complete and is_last) else 0

            payload = bytearray()
            payload += bytes((len(block), flags))
            ids = [lamp_id for lamp_id, _c in block]
            ids += [0] * (MULTI_UPDATE_MAX - len(ids))
            for lamp_id in ids:
                payload += struct.pack("<H", lamp_id)
            for _lamp_id, colour in block:
                payload += bytes(colour)
            payload += bytes(3 * (MULTI_UPDATE_MAX - len(block)))
            self._set_feature(REPORT_MULTI_UPDATE, bytes(payload),
                              SIZE_MULTI_UPDATE)

    # ---- Fertige Muster ----

    def solid(self, colour):
        self.set_range(0, self.lamp_count - 1, colour)

    def gradient(self, start_colour, end_colour, axis="x"):
        """Farbverlauf über die tatsächliche Geometrie des Geräts."""
        lamps = self.lamps()
        values = [getattr(lamp, axis) for lamp in lamps]
        low, high = min(values), max(values)
        span = (high - low) or 1

        items = []
        for lamp in lamps:
            t = (getattr(lamp, axis) - low) / span
            items.append((lamp.id, tuple(
                int(round(a + (b - a) * t))
                for a, b in zip(start_colour, end_colour))))
        self.set_lamps(items)


def parse_colour(text):
    text = text.lstrip("#")
    if len(text) != 6:
        raise ValueError("Farbe muss #rrggbb sein, nicht %r" % text)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--info", action="store_true",
                    help="Lampen zählen und vermessen")
    ap.add_argument("--solid", metavar="#RRGGBB")
    ap.add_argument("--gradient", nargs=2, metavar=("#VON", "#BIS"))
    ap.add_argument("--axis", choices=("x", "y"), default="x")
    ap.add_argument("--hold", type=float, default=0.0, metavar="SEK",
                    help="danach so lange halten, bevor zurückgegeben wird")
    ap.add_argument("--release", action="store_true",
                    help="Steuerung sofort ans Gerät zurückgeben")
    args = ap.parse_args()

    array = LampArray()
    print("LampArray: %s" % array.path)
    print("  %d Lampen, Fläche %.1f × %.1f mm, kürzestes Update %d µs"
          % (array.lamp_count, array.width / 1000, array.height / 1000,
             array.min_update_interval))

    try:
        if args.release:
            array.release()
            print("  Steuerung zurückgegeben.")
            return

        if args.info:
            lamps = array.lamps()
            print("  erste acht Positionen (µm):")
            for lamp in lamps[:8]:
                print("    #%-3d x=%-7d y=%-7d z=%d"
                      % (lamp.id, lamp.x, lamp.y, lamp.z))
            xs = [lamp.x for lamp in lamps]
            ys = [lamp.y for lamp in lamps]
            print("  x von %d bis %d, y von %d bis %d"
                  % (min(xs), max(xs), min(ys), max(ys)))
            return

        if args.solid or args.gradient:
            array.take_control()
            if args.solid:
                array.solid(parse_colour(args.solid))
                print("  alles auf %s" % args.solid)
            else:
                array.gradient(parse_colour(args.gradient[0]),
                               parse_colour(args.gradient[1]), args.axis)
                print("  Verlauf %s → %s entlang %s"
                      % (args.gradient[0], args.gradient[1], args.axis))
            if args.hold:
                time.sleep(args.hold)
                array.release()
                print("  Steuerung zurückgegeben.")
            else:
                array._owned = False   # Farbe stehen lassen
                print("  Zurückgeben mit: %s --release" % sys.argv[0])
    finally:
        array.close()


if __name__ == "__main__":
    main()
