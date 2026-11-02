"""Native health UI regression tests; run under Xvfb, never access a camera."""
import contextlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cairo
from . import view
from .engine import LessonSession


class ProjectionTests(unittest.TestCase):
    def test_invalid_and_missing_joints_stay_missing(self):
        points = [None]*17
        points[5] = (.4, .3, .9)
        points[6] = (.6, .3, .9)
        points[7] = (.2, .4, .01)
        points[9] = (math.nan, .4, .9)
        projected = view.project_body(points, (10, 10, 400, 300))
        self.assertEqual(sum(point is not None for point in projected), 2)
        self.assertIsNone(projected[7]);self.assertIsNone(projected[9])
        self.assertTrue(all(point is None for point in view.project_body(None, (0,0,100,100))))

    def test_mirror_changes_only_horizontal_orientation(self):
        points = [None]*17
        for index, value in {5:(.4,.3,.8),6:(.6,.3,.8),7:(.3,.5,.8),11:(.43,.6,.8)}.items():
            points[index] = value
        normal = view.project_body(points, (0,0,400,300), mirror=False)
        mirrored = view.project_body(points, (0,0,400,300), mirror=True)
        for index in (5,6,7,11):
            self.assertAlmostEqual(normal[index][0]+mirrored[index][0], 400)
            self.assertEqual(normal[index][1], mirrored[index][1])

    def test_reference_arm_motion_never_moves_fixed_ankles_or_hips(self):
        from .catalog import reference_pose
        start=view.project_reference(reference_pose('raise',0.),(83,149,425,328))
        raised=view.project_reference(reference_pose('raise',.5),(83,149,425,328))
        for index in (11,12,13,14,15,16):
            self.assertEqual(start[index],raised[index])
        self.assertNotEqual(start[9],raised[9])

    def test_all_control_layouts_fit_and_hit_at_four_resolutions(self):
        for dimensions in ((640,480),(960,540),(1280,720),(1920,1080)):
            for buttons in (view.WELCOME_BUTTONS, view.COURSE_BUTTONS, view.COMPLETED_BUTTONS,view.RETRY_BUTTONS,view.RECOVERY_BUTTONS,view.HISTORY_BUTTONS):
                rectangles = view.normalized_rectangles(buttons, *dimensions)
                for index, (a,b,c,d) in enumerate(rectangles):
                    self.assertTrue(0 <= a < c <= 1 and 0 <= b < d <= 1)
                    self.assertEqual(view.hit_rectangles(rectangles,(a+c)/2,(b+d)/2),index)
                self.assertIsNone(view.hit_rectangles(rectangles,math.nan,.5))
                self.assertIsNone(view.hit_rectangles(rectangles,-1,.5))


class NativeHealthViewTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = []
        self.reports = []
        factory = lambda mode='learn',course_id='baduanjin': LessonSession(mode=mode,course_id=course_id,clock=lambda:self.now)
        self.factory_patch = patch.object(view, 'LessonSession', side_effect=factory)
        self.factory_patch.start();self.addCleanup(self.factory_patch.stop)
        self.clock_patch = patch.object(view.time, 'monotonic', side_effect=lambda:self.now)
        self.clock_patch.start();self.addCleanup(self.clock_patch.stop)
        self.widget = view.HealthView(lambda:self.home.append(True),self.reports.append,
                                      report_directory=self.directory.name, video_enabled=False)
        self.addCleanup(self.widget.destroy)

    def test_enter_and_start_distinguish_mode_choice_from_lesson(self):
        self.assertEqual(self.widget.enter(),('health_start_learn','health_start_follow','health_start_free',
                                              'health_history','health_lessons','health_info','health_home'))
        self.assertTrue(self.widget.gesture_navigation)
        self.widget.action('health_start_learn')
        self.assertEqual(self.widget.current['mode'],'learn')
        self.assertFalse(self.widget.gesture_navigation)
        self.assertIn('health_pause',self.widget.choices)

    def test_gesture_cursor_is_disabled_during_active_exercise(self):
        self.widget.start()
        selection=dict(cursor=(.5,.5),hover=0,progress=.7)
        self.widget.update_selection(selection)
        self.assertIsNone(self.widget.pointer)
        self.widget.pause()
        self.widget.update_selection(selection)
        self.assertEqual(self.widget.pointer,(.5,.5))
        self.assertTrue(self.widget.gesture_navigation)
        self.widget.resume()
        self.assertFalse(self.widget.gesture_navigation)

    def test_discrete_navigation_can_start_follow_mode(self):
        self.widget.enter()
        self.assertEqual(self.widget.navigate('down'),'health_start_follow')
        self.widget.activate()
        self.assertEqual(self.widget.current['mode'],'follow')
        self.widget.action('health_pause')
        self.assertEqual(self.widget.current['status'],'paused')
        self.widget.action('health_pause')
        self.assertEqual(self.widget.current['status'],'running')

    def test_host_gamepad_focus_matches_a_action_during_running_lesson(self):
        self.widget.start('free')
        self.assertFalse(self.widget.gesture_navigation)
        pause_index=self.widget.choices.index('health_pause')
        self.assertTrue(self.widget.set_gamepad_focus(pause_index))
        self.widget.activate()
        self.assertEqual(self.widget.current['status'],'paused')
        self.widget.set_gamepad_focus(self.widget.choices.index('health_next'))
        self.widget.activate()
        self.assertEqual(self.widget.current['lesson_index'],1)
        self.assertEqual(self.widget.current['status'],'paused')
        self.assertFalse(self.widget.set_gamepad_focus(99))

    def test_missing_pose_does_not_disable_manual_exit(self):
        self.widget.enter()
        self.widget.set_availability(False,'摄像头未连接')
        self.widget.focus = self.widget.choices.index('health_home')
        self.widget.activate()
        self.assertEqual(len(self.home),1)

    def test_report_saved_once_with_no_raw_pose_frames(self):
        self.widget.start('free')
        self.now += 2.
        self.widget.pause()
        self.widget._emit_report('test_leave')
        self.widget._emit_report('test_leave_again')
        self.assertTrue(self.widget.wait_for_saves())
        self.widget.close()
        self.assertEqual(len(self.reports),1)
        path=Path(self.reports[0]['report_path'])
        self.assertTrue(path.is_file())
        report=json.loads(path.read_text())
        self.assertEqual(report['mode'],'free')
        self.assertNotIn('points17',report)
        self.assertNotIn('pose_points',report)
        self.assertEqual(len(list(Path(self.directory.name).glob('*.json'))),1)

    def test_save_failure_retains_report_and_returns_home_without_modal(self):
        occupied=Path(self.directory.name)/'occupied';occupied.write_text('not a directory')
        self.widget.report_directory=occupied
        self.widget.start('free')
        with contextlib.redirect_stderr(io.StringIO()):
            self.widget.action('health_home')
            self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(len(self.reports),1)
        self.assertIn('report_save_error',self.reports[0])
        self.assertEqual(len(self.home),1)

    def test_explicit_retry_preserves_snapshot_and_notifies_saved_state_once(self):
        occupied=Path(self.directory.name)/'occupied';occupied.write_text('not a directory')
        self.widget.report_directory=occupied
        self.widget.start('free');self.now+=7.
        with contextlib.redirect_stderr(io.StringIO()):
            self.widget.finish();self.assertTrue(self.widget.wait_for_saves())
        self.assertIn('health_retry_save',self.widget.choices)
        original=self.widget._pending_report.copy()
        self.assertEqual(len(self.reports),1)
        self.assertEqual(self.reports[0]['report_save_state'],'pending')
        # Automatic completion ticks must neither retry IO nor duplicate the
        # pending callback. Clock progress cannot alter the frozen report.
        self.now+=60.
        for _ in range(30):self.widget._emit_report('completed')
        self.assertEqual(len(self.reports),1)
        occupied.unlink()
        self.widget.action('health_retry_save')
        self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(len(self.reports),2)
        self.assertEqual(self.reports[-1]['report_save_state'],'saved')
        self.assertEqual(self.reports[0]['session_id'],self.reports[1]['session_id'])
        persisted=json.loads(Path(self.widget.report_path).read_text())
        self.assertEqual(persisted,original)
        history=self.widget._load_history()
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['seconds'],original['active_seconds'])
        self.assertFalse(history[0]['timer_finished'])
        self.assertNotIn('health_retry_save',self.widget.choices)
        self.widget.action('health_retry_save');self.widget.close()
        self.assertEqual(len(self.reports),2)
        self.assertEqual(len(list(occupied.glob('*.json'))),1)

    def test_repeated_failed_explicit_retry_does_not_duplicate_pending_callback(self):
        occupied=Path(self.directory.name)/'occupied';occupied.write_text('not a directory')
        self.widget.report_directory=occupied
        self.widget.start('free')
        with contextlib.redirect_stderr(io.StringIO()):
            self.widget.finish()
            self.assertTrue(self.widget.wait_for_saves())
            self.widget.action('health_retry_save')
            self.assertTrue(self.widget.wait_for_saves())
            self.widget.action('health_retry_save')
            self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(len(self.reports),1)
        self.assertIsNone(self.widget.report_path)

    def test_failed_home_reenters_recovery_then_retries_original_report_once(self):
        occupied=Path(self.directory.name)/'occupied';occupied.write_text('not a directory')
        self.widget.report_directory=occupied
        self.widget.start('free');self.now+=7.
        with contextlib.redirect_stderr(io.StringIO()):
            self.widget.action('health_home');self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(len(self.home),1)
        self.assertFalse(self.widget.active)
        self.assertEqual(self.widget.current['status'],'paused')
        original=self.widget._pending_report.copy()
        session_id=original['session_id']
        self.assertEqual(original['active_seconds'],7.)
        paused_elapsed=self.widget.current['session_elapsed']
        self.now+=900.
        self.widget.enter()
        self.assertTrue(self.widget.pending_recovery)
        self.assertEqual(self.widget.choices,('health_retry_save','health_home'))
        self.widget.action('health_pause')
        self.assertEqual(self.widget.current['status'],'paused')
        self.widget.start('follow')
        self.assertEqual(self.widget.session.session_id,session_id)
        self.assertEqual(self.widget.session.snapshot()['session_elapsed'],paused_elapsed)
        self.assertEqual(self.widget._pending_report,original)
        occupied.unlink()
        self.widget.action('health_retry_save')
        self.assertTrue(self.widget.wait_for_saves())
        path=self.reports[-1]['report_path']
        self.assertTrue(Path(path).is_file())
        self.assertEqual(json.loads(Path(path).read_text()),original)
        self.assertFalse(self.widget.pending_recovery)
        self.assertIsNone(self.widget.session)
        self.assertIn('health_start_follow',self.widget.choices)
        self.widget.action('health_history')
        self.assertEqual(len(self.widget.history),1)
        self.assertEqual(self.widget.history[0]['seconds'],7)
        self.assertEqual(len(list(occupied.glob('*.json'))),1)
        self.assertEqual([r['report_save_state'] for r in self.reports],['pending','saved'])

    def test_callback_failure_does_not_destroy_durable_report(self):
        def broken(_):raise RuntimeError('test callback')
        self.widget.on_report=broken
        self.widget.start('free')
        with contextlib.redirect_stderr(io.StringIO()):
            self.widget.finish()
            self.assertTrue(self.widget.wait_for_saves())
        self.assertTrue(Path(self.widget.report_path).is_file())

    def test_repeated_observation_does_not_extend_cursor_freshness(self):
        self.widget.start('free')
        points=[None]*17
        for index, point in {5:(.4,.3),6:(.6,.3),7:(.35,.4),8:(.65,.4),
                             9:(.35,.6),10:(.65,.6),11:(.43,.6),12:(.57,.6)}.items():
            points[index]=(*point,.9)
        state=dict(available=True,pid=9,sequence=1,timestamp_seconds=self.now,points17=points)
        self.widget.receive(state)
        first=self.widget.pose_received
        self.now+=.1;self.widget.receive(state)
        self.assertEqual(self.widget.pose_received,first)
        self.now+=1.;self.widget.receive(state)
        self.assertTrue(all(point is None for point in self.widget.current['pose_points']))

    def test_full_frame_render_cache_is_bounded_and_background_immutable(self):
        self.widget.start('follow')
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        for _ in range(45):
            self.now+=.04
            self.widget.current=self.widget.session.snapshot()
            self.widget.paint(cairo.Context(surface),(960,540))
        self.assertEqual(self.widget.background_builds,1)
        self.assertLessEqual(len(self.widget.text_cache),160)
        self.assertLess(self.widget.layout_builds,55)
        self.assertEqual((surface.get_width(),surface.get_height()),(960,540))

    def test_pause_next_repeat_and_completion_keep_choices_valid(self):
        self.widget.start('free')
        self.widget.pause()
        self.widget.action('health_next')
        self.assertEqual(self.widget.current['lesson_index'],1)
        self.assertEqual(self.widget.current['status'],'paused')
        self.widget.action('health_repeat')
        self.widget.resume()
        self.widget.finish()
        self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(self.widget.current['status'],'completed')
        self.assertEqual(self.widget.choices,('health_restart','health_home'))
        self.assertTrue(self.widget.gesture_navigation)

    def test_history_is_empty_until_a_local_course_is_saved(self):
        self.widget.enter();self.widget.action('health_history')
        self.assertTrue(self.widget.history_open)
        self.assertEqual(self.widget.history,[])
        self.assertEqual(self.widget.choices,('health_history_back','health_home'))
        self.widget.action('health_history_back')
        self.widget.start('free');self.now+=3.;self.widget.finish()
        self.assertTrue(self.widget.wait_for_saves())
        self.widget.enter();self.widget.action('health_history')
        self.assertEqual(len(self.widget.history),1)
        self.assertEqual(self.widget.history[0]['mode'],'free')
        self.assertFalse(self.widget.history[0]['timer_finished'])

    def test_history_ignores_invalid_oversized_and_linked_files(self):
        folder=Path(self.directory.name)
        (folder/'malformed.json').write_text('{')
        (folder/'too-large.json').write_bytes(b'x'*(256*1024+1))
        (folder/'foreign.json').write_text(json.dumps(dict(kind='other')))
        payload=dict(kind='baduanjin_practice',mode='free',active_seconds=5,
                     observed_repetitions=1,status='completed',created_at='2026-10-21T00:00:00+00:00')
        external=folder/'external.txt';external.write_text(json.dumps(payload))
        (folder/'linked.json').symlink_to(external)
        self.widget.enter();self.widget.action('health_history')
        self.assertEqual(self.widget.history,[])

    def test_history_parses_only_five_recent_valid_reports(self):
        folder=Path(self.directory.name)
        for index in range(9):
            (folder/(str(index)+'.json')).write_text(json.dumps(dict(kind='baduanjin_practice',mode='follow',active_seconds=50+index,
                                      observed_repetitions=index,status='completed',created_at='2026-10-21T00:00:00+00:00')))
        self.widget.enter();self.widget.action('health_history')
        self.assertEqual(len(self.widget.history),5)

    def test_history_distinguishes_manual_end_from_course_timer_finished(self):
        folder=Path(self.directory.name)
        for index,timer_finished in enumerate((False,True)):
            (folder/(str(index)+'.json')).write_text(json.dumps(dict(kind='baduanjin_practice',mode='follow',active_seconds=50,
                observed_repetitions=1,status='completed',end_reason='course_timer_finished' if timer_finished else 'manual_stop',
                course_timer_finished=timer_finished,created_at='2026-10-21T00:00:00+00:00')))
        self.widget.enter();self.widget.action('health_history')
        self.assertEqual(sorted(item['timer_finished'] for item in self.widget.history),[False,True])


if __name__=='__main__':unittest.main()
