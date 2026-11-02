"""Run in private Xvfb at 960x540 and 800x450; never accesses real devices."""
import time
import unittest

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from connectivity.view import ConnectionCenter
    HAS_GTK = True
except ImportError:
    HAS_GTK = False


@unittest.skipUnless(HAS_GTK, 'Native GTK is required for low-resolution window QA')
class ConnectivityLayoutTests(unittest.TestCase):
    def setUp(self):
        ready, _ = Gtk.init_check()
        if not ready: self.skipTest('Run using a private Xvfb display')
        self.calls = []
        self.parent = Gtk.Window();self.parent.set_default_size(640, 400)
        self.parent.show_all()
        self.view = ConnectionCenter(self.parent, self.operation,
                                     lambda key: None, lambda: None)

    def operation(self, op, **data):
        self.calls.append((op, data));return 42

    def tearDown(self):
        self.view.destroy();self.parent.destroy();self.drain()

    def drain(self):
        for _ in range(40):
            while Gtk.events_pending(): Gtk.main_iteration_do(False)
            time.sleep(.003)

    def assert_fits(self):
        self.drain()
        screen = self.view.get_screen()
        area = screen.get_monitor_workarea(screen.get_primary_monitor())
        width, height = self.view.get_size()
        self.assertLessEqual(width, area.width)
        self.assertLessEqual(height, area.height)
        self.assertLessEqual(width, 940)
        self.assertLessEqual(height, 510)

    def test_keyboard_scroll_and_device_pages_fit_current_lowres_screen(self):
        self.view.open();self.assert_fits()
        self.view.tabs.set_current_page(1)
        self.view.toggle_keyboard();self.assert_fits()
        self.assertTrue(self.view.keyboard.get_visible())
        self.assertIsInstance(self.view.tabs.get_nth_page(1), Gtk.ScrolledWindow)
        self.view.toggle_keyboard();self.assert_fits()
        self.view.tabs.set_current_page(0);self.assert_fits()
        self.assertIsInstance(self.view.tabs.get_nth_page(0), Gtk.ScrolledWindow)

    def test_right_hand_dwell_uses_actual_resized_window_coordinates(self):
        self.view.open();self.view.tabs.set_current_page(1);self.assert_fits()
        x, y = self.view.scan.translate_coordinates(self.view, 0, 0)
        rect = self.view.scan.get_allocation();width, height = self.view.get_size()
        point = ((x + rect.width / 2) / width, (y + rect.height / 2) / height)
        self.view.update_hand(dict(cursor=point), ('synthetic', 1), now=10.)
        self.view.update_hand(dict(cursor=point), ('synthetic', 2), now=10.2)
        self.view.hand_since = 8.
        self.view.update_hand(dict(cursor=point), ('synthetic', 3), now=10.3)
        self.assertEqual(self.calls, [('scan', {})])


if __name__ == '__main__': unittest.main()
