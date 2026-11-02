"""Versioned scenic details shared by exported ink and progressive playback.

Only bounded vector brush paths are generated. The caller's existing stroke
callback owns measured speed, ink texture and recording of small dirty patches.
This module never allocates a canvas, changes action recognition, or loads art.
"""
from __future__ import annotations

import math
import numpy as np

ART_VERSION = 2
MOTIF_ACTIONS = {"A01": "远鸟", "A04": "岸苇", "A06": "水纹"}
GROUP_LIMITS = {"A01": 3, "A04": 3, "A06": 4}


def art_version(event):
    """Absent version is the original renderer; reject unknown future recipes."""
    value = event.get("art_version", 0)
    if type(value) is not int or value not in (0, 1, 2):
        raise ValueError(f"Unsupported ink art_version: {value!r}")
    return value


def motif_strokes(event, width, height, history):
    """Return ordered paths in pixels; no mutation of event/history/coordinates."""
    if art_version(event) == 0 or event["theme"] != "landscape":
        return []
    action = event["action"]
    if action not in MOTIF_ACTIONS:
        return []
    count = sum(e.get("art_version") in (1, 2) and e.get("theme") == "landscape"
                and e.get("action") == action for e in history)
    # Repeated accepted actions still paint their main subject. Keep a bounded
    # number of scenic groups so a long session does not fill every blank space.
    if count >= GROUP_LIMITS[action]:
        return []
    rng = np.random.default_rng(int(event["seed"]) ^ 0x5A17B1AD)
    amplitude = float(np.clip(event["amplitude"], .25, 3.5))
    direction = float(np.clip(event["direction"], -1., 1.))
    lean = float(np.clip(event["lean"], -.8, .8))
    w, h, scale = float(width), float(height), float(width) / 1280.
    paths = []

    def brush(points, weight, density, color=(35, 46, 44), **kwargs):
        paths.append(dict(points=[(float(x), float(y)) for x, y in points],
                          weight=weight, density=density, color=color, dry=False, **kwargs))

    if action == "A01":
        # Small unequal wings, a single body stroke, and staggered flight make
        # these distant ink birds rather than repeated geometric letter-Ms.
        centers = ((.58, .21), (.35, .14), (.76, .135))
        cx, cy = centers[count]
        cx += .025 * direction
        cy += .009 * lean
        n = min(4, max(2, 2 + int(amplitude / 1.15)))
        unit = (12.5 + 2.3 * amplitude) * scale
        flight = 1. if direction >= 0 else -1.
        offsets = ((0., 0.), (2.8, -1.15), (-2.9, -.95), (5.0, -1.45))
        for i in range(n):
            ox, oy = offsets[i]
            size = unit * (1. if i == 0 else .76 - .07 * (i - 1))
            x = cx * w + ox * unit * flight
            y = cy * h + oy * unit
            rise = float(rng.uniform(.35, .68))
            tilt = .10 * direction + float(rng.uniform(-.07, .07))

            def bird(points):
                return [(x + flight * px * size, y + (py + tilt * px) * size) for px, py in points]

            tone = .98 - .11 * i
            brush(bird([(-1.05, -rise), (-.68, -.26), (-.27, -.17), (0., .13)]), .42, tone)
            brush(bird([(0., .13), (.30, -.18), (.75, -rise * .70), (1.04, -rise * .48)]), .44, tone)
            brush(bird([(-.12, .33), (.02, .10), (.16, -.10)]), .43, min(1.2, tone + .12))

    elif action == "A04":
        # A few ochre seed heads and sparse swept leaves at the near waterline.
        side = 1. if direction >= 0 else -1.
        cx = ((.28, .70, .41) if side > 0 else (.71, .30, .58))[count] * w
        base = h * (.85 - .017 * count)
        n = 3 + int(amplitude > 1.5)
        wind = side * (12 + 11 * abs(lean) + 5 * amplitude) * scale
        for i in range(n):
            root = np.array([cx + (i - (n - 1) / 2) * 10 * scale, base + math.sin(i * 1.4) * 3 * scale])
            height_px = (60 + 14 * amplitude) * (.69, 1.08, .90, .75)[i] * scale
            bend = wind * (.7 + .17 * i)
            tip = root + (bend, -height_px)
            stem = [root, root + (.12 * bend, -.33 * height_px),
                    root + (.47 * bend, -.70 * height_px), tip]
            brush(stem, .20, .75, color=(54, 65, 50))
            for j, sign in enumerate((-1., 1.)):
                start = root + (bend * (.12 + .14 * j), -height_px * (.24 + .17 * j))
                reach = (22 + 4 * amplitude) * scale * sign * float(rng.uniform(.77, 1.12))
                brush([start, start + (reach * .43, -height_px * .20),
                       start + (reach, -height_px * .28)], .48, .69, color=(50, 65, 51))
            brush([tip + (-2 * scale * side, 8 * scale), tip + (3 * scale * side, -1 * scale),
                   tip + (9 * scale * side, -12 * scale)], .82, .60, color=(107, 91, 62))
        brush([(cx - .026 * w, base + .004 * h), (cx - .010 * w, base - .001 * h),
               (cx + .012 * w, base + .001 * h), (cx + .038 * w, base + .002 * h)],
              .17, .29, color=(71, 80, 63))
        brush([(cx - .010 * w, base + .010 * h), (cx + .014 * w, base + .009 * h),
               (cx + .029 * w, base + .011 * h)], .13, .20, color=(66, 85, 79))

    else:
        # Broken unequal lines suggest water without covering its paper light.
        cx = w * (.49 + .08 * direction + (count % 2 - .5) * .06)
        base = h * (.735 + .047 * count)
        span = w * (.18 + .025 * amplitude)
        for j in range(6):
            center = cx + float(rng.uniform(-.05, .05)) * w
            yy = base + j * h * .0105
            length = span * (1 - .075 * j)
            lo, hi = center - length * .52, center + length * .48
            # Alternating the gap supplies breathing space and avoids a ruler.
            if j % 2:
                lo += length * .28
            brush([(lo, yy), (lo + (hi - lo) * .31, yy - h * .002),
                   (lo + (hi - lo) * .69, yy + h * .0014), (hi, yy - h * .001)],
                  .27 if j < 2 else .17, .38 - j * .025, color=(65, 89, 87))

    # Each compound detail consumes one continuous original speed profile.
    # SF/FS is distributed from the first stroke to the last, not restarted in
    # every wing or reed leaf. The existing brush retains its paper-taper tips.
    for i, path in enumerate(paths):
        path["profile_range"] = (i / len(paths), (i + 1) / len(paths))
    return paths


def paint_motifs(event, width, height, history, stroke):
    for path in motif_strokes(event, width, height, history):
        stroke(**path)
