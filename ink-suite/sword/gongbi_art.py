"""Approved gongbi PNGs, rasterized once per viewport into bounded tiles.

Only artwork is quantized. Positions, collision radii and gameplay state never
pass through this module. Cached surfaces have final device dimensions, so the
game loop only performs integer OVER blits. PNGs are decoded at most once per
art instance; size/alpha/variant work happens during viewport preparation.
"""
from collections import OrderedDict
import math
from pathlib import Path
import struct
from types import MappingProxyType

import cairo

LOGICAL_W, LOGICAL_H = 1280., 720.
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_TILE_BYTES = 16 * 1024 * 1024
MAX_TILE_COUNT = 96
MAX_PNG_DIMENSION = 4096
PLAYER_FRAMES = {'left': 0, 'neutral': 1, 'right': 2}
PLAYER_ANCHORS = {'left': (.72, .51), 'neutral': (.50, .51), 'right': (.28, .51)}
EFFECT_STEPS = 16


class GongbiArt:
    """Finite source and tile caches, with read-only accounting for QA."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.sources = OrderedDict()
        self.tiles = OrderedDict()
        self.size = None
        self.source_bytes = 0
        self.tile_bytes = 0
        self.decode_count = 0
        self.tile_allocations = 0
        self.background_bakes = 0

    @property
    def stats(self):
        return MappingProxyType(dict(source_bytes=self.source_bytes,
            tile_bytes=self.tile_bytes, source_count=len(self.sources),
            tile_count=len(self.tiles), decode_count=self.decode_count,
            tile_allocations=self.tile_allocations,
            background_bakes=self.background_bakes,
            max_source_bytes=MAX_SOURCE_BYTES, max_tile_bytes=MAX_TILE_BYTES,
            max_tile_count=MAX_TILE_COUNT))

    @staticmethod
    def _bytes(surface):
        return surface.get_stride() * surface.get_height()

    def _source(self, name):
        if name in self.sources:
            return self.sources[name]
        path = self.directory / (name + '.png')
        with path.open('rb') as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Invalid approved PNG: ' + str(path))
        width, height = struct.unpack('>II', header[16:24])
        allocation = width * height * 4
        if (not min(width, height) or max(width, height) > MAX_PNG_DIMENSION or
                self.source_bytes + allocation > MAX_SOURCE_BYTES):
            raise ValueError('Approved PNG source budget exceeded: ' + str(path))
        surface = cairo.ImageSurface.create_from_png(str(path))
        if name != 'warm-mist-background' and surface.get_format() != cairo.FORMAT_ARGB32:
            raise ValueError('Sprite PNG must preserve alpha: ' + str(path))
        self.sources[name] = surface
        self.source_bytes += self._bytes(surface)
        self.decode_count += 1
        return surface

    @staticmethod
    def _cell(source, column=0, row=0, columns=1, rows=1):
        width, height = source.get_width(), source.get_height()
        x0, x1 = int(column * width / columns), int((column + 1) * width / columns)
        y0, y1 = int(row * height / rows), int((row + 1) * height / rows)
        return x0, y0, x1 - x0, y1 - y0

    def _tile(self, name, box, cell=(0, 0, 1, 1), alpha=1., anchor=None, artwork_height=None):
        key = (name, tuple(box), tuple(cell), alpha, anchor, artwork_height)
        if key in self.tiles:
            self.tiles.move_to_end(key)
            return self.tiles[key]
        source = self._source(name)
        x0, y0, sw, sh = self._cell(source, *cell)
        sx, sy = self.size[0] / LOGICAL_W, self.size[1] / LOGICAL_H
        width, height = max(1, math.ceil(box[0] * sx)), max(1, math.ceil(box[1] * sy))
        tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        p = cairo.Context(tile)
        if anchor is None:
            scale = min(width / sw, height / sh)
            p.translate((width - sw * scale) / 2, (height - sh * scale) / 2)
            p.scale(scale, scale)
        else:
            # The leaning poses move the figure inside their square cells.
            # Their measured body/guard anchors, not the silk bounding box,
            # are placed at the player's unchanged logical collision point.
            p.translate(width / 2, height / 2)
            scale = artwork_height / sh if artwork_height is not None else box[0] / sw
            p.scale(scale * sx, scale * sy)
            p.translate(-sw * anchor[0], -sh * anchor[1])
        p.rectangle(0, 0, sw, sh)
        p.clip()
        p.set_source_surface(source, -x0, -y0)
        p.get_source().set_filter(cairo.FILTER_BILINEAR)
        if alpha == 1.:
            p.paint()
        else:
            p.paint_with_alpha(alpha)
        tile.flush()
        self.tiles[key] = tile
        self.tile_bytes += self._bytes(tile)
        self.tile_allocations += 1
        while len(self.tiles) > MAX_TILE_COUNT or self.tile_bytes > MAX_TILE_BYTES:
            _, discarded = self.tiles.popitem(last=False)
            self.tile_bytes -= self._bytes(discarded)
        return tile

    def prepare(self, width, height):
        self.size = (width, height)
        self.tiles.clear()
        self.tile_bytes = 0
        for bank in ('left', 'neutral', 'right'):
            self.player(bank)
        for name in ('enemy-red', 'enemy-blue', 'elite', 'boss'):
            for bucket in range(4):
                self.enemy(name, bucket)
        for row in range(3):
            for step in range(EFFECT_STEPS):
                self.effect(row, step)

    def background(self, width, height):
        source = self._source('warm-mist-background')
        result = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
        p = cairo.Context(result)
        p.scale(width / source.get_width(), height / source.get_height())
        p.set_source_surface(source)
        p.get_source().set_filter(cairo.FILTER_BILINEAR)
        p.paint()
        result.flush()
        self.background_bakes += 1
        return result

    def player(self, bank='neutral'):
        return self._tile('player-strip', (256, 176),
                          (PLAYER_FRAMES[bank], 0, 3, 1), anchor=PLAYER_ANCHORS[bank],
                          artwork_height=154.)

    def enemy(self, name, bucket=1):
        # Four size buckets cover legacy diagnostic radii without making a new
        # surface for every entity. Engine production radii use bucket one.
        scales = (.78, 1., 1.20, 1.45)
        boxes = {'enemy-red': (112, 124), 'enemy-blue': (112, 124),
                 'elite': (174, 170), 'boss': (328, 280)}
        scale = scales[max(0, min(3, int(bucket)))]
        if name == 'boss':
            # Match the mask's face core to the entity center. A whole-canvas
            # center would put the eyes behind the unchanged boss HUD bar.
            return self._tile(name, tuple(v * scale for v in boxes[name]), anchor=(.5, .38))
        return self._tile(name, tuple(v * scale for v in boxes[name]))

    def effect(self, row, step):
        row, step = max(0, min(2, int(row))), max(0, min(EFFECT_STEPS - 1, int(step)))
        # Four authored beats; four pre-baked fade steps each. No real-time
        # blur, rotation, resampling, alpha variant or large allocation.
        frame = min(3, step // 4)
        fade = (1., .88, .74, .58)[step % 4]
        if frame == 3:
            fade *= (1., .70, .40, .15)[step % 4]
        boxes = ((108, 88), (156, 132), (360, 286))
        return self._tile('effects-atlas', boxes[row], (frame, row, 4, 3), fade)

