"""Bound native composition size without changing game/world coordinates.

The policy keeps the allocated canvas's aspect ratio, not an artificial 16:9
viewport inside a toolbar-shortened canvas. Rendering occurs once in a reusable
small RGB surface; ``present`` performs the sole aspect-preserving scale. No GTK
widget is proxied: input and dirty rectangles still refer to the real allocation.
"""
from dataclasses import dataclass
from collections import OrderedDict
import math
import os
import re
import threading

DEFAULT_CAP = (960, 540)
CAP_ENV = 'INK_SUITE_RENDER_CAP'
_presentation_buffers = threading.local()


def _presentation_buffer(width, height, pixel_format):
    """Two reusable RAM frames per drawing thread, without retaining sources."""
    import cairo
    buffers = getattr(_presentation_buffers, 'buffers', None)
    if buffers is None:
        buffers = _presentation_buffers.buffers = OrderedDict()
    key = width, height, pixel_format
    if key not in buffers:
        buffers[key] = cairo.ImageSurface(pixel_format, width, height)
        while len(buffers) > 2:
            buffers.popitem(last=False)
    buffers.move_to_end(key)
    return buffers[key]


def _size(value, name):
    if (not isinstance(value, (tuple, list)) or len(value) != 2 or
            any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in value)):
        raise ValueError(name + ' must contain two positive integer dimensions')
    return tuple(value)


@dataclass(frozen=True)
class FrameLayout:
    output_width: int
    output_height: int
    render_width: int
    render_height: int

    @property
    def output_size(self):
        return self.output_width, self.output_height

    @property
    def render_size(self):
        return self.render_width, self.render_height

    @property
    def scale(self):
        return min(self.output_width / self.render_width,
                   self.output_height / self.render_height)

    @property
    def offset(self):
        s = self.scale
        return ((self.output_width - self.render_width * s) / 2.,
                (self.output_height - self.render_height * s) / 2.)

    def display_point(self, x, y):
        """Render-pixel point -> real GTK canvas point, also useful for damage."""
        ox, oy = self.offset
        return ox + float(x) * self.scale, oy + float(y) * self.scale

    def render_point(self, x, y):
        """Real GTK canvas point -> render-pixel point, without clamping."""
        ox, oy = self.offset
        return (float(x) - ox) / self.scale, (float(y) - oy) / self.scale

    def normalized_point(self, x, y):
        """Real input point -> normalized rendered content; margins are inert."""
        x, y = self.render_point(x, y)
        if (not math.isfinite(x) or not math.isfinite(y) or
                not (0 <= x <= self.render_width and 0 <= y <= self.render_height)):
            return None
        return x / self.render_width, y / self.render_height

    def display_rect(self, x, y, width, height, pad=1):
        """Outward rounded render damage in actual GTK coordinates."""
        if width < 0 or height < 0 or pad < 0:
            raise ValueError('Damage extents and padding must be nonnegative')
        left, top = self.display_point(x, y)
        right, bottom = self.display_point(x + width, y + height)
        left, top = max(0, math.floor(left) - pad), max(0, math.floor(top) - pad)
        right = min(self.output_width, math.ceil(right) + pad)
        bottom = min(self.output_height, math.ceil(bottom) + pad)
        return left, top, max(0, right - left), max(0, bottom - top)

    def as_dict(self):
        return dict(output_size=list(self.output_size), render_size=list(self.render_size),
                    scale=self.scale, offset=list(self.offset))


class RenderPolicy:
    def __init__(self, cap=DEFAULT_CAP):
        self.cap = _size(cap, 'Render cap') if cap is not None else None

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        raw = environ.get(CAP_ENV, '960x540').strip().lower()
        if raw in ('disabled', 'uncapped', 'off', 'native', '0'):
            return cls(None)
        match = re.fullmatch(r'(\d{3,4})x(\d{3,4})', raw)
        if not match:
            raise ValueError(CAP_ENV + ' must be WIDTHxHEIGHT or uncapped')
        cap = tuple(map(int, match.groups()))
        if not (320 <= cap[0] <= 4096 and 180 <= cap[1] <= 2160):
            raise ValueError(CAP_ENV + ' is outside supported dimension bounds')
        return cls(cap)

    def layout(self, output_width, output_height):
        width, height = _size((output_width, output_height), 'Output size')
        factor = (min(1., self.cap[0] / width, self.cap[1] / height)
                  if self.cap else 1.)
        rw, rh = max(1, round(width * factor)), max(1, round(height * factor))
        if self.cap:
            rw, rh = min(rw, self.cap[0]), min(rh, self.cap[1])
        return FrameLayout(width, height, rw, rh)


def present(context, surface, layout, background=(.09, .14, .13)):
    """Present an opaque completed frame; retain the destination's dirty clip.

    A supplied RGB565 frame is valid only after the caller's original target
    visual/format guard. Keep that format; a fast preview sample is prepared in
    RAM before a 1:1 GTK upload. Brush antialiasing belongs to the small rendered
    source; the saved artwork is unchanged. The same-size fast path remains
    unchanged. Bilinear Cairo/XRender scaling is prohibitively slow on the
    board's 16-bit visual.
    """
    import cairo
    if (surface.get_width(), surface.get_height()) != layout.render_size:
        raise ValueError('Presented surface dimensions differ from render layout')
    if layout.render_size != layout.output_size:
        fmt = surface.get_format() if isinstance(surface, cairo.ImageSurface) else cairo.FORMAT_RGB24
        if fmt not in (cairo.FORMAT_RGB16_565, cairo.FORMAT_RGB24):
            fmt = cairo.FORMAT_RGB24
        frame = _presentation_buffer(*layout.output_size, fmt)
        scaled = cairo.Context(frame)
        scaled.set_source_rgb(*background)
        scaled.paint()
        ox, oy = layout.offset
        scaled.translate(ox, oy)
        scaled.scale(layout.scale, layout.scale)
        scaled.rectangle(0, 0, *layout.render_size)
        scaled.clip()
        scaled.set_source_surface(surface, 0, 0)
        scaled.get_source().set_filter(cairo.FILTER_NEAREST)
        scaled.get_source().set_extend(cairo.EXTEND_PAD)
        scaled.paint()
        frame.flush()
        # Retain the GTK destination's clip and matrix. No transformed source
        # pattern reaches X11; deferred upload also retains its Cairo surface.
        context.save()
        try:
            context.set_source_surface(frame, 0, 0)
            context.paint()
        finally:
            context.restore()
        return
    context.save()
    try:
        ox, oy = layout.offset
        # Exact coverage is the normal 16:9 case. Do not clear the entire X11
        # destination before the sole opaque frame blit.
        if ox or oy:
            context.set_source_rgb(*background)
            context.paint()
        context.translate(ox, oy)
        context.scale(layout.scale, layout.scale)
        context.set_source_surface(surface, 0, 0)
        context.get_source().set_filter(cairo.FILTER_BILINEAR)
        context.paint()
    finally:
        context.restore()


def bounded_window_size(work_width, work_height, preferred=(940, 510)):
    """Fit transient windows, leaving room for desktop/decorations at 540p."""
    width, height = _size((work_width, work_height), 'Work area')
    pw, ph = _size(preferred, 'Preferred window size')
    return min(pw, max(1, width - 24)), min(ph, max(1, height - 48))
