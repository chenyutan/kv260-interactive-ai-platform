"""Small-window decode epochs, forced silence and bounded pipeline lifecycle."""
import tempfile
import time
import unittest
from pathlib import Path
from .video import TeachingVideoPlayer, WIDTH, HEIGHT, COMPACT_WIDTH, COMPACT_HEIGHT
from .test_video import FakePipeline, wait_for


class CompanionPlayerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'reference.webm'
        self.path.write_bytes(b'private synthetic fake-pipeline input')
        self.pipelines = []
        self.routes = []
        self.max_active = 0
        self.callbacks = []
        self.events = []
        def factory(path, epoch, route):
            item = FakePipeline()
            self.pipelines.append(item)
            self.routes.append(route)
            self.max_active = max(self.max_active, sum(not p.closed for p in self.pipelines))
            return item
        self.player = TeachingVideoPlayer(pipeline_factory=factory,
                    on_event=self.events.append, dispatch=self.callbacks.append)
        self.player.set_audio_route({'sink': 'explicit-cube'})

    def tearDown(self):
        self.player.close()
        self.assertTrue(self.player.wait_closed())
        self.assertTrue(all(p.closed for p in self.pipelines))
        self.directory.cleanup()

    def play_companion(self):
        count = len(self.pipelines)
        epoch = self.player.play_companion(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == count + 1))
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        return epoch

    def test_companion_with_connected_cube_has_no_audio_route(self):
        self.play_companion()
        self.assertEqual(self.routes, [None])
        self.assertTrue(self.player.snapshot()['forced_muted'])
        self.assertTrue(self.player.snapshot()['muted'])
        self.assertFalse(self.player.snapshot()['audio_active'])

    def test_inventory_change_cannot_unmute_or_restart_companion(self):
        epoch = self.play_companion()
        for sink in ('new-cube', None, 'reconnected-cube'):
            self.player.set_audio_route({'sink': sink} if sink else None)
        time.sleep(.06)
        self.assertEqual(self.player.snapshot()['epoch'], epoch)
        self.assertEqual(len(self.pipelines), 1)
        self.assertEqual(self.routes, [None])
        self.assertTrue(self.player.snapshot()['muted'])
        self.assertFalse(self.player.snapshot()['audio_active'])

    def test_full_rewatch_restores_latest_explicit_route_and_size(self):
        self.play_companion()
        self.player.set_audio_route({'sink': 'reconnected-cube'})
        self.player.play(self.path)
        self.assertTrue(wait_for(lambda: len(self.pipelines) == 2))
        self.assertEqual(self.routes[-1]['sink'], 'reconnected-cube')
        self.assertFalse(self.player.snapshot()['forced_muted'])
        self.assertEqual((self.player.snapshot()['width'], self.player.snapshot()['height']), (WIDTH, HEIGHT))
        self.assertEqual(self.max_active, 1)

    def test_accepts_only_compact_epoch_shape(self):
        epoch = self.play_companion()
        full = bytes(WIDTH * HEIGHT * 4)
        compact = bytes(COMPACT_WIDTH * COMPACT_HEIGHT * 4)
        self.assertFalse(self.player._accept_frame(epoch, full, WIDTH, HEIGHT, WIDTH * 4))
        self.assertTrue(self.player._accept_frame(epoch, compact, COMPACT_WIDTH, COMPACT_HEIGHT, COMPACT_WIDTH * 4))
        self.assertEqual((self.player.snapshot()['width'], self.player.snapshot()['height']), (COMPACT_WIDTH, COMPACT_HEIGHT))
        self.assertEqual(self.player.snapshot()['queue_limit'], 1)

    def test_late_compact_frame_cannot_replace_full_rewatch(self):
        old_epoch = self.play_companion()
        self.player.play(self.path)
        raw = bytes(COMPACT_WIDTH * COMPACT_HEIGHT * 4)
        self.assertFalse(self.player._accept_frame(old_epoch, raw, COMPACT_WIDTH, COMPACT_HEIGHT, COMPACT_WIDTH * 4))
        self.assertIsNone(self.player.frame())

    def test_pause_resume_keeps_companion_silent_and_one_pipeline(self):
        self.play_companion()
        self.player.pause()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'paused'))
        self.player.set_audio_route({'sink': 'still-connected'})
        self.player.resume()
        self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'playing'))
        self.assertEqual(len(self.pipelines), 1)
        self.assertEqual(self.routes, [None])
        self.assertFalse(self.player.snapshot()['audio_active'])

    def test_eos_replays_only_one_pipeline_at_a_time(self):
        self.play_companion()
        for _ in range(5):
            last = self.pipelines[-1]
            last.position = 8.
            last.events.append(('eos', None))
            self.assertTrue(wait_for(lambda: self.player.snapshot()['status'] == 'ended'))
            self.assertTrue(last.closed)
            self.play_companion()
            self.assertEqual(self.pipelines[-1].seeks, [])
        self.assertEqual(self.max_active, 1)
        self.assertEqual(self.routes, [None] * 6)

    def test_rapid_full_compact_replacement_never_overlaps_and_cleans_up(self):
        for index in range(12):
            self.player.play(self.path, muted=index % 2 == 0, compact=index % 2 == 0)
            time.sleep(.004)
        self.player.stop()
        self.assertTrue(wait_for(lambda: all(p.closed for p in self.pipelines)))
        self.assertEqual(self.max_active, 1)
        self.assertEqual(self.player.snapshot()['status'], 'stopped')

    def test_stop_discards_pending_companion_events_and_surface(self):
        epoch = self.play_companion()
        raw = bytes(COMPACT_WIDTH * COMPACT_HEIGHT * 4)
        self.player._accept_frame(epoch, raw, COMPACT_WIDTH, COMPACT_HEIGHT, COMPACT_WIDTH * 4)
        self.player.stop()
        for callback in self.callbacks:
            callback()
        self.assertEqual(self.events, [])
        self.assertIsNone(self.player.frame())
        self.assertTrue(wait_for(lambda: all(p.closed for p in self.pipelines)))


if __name__ == '__main__':
    unittest.main()
