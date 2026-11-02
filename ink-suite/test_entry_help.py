"""Entry ownership and explicit consent to start, with real host methods."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from entry_help import briefing
import test_action_host as host_fixture

class EntryHelpTests(unittest.TestCase):
    def setUp(self):
        fixture=host_fixture.ActionHostTests();fixture.setUp();self.s=fixture.s;s=self.s
        s.__test_globals__['briefing']=briefing
        s.actions.set_briefing=Mock();s.actions.choices=('intro_start','intro_back')
        s.control_mode='body';s.control_target='platformer';s.control_choice=None;s.control_previous_mode='body'
        s.menu_level='control';s.action_page='control_choice';s.control_return_main=True
        s.gamepad_host=NS(connected=True);s.visitor_busy=False
        s.launch_selected=Mock()
    def test_selecting_control_does_not_start_until_confirmed(self):
        self.s.choose_control('control_gamepad')
        self.assertEqual(self.s.action_page,'entry_help');self.assertEqual(self.s.control_mode,'gamepad')
        self.s.launch_selected.assert_not_called()
        self.assertEqual(self.s.selector.choices,('intro_start','intro_back'))
        self.s.choose_control('intro_start')
        self.s.launch_selected.assert_called_once_with('platformer')
        self.assertIsNone(self.s.control_target)
    def test_start_cannot_skip_control_page_and_invalid_buttons_do_not_launch(self):
        self.s.choose_control('intro_start');self.s.launch_selected.assert_not_called()
        self.s.choose_control('control_body');self.s.choose_control('control_gamepad')
        self.assertEqual(self.s.control_choice,'body');self.s.launch_selected.assert_not_called()
    def test_device_disconnection_during_reading_keeps_help_and_paused_game(self):
        self.s.choose_control('control_gamepad');self.s.gamepad_host.connected=False
        self.s.choose_control('intro_start');self.s.launch_selected.assert_not_called()
        self.assertEqual(self.s.action_page,'entry_help');self.assertTrue(self.s.menu_open)
        self.assertIn('手柄未连接',self.s.actions.text)
    def test_cancel_keeps_previous_control_and_never_resumes_previous_round(self):
        self.s.choose_control('control_gamepad');self.s.cancel_control_choice()
        self.assertEqual(self.s.control_mode,'body');self.s.launch_selected.assert_not_called()
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))
    def test_help_back_reopens_control_choices_and_keeps_original_return_destination(self):
        self.s.choose_control('control_gamepad');self.s.choose_control('intro_back')
        self.assertEqual(self.s.action_page,'control_choice');self.assertEqual(self.s.control_mode,'body')
        self.assertTrue(self.s.control_return_main);self.assertEqual(self.s.control_target,'platformer')
    def test_entry_help_contains_only_known_modes_and_four_readable_sections(self):
        for mode in ('ink','fight','fruit','sword','taiko','platformer','runner','pal','kart'):
            for control in ('body','gamepad'):
                info=briefing(mode,control)
                self.assertEqual(len(info['lines']),4)
                self.assertTrue(all(isinstance(s,str) and 0<len(s)<160 for s in info['lines']))
        for bad in (('unknown','body'),('pal','keyboard')):
            with self.assertRaises(ValueError):briefing(*bad)
    def test_backend_recovery_does_not_consume_pending_entry_or_back_choice(self):
        self.s.choose_control('control_body');self.s.launch_selected.return_value=False
        self.s.choose_control('intro_start')
        self.assertEqual(self.s.control_target,'platformer');self.assertEqual(self.s.control_choice,'body')
        self.s.launch_selected.return_value=True;self.s.choose_control('intro_start')
        self.assertEqual(self.s.launch_selected.call_count,2);self.assertIsNone(self.s.control_target)

if __name__=='__main__':unittest.main()
