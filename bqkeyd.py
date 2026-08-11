#!/usr/bin/env python3
"""
bqkeyd -- Display-Key-Daemon fuer die be quiet! Dark Mount unter Linux.

Lauscht passiv auf dem Vendor-HID-Interface der Tastatur und reagiert auf die
8 Display Keys ueber dem Numblock -- wahlweise durch

  * Ausfuehren eines Kommandos  (config.toml -> command)
  * Einspielen einer virtuellen Taste F13..F24 ueber uinput, sodass KDE die
    Taste im Kurzbefehl-Dialog erkennt  (config.toml -> [uinput])

Hintergrund: die Display Keys senden von sich aus KEINE Tastencodes, sondern
melden sich nur ueber den Vendor-Kanal. Deshalb sieht KDE sie nicht. Der
uinput-Modus erzeugt daraus eine ganz normale Tastatureingabe.

SICHERHEIT
    Der Vendor-Knoten wird ausschliesslich mit O_RDONLY geoeffnet. Der Prozess
    kann dem Geraet strukturell nichts senden -- kein Konfigurations-Write,
    kein Firmware-Kommando. Die Tastatur kann durch diesen Daemon nicht
    veraendert und nicht gebrickt werden. uinput ist ein reiner
    Kernel-Eingabemechanismus und beruehrt die Tastatur nicht.

    Schreibende Bild-, Dock- und Beleuchtungsfunktionen liegen getrennt in der
    GUI und besitzen eigene Positivlisten. Fuer Firmware-Updates bleibt die
    offizielle Web-App unter https://iocenter.bequiet.com/ zustaendig.

VERWENDUNG
    ./bqkeyd.py --list-keys      Tasten-IDs live anzeigen (zum Konfigurieren)
    ./bqkeyd.py                  Daemon im Vordergrund starten
    ./bqkeyd.py -c pfad.toml     alternative Konfiguration
"""

import argparse
import fcntl
import glob
import os
import re
import select
import shlex
import shutil
import struct
import subprocess
import sys
import time
import tomllib

import bqpaths

VENDOR_ID = "373F"

# Vendor-Report-Layout (64 Byte, ohne Report-ID), empirisch bestimmt:
#
#   byte 0     Payload-Laenge (0x06 Heartbeat, 0x0a/0x0b Tasten-Event)
#   byte 2     Session-/Verbindungszaehler
#   byte 5,6   Event-Kennung -- 0x11 0x02 = Display-Key, 0x01 0x03 = Heartbeat
#   byte 7     Tasten-ID (0x6d..0x74 fuer die 8 Display Keys)
#   byte 9     0x01 = gedrueckt
#   byte 10,11 hinterlegte Aktion aus der Web-App (0x00 0x00 = keine)
#   letzte 2   Pruefsumme
EVT_KEY = (0x11, 0x02)

FIRST_KEY = 0x6D
DISPLAY_KEYS = range(FIRST_KEY, FIRST_KEY + 8)  # 0x6d..0x74

DEFAULT_CONFIG = bqpaths.CONFIG_PATH

# Vendor-Defined Usage Page 0xFF00 am Deskriptor-Anfang: 06 00 ff 09 01 a1 01
VENDOR_DESCRIPTOR_PREFIX = bytes.fromhex("0600ff0901a101")

# ---------------------------------------------------------------- uinput ---

# linux/input-event-codes.h
EV_SYN, EV_KEY = 0x00, 0x01
SYN_REPORT = 0
KEY_F13 = 183  # F13..F24 == 183..194, lueckenlos

# linux/uinput.h -- _IOW('U', n, size) bzw. _IO('U', n)
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

KEY_NAMES = {"F%d" % (13 + i): KEY_F13 + i for i in range(12)}


class VirtualKeyboard:
    """Virtuelles Eingabegeraet fuer F13..F24 via /dev/uinput."""

    def __init__(self, keycodes, name="be quiet! Dark Mount Display Keys"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in sorted(set(keycodes)):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)

        # struct uinput_setup: input_id{bustype,vendor,product,version}
        #                      + char name[80] + __u32 ff_effects_max
        BUS_VIRTUAL = 0x06
        setup = struct.pack("<4H80sI", BUS_VIRTUAL, 0x373F, 0x0001, 1,
                            name.encode()[:79], 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        # udev braucht einen Moment, bis das Geraet nutzbar ist.
        time.sleep(0.2)

    def _emit(self, etype, code, value):
        # struct input_event: timeval{sec,usec} + type + code + value
        os.write(self.fd, struct.pack("<qqHHi", 0, 0, etype, code, value))

    def tap(self, keycode):
        self._emit(EV_KEY, keycode, 1)
        self._emit(EV_SYN, SYN_REPORT, 0)
        self._emit(EV_KEY, keycode, 0)
        self._emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


# ------------------------------------------------------------------ util ---

def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def find_vendor_node():
    """Sucht den Vendor-HID-Knoten anhand des Report-Deskriptors.

    Bewusst nicht auf /dev/hidraw16 festgenagelt -- die Nummern vergibt der
    Kernel in Anschlussreihenfolge und sie aendern sich beim Umstecken.
    """
    found = []
    for syspath in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        name = os.path.basename(syspath)
        try:
            with open(os.path.join(syspath, "device/uevent")) as fh:
                if VENDOR_ID not in fh.read().upper():
                    continue
            with open(os.path.join(syspath, "device/report_descriptor"), "rb") as fh:
                descriptor = fh.read()
        except OSError:
            continue
        if descriptor.startswith(VENDOR_DESCRIPTOR_PREFIX):
            found.append(name)
    return found


def open_vendor_node(retry_forever=False):
    warned = False
    while True:
        for node in find_vendor_node():
            try:
                fd = os.open("/dev/" + node, os.O_RDONLY | os.O_NONBLOCK)
                log("Vendor-Interface offen: /dev/%s (nur lesend)" % node)
                return fd, node
            except PermissionError:
                log("Keine Rechte auf /dev/%s -- udev-Regel fuer VID %s noetig."
                    % (node, VENDOR_ID.lower()))
            except OSError as exc:
                log("Kann /dev/%s nicht oeffnen: %s" % (node, exc))
        if not retry_forever:
            return None, None
        if not warned:
            log("Warte auf be quiet! Tastatur...")
            warned = True
        time.sleep(3)


def parse_event(data):
    """Gibt (key_id, action_bytes) zurueck oder None."""
    if len(data) < 12 or (data[5], data[6]) != EVT_KEY or data[9] != 0x01:
        return None
    return data[7], (data[10], data[11])


def parse_key_id(name):
    """Akzeptiert 'key1'..'key8', '0x6d' und '109'."""
    match = re.fullmatch(r"key([1-8])", name)
    if match:
        return FIRST_KEY + int(match.group(1)) - 1
    try:
        return int(name, 0)
    except ValueError:
        return None


def load_config(path):
    """Liefert (commands, vkeys, labels, uinput_enabled)."""
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        log("Keine Konfiguration unter %s -- Tasten werden nur protokolliert." % path)
        return {}, {}, {}, False
    except tomllib.TOMLDecodeError as exc:
        sys.exit("Fehler in %s: %s" % (path, exc))

    uinput_enabled = bool(raw.get("uinput", {}).get("enabled", False))

    commands, vkeys, labels = {}, {}, {}
    for section, entry in raw.get("keys", {}).items():
        key_id = parse_key_id(section)
        if key_id is None:
            log("Ueberspringe unbekannten Abschnitt [keys.%s]" % section)
            continue
        if isinstance(entry, str):
            entry = {"command": entry}
        labels[key_id] = entry.get("label", section)

        if entry.get("command"):
            commands[key_id] = entry["command"]

        key_name = entry.get("key")
        if key_name is not None:
            if key_name is False:
                continue
            code = KEY_NAMES.get(str(key_name).upper())
            if code is None:
                log("Unbekannter Tastenname %r in [keys.%s] -- erlaubt: F13..F24"
                    % (key_name, section))
            else:
                vkeys[key_id] = code

    if uinput_enabled:
        # Automatisches Mapping key1..key8 -> F13..F20 fuer alles,
        # was nicht ausdruecklich belegt oder mit key=false abgeschaltet wurde.
        for slot, key_id in enumerate(DISPLAY_KEYS):
            entry = raw.get("keys", {}).get("key%d" % (slot + 1), {})
            if isinstance(entry, dict) and entry.get("key") is False:
                continue
            vkeys.setdefault(key_id, KEY_F13 + slot)

    return commands, vkeys, labels, uinput_enabled


def run_command(command, key_id, label):
    launcher = desktop_launch_args(command)
    try:
        if launcher is not None:
            result = subprocess.run(launcher, capture_output=True, text=True,
                                    timeout=5)
            if result.returncode:
                raise OSError(result.stderr.strip() or
                              "systemd-run meldet Status %d"
                              % result.returncode)
        elif isinstance(command, list):
            subprocess.Popen(command, start_new_session=True)
        else:
            subprocess.Popen(command, shell=True, start_new_session=True,
                             executable="/bin/sh")
    except Exception as exc:
        log("Taste %-12s (0x%02x): Start fehlgeschlagen: %s" % (label, key_id, exc))
    else:
        shown = command if isinstance(command, str) else shlex.join(command)
        log("Taste %-12s (0x%02x) -> %s" % (label, key_id, shown))


def desktop_launch_args(command):
    """Desktop-Kommando als eigene systemd-User-Unit starten.

    Der HID-Daemon selbst bleibt stark eingeschränkt. Die eigentliche App
    wird dagegen vom User-Manager in einer frischen Unit erzeugt und erbt
    dadurch weder ProtectHome noch Netzwerk-/JIT-Sperren des Daemons.
    """
    runner = shutil.which("systemd-run")
    if runner is None:
        return None
    base = [runner, "--user", "--collect", "--quiet",
            "--service-type=exec", "--"]
    if isinstance(command, list):
        return base + list(command)
    return base + ["/bin/sh", "-lc", command]


# ------------------------------------------------------------------ main ---

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                    help="Konfigurationsdatei (Vorgabe: %(default)s)")
    ap.add_argument("--list-keys", action="store_true",
                    help="nur Tasten-IDs anzeigen, nichts ausfuehren")
    args = ap.parse_args()

    if os.path.abspath(args.config) == os.path.abspath(DEFAULT_CONFIG):
        bqpaths.ensure_user_config()

    if args.list_keys:
        commands, vkeys, labels, uinput_enabled = {}, {}, {}, False
        log("Nur-Anzeige-Modus. Display Keys druecken, Ctrl-C beendet.")
    else:
        commands, vkeys, labels, uinput_enabled = load_config(args.config)

    vkbd = None
    if vkeys:
        try:
            vkbd = VirtualKeyboard(vkeys.values())
        except PermissionError:
            log("Kein Schreibzugriff auf /dev/uinput -- virtuelle Tasten deaktiviert.")
        except OSError as exc:
            log("uinput nicht verfuegbar (%s) -- virtuelle Tasten deaktiviert." % exc)
        else:
            names = {code: name for name, code in KEY_NAMES.items()}
            mapping = ", ".join(
                "Taste %d=%s" % (key_id - FIRST_KEY + 1, names[code])
                for key_id, code in sorted(vkeys.items()))
            log("Virtuelle Tastatur aktiv: %s" % mapping)
            log("Diese Tasten sind jetzt in den KDE-Kurzbefehlen aufzeichenbar.")

    if commands:
        log("%d Taste(n) mit Kommando belegt." % len(commands))

    fd, node = open_vendor_node(retry_forever=True)

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 5.0)
            if not ready:
                continue
            try:
                data = os.read(fd, 512)
            except BlockingIOError:
                continue
            except OSError as exc:
                # Tastatur abgezogen -- auf Wiederkehr warten.
                log("Verbindung zu /dev/%s verloren (%s)." % (node, exc))
                os.close(fd)
                fd, node = open_vendor_node(retry_forever=True)
                continue
            if not data:
                continue

            event = parse_event(data)
            if event is None:
                continue
            key_id, action = event

            if key_id not in DISPLAY_KEYS:
                log("Unbekannte Tasten-ID 0x%02x (nicht in 0x6d..0x74)" % key_id)
                continue

            slot = key_id - FIRST_KEY + 1
            label = labels.get(key_id, "key%d" % slot)

            if args.list_keys:
                extra = ""
                if action != (0x00, 0x00):
                    extra = "   (Web-App-Aktion hinterlegt: %02x %02x)" % action
                log("Display Key %d  ->  id=0x%02x   [keys.key%d]%s"
                    % (slot, key_id, slot, extra))
                continue

            handled = False
            if vkbd is not None and key_id in vkeys:
                code = vkeys[key_id]
                vkbd.tap(code)
                names = {c: n for n, c in KEY_NAMES.items()}
                log("Taste %-12s (0x%02x) -> %s" % (label, key_id, names[code]))
                handled = True
            if key_id in commands:
                run_command(commands[key_id], key_id, label)
                handled = True
            if not handled:
                log("Taste %s (0x%02x) gedrueckt -- nicht belegt." % (label, key_id))
    except KeyboardInterrupt:
        log("Beendet.")
    finally:
        if fd is not None:
            os.close(fd)
        if vkbd is not None:
            vkbd.close()


if __name__ == "__main__":
    main()
