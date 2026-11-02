"""RGB565 presentation semantics; no display, camera or pose service required."""
import ctypes
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sword.rgb565 import RGB565Presenter, cairo


class AvailabilityTests(unittest.TestCase):
    def test_disabled_presenter_returns_original_object(self):
        with patch.dict(os.environ, {'INK_SWORD_DISABLE_RGB565': '1'}):
            presenter = RGB565Presenter()
        source = object()
        self.assertFalse(presenter.enabled)
        self.assertEqual(presenter.backend, 'cairo')
        self.assertIs(presenter.convert(source), source)
        self.assertEqual(presenter.calls, 0)
        self.assertEqual(presenter.fallback_calls, 1)

    def test_missing_library_returns_original_object(self):
        with tempfile.TemporaryDirectory() as temp:
            presenter = RGB565Presenter(temp)
        source = object()
        self.assertFalse(presenter.enabled)
        self.assertIs(presenter.convert(source), source)


NATIVE_AVAILABLE = (cairo is not None and hasattr(cairo, 'FORMAT_RGB16_565') and
                    platform.machine().lower() in ('aarch64', 'arm64') and bool(shutil.which('cc')))


@unittest.skipUnless(NATIVE_AVAILABLE, 'Requires AArch64, Cairo and cc for native conversion')
class NativeRGB565Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = Path(__file__).with_name('rgb565.c')
        library = Path(cls.temp.name) / 'libink_rgb565.so'
        subprocess.run(['cc', '-O3', '-std=c99', '-shared', '-fPIC', '-Wall', '-Wextra',
                        '-Werror', str(source), '-o', str(library)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        with patch.dict(os.environ, {'INK_SWORD_DISABLE_RGB565': '0'}):
            self.presenter = RGB565Presenter(self.temp.name)
        self.assertTrue(self.presenter.enabled)

    def source(self, width, height, seed=260, padding=0):
        stride = width * 4 + padding
        data = bytearray(random.Random(seed).randbytes(stride * height))
        return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_RGB24, width, height, stride), data

    def cairo_reference(self, source):
        target = cairo.ImageSurface(cairo.FORMAT_RGB16_565, source.get_width(), source.get_height())
        context = cairo.Context(target)
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_surface(source, 0, 0)
        context.paint()
        target.flush()
        return target

    def test_pixel_exact_to_cairo_with_odd_widths_and_source_row_padding(self):
        for width in (1, 3, 15, 16, 17, 31, 32, 33, 127):
            for padding in (0, 12):
                with self.subTest(width=width, padding=padding):
                    source, data = self.source(width, 7, padding=padding)
                    before = bytes(data)
                    reference = self.cairo_reference(source)
                    result = self.presenter.convert(source)
                    self.assertIsNot(result, source)
                    result.flush()
                    self.assertEqual(bytes(result.get_data()), bytes(reference.get_data()))
                    self.assertEqual(data, before)
        self.assertEqual(self.presenter.calls, 18)
        self.assertEqual(self.presenter.fallback_calls, 0)

    def test_reuses_output_and_updates_mutable_source_then_reallocates_on_resize(self):
        source, _ = self.source(17, 9)
        first = self.presenter.convert(source)
        old = bytes(first.get_data())
        context = cairo.Context(source)
        context.set_source_rgb(1, 0, 0)
        context.paint()
        second = self.presenter.convert(source)
        self.assertIs(second, first)
        self.assertNotEqual(bytes(second.get_data()), old)
        self.assertEqual(bytes(second.get_data()), bytes(self.cairo_reference(source).get_data()))
        replacement_source, _ = self.source(17, 9, 12)
        self.assertIs(self.presenter.convert(replacement_source), first)
        resized_source, _ = self.source(9, 3, 14)
        resized = self.presenter.convert(resized_source)
        self.assertIsNot(resized, first)
        self.assertEqual((resized.get_width(), resized.get_height()), (9, 3))
        self.assertIs(self.presenter._source, resized_source)

    def test_unsafe_formats_transforms_and_empty_surface_pass_through(self):
        surfaces = [cairo.ImageSurface(cairo.FORMAT_ARGB32, 5, 5),
                    cairo.ImageSurface(cairo.FORMAT_RGB16_565, 5, 5),
                    cairo.ImageSurface(cairo.FORMAT_RGB24, 0, 5)]
        shifted, _ = self.source(5, 5)
        shifted.set_device_offset(1, 0)
        surfaces.append(shifted)
        scaled, _ = self.source(5, 5)
        scaled.set_device_scale(2, 2)
        surfaces.append(scaled)
        for source in surfaces:
            source.flush()
            # Pycairo returns None for an empty ImageSurface on this board.
            before = bytes(source.get_data() or b'')
            self.assertIs(self.presenter.convert(source), source)
            self.assertEqual(bytes(source.get_data() or b''), before)
        self.assertIsNone(self.presenter.convert(None))
        self.assertEqual(self.presenter.calls, 0)

    def test_alias_is_rejected_before_writing(self):
        source, data = self.source(8, 7)
        # Construct shared storage explicitly to exercise the safety guard;
        # ordinary presenter allocation always owns a separate output buffer.
        target = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_RGB16_565,
                                                   8, 7, source.get_stride())
        view = target.get_data()
        anchor = ctypes.c_uint8.from_buffer(view)
        self.presenter._target = target
        self.presenter._target_buffer = (view, anchor, ctypes.addressof(anchor))
        before = bytes(data)
        self.assertIs(self.presenter.convert(source), source)
        self.assertEqual(data, before)
        self.assertEqual(self.presenter.calls, 0)

    def test_c_abi_respects_unaligned_buffers_odd_strides_and_guard_bytes(self):
        convert = self.presenter._lib.sword_rgb24_to_565
        for width in (1, 15, 16, 17, 33):
            height, ss, ds = 3, width * 4 + 3, width * 2 + 5
            source = bytearray([0xC7] * 5) + bytearray(random.Random(width).randbytes(ss * height)) + bytearray([0xA7] * 9)
            destination = bytearray([0xE3] * (3 + ds * height + 11))
            original = bytes(source)
            sa, da = ctypes.c_uint8.from_buffer(source), ctypes.c_uint8.from_buffer(destination)
            self.assertEqual(convert(ctypes.addressof(da) + 3, ds, ctypes.addressof(sa) + 5,
                                     ss, width, height), 0)
            expected = bytearray([0xE3] * len(destination))
            for y in range(height):
                for x in range(width):
                    pos = 5 + y * ss + x * 4
                    value = ((source[pos + 2] >> 3) << 11) | ((source[pos + 1] >> 2) << 5) | (source[pos] >> 3)
                    offset = 3 + y * ds + x * 2
                    expected[offset:offset + 2] = value.to_bytes(2, 'little')
            self.assertEqual(destination, expected)
            self.assertEqual(source, original)
            self.assertEqual(convert(ctypes.addressof(da), 1, ctypes.addressof(sa), ss,
                                     width, height), -1)


if __name__ == '__main__':
    unittest.main()
