"""GTK regression: a decoder's cached last frame must not mask failure.

Use an isolated Xvfb on Linux. No camera, microphone, physical inputs or audio.
"""
import unittest

try:
    import gi
    gi.require_version('Gtk', '3.0')
    import cairo
    from gi.repository import Gtk
    from .view import HealthView
    GTK_AVAILABLE = True
except ImportError:
    GTK_AVAILABLE = False

from .test_media_flow import FakeLibrary
from .test_companion_flow import CompanionPlayer


@unittest.skipUnless(GTK_AVAILABLE, 'GTK/Cairo required; run under isolated Xvfb')
class CompanionViewErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check()[0]:
            raise unittest.SkipTest('An isolated GTK display is required')

    def setUp(self):
        class CachedFramePlayer(CompanionPlayer):
            def __init__(self):
                super().__init__()
                self.calls = 0
                self.surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 320, 180)
                ctx = cairo.Context(self.surface)
                ctx.set_source_rgb(.2, .4, .3)
                ctx.paint()
            def frame(self):
                self.calls += 1
                # Like the real player, failure does not discard the final
                # already-decoded surface. The view must decide not to show it.
                return self.surface, 320, 180
            def close(self): self.stop()
        self.player = CachedFramePlayer()
        self.view = HealthView(lambda: None, save_reports=False,
                video_player=self.player, video_library=FakeLibrary())
        self.view.enter()
        self.view.start('free')
        self.view.action('health_video_skip')
        self.addCleanup(self.view.close)

    def render(self):
        texts = []
        self.view._text = lambda cr, value, *args, **kwargs: texts.append(str(value))
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 960, 540)
        self.view.paint(cairo.Context(surface), (960, 540))
        return texts

    def test_decoder_error_does_not_present_cached_frame_as_looping(self):
        self.player.state = 'error'
        self.view._accept_current(self.view.video_flow.snapshot())
        self.assertTrue(self.view.current['companion_video']['clip'])
        self.assertTrue(self.view.current['companion_video']['error_message'])
        before = self.player.calls
        texts = self.render()
        self.assertEqual(self.player.calls, before, 'Never read stale cached video on failure')
        self.assertIn('示范暂不可用', texts)
        self.assertTrue(any('回看视频可重试' in value for value in texts))
        self.assertFalse(any('静音循环' in value for value in texts))
        self.assertEqual(self.view.current['status'], 'running')
        self.assertEqual(self.view.current['phase'], 'practice')

    def test_healthy_companion_still_renders_video_and_loop_hint(self):
        before = self.player.calls
        texts = self.render()
        self.assertEqual(self.player.calls, before + 1)
        self.assertTrue(any('静音循环' in value for value in texts))
        self.assertNotIn('示范暂不可用', texts)


if __name__ == '__main__':
    unittest.main()
