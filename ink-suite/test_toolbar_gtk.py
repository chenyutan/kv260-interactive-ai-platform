"""Private-display GTK/XEmbed toolbar smoke checks, no camera or audio.

Run with xvfb-run on the board. Windows CI without GTK skips this native gate;
test_toolbar_autohide covers the timing policy independently.
"""
import os
import time
import unittest

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk, GLib
    GTK_AVAILABLE = bool(os.environ.get('DISPLAY'))
except ImportError:
    GTK_AVAILABLE = False

from toolbar_autohide import MouseToolbar, hide_embedded_toolbar


@unittest.skipUnless(GTK_AVAILABLE, 'GTK native tests need a private X11 display')
class ToolbarGtkTests(unittest.TestCase):
    def drain(self):
        until = time.monotonic() + .06
        context = GLib.MainContext.default()
        while context.pending() and time.monotonic() < until:
            context.iteration(False)

    def setUp(self):
        self.now = 100.
        self.pointer = ((-10000, -10000), False)
        self.blocked = False
        self.owner = Gtk.Window()
        self.owner.set_default_size(960, 540)
        self.socket = Gtk.Socket()
        self.socket.set_hexpand(True);self.socket.set_vexpand(True)
        self.socket.connect('plug-removed', lambda *_: True)
        self.owner.add(self.socket);self.owner.show_all();self.drain()
        self.plug = Gtk.Plug.new(self.socket.get_id())
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True);self.canvas.set_vexpand(True)
        self.canvas.connect('draw', self.draw_blue)
        self.canvas_draws = 0
        self.canvas.connect_after('draw', self.canvas_painted)
        self.plug.add(self.canvas);self.plug.show_all();self.drain()
        self.wait_frame(self.canvas,lambda:self.canvas_draws,'Synthetic XEmbed canvas')
        self.bar = Gtk.Box(spacing=10)
        for label in ('体验菜单', '设备', '退出'):
            self.bar.pack_start(Gtk.Button(label=label), False, False, 0)
        self.toolbar = MouseToolbar(self.owner, self.bar, lambda: self.blocked,
            clock=lambda: self.now, pointer_provider=lambda: self.pointer,
            active_provider=lambda: True)
        self.focus_out = []
        self.plug.connect('focus-out-event', lambda *_: self.focus_out.append(True))
        self.toolbar_draws = 0
        self.bar.connect_after('draw', self.toolbar_painted)
        self.drain()

    def toolbar_painted(self,*_):
        self.toolbar_draws += 1
        return False

    def canvas_painted(self,*_):
        self.canvas_draws += 1
        return False

    def wait_frame(self,widget,drawn,description):
        # drain() only handles events which are already pending; GTK's next
        # frame-clock tick can still be in the future. A mapped popup with a
        # size request is not evidence that its pixels have reached X11.
        clock = widget.get_frame_clock()
        self.assertIsNotNone(clock,description+' has no GTK frame clock')
        completed = []
        def after_paint(*_):
            if drawn():completed.append(True)
        handler = clock.connect('after-paint',after_paint)
        widget.queue_draw()
        context = GLib.MainContext.default();deadline = time.monotonic()+1.
        try:
            while time.monotonic()<deadline and not completed:
                while context.pending():context.iteration(False)
                if not completed:time.sleep(.002)
            self.assertTrue(completed,description+' failed to complete a real GTK paint frame')
            self.owner.get_display().sync()
        finally:clock.disconnect(handler)

    @staticmethod
    def draw_blue(_, cr):
        cr.set_source_rgb(.05, .15, .85);cr.paint();return False

    def tearDown(self):
        self.toolbar.close();self.plug.destroy();self.owner.destroy();self.drain()

    def at(self, x, y, now=None, pressed=False):
        left, top, _, _ = self.toolbar._owner_rect()
        self.pointer = ((left + x, top + y), pressed)
        if now is not None:self.now = now
        self.toolbar.poll();self.drain()

    def test_show_hide_keeps_socket_and_canvas_allocation_constant(self):
        before = (self.socket.get_allocated_width(), self.socket.get_allocated_height(),
                  self.canvas.get_allocated_width(), self.canvas.get_allocated_height())
        self.assertEqual(before, (960, 540, 960, 540))
        for index in range(30):
            self.at(300, 1, 100. + index * 5)
            self.assertTrue(self.toolbar.popup.get_visible())
            self.at(300, 200, 100. + index * 5 + .1)
            self.at(300, 200, 100. + index * 5 + 3.2)
            self.assertFalse(self.toolbar.popup.get_visible())
            self.assertEqual(before, (self.socket.get_allocated_width(), self.socket.get_allocated_height(),
                  self.canvas.get_allocated_width(), self.canvas.get_allocated_height()))
        self.assertFalse(self.focus_out, 'A hover popup stole XEmbed keyboard focus')

    def test_popup_is_no_focus_and_native_window_is_above_embedded_canvas(self):
        self.at(300, 1)
        self.wait_frame(self.toolbar.popup,lambda:self.toolbar_draws,'Mouse toolbar')
        self.assertFalse(self.toolbar.popup.get_accept_focus())
        self.assertFalse(self.toolbar.popup.get_focus_on_map())
        self.assertTrue(all(not child.get_can_focus() for child in self.bar.get_children()))
        left, top, _, _ = self.toolbar._owner_rect()
        window=self.toolbar.popup.get_window()
        self.assertEqual(tuple(window.get_origin()[-2:]),(left,top))
        self.assertEqual((window.get_width(),window.get_height()),(960,46))
        image = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), left, top, 960, 100)
        self.assertIsNotNone(image)
        pixels = image.get_pixels();channels = image.get_n_channels();stride = image.get_rowstride()
        offset = 20 * stride + 930 * channels
        r, g, b = pixels[offset:offset + 3]
        self.assertLess(abs(b - r), 90, 'The blue XEmbed child painted over the toolbar')

    def test_dialog_blocks_popup_and_unblocking_requires_edge(self):
        self.at(300, 1)
        self.blocked = True;self.toolbar.poll();self.drain()
        self.assertFalse(self.toolbar.popup.get_visible())
        self.blocked = False;self.at(300, 20)
        self.assertFalse(self.toolbar.popup.get_visible())
        self.at(300, 1);self.assertTrue(self.toolbar.popup.get_visible())

    def test_real_gdk_pointer_path_reveals_popup_without_timer_error(self):
        self.toolbar.pointer_provider = None
        device=self.owner.get_display().get_default_seat().get_pointer()
        left,top,_,_=self.toolbar._owner_rect()
        device.warp(self.owner.get_screen(),left+300,top+1)
        self.owner.get_display().sync()
        point,buttons=self.toolbar._pointer()
        self.assertEqual(point,(left+300,top+1));self.assertIsInstance(buttons,bool)
        self.assertTrue(self.toolbar.poll());self.assertTrue(self.toolbar.popup.get_visible())
        self.wait_frame(self.toolbar.popup,lambda:self.toolbar_draws,'Real-pointer mouse toolbar')
        self.assertFalse(self.focus_out,'The real-pointer popup stole XEmbed keyboard focus')

    def test_embedded_bar_stays_hidden_after_plug_show_all(self):
        bar = Gtk.Box();bar.pack_start(Gtk.Button(label='旧工具条'), False, False, 0)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.pack_start(bar, False, False, 0)
        hide_embedded_toolbar(bar, True)
        layout.show_all()
        self.assertFalse(bar.get_visible())
        standalone = Gtk.Box();hide_embedded_toolbar(standalone, False);standalone.show_all()
        self.assertTrue(standalone.get_visible())
        layout.destroy();standalone.destroy()

    def test_close_removes_timer_and_repeated_close_is_safe(self):
        self.toolbar.close();self.toolbar.close()
        self.assertIsNone(self.toolbar.timer)
        self.assertFalse(self.toolbar.poll())


if __name__ == '__main__':
    unittest.main()
