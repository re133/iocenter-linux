#!/usr/bin/env python3
"""
Klärt, welches Feld die Leerlaufanzeige tatsächlich steuert.

Zwei Felder stehen im Verdacht, beide aus 0x21 0x03:

    Byte 10   bisher gedeutet als Anzeige         (1 = Uhr, 0 = Bild)
    Byte 11   bisher gedeutet als Uhrzeitformat   (2 = 24 h, 1 = 12 h)

Beobachtet wurde aber: Bild erscheint nur bei Byte 11 = 2, Uhr nur bei
Byte 11 = 1 -- unabhängig von Byte 10. Das würde bedeuten, dass Byte 11 die
Anzeige steuert und die Deutung als Uhrzeitformat falsch ist.

Der Test geht alle vier Kombinationen durch und hält dabei jeweils ein Feld
fest. Nach jedem Schritt 8 Sekunden Pause -- in dieser Zeit die Tastatur
NICHT anfassen, damit die Leerlaufanzeige erscheinen kann. Notieren, was zu
sehen war; die Auswertung steht am Ende.

Es wird ohne Umweg geschrieben, also genau ein Kommando je Schritt.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqdock  # noqa: E402

PAUSE = 8

STEPS = [
    (1, 1, "Byte 10 = 1, Byte 11 = 1"),
    (1, 2, "Byte 10 bleibt 1, Byte 11 wird 2"),
    (0, 2, "Byte 11 bleibt 2, Byte 10 wird 0"),
    (0, 1, "Byte 10 bleibt 0, Byte 11 wird 1"),
]


def main():
    dock = bqdock.Dock()
    original = dock.read_settings()
    print("Ausgangszustand: %s\n" % original)

    try:
        for index, (byte10, byte11, description) in enumerate(STEPS, start=1):
            settings = dock.read_settings()
            settings.display = byte10
            settings.clock_format = byte11
            settings.idle_seconds = 5
            dock.write_settings(settings)          # bewusst ohne Umweg

            print("Schritt %d von %d: %s" % (index, len(STEPS), description))
            print("         %d s nicht anfassen und aufs Dock schauen …" % PAUSE)
            time.sleep(PAUSE)
            print("         -> Was war zu sehen, Uhr oder Bild?\n")

        print("Auswertung:")
        print("  1 Uhr, 2 Bild, 3 Bild, 4 Uhr   -> Byte 11 steuert die Anzeige")
        print("  1 Uhr, 2 Uhr,  3 Bild, 4 Bild  -> Byte 10 steuert sie")
        print("  etwas anderes                  -> beide wirken zusammen")
    finally:
        dock.write_settings(original)
        print("\nZurückgesetzt auf: %s" % dock.read_settings())
        dock.close()


if __name__ == "__main__":
    main()
