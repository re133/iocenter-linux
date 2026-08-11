#!/usr/bin/env python3
"""
bqlight -- Onboard-Beleuchtung der be quiet! Dark Mount setzen.

Die Effekte laufen im Geraet selbst weiter, auch ohne angeschlossenen
Rechner. Steuerung über ein Beleuchtungskommando, das aus einem Mitschnitt
der offiziellen Web-App vollständig belegt ist. Davor und danach wird eine
kurzlebige QLink-Sitzung geöffnet bzw. geschlossen:

    01 01   Sitzung öffnen
    01 02   Sitzung schließen
    10 06   Effekt setzen

    byte  7   Zone (im Mitschnitt immer 0)
    byte  8   Effekt 0..5
    byte  9   Richtung
    byte 10   Helligkeit 0..100
    byte 11   Tempo 0..100
    byte 12   Farbmodus: 0 = eine Farbe, 1 = zwei Farben, 2 = Palette
    ab 13     Modus 2: Anzahl Farben, danach RGB-Tripel
              sonst: direkt RGB-Tripel

SICHERHEIT
    Whitelist mit den beiden Sitzungsbefehlen und genau einem Nutzkommando.
    Alle Werte werden auf ihren zulässigen Bereich geprüft. Firmware ist über
    diesen Weg nicht erreichbar (Bootloader = eigenes USB-Geraet, PID 0x0009).

    Fuer Beleuchtung gibt es alternativ den offenen HID-LampArray-Standard
    auf dem vierten HID-Interface (Usage Page 0x59). Der eignet sich fuer
    Einzeltastensteuerung vom Rechner aus, kann aber keine Effekte im
    Geraet speichern -- deshalb hier der Vendor-Weg.

VERWENDUNG
    ./bqlight.py --list
    ./bqlight.py --effect static --color '#ff2800'
    ./bqlight.py --effect colorwave --brightness 80 --speed 40
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqlink  # noqa: E402

CMD_OPEN_SESSION = bqlink.CMD_OPEN_SESSION
CMD_CLOSE_SESSION = bqlink.CMD_CLOSE_SESSION
CMD_SET_EFFECT = (0x10, 0x06)
ALLOWED_COMMANDS = {CMD_SET_EFFECT}

# Reihenfolge wie in availableGeneralEffects des Geraetemanifests.
EFFECTS = {
    "static": 0,
    "colorwave": 1,
    "tornado": 2,
    "breathing": 3,
    "reactive": 4,
    "matrix": 5,
}
EFFECT_NAMES = {
    0: "Statisch",
    1: "Farbwelle",
    2: "Tornado",
    3: "Atmen",
    4: "Reaktiv",
    5: "Matrix",
}

COLOR_SINGLE = 0
COLOR_DUAL = 1
COLOR_PALETTE = 2
MAX_PALETTE = 8      # so viele Farben hat die Web-App höchstens gesendet

TIMEOUT = 2.0
DEFAULT_COLOR = (0xFF, 0x28, 0x00)   # mainColor aus dem Geraetemanifest


def parse_color(text):
    text = text.lstrip("#")
    if len(text) != 6:
        raise ValueError("Farbe muss #rrggbb sein, nicht %r" % text)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def effect_payload(effect, colors=None, brightness=100, speed=50,
                   direction=0):
    """Baut und validiert die Nutzlast fuer ``10 06``.

    Die getrennte Funktion haelt den belegten IOCenter-Report testbar, ohne
    dass dafuer ein echtes HID-Geraet geoeffnet werden muss.
    """
    if CMD_SET_EFFECT not in ALLOWED_COMMANDS:      # bewusst defensiv
        raise ValueError("Kommando steht nicht auf der Whitelist.")
    if effect not in EFFECT_NAMES:
        raise ValueError("Unbekannter Effekt %r, erlaubt sind 0..5"
                         % effect)
    for name, value in (("Helligkeit", brightness), ("Tempo", speed)):
        if not 0 <= value <= 100:
            raise ValueError("%s muss zwischen 0 und 100 liegen, nicht %r"
                             % (name, value))
    if not 0 <= direction <= 7:
        raise ValueError("Richtung muss zwischen 0 und 7 liegen.")

    colors = [tuple(c) for c in (colors or [DEFAULT_COLOR])]
    if len(colors) > MAX_PALETTE:
        raise ValueError("Höchstens %d Farben." % MAX_PALETTE)
    for entry in colors:
        if len(entry) != 3 or not all(0 <= v <= 255 for v in entry):
            raise ValueError("Farbe muss (r, g, b) mit 0..255 sein.")

    if len(colors) == 1:
        body = bytes((COLOR_SINGLE,)) + bytes(colors[0])
    elif len(colors) == 2:
        body = bytes((COLOR_DUAL,)) + bytes(colors[0]) + bytes(colors[1])
    else:
        body = bytes((COLOR_PALETTE, len(colors)))
        for entry in colors:
            body += bytes(entry)

    payload = bytes((0x00, effect, direction, brightness, speed)) + body
    if len(payload) > 54:
        raise ValueError("Nutzlast zu groß: %d Byte" % len(payload))
    return payload


parse_open_session = bqlink.parse_open_session


class Lighting:
    def __init__(self):
        self.link = bqlink.QLink(ALLOWED_COMMANDS)
        self.path = self.link.path

    def close(self):
        self.link.close()

    def set_effect(self, effect, colors=None, brightness=100, speed=50,
                   direction=0):
        """Setzt einen Onboard-Effekt.

        colors ist eine Liste von (r, g, b). Der Farbmodus ergibt sich aus
        ihrer Länge, genau wie es die Web-App im Mitschnitt handhabt:

            eine Farbe   Modus 0, drei Bytes
            zwei Farben  Modus 1, sechs Bytes ohne Anzahlangabe
            mehr         Modus 2, Anzahl gefolgt von den Tripeln
        """
        payload = effect_payload(effect, colors, brightness, speed, direction)

        self.link.request(CMD_SET_EFFECT, payload)
        return True


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="Effekte auflisten")
    ap.add_argument("--effect", choices=sorted(EFFECTS))
    ap.add_argument("--color", action="append", metavar="#RRGGBB",
                    help="mehrfach angeben für Zwei-Farben- oder Palettenmodus")
    ap.add_argument("--brightness", type=int, default=100, metavar="0-100")
    ap.add_argument("--speed", type=int, default=50, metavar="0-100")
    ap.add_argument("--direction", type=int, default=0, metavar="0-7")
    args = ap.parse_args()
    if not args.color:
        args.color = ["#ff2800"]

    if args.list or not args.effect:
        print("Verfügbare Effekte:")
        for name, number in sorted(EFFECTS.items(), key=lambda x: x[1]):
            print("  %-10s %d  %s" % (name, number, EFFECT_NAMES[number]))
        if not args.effect:
            return

    lighting = Lighting()
    try:
        lighting.set_effect(EFFECTS[args.effect],
                            [parse_color(c) for c in args.color],
                            args.brightness, args.speed, args.direction)
        print("Effekt %s gesetzt (%d Farbe(n): %s, Helligkeit %d, Tempo %d)."
              % (EFFECT_NAMES[EFFECTS[args.effect]], len(args.color),
                 ", ".join(args.color), args.brightness, args.speed))
    finally:
        lighting.close()


if __name__ == "__main__":
    main()
