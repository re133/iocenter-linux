import unittest

import bqmeter


class FakeLamp:
    def __init__(self, lamp_id, y):
        self.id = lamp_id
        self.y = y


def dark_mount_layout():
    lamps = [FakeLamp(0, 0), FakeLamp(200, 140000)]
    lamps += [FakeLamp(i, 28000) for i in range(25, 43)]
    lamps += [FakeLamp(i, 28000) for i in range(161, 163)]
    lamps += [FakeLamp(i, 56000) for i in range(45, 64)]
    lamps += [FakeLamp(i, 56000) for i in range(165, 171)]
    # Weitere dicht belegte Tastenreihen; nur ihre Reihenfolge ist relevant.
    lamps += [FakeLamp(i, 70000) for i in range(70, 82)]
    return lamps


class MappingTests(unittest.TestCase):
    def test_dark_mount_meter_rows(self):
        gpu, cpu = bqmeter.meter_keys(dark_mount_layout())
        self.assertEqual(gpu, list(range(27, 37)))
        self.assertEqual(cpu, list(range(47, 57)))

    def test_unknown_layout_is_rejected(self):
        lamps = [FakeLamp(0, 0), FakeLamp(99, 140000)]
        lamps += [FakeLamp(i, 28000) for i in range(10)]
        lamps += [FakeLamp(i + 20, 56000) for i in range(10)]
        with self.assertRaisesRegex(ValueError, "Unbekanntes LampArray-Layout"):
            bqmeter.meter_keys(lamps)


class LevelTests(unittest.TestCase):
    def test_level_boundaries(self):
        cases = {
            -1: 0, 0: 0, 0.1: 1, 10: 1, 10.1: 2,
            50: 5, 99.9: 10, 100: 10, 120: 10,
        }
        for load, expected in cases.items():
            with self.subTest(load=load):
                self.assertEqual(bqmeter.level(load), expected)

    def test_dot_lights_only_current_bucket(self):
        items = bqmeter.row_colours(range(10), 42, (1, 2, 3), "dot")
        self.assertEqual([lamp for lamp, colour in items
                          if colour != bqmeter.OFF], [4])

    def test_bar_lights_all_buckets_up_to_value(self):
        items = bqmeter.row_colours(range(10), 42, (1, 2, 3), "bar")
        self.assertEqual([lamp for lamp, colour in items
                          if colour != bqmeter.OFF], [0, 1, 2, 3, 4])

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            bqmeter.row_colours(range(10), 50, (1, 2, 3), "unknown")


if __name__ == "__main__":
    unittest.main()
