"""Optional, pixel-exact NEON OVER for already rasterized integer sprite tiles.

Unsupported Cairo states return False before modifying the destination, so the
caller can use its original Cairo path. The renderer-only frame interface holds
at most 128 immutable source tiles and binds one target for a single draw call.
Source storage is additionally capped at 64 MiB, including full-size HUD tiles.
"""
from collections import OrderedDict
import ctypes
import math
import os
from pathlib import Path
import platform
import sys

try:
    import cairo
except ImportError:
    cairo = None


def _integer(value):
    # Device tile coordinates are normally Python ints; avoid a float
    # conversion and a repeated int allocation in the per-projectile path.
    return (isinstance(value, int) or
            isinstance(value, float) and math.isfinite(value) and int(value) == value)


class _BatchTile(ctypes.Structure):
    _fields_ = [('src', ctypes.c_void_p), ('src_bytes', ctypes.c_size_t),
                ('src_stride', ctypes.c_ssize_t), ('width', ctypes.c_int),
                ('height', ctypes.c_int), ('left', ctypes.c_int), ('top', ctypes.c_int)]


def _clip_blits(rectangles, left, top, sw, sh, dw, dh):
    """Return (destination x/y, source x/y, width/height), or None if unsafe."""
    values = (left, top, sw, sh, dw, dh)
    if not all(_integer(v) for v in values) or min(sw, sh, dw, dh) < 0:
        return None
    if max(sw, sh, dw, dh) > 2**31 - 1 or len(rectangles) > 128:
        return None
    checked = []
    for rect in rectangles:
        if len(rect) != 4 or not all(_integer(v) for v in rect):
            return None
        x, y, width, height = map(int, rect)
        if width < 0 or height < 0:
            return None
        if not width or not height:
            continue
        # Cairo rectangular clips are disjoint; reject an unexpected overlap
        # rather than accidentally apply OVER twice in the same pixels.
        if any(x < ox + ow and ox < x + width and y < oy + oh and oy < y + height
               for ox, oy, ow, oh in checked):
            return None
        checked.append((x, y, width, height))
    left, top, sw, sh, dw, dh = map(int, values)
    blits = []
    for x, y, width, height in checked:
        x0, y0 = max(0, left, x), max(0, top, y)
        x1, y1 = min(dw, left + sw, x + width), min(dh, top + sh, y + height)
        if x1 > x0 and y1 > y0:
            blits.append((x0, y0, x0 - left, y0 - top, x1 - x0, y1 - y0))
    return blits


class AlphaBlitter:
    SOURCE_CACHE_LIMIT = 128
    SOURCE_CACHE_BYTES = 64 * 1024 * 1024
    BATCH_LIMIT = 512

    def __init__(self, root=None):
        self.backend = 'cairo'
        self._lib = None
        self._batch = None
        self._batch_buffer = None
        self._batch_views = None
        self._batch_sources = None
        self.native_calls = 0
        self.batch_native_calls = 0
        self.fallback_calls = 0
        self.noop_calls = 0
        self._sources = OrderedDict()
        self._source_bytes = 0
        self._frame_size = None
        if (cairo is None or sys.byteorder != 'little' or
                platform.machine().lower() not in ('aarch64', 'arm64') or
                os.environ.get('INK_SWORD_DISABLE_NEON') == '1'):
            return
        path = (Path(root) if root is not None else Path(__file__).parent) / 'libink_alpha.so'
        try:
            # Each operation is a short, pure-C tile copy. Retain the GIL so a
            # background pose poll cannot interleave at every tile boundary.
            lib = ctypes.PyDLL(str(path.resolve()))
            lib.sword_alpha_backend.restype = ctypes.c_char_p
            if lib.sword_alpha_backend() != b'aarch64-neon':
                return
            lib.sword_alpha_over.argtypes = [
                ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p,
                ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_int]
            lib.sword_alpha_over.restype = ctypes.c_int
        except (OSError, AttributeError):
            return
        self._lib = lib
        self.backend = 'aarch64-neon'
        try:
            batch = lib.sword_alpha_batch
            batch.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ssize_t,
                              ctypes.c_int, ctypes.c_int, ctypes.POINTER(_BatchTile), ctypes.c_size_t]
            batch.restype = ctypes.c_int
            self._batch = batch
            self._batch_buffer = (_BatchTile * self.BATCH_LIMIT)()
            # Retain a bounded view of each descriptor instead of constructing
            # and copying a seven-field ctypes Structure for every projectile.
            self._batch_views = [self._batch_buffer[i] for i in range(self.BATCH_LIMIT)]
            self._batch_sources = [None] * self.BATCH_LIMIT
        except AttributeError:
            # Existing deployments with the old ABI retain paint_tile; callers
            # fall back as one untouched batch until the helper is upgraded.
            pass

    @property
    def enabled(self):
        return self._lib is not None

    def clear_sources(self):
        """Release immutable source references, also done on target resize."""
        self._sources.clear()
        self._source_bytes = 0

    def invalidate_source(self, surface):
        """Call before repainting a reused tile; the next read must flush it."""
        entry = self._sources.pop(id(surface), None)
        if entry is not None:
            self._source_bytes -= entry[7]

    def prepare_frame(self, context):
        """Bind the renderer's opaque full-frame target, or return None.

        The caller owns Cairo state for this draw: paint_tile intentionally does
        not re-read CTM/operator/clip. It takes device positions and explicit
        clips. Bind a fresh frame for each draw and use only immutable sources.
        """
        if self._lib is None:
            return None
        try:
            if tuple(context.get_matrix()) != (1., 0., 0., 1., 0., 0.):
                return None
            if context.get_operator() != cairo.OPERATOR_OVER:
                return None
            target = context.get_target()
            if context.get_group_target() != target or not isinstance(target, cairo.ImageSurface):
                return None
            if (target.get_format() != cairo.FORMAT_RGB24 or
                    target.get_device_offset() != (0., 0.) or
                    target.get_device_scale() != (1., 1.)):
                return None
            width, height = target.get_width(), target.get_height()
            if list(context.copy_clip_rectangle_list()) != [(0., 0., float(width), float(height))]:
                return None
            size = (width, height)
            if size != self._frame_size:
                self.clear_sources()
                self._frame_size = size
            target.flush()
            view = target.get_data()
            anchor = ctypes.c_uint8.from_buffer(view)
            return _FrameBlitter(self, target, width, height, view, anchor)
        except (TypeError, ValueError, BufferError, AttributeError, cairo.Error):
            return None

    def _immutable_source(self, surface):
        key = id(surface)
        entry = self._sources.get(key)
        if entry is not None:
            self._sources.move_to_end(key)
            return entry
        try:
            if (not isinstance(surface, cairo.ImageSurface) or
                    surface.get_format() != cairo.FORMAT_ARGB32 or
                    surface.get_device_offset() != (0., 0.) or
                    surface.get_device_scale() != (1., 1.)):
                return None
            surface.flush()
            view = surface.get_data()
            anchor = ctypes.c_uint8.from_buffer(view)
            entry = (surface, ctypes.addressof(anchor), surface.get_stride(),
                     surface.get_width(), surface.get_height(), view, anchor, len(view))
            # An unusually large one-off source may still be painted, but must
            # not displace or exceed the entire retained-source byte budget.
            if len(view) <= self.SOURCE_CACHE_BYTES:
                self._sources[key] = entry
                self._source_bytes += len(view)
                while (len(self._sources) > self.SOURCE_CACHE_LIMIT or
                       self._source_bytes > self.SOURCE_CACHE_BYTES):
                    _, evicted = self._sources.popitem(last=False)
                    self._source_bytes -= evicted[7]
            return entry
        except (TypeError, ValueError, BufferError, AttributeError, cairo.Error):
            return None

    def paint(self, context, surface, left_device, top_device, alpha=1.0):
        """Paint an integer-position tile, returning True only when handled.

        Currently accepts alpha 0 or 1 only. Other opacity values use Cairo
        unchanged; this avoids changing Cairo's float-to-mask quantization.
        """
        handled = self._paint(context, surface, left_device, top_device, alpha)
        if not handled:
            self.fallback_calls += 1
        return handled

    def _paint(self, context, surface, left_device, top_device, alpha):
        if self._lib is None or alpha not in (0, 1):
            return False
        if not _integer(left_device) or not _integer(top_device):
            return False
        try:
            if tuple(context.get_matrix()) != (1., 0., 0., 1., 0., 0.):
                return False
            if context.get_operator() != cairo.OPERATOR_OVER:
                return False
            target = context.get_target()
            if context.get_group_target() != target:
                return False
            if not isinstance(target, cairo.ImageSurface) or not isinstance(surface, cairo.ImageSurface):
                return False
            if surface.get_format() != cairo.FORMAT_ARGB32:
                return False
            fmt = target.get_format()
            if fmt not in (cairo.FORMAT_RGB24, cairo.FORMAT_ARGB32):
                return False
            for item in (surface, target):
                if item.get_device_offset() != (0., 0.) or item.get_device_scale() != (1., 1.):
                    return False
            rectangles = context.copy_clip_rectangle_list()
            blits = _clip_blits(rectangles, left_device, top_device,
                               surface.get_width(), surface.get_height(),
                               target.get_width(), target.get_height())
            if blits is None:
                return False
            if not blits or alpha == 0:
                self.noop_calls += 1
                return True
            # All eligibility checks precede raw access or any modifications.
            surface.flush()
            target.flush()
            src_view, dst_view = surface.get_data(), target.get_data()
            src_anchor = ctypes.c_uint8.from_buffer(src_view)
            dst_anchor = ctypes.c_uint8.from_buffer(dst_view)
            src_ptr, dst_ptr = ctypes.addressof(src_anchor), ctypes.addressof(dst_anchor)
            # Conservatively reject overlapping storage, including distinct
            # Cairo surface objects wrapping parts of the same Python buffer.
            if (src_ptr < dst_ptr + len(dst_view) and
                    dst_ptr < src_ptr + len(src_view)):
                return False
            src_stride, dst_stride = surface.get_stride(), target.get_stride()
            if (src_stride < surface.get_width() * 4 or
                    dst_stride < target.get_width() * 4):
                return False
        except (TypeError, ValueError, BufferError, AttributeError, cairo.Error):
            return False
        for dx, dy, sx, sy, width, height in blits:
            result = self._lib.sword_alpha_over(
                dst_ptr + dy * dst_stride + dx * 4, dst_stride,
                src_ptr + sy * src_stride + sx * 4, src_stride,
                width, height, 255, fmt == cairo.FORMAT_RGB24)
            if result != 0:
                # Returning False here could composite already-written regions
                # twice; this would indicate a programmer/ABI error instead.
                raise RuntimeError('Validated native alpha blit rejected its arguments')
            target.mark_dirty_rectangle(dx, dy, width, height)
            self.native_calls += 1
        return True


class _FrameBlitter:
    """Private single-draw binding; never cache this object across GTK frames."""
    def __init__(self, owner, target, width, height, view, anchor):
        self.owner = owner
        self.target = target
        self.width, self.height = width, height
        self._view, self._anchor = view, anchor
        self._ptr = ctypes.addressof(anchor)
        self._stride = target.get_stride()
        self._end = self._ptr + len(view)
        self._over = owner._lib.sword_alpha_over

    def paint_tiles(self, tiles):
        """Ordered full-frame OVER batch; False means NO tile was painted.

        ``tiles`` is a bounded list/tuple of (immutable ARGB32 source, x, y).
        Source entries are pinned even if cache eviction occurs while preparing
        the batch. Destination synchronization and the C boundary occur once.
        Unsupported/missing-batch helpers retain the ordinary tile fallback.
        """
        owner = self.owner
        if (owner._batch is None or not isinstance(tiles, (list, tuple)) or
                len(tiles) > owner.BATCH_LIMIT):
            owner.fallback_calls += 1
            return False
        if not tiles:
            owner.noop_calls += 1
            return True
        # A per-batch map avoids hundreds of repeated immutable-cache touches
        # when all bullets reuse just a few tile images. It also owns all views.
        pinned = {}
        descriptors = owner._batch_buffer
        try:
            for index, item in enumerate(tiles):
                if not isinstance(item, (tuple, list)) or len(item) != 3:
                    owner.fallback_calls += 1
                    return False
                surface, left, top = item
                if (not _integer(left) or not _integer(top) or
                        not -2**31 <= left <= 2**31 - 1 or not -2**31 <= top <= 2**31 - 1):
                    owner.fallback_calls += 1
                    return False
                key = id(surface)
                entry = pinned.get(key)
                if entry is None:
                    entry = owner._immutable_source(surface)
                    if entry is None:
                        owner.fallback_calls += 1
                        return False
                    pinned[key] = entry
                _, pointer, stride, width, height, _, _, length = entry
                if pointer < self._end and self._ptr < pointer + length:
                    owner.fallback_calls += 1
                    return False
                signature = (pointer, length, stride, width, height)
                descriptor = owner._batch_views[index]
                if owner._batch_sources[index] != signature:
                    descriptor.src, descriptor.src_bytes = pointer, length
                    descriptor.src_stride = stride
                    descriptor.width, descriptor.height = width, height
                    owner._batch_sources[index] = signature
                # Always rewrite both positions, including after an earlier
                # rejected batch. Validation/alias checks still precede C IO.
                descriptor.left, descriptor.top = int(left), int(top)
        except (TypeError, ValueError, OverflowError, BufferError, AttributeError):
            owner.fallback_calls += 1
            return False
        self.target.flush()
        result = owner._batch(self._ptr, len(self._view), self._stride, self.width,
                              self.height, descriptors, len(tiles))
        if result != 0:
            # The C ABI validates the complete descriptor array before writes.
            # Returning False is safe: no first tile can have been composited.
            owner.fallback_calls += 1
            return False
        self.target.mark_dirty()
        owner.native_calls += 1
        owner.batch_native_calls += 1
        return True

    def paint_tile(self, surface, left, top, regions=None):
        if not _integer(left) or not _integer(top):
            self.owner.fallback_calls += 1
            return False
        entry = self.owner._immutable_source(surface)
        if entry is None:
            self.owner.fallback_calls += 1
            return False
        _, pointer, stride, width, height, _, _, length = entry
        if pointer < self._end and self._ptr < pointer + length:
            self.owner.fallback_calls += 1
            return False
        left, top = int(left), int(top)
        if regions is None:
            x0, y0 = max(0, left), max(0, top)
            x1, y1 = min(self.width, left + width), min(self.height, top + height)
            blits = [(x0, y0, x0 - left, y0 - top, x1 - x0, y1 - y0)] if x1 > x0 and y1 > y0 else []
        else:
            blits = _clip_blits(regions, left, top, width, height, self.width, self.height)
            if blits is None:
                self.owner.fallback_calls += 1
                return False
        if not blits:
            self.owner.noop_calls += 1
            return True
        # Cairo may have drawn vectors between tiles, so flush the destination
        # each time. Immutable source buffers need flushing only when cached.
        self.target.flush()
        for dx, dy, sx, sy, w, h in blits:
            result = self._over(self._ptr + dy * self._stride + dx * 4, self._stride,
                                pointer + sy * stride + sx * 4, stride, w, h, 255, 1)
            if result != 0:
                raise RuntimeError('Validated frame alpha blit rejected its arguments')
            self.target.mark_dirty_rectangle(dx, dy, w, h)
            self.owner.native_calls += 1
        return True
