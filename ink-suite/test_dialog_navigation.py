"""Real GTK dialog navigation regressions; Xvfb only, no camera/devices."""
import time
import unittest
import gi

gi.require_version('Gtk','3.0')
from gi.repository import Gtk
from gamepad.dialog_navigation import DialogNavigation
from connectivity.view import ConnectionCenter


def pump(seconds=.05):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        time.sleep(.001)


def pad(pressed=(),buttons=(),released=(),axes=None,connected=True):
    return dict(connected=connected,pressed=pressed,buttons=buttons,released=released,axes=axes or {})


class NativeDialogTests(unittest.TestCase):
    def setUp(self):
        self.windows=[];self.nav=DialogNavigation();self.now=100.
    def tearDown(self):
        for w in reversed(self.windows):w.destroy()
        pump()
    def row(self):
        w=Gtk.Window();box=Gtk.Box();w.add(box);self.windows.append(w)
        events=[];buttons=[]
        for name in ('first','hidden','disabled','last'):
            b=Gtk.Button(label=name);b.connect('clicked',lambda _,n=name:events.append(n));box.pack_start(b,False,False,0);buttons.append(b)
        w.show_all();buttons[1].hide();buttons[2].set_sensitive(False);pump()
        return w,buttons,events
    def tick(self,w,**kwargs):
        return self.nav.update(w,pad(**kwargs),self.now)
    def test_A_assigns_native_initial_focus_before_activation(self):
        w,b,e=self.row();w.set_focus(None)
        result=self.tick(w,pressed=('A',));pump(.3)
        self.assertEqual(w.get_focus(),b[0]);self.assertEqual(e,['first']);self.assertEqual(result['action'],'activate')
    def test_left_stick_moves_and_skips_hidden_disabled_buttons(self):
        w,b,_=self.row();b[0].grab_focus()
        result=self.tick(w,axes={'lx':.8})
        self.assertTrue(result['moved']);self.assertEqual(w.get_focus(),b[3])
    def test_held_direction_repeats_without_new_press_edges(self):
        w,b,_=self.row();b[0].grab_focus()
        self.tick(w,buttons=('RIGHT',));self.assertEqual(w.get_focus(),b[3])
        self.now+=.1;self.assertFalse(self.tick(w,buttons=('RIGHT',))['handled']);self.assertEqual(w.get_focus(),b[3])
        self.now+=.25;self.assertTrue(self.tick(w,buttons=('RIGHT',))['handled']);self.assertEqual(w.get_focus(),b[0])
    def test_direction_release_and_repress_is_immediate(self):
        w,b,_=self.row();b[0].grab_focus();self.tick(w,buttons=('RIGHT',))
        self.now+=.01;self.tick(w)
        self.now+=.01;self.assertTrue(self.tick(w,pressed=('LEFT',))['handled']);self.assertEqual(w.get_focus(),b[0])
    def test_disconnected_and_unmapped_dialog_have_no_effects(self):
        w,b,e=self.row();b[0].grab_focus();self.assertFalse(self.tick(w,pressed=('A',),connected=False)['handled']);self.assertFalse(e)
        w.hide();pump();self.assertFalse(self.tick(w,pressed=('A',))['handled']);self.assertFalse(e)
    def test_disabled_ancestor_default_button_is_never_activated(self):
        w=Gtk.Window();self.windows.append(w);column=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);w.add(column)
        disabled_group=Gtk.Box();old=Gtk.Button(label='old default');disabled_group.add(old);column.add(disabled_group)
        other=Gtk.Button(label='enabled');column.add(other);events=[]
        old.connect('clicked',lambda *_:events.append('old'));other.connect('clicked',lambda *_:events.append('enabled'))
        w.show_all();old.set_can_default(True);w.set_default(old);old.grab_focus();pump()
        disabled_group.set_sensitive(False);pump()
        # GTK removes invalid focus but deliberately retains default_widget.
        # Widget.activate() can emit clicked programmatically even though the
        # parent's effective sensitivity is false, so the helper must filter it.
        self.assertTrue(old.get_sensitive());self.assertFalse(old.is_sensitive())
        self.assertIs(w.get_default_widget(),old);self.assertFalse(DialogNavigation._eligible(old))
        self.tick(w,pressed=('A',));pump(.3)
        self.assertEqual(events,['enabled']);self.assertIs(w.get_focus(),other)
    def test_Back_R3_combo_does_not_also_close(self):
        w,_,_=self.row();self.nav.update(w,pad(released=('BACK',)),self.now,back_release_consumed=True);self.assertTrue(w.get_visible())
    def test_MessageDialog_B_returns_cancel_response(self):
        w=Gtk.MessageDialog(buttons=Gtk.ButtonsType.OK_CANCEL,text='test');self.windows.append(w);responses=[]
        w.connect('response',lambda _,r:responses.append(r));w.show_all();pump();self.tick(w,pressed=('B',))
        self.assertEqual(responses,[Gtk.ResponseType.CANCEL])
    def test_ConnectionCenter_B_uses_semantic_hide_and_can_reopen(self):
        parent=Gtk.Window();self.windows.append(parent);parent.show_all();events=[]
        w=ConnectionCenter(parent,lambda *a,**kw:None,lambda _:None,lambda:events.append('closed'));self.windows.append(w)
        destroyed=[];w.connect('destroy',lambda *_:destroyed.append(True));w.open();pump()
        self.tick(w,pressed=('B',));pump();self.assertFalse(w.get_visible());self.assertEqual(events,['closed']);self.assertFalse(destroyed)
        w.open();pump();self.assertTrue(w.get_visible());self.assertFalse(destroyed)
    def test_existing_ConnectionCenter_close_is_also_reusable(self):
        parent=Gtk.Window();self.windows.append(parent);parent.show_all();events=[]
        w=ConnectionCenter(parent,lambda *a,**kw:None,lambda _:None,lambda:events.append('closed'));self.windows.append(w)
        destroyed=[];w.connect('destroy',lambda *_:destroyed.append(True));w.open();pump();w.close();pump()
        self.assertFalse(w.get_visible());self.assertFalse(destroyed);self.assertEqual(events,['closed'])
        w.open();pump();self.assertTrue(w.get_visible())
    def test_notebook_inactive_page_cannot_receive_A(self):
        w=Gtk.Window();self.windows.append(w);n=Gtk.Notebook();w.add(n);events=[]
        a=Gtk.Button(label='page1');b=Gtk.Button(label='page2');n.append_page(a,Gtk.Label(label='one'));n.append_page(b,Gtk.Label(label='two'))
        a.connect('clicked',lambda *_:events.append('one'));b.connect('clicked',lambda *_:events.append('two'));w.show_all();n.set_current_page(1);pump();w.set_focus(a)
        self.tick(w,pressed=('A',));self.assertNotIn('one',events)


    def native_host(self,snapshot,consumed=False):
        # Construct the actual tick/router without opening evdev or XTest.
        # These tests intentionally retain real GTK discovery and focus.
        import threading
        from types import SimpleNamespace
        from gamepad.host import HostControls
        from gamepad.navigation import Navigation
        s=Gtk.Window();self.windows.append(s);s.show_all();s.stop=threading.Event()
        s.control_mode='body';s.menu_open=True;s.menu_level='main'
        h=HostControls.__new__(HostControls);h.suite=s;h.reader=SimpleNamespace(snapshot=lambda:snapshot)
        h.pointer=SimpleNamespace(update=lambda *a,**kw:dict(back_release_consumed=consumed))
        h.navigation=Navigation();h.dialog_navigation=DialogNavigation();h.closed=False
        h.last=time.monotonic();h.navigation_until=0.;h.connected=False;h.sequence=0
        return h,s
    def test_HostControls_tick_routes_left_stick_then_A_to_real_dialog(self):
        snapshot=pad(axes={'lx':.8});host,parent=self.native_host(snapshot)
        w,b,events=self.row();w.set_transient_for(parent);b[0].grab_focus();pump()
        self.assertIs(host.dialog(),w)
        self.assertIs(w.get_focus(),b[0]);self.assertTrue(w.get_mapped());self.assertTrue(host.tick());self.assertIs(w.get_focus(),b[3])
        snapshot.clear();snapshot.update(pad(pressed=('A',)))
        host.tick();pump(.3)
        self.assertEqual(events,['last'])
    def test_HostControls_tick_respects_pointer_consumed_BACK_release(self):
        snapshot=pad(released=('BACK',));host,parent=self.native_host(snapshot,consumed=True)
        w=Gtk.MessageDialog(transient_for=parent,buttons=Gtk.ButtonsType.OK_CANCEL,text='test')
        self.windows.append(w);responses=[];w.connect('response',lambda _,r:responses.append(r));w.show_all();pump()
        self.assertIs(host.dialog(),w);host.tick();pump(.2)
        self.assertEqual(responses,[]);self.assertTrue(w.get_visible())


if __name__=='__main__':unittest.main(verbosity=2)
