"""Practice references preserve lesson time, observations and media gating."""
import unittest
from .engine import LessonSession
from .media_flow import LessonVideoFlow
from .test_media_flow import FakeClock, FakeLibrary, FakePlayer


class CompanionPlayer(FakePlayer):
    def __init__(self):
        super().__init__()
        self.muted = False
        self.compact = False

    def play(self, path, *, muted=False, compact=False):
        super().play(path)
        self.muted, self.compact = muted, compact
        return True

    def play_companion(self, path):
        return self.play(path, muted=True, compact=True)

    def snapshot(self):
        return dict(super().snapshot(), muted=self.muted, compact=self.compact,
                    desired='playing' if self.state in ('loading', 'ended') else self.state)


class CompanionFlowTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.session = LessonSession('free', clock=self.clock)
        self.session.start()
        self.player = CompanionPlayer()
        self.flow = LessonVideoFlow(self.session, self.player, FakeLibrary())

    def start_companion(self):
        self.assertEqual(self.flow.snapshot()['phase'], 'video')
        return self.flow.action('video_skip')

    def test_full_demonstration_uses_original_player_mode(self):
        state = self.flow.snapshot()
        self.assertEqual(state['phase'], 'video')
        self.assertIsNone(state['companion_video'])
        self.assertFalse(self.player.compact)
        self.assertFalse(self.player.muted)

    def test_companion_starts_muted_in_practice_without_a_new_video_gate(self):
        state = self.start_companion()
        self.assertEqual(state['phase'], 'practice')
        self.assertEqual(state['status'], 'running')
        self.assertTrue(state['companion_video']['muted'])
        self.assertTrue(state['companion_video']['compact'])
        self.assertFalse(self.flow.active)

    def test_companion_eos_only_restarts_media_keeps_time_and_reports(self):
        self.start_companion()
        self.clock.advance(3)
        before = self.flow.snapshot()
        counts = self.flow.report_metadata()
        for _ in range(5):
            self.player.state = 'ended'
            after = self.flow.snapshot()
            self.assertEqual(after['elapsed'], before['elapsed'])
            self.assertEqual(after['session_elapsed'], before['session_elapsed'])
            self.assertEqual(after['lesson_id'], before['lesson_id'])
            self.assertEqual(self.flow.report_metadata(), counts)
            self.assertTrue(self.player.muted)
        self.assertEqual(after['companion_video']['loop_count'], 5)

    def test_repeated_snapshots_do_not_restart_media(self):
        self.start_companion()
        before = len(self.player.operations)
        for _ in range(20):
            self.flow.snapshot()
        self.assertEqual(len(self.player.operations), before)

    def test_practice_observations_are_still_forwarded(self):
        self.start_companion()
        calls = []
        self.session.update = lambda state: calls.append(state)
        payload = {'pid': 10, 'sequence': 3}
        self.flow.update(payload)
        self.assertEqual(calls, [payload])

    def test_pause_resume_has_no_loop_restart_or_elapsed_leak(self):
        self.start_companion()
        self.clock.advance(3)
        self.flow.action('pause')
        self.assertEqual(self.player.state, 'paused')
        before = self.session.session_elapsed
        self.clock.advance(80)
        self.assertEqual(self.flow.snapshot()['session_elapsed'], before)
        self.flow.action('resume')
        self.assertEqual(self.player.state, 'playing')
        self.clock.advance(1)
        self.assertEqual(self.flow.snapshot()['session_elapsed'], before + 1)

    def test_paused_eos_waits_until_resume(self):
        self.start_companion()
        self.flow.action('pause')
        self.player.state = 'ended'
        before = len([x for x in self.player.operations if x[0] == 'play'])
        for _ in range(10):
            self.flow.snapshot()
        self.assertEqual(len([x for x in self.player.operations if x[0] == 'play']), before)
        self.flow.action('resume')
        self.assertEqual(self.player.state, 'playing')

    def test_ui_suspension_stops_reference_and_does_not_resume_session(self):
        self.start_companion()
        self.flow.action('pause')
        self.flow.set_companion_suspended(True)
        self.assertEqual(self.player.state, 'stopped')
        before = len(self.player.operations)
        for _ in range(10):
            self.assertIsNone(self.flow.snapshot()['companion_video'])
        self.assertEqual(len(self.player.operations), before)
        self.flow.set_companion_suspended(False)
        self.flow.snapshot()
        self.assertEqual(self.player.state, 'paused')
        self.assertEqual(self.session.status, 'paused')

    def test_ui_suspension_does_not_change_full_video_gate(self):
        self.flow.snapshot()
        self.flow.action('pause')
        self.flow.set_companion_suspended(True)
        self.assertEqual(self.flow.snapshot()['phase'], 'video')
        self.assertEqual(self.player.state, 'paused')

    def test_next_and_rewatch_restore_full_audio_policy(self):
        self.start_companion()
        state = self.flow.action('next')
        self.assertEqual(state['phase'], 'video')
        self.assertEqual(state['lesson_id'], 'raise')
        self.assertFalse(self.player.compact)
        self.assertFalse(self.player.muted)
        self.flow.action('video_skip')
        state = self.flow.action('video_rewatch')
        self.assertEqual(state['phase'], 'video')
        self.assertFalse(self.player.muted)

    def test_finish_stops_reference_permanently(self):
        self.start_companion()
        self.flow.action('finish')
        self.assertEqual(self.player.state, 'stopped')
        before = len(self.player.operations)
        for _ in range(10):
            self.flow.snapshot()
        self.assertEqual(len(self.player.operations), before)

    def test_exit_cannot_resurrect_reference_or_forward_pose(self):
        self.start_companion()
        self.flow.stop()
        calls = []
        self.session.update = lambda payload: calls.append(payload)
        before = len(self.player.operations)
        for operation in ('resume', 'next', 'video_rewatch'):
            self.flow.action(operation)
            self.flow.update({'unexpected': True})
            self.flow.snapshot()
        self.assertEqual(calls, [])
        self.assertEqual(len(self.player.operations), before)
        self.assertEqual(self.player.state, 'stopped')

    def test_reference_error_is_nonfatal_and_does_not_spin_retry(self):
        self.start_companion()
        self.player.state = 'error'
        self.clock.advance(3)
        state = self.flow.snapshot()
        self.assertIn('失败', state['companion_video']['error_message'])
        before = len(self.player.operations)
        self.clock.advance(1)
        state = self.flow.snapshot()
        self.assertEqual(state['session_elapsed'], 4)
        self.assertEqual(state['status'], 'running')
        self.assertEqual(len(self.player.operations), before)

    def test_missing_reference_does_not_block_practice(self):
        self.flow.snapshot()
        self.flow.library.lookup = lambda _: None
        state = self.flow.action('video_skip')
        self.assertEqual(state['phase'], 'practice')
        self.assertIn('不可用', state['companion_video']['error_message'])
        self.clock.advance(2)
        self.assertEqual(self.flow.snapshot()['session_elapsed'], 2)

    def test_startup_exception_or_disabled_player_does_not_stop_practice(self):
        for failure in (False, RuntimeError('synthetic thread startup error')):
            with self.subTest(failure=failure):
                self.setUp()
                self.flow.snapshot()
                def fail(_):
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                self.player.play_companion = fail
                state = self.flow.action('video_skip')
                self.assertEqual(state['status'], 'running')
                self.assertTrue(state['companion_video']['error_message'])
                before = len(self.player.operations)
                self.clock.advance(2)
                self.assertEqual(self.flow.snapshot()['session_elapsed'], 2)
                self.assertEqual(len(self.player.operations), before)


if __name__ == '__main__':
    unittest.main()
