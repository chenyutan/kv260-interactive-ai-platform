"""Menu ownership must discard body motion without advancing the fight."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from embedded_runner import Controller


class BodyOwnership(unittest.TestCase):
    def controller(self,old=False):
        c=Controller.__new__(Controller)
        c.args=SimpleNamespace(mode='fight',offline_preview=False)
        c.closed=c.paused=False;c.before=None
        c.app=SimpleNamespace(game=SimpleNamespace(state='playing',time=12.5),
            cancel_input=Mock(),gesture=SimpleNamespace(reset=Mock()),
            manual_guard=True,keys={'a'},tracking_pause=True,tick=Mock(),reader=None)
        if not old:c.app.body_control=SimpleNamespace(reset=Mock())
        return c
    @patch('embedded_runner.emit')
    def test_pause_then_resume_discards_two_body_histories(self,emit):
        c=self.controller();c.pause()
        self.assertEqual(c.app.game.state,'paused')
        self.assertEqual(c.app.game.time,12.5)
        self.assertFalse(c.app.keys);self.assertFalse(c.app.manual_guard)
        c.app.body_control.reset.assert_called_once()
        c.resume()
        self.assertEqual(c.app.game.state,'playing')
        self.assertEqual(c.app.game.time,12.5)
        self.assertEqual(c.app.body_control.reset.call_count,2)
        self.assertEqual(c.app.cancel_input.call_count,2)
    @patch('embedded_runner.emit')
    def test_repeated_same_pause_does_not_rearm_game(self,emit):
        c=self.controller();c.pause();c.pause()
        c.app.body_control.reset.assert_called_once()
        self.assertEqual(c.app.game.time,12.5)
    def test_late_focus_event_clears_motion_without_repausing(self):
        c=self.controller();c.focus_out()
        self.assertEqual(c.app.game.state,'playing')
        self.assertFalse(c.paused)
        c.app.cancel_input.assert_called_once()
        c.app.body_control.reset.assert_called_once()
    @patch('embedded_runner.emit')
    def test_old_release_can_pause_and_resume(self,emit):
        c=self.controller(old=True);c.pause();c.resume();c.focus_out()
        self.assertEqual(c.app.game.state,'playing')
    def test_closed_controller_does_not_consume_input(self):
        c=self.controller();c.closed=True;c.focus_out()
        c.app.body_control.reset.assert_not_called()


if __name__=='__main__':unittest.main()
