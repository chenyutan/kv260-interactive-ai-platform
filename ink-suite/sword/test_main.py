"""Native lifecycle regressions. Run under Xvfb; synthetic poses only.

    DISPLAY=:98 python3 -m unittest sword.test_main -v

No test opens a camera, contacts the pose service or uses a saved person.
"""
import io
import json
from pathlib import Path
import tempfile
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from sword import main as ui
    GTK_AVAILABLE = bool(ui.Gtk.init_check()[0])
except ModuleNotFoundError as error:
    if error.name not in ('cairo', 'gi'):
        raise
    GTK_AVAILABLE = False


def control(key, *, x=.5, y=.67, valid=True, status='tracking'):
    return dict(x=x, y=y, valid=valid, special=False,
                source_key=('fixture', key, key*.033), status=status)


@unittest.skipUnless(GTK_AVAILABLE, 'Requires native GTK/Cairo and Xvfb')
class FreshDwellTests(unittest.TestCase):
    def test_native_565_presentation_only_matches_the_window_visual(self):
        def visual(depth=16,red=(0xf800,11,5),green=(0x07e0,5,6),blue=(0x001f,0,5)):
            return SimpleNamespace(get_depth=lambda:depth,get_red_pixel_details=lambda:red,
                get_green_pixel_details=lambda:green,get_blue_pixel_details=lambda:blue)
        self.assertTrue(ui.uses_rgb565(visual()))
        self.assertFalse(ui.uses_rgb565(None))
        for item in (visual(24),visual(32),visual(red=(0x7c00,10,5)),visual(blue=(0x1f,1,5))):
            self.assertFalse(ui.uses_rgb565(item))

    def test_fresh_observations_confirm_after_1_2_seconds(self):
        dwell = ui.FreshDwell()
        fired = [dwell.update(control(n), n*.05) for n in range(27)]
        self.assertEqual(sum(fired), 1)
        self.assertFalse(any(fired[:24]))

    def test_replaying_one_frame_never_confirms(self):
        dwell = ui.FreshDwell()
        for n in range(120):
            self.assertFalse(dwell.update(control(1), n*.033))
        self.assertEqual(dwell.progress, 0)

    def test_new_keys_with_invalid_or_held_points_cannot_confirm(self):
        for mode in ('holding', 'lost', 'waiting'):
            dwell = ui.FreshDwell()
            for n in range(100):
                self.assertFalse(dwell.update(control(n, status=mode), n*.033))
            self.assertEqual(dwell.progress, 0)

    def test_gap_and_departure_discard_partial_dwell(self):
        dwell = ui.FreshDwell()
        for n in range(20):
            dwell.update(control(n), n*.05)
        self.assertGreater(dwell.progress, .5)
        self.assertFalse(dwell.update(control(30), 2.))
        self.assertEqual(dwell.progress, 0)
        dwell.update(control(31), 2.05)
        dwell.update(control(32, x=.1), 2.1)
        self.assertEqual(dwell.progress, 0)

    def test_person_switch_cannot_inherit_another_person_start_dwell(self):
        dwell = ui.FreshDwell()
        for n in range(24):
            self.assertFalse(dwell.update(control(n), n*.05))
        self.assertGreater(dwell.progress, .9)
        switched = control(24); switched['continuity_reset'] = True
        self.assertFalse(dwell.update(switched, 1.2))
        self.assertEqual(dwell.progress, 0.)
        self.assertFalse(dwell.update(control(25), 1.25))

    def test_synthetic_mouse_mapping_covers_normalized_area(self):
        for point in ((0.,0.), (1.,1.), (.5,.67), (.2,.8)):
            result = ui.InputTracker().update(ui.demo_state(10., point), 10.)
            self.assertTrue(result['valid'], point)
            self.assertAlmostEqual(result['x'], point[0])
            self.assertAlmostEqual(result['y'], point[1])


@unittest.skipUnless(GTK_AVAILABLE, 'Requires native GTK/Cairo and Xvfb')
class SwordLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.network_patch = patch('urllib.request.OpenerDirector.open',
            side_effect=AssertionError('Offline test attempted network access'))
        self.network = self.network_patch.start()
        self.emit_patch = patch.object(ui, 'emit')
        self.events = self.emit_patch.start()
        args = SimpleNamespace(embed_socket=1, offline_preview=True,
            base='http://127.0.0.1:1', entry_delay=3., fullscreen=False)
        self.panel = ui.SwordPanel(args)
        self.window = ui.Gtk.Window()
        self.window.resize(1024,600)
        self.window.add(self.panel)
        self.window.show_all()
        # Deterministic input clocks; the draw handler still runs for real.
        ui.GLib.source_remove(self.panel.timer)
        self.panel.timer = None

    def tearDown(self):
        with patch.object(ui.Gtk, 'main_quit'):
            self.panel.operation('shutdown')
        self.window.destroy()
        self.pump(.02)
        self.network.assert_not_called()
        self.network_patch.stop()
        self.emit_patch.stop()

    def pump(self, seconds=.08):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            for _ in range(8):
                if not ui.Gtk.events_pending() or time.monotonic() >= deadline:
                    break
                ui.Gtk.main_iteration_do(False)
            time.sleep(.002)

    def activate(self, playing=False):
        self.panel.operation('resume')
        self.panel.entry_until = 0.
        self.panel.last_tick = 100.
        if playing:
            self.panel.game.start(countdown=0)
        return self.panel

    def step(self, now, sample):
        # advance() can restart via dwell; restart must see the same synthetic
        # clock, otherwise the next delta depends on the host's real uptime.
        with patch.object(ui.time,'monotonic',return_value=now), patch.object(self.panel.tracker, 'update', return_value=sample):
            self.panel.advance(now)

    def test_embedded_starts_paused_and_offline_never_polls(self):
        self.assertFalse(self.panel.active)
        self.assertIsNone(self.panel.poll_thread)
        self.assertTrue(self.panel.game.snapshot()['paused'])
        self.panel.advance(100.)
        self.assertEqual(self.panel.game.snapshot()['phase'], 'ready')
        self.pump()
        self.assertGreater(self.panel.frames_drawn, 0)

    def test_pause_clears_latest_controls_and_freezes_clock(self):
        panel = self.activate(playing=True)
        self.step(100.1, control(1))
        before = panel.game.snapshot()
        panel.latest = (ui.demo_state(100.2), 100.2)
        panel.operation('pause')
        panel.advance(300.)
        self.assertEqual(panel.game.snapshot()['time'], before['time'])
        self.assertEqual(panel.game.snapshot()['lives'], before['lives'])
        self.assertIsNone(panel.latest)
        self.assertIsNone(panel.current_state)
        self.assertFalse(panel.control['valid'])
        self.events.assert_any_call('paused', mode='sword',
            game_state='playing', game_clock=before['time'])

    def test_resume_requires_fresh_input_and_three_seconds(self):
        panel = self.activate(playing=True)
        self.step(100.1, control(1))
        before = panel.game.snapshot()['time']
        panel.operation('pause')
        panel.operation('resume')
        panel.entry_until = 0.
        panel.last_tick = 200.
        self.step(200.1, control(2))
        self.assertGreaterEqual(panel.recovery_until, 203.1)
        for n in range(1,30):
            self.step(200.1+n*.1, control(2+n))
        self.assertEqual(panel.game.snapshot()['time'], before)
        self.step(203.2, control(40))
        self.assertGreater(panel.game.snapshot()['time'], before)
        self.assertFalse(panel.recovery_needed)

    def test_short_tracking_gap_freezes_without_recovery_countdown(self):
        panel = self.activate(playing=True)
        self.step(100.1, control(1))
        before = panel.game.snapshot()['time']
        self.step(100.16, control(2, status='holding'))
        self.assertEqual(panel.game.snapshot()['time'], before)
        self.step(100.22, control(3))
        self.assertGreater(panel.game.snapshot()['time'], before)
        self.assertFalse(panel.recovery_needed)

    def test_ui_reports_loss_duration_only_while_tracking_is_missing(self):
        panel = self.activate(playing=True)
        self.step(100.1, control(1))
        self.step(100.2, control(2, status='holding'))
        with patch.object(ui.time, 'monotonic', return_value=100.35):
            self.assertAlmostEqual(panel.ui_state()['tracking_lost_seconds'], .15)
        self.step(100.4, control(3))
        self.assertEqual(panel.ui_state()['tracking_lost_seconds'], 0.)
        panel.invalid_since = 100.2
        panel.operation('pause')
        self.assertEqual(panel.ui_state()['tracking_lost_seconds'], 0.)

    def test_short_healthy_hold_preserves_recovery_but_never_advances_play(self):
        panel = self.activate(playing=True)
        panel.recovery_needed = True
        self.step(100.1, control(1))
        self.assertAlmostEqual(panel.recovery_until, 103.1)
        self.step(102.8, control(2))
        self.step(102.9, control(2, status='holding'))
        self.assertAlmostEqual(panel.recovery_until, 103.1)
        self.assertEqual(panel.game.snapshot()['time'], 0.)
        self.assertEqual(panel.dwell.progress, 0.)
        self.step(103.2, control(3))
        self.assertFalse(panel.recovery_needed)
        self.assertGreater(panel.game.snapshot()['time'], 0.)

    def test_invalid_hold_or_explicit_missing_rearms_recovery(self):
        for fields in (dict(valid=False), dict(observation_missing=True)):
            with self.subTest(fields=fields):
                panel = self.activate(playing=True)
                panel.recovery_needed = True
                self.step(100.1, control(1))
                held = control(1, status='holding'); held.update(fields)
                self.step(100.2, held)
                self.assertEqual(panel.recovery_until, 0.)
                self.step(100.3, control(2))
                self.assertAlmostEqual(panel.recovery_until, 103.3)

    def test_source_change_cannot_inherit_a_recovery_deadline(self):
        panel = self.activate(playing=True)
        panel.recovery_needed = True
        self.step(100.1, control(1))
        switched = control(2); switched['continuity_reset'] = True
        self.step(102.9, switched)
        self.assertAlmostEqual(panel.recovery_until, 105.9)
        self.assertEqual(panel.game.snapshot()['time'], 0.)

    def test_stale_held_key_cannot_complete_recovery(self):
        panel = self.activate(playing=True)
        panel.recovery_needed = True
        self.step(100.1, control(1))
        for n in range(1,65):
            self.step(100.1+n*.05, control(1, status='holding'))
        self.assertEqual(panel.recovery_until, 0.)
        self.assertTrue(panel.recovery_needed)
        self.assertEqual(panel.game.snapshot()['time'], 0.)

    def test_repeated_bad_pose_cannot_restart_recovery_or_advance_play(self):
        panel = self.activate(playing=True)
        panel.args.offline_preview = False
        panel.latest = (ui.demo_state(100.1), 100.1)
        panel.advance(100.1)
        before = panel.game.time
        broken = ui.demo_state(100.15); broken['points17'][10][2] = 0.
        panel.latest = (broken, 100.15)
        panel.advance(100.15)
        panel.advance(100.166)
        self.assertEqual(panel.game.time, before)
        self.assertTrue(panel.control['observation_missing'])
        panel.recovery_needed = True
        panel.recovery_until = 103.1
        panel.advance(100.18)
        panel.advance(100.196)
        self.assertEqual(panel.recovery_until, 0.)
        self.assertEqual(panel.game.time, before)

    def test_long_tracking_loss_requires_recovery_without_life_loss(self):
        panel = self.activate(playing=True)
        self.step(100.1, control(1))
        before = panel.game.snapshot()
        self.step(100.2, control(2, valid=False, status='lost'))
        self.step(101., control(3, valid=False, status='lost'))
        self.assertTrue(panel.recovery_needed)
        self.assertEqual(panel.game.snapshot()['time'], before['time'])
        self.assertEqual(panel.game.snapshot()['lives'], before['lives'])
        self.step(101.1, control(4))
        self.assertGreaterEqual(panel.recovery_until, 104.1)

    def test_short_network_failure_freezes_without_recovery_countdown(self):
        panel = self.activate(playing=True)
        panel.args.offline_preview = False  # Existing panel has no worker.
        self.step(100.1, control(1))
        before = panel.game.snapshot()['time']
        panel.latest = (None, 100.2)
        self.step(100.2, control(2, status='holding'))
        self.assertFalse(panel.recovery_needed)
        self.assertEqual(panel.game.snapshot()['time'], before)
        self.step(100.3, control(3))
        self.assertGreater(panel.game.snapshot()['time'], before)
        self.assertFalse(panel.recovery_needed)

    def test_sustained_network_failure_requires_recovery(self):
        panel = self.activate(playing=True)
        panel.args.offline_preview = False
        panel.latest = (ui.demo_state(100.1), 100.1)
        panel.advance(100.1)
        before = panel.game.snapshot()
        panel.latest = (None, 100.2)
        panel.advance(100.2)
        self.assertFalse(panel.recovery_needed)
        panel.advance(100.9)
        self.assertTrue(panel.recovery_needed)
        self.assertEqual(panel.game.time, before['time'])
        self.assertEqual(panel.game.lives, before['lives'])
        panel.latest = (ui.demo_state(101.), 101.)
        panel.advance(101.)
        self.assertGreaterEqual(panel.recovery_until, 104.)

    def test_real_tracker_recovers_a_brief_failed_poll_without_long_countdown(self):
        panel = self.activate(playing=True)
        panel.args.offline_preview = False
        panel.latest = (ui.demo_state(100.1), 100.1)
        panel.advance(100.1)
        before = panel.game.time
        panel.latest = (None, 100.14)
        panel.advance(100.14)
        panel.advance(100.17)
        self.assertEqual(panel.game.time, before)
        self.assertFalse(panel.recovery_needed)
        panel.latest = (ui.demo_state(100.2), 100.2)
        panel.advance(100.2)
        self.assertGreater(panel.game.time, before)
        self.assertFalse(panel.recovery_needed)
        self.assertEqual(panel.recovery_until, 0.)

    def test_old_latest_slot_is_discarded_after_ui_stall(self):
        panel = self.activate(playing=True)
        panel.args.offline_preview = False
        panel.latest = (ui.demo_state(99.), 99.)
        panel.advance(100.1)
        self.assertIsNone(panel.current_state)
        self.assertEqual(panel.game.snapshot()['time'], 0.)
        self.assertFalse(panel.recovery_needed)
        panel.advance(100.8)
        self.assertTrue(panel.recovery_needed)

    def test_circle_candidate_freezes_without_extra_recovery_countdown(self):
        panel = self.activate(playing=True)
        panel.args.offline_preview = False
        panel.latest = (dict(suite=dict(control_active=True)), 100.1)
        self.step(100.1, control(1))
        self.assertEqual(panel.game.snapshot()['time'], 0.)
        panel.latest = (dict(suite=dict(control_active=False)), 100.2)
        self.step(100.2, control(2))
        self.assertGreater(panel.game.snapshot()['time'], 0.)
        self.assertFalse(panel.recovery_needed)

    def test_ordinary_ticks_do_not_reset_engine_input_history(self):
        panel = self.activate(playing=True)
        self.step(100.1, control(1))
        with patch.object(panel.game, 'pause', wraps=panel.game.pause) as paused:
            for n in range(2,9):
                self.step(100.+n*.1, control(n, x=.2+n*.02))
        paused.assert_not_called()
        self.assertIsNotNone(panel.game._input_target)

    def test_ready_and_settlement_dwell_both_start_countdown(self):
        panel = self.activate()
        for phase in ('ready', 'won', 'lost'):
            panel.game.phase = phase
            if phase == 'won':
                panel.presentation_snapshot(107.)
            panel.dwell.reset()
            for n in range(28):
                self.step(110.+n*.05, control(100+n))
            self.assertEqual(panel.game.snapshot()['phase'], 'countdown')
            self.assertGreater(panel.game.snapshot()['countdown'], 2.8)

    def test_long_tick_diagnostics_are_bounded_and_disabled_without_metrics(self):
        panel = self.activate(playing=True)
        self.step(100.3, control(1))
        self.assertEqual(len(panel.long_ticks), 0)
        panel.metrics_path = Path('/unused/offline-test-metrics.json')
        panel.last_draw_seconds = .012
        panel.last_frame_timing = dict(total_ms=12., compose_ms=8.)
        for n in range(45):
            self.step(101.+n*.2, control(n+2))
        self.assertEqual(len(panel.long_ticks), 32)
        item = panel.long_ticks[-1]
        self.assertAlmostEqual(item['interval_ms'], 200.)
        self.assertEqual(item['previous_draw_ms'], 12.)
        panel.last_frame_timing['total_ms'] = 999.
        self.assertEqual(item['previous_frame']['total_ms'], 12.)
        self.assertIsNone(panel.metrics_writer)

    def test_enabled_frame_diagnostics_separate_composition_and_presentation(self):
        panel = self.activate()
        panel.metrics_path = Path('/unused/offline-test-metrics.json')
        surface = ui.cairo.ImageSurface(ui.cairo.FORMAT_RGB24, 1024, 600)
        panel.draw(panel.canvas, ui.cairo.Context(surface))
        timing = panel.last_frame_timing
        self.assertIsNotNone(timing)
        self.assertAlmostEqual(timing['total_ms'], timing['compose_ms']+
            timing['convert_ms']+timing['present_ms'], places=4)
        self.assertGreaterEqual(timing['thread_cpu_ms'], 0.)
        self.assertLessEqual(len(panel.long_frames), 32)

    def test_32bit_paint_targets_do_not_convert_for_a_565_window(self):
        panel=self.activate()
        visual=SimpleNamespace(get_depth=lambda:16,
            get_red_pixel_details=lambda:(0xf800,11,5),
            get_green_pixel_details=lambda:(0x07e0,5,6),
            get_blue_pixel_details=lambda:(0x001f,0,5))
        widget=SimpleNamespace(get_allocated_width=lambda:4,
            get_allocated_height=lambda:3,get_visual=lambda:visual)
        def compose(width,height,now):
            panel.backbuffer=ui.cairo.ImageSurface(ui.cairo.FORMAT_RGB24,width,height)
            cr=ui.cairo.Context(panel.backbuffer)
            cr.set_source_rgb(13/255,79/255,143/255);cr.paint()
        for fmt in (ui.cairo.FORMAT_RGB24,ui.cairo.FORMAT_ARGB32):
            target=ui.cairo.ImageSurface(fmt,4,3)
            with patch.object(panel,'compose_frame',side_effect=compose), patch.object(panel.presenter,'convert') as convert:
                panel.draw(widget,ui.cairo.Context(target))
            convert.assert_not_called()
            self.assertFalse(panel.presentation_is_rgb565)
            target.flush();panel.backbuffer.flush()
            actual,source=bytes(target.get_data()),bytes(panel.backbuffer.get_data())
            for offset in range(0,len(actual),4):
                self.assertEqual(int.from_bytes(actual[offset:offset+4],sys.byteorder)&0xffffff,
                    int.from_bytes(source[offset:offset+4],sys.byteorder)&0xffffff)

    def test_paint_target_format_can_change_without_visual_changing(self):
        panel=self.activate()
        visual=SimpleNamespace(get_depth=lambda:24)
        widget=SimpleNamespace(get_allocated_width=lambda:4,
            get_allocated_height=lambda:3,get_visual=lambda:visual)
        def compose(width,height,now):
            panel.backbuffer=ui.cairo.ImageSurface(ui.cairo.FORMAT_RGB24,width,height)
        for fmt,expected in ((ui.cairo.FORMAT_RGB16_565,True),
                             (ui.cairo.FORMAT_RGB24,False),
                             (ui.cairo.FORMAT_RGB16_565,True)):
            with patch.object(panel,'compose_frame',side_effect=compose), patch.object(panel.presenter,'convert',side_effect=lambda s:s) as convert:
                panel.draw(widget,ui.cairo.Context(ui.cairo.ImageSurface(fmt,4,3)))
            self.assertEqual(convert.call_count,int(expected))
            self.assertEqual(panel.presentation_is_rgb565,expected)

    def test_winning_presentation_ages_effects_without_touching_engine(self):
        panel = self.activate(playing=True)
        panel.game.phase = 'won'
        panel.game.special_time = 2.3
        panel.game.effects = [dict(kind='special',x=.5,y=.5,age=0.,ttl=2.3,seed=260)]
        before = panel.game.snapshot()
        self.assertEqual(panel.presentation_snapshot(100.)['phase'], 'celebrating')
        halfway = panel.presentation_snapshot(101.)
        self.assertAlmostEqual(halfway['special_time'], 1.3)
        self.assertAlmostEqual(halfway['effects'][0]['age'], 1.)
        self.assertEqual(panel.game.snapshot(), before)
        self.assertEqual(panel.presentation_snapshot(102.21)['phase'], 'won')

    def test_winning_celebration_disables_dwell_until_settlement(self):
        panel = self.activate(playing=True)
        panel.game.phase = 'won'
        for n in range(40):
            self.step(100.+n*.05, control(n))
        self.assertEqual(panel.game.snapshot()['phase'], 'won')
        self.assertEqual(panel.dwell.progress, 0.)
        for n in range(40,75):
            self.step(100.+n*.05, control(n))
        self.assertEqual(panel.game.snapshot()['phase'], 'countdown')

    def test_menu_pauses_winning_celebration_clock(self):
        panel = self.activate(playing=True)
        panel.game.phase = 'won'
        panel.presentation_snapshot(100.)
        with patch.object(ui.time, 'monotonic', return_value=101.):
            panel.operation('pause')
        self.assertEqual(panel.presentation_snapshot(110.)['phase'], 'celebrating')
        with patch.object(ui.time, 'monotonic', return_value=111.):
            panel.operation('resume')
        self.assertEqual(panel.presentation_snapshot(111.5)['phase'], 'celebrating')
        self.assertEqual(panel.presentation_snapshot(112.3)['phase'], 'won')

    def test_click_on_start_starts_but_motion_only_controls_offline(self):
        panel = self.activate()
        self.pump()
        x, y = panel.canvas.get_allocated_width()*.5, panel.canvas.get_allocated_height()*.67
        event = SimpleNamespace(button=1, x=x, y=y)
        panel.click(panel.canvas, event)
        self.assertEqual(panel.game.snapshot()['phase'], 'countdown')
        panel.motion(panel.canvas, event)
        self.assertEqual(panel.mouse, (.5, .67))
        panel.mouse = None
        panel.args.offline_preview = False
        panel.motion(panel.canvas, event)
        self.assertIsNone(panel.mouse)

    def test_menu_and_fullscreen_events_match_host_protocol(self):
        self.activate(playing=True)
        self.panel.request_menu()
        self.assertFalse(self.panel.active)
        self.events.assert_any_call('menu_requested')
        self.panel.request_fullscreen()
        self.events.assert_any_call('fullscreen_requested')

    def test_standalone_menu_button_can_resume(self):
        self.panel.args.embed_socket = None
        self.activate(playing=True)
        self.panel.request_menu()
        self.assertEqual(self.panel.home_button.get_label(), '继续御剑')
        self.panel.request_menu()
        self.assertTrue(self.panel.active)
        self.assertTrue(self.panel.recovery_needed)

    def test_response_from_old_generation_is_discarded(self):
        panel = self.activate()
        def response(*_, **__):
            panel.operation('pause')
            panel.stop.set()
            return io.StringIO(json.dumps(ui.demo_state(10)))
        opener = SimpleNamespace(open=response)
        with patch.object(ui.urllib.request, 'build_opener', return_value=opener):
            try:
                panel.poll()
                self.assertIsNone(panel.latest)
            finally:
                panel.stop.clear()

    def test_paused_polling_opens_no_requests(self):
        with patch.object(self.panel.stop, 'wait', side_effect=lambda _:self.panel.stop.set()):
            try:
                self.panel.poll()
            finally:
                self.panel.stop.clear()
        self.network.assert_not_called()

    def test_eof_and_invalid_commands_shutdown_at_input_priority(self):
        with patch.object(ui.GLib, 'idle_add', wraps=ui.GLib.idle_add) as idle:
            ui.read_commands(self.panel, io.StringIO('bad\n[]\n{"op":"resume"}\n'))
            # One pending dispatch, and EOF overrides the unsent resume.
            self.assertEqual(idle.call_count, 1)
            self.assertEqual(idle.call_args.kwargs['priority'], ui.GLib.PRIORITY_DEFAULT)
        self.pump()
        self.assertTrue(self.panel.stop.is_set())

    def test_standalone_entrypoint_does_not_watch_closed_stdin(self):
        with patch.object(ui.threading, 'Thread') as worker, \
                patch.object(ui.Gtk, 'main'), patch.object(ui.signal, 'signal'), \
                patch.object(ui.sys, 'stdin', io.StringIO('')):
            self.assertEqual(ui.main(['--offline-preview','--entry-delay','0']), 0)
        worker.assert_not_called()

    def test_embedded_entrypoint_starts_host_pipe_reader(self):
        with patch.object(ui.threading, 'Thread') as worker, \
                patch.object(ui.Gtk.Plug, 'new', side_effect=lambda _:ui.Gtk.Window()), \
                patch.object(ui.Gtk, 'main'), patch.object(ui.signal, 'signal'):
            self.assertEqual(ui.main(['--offline-preview','--embed-socket','123']), 0)
        worker.assert_called_once()
        self.assertIs(worker.call_args.kwargs['target'], ui.read_commands)
        worker.return_value.start.assert_called_once()

    def test_shutdown_is_idempotent_and_does_not_resurrect(self):
        panel = self.activate()
        panel.timer = ui.GLib.timeout_add(33, panel.tick)
        panel.operation('shutdown')
        panel.operation('shutdown')
        panel.operation('resume')
        self.assertIsNone(panel.timer)
        self.assertFalse(panel.active)
        self.assertFalse(panel.tick())

    def test_animation_does_not_starve_host_commands(self):
        panel = self.activate()
        panel.timer = ui.GLib.timeout_add(16, panel.tick,
            priority=ui.GLib.PRIORITY_DEFAULT)
        panel.submit_operation('pause')
        self.pump(.3)
        self.assertFalse(panel.active)
        self.assertFalse(panel.stop.is_set())

    def test_paused_child_stops_drawing_but_commands_can_resume_it(self):
        panel=self.activate(playing=True)
        panel.operation('pause')
        # Cross-process embedding can keep the child mapped. The first tick
        # paints its paused notice; following ticks must leave the menu alone.
        self.assertTrue(panel.tick())
        with patch.object(panel.canvas,'queue_draw') as redraw:
            for _ in range(5):self.assertTrue(panel.tick())
            redraw.assert_not_called()
            panel.operation('resume')
            self.assertTrue(panel.tick())
            self.assertGreaterEqual(redraw.call_count,2)
        self.assertTrue(panel.active)

    def test_slow_logic_still_yields_to_paint_and_shutdown_stops_rescheduling(self):
        panel=self.activate(playing=True)
        panel.last_tick=time.monotonic()
        original=panel.tick
        painted=[]
        def slow_tick():
            time.sleep(.027)  # Longer than the nominal 16 ms timer.
            return original()
        def render(cr,*_):
            painted.append(time.monotonic())
            cr.set_source_rgb(.2,.3,.4);cr.paint()
        with patch.object(panel,'tick',side_effect=slow_tick), \
             patch.object(panel.renderer,'draw',side_effect=render):
            panel.timer=ui.GLib.timeout_add(1,panel.scheduled_tick,
                                          priority=ui.GLib.PRIORITY_DEFAULT)
            self.pump(.55)
            self.assertGreaterEqual(len(painted),3,'Busy logic starved GTK paint')
            panel.operation('shutdown')
            self.pump(.05)
            self.assertIsNone(panel.timer)
            self.assertTrue(panel.stop.is_set())

    def test_moderate_render_hitch_does_not_turn_game_into_slow_motion(self):
        panel=self.activate(playing=True)
        for n in range(1,11):
            self.step(100.+n*.15,control(n))
        self.assertAlmostEqual(panel.game.snapshot()['time'],1.5,delta=1/120)
        self.assertEqual(panel.discarded_tick_seconds,0.)
        before=panel.game.snapshot()['time']
        self.step(102.5,control(11))
        self.assertLessEqual(panel.game.snapshot()['time']-before,ui.MAX_DT+1/120)
        self.assertAlmostEqual(panel.discarded_tick_seconds,.75)

    def test_renderer_composes_in_reused_opaque_memory_surface(self):
        targets=[]
        def render(cr,*_):
            targets.append(cr.get_target())
            cr.set_source_rgb(.2,.3,.4);cr.paint()
        with patch.object(self.panel.renderer,'draw',side_effect=render):
            self.panel.canvas.queue_draw();self.pump()
            self.panel.canvas.queue_draw();self.pump()
        self.assertGreaterEqual(len(targets),2)
        self.assertTrue(all(isinstance(surface,ui.cairo.ImageSurface) for surface in targets))
        self.assertTrue(all(surface.get_format()==ui.cairo.FORMAT_RGB24 for surface in targets))
        from render_policy import RenderPolicy
        expected=RenderPolicy.from_environment().layout(self.panel.canvas.get_allocated_width(),self.panel.canvas.get_allocated_height())
        self.assertEqual((targets[0].get_width(),targets[0].get_height()),expected.render_size)
        self.assertEqual(self.panel.backbuffer,targets[-1])

    def test_565_window_converts_only_the_final_surface(self):
        self.pump()
        panel=self.panel
        visual=SimpleNamespace(get_depth=lambda:16,get_red_pixel_details=lambda:(0xf800,11,5),
            get_green_pixel_details=lambda:(0x7e0,5,6),get_blue_pixel_details=lambda:(0x1f,0,5))
        w,h=panel.canvas.get_allocated_width(),panel.canvas.get_allocated_height()
        # Destination format, not just the window visual, decides whether
        # conversion is needed (GDK's image backing may remain RGB24).
        target=ui.cairo.ImageSurface(ui.cairo.FORMAT_RGB16_565,w,h)
        with patch.object(panel.canvas,'get_visual',return_value=visual), \
                patch.object(panel.presenter,'convert',wraps=panel.presenter.convert) as convert:
            panel.draw(panel.canvas,ui.cairo.Context(target))
            convert.assert_called_once_with(panel.backbuffer)
            self.assertEqual(panel.backbuffer.get_format(),ui.cairo.FORMAT_RGB24)
            self.assertTrue(panel.presentation_is_rgb565)
        target=ui.cairo.ImageSurface(ui.cairo.FORMAT_RGB24,w,h)
        with patch.object(panel.canvas,'get_visual',return_value=None), \
                patch.object(panel.presenter,'convert') as convert:
            panel.draw(panel.canvas,ui.cairo.Context(target))
            convert.assert_not_called()
            self.assertFalse(panel.presentation_is_rgb565)

    def test_optional_metrics_record_full_draw_and_do_not_stop_game_on_io_error(self):
        panel=self.activate(playing=True)
        self.step(100.15,control(1));self.pump()
        with tempfile.TemporaryDirectory() as directory:
            panel.metrics_path=Path(directory)/'metrics.json';panel.next_metrics=0.
            panel.write_metrics()
            deadline=time.monotonic()+2.
            while not panel.metrics_path.exists() and time.monotonic()<deadline:
                time.sleep(.005)
            result=json.loads(panel.metrics_path.read_text())
            self.assertGreater(result['frames_drawn'],0)
            self.assertGreater(result['draw_ms_p50'],0.)
            self.assertFalse(result['physical_scanout_measured'])
            self.assertEqual(result['pause_reason'],'')
            panel.next_metrics=0.
            with patch.object(Path,'write_text',side_effect=OSError('test full disk')):
                panel.write_metrics()
                self.assertTrue(panel.metrics_writer.finished.wait(2.))
            panel.next_metrics=0.
            panel.write_metrics()
            self.assertIsNone(panel.metrics_path)
            self.assertFalse(panel.stop.is_set())

    def test_blocked_metrics_storage_does_not_stall_game_ticks_or_shutdown(self):
        panel=self.activate(playing=True)
        entered,release=threading.Event(),threading.Event()
        def sink(_):
            entered.set()
            release.wait(5.)
        panel.metrics_path=Path('unused-blocked-metrics.json')
        panel.metrics_writer=ui.LatestMetricsWriter(panel.metrics_path,sink=sink)
        try:
            panel.write_metrics()
            self.assertTrue(entered.wait(2.))
            with patch.object(panel.canvas,'queue_draw') as redraw:
                for n in range(1,21):
                    with patch.object(ui.time,'monotonic',return_value=100.+n*.02):
                        panel.next_metrics=0.
                        self.assertTrue(panel.tick())
                self.assertEqual(redraw.call_count,20)
            self.assertGreater(panel.game.time,.3)
            self.assertEqual(panel.metrics_writer.written,0)
            self.assertGreater(panel.metrics_writer.replaced,0)
            panel.operation('shutdown')
            self.assertFalse(panel.metrics_writer.finished.is_set())
            self.assertTrue(panel.stop.is_set())
        finally:
            release.set()
            panel.metrics_writer.close()
            self.assertTrue(panel.metrics_writer.finished.wait(2.))

    def test_render_failure_exits_once_instead_of_repeating(self):
        self.activate()
        with patch.object(self.panel.renderer, 'draw', side_effect=RuntimeError('test failure')):
            self.panel.canvas.queue_draw()
            self.pump()
        self.assertTrue(self.panel.stop.is_set())
        self.assertEqual(self.panel.fatal_error, 'test failure')
        errors = [call for call in self.events.call_args_list if call.args[0] == 'error']
        self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
