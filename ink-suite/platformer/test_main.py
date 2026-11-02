"""Headless lifecycle/IPC tests using real physics and synthetic pose packets.

GTK, rendering, audio and scheduling are replaced at import time; network is
forbidden. These tests neither start the pose service nor touch a camera.
Native GTK presentation is a separate Xvfb/board acceptance check.
"""
import ast
from collections import deque
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from platformer.test_controls import packet, pose


HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
if not (SUITE / 'action_dispatch.py').exists():
    SUITE = HERE.parents[1] / 'candidate' / 'InkSuite'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Widget:
    def __init__(self, *args, **kwargs):
        self.queue_draw = Mock()
        self.connect = Mock()
    def __getattr__(self, name):
        if name == 'get_allocated_width': return lambda: 640
        if name == 'get_allocated_height': return lambda: 360
        return Mock()


class FrameCanvas(Widget):
    """Small frame-clock widget whose callback stops while it is unmapped."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callback = None
        self.visible = True
        self.removed = []
    def add_tick_callback(self, callback):
        self.callback = callback
        return 71
    def remove_tick_callback(self, identifier):
        self.removed.append(identifier)
        self.callback = None
    def fire(self):
        if not self.visible: return True
        if self.callback is None: return False
        keep = self.callback(self, object())
        if not keep: self.callback = None
        return keep


class FakeSound:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.director = SimpleNamespace(status=Mock(return_value={'test': True}))
        for method in ('resume', 'pause', 'restart', 'close', 'tick', 'handle'):
            setattr(self, method, Mock())


def _headless_main(canvas_type=Widget):
    gtk = SimpleNamespace(Box=Widget, DrawingArea=canvas_type,
                          Orientation=SimpleNamespace(VERTICAL=1),
                          main_level=lambda: 0, main_quit=Mock())
    gdk = SimpleNamespace(EventMask=SimpleNamespace(BUTTON_PRESS_MASK=1),
                          KEY_Escape=1, KEY_Menu=2, KEY_F1=3,
                          KEY_Right=4, KEY_Left=5, KEY_space=6, KEY_Return=7)
    glib = SimpleNamespace(timeout_add=Mock(return_value=1),
                           timeout_add_seconds=Mock(return_value=2),
                           source_remove=Mock(), idle_add=Mock(), PRIORITY_DEFAULT=0)
    gi = ModuleType('gi')
    gi.require_version = Mock()
    repository = ModuleType('gi.repository')
    repository.Gtk, repository.Gdk, repository.GLib = gtk, gdk, glib
    render = ModuleType('platformer.render')
    render.Renderer = Mock(return_value=SimpleNamespace(render=Mock(), release_native_output=Mock()))
    audio = ModuleType('platformer.audio')
    audio.PlatformerSound = FakeSound
    metrics = ModuleType('sword.metrics')
    metrics.LatestMetricsWriter = Mock()
    modules = {'gi': gi, 'gi.repository': repository, 'cairo': ModuleType('cairo'),
               'platformer.render': render, 'platformer.audio': audio,
               'sword.metrics': metrics,
               'action_dispatch': _load('_platformer_action_dispatch', SUITE / 'action_dispatch.py')}
    old_path = list(sys.path)
    try:
        with patch.dict(sys.modules, modules), patch.dict('os.environ'):
            return _load('_platformer_headless_main', HERE / 'main.py')
    finally:
        sys.path[:] = old_path


class OnePoll:
    """Run one synchronous reader iteration, without a worker or sleep."""
    def __init__(self): self.stopped = False
    def is_set(self): return self.stopped
    def set(self): self.stopped = True
    def wait(self, seconds):
        self.stopped = True
        return True


class Response:
    def __init__(self, value, hook=None):
        self.raw = json.dumps(value).encode()
        self.hook = hook
        self.read_size = None
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size):
        self.read_size = size
        if self.hook: self.hook()
        return self.raw[:size]


class LifecycleTests(unittest.TestCase):
    def test_unrealize_releases_native_buffer_without_pausing_game_or_audio(self):
        handlers = [args[1] for args, kwargs in self.panel.canvas.connect.call_args_list
                    if args[0] == 'unrealize']
        self.assertEqual(len(handlers), 1)
        before = self.panel.game.snapshot()
        pauses = self.panel.sound.pause.call_count
        handlers[0](self.panel.canvas)
        self.panel.renderer.release_native_output.assert_called_once_with()
        self.assertEqual(self.panel.game.snapshot(), before)
        self.assertEqual(self.panel.sound.pause.call_count, pauses)

    def setUp(self):
        self.ui = _headless_main(getattr(self, 'canvas_type', Widget))
        self.now = 100.
        self.clock = patch.object(self.ui.time, 'monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.network = patch('urllib.request.OpenerDirector.open',
                             side_effect=AssertionError('Headless test attempted network access'))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.thread_patch = patch.object(self.ui.threading, 'Thread')
        self.threads = self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.out = io.StringIO()
        self.stdout_patch = patch('sys.stdout', self.out)
        self.stdout_patch.start()
        self.addCleanup(self.stdout_patch.stop)
        args = SimpleNamespace(embed_socket=None, offline_preview=True,
                               base='http://127.0.0.1:1', entry_delay=0.,
                               benchmark=0, metrics=None, fullscreen=False)
        self.panel = self.ui.PlatformerPanel(args)

    def tearDown(self):
        self.panel.operation('shutdown')
        self.network_mock.assert_not_called()

    def activate(self, live=True):
        self.panel.args.offline_preview = not live
        self.panel.ready = False
        self.panel.game.start(0)
        self.panel.entry_until = 0.

    def tick(self, t, state=None):
        self.now = t
        self.panel.latest = state
        return self.panel.tick()

    def hold_hands(self, right=(.25,.65), left=(0.,.5), seconds=.4):
        rows=[]
        for unused in range(round(seconds/.05)):
            t=self.now+.05
            self.tick(t,packet(t,pose(right,left)))
            rows.append(dict(self.panel.control))
        return rows

    def calibrate_right(self):
        self.hold_hands(seconds=1.05)
        self.assertEqual(self.panel.control['calibration'],'ready')

    def poll_once(self, state, hook=None):
        self.panel.stop = OnePoll()
        response = Response(state, hook)
        opener = SimpleNamespace(open=Mock(return_value=response))
        with patch.object(self.ui.urllib.request, 'build_opener', return_value=opener):
            self.panel.poll()
        # Restore lifecycle object after the bounded test reader returned.
        self.panel.stop = threading.Event()
        return opener, response

    def test_offline_preview_starts_no_pose_reporter_or_audio_workers(self):
        self.threads.assert_not_called()
        self.assertIsNone(self.panel.poll_thread)
        self.assertIsNone(self.panel.reporter)
        self.assertFalse(self.panel.sound.enabled)
        self.assertFalse(self.panel.performance()['camera_used'])

    def test_title_local_hold_and_press_start_without_calibration_after_entry_delay(self):
        self.panel.args.offline_preview=False
        self.panel.args.entry_delay=3.
        self.panel.operation('pause')
        self.panel.operation('resume')
        before=self.panel.game.round_id
        raised=pose((0.,-.5),(0.,-.5))
        with patch.object(self.panel.tracker,'update',wraps=self.panel.tracker.update) as tracker:
            self.tick(100.5,packet(100.5,raised))
            self.assertIn('3秒',self.panel.snapshot['ui']['notice'])
            self.tick(102.99,packet(102.99,raised))
            self.assertIn('1秒',self.panel.snapshot['ui']['notice'])
            tracker.assert_not_called()
        self.tick(103.01,packet(103.01,raised))
        self.assertTrue(self.panel.ready)
        self.assertFalse(self.panel.control['confirm'])
        self.hold_hands(right=(.25,.15),seconds=.1)
        self.hold_hands(right=(1.15,.65),seconds=.4)
        self.assertNotEqual(self.panel.control['calibration'],'ready')
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手向下压即可开始')
        self.assertTrue(self.panel.ready)
        with patch.object(self.panel,'restart',wraps=self.panel.restart) as restart:
            self.hold_hands(right=(1.15,1.02),seconds=.35)
            self.hold_hands(right=(1.15,1.02),seconds=.4)
            restart.assert_called_once_with()
        self.assertFalse(self.panel.ready)
        self.assertEqual(self.panel.game.round_id,before+1)
        self.assertEqual(self.panel.game.state,'countdown')
        self.assertEqual(self.panel.game.round_id,before+1)
        self.assertFalse(self.panel.control['confirm'] or self.panel.control['jump'])

    def test_confirmation_scope_changes_without_raised_hands_confirming(self):
        self.activate()
        raised=pose((0.,-.5),(0.,-.5))
        before=self.panel.game.round_id
        for state in ('playing','over','won'):
            self.panel.game.state=state
            self.panel.clear_input()
            with patch.object(self.panel.tracker,'update',wraps=self.panel.tracker.update) as tracker:
                for unused in range(4):
                    now=self.now+.08
                    self.tick(now,packet(now,raised))
                    self.assertFalse(self.panel.control['confirm'])
                context=False if state=='playing' else 'terminal'
                self.assertTrue(all(call.kwargs=={'confirm_context':context}
                                    for call in tracker.call_args_list))
            self.assertEqual(self.panel.game.round_id,before)

    def test_title_accepts_right_only_hold_and_names_missing_right_arm(self):
        self.panel.args.offline_preview=False
        left_missing=pose((.25,.65),(0.,.5));left_missing[7][2]=0.;left_missing[9][2]=0.
        self.tick(100.01,packet(100.01,left_missing))
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手放在舒适位置，稍停后下压开始')
        for _ in range(8):
            t=self.now+.05
            self.tick(t,packet(t,left_missing))
        self.assertTrue(self.panel.control['confirm_ready'])
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手向下压即可开始')
        right_missing=pose((0.,-.5),(0.,-.5));right_missing[8][2]=0.;right_missing[10][2]=0.
        t=self.now+.05
        self.tick(t,packet(t,right_missing))
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手臂未识别，请完整入镜')
        self.tick(t+.05,packet(99.,pose((0.,-.5),(0.,-.5))))
        self.assertEqual(self.panel.snapshot['ui']['notice'],'骨架更新中，请稍候再操作')
        self.assertTrue(self.panel.ready)
        self.assertFalse(self.panel.control['confirm'])

    def test_title_local_hold_bypasses_old_geometry_freeze_and_updates_zero_on_press(self):
        self.panel.args.offline_preview=False
        self.calibrate_right()
        t=self.now+.05
        self.tick(t,packet(t,pose((.25,.65),scale=1.2)))
        self.assertEqual(self.panel.control['calibration'],'frozen')
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手放在舒适位置，稍停后下压开始')
        self.assertTrue(self.panel.ready)
        self.assertFalse(self.panel.control['confirm'])
        for _ in range(8):
            t=self.now+.05
            self.tick(t,packet(t,pose((.25,.65),scale=1.2)))
        self.assertEqual(self.panel.control['calibration'],'frozen')
        self.assertTrue(self.panel.control['confirm_ready'])
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手向下压即可开始')
        before=self.panel.game.round_id
        for _ in range(8):
            t=self.now+.05
            self.tick(t,packet(t,pose((.25,1.03),scale=1.2)))
        self.assertFalse(self.panel.ready)
        self.assertEqual(self.panel.game.round_id,before+1)

    def test_title_pending_message_precedes_incomplete_movement_calibration(self):
        self.panel.args.offline_preview=False
        self.hold_hands(right=(1.15,.65),seconds=.4)
        self.assertNotEqual(self.panel.control['calibration'],'ready')
        self.assertTrue(self.panel.control['confirm_ready'])
        before=self.panel.game.round_id
        t=self.now+.05
        self.tick(t,packet(t,pose((1.15,1.03))))
        self.assertTrue(self.panel.control['confirm_pending'])
        self.assertEqual(self.panel.snapshot['ui']['notice'],'右手已下压，正在确认')
        self.assertEqual(self.panel.game.round_id,before)
        self.hold_hands(right=(1.15,1.03),seconds=.2)
        self.assertFalse(self.panel.ready)
        self.assertEqual(self.panel.game.round_id,before+1)
        self.hold_hands(right=(1.15,.65),seconds=.25)
        self.assertAlmostEqual(self.panel.control['joystick_dx'],0.)
        self.assertAlmostEqual(self.panel.control['joystick_dy'],0.)

    def test_title_old_center_and_pause_reset_do_not_require_precise_return(self):
        self.panel.args.offline_preview=False
        self.calibrate_right()
        self.panel.operation('pause')
        self.panel.operation('resume')
        before=self.panel.game.round_id
        self.hold_hands(right=(.95,.65),seconds=.5)
        self.assertTrue(self.panel.control['confirm_ready'])
        self.assertAlmostEqual(self.panel.control['joystick_dx'],.70)
        self.hold_hands(right=(.95,1.03),seconds=.4)
        self.assertFalse(self.panel.ready)
        self.assertEqual(self.panel.game.round_id,before+1)

    def test_title_acquired_down_hand_never_starts_without_an_additional_press(self):
        self.panel.args.offline_preview=False
        before=self.panel.game.round_id
        rows=self.hold_hands(right=(.25,1.03),seconds=1.5)
        self.assertFalse(any(row['confirm'] for row in rows))
        self.assertEqual(self.panel.game.round_id,before)
        self.assertTrue(self.panel.ready)

    def test_title_source_loss_discards_pending_confirmation_before_physics(self):
        self.panel.args.offline_preview=False
        self.hold_hands(right=(1.15,.65),seconds=.4)
        before=self.panel.game.round_id
        t=self.now+.05
        self.tick(t,packet(t,pose((1.15,1.03))))
        self.assertTrue(self.panel.control['confirm_pending'])
        self.tick(t+.05,None)
        self.assertFalse(self.panel.control['confirm_pending'] or self.panel.control['confirm'])
        self.hold_hands(right=(1.15,1.03),seconds=.6)
        self.assertEqual(self.panel.game.round_id,before)
        self.assertTrue(self.panel.ready)

    def test_action_ack_accepts_explicit_mode_and_can_be_decoded(self):
        self.ui.emit('action_result', mode='platformer', command_id='safe.1', ok=True)
        row = json.loads(self.out.getvalue().splitlines()[-1])
        self.assertEqual(row['event'], 'action_result')
        self.assertEqual(row['mode'], 'platformer')

    def test_host_restart_while_paused_waits_for_parent_resume_and_is_idempotent(self):
        self.activate()
        self.panel.operation('pause')
        before = self.panel.game.round_id
        request = dict(op='action', command_id='restart.1', action='restart')
        self.panel.handle_action(request)
        self.assertEqual(self.panel.game.round_id, before + 1)
        self.assertEqual(self.panel.game.state, 'paused')
        self.assertFalse(self.panel.active)
        self.panel.handle_action(request)
        self.assertEqual(self.panel.game.round_id, before + 1)
        rows = [json.loads(line) for line in self.out.getvalue().splitlines()]
        acks = [r for r in rows if r['event'] == 'action_result']
        self.assertEqual(len(acks), 2)
        self.assertTrue(all(r['ok'] for r in acks))
        self.assertEqual(acks[0], acks[1])
        self.panel.operation('resume')
        self.assertEqual(self.panel.game.state, 'countdown')

    def test_active_host_action_is_rejected_without_resetting_round(self):
        self.activate()
        before = self.panel.game.round_id
        self.panel.handle_action(dict(op='action', command_id='restart.2', action='restart'))
        self.assertEqual(self.panel.game.round_id, before)
        row = json.loads(self.out.getvalue().splitlines()[-1])
        self.assertFalse(row['ok'])
        self.assertEqual(row['reason'], 'experience_not_paused')

    def test_parent_pause_freezes_time_and_resume_discards_wall_time_debt(self):
        self.activate()
        self.tick(100.05, packet(100.05))
        elapsed = self.panel.game.elapsed
        self.panel.operation('pause')
        self.tick(130.05, packet(130.05, pose((.8, 0.))))
        self.assertEqual(self.panel.game.elapsed, elapsed)
        self.assertEqual(self.panel.control['move'], 0)
        self.now = 140.
        self.panel.operation('resume')
        self.tick(140.016, packet(140.016))
        self.assertLessEqual(self.panel.game.elapsed - elapsed, .02)
        self.assertEqual(self.panel.game.state, 'playing')

    def test_transient_missing_pose_stops_input_without_pausing_game_or_music(self):
        self.activate()
        self.panel.sound.pause.reset_mock()
        for i in range(1, 30): self.tick(100. + i * .016, None)
        self.assertEqual(self.panel.game.state, 'playing')
        self.assertGreater(self.panel.game.elapsed, .4)
        self.assertEqual(self.panel.control['move'], 0)
        self.assertFalse(self.panel.control['jump'])
        self.panel.sound.pause.assert_not_called()
        self.assertGreater(self.panel.sound.tick.call_count, 20)

    def test_repeated_pose_frame_does_not_reissue_jump_to_physics(self):
        self.activate()
        self.hold_hands(seconds=.2)
        for i in range(20):
            t = self.now+.05
            p = packet(t, pose(left=(0., max(-.2, .5 - i * .06))))
            self.tick(t, p)
            if self.panel.control['jump']: break
        self.assertTrue(self.panel.control['jump'])
        accepted = self.panel.game.audio_counts['jump']
        self.assertEqual(accepted, 1)
        for i in range(1, 12):
            self.tick(t + i * .016, p)
            self.assertFalse(self.panel.control['jump'])
        self.assertEqual(self.panel.game.audio_counts['jump'], accepted)

    def test_offline_held_space_does_not_jump_again_on_landing(self):
        self.activate(live=False)
        self.panel.keys.add(self.ui.Gdk.KEY_space)
        for i in range(1, 160): self.tick(100. + i / 60)
        self.assertEqual(self.panel.game.audio_counts['jump'], 1)

    def test_offline_keyboard_feedback_follows_keys_without_claiming_camera_input(self):
        self.activate(live=False)
        self.panel.keys.add(self.ui.Gdk.KEY_Right)
        self.tick(100.05)
        feedback=self.panel.snapshot['ui']['control']
        self.assertEqual(feedback['move'],1)
        self.assertGreater(feedback['joystick_dx'],0.)
        self.assertEqual(feedback['calibration'],'ready')
        self.assertFalse(self.panel.performance()['camera_used'])
        self.panel.keys.clear()
        self.tick(100.1)
        self.assertEqual(self.panel.snapshot['ui']['control']['move'],0)

    def test_terminal_context_press_restarts_once_and_needs_return_to_rest(self):
        self.activate()
        self.panel.game.state = 'over'
        before = self.panel.game.round_id
        self.calibrate_right()
        self.hold_hands(right=(.25,1.02),seconds=.35)
        self.assertEqual(self.panel.game.round_id, before + 1)
        self.assertFalse(self.panel.ready)
        self.assertIn(self.panel.game.state, ('countdown', 'playing'))
        self.panel.game.state = 'over'
        self.hold_hands(right=(.25,1.02),seconds=.7)
        self.assertEqual(self.panel.game.round_id, before + 1)
        self.assertEqual(self.panel.game.state, 'over')
        self.hold_hands(seconds=.4)
        self.hold_hands(right=(.25,1.02),seconds=.35)
        self.assertEqual(self.panel.game.round_id,before+2)

    def test_terminal_local_hold_far_from_old_center_restarts_and_learns_anchor(self):
        self.activate()
        self.calibrate_right()
        self.panel.game.state='won'
        before=self.panel.game.round_id
        self.hold_hands(right=(.95,.65),seconds=.5)
        self.assertTrue(self.panel.control['confirm_ready'])
        self.assertAlmostEqual(self.panel.control['joystick_dx'],.70)
        self.hold_hands(right=(.95,1.03),seconds=.4)
        self.assertEqual(self.panel.game.round_id,before+1)
        self.hold_hands(right=(.95,.65),seconds=.25)
        self.assertAlmostEqual(self.panel.control['joystick_dx'],0.)
        self.assertAlmostEqual(self.panel.control['joystick_dy'],0.)

    def test_ordinary_play_press_only_stops_and_pipe_context_requires_stopped_player(self):
        self.activate()
        self.calibrate_right()
        self.hold_hands(right=(.55,.65),seconds=.3)
        self.assertEqual(self.panel.control['move'],1)
        rows=self.hold_hands(right=(.25,1.02),seconds=.35)
        self.assertTrue(all(row['move']==0 and not row['confirm'] for row in rows))
        self.assertEqual(self.panel.snapshot['ui']['confirm_context'],False)
        pipe=self.panel.game.pipes[0]
        p=self.panel.game.player
        p.update(x=pipe['x']+16-p['w']/2,y=pipe['y']-p['h'],
                 grounded=True,vx=10.,vy=0.)
        self.assertFalse(self.ui.confirmation_context(self.panel.game,False))
        p['vx']=0.
        self.assertEqual(self.ui.confirmation_context(self.panel.game,False),'pipe')
        self.hold_hands(seconds=.4)
        rows=self.hold_hands(right=(.25,1.02),seconds=.35)
        self.assertEqual(sum(row['confirm'] for row in rows),1)
        self.assertEqual(self.panel.game.transition_kind,'pipe')
        self.assertEqual(self.panel.game.state,'transition')

    def test_paused_terminal_screen_remains_terminal_on_continue(self):
        self.activate()
        self.panel.game.state = 'won'
        self.panel.operation('pause')
        self.panel.operation('resume')
        self.tick(100.05, packet(100.05))
        self.assertEqual(self.panel.game.state, 'won')

    def test_entry_delay_does_not_consume_pose_or_advance_physics(self):
        self.activate()
        self.panel.args.entry_delay = 1.
        self.panel.operation('pause')
        self.panel.operation('resume')
        before = self.panel.game.elapsed
        with patch.object(self.panel.tracker, 'update', wraps=self.panel.tracker.update) as tracker:
            self.tick(100.5, packet(100.5, pose((0., -.5), (0., -.5))))
            tracker.assert_not_called()
        self.assertEqual(self.panel.game.elapsed, before)
        self.tick(101.01, packet(101.01, pose((0., -.5), (0., -.5))))
        self.assertFalse(self.panel.control['jump'] or self.panel.control['confirm'])

    def test_menu_candidate_blocks_game_input_without_child_autopause(self):
        self.activate()
        self.tick(100.05, packet(100.05, suite={'control_active': True}))
        self.assertEqual(self.panel.control['reason'], 'menu_gesture')
        self.assertEqual(self.panel.game.state, 'playing')
        self.assertFalse(self.panel.control['jump'] or self.panel.control['confirm'])

    def test_malformed_nested_suite_does_not_break_tick(self):
        self.activate()
        self.assertTrue(self.tick(100.05, packet(100.05, suite=['bad'])))
        self.assertEqual(self.panel.game.state, 'playing')

    def test_delayed_transport_result_is_dropped_across_pause_resume_epoch(self):
        self.activate()
        def pause_resume():
            self.panel.operation('pause')
            self.panel.operation('resume')
        self.poll_once(packet(100.), hook=pause_resume)
        self.assertIsNone(self.panel.latest)

    def test_delayed_transport_result_is_dropped_across_restart_epoch(self):
        self.activate()
        self.poll_once(packet(100.), hook=self.panel.restart)
        self.assertIsNone(self.panel.latest)

    def test_transport_is_latest_only_bounded_and_does_not_schedule_gtk_callbacks(self):
        self.activate()
        self.panel.latest = {'old': True}
        before = self.ui.GLib.idle_add.call_count
        value = packet(100.02)
        opener, response = self.poll_once(value)
        self.assertEqual(self.panel.latest, value)
        self.assertEqual(response.read_size, 256 * 1024 + 1)
        self.assertEqual(self.ui.GLib.idle_add.call_count, before)
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.kwargs['timeout'], .45)

    def test_inactive_transport_does_not_request_pose(self):
        self.panel.operation('pause')
        opener, _ = self.poll_once(packet(100.))
        opener.open.assert_not_called()

    def test_invalid_or_oversized_transport_reply_clears_latest_slot(self):
        self.activate()
        for value in ([], {'padding': 'x' * (256 * 1024 + 1)}):
            self.panel.latest = packet(100.)
            self.poll_once(value)
            self.assertIsNone(self.panel.latest)

    def test_shutdown_is_idempotent_and_removes_timer(self):
        self.panel.operation('shutdown')
        self.panel.operation('shutdown')
        self.panel.sound.close.assert_called_once()
        self.ui.GLib.source_remove.assert_called_once_with(1)
        self.assertFalse(self.panel.tick())


class GamePriorityTests(unittest.TestCase):
    """All OS reads, renice execution and rollback writes are mocked."""
    def setUp(self):
        self.ui = _headless_main()
        self.patch('platform', self.ui.sys, 'platform', 'linux')
        self.patch('pid', self.ui.os, 'getpid', return_value=123456)
        self.patch('priority_constant', self.ui.os, 'PRIO_PROCESS', 0, create=True)
        self.read = self.patch('read', self.ui.os, 'getpriority', side_effect=[0, -5], create=True)
        self.restore = self.patch('restore', self.ui.os, 'setpriority', create=True)
        self.run = self.patch('run', self.ui.subprocess, 'run',
                              return_value=SimpleNamespace(returncode=0, stdout='', stderr=''))

    def patch(self, label, target, name, *args, **kwargs):
        operation = patch.object(target, name, *args, **kwargs)
        value = operation.start(); self.addCleanup(operation.stop)
        return value

    def test_non_linux_preserves_state_without_os_priority_or_command(self):
        self.ui.sys.platform = 'win32'
        result = self.ui.configure_game_priority()
        self.assertEqual(result['reason'], 'non-linux')
        self.assertFalse(result['attempted'])
        self.assertIsNone(result['before']); self.assertIsNone(result['after'])
        self.read.assert_not_called(); self.run.assert_not_called(); self.restore.assert_not_called()

    def test_other_thread_cannot_change_main_thread_priority(self):
        with patch.object(self.ui.threading, 'current_thread', return_value=object()):
            result = self.ui.configure_game_priority()
        self.assertEqual(result['reason'], 'not-main-thread')
        self.read.assert_not_called(); self.run.assert_not_called(); self.restore.assert_not_called()

    def test_higher_existing_priority_is_observed_and_not_lowered(self):
        self.read.side_effect = [-8, -8]
        result = self.ui.configure_game_priority()
        self.assertEqual((result['before'], result['after']), (-8, -8))
        self.assertTrue(result['success']); self.assertFalse(result['changed'])
        self.assertFalse(result['attempted'])
        self.run.assert_not_called(); self.restore.assert_not_called()

    def test_success_requires_actual_priority_and_uses_only_current_pid(self):
        result = self.ui.configure_game_priority()
        self.assertEqual((result['before'], result['after']), (0, -5))
        self.assertTrue(result['success']); self.assertTrue(result['changed'])
        self.assertEqual(result['reason'], 'priority-configured')
        self.run.assert_called_once_with(['sudo', '-n', 'renice', '-n', '-5', '-p', '123456'],
            capture_output=True, text=True, timeout=1., check=False, shell=False)
        self.assertEqual(self.read.call_args_list, [call(0, 123456), call(0, 123456)])
        self.restore.assert_not_called()

    def test_permission_failure_preserves_observed_priority(self):
        self.read.side_effect = [0, 0]
        self.run.return_value = SimpleNamespace(returncode=1, stdout='', stderr='permission denied')
        result = self.ui.configure_game_priority()
        self.assertFalse(result['success']); self.assertFalse(result['changed'])
        self.assertEqual((result['before'], result['after']), (0, 0))
        self.assertEqual(result['reason'], 'renice-rejected')
        self.assertIn('permission denied', result['detail'])
        self.restore.assert_not_called()

    def test_timeout_missing_tool_and_os_error_are_nonfatal(self):
        errors = [self.ui.subprocess.TimeoutExpired(['sudo'], 1.), FileNotFoundError(), PermissionError()]
        reasons = ['renice-timeout', 'renice-unavailable', 'renice-error']
        for error, reason in zip(errors, reasons):
            with self.subTest(reason=reason):
                self.read.side_effect = [0, 0]
                self.run.side_effect = error
                result = self.ui.configure_game_priority()
                self.assertEqual(result['reason'], reason)
                self.assertFalse(result['success']); self.assertFalse(result['changed'])
                self.assertEqual((result['before'], result['after']), (0, 0))
        self.restore.assert_not_called()

    def test_read_unavailable_does_not_attempt_a_priority_change(self):
        self.read.side_effect = PermissionError()
        result = self.ui.configure_game_priority()
        self.assertEqual(result['reason'], 'priority-read-unavailable')
        self.assertFalse(result['attempted'])
        self.run.assert_not_called(); self.restore.assert_not_called()

    def test_successful_tool_exit_without_actual_change_is_reported_failed(self):
        self.read.side_effect = [0, 0]
        result = self.ui.configure_game_priority()
        self.assertFalse(result['success']); self.assertFalse(result['changed'])
        self.assertEqual(result['reason'], 'priority-not-applied')

    def test_verification_failure_records_unknown_actual_priority(self):
        self.read.side_effect = [0, PermissionError()]
        result = self.ui.configure_game_priority()
        self.assertEqual(result['reason'], 'priority-verification-unavailable')
        self.assertFalse(result['success']); self.assertIsNone(result['after'])
        self.assertIsNone(result['changed'])

    def test_partial_timed_out_change_restores_only_our_original_priority(self):
        self.read.side_effect = [0, -5, 0]
        self.run.side_effect = self.ui.subprocess.TimeoutExpired(['sudo'], 1.)
        result = self.ui.configure_game_priority()
        self.assertEqual(result['reason'], 'renice-timeout')
        self.assertTrue(result['rollback_attempted'])
        self.assertEqual(result['after'], 0)
        self.assertFalse(result['success']); self.assertFalse(result['changed'])
        self.restore.assert_called_once_with(0, 123456, 0)

    def test_main_starts_reader_before_priority_and_shows_window_afterward(self):
        order = []
        panel = SimpleNamespace(canvas=object(), key=Mock(), operation=Mock(), poll_thread=None)
        window = Mock()
        window.show_all.side_effect = lambda: order.append('show')
        reader = Mock()
        reader.start.side_effect = lambda: order.append('reader-start')
        priority = dict(before=0, after=-5, success=True)
        def construct(args):
            order.append('panel'); return panel
        def configure():
            order.append('priority'); return priority
        with patch.object(self.ui, 'PlatformerPanel', side_effect=construct), \
             patch.object(self.ui.Gtk, 'Plug', SimpleNamespace(new=Mock(return_value=window)), create=True), \
             patch.object(self.ui.Gtk, 'main', Mock(), create=True), \
             patch.object(self.ui, 'configure_native_canvas', return_value={'enabled': True}), \
             patch.object(self.ui, 'configure_game_priority', side_effect=configure), \
             patch.object(self.ui.threading, 'Thread', return_value=reader) as thread, \
             patch.object(self.ui.signal, 'signal'):
            self.assertEqual(self.ui.main(['--offline-preview', '--embed-socket', '123']), 0)
        self.assertEqual(order, ['panel', 'reader-start', 'priority', 'show'])
        thread.assert_called_once_with(target=self.ui.read_commands, args=(panel,), daemon=True)
        self.assertIs(panel.game_priority, priority)
        self.read.assert_not_called(); self.run.assert_not_called()


class CanvasTickTests(unittest.TestCase):
    def setUp(self):
        self.ui = _headless_main()

    def test_frame_callback_is_preferred_and_explicit_cleanup_is_idempotent(self):
        canvas, tick = FrameCanvas(), Mock(return_value=True)
        driver = self.ui.start_canvas_ticks(canvas, tick)
        self.assertEqual(driver['kind'], 'frame-clock')
        self.ui.GLib.timeout_add.assert_not_called()
        callback = canvas.callback
        self.assertTrue(canvas.fire())
        tick.assert_called_once_with()
        self.ui.stop_canvas_ticks(driver)
        self.ui.stop_canvas_ticks(driver)
        self.assertEqual(canvas.removed, [71])
        self.ui.GLib.source_remove.assert_not_called()
        self.assertFalse(callback(canvas, object()))
        self.assertEqual(tick.call_count, 1)

    def test_false_tick_self_removes_without_cleanup_of_an_expired_id(self):
        canvas, tick = FrameCanvas(), Mock(side_effect=(True, False))
        driver = self.ui.start_canvas_ticks(canvas, tick)
        self.assertTrue(canvas.fire())
        self.assertFalse(canvas.fire())
        self.assertFalse(driver['active'])
        self.assertIsNone(driver['identifier'])
        self.ui.stop_canvas_ticks(driver)
        self.assertEqual(canvas.removed, [])
        self.assertEqual(tick.call_count, 2)

    def test_missing_unsupported_and_generic_mock_callbacks_use_timer(self):
        for canvas in (SimpleNamespace(), Widget(),
                       SimpleNamespace(add_tick_callback=Mock(side_effect=NotImplementedError),
                                       remove_tick_callback=Mock())):
            with self.subTest(canvas=type(canvas).__name__):
                self.ui.GLib.timeout_add.reset_mock()
                self.ui.GLib.source_remove.reset_mock()
                tick = Mock(return_value=True)
                driver = self.ui.start_canvas_ticks(canvas, tick)
                self.assertEqual(driver['kind'], 'timeout')
                interval, callback = self.ui.GLib.timeout_add.call_args.args
                self.assertEqual(interval, 16)
                self.assertTrue(callback())
                tick.assert_called_once_with()
                self.ui.stop_canvas_ticks(driver)
                self.ui.stop_canvas_ticks(driver)
                self.ui.GLib.source_remove.assert_called_once_with(1)
                self.assertFalse(callback())
                self.assertEqual(tick.call_count, 1)

    def test_shutdown_inside_frame_tick_cannot_leave_callback_registered(self):
        canvas = FrameCanvas()
        driver = None
        def tick():
            self.ui.stop_canvas_ticks(driver)
            return True
        driver = self.ui.start_canvas_ticks(canvas, tick)
        self.assertFalse(canvas.fire())
        self.assertEqual(canvas.removed, [71])
        self.ui.stop_canvas_ticks(driver)
        self.assertEqual(canvas.removed, [71])


class FrameLifecycleTests(LifecycleTests):
    """Run the complete IPC/input lifecycle through registered frame callbacks."""
    canvas_type = FrameCanvas

    def tick(self, t, state=None):
        self.now = t
        self.panel.latest = state
        return self.panel.canvas.fire()

    def test_shutdown_is_idempotent_and_removes_timer(self):
        self.panel.operation('shutdown')
        self.panel.operation('shutdown')
        self.panel.sound.close.assert_called_once()
        self.assertEqual(self.panel.canvas.removed, [71])
        self.ui.GLib.source_remove.assert_not_called()
        self.assertFalse(self.panel.canvas.fire())

    def test_unmapped_paused_restart_waits_for_resume_and_discards_hidden_time(self):
        self.activate()
        self.tick(100.05, packet(100.05))
        self.panel.operation('pause')
        self.panel.canvas.visible = False
        before = self.panel.game.elapsed
        self.tick(130.05, packet(130.05, pose((.8, 0.))))
        self.assertEqual(self.panel.game.elapsed, before)
        self.assertTrue(self.panel.timer['active'])
        old_round = self.panel.game.round_id
        self.panel.handle_action(dict(op='action', command_id='hidden-restart.1', action='restart'))
        self.assertEqual(self.panel.game.round_id, old_round + 1)
        self.assertEqual(self.panel.game.state, 'paused')
        self.panel.canvas.visible = True
        self.now = 140.
        self.panel.operation('resume')
        self.tick(140.016, packet(140.016))
        self.assertEqual(self.panel.game.state, 'countdown')
        self.assertEqual(self.panel.game.elapsed, 0.)
        self.assertGreater(self.panel.game.countdown, 1.18)

    def test_identical_timestamps_and_inputs_match_timer_physics_exactly(self):
        args = SimpleNamespace(**vars(self.panel.args))
        with patch.object(self.ui.Gtk, 'DrawingArea', Widget):
            timed = self.ui.PlatformerPanel(args)
        self.addCleanup(timed.operation, 'shutdown')
        timer_callback = self.ui.GLib.timeout_add.call_args.args[1]
        self.activate(live=False)
        timed.ready = False
        timed.game.start(0)
        timed.entry_until = 0.
        for index in range(150):
            # Use the same real-dt trajectory, with substeps and delayed frames.
            self.now += (.005, .011, .024, .033, .017)[index % 5]
            if index == 55:
                self.panel.operation('pause');timed.operation('pause')
                self.panel.canvas.visible = False
            if index == 70:
                self.panel.canvas.visible = True
                self.panel.operation('resume');timed.operation('resume')
            keys = {self.ui.Gdk.KEY_Right}
            if index in (15, 45, 80, 110):keys.add(self.ui.Gdk.KEY_space)
            self.panel.keys = set(keys);timed.keys = set(keys)
            self.assertTrue(timer_callback())
            self.assertTrue(self.panel.canvas.fire())
            self.assertEqual(self.panel.game.snapshot(), timed.game.snapshot())
        self.assertGreater(self.panel.game.elapsed, 1.)
        self.assertEqual(self.panel.performance()['tick_driver'], 'frame-clock')


class X11Display:
    __gtype__ = SimpleNamespace(name='GdkX11Display')


class X11Window:
    __gtype__ = SimpleNamespace(name='GdkX11Window')
    def __init__(self, events, success=True, native=True):
        self.events, self.success, self.native = events, success, native
        self.ensure_native = Mock(side_effect=self.ensure)
    def ensure(self):
        self.events.append('ensure-native')
        return self.success
    def has_native(self): return self.native
    def get_xid(self): return 223344


class Canvas:
    def __init__(self, display=None, window=None):
        self.events = []
        self.display = display or X11Display()
        self.window = window or X11Window(self.events)
        self.owned = True
        self.buffered = True
        self.paintable = False
        self.realized = False
        self.width, self.height = 960, 540
        self.handlers = {}
    def set_app_paintable(self, value): self.paintable = value
    def get_app_paintable(self): return self.paintable
    def set_double_buffered(self, value):
        self.events.append(('buffered', value)); self.buffered = value
    def get_double_buffered(self): return self.buffered
    def get_display(self): return self.display
    def get_window(self): return self.window
    def get_has_window(self): return self.owned
    def get_realized(self): return self.realized
    def get_allocated_width(self): return self.width
    def get_allocated_height(self): return self.height
    def connect(self, name, callback): self.handlers[name] = callback
    def fire(self, name):
        if name != 'size-allocate': self.realized = name == 'realize'
        self.handlers[name](self)


class NativeCanvasTests(unittest.TestCase):
    def setUp(self):
        self.ui = _headless_main()
        repository = ModuleType('gi.repository')
        repository.GdkX11 = SimpleNamespace(X11Display=X11Display, X11Window=X11Window)
        self.modules = patch.dict(sys.modules, {'gi.repository': repository})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.canvas = Canvas()
        self.window = Canvas()

    def configure(self):
        return self.ui.configure_native_canvas(self.canvas, self.window)

    def test_verified_native_window_keeps_world_and_text_in_same_buffer(self):
        state = self.configure()
        self.assertTrue(self.canvas.buffered)
        self.assertFalse(state['enabled'])
        self.assertTrue(state['window_app_paintable'])
        self.assertTrue(state['canvas_app_paintable'])
        self.canvas.fire('realize')
        self.assertTrue(state['enabled'])
        self.assertEqual(state['reason'], 'native-x11-complete-frame')
        self.assertEqual(state['schema'], 2)
        self.assertEqual(state['presentation'], 'gtk-complete-frame')
        self.assertTrue(state['multipass_text'])
        self.assertTrue(state['double_buffered'])
        self.assertEqual(state['display_type'], 'GdkX11Display')
        self.assertEqual(state['canvas_window_type'], 'GdkX11Window')
        self.assertEqual(state['xid'], 223344)
        self.assertTrue(self.canvas.buffered)
        self.assertNotIn(('buffered', False), self.canvas.events)

    def test_previously_unbuffered_canvas_is_restored_before_first_frame(self):
        self.canvas.buffered = False
        state = self.configure(); self.canvas.fire('realize')
        self.assertTrue(self.canvas.buffered)
        self.assertTrue(state['double_buffered'])
        self.assertTrue(state['enabled'])
        self.assertNotIn(('buffered', False), self.canvas.events)
        self.assertIn(('buffered', True), self.canvas.events)

    def test_unsupported_or_shared_window_stays_buffered(self):
        for kind in ('display', 'shared', 'window'):
            with self.subTest(kind=kind):
                self.canvas = Canvas()
                if kind == 'display': self.canvas.display = object()
                elif kind == 'shared': self.canvas.owned = False
                else: self.canvas.window = object()
                state = self.configure(); self.canvas.fire('realize')
                self.assertFalse(state['enabled'])
                self.assertTrue(self.canvas.buffered)
                self.assertNotIn(('buffered', False), self.canvas.events)
                if isinstance(self.canvas.window, X11Window):
                    self.canvas.window.ensure_native.assert_not_called()

    def test_native_creation_failure_or_unverified_native_stays_buffered(self):
        for success, native in ((False, False), (False, True), (True, False)):
            with self.subTest(success=success, native=native):
                self.canvas = Canvas()
                self.canvas.window.success = success
                self.canvas.window.native = native
                state = self.configure(); self.canvas.fire('realize')
                self.assertFalse(state['enabled'])
                self.assertTrue(self.canvas.buffered)
                self.assertNotIn(('buffered', False), self.canvas.events)

    def test_gi_error_restores_safe_buffer(self):
        self.canvas.window.ensure_native.side_effect = RuntimeError('test failure')
        state = self.configure(); self.canvas.fire('realize')
        self.assertFalse(state['enabled'])
        self.assertTrue(self.canvas.buffered)
        self.assertEqual(state['error_type'], 'RuntimeError')

    def test_unrealize_and_new_backend_do_not_reuse_native_flags(self):
        state = self.configure(); self.canvas.fire('realize')
        self.assertTrue(state['enabled'])
        self.canvas.fire('unrealize')
        self.assertTrue(self.canvas.buffered)
        self.assertFalse(state['enabled'])
        self.assertIsNone(state['xid'])
        self.canvas.display = object(); self.canvas.fire('realize')
        self.assertFalse(state['enabled'])
        self.assertFalse(state['x11_display'])
        self.assertEqual(state['realize_count'], 2)

    def test_letterbox_and_full_output_resize_never_expose_partial_text_frame(self):
        state = self.configure(); self.canvas.fire('realize')
        self.assertTrue(state['enabled'])
        for width, height in ((640, 480), (960, 539), (0, 0)):
            with self.subTest(width=width, height=height):
                self.canvas.width, self.canvas.height = width, height
                self.canvas.fire('size-allocate')
                self.assertTrue(self.canvas.buffered)
                self.assertFalse(state['enabled'])
                self.assertFalse(state['full_opaque_output'])
                self.assertEqual(state['allocation'], [width, height])
        self.canvas.width, self.canvas.height = 1280, 720
        self.canvas.fire('size-allocate')
        self.assertTrue(self.canvas.buffered)
        self.assertTrue(state['enabled'])
        self.assertNotIn(('buffered', False), self.canvas.events)
        self.assertEqual(state['realize_count'], 1)

    def test_pre_realize_allocation_does_not_disable_buffer(self):
        state = self.configure(); self.canvas.fire('size-allocate')
        self.assertTrue(self.canvas.buffered)
        self.assertFalse(state['enabled'])
        self.canvas.window.ensure_native.assert_not_called()

    def test_already_realized_plug_child_is_configured_immediately(self):
        self.canvas.realized = True
        state = self.configure()
        self.assertTrue(state['enabled'])
        self.assertTrue(state['realized'])
        self.assertEqual(state['realize_count'], 1)
        self.assertIn('size-allocate', self.canvas.handlers)



class CommandPriorityTests(unittest.TestCase):
    def setUp(self):
        self.ui = _headless_main()
        self.ui.GLib = SimpleNamespace(idle_add=Mock(), PRIORITY_DEFAULT=0)
        self.panel = SimpleNamespace(handle_action=Mock(), operation=Mock())

    def read(self, text):
        with patch.object(self.ui.sys, 'stdin', io.StringIO(text)):
            self.ui.read_commands(self.panel)

    def test_commands_and_eof_keep_wire_order_at_default_priority(self):
        action = dict(op='action', command_id='help.1', action='help')
        self.read('malformed\n[]\n{"op":"pause"}\n' +
                  self.ui.json.dumps(action) + '\n{"op":"resume"}\n')
        self.assertEqual(self.ui.GLib.idle_add.call_args_list, [
            call(self.panel.operation, 'pause', priority=0),
            call(self.panel.handle_action, action, priority=0),
            call(self.panel.operation, 'resume', priority=0),
            call(self.panel.operation, 'shutdown', priority=0)])
        self.panel.operation.assert_not_called()
        self.panel.handle_action.assert_not_called()

    def test_explicit_shutdown_dispatches_once_and_stops_reading(self):
        self.read('{"op":"shutdown"}\n{"op":"resume"}\n')
        self.ui.GLib.idle_add.assert_called_once_with(self.panel.operation, 'shutdown', priority=0)

    def test_read_error_still_queues_shutdown_at_default_priority(self):
        with patch.object(self.ui.sys, 'stdin', Mock(__iter__=Mock(side_effect=OSError))):
            self.ui.read_commands(self.panel)
        self.ui.GLib.idle_add.assert_called_once_with(self.panel.operation, 'shutdown', priority=0)



class MenuIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.layout = _load('_platformer_radial_layout', SUITE / 'radial_layout.py')
        self.catalog = _load('_platformer_action_catalog', SUITE / 'action_catalog.py')

    def test_all_six_experiences_are_hit_testable_at_multiple_screen_sizes(self):
        self.assertEqual(len(self.layout.MODES), 8)
        self.assertIn('platformer', self.layout.MODES)
        for w, h in ((640, 360), (960, 540), (1024, 600), (1920, 1080)):
            center, circles = self.layout.geometry(w, h)
            self.assertEqual(len(circles), len(self.layout.MODES))
            for i, (x, y, r) in enumerate(circles):
                self.assertEqual(self.layout.hit_index(x / w, y / h, w, h), i)
                self.assertGreaterEqual(x - r, 0)
                self.assertLessEqual(x + r, w)
                self.assertGreaterEqual(y - r, 0)
                self.assertLessEqual(y + r, h)
            self.assertEqual(self.layout.hit_index(center[0] / w, center[1] / h, w, h), len(self.layout.MODES))

    def test_visitor_buttons_follow_six_experiences_and_pose_view(self):
        boxes = self.layout.rectangles(960, 540, visitors=True)
        self.assertEqual(len(boxes), len(self.layout.MODES)+3)
        for index, (a, b, c, d) in enumerate(self.layout.VISITOR_RECTS, len(self.layout.MODES)+1):
            self.assertEqual(self.layout.hit_index((a + c) / 2, (b + d) / 2, visitors=True), index)

    def test_platformer_has_help_restart_audio_and_host_mapping(self):
        root = self.catalog.page('platformer')
        ids = {i['id'] for i in root['items']}
        self.assertTrue({'resume', 'restart', 'help', 'home'} <= ids)
        self.assertTrue(self.catalog.page('platformer', 'help')['help'])
        self.assertIn('audio_retry', self.catalog.actions('platformer'))
        tree = ast.parse((SUITE / 'main.py').read_text(encoding='utf-8'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'backend_mode')
        method.decorator_list = []
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'backend_mode', 'exec'), namespace)
        self.assertEqual(namespace['backend_mode']('platformer'), 'sword')
        for mode in self.layout.MODES:
            self.assertEqual(namespace['backend_mode'](mode), 'sword' if mode in ('platformer','runner','pal') else mode)

    def test_default_dwell_selector_accepts_six_dynamic_modes(self):
        with patch.dict(sys.modules, {'radial_layout': self.layout}):
            gesture = _load('_platformer_gesture_menu', SUITE / 'gesture_menu.py')
        selector = gesture.DwellSelector()
        self.assertEqual(selector.choices, self.layout.MODES[:6])
        selector.set_choices(self.layout.MODES,self.layout.rectangles(),hit_test=self.layout.hit_index)
        self.assertEqual(selector.choices,self.layout.MODES)


if __name__ == '__main__':
    unittest.main()
