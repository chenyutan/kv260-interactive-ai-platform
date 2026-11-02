"""Shared circular hit geometry and observed-only skeleton presentation."""
import math

MODES = ('ink', 'sword', 'fight', 'fruit', 'taiko', 'platformer', 'runner', 'pal', 'kart')
VISITOR_RECTS = ((.02, .39, .18, .66), (.82, .39, .98, .66))
EDGES = ((5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11),
         (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
         (0, 1), (0, 2), (1, 3), (2, 4))


def geometry(width=960, height=540):
    w, h = max(1, width), max(1, height)
    cx, cy = w * .5, h * .49
    orbit = min(h * .340, w * .290)
    radius = min(h * .098, w * .080)
    circles = tuple((cx + orbit * math.cos(-math.pi / 2 + i * math.tau / len(MODES)),
                     cy + orbit * math.sin(-math.pi / 2 + i * math.tau / len(MODES)), radius)
                    for i in range(len(MODES)))
    return (cx, cy, min(h * .080, w * .063)), circles


def rectangles(width=960, height=540, visitors=False):
    (cx,cy,r), circles = geometry(width, height)
    boxes = tuple(((x-r)/width, (y-r)/height, (x+r)/width, (y+r)/height)
                  for x, y, r in circles)
    boxes += (((cx-r)/width,(cy-r)/height,(cx+r)/width,(cy+r)/height),)
    return boxes + VISITOR_RECTS if visitors else boxes


def hit_index(x, y, width=960, height=540, visitors=False):
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
        return None
    (mx,my,mr), circles = geometry(width, height)
    for i, (cx, cy, r) in enumerate(circles):
        if math.hypot(x * width - cx, y * height - cy) <= r:
            return i
    if math.hypot(x*width-mx,y*height-my)<=mr:return len(MODES)
    if visitors:
        for i, (a, b, c, d) in enumerate(VISITOR_RECTS, len(MODES)+1):
            if a <= x <= c and b <= y <= d:
                return i
    return None


def observed_pose(state):
    """Do not extrapolate coordinates or replace measured performance values."""
    if not isinstance(state, dict) or state.get('stale') or state.get('available') is False:
        return (), None, None, None
    raw = state.get('points17') or ()
    points = []
    for i in range(17):
        try:
            x, y, score = map(float, raw[i][:3])
            valid = all(math.isfinite(v) for v in (x, y, score)) and 0 <= x <= 1 and 0 <= y <= 1 and score >= .05
            points.append((x, y, score) if valid else None)
        except (IndexError, TypeError, ValueError):
            points.append(None)
    def metric(name):
        try:
            value = float(state[name])
            return value if math.isfinite(value) and value >= 0 else None
        except (KeyError, TypeError, ValueError):
            return None
    key = (state.get('pid'), state.get('sequence'), state.get('timestamp_seconds'))
    return tuple(points), metric('pose_fps'), metric('latency_ms'), key
