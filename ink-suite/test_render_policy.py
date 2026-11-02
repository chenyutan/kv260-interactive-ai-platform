import math
import unittest
from render_policy import FrameLayout, RenderPolicy, present, bounded_window_size


class RenderPolicyTests(unittest.TestCase):
    def test_720p_default_and_lower_screen_keep_bound_and_uniform_scale(self):
        policy = RenderPolicy.from_environment({})
        self.assertEqual(policy.layout(1280, 720).render_size, (960, 540))
        self.assertEqual(policy.layout(960, 540).render_size, (960, 540))
        self.assertEqual(policy.layout(800, 450).render_size, (800, 450))
        self.assertEqual(policy.layout(1280, 628).render_size, (960, 471))
        layout = policy.layout(1024, 508)
        self.assertEqual(layout.render_size, (960, 476))
        self.assertLess(max(layout.offset), 1.)
        self.assertEqual(policy.layout(1, 720).render_width, 1)

    def test_env_uncapped_and_fallback_caps(self):
        for value in ('uncapped', 'disabled', 'native', 'off', '0'):
            self.assertEqual(RenderPolicy.from_environment({'INK_SUITE_RENDER_CAP': value})
                             .layout(1920, 1080).render_size, (1920, 1080))
        self.assertEqual(RenderPolicy.from_environment({'INK_SUITE_RENDER_CAP': '800x450'})
                         .layout(1280, 720).render_size, (800, 450))
        for value in ('garbage', '0x540', '960XNaN', '5000x540', '960x000'):
            with self.assertRaises(ValueError):
                RenderPolicy.from_environment({'INK_SUITE_RENDER_CAP': value})

    def test_real_mouse_and_dirty_coordinates_round_trip(self):
        layout = RenderPolicy().layout(1280, 720)
        for x, y in ((0., 0.), (960., 540.), (420., 300.)):
            dx, dy = layout.display_point(x, y)
            rx, ry = layout.render_point(dx, dy)
            self.assertAlmostEqual(rx, x); self.assertAlmostEqual(ry, y)
        self.assertEqual(layout.normalized_point(640, 360), (.5, .5))
        self.assertIsNone(layout.normalized_point(-1, 50))
        self.assertIsNone(layout.normalized_point(math.nan, 50))
        self.assertEqual(layout.display_rect(100, 100, 30, 30), (132, 132, 43, 43))
        self.assertEqual(layout.display_rect(-100, -100, 1000, 1000), (0, 0, 1201, 720))

    def test_window_fit_and_invalid_sizes(self):
        self.assertEqual(bounded_window_size(960, 540), (936, 492))
        self.assertEqual(bounded_window_size(800, 450), (776, 402))
        self.assertEqual(bounded_window_size(1920, 1080), (940, 510))
        for size in ((0, 540), (True, 540), (1280., 720)):
            with self.assertRaises(ValueError): RenderPolicy().layout(*size)

    def test_presentation_is_single_scaled_composite_and_keeps_dirty_clip(self):
        try: import cairo
        except ImportError: self.skipTest('Cairo is required for raster presentation checks')
        layout = RenderPolicy((320, 180)).layout(640, 360)
        source = cairo.ImageSurface(cairo.FORMAT_RGB24, *layout.render_size)
        sc = cairo.Context(source);sc.set_source_rgb(.8, .2, .1);sc.paint()
        target = cairo.ImageSurface(cairo.FORMAT_RGB24, *layout.output_size)
        tc = cairo.Context(target);tc.set_source_rgb(0, 1, 0);tc.paint()
        tc.rectangle(0, 0, 320, 360);tc.clip()
        before = tc.get_matrix();present(tc, source, layout)
        self.assertEqual(tc.get_matrix(), before)
        data = bytes(target.get_data());stride = target.get_stride()
        self.assertGreater(data[stride * 100 + 100 * 4 + 2], 180)
        self.assertEqual(data[stride * 100 + 500 * 4 + 1], 255)
        with self.assertRaises(ValueError): present(tc, source, RenderPolicy().layout(640, 360))

    def test_upscale_reaches_destination_as_untransformed_same_format_frame(self):
        try: import cairo
        except ImportError: self.skipTest('Cairo is required for raster presentation checks')
        layout = FrameLayout(640, 362, 320, 180)
        source = cairo.ImageSurface(cairo.FORMAT_RGB16_565, *layout.render_size)
        sc = cairo.Context(source);sc.set_source_rgb(1, 0, 0);sc.paint()
        target = cairo.ImageSurface(cairo.FORMAT_RGB24, *layout.output_size)
        tc = cairo.Context(target)
        class Destination:
            def __getattr__(self, name): return getattr(tc, name)
            def scale(self, *args): raise AssertionError('X11 must not scale the uploaded frame')
            def translate(self, *args): raise AssertionError('X11 upload must use integer origin')
            def set_source_surface(self, frame, x, y):
                self.frame = frame
                if frame.get_format() != cairo.FORMAT_RGB16_565: raise AssertionError('565 guard was lost')
                if (frame.get_width(), frame.get_height()) != layout.output_size: raise AssertionError('Unscaled upload')
                if (x, y) != (0, 0): raise AssertionError('Noninteger upload origin')
                tc.set_source_surface(frame, x, y)
        destination = Destination()
        before = tc.get_matrix();present(destination, source, layout, background=(0, 0, 1))
        self.assertEqual(tc.get_matrix(), before)
        target.flush();data=bytes(target.get_data());stride=target.get_stride()
        self.assertEqual(data[10*stride+10*4+2],255)
        self.assertEqual(data[10*4],255)  # blue letterbox remains outside the content
        first = destination.frame
        sc.set_source_rgb(0, 1, 0);sc.paint()
        present(destination, source, layout, background=(0, 0, 1))
        self.assertIs(destination.frame, first)
        target.flush();self.assertEqual(bytes(target.get_data())[10*stride+10*4+1],255)


if __name__ == '__main__': unittest.main()
