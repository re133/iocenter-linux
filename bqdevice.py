#!/usr/bin/env python3
"""
bqdevice -- Geräteinformationen der be quiet! Dark Mount lesen.

Das Modul sendet ausschließlich die beiden lesenden, im vorhandenen
WebHID-Mitschnitt belegten Kommandos:

    03 01   Modell, Hardware-Revision und Firmware-Versionen lesen
    03 02   Seriennummer lesen

Andere Kommandos werden vor dem Schreiben auf das HID-Interface abgewiesen.
Firmware-/DFU-Funktionen sind nicht enthalten.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bqlink  # noqa: E402


CMD_INFO = (0x03, 0x01)
CMD_SERIAL = (0x03, 0x02)
ALLOWED_COMMANDS = {CMD_INFO, CMD_SERIAL}
TIMEOUT = 2.0


def _bcd(value):
    """BCD dekodieren; bei ungültigen Nibbles den Rohwert beibehalten."""
    high, low = value >> 4, value & 0x0F
    return high * 10 + low if high <= 9 and low <= 9 else value


def parse_info(payload):
    """Antwort von 03 01 in ein reines Dictionary übersetzen."""
    if len(payload) < 4:
        raise ValueError("Geräteinformation zu kurz: %d Byte" % len(payload))

    model, revision, count = struct.unpack_from("<HBB", payload, 0)
    expected = 4 + count * 4
    if len(payload) < expected:
        raise ValueError(
            "Geräteinformation unvollständig: %d statt mindestens %d Byte"
            % (len(payload), expected))

    versions = []
    for index in range(count):
        base = 4 + index * 4
        raw = payload[base:base + 4]
        # Der Mitschnitt enthält je MCU vier Bytes in der Reihenfolge
        # Build/Patch/Minor/Major. IOCenter zeigt Major.Minor.Patch.
        versions.append("%d.%d.%d" %
                        (_bcd(raw[3]), _bcd(raw[2]), _bcd(raw[1])))

    return {
        "model": model,
        "revision": revision,
        "versions": versions,
    }


def parse_serial(payload):
    """Längenpräfix + ASCII-Seriennummer robust dekodieren."""
    if not payload:
        raise ValueError("Leere Seriennummer-Antwort")
    length = payload[0]
    if length > len(payload) - 1:
        raise ValueError(
            "Seriennummer unvollständig: %d statt %d Byte"
            % (len(payload) - 1, length))
    raw = payload[1:1 + length]
    try:
        serial = raw.decode("ascii")
    except UnicodeDecodeError:
        serial = raw.hex().upper()
    return serial


class Device:
    """Eng begrenzter, ausschließlich lesender QLink-Client."""

    def __init__(self):
        self.link = bqlink.QLink(ALLOWED_COMMANDS)
        self.path = self.link.path

    def close(self):
        self.link.close()

    def _query(self, command):
        if command not in ALLOWED_COMMANDS:
            raise ValueError(
                "Kommando %02x %02x steht nicht auf der Lese-Whitelist."
                % command)

        return self.link.request(command)

    def read_info(self):
        result = parse_info(self._query(CMD_INFO))
        result["serial"] = parse_serial(self._query(CMD_SERIAL))
        result["path"] = self.path
        return result


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()

    device = Device()
    try:
        info = device.read_info()
    finally:
        device.close()

    print("Gerät:             %s" % info["path"])
    print("Modell:            %d" % info["model"])
    print("Hardware-Revision: %d" % info["revision"])
    print("Seriennummer:      %s" % info["serial"])
    for index, version in enumerate(info["versions"]):
        print("Firmware MCU%d:     %s" % (index, version))


if __name__ == "__main__":
    main()
