import unittest

from photo_state import PhotoSession


class PhotoSessionTests(unittest.TestCase):
    def capturing_session(self):
        session = PhotoSession()
        session.resume()
        self.assertTrue(session.start_countdown(100))
        token = session.tick(103)
        self.assertIsNotNone(token)
        return session, token

    def test_initial_state_and_resume_require_a_click(self):
        session = PhotoSession()
        self.assertEqual(session.phase, 'viewfinder')
        self.assertFalse(session.active)
        self.assertEqual(session.generation, 0)
        self.assertIsNone(session.deadline)
        self.assertIsNone(session.error)
        self.assertFalse(session.start_countdown(100))
        session.resume()
        self.assertTrue(session.active)
        self.assertEqual(session.phase, 'viewfinder')
        self.assertIsNone(session.tick(1000))
        self.assertEqual(session.generation, 0)

    def test_countdown_displays_three_two_one_and_captures_once(self):
        session = PhotoSession()
        session.resume()
        self.assertTrue(session.start_countdown(100))
        self.assertEqual(session.deadline, 103)
        for now, expected in ((99, 3), (100, 3), (100.9, 3),
                              (101, 2), (101.9, 2), (102, 1),
                              (102.9, 1), (103, 0), (200, 0)):
            with self.subTest(now=now):
                self.assertEqual(session.remaining(now), expected)
        self.assertIsNone(session.tick(102.99))
        token = session.tick(103)
        self.assertEqual(token, session.generation)
        self.assertEqual(session.phase, 'capturing')
        self.assertIsNone(session.deadline)
        self.assertEqual(session.remaining(103), 0)
        self.assertIsNone(session.tick(103))
        self.assertIsNone(session.tick(200))

    def test_repeated_click_does_not_restart_countdown(self):
        session = PhotoSession()
        session.resume()
        session.start_countdown(100)
        generation = session.generation
        self.assertFalse(session.start_countdown(102))
        self.assertEqual(session.deadline, 103)
        self.assertEqual(session.generation, generation)
        token = session.tick(103)
        self.assertFalse(session.start_countdown(104))
        self.assertTrue(session.processing(token))
        self.assertFalse(session.start_countdown(105))
        self.assertTrue(session.complete(token))
        self.assertFalse(session.start_countdown(106))

    def test_processing_and_completion_accept_only_current_work(self):
        session, token = self.capturing_session()
        self.assertFalse(session.processing(token - 1))
        self.assertFalse(session.complete(token - 1))
        self.assertFalse(session.fail(token - 1, 'stale'))
        self.assertTrue(session.processing(token))
        self.assertFalse(session.processing(token))
        self.assertTrue(session.complete(token))
        self.assertEqual(session.phase, 'result')
        self.assertFalse(session.complete(token))
        self.assertFalse(session.fail(token, 'late error'))
        self.assertIsNone(session.error)

    def test_capture_can_complete_without_separate_processing_transition(self):
        session, token = self.capturing_session()
        self.assertTrue(session.complete(token))
        self.assertEqual(session.phase, 'result')

    def test_pause_cancels_every_unfinished_stage(self):
        for phase in ('viewfinder', 'countdown', 'capturing', 'processing', 'error'):
            with self.subTest(phase=phase):
                session = PhotoSession()
                session.resume()
                if phase != 'viewfinder':
                    session.start_countdown(100)
                token = session.generation
                if phase in ('capturing', 'processing', 'error'):
                    token = session.tick(103)
                if phase == 'processing':
                    session.processing(token)
                if phase == 'error':
                    session.fail(token, 'camera unavailable')
                self.assertEqual(session.phase, phase)
                generation = session.generation
                session.pause()
                self.assertFalse(session.active)
                self.assertEqual(session.generation, generation + 1)
                self.assertEqual(session.phase, 'viewfinder')
                self.assertIsNone(session.deadline)
                self.assertIsNone(session.tick(1000))
                self.assertFalse(session.processing(token))
                self.assertFalse(session.complete(token))
                self.assertFalse(session.fail(token, 'late error'))
                session.resume()
                self.assertEqual(session.phase, 'viewfinder')
                self.assertIsNone(session.tick(1000))

    def test_result_survives_pause_and_resume_until_explicit_retake(self):
        session, token = self.capturing_session()
        session.complete(token)
        session.pause()
        self.assertEqual(session.phase, 'result')
        self.assertFalse(session.retake())
        self.assertEqual(session.phase, 'result')
        session.resume()
        self.assertEqual(session.phase, 'result')
        self.assertIsNone(session.tick(1000))
        self.assertFalse(session.start_countdown(1000))
        generation = session.generation
        self.assertTrue(session.retake())
        self.assertEqual(session.generation, generation + 1)
        self.assertEqual(session.phase, 'viewfinder')
        self.assertIsNone(session.tick(1000))
        self.assertTrue(session.start_countdown(1000))
        self.assertEqual(session.remaining(1000), 3)

    def test_retake_invalidates_inflight_work_and_cancels_countdown(self):
        for phase in ('countdown', 'capturing', 'processing'):
            with self.subTest(phase=phase):
                session = PhotoSession()
                session.resume()
                session.start_countdown(100)
                token = session.generation
                if phase != 'countdown':
                    token = session.tick(103)
                if phase == 'processing':
                    session.processing(token)
                self.assertTrue(session.retake())
                self.assertEqual(session.phase, 'viewfinder')
                self.assertGreater(session.generation, token)
                self.assertIsNone(session.deadline)
                self.assertIsNone(session.tick(1000))
                self.assertFalse(session.processing(token))
                self.assertFalse(session.complete(token))
                self.assertFalse(session.fail(token, 'late error'))

    def test_reenter_and_new_capture_reject_old_callbacks(self):
        session, old_token = self.capturing_session()
        session.pause()
        session.resume()
        session.start_countdown(200)
        new_token = session.tick(203)
        self.assertNotEqual(new_token, old_token)
        self.assertFalse(session.processing(old_token))
        self.assertFalse(session.complete(old_token))
        self.assertFalse(session.fail(old_token, 'late error'))
        self.assertEqual(session.phase, 'capturing')
        self.assertTrue(session.processing(new_token))
        self.assertTrue(session.complete(new_token))

    def test_error_requires_explicit_click_and_retry_rejects_old_work(self):
        for use_processing in (False, True):
            with self.subTest(use_processing=use_processing):
                session, token = self.capturing_session()
                if use_processing:
                    session.processing(token)
                self.assertTrue(session.fail(token, 'camera unavailable'))
                self.assertEqual(session.phase, 'error')
                self.assertEqual(session.error, 'camera unavailable')
                self.assertIsNone(session.tick(1000))
                self.assertFalse(session.complete(token))
                session.resume()
                self.assertEqual(session.phase, 'error')
                self.assertIsNone(session.tick(1000))
                self.assertTrue(session.start_countdown(1000))
                self.assertIsNone(session.error)
                retry_token = session.tick(1003)
                self.assertFalse(session.complete(token))
                self.assertFalse(session.fail(token, 'late error'))
                self.assertTrue(session.complete(retry_token))

    def test_fail_cannot_change_viewfinder_or_countdown(self):
        session = PhotoSession()
        session.resume()
        self.assertFalse(session.fail(session.generation, 'unrequested'))
        session.start_countdown(100)
        self.assertFalse(session.fail(session.generation, 'too early'))
        self.assertEqual(session.phase, 'countdown')
        self.assertIsNone(session.error)

    def test_shutdown_invalidates_work_and_closes_session(self):
        session, token = self.capturing_session()
        session.shutdown()
        self.assertFalse(session.active)
        self.assertEqual(session.phase, 'closed')
        self.assertGreater(session.generation, token)
        self.assertIsNone(session.deadline)
        self.assertIsNone(session.tick(1000))
        self.assertFalse(session.start_countdown(1000))
        self.assertFalse(session.complete(token))
        self.assertFalse(session.fail(token, 'late error'))
        session.resume()
        self.assertFalse(session.active)
        self.assertFalse(session.start_countdown(1000))

    def test_stage_and_elapsed_follow_only_the_current_capture(self):
        session, token = self.capturing_session()
        self.assertEqual(session.stage, 'capturing')
        self.assertEqual(session.elapsed(109.99), 6)
        self.assertTrue(session.processing(token))
        self.assertTrue(session.progress(token, 'painting'))
        self.assertFalse(session.progress(token, '99%'))
        session.pause();session.resume()
        self.assertFalse(session.progress(token, 'finishing'))
        self.assertIsNone(session.stage)
        self.assertEqual(session.elapsed(200), 0)
        session.start_countdown(200);new_token=session.tick(203);session.processing(new_token)
        self.assertFalse(session.progress(token, 'finished'))
        self.assertEqual(session.stage, 'starting_worker')
        self.assertTrue(session.progress(new_token, 'loading_model'))
        self.assertEqual(session.elapsed(205), 2)


if __name__ == '__main__':
    unittest.main()
