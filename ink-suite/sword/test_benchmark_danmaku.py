"""No GUI/camera tests of safety, workload accounting and benchmark fixtures."""
from contextlib import redirect_stderr
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock

from sword.benchmark_danmaku import (BoundedSamples, DanmakuPoseSource, arguments,
                                     isolated_display, summarize, StressPopulation, counts,
                                     validate_measurement,read_display_number,DiagnosticFrameClock,
                                     read_host_ready,NativeEmbedHost,embed_is_ready,EMBED_HOST)
from sword.benchmark_danmaku import diagnostic_embedded_frame_sync
from sword import engine
from sword.input import InputTracker


class BenchmarkDanmakuTests(unittest.TestCase):
    def test_frame_sync_diagnostic_is_explicit_and_requires_embed(self):
        self.assertFalse(arguments(['--output', 'unused']).disable_embedded_frame_sync)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            arguments(['--output', 'unused', '--disable-embedded-frame-sync'])
        args = arguments(['--output', 'unused', '--embed', '--disable-embedded-frame-sync'])
        self.assertTrue(args.disable_embedded_frame_sync)
        window, x11 = Mock(), Mock()
        self.assertFalse(diagnostic_embedded_frame_sync(window, x11=x11)['applied'])
        window.get_realized.assert_not_called()
        x11.X11Window.set_frame_sync_enabled.assert_not_called()

    def test_frame_sync_diagnostic_only_modifies_realized_native_window(self):
        window, x11, native = Mock(), Mock(), object()
        window.get_realized.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'realized'):
            diagnostic_embedded_frame_sync(window, True, x11=x11)
        x11.X11Window.set_frame_sync_enabled.assert_not_called()
        window.get_realized.return_value = True
        window.get_window.return_value = native
        x11.X11Window.get_xid.return_value = 123
        report = diagnostic_embedded_frame_sync(window, True, x11=x11)
        x11.X11Window.set_frame_sync_enabled.assert_called_once_with(native, False)
        self.assertTrue(report['applied'])
        self.assertEqual(report['native_xid'], 123)

    def test_embedding_is_explicit_and_host_script_compiles(self):
        self.assertFalse(arguments(['--output','unused.json']).embed)
        self.assertTrue(arguments(['--output','unused.json','--embed']).embed)
        compile(EMBED_HOST,'private-embed-host','exec')

    def test_host_readiness_accepts_split_private_line_and_rejects_invalid_id(self):
        with patch('select.select',return_value=([42],[],[])), \
                patch('os.read',side_effect=[b'{"socket_id":17,',b'"host_pid":25}',b'\n']):
            self.assertEqual(read_host_ready(42)['socket_id'],17)
        with patch('select.select',return_value=([42],[],[])), \
                patch('os.read',return_value=b'{"socket_id":0,"host_pid":25}\n'):
            with self.assertRaisesRegex(RuntimeError,'invalid identifiers'):
                read_host_ready(42)

    def test_host_readiness_failure_closes_only_private_subprocess(self):
        process=Mock()
        process.poll.return_value=None
        args=arguments(['--output','unused.json','--embed'])
        with patch('pathlib.Path.open',return_value=Mock()),patch('pathlib.Path.mkdir'), \
                patch('subprocess.Popen',return_value=process), \
                patch('sword.benchmark_danmaku.read_host_ready',side_effect=RuntimeError('readiness timed out')):
            with self.assertRaisesRegex(RuntimeError,'readiness timed out'):
                NativeEmbedHost(args)
        process.stdin.write.assert_called_once_with(b'quit\n')
        process.wait.assert_called_once_with(timeout=3)
        process.stdout.close.assert_called_once()

    def test_host_forced_cleanup_is_bounded_and_idempotent(self):
        import subprocess
        host=NativeEmbedHost.__new__(NativeEmbedHost)
        host.process=process=Mock()
        host.log=log=Mock()
        process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('host',3),subprocess.TimeoutExpired('host',3),0]
        host.close()
        host.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count,3)
        log.close.assert_called_once()

    def test_foreign_embed_requires_mapped_plug_and_canvas_before_resume(self):
        window,panel=Mock(),Mock()
        window.get_embedded.return_value=True
        window.get_mapped.return_value=True
        panel.canvas.get_mapped.return_value=False
        panel.canvas.get_allocated_width.return_value=1024
        panel.canvas.get_allocated_height.return_value=508
        self.assertFalse(embed_is_ready(window,panel))
        panel.canvas.get_mapped.return_value=True
        self.assertTrue(embed_is_ready(window,panel))
        window.get_embedded.return_value=False
        self.assertFalse(embed_is_ready(window,panel))

    @staticmethod
    def scheduler_panel():
        canvas = Mock()
        canvas.add_tick_callback.return_value = 91
        canvas.get_realized.return_value = True
        stop = Mock()
        stop.is_set.return_value = False
        return SimpleNamespace(canvas=canvas,stop=stop,active=True,timer=17,
                               tick=Mock(return_value=True))

    def test_default_scheduler_leaves_production_timer_untouched(self):
        panel,glib = self.scheduler_panel(),Mock()
        scheduler = DiagnosticFrameClock(panel,glib)
        self.assertEqual(panel.timer,17)
        panel.canvas.add_tick_callback.assert_not_called()
        glib.MainContext.default.assert_not_called()
        self.assertFalse(scheduler.report()['enabled'])

    def test_frame_clock_request_does_not_advance_game_or_replace_timer(self):
        panel,glib = self.scheduler_panel(),Mock()
        scheduler = DiagnosticFrameClock(panel,glib,'request')
        self.assertTrue(scheduler.dispatch(panel.canvas,None))
        panel.canvas.queue_draw.assert_called_once()
        panel.tick.assert_not_called()
        self.assertEqual(panel.timer,17)
        panel.active=False
        self.assertTrue(scheduler.dispatch(panel.canvas,None))
        panel.canvas.queue_draw.assert_called_once()
        scheduler.close()
        panel.canvas.remove_tick_callback.assert_called_once_with(91)

    def test_frame_clock_drive_replaces_only_test_timer_and_cleans_up(self):
        panel,glib = self.scheduler_panel(),Mock()
        scheduler = DiagnosticFrameClock(panel,glib,'drive')
        self.assertIsNone(panel.timer)
        glib.MainContext.default().find_source_by_id.assert_called_once_with(17)
        glib.MainContext.default().find_source_by_id().destroy.assert_called_once()
        self.assertTrue(scheduler.dispatch(panel.canvas,None))
        panel.tick.assert_called_once()
        panel.canvas.queue_draw.assert_not_called()
        self.assertEqual(scheduler.report()['frame_clock_driven_ticks'],1)
        scheduler.close()
        panel.canvas.remove_tick_callback.assert_called_once_with(91)

    def test_scheduler_stops_with_panel_and_options_are_mutually_exclusive(self):
        panel,glib = self.scheduler_panel(),Mock()
        scheduler = DiagnosticFrameClock(panel,glib,'request')
        panel.stop.is_set.return_value=True
        self.assertFalse(scheduler.dispatch(panel.canvas,None))
        self.assertIsNone(scheduler.callback_id)
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            arguments(['--output','unused.json','--frame-clock-request','--frame-clock-drive'])

    def test_displayfd_reader_waits_for_separately_written_newline(self):
        with patch('select.select',return_value=([42],[],[])), \
                patch('os.read',side_effect=[b'1',b'2',b'\n']) as reader:
            self.assertEqual(read_display_number(42),':12')
            self.assertEqual(reader.call_count,3)

    def test_displayfd_eof_without_newline_is_not_false_readiness(self):
        with patch('select.select',return_value=([42],[],[])), \
                patch('os.read',side_effect=[b'1',b'']):
            with self.assertRaisesRegex(RuntimeError,'terminating newline'):
                read_display_number(42)

    def test_displayfd_partial_line_timeout_is_explicit_failure(self):
        with patch('select.select',side_effect=[([42],[],[]),([],[],[])]), \
                patch('os.read',return_value=b'1'):
            with self.assertRaisesRegex(RuntimeError,'finish its display-number line'):
                read_display_number(42)

    @staticmethod
    def complete_samples():
        object_counts = dict(bullets=180,shots=72,entities=32,enemies=20,bosses=1,effects=40)
        draws,ticks,logic = [],[],[]
        for i in range(30):
            at = 10.+i*.1
            draws.append(dict(at=at,seconds=.01,cpu_seconds=.009,counts=object_counts))
            ticks.append(dict(at=at,seconds=.002,interval=.1,game_time=at-10,
                              counts=object_counts,phase='playing',pause_reason=''))
            logic.append(dict(at=at,seconds=.001,fixture_seconds=.0002))
        return draws,ticks,logic

    def test_zero_draws_cannot_pass_even_when_engine_and_timer_complete(self):
        _,ticks,logic = self.complete_samples()
        result,failures = validate_measurement([],ticks,logic,10.,3.)
        self.assertFalse(result['passed'])
        self.assertTrue(any('Fewer than 5' in item for item in failures))
        self.assertTrue(any('starvation' in item for item in failures))

    def test_drawing_only_first_second_cannot_hide_later_starvation(self):
        draws,ticks,logic = self.complete_samples()
        result,failures = validate_measurement(draws[:10],ticks,logic,10.,3.)
        self.assertFalse(result['passed'])
        self.assertEqual([b['second'] for b in result['seconds'] if b['draws']==0],[1,2])
        self.assertTrue(any('starvation' in item for item in failures))

    def test_nonfinite_measurement_and_stalled_active_clock_fail(self):
        draws,ticks,logic = self.complete_samples()
        draws[0]['seconds'] = float('nan')
        for row in ticks:
            row['game_time'] = 0.
        result,failures = validate_measurement(draws,ticks,logic,10.,3.)
        self.assertFalse(result['passed'])
        self.assertTrue(any('Non-finite' in item for item in failures))
        self.assertTrue(any('did not advance' in item for item in failures))
        self.assertEqual(summarize(draws,ticks,logic)['draw_ms']['invalid_count'],1)

    def test_terminal_screen_stops_game_clock_but_still_draws_each_second(self):
        draws,ticks,logic = self.complete_samples()
        for row in ticks:
            row.update(phase='won',game_time=60.)
        result,failures = validate_measurement(draws,ticks,logic,10.,3.)
        self.assertTrue(result['passed'],failures)
        self.assertEqual(summarize(draws,ticks,logic)['drawn_object_counts']['effects']['mean'],40)

    def test_real_draws_and_active_progress_pass_validity_gate(self):
        draws,ticks,logic = self.complete_samples()
        result,failures = validate_measurement(draws,ticks,logic,10.,3.)
        self.assertTrue(result['passed'],failures)

    def test_default_does_not_use_inherited_physical_display(self):
        args = arguments(['--output','unused.json'])
        self.assertIsNone(args.physical_display)
        with patch.dict(os.environ, {'DISPLAY':':0'}), patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'no physical-display fallback'):
                with isolated_display(args):
                    self.fail('Missing Xvfb must not fall back to :0')
            self.assertEqual(os.environ['DISPLAY'], ':0')

    def test_physical_display_requires_explicit_option_and_restores_environment(self):
        args = arguments(['--output','unused.json','--physical-display',':7'])
        with patch.dict(os.environ, {'DISPLAY':':0'}):
            with isolated_display(args) as info:
                self.assertEqual(os.environ['DISPLAY'], ':7')
                self.assertTrue(info['physical_display_requested'])
            self.assertEqual(os.environ['DISPLAY'], ':0')

    def test_bounded_duration_rejects_nan_infinity_short_and_unbounded_run(self):
        for value in ('nan','inf','14.9','301'):
            with self.subTest(value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments(['--output','unused.json','--seconds',value])

    def test_trace_storage_has_fixed_capacity_and_counts_loss(self):
        records = BoundedSamples(limit=3)
        for number in range(100):
            records.append(number)
        self.assertEqual(list(records.rows), [97,98,99])
        self.assertEqual(records.dropped,97)

    def test_input_is_deterministic_time_quantized_and_physically_trackable(self):
        source = DanmakuPoseSource(rate=30)
        tracker = InputTracker()
        first = source.state(source.origin+.034)
        duplicate = source.state(source.origin+.045)
        self.assertEqual(first, duplicate)
        for step in range(90):
            now = source.origin+step/30
            frame = source.state(now)
            control = tracker.update(frame,now)
            self.assertEqual(control['status'],'tracking')
            self.assertGreater(control['y'],.75)
            self.assertLess(control['y'],.92)

    def test_fps_uses_draw_cadence_not_reciprocal_of_draw_cost(self):
        draws = [dict(at=t,seconds=.005,cpu_seconds=.004) for t in (1.,1.05,1.10)]
        count = dict(bullets=180,shots=72,entities=32,enemies=20,bosses=1,effects=40)
        ticks = [dict(seconds=.002,interval=.05,counts=count,phase='playing',pause_reason='')]
        logic = [dict(seconds=.001,fixture_seconds=.0002)]
        report = summarize(draws,ticks,logic)
        self.assertAlmostEqual(report['measured_draw_fps'],20.)
        self.assertAlmostEqual(report['draw_ms']['mean'],5.)
        self.assertAlmostEqual(report['logic_ms']['mean'],1.)
        self.assertEqual(report['peak_objects']['bullets'],180)

    def test_empty_results_cannot_report_fake_fps(self):
        report = summarize([],[],[])
        self.assertEqual(report['measured_draw_fps'],0.)
        self.assertEqual(report['draw_ms']['count'],0)
        self.assertEqual(report['peak_objects']['bullets'],0)

    def test_stress_population_reaches_real_budgets_and_stays_bounded(self):
        game = engine.Game()
        game.start(countdown=0)
        fixture = StressPopulation(engine)
        control = dict(x=.5,y=.86,valid=True,status='tracking',special=False)
        for _ in range(90):
            fixture.apply(game)
            before = counts(game)
            self.assertEqual(before, dict(bullets=engine.MAX_BULLETS, shots=engine.MAX_SHOTS,
                entities=engine.MAX_ENTITIES, enemies=engine.MAX_ENEMIES,
                bosses=1, effects=engine.MAX_EFFECTS))
            game.update(1/30,control)
            after = counts(game)
            for field in ('bullets','shots','entities','enemies','bosses','effects'):
                self.assertLessEqual(after[field], before[field])
            self.assertEqual(game.phase,'playing')
        self.assertGreater(game.time,2.9)
        self.assertEqual(fixture.description()['applications'],90)

    def test_collision_stress_really_hits_enemies_and_measures_surviving_shots(self):
        game = engine.Game()
        game.start(countdown=0)
        fixture = StressPopulation(engine,collisions=True)
        fixture.apply(game)
        initial_shots = len(game.shots)
        initial_health = {e['id']:e['health'] for e in game.entities
                          if e['kind'] in ('enemy','elite','boss')}
        game.update(1/30,dict(x=.5,y=.86,valid=True,status='tracking',special=False))
        damaged = [e for e in game.entities if e['id'] in initial_health
                   and e['health'] < initial_health[e['id']]]
        self.assertTrue(damaged, 'Stress must exercise real damage, not be rejected by global AABB')
        self.assertLess(len(game.shots),initial_shots)
        self.assertEqual(len(game.effects),40)
        fixture.apply(game)
        self.assertEqual(len(game.shots),engine.MAX_SHOTS)
        self.assertTrue(fixture.description()['collisions'])

    def test_collision_option_cannot_modify_normal_level_rules(self):
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            arguments(['--scene','level','--stress-collisions','--output','unused.json'])

    def test_normal_level_construction_and_input_do_not_alter_game_rules(self):
        game = engine.Game()
        game.start(countdown=0)
        source = DanmakuPoseSource()
        tracker = InputTracker()
        for step in range(30):
            now = source.origin+step/30
            game.update(1/30,tracker.update(source.state(now),now))
        self.assertEqual(game.duration,75.)
        self.assertLessEqual(game.lives,5)
        self.assertFalse(game._boss_spawned)

    def test_level_strategy_reaches_boss_and_completes_through_real_input_tracker(self):
        for fps in (30,60):
            with self.subTest(fps=fps):
                game = engine.Game()
                game.start(countdown=0)
                source = DanmakuPoseSource(strategy='level')
                tracker = InputTracker()
                spells = 0
                peak_bosses = 0
                for step in range(76*fps):
                    now = source.origin+step/fps
                    control = tracker.update(source.state(now),now)
                    spells += bool(control['special'])
                    game.update(1/fps,control)
                    source.observe(game)
                    peak_bosses = max(peak_bosses,counts(game)['bosses'])
                    if game.phase != 'playing':
                        break
                self.assertEqual(game.duration,75.)
                self.assertEqual(peak_bosses,1)
                self.assertGreater(spells,0)
                self.assertEqual(game.phase,'won')
                self.assertGreater(game.lives,0)
                self.assertLessEqual(game.lives,5)


if __name__ == '__main__':
    unittest.main()
