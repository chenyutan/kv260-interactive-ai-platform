"""Player controls and resource/epoch boundaries; no camera or real speaker."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from health.video import TeachingVideoPlayer, WIDTH, HEIGHT, MAX_FILE_BYTES


def wait_for(predicate, timeout=2.):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.005)
    return False


class FakePipeline:
    def __init__(self):
        self.states = []
        self.closed = False
        self.position = 0.
        self.events = []
        self.seeks = []

    def set_playing(self, value):
        self.states.append(value)

    def seek(self, value):
        self.seeks.append(value)
        self.position = value

    def poll(self):
        return self.events.pop(0) if self.events else None

    def times(self):
        return self.position, 8.

    def close(self):
        self.closed = True


class VideoPlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.webm'
        self.path.write_bytes(b'owned-test-only')
        self.pipelines = []
        self.calls = []
        self.dispatched = []
        self.events = []
        def factory(path, epoch, route):
            self.calls.append((path, epoch, route))
            pipeline = FakePipeline()
            self.pipelines.append(pipeline)
            return pipeline
        self.player = TeachingVideoPlayer(on_event=self.events.append,
                        pipeline_factory=factory, dispatch=self.dispatched.append)

    def tearDown(self):
        self.player.close()
        self.assertTrue(self.player.wait_closed())
        self.temp.cleanup()

    def test_play_is_nonblocking_and_worker_opens_file(self):
        start = time.monotonic()
        epoch = self.player.play(self.path)
        self.assertLess(time.monotonic() - start, .15)
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        self.assertEqual(self.calls[0][1], epoch)
        self.assertEqual(self.player.snapshot()['epoch'], epoch)
        self.assertEqual(self.player.snapshot()['duration_seconds'], 8.)

    def test_pause_resume_does_not_recreate_pipeline(self):
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 1))
        self.player.pause()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'paused'))
        self.player.resume()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        self.assertEqual(len(self.pipelines), 1)

    def test_default_audio_is_silent_with_no_system_sink(self):
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: bool(self.calls)))
        self.assertIsNone(self.calls[0][2])
        self.assertTrue(self.player.snapshot()['muted'])
        self.assertFalse(self.player.snapshot()['audio_active'])

    def test_invalid_and_stale_audio_routes_remain_muted(self):
        for route in ({}, {'sink': ''}, {'sink': 'cube', 'discovery_stale': True}, {'sink': 'x', 'server': object()}):
            self.player.set_audio_route(route)
            self.assertTrue(self.player.snapshot()['muted'])

    def test_audio_route_change_rebuilds_at_measured_position(self):
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 1))
        self.pipelines[0].position = 3.25
        self.assertTrue(wait_for(lambda: self.player.snapshot()['position'] == 3.25))
        self.player.set_audio_route({'device': 'explicit_cube', 'server': 'unix:/run/user/1000/pulse/native'})
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 2))
        self.assertTrue(self.pipelines[0].closed)
        self.assertEqual(self.calls[1][2]['sink'], 'explicit_cube')
        self.assertEqual(self.pipelines[1].seeks, [3.25])
        self.player.set_audio_route(None)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 3))
        self.assertIsNone(self.calls[-1][2])

    def test_replay_same_file_starts_from_zero(self):
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 1))
        self.pipelines[0].position = 3.25
        self.assertTrue(wait_for(lambda: self.player.snapshot()['position'] == 3.25))
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 2))
        self.assertEqual(self.pipelines[1].seeks, [])

    def test_stopped_epoch_discards_queued_events_and_frames(self):
        epoch = self.player.play(self.path)
        self.assertTrue(wait_for(lambda: bool(self.dispatched)))
        self.player.stop()
        for callback in self.dispatched:
            callback()
        self.assertEqual(self.events, [])
        self.assertFalse(self.player._accept_frame(epoch, bytes(WIDTH * HEIGHT * 4), WIDTH, HEIGHT, WIDTH * 4))
        self.assertIsNone(self.player.frame())

    def test_latest_frame_queue_is_one_and_counts_replacements(self):
        epoch = self.player.play(self.path)
        frame = bytes(WIDTH * HEIGHT * 4)
        for _ in range(30):
            self.assertTrue(self.player._accept_frame(epoch, frame, WIDTH, HEIGHT, WIDTH * 4))
        self.assertEqual(self.player.snapshot()['queue_limit'], 1)
        self.assertEqual(self.player.snapshot()['frames'], 30)
        self.assertEqual(self.player.snapshot()['dropped'], 29)

    def test_reject_oversized_or_malformed_raw_frames(self):
        epoch = self.player.play(self.path)
        for data, w, h, stride in ((b'', WIDTH, HEIGHT, WIDTH*4), (b'', 9999, 9999, 9999*4), (b'', WIDTH, HEIGHT, WIDTH*4+512)):
            self.assertFalse(self.player._accept_frame(epoch, data, w, h, stride))
        self.assertEqual(self.player.snapshot()['frames'], 0)

    def test_missing_local_file_becomes_error_not_loading_forever(self):
        self.player.play(Path(self.temp.name) / 'missing.webm')
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'error'))
        self.assertEqual(self.calls, [])

    def test_remote_url_and_relative_path_cannot_fetch_network(self):
        for path in ('https://example.com/video.mp4', 'relative.webm'):
            self.player.play(path)
            self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'error'))
        self.assertEqual(self.calls, [])

    def test_directory_cannot_be_played(self):
        self.player.play(self.temp.name)
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'error'))
        self.assertEqual(self.calls, [])

    def test_eos_and_error_close_pipeline(self):
        for event in (('eos', None), ('error', 'decoder unavailable')):
            expected = len(self.pipelines) + 1
            self.player.play(self.path)
            self.assertTrue(wait_for(lambda: len(self.pipelines) == expected))
            pipeline = self.pipelines[-1]
            pipeline.events.append(event)
            self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] in ('ended', 'error')))
            self.assertTrue(pipeline.closed)

    def test_close_cancels_callback_and_joins_worker(self):
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: bool(self.dispatched)))
        self.player.close()
        self.assertTrue(self.player.wait_closed())
        for callback in self.dispatched:
            callback()
        self.assertEqual(self.events, [])
        self.assertEqual(self.player.snapshot()['status'], 'closed')
        self.assertFalse(self.player.play(self.path))

    def test_disabled_preview_creates_no_worker(self):
        player = TeachingVideoPlayer(enabled=False)
        self.assertFalse(player.play(self.path))
        self.assertEqual(player.snapshot()['status'], 'disabled')
        self.assertFalse(player.snapshot()['worker_alive'])
        player.close()

    def test_factory_failure_can_be_recovered_by_replay(self):
        self.player._factory = lambda *_: (_ for _ in ()).throw(RuntimeError('test decoder failure'))
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'error'))
        self.player._factory = lambda *_: FakePipeline()
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))

    def test_unavailable_explicit_audio_falls_back_once_to_silent_video(self):
        routes = []
        def factory(_path, _epoch, route):
            routes.append(route)
            if route:
                raise RuntimeError('explicit Cube is gone')
            return FakePipeline()
        self.player._factory = factory
        self.player.set_audio_route({'sink': 'cube'})
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        self.assertEqual(len(routes), 2)
        self.assertIsNone(routes[-1])
        self.assertTrue(self.player.snapshot()['muted'])
        self.assertEqual(self.player.snapshot()['audio_fallbacks'], 1)
        self.assertIn('Cube', self.player.snapshot()['audio_error'])
        self.player.pause()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'paused'))
        self.player.resume()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        self.assertFalse(self.player.snapshot()['audio_active'])
        self.assertEqual(len(routes), 2)

    def test_audio_failure_during_playback_keeps_measured_position(self):
        self.player.set_audio_route({'sink': 'cube'})
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 1))
        self.pipelines[0].position = 4.25
        self.pipelines[0].events.append(('error', 'Cube output disconnected'))
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 2))
        self.assertEqual(self.pipelines[-1].seeks, [4.25])
        self.assertIsNone(self.calls[-1][2])
        self.assertEqual(self.player.snapshot()['status'], 'playing')
        self.assertTrue(self.player.snapshot()['muted'])

    def test_stale_decode_pipeline_token_cannot_publish_frames(self):
        epoch = self.player.play(self.path)
        self.player._pipeline_token = 2
        data = bytes(WIDTH * HEIGHT * 4)
        self.assertFalse(self.player._accept_frame(epoch, data, WIDTH, HEIGHT, WIDTH*4, token=1))
        self.assertTrue(self.player._accept_frame(epoch, data, WIDTH, HEIGHT, WIDTH*4, token=2))

    def test_concurrent_new_frame_does_not_blank_current_surface(self):
        from unittest.mock import patch
        import types
        epoch = self.player.play(self.path)
        data = bytes(WIDTH * HEIGHT * 4)
        self.player._accept_frame(epoch, data, WIDTH, HEIGHT, WIDTH*4)
        sentinel = object()
        def create(*_):
            self.player._accept_frame(epoch, data, WIDTH, HEIGHT, WIDTH*4)
            return sentinel
        cairo = types.SimpleNamespace(FORMAT_RGB24=0,
                    ImageSurface=types.SimpleNamespace(create_for_data=create))
        with patch.dict('sys.modules', {'cairo': cairo}):
            frame = self.player.frame()
        self.assertIs(frame[0], sentinel)

    def test_pause_pipeline_exception_recovers_on_explicit_replay(self):
        class FailedPause(FakePipeline):
            def set_playing(self, value):
                if not value:
                    raise RuntimeError('test failed pause')
                super().set_playing(value)
        broken = FailedPause()
        created = []
        def factory(*_):
            item = broken if not created else FakePipeline()
            created.append(item)
            return item
        self.player._factory = factory
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        self.player.pause()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'error'))
        self.assertTrue(broken.closed)
        time.sleep(.06)
        self.assertEqual(len(created), 1, 'Failed epoch must not spin-retry')
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(created) == 2 and self.player.snapshot()['status'] == 'playing'))

    def test_retry_during_failing_cleanup_is_not_lost(self):
        entered, release = threading.Event(), threading.Event()
        class FailedPoll(FakePipeline):
            def poll(self):
                raise RuntimeError('test poll failure')
            def close(self):
                entered.set()
                if not release.wait(2.):
                    raise RuntimeError('test timed out waiting for cleanup')
                super().close()
        made = []
        def factory(*_):
            item = FailedPoll() if not made else FakePipeline()
            made.append(item)
            return item
        self.player._factory = factory
        self.player.play(self.path)
        self.assertTrue(entered.wait(1.))
        try:
            retry_epoch = self.player.play(self.path)
        finally:
            release.set()
        self.assertTrue(wait_for(lambda: len(made) == 2 and self.player.snapshot()['status'] == 'playing'))
        self.assertEqual(self.player.snapshot()['epoch'], retry_epoch)
        self.assertTrue(made[0].closed)


if __name__ == '__main__':
    unittest.main()
