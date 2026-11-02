"""Controller/gesture ownership against the real Suite AST, without GTK or IO."""
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

import test_action_host as host_fixture


class ConnectionCenter:
    """Stateful stand-in for the connection center's public hand API."""
    def __init__(self):
        self.hand_target = object()
        self.hand_since = 98.1
        self.hand_key = ('old', 1)
        self.hand_time = 100.
        self.pointer = (.5, .5)
        self.hand_progress = .95
        self.reset_hand = Mock(side_effect=self._reset_hand)
        self.update_hand = Mock()

    def get_visible(self):
        return True

    def _reset_hand(self):
        self.hand_target = self.hand_key = self.hand_time = self.pointer = None
        self.hand_since = self.hand_progress = 0.


class GesturePriorityTests(unittest.TestCase):
    def setUp(self):
        fixture = host_fixture.ActionHostTests()
        fixture.setUp()
        self.s, self.clock = fixture.s, fixture.clock
        s = self.s
        s.lock = threading.Lock()
        s.pending_state = False
        s.control_mode = 'body'
        s.menu_level = 'main'
        s.serial = 5
        s.health_active = False
        s.health_choices = None
        s.device_view = s.device_dialog = s.display_dialog = None
        s.pipeline.update = Mock()
        s.sync_gamepad_backend = Mock()
        s.sync_health_voice = Mock()
        s.send_child = Mock()
        self.pad = NS(connected=True, blocked=False)
        self.pad.gestures_blocked = lambda now: self.pad.blocked
        s.gamepad_host = self.pad
        for view in (s.menu, s.actions):
            view.update_selection = Mock(wraps=view.update_selection)
        s.health_view = NS(
            receive=Mock(), pause=Mock(), action=Mock(), update_selection=Mock(),
            gesture_navigation=True, choices=('health_pause',),
            rectangles=((.05, .05, .95, .95),), hit_index=lambda x, y: 0)
        self.sequence = 0

    def state(self, **suite_changes):
        self.sequence += 1
        suite = dict(active=True, menu=self.s.menu_open, mode='fight',
                     input_mode='body', open_serial=5, control_active=False)
        suite.update(suite_changes)
        return dict(pid=123, sequence=self.sequence,
                    timestamp_seconds=self.clock.now,
                    points17=host_fixture.pose(.5, .5), suite=suite)

    def deliver(self, **suite_changes):
        self.s.latest = self.state(**suite_changes)
        self.assertFalse(self.s.deliver_state())
        return self.s.latest

    def dwell_to_1_9_seconds(self):
        """Use fresh observations to accumulate a real, almost complete dwell."""
        s = self.s
        s.selector.set_choices(('choose_target',), ((.05, .05, .95, .95),))
        s.selector.open(None, 100., host_fixture.pose(.2, .5))
        for tenth in range(6):
            self.clock.now = 100. + tenth / 10
            self.deliver()
            if s.selector.since is not None:
                break
        self.assertIsNotNone(s.selector.since)
        # Keep every observation fresh and stop before the selector completes.
        target = s.selector.since + 1.9
        while self.clock.now < target:
            self.clock.now = min(target, self.clock.now + .1)
            self.deliver()
        self.assertAlmostEqual(s.menu.selection['progress'], .95)
        s.select.assert_not_called()

    def test_pad_cancels_1_9_second_dwell_and_requires_fresh_movement(self):
        self.dwell_to_1_9_seconds()
        s = self.s
        self.pad.blocked = True
        self.clock.now += .04
        self.deliver()
        self.assertIsNone(s.selector.opened)
        self.assertIsNone(s.selector.since)
        self.assertFalse(s.selector.armed)
        self.assertEqual(s.menu.selection, {})
        s.select.assert_not_called()

        self.pad.blocked = False
        for _ in range(25):
            self.clock.now += .1
            self.deliver()
        # A stationary hand cannot inherit either the old arming or dwell time.
        self.assertFalse(s.selector.armed)
        self.assertEqual(s.menu.selection['progress'], 0.)
        s.select.assert_not_called()

    def test_idle_connected_pad_preserves_body_dwell_and_selection(self):
        self.dwell_to_1_9_seconds()
        self.clock.now += .11
        self.deliver()
        self.s.select.assert_called_once_with('choose_target')
        self.assertFalse(self.s.gesture_navigation_suppressed)
        self.assertEqual(self.s.control_mode, 'body')

    def test_blocked_health_keeps_pose_and_voice_but_never_navigates(self):
        s = self.s
        s.health_active = True
        s.menu_level = 'health'
        s.selector.update = Mock(wraps=s.selector.update)
        self.pad.blocked = True
        state = self.deliver(menu=True)
        s.health_view.receive.assert_called_once_with(state)
        s.sync_health_voice.assert_called_once()
        s.selector.update.assert_not_called()
        s.health_view.action.assert_not_called()
        s.health_view.update_selection.assert_called_once_with({})
        self.assertIsNone(s.selector.opened)
        self.assertIsNone(s.health_choices)

    def test_health_connection_center_clears_old_dwell_without_hand_updates(self):
        s = self.s
        s.health_active = True
        s.menu_level = 'health'
        s.device_view = ConnectionCenter()
        self.pad.blocked = True
        state = self.deliver(menu=True)
        s.device_view.reset_hand.assert_called_once()
        s.device_view.update_hand.assert_not_called()
        self.assertIsNone(s.device_view.hand_target)
        self.assertIsNone(s.device_view.pointer)
        self.assertEqual(s.device_view.hand_progress, 0.)
        s.health_view.receive.assert_called_once_with(state)
        s.health_view.pause.assert_called_once()
        s.health_view.action.assert_not_called()

    def test_device_warning_and_connection_center_reset_without_gesture_click(self):
        s = self.s
        s.device_view = ConnectionCenter()
        s.device_dialog = NS(hand_target=object(), hand_since=98.1,
                             hand_key=('old', 1), hand_time=100.)
        s.device_dialog_hand = Mock()
        self.pad.blocked = True
        self.deliver()
        s.device_view.reset_hand.assert_called_once()
        s.device_view.update_hand.assert_not_called()
        s.device_dialog_hand.assert_not_called()
        for field in ('hand_target', 'hand_key', 'hand_time'):
            self.assertIsNone(getattr(s.device_dialog, field))
        self.assertEqual(s.device_dialog.hand_since, 0.)

    def test_consumed_circle_serial_never_replays_after_pad_release(self):
        s = self.s
        s.menu_open = False
        s.open_menu = Mock()
        self.pad.blocked = True
        self.deliver(menu=True, open_serial=12, control_active=True)
        self.assertEqual(s.serial, 12)
        s.open_menu.assert_not_called()

        self.pad.blocked = False
        self.clock.now += 4.
        self.deliver(menu=True, open_serial=12, control_active=True)
        s.open_menu.assert_not_called()
        # Subsequent genuine body requests remain usable.
        self.clock.now += .1
        state = self.deliver(menu=True, open_serial=13)
        s.open_menu.assert_called_once_with(None, state['points17'], from_backend=True)

    def test_repeated_blocked_state_preserves_new_controller_focus(self):
        s = self.s
        self.pad.blocked = True
        self.deliver()
        s.menu.update_selection.assert_called_once_with({})
        focus = dict(cursor=(.75, .5), hover=2, progress=0.,
                     sample_key=('gamepad', 10))
        s.menu.update_selection(focus)
        s.menu.update_selection.reset_mock()
        for _ in range(3):
            self.clock.now += .04
            self.assertFalse(s.deliver_state())
        s.menu.update_selection.assert_not_called()
        self.assertEqual(s.menu.selection, focus)

    def test_pad_navigation_does_not_change_running_body_game_mode(self):
        s = self.s
        s.menu_open = False
        self.pad.blocked = True
        state = self.deliver(menu=False)
        self.assertEqual(s.control_mode, 'body')
        s.pipeline.update.assert_called_once_with(state, self.clock.now)
        s.sync_gamepad_backend.assert_not_called()
        s.send_child.assert_called_once_with('resume')


if __name__ == '__main__':
    unittest.main()
