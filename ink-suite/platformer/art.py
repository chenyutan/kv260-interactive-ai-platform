"""Original small-grid artwork for 超级玛丽; no runtime asset download.

All artwork is authored on integer pixel grids. The palette and sprite matrices
are shared by the Cairo atlas and the optional PNG contact-sheet exporter.
"""
from __future__ import annotations

PALETTE = {
    '.': None, 'o': '#20313f', 'h': '#354551', 'H': '#1c2b39',
    's': '#e8bb83', 'S': '#f5d7a3', 'a': '#b98059',
    't': '#328c86', 'T': '#73c5a0', 'd': '#205b65',
    'c': '#ede7c9', 'w': '#fff5cf', 'b': '#526978',
    'B': '#8ca3a4', 'l': '#bdd3b9', 'r': '#b75746',
    'R': '#eb8960', 'y': '#e5b65d', 'Y': '#fff0a1',
    'g': '#4e7858', 'G': '#93b473', 'n': '#324e48',
    'k': '#665950', 'K': '#99836b', 'q': '#bdac88',
    'v': '#75846b', 'm': '#493e49', 'f': '#dc9e91',
}


def rgb(value):
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


class Grid:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.p = [['.'] * w for _ in range(h)]

    def dot(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.p[y][x] = color

    def rect(self, x, y, w, h, color):
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.p[yy][xx] = color

    def line(self, x1, y1, x2, y2, color):
        dx, dy = abs(x2 - x1), -abs(y2 - y1)
        sx, sy = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
        err = dx + dy
        while True:
            self.dot(x1, y1, color)
            if (x1, y1) == (x2, y2):
                break
            twice = err * 2
            if twice >= dy:
                err += dy
                x1 += sx
            if twice <= dx:
                err += dx
                y1 += sy

    def polygon(self, points, color):
        # Integer scanlines keep every edge square, including mountain ridges.
        for y in range(max(0, min(p[1] for p in points)),
                       min(self.h, max(p[1] for p in points) + 1)):
            intersections = []
            for index, (x1, y1) in enumerate(points):
                x2, y2 = points[(index + 1) % len(points)]
                if (y1 <= y < y2) or (y2 <= y < y1):
                    intersections.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            intersections.sort()
            for i in range(0, len(intersections) - 1, 2):
                a, b = round(intersections[i]), round(intersections[i + 1])
                self.rect(a, y, b - a + 1, 1, color)

    def rows(self):
        return tuple(''.join(row) for row in self.p)


def player(frame=0, state='idle', facing=1, powered=False):
    """16×24 original explorer with a teal cap, orange scarf and backpack."""
    g = Grid(16, 24)
    bob = int(state == 'idle' and frame % 2 == 1)
    # Compact backpack and cap keep a distinct, original silhouette.
    g.rect(1, 10 + bob, 4, 8, 'o')
    g.rect(2, 11 + bob, 3, 6, 'K')
    g.rect(4, 0 + bob, 7, 3, 'd')
    g.rect(5, 0 + bob, 5, 1, 'T')
    g.rect(5, 2 + bob, 7, 3, 'o')
    g.rect(4, 4 + bob, 9, 5, 'H')
    g.rect(6, 4 + bob, 6, 5, 's')
    g.rect(7, 4 + bob, 5, 2, 'S')
    g.rect(4, 2 + bob, 8, 3, 't')
    g.rect(7, 4 + bob, 7, 1, 'T')
    g.rect(4, 5 + bob, 3, 2, 'H')
    g.dot(11, 6 + bob, 'o')
    g.rect(5, 9 + bob, 7, 2, 'R')
    g.rect(4, 10 + bob, 2, 4, 'r')
    g.rect(4, 10 + bob, 9, 8, 'o')
    g.rect(5, 10 + bob, 7, 6, 't')
    g.rect(6, 10 + bob, 3, 4, 'T')
    g.line(8, 10 + bob, 9, 13 + bob, 'c')
    g.rect(5, 15 + bob, 7, 2, 'c')
    g.dot(10, 15 + bob, 'y')
    g.polygon(((4, 17 + bob), (12, 17 + bob), (14, 20), (3, 20)), 'd')
    g.rect(5, 17 + bob, 5, 3, 't')
    # Arms and legs visibly alternate; jump folds both knees.
    if state in ('jumping', 'jump'):
        g.rect(3, 10, 2, 5, 'd'); g.rect(3, 14, 2, 2, 's')
        g.rect(12, 8, 2, 5, 't'); g.rect(12, 7, 2, 2, 'S')
        g.rect(4, 20, 4, 2, 'k'); g.rect(4, 22, 3, 1, 'o')
        g.rect(10, 20, 3, 2, 'K'); g.rect(12, 21, 3, 2, 'o')
    elif state in ('running', 'run'):
        swing = (0, 1, 0, -1)[frame % 4]
        g.rect(3, 11 + swing, 2, 5, 'd'); g.rect(3, 15 + swing, 2, 2, 's')
        g.rect(12, 12 - swing, 2, 4, 't'); g.rect(12, 15 - swing, 2, 2, 'S')
        legs = (((3, 20, 4, 2), (9, 20, 3, 4)),
                ((4, 20, 3, 4), (10, 20, 4, 2)),
                ((5, 20, 3, 4), (9, 20, 3, 4)),
                ((4, 20, 3, 2), (9, 20, 3, 4)))
        for xx, yy, ww, hh in legs[frame % 4]:
            g.rect(xx, yy, ww, hh, 'K')
            g.rect(xx - 1, yy + hh - 1, ww + 1, 1, 'o')
    else:
        g.rect(3, 11 + bob, 2, 5, 'd'); g.rect(3, 15 + bob, 2, 2, 's')
        g.rect(12, 11 + bob, 2, 5, 't'); g.rect(12, 15 + bob, 2, 2, 'S')
        g.rect(5, 20, 3, 3, 'K'); g.rect(10, 20, 3, 3, 'k')
        g.rect(4, 23, 4, 1, 'o'); g.rect(9, 23, 4, 1, 'o')
    if powered:
        g.rect(4, 10 + bob, 1, 4, 'y'); g.dot(9, 2 + bob, 'Y')
    if state in ('hurt', 'dead'):
        g.dot(10, 6 + bob, 'r'); g.dot(11, 7 + bob, 'r')
    rows = g.rows()
    return tuple(row[::-1] for row in rows) if facing < 0 else rows


def tile(kind='ground', variant=0):
    g = Grid(16, 16)
    if kind in ('ground', 'foundation'):
        g.rect(0, 0, 16, 16, 'k')
        if kind == 'ground':
            g.rect(0, 0, 16, 3, 'n')
            g.rect(0, 0, 16, 1, 'G'); g.rect(0, 1, 16, 2, 'g')
        for xx, yy, ww in ((1, 5, 5), (9, 5, 6), (0, 11, 7), (9, 12, 5)):
            g.rect(xx, yy, ww, 1, 'K'); g.rect(xx, yy + 1, ww, 1, 'm')
        if kind == 'ground':
            g.dot((variant * 3 + 2) % 16, 3, 'G')
        g.dot((variant * 5 + 11) % 16, 7, 'q')
    elif kind in ('brick', 'wood', 'bridge'):
        g.rect(0, 0, 16, 16, 'o'); g.rect(1, 1, 14, 14, 'k')
        for yy in (2, 7, 12):
            g.rect(1, yy, 14, 2, 'K'); g.rect(1, yy + 2, 14, 1, 'q')
        g.rect((variant % 2) * 7 + 4, 1, 1, 5, 'm')
        g.rect(8, 7, 1, 5, 'm')
    elif kind in ('question', 'chest'):
        g.rect(0, 0, 16, 16, 'o'); g.rect(1, 1, 14, 14, 'y')
        g.rect(2, 2, 12, 2, 'Y'); g.rect(2, 13, 12, 2, 'a')
        g.rect(5, 4, 6, 2, 'w'); g.rect(9, 6, 2, 2, 'w')
        g.rect(7, 7, 3, 2, 'w'); g.rect(7, 11, 2, 2, 'w')
        for xx in (2, 13):
            g.dot(xx, 3, 'a'); g.dot(xx, 12, 'a')
    elif kind in ('cave_ground', 'castle_ground', 'hall_ground'):
        g.rect(0, 0, 16, 16, 'H')
        g.rect(1, 1, 14, 14, 'h' if kind == 'cave_ground' else 'b')
        g.rect(0, 0, 16, 2, 'b' if kind == 'cave_ground' else 'B')
        for yy in (6, 12):
            g.rect(0, yy, 16, 1, 'o')
        g.rect(5 if variant % 2 else 10, 2, 1, 4, 'o')
        g.rect(8, 7, 1, 5, 'o')
        g.dot(3 + variant, 3, 'B')
        if kind == 'hall_ground':
            g.rect(0, 0, 16, 1, 'y'); g.rect(0, 1, 16, 2, 'r')
    elif kind == 'used':
        g.rect(0, 0, 16, 16, 'o'); g.rect(1, 1, 14, 14, 'k')
        g.rect(2, 2, 12, 2, 'K'); g.rect(2, 7, 12, 2, 'm')
        g.rect(6, 5, 4, 5, 'K'); g.dot(7, 7, 'o')
    elif kind == 'pipe':
        g.rect(0, 0, 16, 16, 'o'); g.rect(1, 1, 14, 14, 'd')
        g.rect(2, 1, 3, 14, 'T'); g.rect(6, 1, 5, 14, 't')
        g.rect(0, 0, 16, 3, 'T'); g.rect(0, 3, 16, 1, 'n')
        g.rect(2, 11, 12, 2, 'g')
    else:
        g.rect(0, 0, 16, 16, 'k'); g.rect(1, 1, 14, 2, 'K')
    return g.rows()


def lantern(frame=0, facing=1):
    """Original small orange beetle; legacy name preserves entity/audio APIs."""
    g = Grid(16, 18)
    g.rect(3, 4, 10, 3, 'o'); g.rect(1, 7, 14, 6, 'o')
    g.rect(3, 5, 10, 6, 'r'); g.rect(4, 5, 8, 2, 'R')
    g.rect(2, 8, 12, 4, 'R'); g.rect(7, 5, 1, 7, 'y')
    g.rect(3, 9, 3, 3, 'w'); g.rect(10, 9, 3, 3, 'w')
    g.dot(4, 10, 'o'); g.dot(11, 10, 'o')
    g.rect(2, 13, 4, 2 + frame % 2, 'o')
    g.rect(10, 13, 4, 3 - frame % 2, 'o')
    return g.rows()


def stone(frame=0, facing=1):
    g = Grid(18, 16)
    g.rect(3, 1, 12, 3, 'o'); g.rect(1, 4, 16, 9, 'o')
    g.rect(3, 3, 12, 8, 'b'); g.rect(4, 3, 10, 2, 'B')
    g.rect(2, 6, 14, 6, 'b'); g.rect(2, 11, 14, 2, 'h')
    g.rect(3, 1, 6, 2, 'g'); g.dot(6, 0, 'G'); g.dot(3, 3, 'G')
    g.rect(5, 6, 2, 2, 'o'); g.rect(11, 6, 2, 2, 'o')
    g.rect(7, 9, 4, 1, 'K'); g.dot(13, 3, 'l')
    step = frame % 2
    g.rect(3, 13, 4, 2 + step, 'o'); g.rect(11, 13, 4, 3 - step, 'o')
    return g.rows()


def coin(frame=0):
    g = Grid(10, 12)
    widths = (8, 6, 3, 6)
    ww = widths[frame % 4]; xx = (10 - ww) // 2
    g.rect(xx, 2, ww, 8, 'a'); g.rect(xx + 1, 1, max(1, ww - 2), 10, 'y')
    g.rect(xx + 1, 2, max(1, ww - 3), 2, 'Y')
    if ww >= 6:
        g.rect(4, 5, 2, 2, 'o'); g.dot(4, 4, 'Y')
    return g.rows()


def powerup(frame=0):
    g = Grid(14, 16)
    g.rect(5, 0, 4, 2, 'o'); g.rect(4, 2, 6, 3, 'y')
    g.rect(5, 2, 4, 2, 'Y'); g.rect(3, 5, 8, 2, 'o')
    g.rect(2, 7, 10, 6, 'o'); g.rect(3, 7, 8, 6, 't')
    g.rect(4, 7, 3, 5, 'T'); g.rect(5, 9, 4, 2, 'y')
    g.rect(3, 13, 8, 2, 'o'); g.dot(0, 5 + frame % 2, 'Y')
    g.dot(13, 8 - frame % 2, 'Y')
    return g.rows()


def heart(full=True):
    rows = ('..........', '.rr..rr...', 'rrrrrrrr..', 'rRRrrrrr..',
            '.rrrrrr...', '..rrrr....', '...rr.....', '..........')
    return rows if full else tuple(row.replace('r', 'h').replace('R', 'b') for row in rows)


def flag(frame=0):
    g = Grid(24, 48)
    g.rect(3, 3, 2, 43, 'o'); g.rect(4, 4, 1, 40, 'q')
    g.rect(2, 1, 4, 3, 'y'); g.dot(3, 0, 'Y')
    g.polygon(((5, 5), (20, 5 + frame % 2), (18, 17), (5, 16)), 'r')
    g.rect(7, 7, 9, 1, 'y'); g.rect(10, 9, 1, 5, 'Y')
    g.rect(8, 11, 6, 1, 'Y'); g.rect(1, 45, 7, 3, 'k')
    return g.rows()


def princess(frame=0):
    """Original 18x26 princess with crown, auburn hair and coral dress."""
    g = Grid(18, 26)
    g.rect(5, 1, 8, 5, 'k'); g.rect(4, 5, 10, 12, 'a')
    g.rect(5, 0, 2, 3, 'Y'); g.rect(8, 0, 2, 3, 'Y'); g.rect(11, 0, 2, 3, 'Y')
    g.rect(5, 2, 8, 2, 'y'); g.dot(8, 2, 'T')
    g.rect(6, 5, 7, 6, 's'); g.rect(7, 5, 5, 2, 'S')
    g.dot(7, 8, 'o'); g.dot(11, 8, 'o'); g.rect(8, 10, 2, 1, 'r')
    g.rect(6, 12, 6, 6, 'r'); g.rect(7, 12, 4, 2, 'f')
    g.polygon(((6, 16), (12, 16), (16, 24), (2, 24)), 'r')
    g.polygon(((7, 16), (10, 16), (12, 23), (5, 23)), 'f')
    g.rect(2, 24, 14, 1, 'Y'); g.rect(5, 25, 3, 1, 'o'); g.rect(10, 25, 3, 1, 'o')
    g.rect(3, 13, 3, 2, 's'); g.rect(2, 11 - frame % 2, 2, 4, 'S')
    g.rect(12, 13, 2, 5, 's')
    return g.rows()


def checkpoint(active=False):
    g = Grid(18, 38)
    g.rect(3, 2, 2, 34, 'q'); g.rect(2, 0, 4, 3, 'y')
    g.rect(5, 4, 11, 10, 'T' if active else 'b')
    g.rect(7, 7, 6, 2, 'w'); g.rect(9, 5, 2, 6, 'w')
    g.rect(1, 36, 8, 2, 'o')
    return g.rows()


# Compact 3×5 digits and Latin labels provide stable HUD baselines on every OS.
FONT = {
    '0': ('111','101','101','101','111'), '1': ('010','110','010','010','111'),
    '2': ('111','001','111','100','111'), '3': ('111','001','111','001','111'),
    '4': ('101','101','111','001','001'), '5': ('111','100','111','001','111'),
    '6': ('111','100','111','101','111'), '7': ('111','001','010','010','010'),
    '8': ('111','101','111','101','111'), '9': ('111','101','111','001','111'),
    '-': ('000','000','111','000','000'), ':': ('000','010','000','010','000'),
    '/': ('001','001','010','100','100'), ' ': ('000',)*5,
    'A': ('010','101','111','101','101'), 'B': ('110','101','110','101','110'),
    'C': ('111','100','100','100','111'), 'D': ('110','101','101','101','110'),
    'E': ('111','100','110','100','111'), 'F': ('111','100','110','100','100'),
    'G': ('111','100','101','101','111'), 'H': ('101','101','111','101','101'),
    'I': ('111','010','010','010','111'), 'J': ('001','001','001','101','111'),
    'K': ('101','101','110','101','101'), 'L': ('100','100','100','100','111'),
    'M': ('101','111','111','101','101'), 'N': ('101','111','111','111','101'),
    'O': ('111','101','101','101','111'), 'P': ('111','101','111','100','100'),
    'Q': ('111','101','101','111','001'), 'R': ('110','101','110','101','101'),
    'S': ('111','100','111','001','111'), 'T': ('111','010','010','010','010'),
    'U': ('101','101','101','101','111'), 'V': ('101','101','101','101','010'),
    'W': ('101','101','111','111','101'), 'X': ('101','101','010','101','101'),
    'Y': ('101','101','010','010','010'), 'Z': ('111','001','010','100','111'),
    '.': ('000','000','000','000','010'), '!': ('010','010','010','000','010'),
}


def paint_rows(cr, rows, palette=PALETTE):
    """Batch horizontal runs by colour while constructing a cached sprite."""
    colors = {}
    for yy, row in enumerate(rows):
        xx = 0
        while xx < len(row):
            color = row[xx]
            end = xx + 1
            while end < len(row) and row[end] == color:
                end += 1
            if palette.get(color):
                colors.setdefault(color, []).append((xx, yy, end - xx, 1))
            xx = end
    for color, rects in colors.items():
        cr.set_source_rgb(*rgb(palette[color]))
        for rect in rects:
            cr.rectangle(*rect)
        cr.fill()


def export_sprite_sheet(path):
    """Optional developer preview; Pillow is never required on the board."""
    from PIL import Image, ImageDraw
    samples = [(f'walk {i}', player(i, 'running')) for i in range(4)]
    samples += [('idle', player()), ('jump', player(state='jumping')),
                ('power', player(powered=True)), ('hurt', player(state='hurt')),
                ('beetle', lantern()), ('stone', stone()), ('coin', coin()),
                ('shield', powerup()), ('question', tile('question')), ('grass', tile()),
                ('brick', tile('brick')), ('pipe', tile('pipe')),
                ('princess', princess()), ('checkpoint', checkpoint(True)),
                ('cave', tile('cave_ground')), ('castle', tile('castle_ground')),
                ('hall', tile('hall_ground'))]
    sheet = Image.new('RGB', (512, 336), '#edf0dc')
    draw = ImageDraw.Draw(sheet)
    for index, (label, rows) in enumerate(samples):
        x, y = (index % 8) * 64, (index // 8) * 112
        for yy, row in enumerate(rows):
            for xx, char in enumerate(row):
                color = PALETTE.get(char)
                if color:
                    draw.rectangle((x + 8 + xx * 2, y + 8 + yy * 2,
                                    x + 9 + xx * 2, y + 9 + yy * 2), fill=color)
        draw.text((x + 4, y + 64), label, fill='#20313f')
    sheet.save(path)
    return path
