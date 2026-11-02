"""Actual reader/host routing into AI scroll state; no devices or API."""
import unittest
from unittest.mock import Mock,patch
from gamepad.device import EV_KEY,EV_ABS


class AIScrollInputTests(unittest.TestCase):
    def setUp(self):
        from test_ai_host import AIHostControllerTests
        self.harness=AIHostControllerTests('test_idle_pad_preserves_hand_selection_on_ai_page')
        self.harness.setUp()
        self.fixture=self.harness.fixture;self.view=self.harness.view
        self.host=self.fixture.host
        index=self.view.choices.index('ai_reveal')
        self.host.navigation.index=index;self.view.set_gamepad_focus(index)
        self.focus=index
    def tearDown(self):self.harness.tearDown()
    def tick(self,seconds=.016):
        self.fixture.now+=seconds
        self.assertTrue(self.host.tick());self.assertEqual(self.host.last_error,'')

    def test_held_shoulders_scroll_only_answer_keep_focus_and_never_submit(self):
        before=(self.view.choices,self.view.rectangles,self.host.navigation.page)
        self.fixture.report((EV_KEY,311,1))
        self.tick();first=self.view.scroll.target
        self.tick();self.assertGreater(self.view.scroll.target,first)
        self.assertFalse(self.view.scroll.follow)
        self.assertEqual(self.view.hover,self.focus);self.assertEqual(self.host.navigation.index,self.focus)
        self.assertEqual(before,(self.view.choices,self.view.rectangles,self.host.navigation.page))
        self.assertFalse(self.host.pointer.update.call_args.kwargs['enabled'])
        self.assertFalse(self.harness.calls);self.fixture.suite.ai_select.assert_not_called()
        self.assertTrue(self.host.gestures_blocked(self.fixture.now))

    def test_fast_tap_survives_peek_and_releases_without_more_scroll(self):
        self.fixture.report((EV_KEY,311,1));self.fixture.report((EV_KEY,311,0))
        self.assertTrue(self.host.gestures_blocked(self.fixture.now));self.tick()
        after=self.view.scroll.target;self.assertGreater(after,0)
        self.tick();self.assertEqual(after,self.view.scroll.target)

    def test_held_scroll_keeps_visible_focus_through_stale_pointer_timeout(self):
        from gi.repository import Gtk,GLib
        window=Gtk.Window();window.add(self.view);window.resize(960,540);window.show_all()
        try:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            self.fixture.report((EV_KEY,311,1))
            with patch('menu_view.time.monotonic',side_effect=lambda:self.fixture.now):
                self.view.set_gamepad_focus(self.focus)
                for _ in range(40):
                    self.tick(.04);self.view.animate(self.fixture.now)
                    self.assertEqual(self.view.hover,self.focus)
                    self.assertEqual(self.host.navigation.index,self.focus)
            self.assertFalse(self.harness.calls)
        finally:
            window.remove(self.view);window.destroy()

    def test_y_resumes_follow_once_without_submission(self):
        self.view.scroll_by(-28);self.assertFalse(self.view.scroll.follow)
        with patch.object(self.view,'scroll_follow',wraps=self.view.scroll_follow) as follow:
            self.fixture.report((EV_KEY,308,1));self.tick();self.tick()
            follow.assert_called_once()
        self.assertTrue(self.view.scroll.follow);self.assertEqual(self.view.scroll.target,self.view.scroll.limit)
        self.assertFalse(self.harness.calls);self.assertEqual(self.view.hover,self.focus)

    def test_scroll_without_visible_focus_never_restores_old_index(self):
        self.view.update_selection({});self.assertIsNone(self.view.hover)
        self.fixture.report((EV_KEY,311,1));self.tick()
        self.assertIsNone(self.view.hover);self.assertEqual(self.host.navigation.index,self.focus)
        self.fixture.report((EV_KEY,311,0),(EV_KEY,308,1));self.tick()
        self.assertIsNone(self.view.hover);self.assertEqual(self.host.navigation.index,self.focus)

    def test_b_precedes_scrolling_and_phone_never_scrolls(self):
        with patch.object(self.view,'controller_scroll',wraps=self.view.controller_scroll) as scroll:
            self.fixture.report((EV_KEY,311,1),(EV_KEY,305,1));self.tick()
            self.fixture.suite.navigate_back.assert_called_once();scroll.assert_not_called()
            self.fixture.report((EV_KEY,305,0));self.view.select('ai_phone');self.tick()
            scroll.assert_not_called()

    def test_disconnect_dialog_or_another_page_stops_ai_scroll(self):
        self.fixture.report((EV_KEY,311,1));self.tick()
        with patch.object(self.view,'controller_scroll',wraps=self.view.controller_scroll) as scroll:
            self.fixture.suite.menu_level='main';self.tick();scroll.assert_not_called()
            self.fixture.suite.menu_level='ai'
            dialog=Mock();self.host.dialog=lambda:dialog;self.tick();scroll.assert_not_called()
            dialog.gamepad.assert_called_once()
            self.host.dialog=lambda:None;self.fixture.reader.disconnect();self.tick();scroll.assert_not_called()

    def test_shoulders_do_not_disable_right_stick_pointer(self):
        self.fixture.report((EV_KEY,311,1),(EV_ABS,3,20000));self.tick()
        snapshot=self.host.pointer.update.call_args.args[0]
        self.assertGreater(snapshot['axes']['rx'],0)
        self.assertFalse(self.host.pointer.update.call_args.kwargs['enabled'])
        self.assertGreater(self.view.scroll.target,0)
        self.assertEqual(self.view.hover,self.focus)


if __name__=='__main__':unittest.main()
