"""Native course interaction audit using fake media and synthetic clocks only."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import patch

import cairo

from .engine import LessonSession
from .media_flow import LessonVideoFlow
from .test_media_flow import FakeClock, FakePlayer, FakeLibrary
from .view import HealthView, normalized_rectangles, hit_rectangles


class Player(FakePlayer):
    def frame(self):return None
    def close(self):self.stop()


class CourseInteractionTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = []
        self.player = Player()
        self.widgets = []
        factory = lambda mode='learn',course_id='baduanjin': LessonSession(mode, clock=self.clock, course_id=course_id)
        self.factory = patch('health.view.LessonSession', side_effect=factory)
        self.factory.start();self.addCleanup(self.factory.stop)
        self.time_patch = patch('health.view.time.monotonic', side_effect=self.clock)
        self.time_patch.start();self.addCleanup(self.time_patch.stop)
        self.addCleanup(self.destroy_widgets)

    def destroy_widgets(self):
        for widget in self.widgets:widget.destroy()

    def view(self, video=False, course='baduanjin'):
        widget = HealthView(lambda:self.home.append(True), report_directory=self.folder.name,
                            video_enabled=video, video_player=self.player,
                            video_library=FakeLibrary(), course_id=course)
        self.widgets.append(widget)
        widget.enter(course)
        return widget

    def test_twenty_four_forms_reachable_on_two_pages_by_existing_focus_api(self):
        widget = self.view(course='taichi')
        widget.action('health_lessons')
        first = [name for name in widget.choices if name.startswith('health_pick_')]
        self.assertEqual(len(first), 12)
        widget.set_gamepad_focus(widget.choices.index('health_lessons_next'));widget.activate()
        second = [name for name in widget.choices if name.startswith('health_pick_')]
        self.assertEqual(len(second), 12)
        self.assertEqual(len(set(first+second)), 24)
        widget.set_gamepad_focus(widget.choices.index('health_pick_finish'));widget.activate()
        self.assertFalse(widget.lesson_picker_open)
        self.assertEqual(widget.selected_lesson_index, 23)
        widget.action('health_start_free')
        self.assertEqual(widget.current['lesson_id'], 'finish')
        self.assertEqual(widget.current['lesson_count'], 24)

    def test_all_picker_rectangles_are_inside_stage_and_hit_tested(self):
        widget = self.view(course='taichi')
        widget.action('health_lessons')
        for page in range(2):
            for size in ((640, 360), (960, 540), (1280, 720), (640, 480)):
                rectangles = normalized_rectangles(widget.buttons, *size)
                for index, (a,b,c,d) in enumerate(rectangles):
                    self.assertTrue(0 <= a < c <= 1 and 0 <= b < d <= 1)
                    self.assertEqual(hit_rectangles(rectangles, (a+c)/2, (b+d)/2), index)
            widget.action('health_lessons_next')

    def test_picker_freezes_practice_and_back_restores_running_state(self):
        widget = self.view();widget.start('free')
        self.clock.advance(3.)
        widget.action('health_lessons')
        self.assertEqual(widget.session.status, 'paused')
        before = widget.session.session_elapsed
        self.clock.advance(120.)
        widget.receive({});widget.tick()
        self.assertEqual(widget.session.session_elapsed, before)
        widget.back()
        self.assertEqual(widget.current['status'], 'running')
        self.clock.advance(1.)
        self.assertEqual(widget.session.snapshot()['session_elapsed'], before+1.)

    def test_picker_back_preserves_preexisting_pause(self):
        widget = self.view();widget.start('free');widget.pause()
        widget.action('health_lessons');widget.back()
        self.assertEqual(widget.current['status'], 'paused')

    def test_selected_lesson_from_running_practice_resumes_without_videos(self):
        widget = self.view();widget.start('free')
        widget.action('health_lessons');widget.action('health_pick_bend')
        self.assertEqual(widget.current['lesson_id'], 'bend')
        self.assertEqual(widget.current['status'], 'running')

    def test_selected_lesson_from_paused_practice_remains_paused(self):
        widget = self.view();widget.start('free');widget.pause()
        widget.action('health_lessons');widget.action('health_pick_bend')
        self.assertEqual(widget.current['lesson_id'], 'bend')
        self.assertEqual(widget.current['status'], 'paused')

    def test_pick_during_video_opens_matching_clip_and_preserves_running_return(self):
        widget = self.view(video=True);widget.start('free')
        widget.action('health_lessons');widget.action('health_pick_bow')
        self.assertEqual(self.player.path, '/local/bow.webm')
        self.assertEqual(widget.current['phase'], 'video')
        widget.action('health_video_skip')
        self.assertEqual(widget.current['lesson_id'], 'bow')
        self.assertEqual(widget.current['status'], 'running')

    def test_pick_during_paused_video_does_not_resume_practice_after_skip(self):
        widget = self.view(video=True);widget.start('free');widget.pause()
        widget.action('health_lessons');widget.action('health_pick_bow')
        widget.action('health_video_skip')
        self.assertEqual(widget.current['status'], 'paused')

    def test_picker_reentry_does_not_lose_original_resume_intent(self):
        widget = self.view();widget.start('free')
        widget.action('health_lessons');widget.action('health_lessons');widget.back()
        self.assertEqual(widget.current['status'], 'running')

    def test_menu_during_picker_cannot_resume_hidden_practice_clock(self):
        widget = self.view();widget.start('free');self.clock.advance(2.)
        widget.action('health_lessons')
        before = widget.session.snapshot()['session_elapsed']
        for action in ('health_pause', 'pause', 'play', 'resume', 'health_next'):
            widget.action(action)
            self.assertEqual(widget.session.status, 'paused')
        self.clock.advance(90.)
        self.assertEqual(widget.session.snapshot()['session_elapsed'], before)
        widget.back()
        self.assertEqual(widget.current['status'], 'running')
        self.assertEqual(widget.session.snapshot()['session_elapsed'], before)

    def test_unknown_selection_does_not_close_picker_or_replace_lesson(self):
        widget = self.view();widget.action('health_lessons')
        with self.assertRaises(ValueError):widget.action('health_pick_made_up')
        self.assertTrue(widget.lesson_picker_open)
        self.assertEqual(widget.selected_lesson_index, 0)

    def test_back_from_history_stays_in_course(self):
        widget = self.view();widget.action('health_history');widget.back()
        self.assertFalse(widget.history_open)
        self.assertTrue(widget.active)
        self.assertEqual(self.home, [])

    def test_back_is_pause_then_welcome_then_exit(self):
        widget = self.view();widget.start('free');self.clock.advance(4.)
        widget.back();self.assertEqual(widget.current['status'], 'paused')
        widget.back();self.assertTrue(widget.wait_for_saves());self.assertIsNone(widget.session)
        self.assertEqual(self.home, [])
        self.assertEqual(len(list(Path(self.folder.name).glob('*.json'))), 1)
        self.assertIs(widget.back(), True)
        self.assertEqual(self.home, [True])

    def test_return_to_course_menu_freezes_and_saves_original_statistics(self):
        widget = self.view();widget.start('free');self.clock.advance(7.)
        widget.pause();widget.back()
        self.assertTrue(widget.wait_for_saves())
        report = json.loads(next(Path(self.folder.name).glob('*.json')).read_text(encoding='utf8'))
        self.assertEqual(report['active_seconds'], 7.)
        self.assertEqual(report['exit_reason'], 'returned_to_course_menu')
        self.clock.advance(400.)
        widget.action('health_start_follow')
        self.assertEqual(widget.current['session_elapsed'], 0.)

    def test_save_failure_at_course_menu_keeps_frozen_report_and_allows_explicit_home(self):
        widget = self.view();widget.start('free');widget.pause()
        with contextlib.redirect_stderr(io.StringIO()), patch.object(widget.session, 'save_report', side_effect=OSError('fixture full disk')):
            widget.back()
            self.assertTrue(widget.wait_for_saves())
            self.assertTrue(widget.pending_recovery)
            original = dict(widget._pending_report)
            self.assertIs(widget.action('health_home'), True)
        self.assertEqual(self.home, [True])
        self.assertEqual(widget._pending_report, original)
        widget.enter();self.assertTrue(widget.pending_recovery)
        widget.action('health_retry_save')
        self.assertTrue(widget.wait_for_saves())
        self.assertFalse(widget.pending_recovery)
        self.assertIsNone(widget.session)

    def test_normal_home_exit_stops_session_clock(self):
        widget = self.view();widget.start('free');self.clock.advance(5.)
        self.assertIs(widget.action('health_home'), True)
        before = widget.session.snapshot()['session_elapsed']
        self.clock.advance(100.)
        self.assertEqual(widget.session.snapshot()['session_elapsed'], before)

    def test_hidden_course_pauses_without_automatic_resume_or_unseen_progress(self):
        widget = self.view(video=True);widget.start('free')
        widget.action('health_video_skip');self.clock.advance(3.)
        widget.unmapped();self.assertEqual(widget.current['status'],'paused')
        before = widget.session.snapshot()['session_elapsed']
        self.clock.advance(200.)
        self.assertEqual(widget.session.snapshot()['session_elapsed'], before)
        self.assertEqual(widget.current['status'],'paused')

    def test_phase_completion_keeps_a_valid_visible_gamepad_focus(self):
        widget = self.view();widget.start('follow')
        for _ in range(9):widget.action('health_next')
        widget.focus = widget.choices.index('health_home')
        self.clock.advance(widget.session.lesson['practice_seconds']+1.)
        widget.receive({})
        self.assertEqual(widget.current['status'], 'completed')
        self.assertIn(widget.focus, range(len(widget.choices)))
        self.assertEqual(widget._focus_choice(), 'health_home')

    def test_picker_native_paint_is_bounded_and_reuses_layouts(self):
        widget = self.view(course='taichi');widget.action('health_lessons')
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 960, 540)
        widget.paint(cairo.Context(surface));before = widget.layout_builds
        for _ in range(30):widget.paint(cairo.Context(surface))
        self.assertEqual(widget.layout_builds, before)
        self.assertLessEqual(len(widget.text_cache), 160)

    def test_actual_host_navigation_reaches_picker_page_two_starts_and_backs_out(self):
        from gamepad.host import HostControls
        from gamepad.navigation import Navigation
        widget = self.view(course='taichi')
        host = HostControls.__new__(HostControls)
        host.suite = SimpleNamespace(stop=threading.Event(), health_active=True,
                                     health_view=widget, control_mode='gamepad', menu_level='health')
        reader = SimpleNamespace(data={})
        reader.snapshot = lambda:reader.data
        host.reader = reader
        host.pointer = SimpleNamespace(update=lambda *args, **kwargs:{})
        host.navigation = Navigation();host.dialog = lambda:None
        host.connected=True;host.name='QA synthetic gamepad';host.closed=False
        host.last=self.clock.now;host.navigation_until=0.;host.sequence=0

        def step(button=None):
            self.clock.advance(.05)
            reader.data = dict(connected=True, name='QA synthetic gamepad',
                               pressed=() if button is None else (button,),
                               buttons=() if button is None else (button,), released=(), axes={})
            self.assertTrue(host.tick())

        def route(choice):
            step()  # Initialize the real host's current navigation page.
            target=widget.choices.index(choice)
            queue=deque([(copy.deepcopy(host.navigation), ())]);seen=set()
            result=None
            while queue:
                navigation, path=queue.popleft()
                if navigation.index==target:result=path;break
                if navigation.index in seen:continue
                seen.add(navigation.index)
                for button in ('LEFT','RIGHT','UP','DOWN'):
                    candidate=copy.deepcopy(navigation);candidate.direction=None
                    candidate.update(dict(pressed=(button,),axes={}), widget.choices, widget.rectangles,
                                     ('health',widget.course_id,widget.choices), self.clock.now+100.)
                    queue.append((candidate,path+(button,)))
            self.assertIsNotNone(result, 'Unreachable controller action: '+choice)
            for button in result:step(button);step()
            self.assertEqual(widget._focus_choice(),choice)
            step('A');step()

        route('health_lessons');self.assertTrue(widget.lesson_picker_open)
        route('health_lessons_next');self.assertEqual(widget.lesson_page,1)
        route('health_pick_needle');self.assertEqual(widget.selected_lesson_index,18)
        route('health_start_free');self.assertEqual(widget.current['lesson_id'],'needle')
        step('B');step();self.assertEqual(widget.current['status'],'paused')
        step('B');step();self.assertTrue(widget.wait_for_saves());self.assertIsNone(widget.session)
        reports=[json.loads(path.read_text(encoding='utf8')) for path in Path(self.folder.name).glob('*.json')]
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['course_id'],'taichi')
        self.assertEqual(reports[0]['current_lesson_id'],'needle')
        self.assertEqual(reports[0]['record_state'],'final')
        step('B');self.assertEqual(self.home,[True])


class PauseReturnFlowTests(unittest.TestCase):
    def flow(self):
        clock = FakeClock();session = LessonSession('free', clock=clock);session.start()
        player = FakePlayer();flow = LessonVideoFlow(session, player, FakeLibrary())
        flow.snapshot();flow.action('video_skip')
        return clock, session, player, flow

    def test_rewatch_from_paused_practice_returns_paused_on_eos(self):
        clock, session, player, flow = self.flow()
        clock.advance(3.);flow.action('pause');flow.action('video_rewatch')
        clock.advance(200.);player.state = 'ended';shot = flow.snapshot()
        self.assertEqual(shot['status'], 'paused')
        self.assertEqual(shot['elapsed'], 3.)
        clock.advance(20.);self.assertEqual(flow.snapshot()['elapsed'], 3.)

    def test_rewatch_from_running_practice_returns_running_on_skip(self):
        clock, session, player, flow = self.flow()
        clock.advance(3.);flow.action('video_rewatch');flow.action('video_skip')
        self.assertEqual(session.status, 'running')
        self.assertEqual(session.phase_elapsed, 3.)

    def test_stop_leaves_no_lesson_clock_running(self):
        clock, session, player, flow = self.flow()
        clock.advance(2.);flow.stop();before = session.session_elapsed
        clock.advance(50.)
        self.assertEqual(session.snapshot()['session_elapsed'], before)


if __name__ == '__main__':unittest.main()
