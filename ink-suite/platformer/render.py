"""320×180 pixel world with readable, cached monochrome pixel lettering.

Use ``Renderer().render(cr, game.snapshot(), width, height)``. No game clock,
camera, sensor or physics state is modified by rendering. ``--preview`` and
``--benchmark`` exercise deterministic scenes without GTK or camera access.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left, bisect_right
from collections import OrderedDict, deque
from functools import lru_cache
import io
import json
import math
from pathlib import Path
import statistics
import sys
import time

try:
    import cairo
except ImportError:
    import cairocffi as cairo

try:
    from PIL import Image as _PillowImage
except ImportError:
    _PillowImage = None

try:
    from . import art
except ImportError:
    import art

W, H, TILE = 320, 180, 16
FLOOR_Y, BACKGROUND_HORIZON_Y = 160, 152
GAME_TITLE = '超级玛丽'
TEXT_CACHE_BYTES = 6 * 1024 * 1024
NATIVE_SERVER_CACHE_BYTES = 8 * 1024 * 1024
THEMES = {
    'bamboo': {'sky': '#7dc4de', 'low': '#b9ddaf', 'far': '#83bfa4',
               'ridge': '#659c8e', 'mid': '#438f71', 'cloud': '#fff5df',
               'tree': '#326950', 'leaf': '#83b862', 'title': '草地',
               'haze': '#b3d2d2', 'abyss': '#172d3e'},
    'temple': {'sky': '#202b3e', 'low': '#35455a', 'far': '#2a394e',
               'ridge': '#52627a', 'mid': '#435269', 'cloud': '#79b4be',
               'tree': '#283447', 'leaf': '#7299a7', 'title': '地下通道',
               'haze': '#35455a', 'abyss': '#0b1424'},
    'moon': {'sky': '#38496b', 'low': '#bd8b85', 'far': '#59637f',
             'ridge': '#78839a', 'mid': '#454c65', 'cloud': '#e3b7aa',
             'tree': '#303b53', 'leaf': '#8b91a8', 'title': '城堡',
             'haze': '#737c94', 'abyss': '#141e31'},
}


@lru_cache(maxsize=128)
def _rgb(value):
    return art.rgb(value)


def _color(cr, value, alpha=1):
    cr.set_source_rgba(*_rgb(value), alpha)


def _rect(cr, x, y, w, h, color, alpha=1):
    _color(cr, color, alpha); cr.rectangle(int(x), int(y), int(w), int(h)); cr.fill()


def _poly(cr, points, color):
    _color(cr, color); cr.move_to(*points[0])
    for x, y in points[1:]:
        cr.line_to(int(x), int(y))
    cr.close_path(); cr.fill()


def _surface(w, h):
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))


def _normalize_glyph_mask(data, width, height, stride, rgb, *, byteorder=None):
    """Set binary ARGB pixels and return inclusive ink bounds; preserve padding.

    Pillow's point/paste/getbbox execute the cold-glyph scan in C. The optional
    fallback has identical threshold, color, transparent bytes and bounds.
    Cairo ARGB32 is native endian; decoding here never treats its premultiplied
    color channels as authoritative because the requested RGB replaces them.
    """
    byteorder = byteorder or sys.byteorder
    data = memoryview(data).cast('B')
    if _PillowImage is not None:
        raw_mode = 'BGRA' if byteorder == 'little' else 'ARGB'
        image = _PillowImage.frombytes('RGBA', (width, height), bytes(data),
                                       'raw', raw_mode, stride, 1)
        alpha = image.getchannel('A').point([0] * 128 + [255] * 128)
        bbox = alpha.getbbox()
        normalized = _PillowImage.new('RGBA', (width, height), (0, 0, 0, 0))
        normalized.paste((*rgb, 255), (0, 0, width, height), alpha)
        if byteorder == 'little':
            pixels = normalized.tobytes('raw', 'BGRA')
        else:
            # Pillow decodes ARGB but has no RGBA->ARGB raw packer. Reorder
            # channels with its C merge instead of a per-pixel Python loop.
            red, green, blue, alpha = normalized.split()
            pixels = _PillowImage.merge('RGBA', (alpha, red, green, blue)).tobytes()
        row_bytes = width * 4
        if stride == row_bytes:
            data[:height * stride] = pixels
        else:
            for yy in range(height):
                data[yy * stride:yy * stride + row_bytes] = pixels[yy * row_bytes:(yy + 1) * row_bytes]
        return (bbox[0], bbox[1], bbox[2] - 1, bbox[3] - 1) if bbox else (width, height, -1, -1)
    opaque = bytes((rgb[2], rgb[1], rgb[0], 255) if byteorder == 'little'
                   else (255, rgb[0], rgb[1], rgb[2]))
    alpha_index = 3 if byteorder == 'little' else 0
    left, top, right, bottom = width, height, -1, -1
    for yy in range(height):
        for xx in range(width):
            offset = yy * stride + xx * 4
            if data[offset + alpha_index] >= 128:
                data[offset:offset + 4] = opaque
                left, top, right, bottom = min(left, xx), min(top, yy), max(right, xx), max(bottom, yy)
            else:
                data[offset:offset + 4] = b'\0\0\0\0'
    return left, top, right, bottom


class LRU:
    def __init__(self, limit, max_bytes=None):
        self.limit, self.data = limit, OrderedDict()
        self.max_bytes = max_bytes
        self.hits = self.misses = self.builds = 0

    def get(self, key, build):
        if key in self.data:
            self.hits += 1
            self.data.move_to_end(key)
            return self.data[key]
        self.misses += 1
        value = build(); self.builds += 1
        self.data[key] = value
        while len(self.data) > self.limit or (self.max_bytes is not None and self.bytes() > self.max_bytes):
            self.data.popitem(last=False)
        return value

    def bytes(self):
        return sum(value.get_stride() * value.get_height() for value in self.data.values())


class Renderer:
    """Pixel atlas, parallax strips, visible terrain chunks, and cached text."""
    CHUNK = 256
    MAX_PARTICLES = 64

    def __init__(self):
        # Every frame starts with the full opaque sky. Keep alpha on cached
        # sprites/layers, but expose an opaque framebuffer to native X11.
        self.buffer = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        self.context = cairo.Context(self.buffer)
        self.context.set_antialias(cairo.ANTIALIAS_NONE)
        self.sprites, self.backgrounds = {}, {}
        self.underlays, self.backdrops = LRU(8), LRU(8)
        self._last_backdrop_key = None
        self.backdrop_direct_composites = 0
        self.underlay_build_times, self.backdrop_build_times = deque(maxlen=240), deque(maxlen=240)
        self.chunks, self.texts, self.huds, self.overlays = LRU(16), LRU(64, TEXT_CACHE_BYTES), LRU(8), LRU(12)
        self.frame_times = deque(maxlen=240)
        self.submission_times = deque(maxlen=240)
        self.group_submission_times = deque(maxlen=240)
        self.native_submission_times = deque(maxlen=240)
        self.native_build_times = deque(maxlen=240)
        self._native_output = self._native_output_context = self._native_output_binding = None
        self._native_output_estimated_bytes = 0
        self._native_output_builds = self._native_output_hits = self._native_output_releases = 0
        self._native_release_errors = 0
        self._native_fallback_reason = 'not-yet-rendered'
        self._presentation = 'temporary-opaque-cairo-group-single-paint'
        self.frames = 0
        self.last_counts = {}
        self.last_layout = {}
        self.last_text_layout = []
        self._pango = None
        self._font_mode = 'Cairo native pixel cached'
        self._text_info = {}
        self._last_text_info = {}
        try:
            import gi
            gi.require_version('PangoCairo', '1.0')
            from gi.repository import Pango, PangoCairo
            self._pango = (Pango, PangoCairo)
            self._font_mode = 'Pango native pixel cached'
        except (ImportError, ValueError):
            pass
        self._build_atlas()

    def _build_sprite(self, rows):
        surface = _surface(max(map(len, rows)), len(rows))
        cr = cairo.Context(surface); cr.set_antialias(cairo.ANTIALIAS_NONE)
        art.paint_rows(cr, rows)
        return surface

    def _build_atlas(self):
        for state, frames in (('idle', 2), ('running', 4), ('jumping', 1), ('hurt', 1), ('dead', 1)):
            for frame in range(frames):
                for facing in (-1, 1):
                    for power in (False, True):
                        self.sprites[('player', state, frame, facing, power)] = self._build_sprite(
                            art.player(frame, state, facing, power))
        for kind in ('ground', 'foundation', 'brick', 'question', 'used', 'pipe', 'cave_ground', 'castle_ground', 'hall_ground'):
            for variant in range(4):
                self.sprites[(kind, variant)] = self._build_sprite(art.tile(kind, variant))
        for frame in range(4):
            self.sprites[('coin', frame)] = self._build_sprite(art.coin(frame))
            self.sprites[('lantern', frame)] = self._build_sprite(art.lantern(frame))
            self.sprites[('stone', frame)] = self._build_sprite(art.stone(frame))
            self.sprites[('powerup', frame)] = self._build_sprite(art.powerup(frame))
            self.sprites[('flag', frame)] = self._build_sprite(art.flag(frame))
        self.sprites['heart'] = self._build_sprite(art.heart())
        self.sprites['heart_empty'] = self._build_sprite(art.heart(False))
        self.sprites['portal'] = self._portal()
        self.sprites['castle_hall'] = self._castle_hall()
        for frame in range(2):
            self.sprites[('princess', frame)] = self._build_sprite(art.princess(frame))
            self.sprites[('checkpoint', bool(frame))] = self._build_sprite(art.checkpoint(bool(frame)))

    def _portal(self):
        # Lip exactly matches the collision top at y=8; no false standing edge.
        s = _surface(32, 40); cr = cairo.Context(s); cr.set_antialias(cairo.ANTIALIAS_NONE)
        _rect(cr, 3, 8, 26, 32, '#20313f')
        _rect(cr, 5, 11, 22, 29, '#328c86')
        _rect(cr, 7, 11, 4, 29, '#73c5a0'); _rect(cr, 23, 11, 3, 29, '#205b65')
        _rect(cr, 0, 8, 32, 9, '#20313f'); _rect(cr, 1, 9, 30, 6, '#328c86')
        _rect(cr, 2, 9, 27, 2, '#73c5a0'); _rect(cr, 3, 11, 5, 4, '#bdd3b9')
        return s

    def _castle_hall(self):
        """One cached cutaway room strip; no per-frame architecture paths."""
        s = _surface(640, 160); cr = cairo.Context(s); cr.set_antialias(cairo.ANTIALIAS_NONE)
        _rect(cr, 0, 0, 640, 160, '#293244')
        for y in range(24, 160, 16):
            _rect(cr, 0, y, 640, 1, '#394359')
            for x in range((y // 16 % 2)*16, 640, 32):
                _rect(cr, x, y, 1, 16, '#394359')
        _rect(cr, 0, 28, 640, 5, '#758099')
        for x in range(30, 640, 96):
            _rect(cr, x, 45, 24, 46, '#121e32')
            _poly(cr, ((x, 45), (x+12, 33), (x+24, 45)), '#121e32')
            _rect(cr, x+3, 47, 18, 40, '#536b94')
            _rect(cr, x+11, 43, 2, 47, '#8b91a8'); _rect(cr, x+1, 65, 22, 2, '#8b91a8')
            _rect(cr, x+46, 34, 12, 120, '#526079')
            _rect(cr, x+48, 37, 3, 114, '#78839a')
            _rect(cr, x+42, 33, 20, 6, '#8b91a8'); _rect(cr, x+42, 151, 20, 9, '#8b91a8')
            _rect(cr, x+72, 51, 12, 31, '#a74f63')
            _poly(cr, ((x+72, 81), (x+78, 88), (x+84, 81)), '#a74f63')
            _rect(cr, x+76, 58, 4, 11, '#e5b65d')
        # Threshold at the left makes the exterior-to-interior boundary legible.
        _rect(cr, 0, 24, 12, 136, '#8b91a8'); _rect(cr, 2, 26, 3, 134, '#bdc1c9')
        return s

    @staticmethod
    def _blit(cr, surface, x, y):
        x, y = int(x), int(y)
        cr.set_source_surface(surface, x, y)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        # Cached surfaces use OVER except for the complete sky. Restrict work
        # to their bounds instead of compositing transparent pixels across the
        # entire current clip for every small sprite.
        cr.rectangle(x, y, surface.get_width(), surface.get_height())
        cr.fill()

    def _text(self, value, size=9, color='#fff5cf', bold=False, width=None, *, height=None, align='center'):
        """Cache hard-edged pixel lettering without reducing the world detail.

        Large labels use two output pixels per glyph pixel. CJK labels prefer
        at least 16 source pixels where the box permits; tighter small-display
        boxes retain one output pixel so interior strokes remain readable. Glyphs
        are measured before positioning; integer coordinates and a binary
        alpha mask prevent subpixel fringes or frame-dependent font rasterizing.
        """
        value = str(value)
        width = max(1, int(width or math.ceil(len(value) * size * 1.1 + 4)))
        height = max(1, int(height or math.ceil(size * 1.65 + 4)))
        size = max(1, float(size))
        cjk = any('\u2e80' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff' for char in value)
        raster_size = max(size, 32) if cjk and height >= 32 else size
        initial_grid = 2 if raster_size >= 32 else 1
        key = value, size, color, bold, width, height, align, initial_grid
        def build():
            options = cairo.FontOptions()
            options.set_antialias(cairo.ANTIALIAS_NONE)
            options.set_hint_style(cairo.HINT_STYLE_FULL)
            options.set_hint_metrics(cairo.HINT_METRICS_ON)
            grid = initial_grid
            while True:
                sw, sh = max(1, width // grid), max(1, height // grid)
                mask = _surface(sw, sh); cr = cairo.Context(mask)
                cr.set_antialias(cairo.ANTIALIAS_NONE); cr.set_font_options(options)
                font_pixels = max(1, int(raster_size / grid))
                if self._pango:
                    Pango, PangoCairo = self._pango
                    layout = PangoCairo.create_layout(cr)
                    layout.set_text(value, -1); layout.set_width(-1)
                    layout.set_single_paragraph_mode(True)
                    desc = Pango.FontDescription('Noto Sans CJK SC Bold' if bold else 'Noto Sans CJK SC')
                    PangoCairo.context_set_font_options(layout.get_context(), options)
                    def measure(pixels):
                        desc.set_absolute_size(pixels * Pango.SCALE); layout.set_font_description(desc)
                        ink, unused_logical = layout.get_pixel_extents()
                        return ink.x, ink.y, ink.width, ink.height
                    def paint(x, y):
                        _color(cr, color); cr.move_to(x, y); PangoCairo.show_layout(cr, layout)
                else:
                    fontpath = None
                    try:
                        from PIL import Image, ImageDraw, ImageFont
                        candidates = ('C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc',
                                      '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc')
                        fontpath = next((p for p in candidates if Path(p).exists()), None)
                    except ImportError:
                        pass
                    if fontpath:
                        holder = {}
                        im = Image.new('1', (sw, sh)); draw = ImageDraw.Draw(im); draw.fontmode = '1'
                        def measure(pixels):
                            holder['font'] = ImageFont.truetype(fontpath, pixels)
                            x0, y0, x1, y1 = draw.textbbox((0, 0), value, font=holder['font'])
                            return x0, y0, x1-x0, y1-y0
                        def paint(x, y):
                            draw.text((x, y), value, font=holder['font'], fill=1)
                            rgba = Image.new('RGBA', (sw, sh), color); rgba.putalpha(im.convert('L'))
                            stream = io.BytesIO(); rgba.save(stream, format='PNG'); stream.seek(0)
                            self._blit(cr, cairo.ImageSurface.create_from_png(stream), 0, 0)
                        self._font_mode = 'Pillow native pixel cached'
                    else:
                        cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL,
                                            cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
                        def measure(pixels):
                            cr.set_font_size(pixels)
                            xb, yb, iw, ih, unused_xa, unused_ya = cr.text_extents(value)
                            return int(math.floor(xb)), int(math.floor(yb)), int(math.ceil(iw)), int(math.ceil(ih))
                        def paint(x, y):
                            _color(cr, color); cr.move_to(x, y); cr.show_text(value)
                xb, yb, iw, ih = measure(font_pixels)
                if iw > sw or ih > sh:
                    font_pixels = max(1, int(font_pixels * min(sw/max(1, iw), sh/max(1, ih))))
                    xb, yb, iw, ih = measure(font_pixels)
                    while font_pixels > 1 and (iw > sw or ih > sh):
                        font_pixels -= 1; xb, yb, iw, ih = measure(font_pixels)
                # A 2x grid must not discard CJK details by shrinking its font
                # beneath 16 source pixels. Small boxes use the denser grid.
                if grid == 2 and font_pixels < 16:
                    grid = 1; continue
                tx = 0 if align == 'left' else sw-iw if align == 'right' else (sw-iw)//2
                paint(max(0, tx)-xb, max(0, (sh-ih)//2)-yb)
                break
            # Normalize every backend to precisely binary alpha. This also
            # removes any antialiased bitmap-font coverage from font fallback.
            mask.flush(); data = memoryview(mask.get_data()).cast('B')
            rgb = tuple(round(channel*255) for channel in _rgb(color))
            left, top, right, bottom = _normalize_glyph_mask(data, sw, sh, mask.get_stride(), rgb)
            mask.mark_dirty()
            surface = _surface(width, height); cc = cairo.Context(surface)
            cc.set_antialias(cairo.ANTIALIAS_NONE); cc.scale(grid, grid)
            cc.set_source_surface(mask, 0, 0); cc.get_source().set_filter(cairo.FILTER_NEAREST)
            cc.rectangle(0, 0, sw, sh); cc.fill()
            self._text_info[key] = dict(pixel_grid=grid, source_font_pixels=font_pixels,
                font_pixels=font_pixels*grid, requested_font_pixels=size,
                source_size=[sw, sh], ink_box=[left*grid, top*grid, (right-left+1)*grid, (bottom-top+1)*grid]
                if right >= left else [0, 0, 0, 0])
            return surface
        surface = self.texts.get(key, build)
        self._last_text_info = self._text_info[key]
        if len(self._text_info) > len(self.texts.data):
            self._text_info = {k: self._text_info[k] for k in self.texts.data}
        return surface

    def _background(self, theme):
        if theme in self.backgrounds:
            return self.backgrounds[theme]
        p = THEMES[theme]
        sky = _surface(W, H); cr = cairo.Context(sky); cr.set_antialias(cairo.ANTIALIAS_NONE)
        _rect(cr, 0, 0, W, H, p['sky']); _rect(cr, 0, 100, W, 80, p['low'])
        # Reserve the floor-depth region for the real terrain layer. Previously
        # a pale-green sky strip and parallax shrubs continued through holes,
        # making a missing collision tile look like a safe green platform.
        _rect(cr, 0, BACKGROUND_HORIZON_Y, W, FLOOR_Y-BACKGROUND_HORIZON_Y, p['haze'])
        _rect(cr, 0, FLOOR_Y, W, H-FLOOR_Y, p['abyss'])
        far, clouds, mid = (_surface(640, H) for _ in range(3))
        if theme == 'bamboo':
            _rect(cr, 246, 33, 23, 23, '#fff0a1'); _rect(cr, 242, 38, 31, 13, '#fff0a1')
            cc = cairo.Context(far); cc.set_antialias(cairo.ANTIALIAS_NONE)
            for x in range(-100, 640, 140):
                _poly(cc, ((x,160),(x+35,94),(x+65,72),(x+89,79),(x+140,160)), p['far'])
                _poly(cc, ((x+65,72),(x+89,79),(x+140,160),(x+102,160)), p['ridge'])
            cc = cairo.Context(clouds)
            for x,y in ((28,43),(193,69),(350,36),(522,61)):
                _rect(cc,x,y,57,10,p['cloud']); _rect(cc,x+9,y-7,34,7,p['cloud'])
                _rect(cc,x+17,y-12,18,5,p['cloud'])
            cc = cairo.Context(mid); cc.set_antialias(cairo.ANTIALIAS_NONE)
            for x in (30,180,345,530):
                _rect(cc,x+14,106,7,54,'#665950')
                _rect(cc,x,83,38,31,p['tree']); _rect(cc,x+5,74,28,40,p['tree'])
                _rect(cc,x+4,82,29,12,p['leaf']); _rect(cc,x+10,76,18,7,p['leaf'])
                _rect(cc,x+16,110,2,43,'#99836b')
            for x in range(0,640,74):
                _rect(cc,x,150,40,10,p['mid']); _rect(cc,x+8,145,25,8,p['leaf'])
        elif theme == 'temple':
            cc = cairo.Context(far); cc.set_antialias(cairo.ANTIALIAS_NONE)
            for x in range(0,640,64):
                _poly(cc,((x,25),(x+64,25),(x+53,74),(x+35,98),(x+20,58)),p['far'])
                _poly(cc,((x,180),(x+16,120),(x+37,103),(x+64,180)),p['ridge'])
            cc = cairo.Context(clouds)
            for x,y in ((36,63),(174,107),(342,75),(514,100)):
                _rect(cc,x,y,2,2,'#c6e4d7'); _rect(cc,x+6,y-4,1,1,p['cloud'])
            cc = cairo.Context(mid); cc.set_antialias(cairo.ANTIALIAS_NONE)
            _rect(cc,0,25,640,7,p['tree'])
            for x in range(0,640,96):
                _poly(cc,((x,31),(x+29,31),(x+18,65)),p['mid'])
                _rect(cc,x+59,67,7,93,p['tree']); _rect(cc,x+56,64,13,7,p['leaf'])
                _poly(cc,((x+8,160),(x+12,136),(x+19,151),(x+27,131),(x+31,160)),'#4b929f')
                _poly(cc,((x+19,151),(x+27,131),(x+26,158)),'#9ad2cb')
        else:
            _rect(cr,244,34,23,23,'#f6dcc4'); _rect(cr,240,39,31,13,'#f6dcc4')
            cc = cairo.Context(far); cc.set_antialias(cairo.ANTIALIAS_NONE)
            for x in range(-40,640,150):
                _poly(cc,((x,160),(x+57,70),(x+82,90),(x+150,160)),p['far'])
            cc = cairo.Context(clouds)
            for x,y in ((30,52),(210,35),(390,63),(550,39)):
                _rect(cc,x,y,66,4,p['cloud']); _rect(cc,x+12,y-3,35,3,p['cloud'])
            cc = cairo.Context(mid); cc.set_antialias(cairo.ANTIALIAS_NONE)
            for x in (35,350):
                _rect(cc,x,102,180,58,p['tree'])
                for xx,top in ((x,65),(x+64,43),(x+144,65)):
                    _rect(cc,xx,top,36,95,p['mid']); _rect(cc,xx+3,top,4,95,p['ridge'])
                    for dx in (0,12,24):_rect(cc,xx+dx,top-8,8,10,p['mid'])
                    _rect(cc,xx+14,top+19,8,17,'#d7b38d')
                _rect(cc,x+78,119,24,41,'#1f2d43')
                _poly(cc,((x+78,119),(x+90,107),(x+102,119)),'#1f2d43')
                _rect(cc,x+80,20,2,22,'#bdc1c9'); _rect(cc,x+82,20,16,9,'#b75746')
        # Keep distant silhouettes above a narrow atmospheric horizon. This
        # clear separation prevents a bush or castle silhouette from forming
        # a false bridge at y=160. This is cold-cache work only; foreground
        # tiles cover the dark depth exactly where actual solids exist.
        for layer in (far, clouds, mid):
            clear = cairo.Context(layer)
            clear.set_operator(cairo.OPERATOR_CLEAR)
            clear.rectangle(0, BACKGROUND_HORIZON_Y, layer.get_width(), H-BACKGROUND_HORIZON_Y)
            clear.fill()
        self.backgrounds[theme] = sky, far, clouds, mid
        return self.backgrounds[theme]

    def _wrap(self, cr, surface, camera, factor):
        offset = int(camera * factor) % surface.get_width()
        self._blit(cr, surface, -offset, 0)
        self._blit(cr, surface, surface.get_width() - offset, 0)

    def _underlay(self, theme, camera):
        sky, far, clouds, unused_mid = self._background(theme)
        key = theme, int(camera * .22) % far.get_width(), int(camera * .10) % clouds.get_width()
        def build():
            begin = time.perf_counter()
            surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
            cr = cairo.Context(surface); cr.set_antialias(cairo.ANTIALIAS_NONE)
            cr.set_operator(cairo.OPERATOR_SOURCE); self._blit(cr, sky, 0, 0)
            cr.set_operator(cairo.OPERATOR_OVER)
            self._wrap(cr, far, camera, .22); self._wrap(cr, clouds, camera, .10)
            self.underlay_build_times.append((time.perf_counter() - begin) * 1000)
            return surface
        return self.underlays.get(key, build)

    def _backdrop(self, theme, camera):
        unused_sky, far, clouds, mid = self._background(theme)
        key = (theme, int(camera * .22) % far.get_width(),
               int(camera * .10) % clouds.get_width(), int(camera * .46) % mid.get_width())
        def build():
            begin = time.perf_counter()
            surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
            cr = cairo.Context(surface); cr.set_antialias(cairo.ANTIALIAS_NONE)
            cr.set_operator(cairo.OPERATOR_SOURCE); self._blit(cr, self._underlay(theme, camera), 0, 0)
            cr.set_operator(cairo.OPERATOR_OVER)
            self._wrap(cr, mid, camera, .46)
            self.backdrop_build_times.append((time.perf_counter() - begin) * 1000)
            return surface
        return self.backdrops.get(key, build)

    def _paint_backdrop(self, cr, theme, camera):
        unused_sky, far, clouds, mid = self._background(theme)
        key = (theme, int(camera * .22) % far.get_width(),
               int(camera * .10) % clouds.get_width(), int(camera * .46) % mid.get_width())
        cr.set_operator(cairo.OPERATOR_SOURCE)
        if key in self.backdrops.data or key == self._last_backdrop_key:
            # A repeated position amortizes a complete backdrop. Existing
            # entries retain the same LRU promotion and hit accounting.
            self._blit(cr, self._backdrop(theme, camera), 0, 0)
        else:
            # While scrolling, composite the same opaque underlay and mid
            # strips straight into the RGB24 world buffer. This avoids a
            # temporary complete backdrop and its additional full-frame copy.
            self._blit(cr, self._underlay(theme, camera), 0, 0)
            cr.set_operator(cairo.OPERATOR_OVER)
            self._wrap(cr, mid, camera, .46)
            self.backdrop_direct_composites += 1
        self._last_backdrop_key = key

    def _particles(self, cr, particles, camera):
        counts, pending_color = 0, None
        for particle in particles[:self.MAX_PARTICLES]:
            x, y = float(particle.get('x', 0)) - camera, float(particle.get('y', 0))
            if 0 <= x < W and 0 <= y < H:
                kind = str(particle.get('kind', 'spark'))
                color = '#fff0a1' if kind in ('spark', 'coin', 'stomp') else '#73c5a0'
                if color != pending_color:
                    if pending_color is not None:
                        cr.fill()
                    _color(cr, color)
                    pending_color = color
                # Each particle is an opaque, integer-aligned 2x2 rectangle.
                # Batch consecutive equal colors; flush at color changes so
                # overlaps retain the original particle order and appearance.
                cr.rectangle(int(x), int(y), 2, 2)
                counts += 1
        if pending_color is not None:
            cr.fill()
        return counts

    def _terrain_partitions(self, tiles, start, end):
        lefts = [index * self.CHUNK for index in range(start, end + 1)]
        rights = [left + self.CHUNK for left in lefts]
        groups = [[] for _ in lefts]
        for tile in tiles:
            x = float(tile['x'])
            if not x < rights[-1]:
                continue
            right = x + float(tile.get('w', TILE))
            if not right > lefts[0]:
                continue
            # These searches select exactly the original x < chunk_right and
            # right > chunk_left inequalities; no floor/rounding of geometry.
            first, stop = bisect_right(rights, x), bisect_left(lefts, right)
            if first >= stop:
                continue
            signature = (int(tile['x']), int(tile['y']), int(tile.get('w', TILE)),
                         int(tile.get('h', TILE)), str(tile.get('kind', 'ground')))
            for index in range(first, stop):
                groups[index].append(signature)
        return groups

    def _terrain(self, cr, snapshot, camera):
        counts = 0
        start, end = int(camera) // self.CHUNK, (int(camera) + W - 1) // self.CHUNK
        groups = self._terrain_partitions(snapshot.get('tiles', ()), start, end)
        for index, members in zip(range(start, end + 1), groups):
            left = index * self.CHUNK
            signature = tuple(members)
            key = snapshot.get('level_index', 0), index, signature
            def build():
                s = _surface(self.CHUNK, 192); cc = cairo.Context(s)
                cc.set_antialias(cairo.ANTIALIAS_NONE)
                for x, y, w, h, kind in signature:
                    variant = (x // TILE + y // TILE) % 4
                    tile = self.sprites.get((kind, variant), self.sprites[('brick', variant)])
                    cc.save(); cc.rectangle(x - left, y, w, h); cc.clip()
                    for yy in range(y, y + h, TILE):
                        for xx in range(x, x + w, TILE):
                            cell = self.sprites[('foundation', variant)] if kind == 'ground' and yy > y else tile
                            self._blit(cc, cell, xx - left, yy)
                    cc.restore()
                    if kind == 'ground' and y <= 160 and x // TILE % 9 == 2:
                        _rect(cc, x - left + 3, y - 3, 1, 3, '#4e7858')
                        _rect(cc, x - left + 2, y - 4, 3, 1, '#93b473')
                return s
            self._blit(cr, self.chunks.get(key, build), left - camera, 0)
            counts += len(members)
        return counts

    def _hud(self, snapshot, theme):
        lives = max(0, min(9, int(snapshot.get('lives', 3))))
        def build():
            s = _surface(W, 25); cc = cairo.Context(s)
            _rect(cc, 6, 5, 308, 17, '#20313f', .90)
            _rect(cc, 7, 5, 306, 1, '#8ca3a4', .7)
            for i in range(5):
                self._blit(cc, self.sprites['heart' if i < lives else 'heart_empty'], 10 + i * 8, 9)
            self._blit(cc, self.sprites[('coin', 0)], 55, 7)
            return s
        return self.huds.get(lives, build)

    @staticmethod
    def _control_info(snapshot):
        """Finite corner feedback from the published control state only."""
        ui = snapshot.get('ui') or {}
        control = ui.get('control')
        if not isinstance(control, dict) or not control:
            return None
        if ui.get('control_mode')=='gamepad':
            return dict(move=control.get('move',0),ready=bool(control.get('valid')),calibration='ready',progress=1.,primary='左摇杆移动',secondary='A 跳跃 · B 进入管道')
        calibration = str(control.get('calibration', 'waiting'))
        valid = bool(control.get('valid', False))
        reason = str(control.get('reason') or '')
        offsets = (control.get('joystick_dx'), control.get('joystick_dy'))
        right_tracked = (valid and reason != 'missing_right_arm'
                         and all(isinstance(value, (int, float)) and math.isfinite(value)
                                 for value in offsets))
        ready = calibration == 'ready' and right_tracked
        move = control.get('move', 0)
        move = move if isinstance(move, (int, float)) and math.isfinite(move) else 0
        move = (-1 if move < 0 else 1 if move > 0 else 0) if ready else 0
        progress = control.get('calibration_progress', 0)
        progress = progress if isinstance(progress, (int, float)) and math.isfinite(progress) else 0
        progress = max(0., min(1., progress))
        if calibration == 'body_changed':
            primary = '右手放松稍停'
        elif calibration == 'frozen':
            primary = '面向画面恢复站姿'
        elif calibration == 'calibrating':
            primary = '舒适位置稍停'
        elif calibration == 'ready' and valid and not right_tracked:
            primary = '等待右手入镜'
        elif not ready:
            primary = '右手放松稍停' if calibration != 'ready' else '等待手臂入镜'
        else:
            primary = '右手 ' + {-1: '左', 0: '停', 1: '右'}[move]
        if calibration == 'ready' and valid:
            secondary = ('左手已准备' if control.get('jump_ready') else
                         '等待左手入镜' if reason == 'missing_left_arm' else '放低左手准备')
        elif calibration == 'frozen':
            secondary = '站回原来的距离'
        else:
            secondary = '腰腹至下胸 · 放松弯肘'
        return dict(move=move, ready=ready, calibration=calibration,
                    progress=progress, primary=primary, secondary=secondary)

    def _paint_control_hud(self, cr, snapshot):
        return

    @staticmethod
    def _route_hint(snapshot):
        return ''

    @staticmethod
    def _overlay_info(snapshot):
        state = str(snapshot.get('state', 'playing'))
        ui = snapshot.get('ui', {})
        if ui.get('ready'):
            state = 'menu'
        countdown = max(1, min(3, math.ceil(float(snapshot.get('countdown', 3)))))
        if state in ('menu', 'title', 'idle', 'ready', 'intro'):
            words = GAME_TITLE, '穿过草地和地下通道，到城堡寻找公主', '准备好后确认开始'
        elif state == 'countdown':
            words = str(countdown), '准备出发', ''
        elif state in ('paused', 'pause'):
            words = '已暂停', '可以继续游戏或重新开始', '在菜单中选择继续'
        elif state in ('game_over', 'gameover', 'lost', 'over'):
            words = '游戏结束', '再试一次吧', '确认重新开始'
        elif state in ('victory', 'won', 'complete'):
            words = '找到公主了！', '冒险完成，谢谢你的帮助！', '确认重新开始'
        elif state in ('level_complete', 'stage_clear', 'level_transition', 'transition'):
            transition = str(snapshot.get('transition_kind', 'stage'))
            if transition == 'pipe':
                words = '进入管道', '前往奖励路线', ''
            elif transition == 'respawn':
                words = '重新出发', '正在返回保存的位置', ''
            else:
                words = '过关！', '继续下一关', '即将进入下一关'
        elif state in ('respawn', 'dying'):
            words = '重新出发', '正在返回保存的位置', ''
        else:
            words = None
        if words and ui.get('control_mode')=='gamepad':
            words=(words[0],words[1],'按 X 开始 / 重开' if state!='paused' else 'Menu 键打开菜单继续')
        return state, words

    def _overlay(self, snapshot):
        state, words = self._overlay_info(snapshot)
        if state in ('playing', 'running', 'active'):
            return None
        def build():
            s = _surface(W, H); cc = cairo.Context(s)
            _rect(cc, 0, 0, W, H, '#172d3e', .42)
            if state in ('menu', 'title', 'idle', 'ready', 'intro'):
                _rect(cc, 36, 34, 248, 107, '#20313f', .94)
                _rect(cc, 38, 36, 244, 1, '#e5b65d'); _rect(cc, 38, 138, 244, 1, '#e5b65d')
                return s
            if state == 'countdown' or words is None:
                return s
            if state in ('victory', 'won', 'complete'):
                _rect(cc, 43, 34, 234, 86, '#20313f', .94)
                _rect(cc, 45, 36, 230, 1, '#e5b65d')
                return s
            _rect(cc, 43, 48, 234, 92, '#20313f', .94)
            _rect(cc, 45, 50, 230, 1, '#e5b65d'); _rect(cc, 45, 137, 230, 1, '#e5b65d')
            return s
        return self.overlays.get(state, build)

    def _ui_text_runs(self, snapshot, theme):
        """Text boxes are logical coordinates; glyphs are rasterized later."""
        state, words = self._overlay_info(snapshot)
        opacity = 1 if state in ('playing', 'running', 'active') else .58
        coins = snapshot.get('coin_count', snapshot.get('coins', 0))
        if not isinstance(coins, (int, float)):
            coins = 0
        level, score = int(snapshot.get('level_index', 0)), max(0, int(snapshot.get('score', 0)))
        runs = [(f'{int(coins):02d}', 65, 7, 24, 14, 8, '#fff0a1', False, 'left', opacity),
                (THEMES[theme]['title'], 94, 7, 76, 14, 8, '#ede7c9', False, 'center', opacity),
                (f'{level + 1}-3', 179, 7, 38, 14, 8, '#73c5a0', False, 'center', opacity),
                (f'{score:06d}', 231, 7, 77, 14, 8, '#fff5cf', False, 'right', opacity)]
        hint = ''
        if words is not None:
            title, subtitle, instruction = words
            if state in ('menu', 'title', 'idle', 'ready', 'intro'):
                runs.extend([(title, 40, 40, 240, 34, 23, '#fff0a1', True, 'center', 1),
                             (subtitle, 40, 74, 240, 18, 10, '#73c5a0', False, 'center', 1),
                             (instruction, 40, 98, 240, 18, 10, '#ede7c9', False, 'center', 1),
                             (snapshot.get('ui', {}).get('notice') or '确认开始',
                              40, 117, 240, 16, 9, '#bdd3b9', False, 'center', 1)])
            elif state == 'countdown':
                runs.extend([(title, 110, 51, 100, 60, 40, '#fff0a1', True, 'center', 1),
                             (subtitle, 60, 114, 200, 22, 11, '#fff5cf', True, 'center', 1)])
            elif state in ('victory', 'won', 'complete'):
                runs.extend([(title, 48, 39, 224, 31, 20, '#fff0a1', True, 'center', 1),
                             (subtitle, 48, 74, 224, 18, 10, '#73c5a0', False, 'center', 1),
                             (instruction, 48, 99, 224, 16, 8, '#ede7c9', False, 'center', 1)])
            else:
                runs.extend([(title, 48, 53, 224, 35, 22, '#fff0a1', True, 'center', 1),
                             (subtitle, 48, 90, 224, 20, 11, '#73c5a0', False, 'center', 1)])
                if instruction:
                    runs.append((instruction, 48, 116, 224, 18, 9, '#ede7c9', False, 'center', 1))
        ui = snapshot.get('ui', {})
        if ui and not ui.get('tracking', True) and not ui.get('ready') and not hint:
            label = '手柄已断开' if ui.get('control_mode') == 'gamepad' else '等待识别'
            runs.append((label, 231, 161, 80, 14, 8, '#ede7c9', False, 'center', 1))
        return runs

    def _paint_native_text(self, target, snapshot, theme, content, scale):
        ox, oy, outw, outh = content
        target.save()
        try:
            target.set_operator(cairo.OPERATOR_OVER)
            target.rectangle(ox, oy, outw, outh); target.clip()
            self.last_text_layout = []
            for value, x, y, width, height, size, color, bold, align, opacity in self._ui_text_runs(snapshot, theme):
                px, py = round(ox + x * scale), round(oy + y * scale)
                pw, ph = max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))
                font_pixels = max(1, size * scale)
                surface = self._text(value, font_pixels, color, bold, pw, height=ph, align=align)
                details = dict(self._last_text_info)
                grid = details['pixel_grid']
                px, py = grid * round(px/grid), grid * round(py/grid)
                target.save()
                try:
                    target.rectangle(px, py, pw, ph); target.clip()
                    target.set_source_surface(surface, px, py)
                    target.get_source().set_filter(cairo.FILTER_NEAREST)
                    target.paint_with_alpha(opacity)
                finally:
                    target.restore()
                self.last_text_layout.append({'text': value, 'box': [px, py, pw, ph],
                                              **details, 'align': align,
                                              'surface_pixels': [surface.get_width(), surface.get_height()]})
        finally:
            target.restore()

    def render(self, target, game_or_snapshot, width=960, height=540, *, draw_text=True):
        """Submit a complete frame once, using one verified native backplate.

        Ordinary Xlib targets reuse a single server-side surface. Other Cairo
        contexts retain temporary explicit grouping. Both paths keep all world
        and text passes off the visible target until the frame is complete.
        """
        started = time.perf_counter()
        width, height = max(1, int(width)), max(1, int(height))
        binding, reason = self._native_output_key(target, width, height)
        if binding is not None and self._prepare_native_output(binding):
            counts = self._render_native_frame(target, game_or_snapshot, width, height, draw_text=draw_text)
            self._native_fallback_reason = None
            self._presentation = 'cached-native-xlib-single-paint'
        else:
            self.release_native_output()
            if reason is not None:
                self._native_fallback_reason = reason
            counts = self._render_group(target, game_or_snapshot, width, height, draw_text=draw_text)
            self._presentation = 'temporary-opaque-cairo-group-single-paint'
        self.frames += 1
        self.frame_times.append((time.perf_counter()-started)*1000)
        return counts

    def _native_output_key(self, target, width, height):
        """Only a full, untransformed owned Xlib target can reuse native pixels."""
        native_type = getattr(cairo, 'XlibSurface', None)
        if native_type is None:
            return None, 'xlib-surface-api-unavailable'
        try:
            surface = target.get_target()
            if not isinstance(surface, native_type):
                return None, 'non-xlib-target'
            if target.get_group_target() != surface:
                return None, 'nested-cairo-group'
            if tuple(target.get_matrix()) != (1., 0., 0., 1., 0., 0.):
                return None, 'transformed-target'
            if tuple(surface.get_device_offset()) != (0., 0.):
                return None, 'device-offset'
            if tuple(surface.get_device_scale()) != (1., 1.):
                return None, 'device-scale'
            if (surface.get_width(), surface.get_height()) != (width, height):
                return None, 'target-size-mismatch'
            rectangles = target.copy_clip_rectangle_list()
            if len(rectangles) != 1 or tuple(rectangles[0]) != (0., 0., float(width), float(height)):
                return None, 'partial-or-complex-clip'
            if width*height*4 > NATIVE_SERVER_CACHE_BYTES:
                return None, 'server-cache-byte-budget'
            # Pycairo equality and hashing use underlying Cairo identities,
            # whereas each accessor may return a distinct Python wrapper.
            return (surface, surface.get_device(), width, height), None
        except Exception as error:
            return None, 'native-verification-'+type(error).__name__

    def release_native_output(self):
        """Release only our owned backplate; safe at resize/unrealize/shutdown."""
        surface = self._native_output
        self._native_output = self._native_output_context = self._native_output_binding = None
        self._native_output_estimated_bytes = 0
        if surface is None:
            return False
        self._native_output_releases += 1
        try:
            surface.finish()
        except Exception:
            self._native_release_errors += 1
        return True

    def _prepare_native_output(self, binding):
        if self._native_output is not None and self._native_output_binding == binding:
            self._native_output_hits += 1
            return True
        self.release_native_output()
        owner, unused_device, width, height = binding
        started = time.perf_counter(); surface = None
        try:
            surface = owner.create_similar(cairo.CONTENT_COLOR, width, height)
            native_type = getattr(cairo, 'XlibSurface', None)
            if native_type is None or not isinstance(surface, native_type):
                self._native_fallback_reason = 'similar-surface-not-xlib'
                surface.finish(); return False
            if (surface.get_width(), surface.get_height()) != (width, height):
                self._native_fallback_reason = 'similar-surface-size-mismatch'
                surface.finish(); return False
            context = cairo.Context(surface)
            self._native_output, self._native_output_context = surface, context
            self._native_output_binding = binding
            self._native_output_estimated_bytes = width*height*4
            self._native_output_builds += 1
            self.native_build_times.append((time.perf_counter()-started)*1000)
            return True
        except Exception as error:
            if surface is not None:
                try: surface.finish()
                except Exception: pass
            self._native_fallback_reason = 'native-backplate-build-'+type(error).__name__
            return False

    def _render_native_frame(self, target, game_or_snapshot, width, height, *, draw_text):
        # Always redraw every pixel; a previous frame or aborted attempt is
        # never used as the content of the next frame. Only allocation is reused.
        try:
            counts = self._render_frame(self._native_output_context, game_or_snapshot, width, height,
                                        draw_text=draw_text)
            submitted = time.perf_counter()
            target.save()
            try:
                target.set_operator(cairo.OPERATOR_SOURCE)
                target.set_source_surface(self._native_output, 0, 0)
                target.get_source().set_filter(cairo.FILTER_NEAREST)
                target.paint()
            finally:
                target.restore()
            milliseconds = (time.perf_counter()-submitted)*1000
            self.submission_times.append(milliseconds); self.native_submission_times.append(milliseconds)
            return counts
        except BaseException:
            self.release_native_output()
            self._native_fallback_reason = 'native-frame-aborted'
            raise

    def _render_group(self, target, game_or_snapshot, width, height, *, draw_text):
        target.save()
        grouped = False
        try:
            target.push_group_with_content(cairo.CONTENT_COLOR)
            grouped = True
            counts = self._render_frame(target, game_or_snapshot, width, height, draw_text=draw_text)
            submitted = time.perf_counter()
            target.pop_group_to_source()
            grouped = False
            target.set_operator(cairo.OPERATOR_SOURCE)
            target.paint()
            milliseconds = (time.perf_counter()-submitted)*1000
            self.submission_times.append(milliseconds); self.group_submission_times.append(milliseconds)
        except BaseException:
            if grouped:
                # Discard incomplete pixels and restore Cairo's group state;
                # the outer save additionally restores caller source/operator.
                target.pop_group()
            raise
        finally:
            target.restore()
        return counts

    def _render_frame(self, target, game_or_snapshot, width=960, height=540, *, draw_text=True):
        """Paint world and letters into the render entrypoint's opaque group."""
        s = game_or_snapshot.snapshot() if hasattr(game_or_snapshot, 'snapshot') else game_or_snapshot
        cr = self.context
        camera = max(0, int(s.get('camera_x', 0)))
        theme = str(s.get('stage', {}).get('theme', 'bamboo'))
        if theme not in THEMES:
            theme = ('bamboo', 'temple', 'moon')[int(s.get('level_index', 0)) % 3]
        self._paint_backdrop(cr, theme, camera)
        cr.set_operator(cairo.OPERATOR_OVER)
        castle = float(s.get('stage', {}).get('castle_x', 0))
        if castle and camera + W > castle:
            left = int(castle - camera)
            cr.save(); cr.rectangle(max(0, left), 0, W-max(0, left), 160); cr.clip()
            for x in range(left, W, 640):
                if x + 640 > 0:
                    self._blit(cr, self.sprites['castle_hall'], x, 0)
            cr.restore()
        tile_count = self._terrain(cr, s, camera)
        elapsed = float(s.get('elapsed', 0)); frame = int(elapsed * 8) % 4
        counts = {'tiles': tile_count, 'coins': 0, 'enemies': 0, 'particles': 0, 'sprites': 0}
        goal = float(s.get('stage', {}).get('goal_x', 1100)) - camera
        princess = float(s.get('stage', {}).get('princess_x', 0))
        checkpoint = float(s.get('stage', {}).get('checkpoint_x', -1000)) - camera
        if -18 < checkpoint < W:
            self._blit(cr, self.sprites[('checkpoint', bool(s.get('checkpoint')))], checkpoint, 122)
        if princess and -24 < princess-camera < W:
            self._blit(cr, self.sprites[('princess', frame % 2)], princess-camera, 134)
            counts['sprites'] += 1
        if not princess and -32 < goal < W + 32:
            self._blit(cr, self.sprites[('flag', frame)], goal, 112)
        for pipe in s.get('pipes', ()):
            x, y = float(pipe['x']) - camera, float(pipe['y'])
            w, h = max(1, float(pipe.get('w', 32))), max(1, float(pipe.get('h', 32)))
            if x < W and x + w > 0:
                cr.save(); cr.translate(int(x), int(y + h - 40));
                # Pipe lip matches the collision surface; the top stays unobstructed.
                self._blit(cr, self.sprites['portal'], 0, 0); cr.restore(); counts['sprites'] += 1
        collectibles = s.get('collectibles', s.get('coin_entities', ()))
        if not collectibles and isinstance(s.get('coins'), list):
            collectibles = s['coins']
        for item in collectibles:
            if item.get('active', not item.get('collected', False)):
                x, y = float(item['x']) - camera, float(item['y'])
                if -12 < x < W:
                    self._blit(cr, self.sprites[('coin', frame)], x + 1, y + int(math.sin(elapsed * 5 + x * .03)))
                    counts['coins'] += 1; counts['sprites'] += 1
        for item in s.get('powerups', ()):
            if item.get('active', True):
                x, y = float(item['x']) - camera, float(item['y'])
                if -16 < x < W:
                    self._blit(cr, self.sprites[('powerup', frame)], x, y); counts['sprites'] += 1
        for enemy in s.get('enemies', ()):
            if not enemy.get('alive', True):
                continue
            x, y = float(enemy['x']) - camera, float(enemy['y'])
            if -24 < x < W:
                kind = str(enemy.get('kind', 'lantern'))
                kind = kind if kind in ('lantern', 'stone') else 'lantern'
                bob = int(math.sin(elapsed * 6 + x * .04)) if kind == 'lantern' else 0
                sprite = self.sprites[(kind, frame)]
                self._blit(cr, sprite, x + (float(enemy.get('w', 12))-sprite.get_width())/2,
                           y + float(enemy.get('h', 12))-sprite.get_height() + bob)
                counts['enemies'] += 1; counts['sprites'] += 1
        player = s.get('player', {})
        if player:
            px, py = float(player.get('x', 40)) - camera, float(player.get('y', 136))
            state = 'dead' if player.get('dead') else ('jumping' if not player.get('grounded', True) else
                                                     ('running' if abs(float(player.get('vx', 0))) > 2 else 'idle'))
            invincible = float(player.get('invincible', player.get('invuln', 0)))
            if invincible and int(elapsed * 15) % 2:
                state = 'hurt'
            frames = 4 if state == 'running' else 2 if state == 'idle' else 1
            sprite = self.sprites[('player', state, frame % frames, -1 if player.get('facing', 1) < 0 else 1,
                                   bool(player.get('powered', False)))]
            self._blit(cr, sprite, px + (float(player.get('w', 14)) - 16) / 2,
                       py + float(player.get('h', 24)) - 24)
            counts['sprites'] += 1
        counts['particles'] = self._particles(cr, s.get('particles', ()), camera)
        self._blit(cr, self._hud(s, theme), 0, 0)
        overlay = self._overlay(s)
        if overlay:
            self._blit(cr, overlay, 0, 0)
        ui = s.get('ui', {})
        self._paint_control_hud(cr, s)
        hint = self._route_hint(s) if self._overlay_info(s)[0] in ('playing', 'running', 'active') else ''
        if hint:
            _rect(cr, 6, 161, 308, 14, '#20313f', .85)
        elif ui and not ui.get('tracking', True) and not ui.get('ready'):
            _rect(cr, 229, 161, 85, 14, '#20313f', .85)
        width, height = max(1, int(width)), max(1, int(height))
        scale = min(width / W, height / H)
        outw, outh = W * scale, H * scale
        ox, oy = (width - outw) / 2, (height - outh) / 2
        canvas = target
        canvas.save()
        try:
            canvas.set_operator(cairo.OPERATOR_SOURCE)
            if ox or oy:
                canvas.set_source_rgb(*_rgb('#17212e')); canvas.paint()
                # Fractional letterbox edges have antialias coverage. Preserve the
                # original OVER rounding there; full 16:9 output stays SOURCE.
                canvas.set_operator(cairo.OPERATOR_OVER)
            # The framebuffer is opaque, so SOURCE gives the same visible pixels
            # while avoiding output-size alpha composition on the native target.
            canvas.rectangle(ox, oy, outw, outh); canvas.clip()
            canvas.translate(ox, oy); canvas.scale(scale, scale)
            canvas.set_source_surface(self.buffer, 0, 0)
            canvas.get_source().set_filter(cairo.FILTER_NEAREST); canvas.paint()
        finally:
            canvas.restore()
        self.last_text_layout = []
        if draw_text:
            # Keep the world and native glyphs on GTK's provided draw target.
            # A second full-size image copy failed to reach the parent window
            # in cross-process Gtk.Plug embedding despite a correct child image.
            self._paint_native_text(canvas, s, theme, (ox, oy, outw, outh), scale)
        self.last_counts = counts
        self.last_layout = {'internal': [W, H], 'output': [width, height],
                            'content': [ox, oy, outw, outh], 'scale': scale,
                            'text_enabled': bool(draw_text), 'text_resolution': 'native-pixel'}
        return self.last_counts

    def stats(self):
        samples = sorted(self.frame_times)
        backgrounds = sum(s.get_stride() * s.get_height() for group in self.backgrounds.values() for s in group)
        sprites = sum(s.get_stride() * s.get_height() for s in self.sprites.values())
        def build_times(values):
            ordered = sorted(values)
            return {'count': len(ordered), 'mean': statistics.mean(ordered) if ordered else 0,
                    'p95': ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else 0,
                    'maximum': max(ordered) if ordered else 0}
        return {'frames': self.frames, 'timing_samples': len(samples),
                'render_ms_mean': statistics.mean(samples) if samples else 0,
                'render_ms_p95': samples[min(len(samples) - 1, int(len(samples) * .95))] if samples else 0,
                'render_ms_max': max(samples) if samples else 0,
                'presentation': self._presentation,
                'group_submission_ms': build_times(self.group_submission_times),
                'native_submission_ms': build_times(self.native_submission_times),
                'presentation_submission_ms': build_times(self.submission_times),
                'native_backplate_build_ms': build_times(self.native_build_times),
                'native_server_cache': {'items': int(self._native_output is not None),
                    'limit_items': 1, 'estimated_bytes': self._native_output_estimated_bytes,
                    'limit_estimated_bytes': NATIVE_SERVER_CACHE_BYTES,
                    'builds': self._native_output_builds, 'hits': self._native_output_hits,
                    'releases': self._native_output_releases, 'release_errors': self._native_release_errors,
                    'fallback_reason': self._native_fallback_reason,
                    'accounting': 'estimated server pixmap bytes at 4 bytes per pixel; separate from PS image buffers'},
                'cached_surface_bytes': self.buffer.get_stride() * H + backgrounds + sprites +
                                        sum(c.bytes() for c in (self.chunks, self.texts, self.huds, self.overlays, self.underlays, self.backdrops)),
                'cache_limits': {'chunks': 16, 'text': 64, 'text_bytes': TEXT_CACHE_BYTES,
                                 'hud': 8, 'overlay': 12, 'particles': 64, 'backdrops': 8, 'underlays': 8},
                'cache_entries': {'chunks': len(self.chunks.data), 'text': len(self.texts.data),
                                  'hud': len(self.huds.data), 'overlay': len(self.overlays.data),
                                  'backdrops': len(self.backdrops.data), 'underlays': len(self.underlays.data)},
                'backdrop_builds': self.backdrops.builds, 'backdrop_hits': self.backdrops.hits,
                'backdrop_direct_composites': self.backdrop_direct_composites,
                'underlay_builds': self.underlays.builds, 'underlay_hits': self.underlays.hits,
                'underlay_cache_bytes': self.underlays.bytes(),
                'underlay_build_ms': build_times(self.underlay_build_times),
                'backdrop_build_ms': build_times(self.backdrop_build_times),
                'chunk_builds': self.chunks.builds, 'chunk_hits': self.chunks.hits,
                'hud_builds': self.huds.builds, 'hud_hits': self.huds.hits,
                'text_builds': self.texts.builds, 'text_hits': self.texts.hits,
                'native_text_cache_bytes': self.texts.bytes(), 'native_text_runs': len(self.last_text_layout),
                'native_text_layout': self.last_text_layout, 'font_backend': self._font_mode,
                'last_counts': self.last_counts, 'layout': self.last_layout}


def fixture(theme='bamboo', state='playing', dense=False):
    """Stable render-only scene; no engine transition or camera access."""
    level = ('bamboo', 'temple', 'moon').index(theme)
    ground = [{'x': x, 'y': 160, 'w': 16, 'h': 32, 'kind': 'ground'} for x in range(0, 1280, 16)
              if not (240 <= x < 288 or 672 <= x < 736)]
    ground += [{'x': x, 'y': 112, 'w': 16, 'h': 16, 'kind': 'question' if x == 160 else 'brick'}
               for x in range(112, 193, 16)]
    ground += [{'x': x, 'y': 128, 'w': 16, 'h': 16, 'kind': 'brick'} for x in range(240, 304, 16)]
    coins = [{'x': x, 'y': 89 if 110 <= x <= 194 else 137, 'w': 10, 'h': 12, 'active': True}
             for x in range(72, 304, 26 if not dense else 12)]
    enemies = [{'x': x, 'y': 142 if i % 2 == 0 else 144, 'w': 16, 'h': 16, 'alive': True,
                'kind': 'lantern' if i % 2 == 0 else 'stone'}
               for i, x in enumerate(range(205 if not dense else 64, 310, 80 if not dense else 17))]
    particles = [{'x': (i * 37) % W, 'y': 35 + (i * 11) % 110, 'kind': 'spark'}
                 for i in range(64 if dense else 0)]
    return {'state': state, 'countdown': 2.4, 'score': 1250, 'coin_count': 7, 'lives': 3,
            'level_index': level, 'elapsed': 2.25, 'camera_x': 0,
            'stage': {'name': THEMES[theme]['title'], 'theme': theme, 'width': 1280,
                      'height': 192, 'goal_x': 1190},
            'player': {'x': 79, 'y': 136, 'w': 14, 'h': 24, 'vx': 95, 'vy': 0,
                       'facing': 1, 'grounded': True, 'powered': theme == 'moon'},
            'tiles': ground, 'collectibles': coins, 'enemies': enemies, 'particles': particles,
            'powerups': [{'x': 178, 'y': 96, 'active': True}],
            'pipes': [{'x': 30, 'y': 128, 'w': 32, 'h': 32, 'label': '云门'}]}


def contact_sheet(path):
    renderer = Renderer(); surface = _surface(1280, 1080); cr = cairo.Context(surface)
    states = [('bamboo', 'playing'), ('temple', 'playing'), ('moon', 'playing'),
              ('bamboo', 'menu'), ('temple', 'paused'), ('moon', 'victory')]
    for index, (theme, state) in enumerate(states):
        cr.save(); cr.rectangle((index % 2) * 640, (index // 2) * 360, 640, 360); cr.clip()
        cr.translate((index % 2) * 640, (index // 2) * 360)
        scene = fixture(theme, state)
        renderer.render(cr, scene, 640, 360)
        cr.restore()
    surface.write_to_png(str(path))
    for theme in THEMES:
        single = _surface(960, 540)
        renderer.render(cairo.Context(single), fixture(theme), 960, 540)
        single.write_to_png(str(Path(path).with_name('stage_' + theme + '.png')))
    return renderer.stats()


def benchmark(path=None, frames=600, width=960, height=540):
    renderer = Renderer(); surface = _surface(width, height); cr = cairo.Context(surface)
    scene = fixture('moon', dense=True)
    renderer.render(cr, scene, width, height)
    # First-frame cache construction is reported separately from warmed frames.
    cold_ms = renderer.frame_times[-1]; renderer.frame_times.clear()
    started = time.perf_counter()
    for frame in range(frames):
        scene['elapsed'] = frame / 60
        renderer.render(cr, scene, width, height)
    total = time.perf_counter() - started
    stats = renderer.stats()
    stats.update({'benchmark': 'deterministic dense renderer only', 'cold_frame_ms': cold_ms,
                  'measured_frames': frames, 'wall_seconds': total, 'renderer_fps': frames / total,
                  'camera_access': False, 'display_presented': False,
                  'target_size': [width, height], 'framebuffer_pixels': W * H,
                  'note': 'Timing is machine-specific Cairo paint time; this does not measure live GTK display or body control.'})
    if path:
        Path(path).write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview'); parser.add_argument('--benchmark'); parser.add_argument('--frames', type=int, default=600)
    args = parser.parse_args()
    if args.preview:
        contact_sheet(args.preview)
    if args.benchmark:
        print(json.dumps(benchmark(args.benchmark, args.frames), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
