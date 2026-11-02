"""Pure cursor-idle contracts; no GTK, display, devices, or user data.

Run with the intended suite directory on PYTHONPATH after cursor_autohide.py
has been implemented. This file intentionally checks public policy outcomes
rather than reproducing its implementation or relying on private fields.
"""
import math
import unittest

from cursor_autohide import CursorPolicy


class CursorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = CursorPolicy(hide_seconds=2.)
        self.point = (120., 240.)

    def update(self, now, point=None, **kw):
        return self.policy.update(now, self.point if point is None else point, **kw)

    def hidden(self):
        self.assertTrue(self.update(10.))
        self.assertFalse(self.update(12.))

    def test_first_valid_sample_shows_even_after_long_process_start(self):
        self.assertTrue(self.update(12345.))
        self.assertTrue(self.update(12346.999))
        self.assertFalse(self.update(12347.))

    def test_exact_two_second_idle_boundary(self):
        self.assertTrue(self.update(10.))
        self.assertTrue(self.update(11.999))
        self.assertFalse(self.update(12.))
        self.assertFalse(self.update(20.))

    def test_identical_samples_do_not_extend_deadline(self):
        self.assertTrue(self.update(10.))
        for now in (10.1, 10.3, 10.8, 11.3, 11.9):
            self.assertTrue(self.update(now))
        self.assertFalse(self.update(12.))

    def test_numerically_equal_coordinates_are_not_motion(self):
        self.assertTrue(self.update(10., (120, 240)))
        self.assertTrue(self.update(11.9, (120., 240.)))
        self.assertFalse(self.update(12., (120, 240)))

    def test_motion_recovers_a_hidden_cursor_immediately(self):
        self.hidden()
        self.assertTrue(self.update(12.01, (121., 240.)))
        self.assertTrue(self.update(14., (121., 240.)))
        self.assertFalse(self.update(14.01, (121., 240.)))

    def test_motion_in_either_axis_counts(self):
        for point in ((121., 240.), (120., 241.)):
            with self.subTest(point=point):
                self.policy.reset()
                self.hidden()
                self.assertTrue(self.update(12.1, point))

    def test_continuous_motion_remains_visible(self):
        for index in range(30):
            self.assertTrue(self.update(100. + index, (120. + index, 240.)))
        self.assertTrue(self.update(130., (149., 240.)))
        self.assertFalse(self.update(131., (149., 240.)))

    def test_configured_idle_period_is_used(self):
        self.policy = CursorPolicy(hide_seconds=.5)
        self.assertTrue(self.update(10.))
        self.assertTrue(self.update(10.499))
        self.assertFalse(self.update(10.5))

    def test_button_hold_preserves_visibility_without_pointer_motion(self):
        self.hidden()
        self.assertTrue(self.update(12.1, buttons_down=True))
        self.assertTrue(self.update(20., buttons_down=True))
        self.assertTrue(self.update(40., buttons_down=True))

    def test_button_release_starts_a_new_idle_period(self):
        self.assertTrue(self.update(10.))
        self.assertTrue(self.update(20., buttons_down=True))
        self.assertTrue(self.update(25., buttons_down=True))
        self.assertTrue(self.update(25.1, buttons_down=False))
        self.assertTrue(self.update(27.099, buttons_down=False))
        self.assertFalse(self.update(27.1, buttons_down=False))

    def test_ineligible_after_hide_forces_visible(self):
        self.hidden()
        self.assertTrue(self.update(12.1, eligible=False))
        self.assertTrue(self.update(100., eligible=False))

    def test_reentering_application_gets_a_fresh_idle_period(self):
        self.hidden()
        self.assertTrue(self.update(12.1, eligible=False))
        self.assertTrue(self.update(100., eligible=True))
        self.assertTrue(self.update(101.999, eligible=True))
        self.assertFalse(self.update(102., eligible=True))

    def test_ineligible_and_held_button_still_forces_visible(self):
        self.hidden()
        self.assertTrue(self.update(12.1, eligible=False, buttons_down=True))
        self.assertTrue(self.update(100., eligible=True, buttons_down=False))

    def test_missing_pointer_forces_visible_and_resets_previous_idle(self):
        self.hidden()
        self.assertTrue(self.policy.update(12.1, None))
        self.assertTrue(self.update(100.))
        self.assertFalse(self.update(102.))

    def test_invalid_coordinates_never_leave_cursor_hidden(self):
        invalid = ((math.nan, 240.), (120., math.inf), (-math.inf, 240.),
                   (), (120.,), (120., 240., 0.), 'bad', object())
        for point in invalid:
            with self.subTest(point=repr(point)):
                self.policy.reset()
                self.hidden()
                self.assertTrue(self.policy.update(12.1, point))
                self.assertTrue(self.update(100.))

    def test_negative_valid_screen_coordinates_work(self):
        self.assertTrue(self.update(10., (-120., -240.)))
        self.assertFalse(self.update(12., (-120., -240.)))
        self.assertTrue(self.update(12.1, (-119., -240.)))

    def test_reset_while_hidden_discards_previous_idle_history(self):
        self.hidden()
        self.policy.reset()
        self.assertTrue(self.update(100.))
        self.assertTrue(self.update(101.999))
        self.assertFalse(self.update(102.))

    def test_reset_is_idempotent(self):
        self.hidden()
        self.policy.reset()
        self.policy.reset()
        self.assertTrue(self.update(100.))
        self.assertFalse(self.update(102.))


if __name__ == '__main__':
    unittest.main()
