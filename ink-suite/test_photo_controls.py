"""Native GTK controller checks. Run under Xvfb; never opens the camera."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from PIL import Image

from photo_navigation import DirectionRepeat, PhotoControls, PhotoFileChooser
from photo_studio import PhotoStudio


def drain(seconds=.02):
    until = time.monotonic()+seconds
    while time.monotonic()<until:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(.002)


def ready(predicate):
    until = time.monotonic()+3
    while time.monotonic()<until:
        drain()
        if predicate():
            return
    raise AssertionError('native widget did not become ready')


def state(key=None, axes=None):
    return dict(pressed=() if key is None else (key,), buttons=(), released=(), axes=axes or {})


def press(target, key):
    target.gamepad(state())
    target.gamepad(state(key))
    drain()


class PhotoControllerTests(unittest.TestCase):
    def setUp(self):
        self.window = Gtk.Window()
        self.window.set_decorated(False)
        self.window.set_default_size(960,540)
        self.exits=[]
        self.panel=PhotoStudio(lambda:self.exits.append(True))
        self.window.add(self.panel)
        self.window.show_all()
        drain()

    def tearDown(self):
        self.panel.close()
        self.window.destroy()
        drain()

    def test_combobox_direction_and_confirm_change_actual_values(self):
        nav=self.panel.pad_controls
        nav.focus(self.panel.style)
        press(self.panel,'RIGHT')
        self.assertEqual(self.panel.style.get_active_id(),'ink')
        press(self.panel,'LEFT')
        self.assertEqual(self.panel.style.get_active_id(),'color')
        press(self.panel,'A')
        self.assertEqual(self.panel.style.get_active_id(),'ink')
        self.assertFalse(self.panel.style.get_property('popup-shown'))

    def test_history_actual_selection_without_popup(self):
        h=self.panel.history
        h.append('one','作品一');h.append('two','作品二');h.set_active(0);h.set_sensitive(True)
        self.panel.pad_controls.focus(h)
        press(self.panel,'RIGHT')
        self.assertEqual(h.get_active_id(),'two')
        press(self.panel,'A')
        self.assertEqual(h.get_active_id(),'one')
        self.assertFalse(h.get_property('popup-shown'))

    def test_disabled_actions_are_skipped_and_visual_focus_present(self):
        self.panel.pad_controls.focus(self.panel.style)
        press(self.panel,'DOWN')
        self.assertIs(self.panel.pad_controls.current,self.panel.actions[-1])
        self.assertTrue(self.panel.actions[-1].get_style_context().has_class('photo-pad-focus'))
        self.assertFalse(self.panel.style.get_style_context().has_class('photo-pad-focus'))

    def test_original_toggle_and_back_button(self):
        self.panel.original.set_sensitive(True)
        self.panel.pad_controls.focus(self.panel.original)
        press(self.panel,'A');self.assertTrue(self.panel.original.get_active())
        press(self.panel,'A');self.assertFalse(self.panel.original.get_active())
        self.panel.pad_controls.focus(self.panel.actions[-1])
        press(self.panel,'A');self.assertEqual(len(self.exits),1)
        press(self.panel,'B');self.assertEqual(len(self.exits),2)

    def test_generate_is_clicked_once_per_edge(self):
        clicks=[]
        self.panel.generate.connect('clicked',lambda *_:clicks.append(True))
        self.panel.generate.set_sensitive(True)
        self.panel.pad_controls.focus(self.panel.generate)
        press(self.panel,'A')
        self.panel.gamepad(dict(buttons=('A',),pressed=(),released=(),axes={}))
        self.assertEqual(clicks,[True])

    def test_picker_open_cancel_returns_to_visible_pick_focus(self):
        self.panel.pad_controls.focus(self.panel.pick)
        press(self.panel,'A')
        self.assertIsInstance(self.panel.dialog,PhotoFileChooser)
        press(self.panel.dialog,'B')
        self.assertIsNone(self.panel.dialog)
        self.assertIs(self.panel.pad_controls.current,self.panel.pick)

    def test_layout_fits_960_540_and_help_is_beside_preview(self):
        # Include the QR area present on a connected LAN.
        from gi.repository import GdkPixbuf
        pix=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,112,112)
        pix.fill(0xffffffff);self.panel.qr.set_from_pixbuf(pix)
        self.panel.network.set_text('手机与板卡连接同一个 Wi-Fi。\n扫码后在“照片水墨”上传。')
        drain(.25)
        self.assertEqual((self.window.get_allocated_width(),self.window.get_allocated_height()),(960,540))
        for control in self.panel.actions+(self.panel.pad_help,self.panel.status,self.panel.qr):
            x,y=control.translate_coordinates(self.window,0,0)
            self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+control.get_allocated_width(),960)
            self.assertLessEqual(y+control.get_allocated_height(),540)
        self.assertGreater(self.panel.canvas.get_allocated_height(),150)
        self.assertIn('R3',self.panel.pad_help.get_text())


class DirectionTests(unittest.TestCase):
    def test_analog_initial_repeat_and_neutral_rearm(self):
        repeat=DirectionRepeat()
        s=state(axes={'ly':.9})
        self.assertEqual(repeat.update(s,0),'DOWN')
        self.assertIsNone(repeat.update(s,.1))
        self.assertEqual(repeat.update(s,.35),'DOWN')
        self.assertIsNone(repeat.update(state(),.36))
        self.assertEqual(repeat.update(s,.37),'DOWN')


class NativeFileChooserTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'folder').mkdir()
        Image.new('RGB',(3,2),'green').save(self.root/'folder'/'sample.png')
        Image.new('RGB',(3,2),'red').save(self.root/'direct.jpg')
        (self.root/'ignore.txt').write_text('not an image')
        self.dialog=PhotoFileChooser(initial=self.root)
        self.responses=[]
        self.dialog.connect('response',lambda _,value:self.responses.append(value))
        ready(lambda:len(self.dialog.paths)==2)

    def tearDown(self):
        self.dialog.destroy();drain();self.temp.cleanup()

    def test_directory_then_photo_by_A(self):
        self.assertEqual(self.dialog.listbox.get_selected_row().photo_path.name,'folder')
        press(self.dialog,'A')
        ready(lambda:self.dialog.paths==[self.root/'folder'/'sample.png'])
        press(self.dialog,'A')
        self.assertEqual(self.responses,[Gtk.ResponseType.OK])
        self.assertEqual(self.dialog.get_filename(),str(self.root/'folder'/'sample.png'))

    def test_file_selection_then_confirm_button(self):
        press(self.dialog,'DOWN')
        self.assertEqual(self.dialog.get_filename(),str(self.root/'direct.jpg'))
        press(self.dialog,'RIGHT')
        self.assertIs(self.dialog.focus_area,self.dialog.cancel)
        press(self.dialog,'RIGHT')
        self.assertIs(self.dialog.focus_area,self.dialog.accept)
        press(self.dialog,'A')
        self.assertEqual(self.responses,[Gtk.ResponseType.OK])

    def test_cancel_button_and_B(self):
        press(self.dialog,'RIGHT')
        self.assertIs(self.dialog.focus_area,self.dialog.cancel)
        press(self.dialog,'A')
        press(self.dialog,'B')
        self.assertEqual(self.responses,[Gtk.ResponseType.CANCEL]*2)

    def test_up_button_and_LB(self):
        press(self.dialog,'A')
        ready(lambda:self.dialog.folder==self.root/'folder' and len(self.dialog.paths)==1)
        press(self.dialog,'LB')
        ready(lambda:self.dialog.folder==self.root and len(self.dialog.paths)==2)
        self.dialog.set_area(self.dialog.up)
        press(self.dialog,'A')
        ready(lambda:self.dialog.folder==self.root.parent)

    def test_initial_scan_and_delayed_gtk_focus_do_not_redirect_first_A(self):
        import threading
        from gi.repository import GLib
        import photo_navigation
        gate=threading.Event();real_scandir=photo_navigation.os.scandir
        def delayed_scandir(path):
            if not gate.wait(2):raise AssertionError('test scan gate was not released')
            return real_scandir(path)
        with patch('photo_navigation.os.scandir',side_effect=delayed_scandir):
            dialog=PhotoFileChooser(initial=self.root)
            try:
                # Simulate late X11 automatic initial focus before row delivery.
                dialog.up.grab_focus();drain(.025)
                self.assertIs(dialog.focus_area,dialog.listbox)
                gate.set()
                ready(lambda:len(dialog.paths)==2)
                # And the same automatic event after the async list has loaded.
                GLib.timeout_add(12,lambda:dialog.up.grab_focus() or False)
                drain(.06)
                self.assertIs(dialog.focus_area,dialog.listbox)
                self.assertIs(dialog.get_focus(),dialog.listbox.get_selected_row())
                press(dialog,'A')
                ready(lambda:dialog.folder==self.root/'folder' and len(dialog.paths)==1)
            finally:
                gate.set();dialog.destroy();drain()

    def test_explicit_toolbar_selection_is_preserved_during_async_scan(self):
        import threading
        import photo_navigation
        gate=threading.Event();real_scandir=photo_navigation.os.scandir
        def delayed_scandir(path):
            if not gate.wait(2):raise AssertionError('test scan gate was not released')
            return real_scandir(path)
        with patch('photo_navigation.os.scandir',side_effect=delayed_scandir):
            dialog=PhotoFileChooser(initial=self.root)
            try:
                # Explicit hand-pad navigation still wins while loading.
                press(dialog,'LEFT')
                self.assertIs(dialog.focus_area,dialog.location)
                gate.set();ready(lambda:len(dialog.paths)==2)
                self.assertIs(dialog.focus_area,dialog.location)
                # A real pointer selection uses the same explicit ownership.
                dialog._mouse_focus(dialog.up)
                drain(.04)
                self.assertIs(dialog.focus_area,dialog.up)
            finally:
                gate.set();dialog.destroy();drain()

    def test_locations_are_reachable_and_change(self):
        self.dialog.set_area(self.dialog.location)
        press(self.dialog,'DOWN')
        first=self.dialog.location.get_active()
        self.assertGreaterEqual(first,0)
        press(self.dialog,'A')
        self.assertEqual(self.dialog.location.get_active(),(first+1)%len(self.dialog.locations))
        self.assertEqual(self.dialog.folder,self.dialog.locations[(first+1)%len(self.dialog.locations)][1])

    def test_set_filename_compatibility_and_nonphoto_rejection(self):
        self.assertTrue(self.dialog.set_filename(self.root/'folder'/'sample.png'))
        ready(lambda:self.dialog.filename==self.root/'folder'/'sample.png')
        self.assertFalse(self.dialog.set_filename(self.root/'ignore.txt'))

    def test_analog_selects_and_empty_folder_cannot_confirm(self):
        self.dialog.gamepad(state(axes={'ly':.9}));drain()
        self.assertEqual(self.dialog.filename,self.root/'direct.jpg')
        empty=self.root/'empty';empty.mkdir()
        self.dialog.open_folder(empty)
        ready(lambda:not self.dialog.listbox.get_children())
        press(self.dialog,'A')
        self.assertFalse(self.responses)
        self.assertFalse(self.dialog.accept.is_sensitive())

    def test_dialog_content_and_buttons_fit_display(self):
        drain(.1)
        width,height=self.dialog.get_size()
        self.assertLessEqual(width,960);self.assertLessEqual(height,480)
        self.assertTrue(self.dialog.up.get_visible())
        self.assertTrue(self.dialog.location.get_visible())
        self.assertTrue(self.dialog.cancel.get_visible())
        self.assertTrue(self.dialog.accept.get_visible())

    def test_keyboard_tab_enter_and_escape_follow_explicit_selection(self):
        from gi.repository import Gdk
        def key(value):
            event=Gdk.Event.new(Gdk.EventType.KEY_PRESS);event.keyval=value;event.state=0
            self.dialog.emit('key-press-event',event);drain()
        key(Gdk.KEY_Tab)
        self.assertIs(self.dialog.focus_area,self.dialog.cancel)
        key(Gdk.KEY_Return)
        self.assertEqual(self.responses,[Gtk.ResponseType.CANCEL])
        key(Gdk.KEY_Escape)
        self.assertEqual(self.responses,[Gtk.ResponseType.CANCEL]*2)

    def test_refresh_recovers_missing_directory_after_it_appears(self):
        path=self.root/'reconnected-usb'
        self.dialog.open_folder(path)
        ready(lambda:'无法读取' in self.dialog.message.get_text())
        self.assertFalse(self.dialog.accept.is_sensitive())
        path.mkdir();Image.new('RGB',(4,3),'red').save(path/'new.png')
        press(self.dialog,'RB')
        ready(lambda:self.dialog.filename==path/'new.png')
        self.assertTrue(self.dialog.accept.is_sensitive())

    def test_row_selection_and_confirmation_do_not_stat_usb_on_gtk_thread(self):
        with patch.object(Path,'is_file',side_effect=AssertionError('GUI filesystem stat')),\
             patch.object(Path,'is_dir',side_effect=AssertionError('GUI filesystem stat')):
            press(self.dialog,'DOWN')
            self.assertEqual(self.dialog.filename,self.root/'direct.jpg')
            press(self.dialog,'A')
        self.assertEqual(self.responses,[Gtk.ResponseType.OK])


class HostDispatchTests(unittest.TestCase):
    def host(self, dialog, snapshot, consumed=False):
        from gamepad.host import HostControls
        h=HostControls.__new__(HostControls)
        h.closed=False;h.last=time.monotonic();h.navigation_until=0
        h.suite=SimpleNamespace(stop=SimpleNamespace(is_set=lambda:False),menu_level='photo')
        h.reader=SimpleNamespace(snapshot=lambda:snapshot)
        h.pointer=SimpleNamespace(update=lambda *a,**k:{'back_release_consumed':consumed})
        h.dialog=lambda:dialog
        return h

    def test_host_routes_real_chooser_actions_instead_of_widget_activate(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog=PhotoFileChooser(initial=temp)
            result=[];dialog.connect('response',lambda _,value:result.append(value))
            host=self.host(dialog,dict(state('B'),connected=True))
            self.assertTrue(host.tick())
            self.assertEqual(result,[Gtk.ResponseType.CANCEL])
            dialog.destroy();drain()

    def test_pointer_back_R3_does_not_cancel_photo_dialog(self):
        dialog=SimpleNamespace(gamepad=Mock())
        host=self.host(dialog,dict(state(),connected=True,released=('BACK',)),consumed=True)
        self.assertTrue(host.tick())
        snapshot=dialog.gamepad.call_args.args[0]
        self.assertNotIn('BACK',snapshot['released'])


if __name__=='__main__':
    unittest.main(verbosity=2)
