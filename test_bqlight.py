import unittest

import bqlight


class EffectPayloadTests(unittest.TestCase):
    def test_single_colour_matches_iocenter_trace(self):
        self.assertEqual(
            bqlight.effect_payload(
                bqlight.EFFECTS["static"], [(0xFF, 0xDD, 0x27)],
                brightness=100, speed=80),
            bytes.fromhex("00 00 00 64 50 00 ff dd 27"),
        )

    def test_palette_matches_iocenter_trace(self):
        colours = [
            (0xFF, 0x00, 0x00), (0x00, 0xFF, 0xFF),
            (0x00, 0x11, 0x00), (0xFF, 0x00, 0x21),
            (0x00, 0xFF, 0xFF), (0x32, 0x00, 0x00),
            (0xFF, 0x43, 0xFF),
        ]
        self.assertEqual(
            bqlight.effect_payload(
                bqlight.EFFECTS["tornado"], colours,
                brightness=100, speed=50, direction=4),
            bytes.fromhex(
                "00 02 04 64 32 02 07 "
                "ff 00 00 00 ff ff 00 11 00 ff 00 21 00 ff ff "
                "32 00 00 ff 43 ff"
            ),
        )

    def test_rejects_invalid_rgb(self):
        with self.assertRaisesRegex(ValueError, "Farbe"):
            bqlight.effect_payload(0, [(256, 0, 0)])


class SessionTests(unittest.TestCase):
    def test_parses_session_after_nonce(self):
        nonce = bytes.fromhex("a0 5b 00 00")
        payload = nonce + bytes.fromhex("07 01 02")
        self.assertEqual(bqlight.parse_open_session(payload, nonce), 7)

    def test_rejects_wrong_nonce(self):
        with self.assertRaisesRegex(IOError, "Nonce"):
            bqlight.parse_open_session(
                bytes.fromhex("a0 5b 00 00 02 01 07"),
                bytes.fromhex("39 30 00 00"),
            )


if __name__ == "__main__":
    unittest.main()
