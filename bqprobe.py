#!/usr/bin/env python3
"""
bqprobe -- fragt die Fähigkeiten der be quiet! Dark Mount ab.

Zweck: herausfinden, was hinter Kommandogruppe 0x12 steckt. Die Tastatur
listet diese Gruppe beim Verbinden selbst als unterstützt auf (Kommando
0x01 0x04), die offizielle Web-App spricht sie aber nie an. Vermutung:
die MEDIA-Ansicht des Media-Docks.

SICHERHEITSREGELN DIESES WERKZEUGS

  * Es sendet ausschliesslich **Abfragen mit leerer Nutzlast**. Es gibt in
    diesem Programm keine Funktion, die Daten auf das Geraet schreibt.
  * Gefragt wird nur nach Gruppen, die die Tastatur in ihrer eigenen
    Faehigkeitsliste (0x01 0x04) gemeldet hat -- die Whitelist stammt also
    vom Geraet selbst, nicht von mir.
  * Als Unterkommando ist nur 0x01 erlaubt, das durchgaengige Muster fuer
    "Information abfragen" (0x03 0x01, 0x07 0x01, 0x10 0x01, 0x11 0x01,
    0x20 0x01, 0x21 0x01 folgen im Mitschnitt alle diesem Schema).
  * Vor jeder unbekannten Abfrage werden bekannte Kommandos abgefragt und
    mit dem Mitschnitt verglichen. Weichen die ab, bricht das Programm ab,
    statt weiterzumachen.

Firmware ist ueber diesen Weg nicht erreichbar: der Bootloader meldet sich
laut Geraetemanifest als eigenes USB-Geraet (PID 0x0009 statt 0x0001).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqlink  # noqa: E402

CMD_CAPABILITIES = (0x01, 0x04)   # liefert die Liste unterstuetzter Gruppen
INFO_SUBCOMMAND = 0x01            # einziges erlaubtes Unterkommando
TIMEOUT = 2.0

TRACE = os.path.expanduser("~/Downloads/bq-trace.json")

# Gruppen, deren Antwort wir aus dem Mitschnitt kennen -- dienen als Kontrolle.
KNOWN_GROUPS = (0x03, 0x07, 0x10, 0x11, 0x20, 0x21)


class Prober:
    """Sendet ausschliesslich Abfragen ohne Nutzlast."""

    def __init__(self):
        self.link = bqlink.QLink({CMD_CAPABILITIES}, timeout=TIMEOUT)
        self.path = self.link.path
        self.allowed_groups = None   # wird aus der Geraeteantwort gefuellt

    def close(self):
        self.link.close()

    def query(self, command):
        """Baut eine Abfrage ohne Nutzlast und wartet auf die Antwort."""
        group, sub = command
        if command != CMD_CAPABILITIES:
            if sub != INFO_SUBCOMMAND:
                raise ValueError(
                    "Nur Unterkommando 0x01 erlaubt, nicht 0x%02x." % sub)
            if self.allowed_groups is None:
                raise ValueError(
                    "Fähigkeitsliste noch nicht gelesen -- keine Abfrage "
                    "ohne Freigabe durch das Gerät.")
            if group not in self.allowed_groups:
                raise ValueError(
                    "Gruppe 0x%02x steht nicht in der Fähigkeitsliste des "
                    "Geräts -- wird nicht gesendet." % group)

        try:
            return self.link.request(command, return_frame=True)
        except TimeoutError:
            return None, None

    def read_capabilities(self):
        payload, raw = self.query(CMD_CAPABILITIES)
        if payload is None:
            raise SystemExit("Keine Antwort auf die Fähigkeitsabfrage.")
        count = payload[0]
        groups = []
        for i in range(count):
            base = 1 + i * 2
            if base + 1 < len(payload):
                groups.append((payload[base], payload[base + 1]))
        self.allowed_groups = {g for g, _s in groups}
        for group in self.allowed_groups:
            self.link.allow((group, INFO_SUBCOMMAND))
        return groups, raw


def trace_reference():
    """Antworten aus dem Mitschnitt, nach (gruppe, unterkommando)."""
    if not os.path.exists(TRACE):
        return {}
    reference = {}
    for event in json.load(open(TRACE)):
        data = bytes(int(x, 16) for x in event["data"].split())
        if event["dir"] != "IN":
            continue
        key = (data[5], data[6])
        if key[1] == INFO_SUBCOMMAND and key[0] in KNOWN_GROUPS:
            reference.setdefault(key, data[7:data[0] + 1])
    return reference


def hexs(data):
    return " ".join("%02x" % b for b in data) if data else "(leer)"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", type=lambda x: int(x, 0), metavar="0x12",
                    help="nur diese Gruppe abfragen")
    ap.add_argument("--skip-check", action="store_true",
                    help="Kontrollabfragen überspringen (nicht empfohlen)")
    args = ap.parse_args()

    prober = Prober()
    print("Gerät: %s" % prober.path)
    print("Sendet ausschließlich Abfragen ohne Nutzlast.\n")

    try:
        print("── Fähigkeitsliste (0x01 0x04) " + "─" * 30)
        groups, raw = prober.read_capabilities()
        print("  roh: %s" % hexs(raw[:24]))
        print("  Gerät meldet %d Gruppen: %s\n"
              % (len(groups), ", ".join("%02x %02x" % g for g in groups)))

        if not args.skip_check:
            print("── Kontrolle gegen den Mitschnitt " + "─" * 27)
            reference = trace_reference()
            if not reference:
                print("  Kein Mitschnitt unter %s — Kontrolle entfällt.\n"
                      % TRACE)
            else:
                mismatches = 0
                for group in KNOWN_GROUPS:
                    command = (group, INFO_SUBCOMMAND)
                    if command not in reference:
                        continue
                    if group not in prober.allowed_groups:
                        print("  %02x 01  nicht in der Fähigkeitsliste — "
                              "übersprungen" % group)
                        continue
                    payload, _raw = prober.query(command)
                    got = payload
                    want = reference[command]
                    if got == want:
                        print("  %02x 01  stimmt überein   %s"
                              % (group, hexs(want)))
                    else:
                        mismatches += 1
                        print("  %02x 01  ABWEICHUNG" % group)
                        print("           erwartet: %s" % hexs(want))
                        print("           erhalten: %s" % hexs(got))
                if mismatches:
                    raise SystemExit(
                        "\n%d Abweichung(en) — Abfragemuster stimmt nicht. "
                        "Abbruch, es wird nichts weiter gesendet." % mismatches)
                print("  Alle Kontrollabfragen stimmen. Muster bestätigt.\n")

        targets = ([args.group] if args.group is not None
                   else sorted(g for g, _s in groups
                               if g not in KNOWN_GROUPS and g != 0x01))
        print("── Unbekannte Gruppen " + "─" * 39)
        for group in targets:
            command = (group, INFO_SUBCOMMAND)
            try:
                payload, raw = prober.query(command)
            except ValueError as exc:
                print("  %02x 01  nicht gesendet: %s" % (group, exc))
                continue
            if payload is None:
                print("  %02x 01  keine Antwort (Zeitüberschreitung)" % group)
            else:
                print("  %02x 01  Antwort: %s" % (group, hexs(payload)))
                print("           roh:     %s" % hexs(raw[:24]))
    finally:
        prober.close()


if __name__ == "__main__":
    main()
