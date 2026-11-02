"""Clip safety and pixel semantics; native pixel tests need AArch64 + Cairo + cc.

No X display, camera, network or production library is used. The native helper
is built from current source into a temporary directory for these tests.
"""
import os
import ctypes
from pathlib import Path
import platform
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sword.alpha_blit import AlphaBlitter, _BatchTile, _clip_blits, cairo


class ClipPlanTests(unittest.TestCase):
    def test_offscreen_source_coordinates_are_clipped_with_target(self):
        self.assertEqual(_clip_blits([(4, 1, 9, 7)], -3, -2, 13, 9, 17, 11),
                         [(4, 1, 7, 3, 6, 6)])

    def test_disjoint_clip_hole_is_preserved(self):
        self.assertEqual(_clip_blits([(0, 0, 3, 10), (7, 0, 3, 10)],
                                     1, 1, 8, 8, 10, 10),
                         [(1, 1, 0, 0, 2, 8), (7, 1, 6, 0, 2, 8)])

    def test_subpixel_clip_and_overlapping_regions_are_rejected(self):
        for rects in ([(.5, 0, 2, 2)], [(0, 0, 4, 4), (2, 2, 4, 4)],
                      [(0, 0, -1, 2)], [(float('nan'), 0, 2, 2)]):
            self.assertIsNone(_clip_blits(rects, 0, 0, 10, 10, 10, 10))

    def test_empty_and_outside_clip_are_safe_noops(self):
        self.assertEqual(_clip_blits([], 0, 0, 10, 10, 10, 10), [])
        self.assertEqual(_clip_blits([(0, 0, 10, 10)], 20, 20, 3, 3, 10, 10), [])

    def test_unsupported_placement_and_huge_dimensions_are_rejected(self):
        self.assertIsNone(_clip_blits([(0, 0, 4, 4)], .5, 0, 2, 2, 4, 4))
        self.assertIsNone(_clip_blits([(0, 0, 4, 4)], 0, 0, 2**31, 2, 4, 4))

    def test_disabled_and_missing_library_fall_back_without_context_access(self):
        with patch.dict(os.environ, {'INK_SWORD_DISABLE_NEON': '1'}):
            helper = AlphaBlitter()
            self.assertEqual(helper.backend, 'cairo')
            self.assertFalse(helper.paint(None, None, 0, 0))
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            helper = AlphaBlitter(temp)
            self.assertEqual(helper.backend, 'cairo')
            self.assertFalse(helper.paint(None, None, 0, 0))


NATIVE_AVAILABLE = (cairo is not None and platform.machine().lower() in ('aarch64', 'arm64')
                    and bool(shutil.which('cc')))


@unittest.skipUnless(NATIVE_AVAILABLE, 'Requires AArch64, Cairo and cc for native pixel tests')
class NativePixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = Path(__file__).with_name('alpha_blit.c')
        output = Path(cls.temp.name) / 'libink_alpha.so'
        subprocess.run(['cc', '-O3', '-std=c99', '-shared', '-fPIC', '-Wall',
                        '-Wextra', '-Werror', str(source), '-o', str(output)], check=True)
        with patch.dict(os.environ, {'INK_SWORD_DISABLE_NEON': '0'}):
            cls.helper = AlphaBlitter(cls.temp.name)
        if cls.helper.backend != 'aarch64-neon':
            raise AssertionError('Compiled AArch64 helper failed to load')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def make_surface(self, fmt, width, height, seed, padding=0):
        stride = width * 4 + padding
        raw = bytearray(random.Random(seed).randbytes(stride * height))
        if fmt == cairo.FORMAT_ARGB32:
            for y in range(height):
                for x in range(width):
                    i = y * stride + x * 4
                    a = raw[i + 3]
                    for channel in range(3):
                        raw[i + channel] = (raw[i + channel] * a + 127) // 255
        return cairo.ImageSurface.create_for_data(raw, fmt, width, height, stride), raw

    def assert_pixels_equal(self, a, b, fmt, width, height, stride):
        for y in range(height):
            for x in range(width):
                i = y * stride + x * 4
                channels = 3 if fmt == cairo.FORMAT_RGB24 else 4
                self.assertEqual(a[i:i + channels], b[i:i + channels], (x, y))
            self.assertEqual(a[y * stride + width * 4:(y + 1) * stride],
                             b[y * stride + width * 4:(y + 1) * stride])

    def test_pixel_equivalence_with_negative_positions_strides_and_clip_holes(self):
        for fmt in (cairo.FORMAT_RGB24, cairo.FORMAT_ARGB32):
            for left, top in ((-3, -2), (4, 3), (15, 8), (30, 30)):
                with self.subTest(format=fmt, position=(left, top)):
                    src, src_data = self.make_surface(cairo.FORMAT_ARGB32, 13, 9, 11, 12)
                    ref, ref_data = self.make_surface(fmt, 17, 11, 12, 4)
                    dst, dst_data = self.make_surface(fmt, 17, 11, 12, 4)
                    before_src = bytes(src_data)
                    expected = cairo.Context(ref)
                    actual = cairo.Context(dst)
                    for context in (expected, actual):
                        context.rectangle(1, 0, 6, 11)
                        context.rectangle(10, 2, 7, 8)
                        context.clip()
                    expected.set_source_surface(src, left, top)
                    expected.paint()
                    self.assertTrue(self.helper.paint(actual, src, left, top))
                    ref.flush()
                    dst.flush()
                    self.assert_pixels_equal(dst_data, ref_data, fmt, 17, 11, dst.get_stride())
                    self.assertEqual(src_data, before_src)

    def test_alpha_zero_is_noop_and_noninteger_opacity_falls_back(self):
        src, _ = self.make_surface(cairo.FORMAT_ARGB32, 9, 9, 20)
        dst, data = self.make_surface(cairo.FORMAT_ARGB32, 11, 11, 21)
        before = bytes(data)
        context = cairo.Context(dst)
        self.assertTrue(self.helper.paint(context, src, 0, 0, 0))
        self.assertFalse(self.helper.paint(context, src, 0, 0, .88))
        self.assertEqual(data, before)

    def test_unsupported_cairo_states_return_false_without_modification(self):
        for mode in ('ctm', 'operator', 'curved_clip', 'fractional_clip',
                     'fractional_position', 'src_scale', 'src_offset', 'dst_scale',
                     'dst_offset', 'rgb_source', 'group'):
            with self.subTest(mode=mode):
                src, _ = self.make_surface(cairo.FORMAT_ARGB32, 9, 9, 30)
                dst, data = self.make_surface(cairo.FORMAT_ARGB32, 11, 11, 31)
                before = bytes(data)
                context = cairo.Context(dst)
                left = 0
                if mode == 'ctm':
                    context.translate(1, 0)
                elif mode == 'operator':
                    context.set_operator(cairo.OPERATOR_SOURCE)
                elif mode == 'curved_clip':
                    context.arc(5, 5, 3, 0, 6.28)
                    context.clip()
                elif mode == 'fractional_clip':
                    context.rectangle(.5, 0, 9, 9)
                    context.clip()
                elif mode == 'fractional_position':
                    left = .5
                elif mode == 'src_scale':
                    src.set_device_scale(2, 2)
                elif mode == 'src_offset':
                    src.set_device_offset(1, 1)
                elif mode == 'dst_scale':
                    dst.set_device_scale(2, 2)
                elif mode == 'dst_offset':
                    dst.set_device_offset(1, 1)
                elif mode == 'rgb_source':
                    src, _ = self.make_surface(cairo.FORMAT_RGB24, 9, 9, 32)
                elif mode == 'group':
                    context.push_group()
                self.assertFalse(self.helper.paint(context, src, left, 0))
                self.assertEqual(data, before)

    def test_shared_source_destination_memory_falls_back(self):
        dst, data = self.make_surface(cairo.FORMAT_ARGB32, 11, 11, 41)
        alias = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, 11, 11, dst.get_stride())
        before = bytes(data)
        self.assertFalse(self.helper.paint(cairo.Context(dst), alias, 0, 0))
        self.assertEqual(data, before)

    def test_frame_path_matches_cairo_for_clipping_and_interleaved_vectors(self):
        src, original_src = self.make_surface(cairo.FORMAT_ARGB32, 13, 9, 51, 12)
        ref, ref_data = self.make_surface(cairo.FORMAT_RGB24, 17, 11, 52, 4)
        dst, dst_data = self.make_surface(cairo.FORMAT_RGB24, 17, 11, 52, 4)
        expected, actual = cairo.Context(ref), cairo.Context(dst)
        frame = self.helper.prepare_frame(actual)
        self.assertIsNotNone(frame)
        before_source = bytes(original_src)
        count_before = self.helper.native_calls
        for left, top, regions in [(-3, -2, None),
                                   (4, 3, [(0, 0, 6, 11), (10, 0, 7, 11)]),
                                   (15, 8, None), (30, 30, None)]:
            for context in (expected, actual):
                context.set_source_rgb(.2, .3, .4)
                context.rectangle(1, 1, 2, 2)
                context.fill()
            expected.save()
            if regions is not None:
                for region in regions:
                    expected.rectangle(*region)
                expected.clip()
            expected.set_source_surface(src, left, top)
            expected.paint()
            expected.restore()
            self.assertTrue(frame.paint_tile(src, left, top, regions))
        ref.flush()
        dst.flush()
        self.assert_pixels_equal(dst_data, ref_data, cairo.FORMAT_RGB24, 17, 11, dst.get_stride())
        self.assertGreater(self.helper.native_calls, count_before)
        self.assertEqual(original_src, before_source)

    def test_frame_invalid_sources_regions_and_alias_leave_pixels_unchanged(self):
        src, _ = self.make_surface(cairo.FORMAT_ARGB32, 9, 9, 61)
        rgb, _ = self.make_surface(cairo.FORMAT_RGB24, 9, 9, 62)
        dst, data = self.make_surface(cairo.FORMAT_RGB24, 11, 11, 63)
        frame = self.helper.prepare_frame(cairo.Context(dst))
        self.assertIsNotNone(frame)
        alias = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, 11, 11, dst.get_stride())
        before = bytes(data)
        for tile, left, regions in [(rgb, 0, None), (src, .5, None),
                                   (src, 0, [(0.5, 0, 5, 5)]),
                                   (src, 0, [(0, 0, 5, 5), (2, 2, 5, 5)]),
                                   (alias, 0, None)]:
            self.assertFalse(frame.paint_tile(tile, left, 0, regions))
            self.assertEqual(data, before)

    def test_frame_requires_full_opaque_target_and_bounds_source_cache(self):
        argb, _ = self.make_surface(cairo.FORMAT_ARGB32, 4, 4, 70)
        self.assertIsNone(self.helper.prepare_frame(cairo.Context(argb)))
        dst, _ = self.make_surface(cairo.FORMAT_RGB24, 4, 4, 71)
        clipped = cairo.Context(dst)
        clipped.rectangle(0, 0, 2, 2)
        clipped.clip()
        self.assertIsNone(self.helper.prepare_frame(clipped))
        context = cairo.Context(dst)
        frame = self.helper.prepare_frame(context)
        self.assertIsNotNone(frame)
        self.helper.clear_sources()
        for i in range(130):
            tile, _ = self.make_surface(cairo.FORMAT_ARGB32, 1, 1, 80 + i)
            self.assertTrue(frame.paint_tile(tile, 0, 0))
        self.assertEqual(len(self.helper._sources), 128)
        self.assertEqual(self.helper._source_bytes, 128 * 4)
        resized, _ = self.make_surface(cairo.FORMAT_RGB24, 5, 5, 72)
        self.assertIsNotNone(self.helper.prepare_frame(cairo.Context(resized)))
        self.assertEqual(len(self.helper._sources), 0)
        self.assertEqual(self.helper._source_bytes, 0)

    def test_frame_source_cache_obeys_byte_budget_and_large_sources_are_not_retained(self):
        dst, _ = self.make_surface(cairo.FORMAT_RGB24, 16, 16, 91)
        frame = self.helper.prepare_frame(cairo.Context(dst))
        self.helper.clear_sources()
        # Scale the bound down for a fast deterministic eviction test; the
        # production constant remains 64 MiB and is asserted independently.
        self.assertEqual(self.helper.SOURCE_CACHE_BYTES, 64 * 1024 * 1024)
        with patch.object(self.helper, 'SOURCE_CACHE_BYTES', 512):
            first, _ = self.make_surface(cairo.FORMAT_ARGB32, 8, 8, 92)
            second, _ = self.make_surface(cairo.FORMAT_ARGB32, 8, 8, 93)
            third, _ = self.make_surface(cairo.FORMAT_ARGB32, 8, 8, 94)
            for tile in (first, second, third):
                self.assertTrue(frame.paint_tile(tile, 0, 0))
            self.assertEqual(self.helper._source_bytes, 512)
            self.assertNotIn(id(first), self.helper._sources)
            self.assertIn(id(second), self.helper._sources)
            oversized, _ = self.make_surface(cairo.FORMAT_ARGB32, 16, 16, 95)
            self.assertTrue(frame.paint_tile(oversized, 0, 0))
            self.assertNotIn(id(oversized), self.helper._sources)
            self.assertEqual(self.helper._source_bytes, 512)
        self.helper.clear_sources()
        self.assertEqual(self.helper._source_bytes, 0)

    def test_batch_matches_cairo_order_overlap_clipping_and_interleaved_vectors(self):
        sources = [self.make_surface(cairo.FORMAT_ARGB32, 13, 9, 111 + i, 12)
                   for i in range(4)]
        ref, ref_data = self.make_surface(cairo.FORMAT_RGB24, 17, 11, 121, 8)
        dst, dst_data = self.make_surface(cairo.FORMAT_RGB24, 17, 11, 121, 8)
        expected, actual = cairo.Context(ref), cairo.Context(dst)
        frame = self.helper.prepare_frame(actual)
        originals = [bytes(raw) for _, raw in sources]
        before = self.helper.native_calls
        batch_before = self.helper.batch_native_calls
        batches = [
            [(sources[i % 4][0], x, y) for i, (x, y) in enumerate(
                [(-8, -7), (2, 2), (3, 4), (2, 2), (15, 9), (30, 30),
                 (2**31 - 1, -2**31), (-2**31, 2**31 - 1)])],
            [(sources[3][0], -3, -2), (sources[0][0], 5, 3), (sources[2][0], 5, 3)],
        ]
        for tiles in batches:
            for context in (expected, actual):
                context.set_source_rgb(.31, .63, .24)
                context.rectangle(1, 0, 4, 8)
                context.fill()
            for surface, left, top in tiles:
                expected.set_source_surface(surface, left, top)
                expected.paint()
            self.assertTrue(frame.paint_tiles(tiles))
        ref.flush()
        dst.flush()
        self.assert_pixels_equal(dst_data, ref_data, cairo.FORMAT_RGB24, 17, 11, dst.get_stride())
        self.assertEqual(self.helper.native_calls - before, 2)
        self.assertEqual(self.helper.batch_native_calls - batch_before, 2)
        self.assertEqual([bytes(raw) for _, raw in sources], originals)

    def test_batch_invalid_later_tile_rejects_whole_batch_without_partial_paint(self):
        src, _ = self.make_surface(cairo.FORMAT_ARGB32, 5, 5, 131)
        rgb, _ = self.make_surface(cairo.FORMAT_RGB24, 5, 5, 132)
        dst, data = self.make_surface(cairo.FORMAT_RGB24, 11, 11, 133)
        alias = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, 11, 11, dst.get_stride())
        frame = self.helper.prepare_frame(cairo.Context(dst))
        before = bytes(data)
        count_before = self.helper.native_calls
        for bad in ((rgb, 0, 0), (src, .5, 0), (src, 0, float('nan')),
                    (src, 2**31, 0), (src, 0, -2**31 - 1),
                    (alias, 0, 0), (src, 0), None):
            with self.subTest(bad=repr(bad)):
                self.assertFalse(frame.paint_tiles([(src, 0, 0), bad]))
                self.assertEqual(data, before)
        self.assertFalse(frame.paint_tiles([(src, 0, 0)] * (self.helper.BATCH_LIMIT + 1)))
        self.assertEqual(data, before)
        self.assertEqual(self.helper.native_calls, count_before)

    def test_batch_limit_preallocation_unique_source_lookup_and_eviction_pins(self):
        dst, _ = self.make_surface(cairo.FORMAT_RGB24, 16, 16, 141)
        frame = self.helper.prepare_frame(cairo.Context(dst))
        sources = [self.make_surface(cairo.FORMAT_ARGB32, 2, 2, 142 + i)[0]
                   for i in range(4)]
        tiles = [(sources[i % 4], i % 15, (i // 15) % 15) for i in range(self.helper.BATCH_LIMIT)]
        buffer = self.helper._batch_buffer
        with patch.object(self.helper, '_immutable_source', wraps=self.helper._immutable_source) as lookup:
            self.assertTrue(frame.paint_tiles(tiles))
            self.assertEqual(lookup.call_count, 4)
        self.assertIs(self.helper._batch_buffer, buffer)
        self.helper.clear_sources()
        ref, ref_data = self.make_surface(cairo.FORMAT_RGB24, 16, 16, 150)
        out, out_data = self.make_surface(cairo.FORMAT_RGB24, 16, 16, 150)
        ref_context, out_context = cairo.Context(ref), cairo.Context(out)
        frame = self.helper.prepare_frame(out_context)
        many_sources = [self.make_surface(cairo.FORMAT_ARGB32, 2, 2, 151 + i)[0]
                        for i in range(140)]
        tiles = [(surface, i % 15, (i // 15) % 15) for i, surface in enumerate(many_sources)]
        with patch.object(self.helper, 'SOURCE_CACHE_LIMIT', 2):
            for surface, left, top in tiles:
                ref_context.set_source_surface(surface, left, top)
                ref_context.paint()
            self.assertTrue(frame.paint_tiles(tiles))
            self.assertEqual(len(self.helper._sources), 2)
        ref.flush()
        out.flush()
        self.assert_pixels_equal(out_data, ref_data, cairo.FORMAT_RGB24, 16, 16, out.get_stride())

    def test_batch_native_checks_every_descriptor_before_any_pixel_write(self):
        src, src_data = self.make_surface(cairo.FORMAT_ARGB32, 4, 4, 301)
        dst, dst_data = self.make_surface(cairo.FORMAT_RGB24, 8, 8, 302)
        src_pointer = ctypes.addressof(ctypes.c_uint8.from_buffer(src_data))
        dst_pointer = ctypes.addressof(ctypes.c_uint8.from_buffer(dst_data))
        before = bytes(dst_data)
        good = _BatchTile(src_pointer, len(src_data), src.get_stride(), 4, 4, 0, 0)
        bads = [
            _BatchTile(src_pointer, len(src_data), 15, 4, 4, 0, 0),
            _BatchTile(src_pointer, 10, src.get_stride(), 4, 4, 0, 0),
            _BatchTile(src_pointer, len(src_data), src.get_stride(), -1, 4, 0, 0),
            _BatchTile(0, len(src_data), src.get_stride(), 4, 4, 0, 0),
            _BatchTile(dst_pointer, len(dst_data), dst.get_stride(), 8, 8, 0, 0),
            _BatchTile(ctypes.c_void_p(-1).value, 64, 16, 4, 4, 0, 0),
        ]
        for bad in bads:
            descriptors = (_BatchTile * 2)(good, bad)
            result = self.helper._batch(dst_pointer, len(dst_data), dst.get_stride(), 8, 8, descriptors, 2)
            self.assertEqual(result, -1)
            self.assertEqual(dst_data, before)
        descriptors = (_BatchTile * 1)(good)
        self.assertEqual(self.helper._batch(dst_pointer, 1, dst.get_stride(), 8, 8, descriptors, 1), -1)
        self.assertEqual(self.helper._batch(dst_pointer, len(dst_data), dst.get_stride(), 8, 8,
                                          descriptors, self.helper.BATCH_LIMIT + 1), -1)
        descriptor_bytes = bytes(descriptors)
        self.assertEqual(self.helper._batch(ctypes.addressof(descriptors), ctypes.sizeof(descriptors),
                                          4, 1, 1, descriptors, 1), -1)
        self.assertEqual(bytes(descriptors), descriptor_bytes)
        self.assertEqual(dst_data, before)

    def test_batch_empty_noop_and_missing_batch_abi_keep_original_tile_fallback(self):
        src, _ = self.make_surface(cairo.FORMAT_ARGB32, 4, 4, 311)
        dst, data = self.make_surface(cairo.FORMAT_RGB24, 8, 8, 312)
        frame = self.helper.prepare_frame(cairo.Context(dst))
        before = bytes(data)
        calls = self.helper.native_calls
        self.assertTrue(frame.paint_tiles([]))
        self.assertEqual(self.helper.native_calls, calls)
        with patch.object(self.helper, '_batch', None):
            self.assertFalse(frame.paint_tiles([(src, 0, 0)]))
            self.assertEqual(data, before)
            self.assertTrue(frame.paint_tile(src, 0, 0))
        self.assertNotEqual(data, before)


if __name__ == '__main__':
    unittest.main()
