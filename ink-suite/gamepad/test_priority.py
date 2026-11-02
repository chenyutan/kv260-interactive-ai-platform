"""Ownership is based on live controls, independently of menu focus."""
import unittest

from gamepad.device import GamepadReader, EV_KEY, EV_ABS, EV_SYN, SYN_REPORT
from gamepad.priority import InputPriority
from gamepad.test_device import standard_axes


def state(**values):
    result = dict(connected=True, buttons=(), pressed=(), released=(), axes={})
    result.update(values)
    return result


class PriorityTests(unittest.TestCase):
    def test_idle_connection_never_owns_body_control(self):
        priority = InputPriority()
        for now in (0., 1., 9., 500.):
            priority.update(state(), now)
            self.assertFalse(priority.gestures_blocked(now))
            self.assertFalse(priority.navigation_active(now))

    def test_pressed_released_and_held_each_extend_grace(self):
        for field in ('pressed', 'released', 'buttons'):
            with self.subTest(field=field):
                priority = InputPriority()
                priority.update(state(**{field: ('A',)}), 5.)
                priority.update(state(), 7.999)
                self.assertTrue(priority.gestures_blocked(7.999))
                self.assertFalse(priority.gestures_blocked(8.))

    def test_held_direction_never_times_out_and_release_starts_grace(self):
        priority = InputPriority()
        for now in (1., 5., 10., 100.):
            priority.update(state(buttons=('RIGHT',)), now)
            self.assertTrue(priority.gestures_blocked(now))
            self.assertTrue(priority.navigation_active(now))
        priority.update(state(released=('RIGHT',)), 101.)
        priority.update(state(), 103.999)
        self.assertTrue(priority.gestures_blocked(103.999))
        self.assertFalse(priority.gestures_blocked(104.))

    def test_all_sticks_after_reader_deadzone_and_triggers_own_input(self):
        for name, value in (('lx', .01), ('ly', -.01), ('rx', .01),
                            ('ry', -.01), ('lt', .1), ('rt', .1)):
            with self.subTest(axis=name):
                priority = InputPriority()
                for now in (1., 5., 10.):
                    priority.update(state(axes={name: value}), now)
                    self.assertTrue(priority.gestures_blocked(now))
                priority.update(state(), 12.999)
                self.assertTrue(priority.gestures_blocked(12.999))
                self.assertFalse(priority.gestures_blocked(13.))

    def test_trigger_rest_noise_does_not_claim_control(self):
        priority = InputPriority()
        priority.update(state(axes={'lt': .02, 'rt': .05}), 100.)
        self.assertFalse(priority.gestures_blocked(100.))

    def test_reader_deadzone_jitter_does_not_claim_control(self):
        reader = GamepadReader(start=False)
        self.addCleanup(reader.close)
        reader.attach('test', standard_axes())
        reader.feed(EV_ABS, 0, 1000)
        reader.feed(EV_ABS, 3, -2000)
        reader.feed(EV_SYN, SYN_REPORT, 0)
        priority = InputPriority()
        priority.update(reader.snapshot(), 100.)
        self.assertFalse(priority.gestures_blocked(100.))
        reader.feed(EV_ABS, 3, 12000)
        reader.feed(EV_SYN, SYN_REPORT, 0)
        priority.update(reader.snapshot(), 101.)
        self.assertTrue(priority.gestures_blocked(101.))
        self.assertFalse(priority.navigation_active(101.))

    def test_pointer_takes_over_old_focus_even_in_explicit_gamepad_mode(self):
        for mode in ('body', 'gamepad'):
            with self.subTest(mode=mode):
                priority = InputPriority()
                priority.update(state(pressed=('RIGHT',)), 1.)
                self.assertTrue(priority.navigation_active(1., mode))
                priority.update(state(axes={'rx': .8}), 1.1)
                self.assertTrue(priority.gestures_blocked(1.1, mode))
                self.assertFalse(priority.navigation_active(1.1, mode))
                priority.update(state(), 1.2)
                self.assertFalse(priority.navigation_active(1.2, mode))
                priority.update(state(pressed=('A',)), 1.3)
                self.assertTrue(priority.navigation_active(1.3, mode))

    def test_R3_and_back_chord_do_not_claim_menu_focus(self):
        priority = InputPriority()
        for snapshot in (state(buttons=('R3', 'BACK'), pressed=('R3', 'BACK')),
                         state(buttons=('BACK',), released=('R3',)),
                         state(released=('BACK',))):
            priority.update(snapshot, 10.)
            self.assertTrue(priority.gestures_blocked(10.))
            self.assertFalse(priority.navigation_active(10., 'gamepad'))

    def test_explicit_gamepad_mode_blocks_gestures_after_idle_and_disconnect(self):
        priority = InputPriority()
        priority.update(state(), 100.)
        self.assertTrue(priority.gestures_blocked(100., 'gamepad'))
        self.assertTrue(priority.navigation_active(100., 'gamepad'))
        priority.update(state(connected=False, released=('A',)), 101.)
        self.assertTrue(priority.gestures_blocked(101., 'gamepad'))
        self.assertFalse(priority.gestures_blocked(101., 'body'))
        self.assertFalse(priority.navigation_active(101., 'gamepad'))
        priority.update(state(), 102.)
        self.assertFalse(priority.gestures_blocked(102.))

    def test_invalid_axes_do_not_poison_ownership(self):
        priority = InputPriority()
        priority.update(state(axes={'lx': float('nan'), 'rx': float('inf'),
                                   'lt': None, 'rt': 'bad'}), 100.)
        self.assertFalse(priority.gestures_blocked(100.))


class SnapshotPeekTests(unittest.TestCase):
    def test_peek_preserves_press_release_and_latest_held_state(self):
        reader = GamepadReader(start=False)
        self.addCleanup(reader.close)
        reader.attach('test', standard_axes())
        for value in (1, 0):
            reader.feed(EV_KEY, 304, value)
            reader.feed(EV_SYN, SYN_REPORT, 0)
        for _ in range(3):
            peek = reader.snapshot(consume_edges=False)
            self.assertEqual(peek['pressed'], {'A'})
            self.assertEqual(peek['released'], {'A'})
            self.assertFalse(peek['buttons'])
        consumed = reader.snapshot()
        self.assertEqual(consumed['pressed'], {'A'})
        self.assertEqual(consumed['released'], {'A'})
        self.assertFalse(reader.snapshot()['pressed'])
        self.assertFalse(reader.snapshot()['released'])


if __name__ == '__main__':
    unittest.main()
