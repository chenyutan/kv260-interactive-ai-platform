"""Optional native RGB565 presenter for an explicitly matched X11 visual.

The caller must verify depth 16 and RGB masks f800/07e0/001f before use. The
game still renders at full RGB24 precision; this replaces only its final format
conversion. Unsupported surfaces and unavailable native libraries pass through.
"""
import ctypes
import os
from pathlib import Path
import platform
import sys

try:
    import cairo
except ImportError:
    cairo = None


class RGB565Presenter:
    def __init__(self, root=None):
        self.backend = 'cairo'
        self.calls = 0
        self.fallback_calls = 0
        self._lib = None
        self._target = None
        self._target_buffer = None
        self._source = None
        self._source_buffer = None
        if (cairo is None or not hasattr(cairo, 'FORMAT_RGB16_565') or
                sys.byteorder != 'little' or platform.machine().lower() not in ('aarch64', 'arm64') or
                os.environ.get('INK_SWORD_DISABLE_RGB565') == '1'):
            return
        path = (Path(root) if root is not None else Path(__file__).parent) / 'libink_rgb565.so'
        try:
            lib = ctypes.PyDLL(str(path.resolve()))
            lib.sword_rgb565_backend.restype = ctypes.c_char_p
            if lib.sword_rgb565_backend() != b'aarch64-neon':
                return
            lib.sword_rgb24_to_565.argtypes = [
                ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p,
                ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int]
            lib.sword_rgb24_to_565.restype = ctypes.c_int
        except (OSError, AttributeError):
            return
        self._lib = lib
        self.backend = 'aarch64-neon'

    @property
    def enabled(self):
        return self._lib is not None

    def _fallback(self, surface):
        self.fallback_calls += 1
        return surface

    def convert(self, surface):
        """Return a reusable RGB565 surface, or the original source on fallback.

        Returned RGB565 storage is overwritten by the next successful convert;
        display/copy it synchronously. Only one source and output are retained.
        """
        if self._lib is None:
            return self._fallback(surface)
        try:
            if (not isinstance(surface, cairo.ImageSurface) or
                    surface.get_format() != cairo.FORMAT_RGB24 or
                    surface.get_device_offset() != (0., 0.) or
                    surface.get_device_scale() != (1., 1.)):
                return self._fallback(surface)
            width, height, src_stride = surface.get_width(), surface.get_height(), surface.get_stride()
            if not (0 < width <= 2**31 - 1 and 0 < height <= 2**31 - 1 and src_stride >= width * 4):
                return self._fallback(surface)
            surface.flush()
            if surface is not self._source:
                source_view = surface.get_data()
                source_anchor = ctypes.c_uint8.from_buffer(source_view)
                self._source = surface
                self._source_buffer = (source_view, source_anchor, ctypes.addressof(source_anchor))
            source_view, source_anchor, source_pointer = self._source_buffer
            if len(source_view) < src_stride * height:
                return self._fallback(surface)
            if (self._target is None or self._target.get_width() != width or
                    self._target.get_height() != height):
                target = cairo.ImageSurface(cairo.FORMAT_RGB16_565, width, height)
                target.flush()
                target_view = target.get_data()
                target_anchor = ctypes.c_uint8.from_buffer(target_view)
                self._target = target
                self._target_buffer = (target_view, target_anchor, ctypes.addressof(target_anchor))
            target_view, target_anchor, target_pointer = self._target_buffer
            dst_stride = self._target.get_stride()
            if (dst_stride < width * 2 or len(target_view) < dst_stride * height or
                    (source_pointer < target_pointer + len(target_view) and
                     target_pointer < source_pointer + len(source_view))):
                return self._fallback(surface)
            self._target.flush()
            result = self._lib.sword_rgb24_to_565(target_pointer, dst_stride, source_pointer,
                                                src_stride, width, height)
            if result != 0:
                return self._fallback(surface)
            self._target.mark_dirty()
            self.calls += 1
            return self._target
        except (TypeError, ValueError, BufferError, AttributeError, cairo.Error):
            return self._fallback(surface)
