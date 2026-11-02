"""Synthetic control contract tests; no hardware, camera or game is opened."""
import math
import unittest

from platformer.test_controls import COMFORT_RIGHT, missing_arm, packet, pose
from thextech.controls import BITS, BUTTONS, BodyKeys, PadKeys, mask_for


class BridgeContractTests(unittest.TestCase):
    def test_wire_bits_are_exactly_the_bridge_order(self):
        self.assertEqual(BUTTONS, ('Up', 'Down', 'Left', 'Right', 'Jump',
                                  'AltJump', 'Run', 'AltRun', 'Drop', 'Start',
                                  'Confirm', 'Back'))
        self.assertEqual(mask_for(BUTTONS), 0xfff)
        self.assertEqual(mask_for({'Left', 'Jump', 'Run'}), (1 << 2) | (1 << 4) | (1 << 6))
        with self.assertRaises(KeyError):
            mask_for({'not-an-engine-action'})


class BodyTests(unittest.TestCase):
    def setUp(self):
        self.control = BodyKeys()
        self.t = 10.
        self.last = None

    def feed(self, right=COMFORT_RIGHT, left=(0., .5), *, dt=.05,
             menu=False, points=None, fields=None):
        self.t += dt
        self.last = packet(self.t, pose(right, left) if points is None else points,
                           **(fields or {}))
        out = self.control.update(self.last, self.t, menu=menu)
        self.assertEqual(out['mask'], mask_for(out['held']))
        self.assertNotIn('Start', out['held'])
        return out

    def ready(self, *, menu=False):
        for _ in range(18):
            out = self.feed(menu=menu)
        self.assertEqual(out['calibration'], 'ready')
        self.assertFalse(out['held'])

    def move(self, direction=1, **kwargs):
        right = (COMFORT_RIGHT[0] + direction * .32, COMFORT_RIGHT[1])
        self.feed(right, dt=.06, **kwargs)
        return self.feed(right, dt=.06, **kwargs)

    def lift_until_event(self, *, menu=False):
        for y in (.40, .22, .06, -.08, -.15, -.20, -.20):
            out = self.feed(left=(0., y), menu=menu)
            if out['pulses']:
                return out
        self.fail('fresh left-arm lift produced no event')

    def test_calibration_is_reused_and_horizontal_auto_run_stops_at_center(self):
        self.ready()
        self.assertEqual(self.move(1)['held'], {'Right', 'Run'})
        self.assertEqual(self.feed()['held'], set())
        self.assertEqual(self.move(-1)['held'], {'Left', 'Run'})
        self.assertEqual(self.feed()['held'], set())

    def test_optional_auto_run_off_preserves_walking(self):
        self.control = BodyKeys(auto_run=False)
        self.ready()
        self.assertEqual(self.move()['held'], {'Right'})

    def test_vertical_debounce_hysteresis_and_center_release(self):
        self.ready()
        low = (COMFORT_RIGHT[0], COMFORT_RIGHT[1] + .36)
        self.assertFalse(self.feed(low)['held'])
        self.assertEqual(self.feed(low)['held'], {'Down'})
        self.assertEqual(self.feed((.25, .87))['held'], {'Down'})
        self.assertFalse(self.feed((.25, .79))['held'])
        high = (COMFORT_RIGHT[0], COMFORT_RIGHT[1] - .36)
        self.assertFalse(self.feed(high)['held'])
        self.assertEqual(self.feed(high)['held'], {'Up'})
        self.assertFalse(self.feed()['held'])

    def test_duplicate_cannot_complete_vertical_activation(self):
        self.ready()
        self.assertFalse(self.feed((.25, 1.01))['held'])
        repeated = self.last
        for offset in (.02, .04, .06):
            out = self.control.update(repeated, self.t + offset)
            self.assertFalse(out['held'])
        self.t += .06
        self.assertEqual(self.feed((.25, 1.01))['held'], {'Down'})

    def test_vertical_reversal_releases_old_before_new_direction(self):
        self.ready()
        self.feed((.25, 1.01)); self.feed((.25, 1.01))
        # Move through a plausible intermediate position to avoid generating a
        # known impossible arm teleport in the inherited observation filter.
        self.feed((.25, .65), dt=.1)
        self.assertFalse(self.feed((.25, .29), dt=.1)['held'])
        self.assertEqual(self.feed((.25, .29))['held'], {'Up'})

    def test_diagonal_and_fully_dropped_right_hand_do_not_crouch(self):
        self.ready()
        for _ in range(3):
            self.assertFalse(self.feed((.85, 1.01))['held'])
        for _ in range(3):
            self.assertFalse(self.feed((.25, 1.42), dt=.1)['held'])

    def test_jump_is_one_bounded_hold_and_not_retriggered_by_duplicate(self):
        self.ready()
        out = self.lift_until_event()
        self.assertEqual(out['pulses'], {'Jump'})
        self.assertIn('Jump', out['held'])
        event_packet, event_time = self.last, self.t
        self.assertIn('Jump', self.control.update(event_packet, event_time + .06)['held'])
        self.assertFalse(self.control.update(event_packet, event_time + .06)['pulses'])
        self.assertNotIn('Jump', self.control.update(event_packet, event_time + .15)['held'])
        self.t += .15
        for _ in range(7):
            self.assertFalse(self.feed(left=(0., -.20))['pulses'])
        for y in (-.05, .15, .4, .5):
            self.feed(left=(0., y))
        self.assertEqual(self.lift_until_event()['pulses'], {'Jump'})

    def test_loss_and_expired_pose_release_timed_jump_and_directions(self):
        self.ready()
        self.lift_until_event()
        self.assertFalse(self.control.update(None, self.t + .01)['held'])
        self.assertFalse(self.control.update(self.last, self.t + 1.)['held'])
        self.assertFalse(self.control.update(self.last, self.t + 1.)['valid'])

    def test_partial_arm_loss_releases_only_its_actions(self):
        self.ready()
        self.move()
        p = missing_arm(pose((.57, .65)), 'right')
        out = self.feed(points=p)
        self.assertFalse(out['held'])
        self.lift_until_event()
        out = self.feed(points=missing_arm(pose(COMFORT_RIGHT), 'left'))
        self.assertNotIn('Jump', out['held'])

    def test_menu_has_direction_and_confirm_without_run_jump_or_start(self):
        self.ready(menu=True)
        self.assertEqual(self.move(menu=True)['held'], {'Right'})
        self.feed(menu=True)
        out = self.lift_until_event(menu=True)
        self.assertEqual(out['pulses'], {'Confirm'})
        self.assertEqual(out['held'], {'Confirm'})

    def test_context_change_and_reset_do_not_replay_old_jump(self):
        self.ready()
        self.lift_until_event()
        out = self.control.update(self.last, self.t + .01, menu=True)
        self.assertFalse(out['held'])
        self.assertFalse(out['pulses'])
        self.control.reset()
        out = self.control.update(self.last, self.t + .02, menu=True)
        self.assertFalse(out['held'])

    def test_invalid_clock_and_jump_duration_are_rejected(self):
        for duration in (0., .5, math.nan, True):
            with self.assertRaises(ValueError):
                BodyKeys(jump_hold=duration)
        self.ready()
        self.assertFalse(self.control.update(self.last, math.nan)['held'])


class PadTests(unittest.TestCase):
    def setUp(self):
        self.control = PadKeys()
        self.t, self.seq = 10., 0

    def feed(self, buttons=(), pressed=(), *, axes=None, menu=False, **kwargs):
        self.t += .05
        self.seq += 1
        sample = dict(connected=True, buttons=set(buttons), pressed=set(pressed),
                      axes=axes or {}, sequence=self.seq, timestamp=self.t,
                      sampled_at=self.t)
        sample.update(kwargs)
        out = self.control.update(sample, self.t, menu=menu)
        self.assertNotIn('Start', out['held'])
        self.assertEqual(out['mask'], mask_for(out['held']))
        return out

    def ready(self, **kwargs):
        self.assertTrue(self.feed(**kwargs)['valid'])

    def test_held_buttons_on_entry_require_release(self):
        self.assertFalse(self.feed({'A'})['valid'])
        self.assertFalse(self.feed(axes={'lx': .8})['valid'])
        self.ready()
        self.assertIn('Jump', self.feed({'A'})['held'])

    def test_directions_and_simultaneous_contradictions(self):
        self.ready()
        self.assertEqual(self.feed({'LEFT'})['held'], {'Left'})
        self.assertEqual(self.feed(axes={'lx': .8, 'ly': -.8})['held'], {'Right', 'Up'})
        self.assertFalse(self.feed({'LEFT', 'RIGHT', 'UP', 'DOWN'})['held'])

    def test_gameplay_buttons_map_without_host_start_or_right_stick(self):
        self.ready()
        out = self.feed({'A', 'B', 'X', 'Y', 'BACK', 'START', 'R3'},
                        axes={'rx': 1., 'ry': -1.})
        self.assertEqual(out['held'], {'Jump', 'AltJump', 'Run', 'AltRun', 'Drop'})
        self.assertEqual(out['pulses'], out['held'])

    def test_rt_runs_and_held_jump_retains_variable_height(self):
        self.ready()
        self.feed({'A'})
        for _ in range(10):
            out = self.feed({'A'}, axes={'rt': .7})
            self.assertEqual(out['held'], {'Jump', 'Run'})
            self.assertFalse(out['pulses'])
        self.assertFalse(self.feed()['held'])

    def test_quick_press_between_snapshots_is_not_lost(self):
        self.ready()
        out = self.feed(pressed={'A'})
        self.assertEqual(out['held'], {'Jump'})
        self.assertEqual(out['pulses'], {'Jump'})
        self.assertIn('Jump', self.feed()['held'])
        self.feed()
        self.assertNotIn('Jump', self.feed()['held'])

    def test_same_event_snapshot_never_repeats_press(self):
        self.ready()
        sample = dict(connected=True, buttons=set(), pressed={'A'}, axes={},
                      timestamp=self.t, sampled_at=self.t, sequence=self.seq)
        # Event identity already observed: even raw_pressed is not replayed.
        self.assertFalse(self.control.update(sample, self.t)['pulses'])
        sample['sequence'] += 1
        self.assertEqual(self.control.update(sample, self.t)['pulses'], {'Jump'})
        self.assertFalse(self.control.update(sample, self.t + .04)['pulses'])

    def test_menu_confirm_back_without_game_actions(self):
        self.ready(menu=True)
        out = self.feed({'A', 'B', 'X', 'Y', 'START'}, menu=True)
        self.assertEqual(out['held'], {'Confirm', 'Back'})
        self.assertEqual(out['pulses'], {'Confirm', 'Back'})

    def test_disconnect_and_reconnect_release_and_require_neutral(self):
        self.ready()
        self.feed({'A', 'RIGHT'})
        self.assertFalse(self.feed(connected=False)['held'])
        self.assertFalse(self.feed({'A'})['valid'])
        self.ready()
        self.assertIn('Jump', self.feed({'A'})['held'])

    def test_transport_stale_releases_but_old_event_time_does_not_timeout(self):
        self.ready()
        self.assertEqual(self.feed({'RIGHT'}, timestamp=0.)['held'], {'Right'})
        self.assertFalse(self.feed({'RIGHT'}, sampled_at=self.t - 1.)['held'])
        self.assertFalse(self.feed({'RIGHT'})['valid'])

    def test_future_or_invalid_samples_cannot_latch_actions(self):
        self.ready()
        for field in ({'timestamp': self.t + 5}, {'sampled_at': self.t + 5},
                      {'axes': {'lx': math.nan}}, {'sequence': True},
                      {'buttons': 'A'}, {'pressed': [1]}):
            sample = dict(connected=True, buttons=set(), pressed=set(), axes={},
                          timestamp=self.t, sampled_at=self.t, sequence=self.seq)
            sample.update(field)
            self.assertFalse(self.control.update(sample, self.t)['held'])
            self.feed()

    def test_sequence_rollback_clears_and_requires_release(self):
        self.ready()
        self.feed({'A'})
        self.assertFalse(self.feed({'A'}, sequence=0)['held'])
        self.assertFalse(self.feed({'A'})['valid'])
        self.ready()

    def test_context_change_does_not_leak_confirmation_into_jump(self):
        self.ready(menu=True)
        self.feed({'A'}, menu=True)
        self.assertFalse(self.feed({'A'}, menu=False)['held'])
        self.ready(menu=False)
        self.assertEqual(self.feed({'A'})['pulses'], {'Jump'})


if __name__ == '__main__':
    unittest.main()
