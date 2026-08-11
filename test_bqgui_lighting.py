import unittest
from unittest.mock import patch

import bqgui


class OnboardLightingTests(unittest.TestCase):
    def test_writes_selected_colour_then_releases_lamparray(self):
        events = []

        class FakeLighting:
            def __init__(self):
                events.append("open-qlink")

            def set_effect(self, effect, rgb, brightness, speed):
                events.append(("set", effect, rgb, brightness, speed))

            def close(self):
                events.append("close-qlink")

        class FakeLampArray:
            def __init__(self):
                events.append("open-lamparray")

            def release(self):
                events.append("release-lamparray")

            def close(self):
                events.append("close-lamparray")

        with patch.object(bqgui.bqlight, "Lighting", FakeLighting), \
                patch.object(bqgui.bqlamp, "LampArray", FakeLampArray):
            bqgui.apply_onboard_effect(0, [(0x12, 0x34, 0x56)], 80, 40)

        self.assertEqual(events, [
            "open-qlink",
            ("set", 0, [(0x12, 0x34, 0x56)], 80, 40),
            "close-qlink",
            "open-lamparray",
            "release-lamparray",
            "close-lamparray",
        ])


class ImageLoaderProgressTests(unittest.TestCase):
    def test_reports_byte_progress_inside_each_key(self):
        class FakeKeyboard:
            def read_image(self, _key_id, progress=None):
                for done in (10, 25, 50, 75, 100):
                    progress(done, 100)
                return b"jpeg", 120, 120, 3

            def close(self):
                pass

        values = []
        loader = bqgui.ImageLoader(
            [bqgui.bqkeyd.FIRST_KEY, bqgui.bqkeyd.FIRST_KEY + 1])
        loader.progress_value.connect(
            lambda value, maximum: values.append((value, maximum)))

        with patch.object(bqgui.bqimage, "Keyboard", FakeKeyboard):
            loader.run()

        self.assertEqual(values[0], (0, 2000))
        self.assertIn((500, 2000), values)
        self.assertIn((1500, 2000), values)
        self.assertEqual(values[-1], (2000, 2000))


if __name__ == "__main__":
    unittest.main()
