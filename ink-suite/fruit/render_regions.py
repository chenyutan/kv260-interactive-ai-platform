"""Finite damage planning for the fruit game's persistent RGB backbuffer.

Only presentation is affected. All game positions and collision tests remain
in the original 1280 by 720 logical plane.
"""
import math

MAX_DAMAGE_RECTS = 4
FULL_REDRAW_RATIO = .4


def intersects(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def merge_damage(rectangles, width, height, limit=MAX_DAMAGE_RECTS,
                 full_ratio=FULL_REDRAW_RATIO):
    """Round outwards, clip, and merge overlapping boxes with a fixed budget."""
    checked = []
    checked_area = 0
    full_area = width * height * full_ratio
    for rect in rectangles:
        if len(rect) != 4 or not all(math.isfinite(v) for v in rect):
            return [(0, 0, width, height)]
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            continue
        left, top = max(0, math.floor(x)), max(0, math.floor(y))
        right, bottom = min(width, math.ceil(x+w)), min(height, math.ceil(y+h))
        if right <= left or bottom <= top:
            continue
        current = (left, top, right-left, bottom-top)
        if current[2] * current[3] >= full_area:
            return [(0, 0, width, height)]
        # Merge against all earlier boxes again after an expansion. Never
        # re-blend overlapping partial clips into the same pixel twice.
        index = 0
        while index < len(checked):
            old = checked[index]
            if intersects(current, old):
                left, top = min(current[0],old[0]), min(current[1],old[1])
                right = max(current[0]+current[2], old[0]+old[2])
                bottom = max(current[1]+current[3], old[1]+old[3])
                current = left,top,right-left,bottom-top
                checked.pop(index)
                checked_area -= old[2] * old[3]
                if current[2] * current[3] >= full_area:
                    return [(0, 0, width, height)]
                index = 0
            else:
                index += 1
        checked.append(current)
        checked_area += current[2] * current[3]
        # Merged damage area cannot shrink when later rectangles are added.
        # Dense scenes already require a full paint; do not spend another
        # quadratic pass merging the remaining current/previous bounds.
        if len(checked)>limit or checked_area >= full_area:
            return [(0,0,width,height)]
    return checked


def clip_damage(context, width, height, layout=None):
    """Read actual GTK damage; map the device pixels to our small RAM frame.

    A nonrectangular/unsupported clip falls back to the full frame. Initial
    expose and resize are safe for the same reason.
    """
    try:
        rectangles = list(context.copy_clip_rectangle_list())
        if layout is not None:
            mapped = []
            for x,y,w,h in rectangles:
                left,top = layout.render_point(x,y)
                right,bottom = layout.render_point(x+w,y+h)
                mapped.append((left-1,top-1,right-left+2,bottom-top+2))
            rectangles = mapped
        return merge_damage(rectangles,width,height)
    except Exception:
        return [(0,0,width,height)]


def apply_clip(context, rectangles):
    for rect in rectangles:
        context.rectangle(*rect)
    context.clip()
