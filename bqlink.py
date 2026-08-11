#!/usr/bin/env python3
"""Gemeinsamer, eng begrenzter QLink-Transport für die Dark Mount.

Der Transport kümmert sich ausschließlich um den wiederkehrenden Rahmen:
Gerät finden, QLink-Sitzung öffnen/schließen, Sequenznummer, CRC und
Gerätestatus. Welche Nutzkommandos erlaubt sind, entscheidet weiterhin jedes
Fachmodul über seine eigene Whitelist.
"""

from collections import deque
import os
import secrets
import select
import struct
import time

import bqkeyd


PACKET_SIZE = 64
DATA_OFFSET = 7
CRC_OFFSET = 62
MAX_PAYLOAD = CRC_OFFSET - DATA_OFFSET
DEFAULT_TIMEOUT = 2.0

CMD_OPEN_SESSION = (0x01, 0x01)
CMD_CLOSE_SESSION = (0x01, 0x02)

_CRC_TABLE = []
for _index in range(256):
    _value = _index
    for _bit in range(8):
        _value = ((_value >> 1) ^ 0xA001
                  if _value & 1 else _value >> 1)
    _CRC_TABLE.append(_value)


def crc16_modbus(data):
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def parse_open_session(payload, nonce):
    """OpenSession-Antwort validieren und die Session-ID liefern."""
    if len(payload) < 7:
        raise IOError("OpenSession-Antwort zu kurz: %d Byte" % len(payload))
    if payload[:4] != nonce:
        raise IOError("OpenSession-Antwort enthält das falsche Nonce.")
    session = payload[4]
    if session == 0:
        raise IOError("OpenSession lieferte die ungültige Session 0.")
    return session


def build_frame(session, sequence, command, payload=b""):
    """Einen 64-Byte-QLink-Rahmen bauen; nützlich für Tests und Mitschnitte."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Nutzlast zu groß: %d Byte (erlaubt %d)"
                         % (len(payload), MAX_PAYLOAD))
    packet = bytearray(PACKET_SIZE)
    packet[0] = 6 + len(payload)
    packet[2] = session
    packet[4] = sequence
    packet[5], packet[6] = command
    packet[DATA_OFFSET:DATA_OFFSET + len(payload)] = payload
    struct.pack_into("<H", packet, CRC_OFFSET,
                     crc16_modbus(packet[:CRC_OFFSET]))
    return bytes(packet)


def frame_payload(frame):
    end = min(CRC_OFFSET, frame[0] + 1)
    return bytes(frame[DATA_OFFSET:end])


class QLink:
    """QLink-Verbindung mit expliziter Session und Statusprüfung."""

    def __init__(self, allowed_commands, path=None, timeout=DEFAULT_TIMEOUT):
        self.allowed_commands = set(allowed_commands)
        self.timeout = float(timeout)
        self.sequence = 0x30
        self.session = 0
        self._pending = deque(maxlen=32)

        if path is None:
            nodes = bqkeyd.find_vendor_node()
            if not nodes:
                raise SystemExit("Keine be quiet! Tastatur gefunden.")
            path = "/dev/" + nodes[0]
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        try:
            self._drain()
            self._open_session()
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()

    def close(self):
        if self.fd is None:
            return
        try:
            if self.session:
                try:
                    self._exchange(CMD_CLOSE_SESSION, b"", self.session)
                except (OSError, TimeoutError):
                    pass
                self.session = 0
        finally:
            os.close(self.fd)
            self.fd = None

    def allow(self, command):
        """Ein nach Geräteabfrage bestätigtes Kommando ergänzen."""
        self.allowed_commands.add(tuple(command))

    def _next_sequence(self):
        self.sequence = (self.sequence + 1) & 0xFF
        if self.sequence == 0:
            self.sequence = 1
        return self.sequence

    def _drain(self):
        while True:
            ready, _, _ = select.select([self.fd], [], [], 0)
            if not ready:
                return
            try:
                os.read(self.fd, PACKET_SIZE)
            except BlockingIOError:
                return

    def _read_frame(self, deadline):
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [self.fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                return None
            try:
                frame = os.read(self.fd, PACKET_SIZE)
            except BlockingIOError:
                continue
            if len(frame) != PACKET_SIZE:
                continue
            if not 6 <= frame[0] < CRC_OFFSET:
                continue
            expected = frame[CRC_OFFSET] | (frame[CRC_OFFSET + 1] << 8)
            if crc16_modbus(frame[:CRC_OFFSET]) != expected:
                continue
            return frame
        return None

    def _exchange(self, command, payload=b"", session=None,
                  return_frame=False):
        sequence = self._next_sequence()
        packet = build_frame(
            self.session if session is None else session,
            sequence, command, payload)
        written = os.write(self.fd, b"\x00" + packet)
        if written != PACKET_SIZE + 1:
            raise IOError("QLink-Report unvollständig geschrieben: %d Byte"
                          % written)

        deadline = time.monotonic() + self.timeout
        while True:
            frame = self._read_frame(deadline)
            if frame is None:
                break
            if tuple(frame[5:7]) != tuple(command) or frame[4] != sequence:
                self._pending.append(frame)
                continue
            if frame[3] != 0:
                raise IOError(
                    "Gerät hat %02x/%02x abgelehnt (Status %d)."
                    % (command[0], command[1], frame[3]))
            payload_out = frame_payload(frame)
            return (payload_out, frame) if return_frame else payload_out
        raise TimeoutError("Keine Antwort auf %02x/%02x (Sequenz %d)."
                           % (command[0], command[1], sequence))

    def _open_session(self):
        nonce = secrets.randbelow(90000) + 10000
        nonce_bytes = struct.pack("<I", nonce)
        response = self._exchange(
            CMD_OPEN_SESSION, nonce_bytes + bytes((2,)), session=0)
        self.session = parse_open_session(response, nonce_bytes)

    def request(self, command, payload=b"", return_frame=False):
        command = tuple(command)
        if command not in self.allowed_commands:
            raise ValueError("Kommando %02x %02x steht nicht auf der "
                             "Whitelist." % command)
        return self._exchange(command, payload, return_frame=return_frame)

    def read_notification(self, command, predicate=None, timeout=None):
        """Eine unaufgeforderte, CRC-geprüfte Nachricht abholen."""
        command = tuple(command)
        predicate = predicate or (lambda _frame: True)

        kept = deque(maxlen=self._pending.maxlen)
        found = None
        while self._pending:
            frame = self._pending.popleft()
            if (found is None and tuple(frame[5:7]) == command
                    and predicate(frame)):
                found = frame
            else:
                kept.append(frame)
        self._pending.extend(kept)
        if found is not None:
            return frame_payload(found), found

        deadline = time.monotonic() + (
            self.timeout if timeout is None else float(timeout))
        while True:
            frame = self._read_frame(deadline)
            if frame is None:
                return None, None
            if tuple(frame[5:7]) == command and predicate(frame):
                return frame_payload(frame), frame
            self._pending.append(frame)
