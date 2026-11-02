import math
import struct
import unittest
from gamepad.device import (GamepadReader, EV_KEY, EV_ABS, EV_SYN, SYN_REPORT,
                            SYN_DROPPED, EVENT, _sysfs_bitmap)


def standard_axes():
    return {code: (0, -32768, 32767, 0, 3000, 0) for code in (0, 1, 3, 4)} | {
        code: (0, 0, 255, 0, 0, 0) for code in (2, 5)}


def generic_axes(zero_initial=False):
    return {code: (0 if zero_initial else 128, 0, 255, 0, 15, 0)
            for code in (0, 1, 2, 5)} | {
        9: (0, 0, 255, 0, 15, 0), 10: (0, 0, 255, 0, 15, 0),
        16: (0, -1, 1, 0, 0, 0), 17: (0, -1, 1, 0, 0, 0)}


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.reader = GamepadReader(start=False)
        self.reader.attach("test", standard_axes())
        self.reader.snapshot()

    def tearDown(self): self.reader.close()

    def report(self, *events):
        for typ, code, value in events: self.reader.feed(typ, code, value)
        self.reader.feed(EV_SYN, SYN_REPORT, 0)
        return self.reader.snapshot()

    def test_idle_snapshot_schema_and_copy(self):
        s = self.reader.snapshot()
        self.assertTrue(s["connected"])
        self.assertIsInstance(s["buttons"], frozenset)
        self.assertEqual(set(s["axes"]), {"lx", "ly", "rx", "ry", "lt", "rt"})
        s["axes"]["lx"] = 99
        self.assertEqual(self.reader.snapshot()["axes"]["lx"], 0)

    def test_atomic_reports_and_consumed_edges(self):
        self.reader.feed(EV_KEY, 304, 1)
        self.assertNotIn("A", self.reader.snapshot()["buttons"])
        s = self.report()
        self.assertEqual(s["pressed"], {"A"})
        self.assertEqual(self.reader.snapshot()["pressed"], set())
        self.assertEqual(self.reader.snapshot()["buttons"], {"A"})

    def test_button_release_and_repeat(self):
        self.report((EV_KEY, 304, 1))
        s = self.report((EV_KEY, 304, 2))
        self.assertEqual(s["pressed"], set())
        self.assertEqual(self.report((EV_KEY, 304, 0))["released"], {"A"})

    def test_all_required_key_names(self):
        codes = {304: "A", 305: "B", 307: "X", 308: "Y", 310: "LB", 311: "RB",
                 312: "LT", 313: "RT", 314: "BACK", 315: "START", 316: "GUIDE",
                 317: "L3", 318: "R3", 544: "UP", 545: "DOWN", 546: "LEFT", 547: "RIGHT"}
        s = self.report(*[(EV_KEY, code, 1) for code in codes])
        self.assertEqual(s["pressed"], set(codes.values()))

    def test_deadzone_and_normalized_extreme(self):
        s = self.report((EV_ABS, 0, 2500), (EV_ABS, 1, -2500))
        self.assertEqual(s["axes"]["lx"], 0)
        s = self.report((EV_ABS, 0, 32767), (EV_ABS, 1, 0))
        self.assertAlmostEqual(s["axes"]["lx"], 1)
        self.assertEqual(s["axes"]["ly"], 0)
        s = self.report((EV_ABS, 0, -32768))
        self.assertAlmostEqual(s["axes"]["lx"], -1)

    def test_diagonal_is_bounded_radially(self):
        s = self.report((EV_ABS, 0, 32767), (EV_ABS, 1, 32767))
        self.assertAlmostEqual(math.hypot(s["axes"]["lx"], s["axes"]["ly"]), 1)

    def test_g7_hid_axis_profile(self):
        self.reader.attach("GameSir-G7 Pro", generic_axes())
        s = self.report((EV_ABS, 2, 255), (EV_ABS, 5, 128), (EV_ABS, 10, 255))
        self.assertGreater(s["axes"]["rx"], .99)
        self.assertAlmostEqual(s["axes"]["lt"], 1)
        self.assertEqual(s["axes"]["rt"], 0)
        self.assertIn("LT", s["pressed"])

    def test_g7_zero_initial_sticks_require_center(self):
        self.reader.attach("GameSir-G7 Pro", generic_axes(True))
        self.assertEqual(self.reader.snapshot()["axes"]["rx"], 0)
        self.assertEqual(self.report((EV_ABS, 2, 255))["axes"]["rx"], 0)
        self.report((EV_ABS, 2, 128), (EV_ABS, 5, 128))
        self.assertGreater(self.report((EV_ABS, 2, 255))["axes"]["rx"], .99)

    def test_hat_changes_are_edges(self):
        self.reader.attach("G7", generic_axes())
        self.assertEqual(self.report((EV_ABS, 16, -1))["pressed"], {"LEFT"})
        s = self.report((EV_ABS, 16, 1), (EV_ABS, 17, -1))
        self.assertEqual(s["released"], {"LEFT"})
        self.assertEqual(s["pressed"], {"RIGHT", "UP"})

    def test_trigger_hysteresis(self):
        self.assertIn("RT", self.report((EV_ABS, 5, 160))["pressed"])
        self.assertIn("RT", self.report((EV_ABS, 5, 110))["buttons"])
        self.assertIn("RT", self.report((EV_ABS, 5, 80))["released"])

    def test_attach_held_button_requires_release_and_repress(self):
        self.reader.attach("test", standard_axes(), [304])
        self.assertNotIn("A", self.reader.snapshot()["buttons"])
        self.assertNotIn("A", self.report((EV_KEY, 304, 2))["pressed"])
        self.report((EV_KEY, 304, 0))
        self.assertEqual(self.report((EV_KEY, 304, 1))["pressed"], {"A"})

    def test_attach_held_trigger_requires_release(self):
        axes = standard_axes()
        axes[2] = (255, 0, 255, 0, 0, 0)
        self.reader.attach("test", axes)
        self.assertEqual(self.reader.snapshot()["axes"]["lt"], 0)
        self.report((EV_ABS, 2, 0))
        self.assertIn("LT", self.report((EV_ABS, 2, 255))["pressed"])

    def test_disconnect_cancels_unsent_press_and_releases_hold(self):
        self.report((EV_KEY, 304, 1))
        self.reader.feed(EV_KEY, 305, 1)
        self.reader.feed(EV_SYN, SYN_REPORT, 0)
        self.reader.disconnect()
        s = self.reader.snapshot()
        self.assertFalse(s["connected"])
        self.assertFalse(s["pressed"])
        self.assertEqual(s["released"], {"A", "B"})
        self.assertFalse(s["buttons"])
        self.assertTrue(all(v == 0 for v in s["axes"].values()))

    def test_syn_dropped_cancels_state_and_ignores_until_resync(self):
        self.report((EV_KEY, 304, 1))
        self.reader.feed(EV_SYN, SYN_DROPPED, 0)
        self.reader.feed(EV_KEY, 305, 1)
        s = self.report()
        self.assertEqual(s["released"], {"A"})
        self.assertFalse(s["buttons"])
        self.reader.attach("test", standard_axes(), [304, 305])
        self.assertFalse(self.reader.snapshot()["pressed"])
        self.report((EV_KEY, 305, 0))
        self.assertEqual(self.report((EV_KEY, 305, 1))["pressed"], {"B"})

    def test_edges_bounded_under_unresponsive_consumer(self):
        for i in range(1000):
            self.reader.feed(EV_KEY, 304, i % 2)
            self.reader.feed(EV_SYN, SYN_REPORT, 0)
        self.assertLessEqual(len(self.reader._edges), 64)
        self.assertGreater(self.reader.snapshot()["edge_overflows"], 0)

    def test_unplug_replug_never_replays_held_action(self):
        self.report((EV_KEY, 315, 1))
        self.reader.disconnect()
        self.reader.attach("test", standard_axes(), [315])
        self.assertFalse(self.reader.snapshot()["pressed"])
        self.report((EV_KEY, 315, 0))
        self.assertEqual(self.report((EV_KEY, 315, 1))["pressed"], {"START"})

    def test_sysfs_bitmap_unpadded_machine_words(self):
        if struct.calcsize("P") == 8:
            value = _sysfs_bitmap("7fff000000000000 0 0 0 0")
            self.assertTrue(value & (1 << 304))
            self.assertTrue(value & (1 << 318))
            self.assertFalse(value & (1 << 319))

    def test_event_struct_matches_native_linux_layout(self):
        expected = 24 if struct.calcsize("P") == 8 else 16
        self.assertEqual(EVENT.size, expected)

    def test_close_is_repeatable(self):
        self.reader.close()
        self.reader.close()
        self.assertFalse(self.reader.snapshot()["connected"])


if __name__ == "__main__": unittest.main()
