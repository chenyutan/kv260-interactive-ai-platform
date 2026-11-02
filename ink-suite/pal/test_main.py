import os
import ast
import struct
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch,Mock
from pal.main import launch_spec,DirectedKeys,PalChild,video_size
from pal.controls import BodyKeys,PadKeys,BODY_HINT,PAD_HINT

class LaunchTests(unittest.TestCase):
    def test_environment_is_child_scoped_and_data_not_copied(self):
        old=dict(os.environ)
        cmd,env=launch_spec(Path('/private/pal'),Path('/private/saves'),777,Path('/tmp/control'),Path('/private/bridge.so'),(960,510))
        self.assertEqual(dict(os.environ),old)
        self.assertEqual(env['SDL_WINDOWID'],'777');self.assertEqual(env['LD_PRELOAD'],str(Path('/private/bridge.so')))
        self.assertEqual(cmd[cmd.index('--save-dir')+1],str(Path('/private/saves')))
        self.assertEqual(cmd[cmd.index('--data-dir')+1],str(Path('/private/pal/data')))
        self.assertIn(str(Path('/private/pal/runtime/lib')),env['LD_LIBRARY_PATH'])
        self.assertNotIn('--fullscreen',cmd)
    def test_no_audio_for_isolated_tests(self):
        cmd,env=launch_spec('/p','/s',1,'/c','/b',(640,400),True)
        self.assertIn('--no-audio',cmd);self.assertEqual(env['SDL_AUDIODRIVER'],'dummy')
    def test_invalid_xid_and_size_rejected(self):
        for xid,size in ((0,(640,400)),(1,(20,30)),(1,(9999,9999))):
            with self.assertRaises(ValueError):launch_spec('/p','/s',xid,'/c','/b',size)
    def test_key_targets_never_use_global_focus_or_xtest(self):
        text=Path(__file__).with_name('main.py').read_text(encoding='utf-8')
        self.assertNotIn('XSetInputFocus',text);self.assertNotIn('XTestFakeKeyEvent',text)
        self.assertNotIn('XSendEvent',text)
    def test_pause_bridge_never_stops_process(self):
        text=Path(__file__).with_name('pause_bridge.c').read_text()
        self.assertIn('SDL_PauseAudio',text);self.assertIn('paused_total',text)
        self.assertNotIn('SIGSTOP',text)
    def test_native_size_limits_include_fitted_2k_and_reject_non_integer(self):
        self.assertEqual(video_size(1920,1440),(1920,1440))
        self.assertEqual(video_size(3840,2160),(3840,2160))
        for size in ((319,240),(640,199),(3841,2160),(3840,2161),(640.0,480),(True,480)):
            with self.assertRaises(ValueError):video_size(*size)


class ResizeTests(unittest.TestCase):
    def test_resize_preserves_process_keys_audio_pause_and_saved_progress(self):
        child=PalChild(NS(),7,(320,240));child.control=bytearray(32)
        child.process=object();child.keys=object();process,keys=child.process,child.keys
        child.write_word(0,1);child.write_word(2,1);child.write_word(5,8)
        child.close=Mock();child.start=Mock()
        self.assertTrue(child.resize(672,504))
        self.assertEqual(child.word(3),(504<<16)|672)
        self.assertEqual((child.word(0),child.word(2),child.word(5)),(1,1,8))
        self.assertIs(child.process,process);self.assertIs(child.keys,keys)
        self.assertFalse(child.resize(672,504))
        child.resize(920,690)
        self.assertEqual(child.word(3),(690<<16)|920)
        child.close.assert_not_called();child.start.assert_not_called()
        previous=bytes(child.control)
        with self.assertRaises(ValueError):child.resize(10,10)
        self.assertEqual(bytes(child.control),previous)

class KeyLifetimeTests(unittest.TestCase):
    def keyer(self):
        x=DirectedKeys.__new__(DirectedKeys);x.held=set();x.expires={};x.display=1
        x.lib=type('Lib',(),{'XFlush':lambda *_:None})();x.events=[]
        x.send=lambda key,pressed:x.events.append((key,pressed));return x
    def test_pulse_releases_and_pause_releases_held_keys(self):
        x=self.keyer();x.update({'Left'},{'Return'},1)
        self.assertEqual(set(x.events),{('Left',True),('Return',True)})
        x.update({'Left'},now=1.2);self.assertEqual(x.events[-1],('Return',False))
        x.release();self.assertEqual(x.events[-1],('Left',False));self.assertEqual(x.held,set())
    def test_unknown_input_cannot_inject_arbitrary_key(self):
        x=self.keyer();x.update({'F12'},{'super'},1);self.assertEqual(x.events,[])


class ControllerPauseTests(unittest.TestCase):
    """Unchanged real Panel methods, inert private-bridge process and reader."""
    def panel(self):
        tree=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf8'))
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='Panel')
        node.bases=[]
        ns=dict(time=time,BodyKeys=BodyKeys,PadKeys=PadKeys,BODY_HINT=BODY_HINT,PAD_HINT=PAD_HINT,
                Gtk=NS(main_level=lambda:0),GLib=NS(source_remove=Mock(),timeout_add=Mock(return_value=44)),
                emit=Mock(),args=NS(offline_preview=False),PalChild=Mock(),video_size=video_size)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'pal/main.py','exec'),ns)
        p=ns['Panel'].__new__(ns['Panel']);p.stopped=False;p.active=True;p.ready=True
        p.control_mode='gamepad';p.gamepad_paused=True;p.entry_until=0.;p.generation=0;p.resize_timer=0
        p.pad=PadKeys();p.body=BodyKeys();p.latest=None;p.lock=threading.Lock();p.stop=threading.Event()
        p.hint=NS(set_text=Mock(),set_visible=Mock())
        p.reader=NS(snapshot=Mock(return_value=dict(connected=True)),close=Mock())
        p.child=NS(process=NS(poll=lambda:None),keys=NS(update=Mock(),release=Mock()),pause=Mock(),
                   started=0.,word=lambda _index:1,close=Mock())
        return p
    def test_realize_waits_for_final_allocation_and_shutdown_cancels_pending_work(self):
        p=self.panel();p.child=None
        namespace=p.sync_viewport.__globals__;child_factory=namespace['PalChild']
        p.canvas=NS(get_realized=lambda:True,get_allocated_width=lambda:672,get_allocated_height=lambda:504,
                    get_display=lambda:NS(sync=Mock()),get_window=lambda:NS(get_xid=lambda:7))
        p.realize(p.canvas);child_factory.assert_not_called()
        p.queue_viewport(p.canvas);namespace['GLib'].source_remove.assert_called_with(44)
        p.sync_viewport();child_factory.assert_called_once_with(namespace['args'],7,(672,504))
        p.child.start.assert_called_once_with(False)
        child=p.child;p.canvas.get_allocated_width=lambda:920;p.canvas.get_allocated_height=lambda:690
        p.sync_viewport();self.assertIs(p.child,child);child.resize.assert_called_once_with(920,690)
        p.resize_timer=44;p.operation('shutdown')
        self.assertEqual(p.resize_timer,0)
        self.assertFalse(p.sync_viewport());self.assertEqual(child_factory.call_count,1)
    def test_hidden_stack_bootstraps_until_host_can_show_game_then_resizes_same_child(self):
        p=self.panel();p.active=False;p.child=None
        factory=p.sync_viewport.__globals__['PalChild']
        p.canvas=NS(get_realized=lambda:True,get_allocated_width=lambda:1,get_allocated_height=lambda:1,
                    get_display=lambda:NS(sync=Mock()),get_window=lambda:NS(get_xid=lambda:7))
        p.sync_viewport();factory.assert_called_once_with(p.sync_viewport.__globals__['args'],7,(320,240))
        child=p.child;child.start.assert_called_once_with(False)
        p.canvas.get_allocated_width=lambda:679;p.canvas.get_allocated_height=lambda:509
        p.sync_viewport();child.resize.assert_called_once_with(679,509)
        p.canvas.get_allocated_width=lambda:1;p.canvas.get_allocated_height=lambda:1
        p.sync_viewport();self.assertEqual(child.resize.call_count,1);self.assertEqual(factory.call_count,1)
    def test_healthy_play_hides_footer_and_reconnection_errors_remain_visible(self):
        p=self.panel();p.tick()
        p.hint.set_text.assert_called_with('');p.hint.set_visible.assert_called_with(False)
        p.reader.snapshot.return_value=dict(connected=False);p.tick()
        self.assertIn('手柄未连接',p.hint.set_text.call_args.args[0])
        p.hint.set_visible.assert_called_with(True)
        p.reader.snapshot.return_value=dict(connected=True);p.tick()
        p.hint.set_text.assert_called_with('');p.hint.set_visible.assert_called_with(False)

    def test_disconnect_freezes_child_until_connected_neutral(self):
        p=self.panel();p.tick();self.assertFalse(p.gamepad_paused)
        p.child.pause.assert_called_once_with(False)
        p.reader.snapshot.return_value=dict(connected=False)
        p.tick();p.tick()
        self.assertTrue(p.gamepad_paused);self.assertEqual(p.child.pause.call_count,2)
        self.assertIn('手柄未连接',p.hint.set_text.call_args.args[0])
        p.child.keys.update.assert_called_with(set(),set(),unittest.mock.ANY)
        p.reader.snapshot.return_value=dict(connected=True,axes={'lx':1})
        p.tick();self.assertTrue(p.gamepad_paused)
        p.reader.snapshot.return_value=dict(connected=True)
        p.tick();self.assertFalse(p.gamepad_paused);self.assertEqual(p.child.pause.call_count,3)
    def test_host_pause_cannot_be_undone_by_reconnect_while_menu_owns_input(self):
        p=self.panel();p.tick();p.operation('pause')
        self.assertFalse(p.active);self.assertTrue(p.gamepad_paused)
        p.reader.snapshot.reset_mock();p.child.pause.reset_mock()
        p.reader.snapshot.return_value=dict(connected=True)
        p.tick();p.tick()
        p.reader.snapshot.assert_not_called();p.child.pause.assert_not_called()
        self.assertTrue(p.gamepad_paused)
    def test_host_resume_still_requires_release_and_preserves_short_press_afterward(self):
        p=self.panel();p.tick();p.operation('pause');p.operation('resume')
        self.assertTrue(p.active);self.assertTrue(p.gamepad_paused)
        p.reader.snapshot.return_value=dict(connected=True,buttons={'A'},pressed={'A'})
        p.tick();self.assertTrue(p.gamepad_paused)
        p.reader.snapshot.return_value=dict(connected=True)
        p.tick();self.assertFalse(p.gamepad_paused)
        p.reader.snapshot.return_value=dict(connected=True,pressed={'Y'})
        p.tick();self.assertEqual(p.child.keys.update.call_args.args[1],{'Escape'})
    def test_body_mode_keeps_original_host_pause_behavior(self):
        p=self.panel();p.control_mode='body';p.operation('pause');p.operation('resume')
        self.assertEqual([c.args[0] for c in p.child.pause.call_args_list],[True,False])
    def test_shutdown_closes_reader_and_child_once(self):
        p=self.panel();reader=p.reader;p.operation('shutdown');p.operation('shutdown')
        reader.close.assert_called_once();p.child.close.assert_called_once()
        self.assertFalse(p.tick())

if __name__=='__main__':unittest.main()
