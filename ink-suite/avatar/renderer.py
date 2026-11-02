"""Native Cairo ink-and-colour character; a fixed face and tailored costume.

No image of the participant is accessed. Decorative torso/head/robe art is
rasterized once per Renderer; sleeves, continuous robe mesh and limbs deform.
The supplied context is composited transparently, leaving backgrounds to GTK.
"""
import math
import cairo

from .rig import neutral_pose


INK = (.13, .22, .21)
DEEP = (.13, .29, .28)
JADE = (.31, .50, .43)
PALE = (.69, .76, .62)
PAPER = (.94, .90, .77)
SKIN = (.91, .79, .64)
_INK_TEXTURE = None
_FABRICS = {}
_CLOTH_CIRCLE = tuple((math.cos(i * math.tau / 16), math.sin(i * math.tau / 16)) for i in range(16))


def _pigment_pattern():
    """One immutable, tileable dry-pigment surface; never random per frame."""
    global _INK_TEXTURE
    if _INK_TEXTURE is None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 128, 128)
        c = cairo.Context(surface)
        # A deterministic low-discrepancy layout avoids a visible dot grid.
        for i in range(720):
            x, y = (i * 73.31) % 128, (i * i * .618 + i * 37.19) % 128
            radius = .32 + (i % 5) * .16
            c.set_source_rgba(*((.94, .92, .79) if i % 3 else (.06, .17, .15)), .10 + (i % 7) * .021)
            c.arc(x, y, radius, 0, math.tau); c.fill()
        for i in range(80):
            x, y = (i * 41.33) % 128, (i * 63.79) % 128
            c.set_source_rgba(.91, .89, .72, .12)
            c.set_line_width(.45); c.move_to(x, y); c.line_to(x + 2 + i % 4, y - .3); c.stroke()
        pattern = cairo.SurfacePattern(surface); pattern.set_extend(cairo.EXTEND_REPEAT)
        pattern.set_filter(cairo.FILTER_BILINEAR)
        _INK_TEXTURE = pattern
    return _INK_TEXTURE


def _path(c, items, close=True):
    c.new_path()
    for row in items:
        if row[0] == 'M': c.move_to(*row[1:])
        elif row[0] == 'L': c.line_to(*row[1:])
        elif row[0] == 'C': c.curve_to(*row[1:])
    if close: c.close_path()


def _cloth_outline(points):
    """Rounded convex cloth envelope; cannot self-intersect at folded elbows."""
    points = sorted(set(points))
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0: lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0: upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    rows = []
    for i, p in enumerate(hull):
        before, after = hull[i - 1], hull[(i + 1) % len(hull)]
        a = (p[0] * .77 + before[0] * .23, p[1] * .77 + before[1] * .23)
        b = (p[0] * .77 + after[0] * .23, p[1] * .77 + after[1] * .23)
        rows.append(('M' if i == 0 else 'L', *a))
        rows.append(('C', *p, *p, *b))
    return rows


def _fill(c, color, stroke=INK, width=1.5):
    c.set_source_rgba(*color) if len(color) == 4 else c.set_source_rgb(*color)
    c.fill_preserve()
    if stroke:
        c.set_source_rgba(*stroke) if len(stroke) == 4 else c.set_source_rgb(*stroke)
        c.set_line_width(width); c.stroke()
    else: c.new_path()


def _line(c, items, color=INK, width=1.):
    _path(c, items, False)
    c.set_source_rgba(*color) if len(color) == 4 else c.set_source_rgb(*color)
    c.set_line_width(width); c.stroke()


def _ellipse(c, x, y, rx, ry, color):
    c.save(); c.translate(x, y); c.scale(rx, ry)
    c.arc(0, 0, 1, 0, 2 * math.pi)
    c.set_source_rgba(*color) if len(color) == 4 else c.set_source_rgb(*color)
    c.fill(); c.restore()


def _wash(c, x0, y0, x1, y1, light, dark):
    # Gradient and dry pigment are immutable artwork. Baking them once avoids
    # two software raster passes plus a complex clip on ARM for every sleeve.
    key = (light, dark)
    if key not in _FABRICS:
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 256, 256)
        brush = cairo.Context(surface)
        g = cairo.LinearGradient(0, 0, 0, 256)
        g.add_color_stop_rgb(0, *light); g.add_color_stop_rgb(1, *dark)
        brush.set_source(g); brush.paint()
        # Fade grain at the texture border: PAD must extend a clean pigment
        # colour, otherwise one border dot becomes a long streak on a sleeve.
        mask=cairo.ImageSurface(cairo.FORMAT_A8,256,256);mc=cairo.Context(mask)
        for axis in (0,1):
            fade=cairo.LinearGradient(0,0,256 if axis==0 else 0,256 if axis==1 else 0)
            for stop,alpha in ((0.,0.),(.04,.78),(.96,.78),(1.,0.)):
                fade.add_color_stop_rgba(stop,1,1,1,alpha)
            if axis:mc.set_operator(cairo.OPERATOR_DEST_IN)
            mc.set_source(fade);mc.paint()
        brush.set_source(_pigment_pattern());brush.mask_surface(mask)
        surface.flush(); _FABRICS[key] = surface
    dx, dy = x1 - x0, y1 - y0
    length = max(1., math.hypot(dx, dy)); squared = length * length
    pattern = cairo.SurfacePattern(_FABRICS[key])
    # Only this low-contrast pigment wash uses point sampling. Silhouette,
    # face, costume lines and the final character remain antialiased.
    pattern.set_extend(cairo.EXTEND_PAD); pattern.set_filter(cairo.FILTER_NEAREST)
    # Project the cached wash onto precisely the previous gradient axis.
    pattern.set_matrix(cairo.Matrix(xx=-dy / length, xy=dx / length,
        yx=dx * 256 / squared, yy=dy * 256 / squared,
        x0=128 + (dy * x0 - dx * y0) / length,
        y0=-(dx * x0 + dy * y0) * 256 / squared))
    c.set_source(pattern); c.fill_preserve()
    # A faint soft edge sits outside the decisive ink contour.
    c.save(); c.set_source_rgba(*INK, .14)
    c.set_line_width(2.4); c.stroke_preserve(); c.restore()
    c.set_source_rgba(*INK, .72); c.set_line_width(1.15); c.stroke()


class Renderer:
    """Call render(ctx, width, height, pose, character='scholar').

    No display, GTK, network or camera is opened. Reuse this instance to retain
    its static artwork cache. Optional backgrounds belong to the caller.
    """
    def __init__(self):
        self.cache_builds = 0
        self.head = self._cache(100, 166, 50, 94, self._head)
        self.torso = self._cache(124, 174, 62, 162, self._torso)
        standing = neutral_pose().joints
        local = {i:(p[0]-640.,p[1]-417.) for i,p in standing.items()}
        self.skirt = self._cache(260, 246, 130, 10, lambda c:self._draw_skirt(c, local))
        self.skirt_rest = (min(local[15][0]-40.,-64.),max(local[16][0]+40.,64.),max(local[15][1],local[16][1])-3.)

    def _cache(self, w, h, x, y, painter):
        # 2x artwork retains the small ink face lines at fullscreen resolution.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * 2, h * 2)
        c = cairo.Context(surface); c.scale(2, 2); c.translate(x, y)
        c.set_line_cap(cairo.LINE_CAP_ROUND); c.set_line_join(cairo.LINE_JOIN_ROUND)
        painter(c); surface.flush(); self.cache_builds += 1
        return surface, x, y

    @staticmethod
    def _blit(c, cached, x, y, angle=0):
        surface, ox, oy = cached
        c.save(); c.translate(x, y); c.rotate(angle); c.scale(.5, .5)
        c.set_source_surface(surface, -ox * 2, -oy * 2)
        c.get_source().set_filter(cairo.FILTER_BILINEAR)
        c.paint(); c.restore()

    def _head(self, c):
        # Long hair under the jaw and a tied, brushed topknot.
        _path(c, [('M', -24, -39), ('C', -38, -16, -35, 36, -40, 65),
                  ('C', -18, 57, -19, 20, -10, 12), ('L', 24, 12),
                  ('C', 31, 28, 24, 56, 37, 66), ('C', 42, 18, 34, -35, 22, -45)])
        _fill(c, (.13, .19, .18), width=1.)
        _path(c, [('M', -15, -46), ('C', -29, -58, -19, -81, -2, -84),
                  ('C', 23, -84, 28, -60, 13, -44)])
        _fill(c, (.12, .17, .16))
        for k in range(6):
            x = -14 + k * 5
            _line(c, [('M', x, -49), ('C', x - 12, -65, x - 6, -77, 2, -80)], (.48, .48, .35, .36), .8)
        _path(c, [('M', -18, -51), ('L', 17, -54), ('L', 18, -46), ('L', -19, -43)])
        _fill(c, (.72, .65, .40), width=.8)
        _line(c, [('M', -28, -49), ('L', 28, -55)], (.20, .30, .27), 2.6)
        _ellipse(c, 29, -55, 3.4, 2.7, (.77, .70, .46))
        # Ears, neck and softly tapered face are all fixed character artwork.
        _ellipse(c, -26, 2, 5., 10., SKIN); _ellipse(c, 26, 2, 5., 10., SKIN)
        _path(c, [('M', -11, 21), ('L', -12, 45), ('C', -8, 55, 7, 57, 14, 44), ('L', 12, 20)])
        _fill(c, SKIN, (.46, .40, .31), .85)
        _path(c, [('M', -25, -27), ('C', -32, -7, -23, 25, -14, 34),
                  ('C', -7, 42, 3, 43, 11, 37), ('C', 28, 25, 32, -9, 23, -30),
                  ('C', 11, -43, -13, -44, -25, -27)])
        _wash(c, -24, -15, 27, 20, (.98, .90, .73), (.86, .75, .60))
        _path(c, [('M', -29, -1), ('C', -34, -37, -26, -54, -2, -53),
                  ('C', 26, -55, 38, -32, 29, 2), ('L', 23, -10),
                  ('C', 19, -20, 13, -27, 2, -31), ('C', -9, -14, -15, -12, -25, -12)])
        _fill(c, (.10, .16, .15), width=1.1)
        for k in range(5):
            _line(c, [('M', -20 + k * 5, -43), ('C', -4 + k * 3, -44, 15 + k * 2, -29, 23 + k, -15)], (.54, .54, .41, .21), .8)
        _line(c, [('M', -22, -29), ('C', -24, -1, -26, 18, -31, 30)], (.13, .20, .19), 2.1)
        _line(c, [('M', 26, -17), ('C', 27, 3, 29, 13, 31, 23)], (.13, .20, .19), 1.5)
        # Slender calligraphic brows, eyes and reserved expression.
        _line(c, [('M', -20, -5), ('C', -15, -9, -8, -8, -5, -6)], (.20, .24, .21), 1.6)
        _line(c, [('M', 5, -6), ('C', 12, -9, 17, -8, 20, -5)], (.20, .24, .21), 1.45)
        _line(c, [('M', -19, 1), ('C', -14, -1, -9, 0, -6, 2)], (.17, .23, .21), 1.)
        _line(c, [('M', 6, 2), ('C', 11, -1, 17, 0, 20, 1)], (.17, .23, .21), 1.)
        _ellipse(c, -12, 2, 1.35, 2.1, (.13, .20, .19)); _ellipse(c, 13, 2, 1.35, 2.1, (.13, .20, .19))
        _line(c, [('M', 1, 3), ('C', 0, 10, -3, 15, 1, 17), ('L', 5, 16)], (.55, .44, .33), .75)
        _line(c, [('M', -6, 25), ('C', -2, 23, 3, 23, 8, 24)], (.58, .34, .28), 1.2)
        _line(c, [('M', -2, 28), ('L', 4, 28)], (.64, .43, .34), .55)

    def _torso(self, c):
        # Origin is the belt/waist. Shape has fixed shoulders and fixed length.
        _path(c, [('M', -17, -154), ('C', -30, -151, -45, -144, -55, -137),
                  ('C', -58, -107, -43, -69, -32, -8), ('C', -14, 2, 14, 2, 33, -7),
                  ('C', 41, -68, 57, -109, 55, -137), ('C', 38, -148, 22, -153, 15, -154)])
        _wash(c, -50, -150, 51, 0, (.54, .65, .50), (.19, .38, .34))
        _path(c, [('M', -16, -153), ('L', 0, -124), ('L', 16, -153), ('L', 31, -139),
                  ('L', 1, -92), ('L', -31, -140)])
        _fill(c, (.90, .87, .73), (.34, .43, .34), 1.)
        # Right-over-left crossing collar, ochre piping and soft overlap shadow.
        _path(c, [('M', -23, -151), ('L', 35, -86), ('L', 27, -61), ('L', -35, -142)])
        _fill(c, (.23, .39, .33), (.18, .30, .26), 1.)
        _line(c, [('M', -26, -150), ('L', 32, -84)], (.72, .69, .45), 1.3)
        _path(c, [('M', 20, -151), ('L', 34, -141), ('L', -27, -56), ('L', -37, -72)])
        _wash(c, 15, -150, -31, -70, (.57, .63, .45), (.28, .44, .34))
        _line(c, [('M', 26, -148), ('C', 13, -119, -18, -82, -31, -63)], (.77, .70, .47), 1.5)
        for items in [
            [('M', -46, -117), ('C', -44, -84, -24, -56, -21, -17)],
            [('M', 43, -118), ('C', 39, -84, 19, -52, 17, -19)],
            [('M', -17, -60), ('C', -8, -40, -8, -31, -2, -12)],
            [('M', 28, -81), ('C', 24, -48, 29, -27, 26, -11)]]:
            _line(c, items, (.11, .27, .24, .48), 1.25)
        for x in (-41, -29, 23, 36):
            _line(c, [('M', x, -95), ('C', x + 6, -61, x / 2, -43, x / 2, -16)], (.81, .83, .65, .22), 2.)
        # Tiny botanical embroidery is baked into the torso surface.
        c.save(); c.translate(25, -104); c.rotate(-.2)
        _line(c, [('M', -2, 8), ('C', 0, 0, 3, -8, 7, -15)], (.72, .72, .49, .78), .65)
        for x, y, side in ((0, 3, -1), (3, -4, 1), (5, -10, -1)):
            _path(c, [('M', x, y), ('C', x + side * 8, y - 2, x + side * 6, y - 8, x, y - 3)])
            _fill(c, (.68, .71, .48, .75), None)
        c.restore()
        _path(c, [('M', -35, -11), ('C', -10, -6, 13, -7, 35, -13),
                  ('L', 34, 3), ('C', 10, 8, -11, 7, -35, 2)])
        _fill(c, (.17, .27, .23), (.11, .22, .19), 1.2)
        _line(c, [('M', -33, -6), ('C', -9, -1, 10, -2, 33, -8)], (.71, .63, .39), 1.3)
        _path(c, [('M', -10, -9), ('L', 7, -10), ('L', 10, 3), ('L', -8, 6)])
        _fill(c, (.64, .58, .37), (.29, .34, .25), .8)
        _ellipse(c, 0, -2, 3.6, 4.1, (.39, .55, .43))

    def _sleeve(self, c, shoulder, elbow, wrist, side):
        sx, sy = shoulder; ex, ey = elbow; wx, wy = wrist
        dx, dy = wx - ex, wy - ey
        length = max(1., math.hypot(dx, dy)); dx /= length; dy /= length
        nx, ny = -dy, dx
        if ny < -.02 or (abs(ny) <= .02 and nx * side < 0): nx, ny = -nx, -ny
        tx, ty = wx - nx * 18 - dx * 8, wy - ny * 18 - dy * 8
        bx, by = wx + nx * 27 - dx * 7, wy + ny * 27 - dy * 7
        lower_x, lower_y = ex + nx * 48, ey + ny * 48 + 38
        # Cover the bent arm with one continuous fabric envelope. A naive
        # shoulder-elbow-wrist Bezier loop inverts when a wrist folds back
        # towards the shoulder; its crossing creates a transparent hole.
        cloud = [(tx, ty), (bx, by), (wx - dx * 9, wy - dy * 9)]
        for dx_circle, dy_circle in _CLOTH_CIRCLE:
            cloud.append((sx + dx_circle * 27, sy + dy_circle * 25))
            cloud.append((ex + dx_circle * 32, ey + dy_circle * 36 + 10))
        # Gravity gathers the generous lower edge between elbow and cuff.
        cloud.append(((ex + wx) * .5 + nx * 31 - dx * 10,
                      (ey + wy) * .5 + ny * 31 - dy * 10 + 17))
        outline = _cloth_outline(cloud)
        _path(c, outline)
        silhouette = c.copy_path()
        _wash(c, sx, sy - 25, lower_x, lower_y, (.57, .68, .53), (.22, .41, .35))
        # Translucent pigment pools and thin irregular fold lines.
        c.save(); c.new_path(); c.append_path(silhouette); c.clip()
        _line(c, [('M', sx + side * 8, sy - 10), ('C', ex - nx * 15, ey - ny * 15,
                  wx - nx * 12 - dx * 28, wy - ny * 12 - dy * 28, tx - dx * 6, ty - dy * 6)], (.79, .81, .61, .53), 2.2)
        _line(c, [('M', sx + side * 11, sy + 22), ('C', ex + nx * 23, ey + ny * 23 + 21,
                  wx + nx * 17 - dx * 32, wy + ny * 17 - dy * 32 + 15, bx - dx * 10, by - dy * 10)], (.12, .28, .25, .67), 1.2)
        _line(c, [('M', ex - dx * 19, ey - dy * 19), ('C', ex + nx * 21, ey + ny * 21 + 21,
                  lower_x - dx * 14, lower_y - dy * 14 - 13, lower_x - dx * 25, lower_y - dy * 25)], (.12, .30, .27, .45), 1.1)
        _line(c, [('M', lower_x - dx * 22, lower_y - dy * 22 - 3),
                  ('C', lower_x + nx * 2, lower_y - 3, wx + nx * 28 - dx * 27, wy + ny * 28 - dy * 27 + 13, bx, by)], (.16, .31, .27, .40), 3.1)
        c.restore()
        # A woven cuff travels and rotates with the actual forearm.
        _path(c, [('M', tx, ty), ('L', tx + dx * 10, ty + dy * 10),
                  ('L', bx + dx * 10, by + dy * 10), ('L', bx, by)])
        _fill(c, (.78, .78, .57), (.23, .38, .31), 1.1)
        _line(c, [('M', tx + dx * 5, ty + dy * 5), ('L', bx + dx * 5, by + dy * 5)], (.43, .51, .35), .8)

    def _hand(self, c, elbow, wrist, side):
        dx, dy = wrist[0] - elbow[0], wrist[1] - elbow[1]
        c.save(); c.translate(*wrist); c.rotate(math.atan2(dy, dx) - math.pi / 2)
        _path(c, [('M', -8, -3), ('C', -9, 4, -10, 8, -8, 18),
                  ('C', -7, 24, -2, 29, 2, 28), ('C', 8, 28, 9, 20, 8, 15),
                  ('L', 8, 9), ('C', 14, 11, 16, 7, 12, 4), ('L', 7, -2)])
        _fill(c, SKIN, (.37, .39, .29), 1.)
        for x in (-4, 0, 4):
            _line(c, [('M', x, 16), ('L', x + 1, 24)], (.59, .49, .36), .55)
        _line(c, [('M', 7, 8), ('L', 4, 12)], (.59, .49, .36), .55)
        c.restore()

    def _skirt(self, c, joints):
        """Warp one cached robe continuously to the actual leg-driven hem.

        Four affine triangles preserve the belt width and follow both hem
        widths/height. No angle quantization or cache of repeated poses is used.
        The full-resolution ink folds, texture and jade ornament stay intact.
        """
        cx,cy=joints['waist'];lx,ly=joints[15];rx,ry=joints[16]
        rest_left,rest_right,rest_bottom=self.skirt_rest
        factors=(min(lx-cx-40.,-64.)/rest_left,max(rx-cx+40.,64.)/rest_right)
        vertical=(min(647.,max(ly,ry)-3.)-cy)/rest_bottom
        c.save();c.translate(cx,cy)
        surface,ox,oy=self.skirt
        for side,factor in ((-1,factors[0]),(1,factors[1])):
            x=130.*side
            source=((0.,-10.),(x,-10.),(x,236.),(0.,236.))
            target=((0.,-10.*vertical),(x,-10.*vertical),(x*factor,236.*vertical),(0.,236.*vertical))
            for indices in ((0,1,2),(0,2,3)):
                src=[source[i] for i in indices];dst=[target[i] for i in indices]
                u1,v1=src[1][0]-src[0][0],src[1][1]-src[0][1]
                u2,v2=src[2][0]-src[0][0],src[2][1]-src[0][1]
                du1,dv1=dst[1][0]-dst[0][0],dst[1][1]-dst[0][1]
                du2,dv2=dst[2][0]-dst[0][0],dst[2][1]-dst[0][1]
                det=u1*v2-u2*v1
                xx,xy=(du1*v2-du2*v1)/det,(du2*u1-du1*u2)/det
                yx,yy=(dv1*v2-dv2*v1)/det,(dv2*u1-dv1*u2)/det
                tx=dst[0][0]-xx*src[0][0]-xy*src[0][1]
                ty=dst[0][1]-yx*src[0][0]-yy*src[0][1]
                c.save();c.set_antialias(cairo.ANTIALIAS_NONE)
                c.move_to(*dst[0]);c.line_to(*dst[1]);c.line_to(*dst[2]);c.close_path();c.clip()
                c.transform(cairo.Matrix(xx,yx,xy,yy,tx,ty));c.scale(.5,.5)
                c.set_source_surface(surface,-ox*2,-oy*2);c.get_source().set_filter(cairo.FILTER_BILINEAR)
                c.paint();c.restore()
        c.restore()

    def _draw_skirt(self, c, joints):
        lx, ly = joints[15]; rx, ry = joints[16]
        cx, cy = joints['waist']
        # Cloth follows both legs but retains a continuous, generous robe hem.
        left, right = min(lx - 40, cx - 64), max(rx + 40, cx + 64)
        bottom = min(647., max(ly, ry) - 3)
        _path(c, [('M', cx - 34, cy - 3), ('L', cx + 34, cy - 3),
                  ('C', cx + 42, cy + 66, right - 16, bottom - 49, right, bottom - 5),
                  ('C', right - 18, bottom + 9, cx + 20, bottom - 2, cx - 4, bottom + 2),
                  ('C', cx - 34, bottom + 6, left + 10, bottom + 1, left, bottom - 8),
                  ('C', left + 18, bottom - 74, cx - 40, cy + 54, cx - 34, cy - 3)])
        _wash(c, left, cy, right, bottom, (.39, .55, .45), (.17, .33, .31))
        _path(c, [('M', cx - 23, cy + 3), ('L', cx + 8, cy + 4),
                  ('C', cx + 1, cy + 98, cx + 40, bottom - 53, cx + 30, bottom),
                  ('C', cx + 7, bottom + 1, cx - 8, bottom + 3, cx - 22, bottom),
                  ('C', cx - 14, cy + 98, cx - 23, cy + 70, cx - 23, cy + 3)])
        _fill(c, (.64, .69, .52, .65), (.20, .36, .30, .5), .8)
        for start, end, bend in ((-26, left + 13, -30), (-17, left + 33, -13), (-4, cx - 6, 8),
                                  (13, cx + 41, 12), (26, right - 19, 22)):
            _line(c, [('M', cx + start, cy + 12), ('C', cx + start + bend, cy + 80,
                      end - bend * .3, bottom - 57, end, bottom - 9)], (.09, .23, .22, .53), 1.5)
            _line(c, [('M', cx + start + 3, cy + 26), ('C', cx + start + bend + 4, cy + 86,
                      end - bend * .3 + 4, bottom - 60, end + 3, bottom - 13)], (.76, .79, .62, .34), 2.2)
        _line(c, [('M', left + 5, bottom - 7), ('C', cx - 46, bottom + 2, cx + 19, bottom - 9, right - 5, bottom - 8)], (.66, .64, .42), 1.4)
        # Decorative trailing sash and jade pendant, fixed to the belt.
        _path(c, [('M', cx + 16, cy + 2), ('C', cx + 23, cy + 38, cx + 48, cy + 54, cx + 37, cy + 98),
                  ('L', cx + 48, cy + 91), ('C', cx + 54, cy + 46, cx + 31, cy + 22, cx + 30, cy)])
        _fill(c, (.76, .72, .47), (.34, .41, .29), .8)
        _line(c, [('M', cx - 19, cy + 5), ('C', cx - 12, cy + 24, cx - 8, cy + 39, cx - 14, cy + 55)], (.77, .69, .43), 1.2)
        _ellipse(c, cx - 14, cy + 58, 4., 7., (.55, .70, .57))
        for offset in (-2, 0, 2):
            _line(c, [('M', cx - 14 + offset, cy + 65), ('L', cx - 17 + offset, cy + 81)], (.42, .40, .27), .75)

    def render(self, ctx, width, height, pose=None, character='scholar'):
        """Composite one character over the caller's background; no clear/paint."""
        if width <= 0 or height <= 0:
            return
        pose = pose or neutral_pose()
        j = pose.joints
        scale = min(width / 1280., height / 720.)
        ctx.save(); ctx.translate((width - 1280 * scale) / 2, (height - 720 * scale) / 2)
        ctx.scale(scale, scale); ctx.set_line_join(cairo.LINE_JOIN_ROUND); ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        # Only lost tracking needs an offscreen alpha group.
        faded = pose.opacity < .999
        if faded: ctx.push_group()
        waist = j['waist']
        _ellipse(ctx, waist[0], 644, 102, 8, (.16, .27, .21, .07))
        for index, side in ((15, -1), (16, 1)):
            x, y = j[index]
            _path(ctx, [('M', x - 12, y - 14), ('L', x + 11, y - 14),
                        ('C', x + 15, y - 3, x + side * 30, y - 1, x + side * 29, y + 5),
                        ('C', x + 8, y + 11, x - 21, y + 8, x - 17, y)])
            _fill(ctx, (.13, .23, .22), (.08, .16, .15), 1.1)
        self._skirt(ctx, j)
        front = []
        for sh, el, wr, side in ((5, 7, 9, -1), (6, 8, 10, 1)):
            if (j[wr][0] - j[sh][0]) * side < 5 and j[wr][1] < waist[1] + 15:
                front.append((sh, el, wr, side))
            else:
                self._sleeve(ctx, j[sh], j[el], j[wr], side)
        self._blit(ctx, self.torso, *waist, pose.lean)
        self._blit(ctx, self.head, *j['head'], pose.lean + pose.head_angle)
        for sh, el, wr, side in front:
            self._sleeve(ctx, j[sh], j[el], j[wr], side)
        for el, wr, side in ((7, 9, -1), (8, 10, 1)):
            self._hand(ctx, j[el], j[wr], side)
        if faded:
            ctx.pop_group_to_source(); ctx.paint_with_alpha(max(0., min(1., pose.opacity)))
        ctx.restore()


_default_renderer = None


def render(ctx, width, height, pose=None, character='scholar'):
    """Convenience entry point retaining one native template cache."""
    global _default_renderer
    if _default_renderer is None:
        _default_renderer = Renderer()
    _default_renderer.render(ctx, width, height, pose, character)
