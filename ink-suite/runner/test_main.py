"""Headless lifecycle tests: real simulation/actions, forbidden network access.

Native GTK and physical output are validated separately in an isolated Xvfb.
These tests cannot obtain camera images, live pose packets or speaker access.
"""
from collections import deque
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parent
SUITE=HERE.parent
if not (SUITE/'action_dispatch.py').exists():
    SUITE=HERE.parents[1]/'integration'


class Widget:
    def __init__(self,*args,**kwargs):
        self.queue_draw=Mock();self.connect=Mock()
    def __getattr__(self,name):
        if name=='get_allocated_width':return lambda:960
        if name=='get_allocated_height':return lambda:540
        return Mock()


class FakeSound:
    def __init__(self,enabled=True):
        self.enabled=enabled;self.director=SimpleNamespace(status=Mock(return_value={'test':True}))
        self.set_playing=Mock();self.restart=Mock();self.close=Mock();self.events=Mock()
        self.handle=Mock(return_value=True)


def load_main():
    gtk=SimpleNamespace(Box=Widget,DrawingArea=Widget,Orientation=SimpleNamespace(VERTICAL=1),
                        main_level=lambda:1,main_quit=Mock())
    gdk=SimpleNamespace(EventMask=SimpleNamespace(BUTTON_PRESS_MASK=1),
        **{'KEY_'+name:i+1 for i,name in enumerate(('Escape','Menu','F1','Left','Right','space','Up','Down','Return'))})
    glib=SimpleNamespace(timeout_add_seconds=Mock(return_value=1),idle_add=Mock(),source_remove=Mock(),PRIORITY_DEFAULT=0)
    gi=ModuleType('gi');gi.require_version=Mock()
    repository=ModuleType('gi.repository');repository.Gtk=gtk;repository.Gdk=gdk;repository.GLib=glib
    platform=ModuleType('platformer.main')
    platform.configure_native_canvas=Mock(return_value={})
    platform.configure_game_priority=Mock(return_value=dict(success=True,before=0,after=-5,changed=True))
    platform.start_canvas_ticks=Mock(return_value={'kind':'mock','source':1})
    platform.stop_canvas_ticks=Mock()
    render=ModuleType('runner.native_render')
    render.Renderer=Mock(return_value=SimpleNamespace(render=Mock(),release_native_output=Mock(),
        metrics=Mock(return_value={}),event=Mock(),update_effects=Mock(),is_native=False,
        attach_window=Mock(return_value=False),resize=Mock(return_value=False)))
    audio=ModuleType('runner.audio');audio.RunnerSound=FakeSound
    metrics=ModuleType('sword.metrics');metrics.LatestMetricsWriter=Mock()
    actions_spec=importlib.util.spec_from_file_location('_runner_test_dispatch',SUITE/'action_dispatch.py')
    actions=importlib.util.module_from_spec(actions_spec);actions_spec.loader.exec_module(actions)
    modules={'gi':gi,'gi.repository':repository,'platformer.main':platform,
             'runner.native_render':render,'runner.audio':audio,'sword.metrics':metrics,'action_dispatch':actions}
    old_path=list(sys.path)
    try:
        sys.path.insert(0,str(HERE.parent))
        with patch.dict(sys.modules,modules),patch.dict('os.environ'):
            spec=importlib.util.spec_from_file_location('_runner_headless_main',HERE/'main.py')
            result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
            return result
    finally:sys.path[:]=old_path


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.ui=load_main();self.now=100.
        self.clock=patch.object(self.ui.time,'monotonic',side_effect=lambda:self.now)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.network=patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Forbidden test network'))
        self.open=self.network.start();self.addCleanup(self.network.stop)
        args=SimpleNamespace(embed_socket=None,offline_preview=True,seed=4,entry_delay=0.,metrics=None,benchmark=0)
        self.panel=self.ui.RunnerPanel(args)
        self.addCleanup(lambda:self.panel.operation('shutdown'))

    def action(self,action,command_id):
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.panel.handle_action(dict(op='action',action=action,command_id=command_id))
        packets=[json.loads(line) for line in stream.getvalue().splitlines() if line.startswith('{')]
        results=[p for p in packets if p.get('event')=='action_result']
        self.assertTrue(results,'Every accepted/rejected command needs an acknowledgement')
        return results[-1]

    def test_offline_preview_never_starts_pose_thread(self):
        self.assertIsNone(self.panel.poll_thread);self.assertIsNone(self.panel.reporter)
        self.assertFalse(self.panel.sound.enabled)
        self.now+=.02;self.panel.tick();self.open.assert_not_called()

    def test_start_restart_requires_pause_and_ack_uses_runner_mode(self):
        rejected=self.action('start','live-start')
        self.assertFalse(rejected['ok']);self.assertEqual(rejected['reason'],'experience_not_paused')
        self.panel.operation('pause')
        accepted=self.action('start','paused-start')
        self.assertTrue(accepted['ok']);self.assertEqual(accepted['mode'],'runner')
        self.assertEqual(self.panel.game.state,'paused')
        self.panel.operation('resume');self.assertEqual(self.panel.game.state,'running')

    def test_terminal_restart_and_resume_twice(self):
        for n in range(2):
            self.panel.game.restart();self.panel.game.state='over';self.panel.game.score=100+n
            self.panel.operation('pause')
            result=self.action('restart','over-restart-'+str(n))
            self.assertTrue(result['ok']);self.assertEqual(self.panel.game.state,'paused')
            self.assertFalse(self.panel.active);self.assertEqual(self.panel.game.score,0)
            self.panel.operation('resume');self.assertEqual(self.panel.game.state,'running')
            self.now+=.05;self.panel.tick();self.assertGreater(self.panel.game.elapsed,0)

    def test_duplicate_command_replays_ack_without_second_restart(self):
        self.panel.operation('pause')
        self.action('restart','idempotent')
        self.panel.game.score=777
        result=self.action('restart','idempotent')
        self.assertTrue(result['ok']);self.assertEqual(self.panel.game.score,777)

    def test_pause_discards_keyboard_commands_and_preserves_clock(self):
        self.panel.game.start();self.now+=.1;self.panel.tick()
        elapsed=self.panel.game.elapsed;self.panel.pending=[('jump',True)]
        self.panel.operation('pause');self.assertFalse(self.panel.pending)
        self.now+=10.;self.panel.tick();self.assertEqual(self.panel.game.elapsed,elapsed)
        self.panel.operation('resume');self.now+=.02;self.panel.tick()
        self.assertLess(self.panel.game.elapsed-elapsed,.04)
        self.assertFalse(self.panel.game.jumping)

    def test_stalled_source_only_freezes_after_source_grace_period(self):
        self.panel.args.offline_preview=False;self.panel.game.start()
        self.panel.game._next_wave_distance=1e9
        self.panel.tracker.update=Mock(return_value=dict(valid=False,reason='test_missing'))
        self.now+=.5;self.panel.tick();self.assertFalse(self.panel.tracking_paused)
        self.now+=1.;self.panel.tick();self.assertFalse(self.panel.tracking_paused)
        elapsed=self.panel.game.elapsed
        self.now+=1.1;self.panel.tick();self.assertTrue(self.panel.tracking_paused)
        self.assertEqual(self.panel.game.elapsed,elapsed)
        self.panel.latest=dict(pid=123,sequence=1,timestamp_seconds=self.now)
        self.panel.tracker.update.return_value=dict(valid=True)
        self.now+=.02;self.panel.tick();self.assertFalse(self.panel.tracking_paused)
        self.open.assert_not_called()

    def test_fresh_frames_without_any_skeleton_keep_running_and_music(self):
        self.panel.args.offline_preview=False;self.panel.game.start();self.panel.game._next_wave_distance=1e9
        self.panel.tracker.update=Mock(return_value=dict(valid=False,reason='missing_hips'))
        for sequence in range(1,101):
            self.now+=.1
            self.panel.latest=dict(pid=123,sequence=sequence,timestamp_seconds=self.now,points17=None)
            self.panel.tick();self.assertFalse(self.panel.tracking_paused)
        self.assertGreater(self.panel.game.elapsed,9.)
        self.panel.sound.set_playing.assert_called_with(True)
        self.assertFalse(self.panel.game.jumping);self.assertEqual(self.panel.game.player_lane,1)

    def test_gamepad_running_does_not_need_camera_source(self):
        self.panel.args.offline_preview=False;self.panel.game.start();self.panel.game._next_wave_distance=1e9
        self.panel.gamepad=SimpleNamespace(runner=Mock(return_value=dict(valid=True)),close=Mock(),reset=Mock())
        for _ in range(100):
            self.now+=.1;self.panel.tick();self.assertFalse(self.panel.tracking_paused)
        self.assertGreater(self.panel.game.elapsed,9.)
        self.panel.gamepad.runner.return_value=dict(valid=False,reason='controller_disconnected')
        self.now+=.1;self.panel.tick();self.assertTrue(self.panel.tracking_paused)

    def test_running_phase_uses_body_motion_with_no_hand_jump(self):
        from .test_core import pose
        self.panel.args.offline_preview=False;self.panel.game.start()
        self.panel.game._next_wave_distance=1e9
        for sequence in range(1,10):
            self.now+=.05
            self.panel.latest=pose(self.now,sequence,left_y=.18,right_y=.18)
            self.panel.tick()
        self.assertFalse(self.panel.game.jumping)
        self.now+=.05
        self.panel.latest=pose(self.now,10,body_down=-.045,left_y=.18,right_y=.18)
        self.panel.tick()
        self.assertTrue(self.panel.game.jumping)
        self.assertTrue(self.panel.control['jump'])
        self.assertFalse(self.panel.control['confirm'])
        self.open.assert_not_called()

    def test_legacy_tracking_metadata_does_not_block_body_controls(self):
        from .test_core import pose
        self.panel.args.offline_preview=False;self.panel.game.start()
        self.panel.game._next_wave_distance=1e9
        for sequence in range(1,10):
            self.now+=.05;self.panel.latest=pose(self.now,sequence);self.panel.tick()
        self.now+=.05
        p=pose(self.now,10,body_down=-.05,body_x=-.08)
        p['player_tracking']=dict(identity='distractor',epoch=2,locked=True,active=True,input_allowed=False)
        self.panel.latest=p;self.panel.tick()
        self.assertTrue(self.panel.control['valid'])
        self.assertTrue(self.panel.control['jump'])
        # The side zone now needs several fresh observations; person-lock
        # metadata must not suppress the jump or its confirmed lane target.
        self.assertEqual(self.panel.game.player_lane,1)
        for sequence in (11,12):
            self.now+=.05
            p=pose(self.now,sequence,body_down=-.05,body_x=-.08)
            p['player_tracking']=dict(identity='distractor',epoch=2,locked=True,active=True,input_allowed=False)
            self.panel.latest=p;self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,2)
        self.assertTrue(self.panel.game.jumping)
        self.open.assert_not_called()

    def test_arrow_fallback_is_not_shadowed_by_a_body_lane_target(self):
        self.panel.args.offline_preview=False;self.panel.game.start()
        self.panel.game._next_wave_distance=1e9
        self.panel.tracker.update=Mock(side_effect=lambda *_a,**_k:dict(valid=True,lane_target=2,move=0))
        self.now+=.05;self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,2)
        self.panel.down.add(self.ui.Gdk.KEY_Left);self.panel.pending.append(('move',-1))
        self.now+=.05;self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,1)
        self.now+=.05;self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,1)
        self.panel.down.clear();self.now+=.05;self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,2)

    def test_circle_candidate_preserves_torso_controls_but_open_menu_blocks(self):
        self.panel.args.offline_preview=False;self.panel.game.start()
        self.panel.game._next_wave_distance=1e9
        self.panel.tracker.update=Mock(return_value=dict(valid=True,move=1))
        self.panel.tracker.reset=Mock()
        self.now+=.05
        self.panel.latest=dict(pid=123,sequence=1,timestamp_seconds=self.now,
                               suite=dict(menu=False,control_active=True))
        self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,2)
        self.panel.tracker.reset.assert_not_called()
        self.now+=.05
        self.panel.latest=dict(pid=123,sequence=2,timestamp_seconds=self.now,
                               suite=dict(menu=True,control_active=True))
        self.panel.tracker.update.return_value=dict(valid=True,move=-1,jump=True)
        self.panel.tick()
        self.assertEqual(self.panel.game.player_lane,2)
        self.assertFalse(self.panel.game.jumping)
        self.assertEqual(self.panel.control['reason'],'menu_gesture')
        self.panel.tracker.reset.assert_called_once()

    def test_shutdown_cleanup_is_idempotent(self):
        self.panel.operation('shutdown');self.panel.operation('shutdown')
        self.panel.sound.close.assert_called_once();self.ui.stop_canvas_ticks.assert_called_once()
        self.assertTrue(self.panel.stop.is_set());self.assertFalse(self.panel.active)
        self.panel.renderer.release_native_output.assert_called_once()

    def test_direct_egl_disables_gtk_backbuffer_but_cairo_restores_it(self):
        self.panel.canvas.set_double_buffered=Mock()
        self.panel.renderer.is_native=True
        self.panel.renderer.metrics.return_value={'accelerated':True}
        self.panel.apply_renderer_presentation(self.panel.canvas)
        self.panel.canvas.set_double_buffered.assert_called_with(False)
        self.assertEqual(self.panel.native_presentation['presentation'],'egl-gles3d-single-swap')
        self.assertTrue(self.panel.native_presentation['renderer_accelerated'])
        self.panel.renderer.is_native=False
        self.panel.renderer.metrics.return_value={'accelerated':False}
        self.panel.apply_renderer_presentation(self.panel.canvas)
        self.panel.canvas.set_double_buffered.assert_called_with(True)
        self.assertFalse(self.panel.native_presentation['renderer_accelerated'])

    def test_native_draw_consumes_signal_and_fallback_draw_does_not(self):
        self.panel.renderer.is_native=True
        self.panel.renderer.render.return_value=True
        self.assertTrue(self.panel.draw(self.panel.canvas,None))
        self.panel.renderer.is_native=False
        self.panel.renderer.render.return_value=False
        self.assertFalse(self.panel.draw(self.panel.canvas,None))

    def test_native_window_creation_reaches_server_before_egl_connection_queries_it(self):
        # Real-board counterexample: EGL's separate X connection cannot see
        # GDK's newly-realized drawable until GDK completes its X request.
        order=[]
        class NativeWindow:
            def ensure_native(self):order.append('native');return True
            def get_xid(self):order.append('xid');return 101
        window=NativeWindow()
        widget=SimpleNamespace(get_window=lambda:window,get_has_window=lambda:True,
            get_visual=lambda:SimpleNamespace(get_depth=lambda:16),
            get_allocated_width=lambda:960,get_allocated_height=lambda:540,
            get_display=lambda:SimpleNamespace(sync=lambda:order.append('sync')))
        repo=ModuleType('gi.repository');repo.GdkX11=SimpleNamespace(X11Window=NativeWindow)
        self.panel.renderer.configure_depth=Mock();self.panel.renderer.is_native=True
        self.panel.renderer.attach_window=Mock(side_effect=lambda *args:order.append('egl'))
        self.panel.apply_renderer_presentation=Mock()
        with patch.dict(sys.modules,{'gi.repository':repo}):self.panel.prepare_renderer(widget)
        self.assertEqual(order,['native','xid','sync','egl'])
        self.panel.renderer.attach_window.assert_called_once_with(101,960,540)

    def test_native_visual_uses_current_x_root_id_not_a_fixed_visual_number(self):
        x=SimpleNamespace(**{name:Mock() for name in ('XOpenDisplay','XDefaultScreen',
            'XDefaultVisual','XVisualIDFromVisual','XCloseDisplay')})
        x.XOpenDisplay.return_value=17;x.XDefaultScreen.return_value=0
        x.XDefaultVisual.return_value=91;x.XVisualIDFromVisual.return_value=913
        screen=object();visual=object();lookup=Mock(return_value=visual)
        repo=ModuleType('gi.repository');repo.GdkX11=SimpleNamespace(
            X11Screen=SimpleNamespace(lookup_visual=lookup))
        canvas=SimpleNamespace(get_screen=lambda:screen,set_visual=Mock())
        with patch.dict(sys.modules,{'gi.repository':repo}),patch.object(self.ui.ctypes,'CDLL',return_value=x):
            self.assertEqual(self.ui.choose_native_visual(canvas),913)
        lookup.assert_called_once_with(screen,913);canvas.set_visual.assert_called_once_with(visual)
        x.XCloseDisplay.assert_called_once_with(17)

    def test_stdin_action_and_eof_queue_shutdown(self):
        lines='not-json\n[]\n'+json.dumps(dict(op='action',action='start',command_id='stdin-start'))+'\n'
        with patch.object(self.ui.sys,'stdin',io.StringIO(lines)):
            self.ui.commands(self.panel)
        calls=self.ui.GLib.idle_add.call_args_list
        self.assertEqual(calls[0].args[0],self.panel.handle_action)
        self.assertEqual(calls[-1].args,(self.panel.operation,'shutdown'))
        self.assertTrue(all(call.kwargs.get('priority')==0 for call in calls),
                        'Host commands must not starve behind continuous GPU painting')


if __name__=='__main__':unittest.main()
