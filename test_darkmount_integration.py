import struct
import unittest

import bqdevice
import bqdock


class DeviceInfoTests(unittest.TestCase):
    def test_parse_captured_device_info(self):
        payload = bytes.fromhex(
            "01 00 01 03 00 00 29 01 00 00 29 01 00 00 29 01")
        self.assertEqual(
            bqdevice.parse_info(payload),
            {
                "model": 1,
                "revision": 1,
                "versions": ["1.29.0", "1.29.0", "1.29.0"],
            })

    def test_parse_captured_serial(self):
        payload = bytes.fromhex("0e 30 30 32 43 35 33 39 30 30 30 32 31 32 30")
        self.assertEqual(bqdevice.parse_serial(payload), "002C5390002120")

    def test_rejects_truncated_info(self):
        with self.assertRaises(ValueError):
            bqdevice.parse_info(bytes.fromhex("01 00 01 03 00"))


class DockReadTests(unittest.TestCase):
    def test_read_image_uses_bounded_chunks(self):
        pixels = bytes((n % 251 for n in range(320 * 240 * 2)))
        blob = struct.pack("<IHHB", bqdock.IMAGE_BYTES, 320, 240, 1) + pixels
        requests = []

        dock = object.__new__(bqdock.Dock)

        def request(command, payload=b"", offset=None):
            self.assertEqual(command, bqdock.CMD_IMAGE_READ)
            self.assertIsNone(offset)
            image_type, start, length = struct.unpack("<BII", payload)
            self.assertEqual(image_type, 0)
            self.assertLessEqual(length, bqdock.READ_CHUNK)
            requests.append((start, length))
            start, length = requests[-1]
            return blob[start:start + length]

        dock._request = request

        self.assertEqual(dock.read_image(), pixels)
        self.assertEqual(requests[0], (0, bqdock.HEADER_SIZE))
        self.assertEqual(requests[1], (bqdock.HEADER_SIZE,
                                       bqdock.READ_CHUNK))
        self.assertEqual(requests[-1][0] + requests[-1][1],
                         bqdock.IMAGE_BYTES)

    def test_read_image_rejects_wrong_dimensions(self):
        dock = object.__new__(bqdock.Dock)
        dock._request = lambda *args, **kwargs: struct.pack(
            "<IHHB", bqdock.IMAGE_BYTES, 240, 320, 1)
        with self.assertRaisesRegex(IOError, "Unerwarteter Bildkopf"):
            dock.read_image()


if __name__ == "__main__":
    unittest.main()
