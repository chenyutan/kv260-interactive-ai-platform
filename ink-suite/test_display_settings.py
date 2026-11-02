"""Private Xvfb UI tests; mode commands are entirely injected fakes."""
from pathlib import Path
import ast
import io
import json
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import display_modes
from test_display_modes import FakeXRandR

try:
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk
    from display_settings import DisplaySettings
    HAS_GTK=True
except ImportError:HAS_GTK=False


@unittest.skipUnless(HAS_GTK,'GTK is required; use a private Xvfb display')
class DisplaySettingsTests(unittest.TestCase):
    def setUp(self):
        ready,_=Gtk.init_check()
        if not ready:self.skipTest('Private Xvfb display is required')
        self.temp=tempfile.TemporaryDirectory();self.calls=[];self.closed=[];self.windows=[]
    def tearDown(self):
        for window in self.windows:window.close()
        self.wait(lambda:all(w.close_delivered for w in self.windows))
        self.temp.cleanup()
    def wait(self,condition,timeout=3.):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            while Gtk.events_pending():Gtk.main_iteration_do(False)
            if condition():return True
            time.sleep(.003)
        self.assertTrue(condition(),'GTK callback did not complete')
    def make(self,duration=5.,slow=False):
        def trial(size,state_dir):
            self.calls.append(('try',size))
            if slow:time.sleep(.15)
            return dict(token='a'*16,transaction=str(Path(state_dir)/'fake.json'),
                        confirm_before_monotonic=time.monotonic()+duration)
        def confirm(token,state_dir):self.calls.append(('confirm',token));return dict(saved=True)
        def restore(path):self.calls.append(('restore',path));return dict(status='rolled_back')
        window=DisplaySettings(closed=lambda:self.closed.append(True),state_dir=self.temp.name,
            inspect_fn=lambda:dict(outputs=[dict(name='DP-1',current_mode='1280x720',current_rate='60.00')]),
            trial_fn=trial,confirm_fn=confirm,restore_fn=restore)
        self.windows.append(window);self.wait(lambda:not window.pending);return window
    def test_trial_requires_user_click_then_keep_and_fits_current_screen(self):
        window=self.make();self.assertEqual(self.calls,[])
        self.assertFalse(window.keep_button.get_sensitive())
        window.try_button.clicked();self.wait(lambda:window.attempt is not None and not window.pending)
        self.assertEqual(self.calls,[('try','960x540')])
        self.assertTrue(window.keep_button.get_sensitive())
        width,height=window.get_size();screen=window.get_screen()
        area=screen.get_monitor_workarea(screen.get_primary_monitor())
        self.assertLessEqual(width,area.width);self.assertLessEqual(height,area.height)
        window.keep_button.clicked();self.wait(lambda:not window.pending)
        self.assertEqual(self.calls[-1],('confirm','a'*16));self.assertIsNone(window.attempt)
        window.close();self.wait(lambda:window.close_delivered)
        self.assertFalse(any(c[0]=='restore' for c in self.calls))
    def test_timeout_rolls_back_never_confirms(self):
        window=self.make(duration=.15);window.try_button.clicked()
        self.wait(lambda:len(self.calls)>1 and not window.pending)
        self.assertEqual([c[0] for c in self.calls],['try','restore'])
        self.assertIsNone(window.attempt)
    def test_hide_or_close_rolls_back_pending_trial(self):
        for hide in (True,False):
            window=self.make();window.try_button.clicked()
            self.wait(lambda:window.attempt is not None and not window.pending)
            window.hide() if hide else window.close()
            self.wait(lambda:window.close_delivered)
            self.assertEqual(self.calls[-1][0],'restore')
    def test_close_while_switch_worker_running_rolls_back_without_gui_trial_callback(self):
        window=self.make(slow=True);window.try_button.clicked();window.close()
        self.wait(lambda:window.close_delivered)
        self.assertEqual([c[0] for c in self.calls],['try','restore'])
        self.assertFalse(any(c[0]=='confirm' for c in self.calls))


class SavedDisplayFirstFrameTests(unittest.TestCase):
    """Exercise the actual host callback without a camera or native mode change."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.runner=FakeXRandR();self.now=100.;self.acknowledged=[]
        module=ast.parse((Path(__file__).parent/'main.py').read_text(encoding='utf-8'))
        suite=next(node for node in module.body if isinstance(node,ast.ClassDef) and node.name=='Suite')
        method=next(node for node in suite.body if isinstance(node,ast.FunctionDef) and node.name=='complete_saved_display')
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        namespace=dict(threading=SimpleNamespace(Thread=ImmediateThread),sys=sys,
                       Gdk=SimpleNamespace(WindowState=SimpleNamespace(FULLSCREEN=1)))
        exec(compile(ast.Module(body=[method],type_ignores=[]),'host-first-frame','exec'),namespace)
        self.callback=namespace['complete_saved_display']
    def tearDown(self):self.temp.cleanup()
    def guardian(self,path):
        display_modes.atomic_json(path.with_suffix('.ready.json'),dict(ready=True,pid=98765))
        return 98765
    def trial(self):
        return display_modes.trial('960x540',self.directory,self.runner,self.guardian,lambda:self.now)
    def saved_startup(self):
        first=self.trial()
        display_modes.confirm(first['token'],self.directory,self.runner,lambda:self.now)
        self.runner.current='1280x720'
        return display_modes.apply_confirmed(self.directory,self.runner,self.guardian,lambda:self.now)
    def run_callback(self,attempt,*,fullscreen=False,size=(1024,600),mapped=True):
        app=SimpleNamespace(saved_display_trial=attempt,get_mapped=lambda:mapped,
            get_window=lambda:SimpleNamespace(get_state=lambda:int(fullscreen)),get_size=lambda:size)
        original=display_modes.complete_confirmed_startup
        def complete(token):
            self.acknowledged.append(token)
            return original(token,self.directory,self.runner,lambda:self.now)
        with patch.object(display_modes,'complete_confirmed_startup',complete),patch('sys.stderr',io.StringIO()):
            self.assertFalse(self.callback(app))
        return app
    def test_windowed_first_mapped_frame_confirms_real_output_not_window_dimensions(self):
        attempt=self.saved_startup();app=self.run_callback(attempt)
        self.assertIsNone(app.saved_display_trial)
        self.assertEqual(self.acknowledged,[attempt['token']])
        self.assertEqual(json.loads(Path(attempt['transaction']).read_text())['status'],'confirmed_startup')
    def test_fullscreen_waits_until_resize_and_then_completes(self):
        attempt=self.saved_startup();app=self.run_callback(attempt,fullscreen=True,size=(1280,720))
        self.assertIs(app.saved_display_trial,attempt);self.assertEqual(self.acknowledged,[])
        self.assertEqual(json.loads(Path(attempt['transaction']).read_text())['status'],'pending')
        self.run_callback(attempt,fullscreen=True,size=(960,540))
        self.assertEqual(self.acknowledged,[attempt['token']])
        self.assertEqual(json.loads(Path(attempt['transaction']).read_text())['status'],'confirmed_startup')
    def test_unmapped_frame_never_completes(self):
        attempt=self.saved_startup();app=self.run_callback(attempt,mapped=False)
        self.assertIs(app.saved_display_trial,attempt);self.assertEqual(self.acknowledged,[])
        self.assertEqual(json.loads(Path(attempt['transaction']).read_text())['status'],'pending')
    def test_new_trial_cannot_be_confirmed_by_host_frame_callback(self):
        attempt=self.trial();self.run_callback(attempt)
        self.assertEqual(json.loads(Path(attempt['transaction']).read_text())['status'],'pending')
        self.assertFalse((self.directory/'confirmed.json').exists())


if __name__=='__main__':unittest.main()
