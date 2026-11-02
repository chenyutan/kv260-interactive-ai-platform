"""Mouse toolbar timing, owner boundaries and standalone-control contracts."""
import unittest
from unittest.mock import Mock
from toolbar_autohide import ToolbarPolicy, hide_embedded_toolbar, inside


class ToolbarPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ToolbarPolicy()
        self.owner = (100, 200, 960, 540)
        self.bar = (100, 200, 960, 46)

    def update(self, now, point, **kw):
        return self.policy.update(now, point, self.owner, self.bar, **kw)

    def test_start_hidden_and_only_three_pixel_top_reveals(self):
        self.assertFalse(self.update(1., (350, 210)))
        self.assertFalse(self.update(2., (350, 203)))
        self.assertTrue(self.update(3., (350, 202)))
        self.assertTrue(self.update(4., (1059, 200)))

    def test_leaving_bar_keeps_three_seconds_then_hides(self):
        self.update(0., (350, 200))
        self.assertTrue(self.update(1., (350, 350)))
        self.assertTrue(self.update(3.999, (350, 350)))
        self.assertFalse(self.update(4., (350, 350)))
        self.assertIsNone(self.policy.hide_at)

    def test_return_to_bar_cancels_old_deadline(self):
        self.update(0., (350, 200));self.update(1., (350, 350))
        self.assertTrue(self.update(3., (350, 220)))
        self.assertIsNone(self.policy.hide_at)
        self.assertTrue(self.update(5., (350, 350)))
        self.assertTrue(self.update(7.999, (350, 350)))
        self.assertFalse(self.update(8., (350, 350)))

    def test_held_button_defers_hiding_until_three_seconds_after_release(self):
        self.update(0., (350, 200));self.update(1., (350, 350))
        self.assertTrue(self.update(8., (350, 350), buttons_down=True))
        self.assertTrue(self.update(9., (350, 350)))
        self.assertFalse(self.update(12., (350, 350)))

    def test_blocked_inactive_or_pointer_outside_owner_hides_immediately(self):
        for point, available in (((350, 201), False), ((99, 200), True),
                                 ((1060, 201), True), ((350, 740), True), (None, True)):
            with self.subTest(point=point, available=available):
                self.update(0., (350, 200))
                self.assertFalse(self.update(1., point, available=available))
                self.assertIsNone(self.policy.hide_at)

    def test_page_reset_discards_deadline_and_next_edge_can_reveal(self):
        self.update(0., (350, 200));self.update(1., (350, 350))
        self.policy.reset()
        self.assertFalse(self.update(2., (350, 220)))
        self.assertTrue(self.update(3., (350, 200)))

    def test_geometry_uses_current_owner_position_and_size(self):
        self.assertFalse(self.policy.update(1., (350, 201), (20, 40, 1280, 720), (20, 40, 1280, 46)))
        self.assertTrue(self.policy.update(2., (1270, 42), (20, 40, 1280, 720), (20, 40, 1280, 46)))

    def test_invalid_pointer_cannot_leave_toolbar_armed(self):
        for point in ((float('nan'), 200), (350, float('inf')), (), 'bad'):
            self.update(0., (350, 200))
            self.assertFalse(self.update(1., point))

    def test_invisible_toolbar_does_not_respond_to_held_button_off_hotzone(self):
        self.assertFalse(self.update(1., (350, 220), buttons_down=True))

    def test_rect_boundaries_are_half_open(self):
        self.assertTrue(inside((100, 200), self.owner))
        self.assertTrue(inside((1059, 739), self.owner))
        self.assertFalse(inside((1060, 739), self.owner))

    def test_embedded_toolbar_survives_parent_show_all_contract(self):
        bar = Mock()
        hide_embedded_toolbar(bar, True)
        bar.set_no_show_all.assert_called_once_with(True)
        bar.hide.assert_called_once_with()

    def test_standalone_toolbar_is_not_modified(self):
        bar = Mock()
        hide_embedded_toolbar(bar, False)
        self.assertFalse(bar.mock_calls)


if __name__ == '__main__':
    unittest.main()
