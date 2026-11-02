"""Bounded 2-D retargeting, with no camera, network or GTK dependency.

Input is the existing API state: points17 contains COCO [x, y, confidence]
in normalized *unmirrored camera* coordinates. Output is in a fixed 1280x720
artboard. Joint keys are template slots (5 is the screen-left shoulder);
unmirrored mode swaps paired anatomical labels into those screen slots. Only
bone directions drive the template: camera body proportions never resize it.
The caller must call update every draw tick, even while the backend is lost.
"""
from dataclasses import dataclass, field
import math


WIDTH, HEIGHT = 1280, 720
ARM_LENGTHS = (79., 73.)
LEG_LENGTHS = (105., 105.)
HOLD_SECONDS = .24
LOST_SECONDS = .38
RETURN_SECONDS = .70
_SIDES = ((5, 7, 9, 11, 13, 15, -1), (6, 8, 10, 12, 14, 16, 1))


def _clamp(value, low, high):
    return max(low, min(high, value))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _angle_delta(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def _neutral_angles():
    return {7: 1.90, 9: 1.75, 8: 1.24, 10: 1.39,
            13: 1.61, 15: 1.60, 14: 1.53, 16: 1.54,
            'lean': 0., 'head': 0., 'shift': 0.}


@dataclass(frozen=True)
class RenderPose:
    joints: dict
    status: str = 'waiting'
    message: str = '请入镜，展开双肩与双臂'
    tracking: bool = False
    opacity: float = 1.
    source_age: float = math.inf
    missing: tuple = field(default_factory=tuple)
    mirrored: bool = True
    lean: float = 0.
    head_angle: float = 0.
    source_key: tuple | None = None


def _geometry(angles, mirrored=True, **metadata):
    # Shoulders and hips rotate as a single fixed-width torso around the waist.
    lean = _clamp(angles['lean'], -.20, .20)
    shift = _clamp(angles['shift'], -72., 72.)
    waist = (640. + shift, 417.)
    along = (math.cos(lean), math.sin(lean))
    up = (math.sin(lean), -math.cos(lean))
    shoulders = (waist[0] + up[0] * 139., waist[1] + up[1] * 139.)
    joints = {'waist': waist, 'neck': (shoulders[0] + up[0] * 22., shoulders[1] + up[1] * 22.),
              'head': (shoulders[0] + up[0] * 62., shoulders[1] + up[1] * 62.)}
    for sh, el, wr, hip, knee, ankle, side in _SIDES:
        joints[sh] = (shoulders[0] + side * along[0] * 50., shoulders[1] + side * along[1] * 50.)
        joints[hip] = (waist[0] + side * along[0] * 30., waist[1] + side * along[1] * 30.)
        for parent, child, length in ((sh, el, 79.), (el, wr, 73.), (hip, knee, 105.), (knee, ankle, 105.)):
            a = angles[child]
            joints[child] = (joints[parent][0] + math.cos(a) * length,
                             joints[parent][1] + math.sin(a) * length)
    return RenderPose(joints, mirrored=mirrored, lean=lean, head_angle=angles['head'], **metadata)


def neutral_pose(mirrored=True):
    """An attractive stationary template, also used before the first observation."""
    return _geometry(_neutral_angles(), mirrored)


class Rig:
    def __init__(self, mirrored=True, min_confidence=.20, aspect=16 / 9):
        self.mirrored = bool(mirrored)
        self.min_confidence = _clamp(float(min_confidence), .05, .95)
        self.aspect = float(aspect) if _finite(aspect) and aspect > 0 else 16 / 9
        self.reset()

    def reset(self):
        self.angles = _neutral_angles()
        self.targets = {}
        self.seen = {}
        self.last_key = self.last_time = self.last_good = self.identity = self.body = None
        self.last_source_stamp = None
        self.missing = tuple(range(5, 17))
        self.discontinuities = 0

    def _clear_person(self):
        # Held angles must never be inherited by the next selected person.
        self.angles = _neutral_angles()
        self.targets.clear()
        self.seen.clear()
        self.last_good = self.body = None
        self.discontinuities += 1

    def _point(self, points, i):
        try:
            p = points[i]
            if len(p) < 3 or not all(_finite(v) for v in p[:3]):
                return None
            if p[2] < self.min_confidence or not (0 <= p[0] <= 1 and 0 <= p[1] <= 1):
                return None
            return (float(p[0]) * self.aspect, float(p[1]))
        except (TypeError, IndexError, KeyError):
            return None

    def _observe(self, state, now):
        if not isinstance(state, dict) or state.get('stale') or state.get('available') is False:
            return
        stamp, sequence = state.get('timestamp_seconds'), state.get('sequence')
        if not _finite(stamp) or sequence is None:
            return
        key = (state.get('pid'), sequence, stamp)
        if key == self.last_key:
            return
        latency = state.get('latency_ms')
        if _finite(latency) and latency > 850:
            return
        selection = state.get('roi_selection') or {}
        if not isinstance(selection, dict):
            selection = {}
        identity = (state.get('pid'), state.get('person_id', state.get('track_id',
                    selection.get('person_id', selection.get('track_id')))))
        switched = (self.identity is not None and identity != self.identity)
        backwards = self.last_source_stamp is not None and stamp <= self.last_source_stamp
        discontinuous = bool(selection.get('motion_history_discontinuity') or state.get('motion_history_discontinuity'))
        if backwards and not switched:
            # A republished frozen timestamp remains stale even if an outer
            # API sequence counter changes. Clock rollback invalidates this
            # frame and seeds the next epoch without borrowing old joints.
            if stamp < self.last_source_stamp:
                self._clear_person()
                self.last_key, self.last_source_stamp = key, stamp
            return
        if switched or backwards or discontinuous:
            self._clear_person()
        self.identity, self.last_key, self.last_source_stamp = identity, key, stamp
        points = state.get('points17')
        if not isinstance(points, (list, tuple)) or len(points) != 17:
            return
        p = {i: self._point(points, i) for i in range(17)}
        self.missing = tuple(i for i in range(5, 17) if p[i] is None)
        a, b = p[5], p[6]
        if a is None or b is None:
            return
        width = math.hypot(a[0] - b[0], a[1] - b[1])
        if not .055 <= width <= 1.05:
            return
        center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if self.body is not None and self.last_good is not None:
            old_center, old_width = self.body
            jump = math.hypot(center[0] - old_center[0], center[1] - old_center[1])
            if (now - self.last_good > .65 or jump > max(.14, old_width * .90)
                    or not .57 <= width / old_width <= 1.75):
                self._clear_person()
        self.body, self.last_good = (center, width), now
        mirror = -1. if self.mirrored else 1.
        # Anatomical labels are mapped onto the displayed template consistently.
        # With mirror disabled the label-to-screen side is reversed too.
        mapping = {} if self.mirrored else {5: 6, 6: 5, 7: 8, 8: 7, 9: 10, 10: 9,
                                            11: 12, 12: 11, 13: 14, 14: 13, 15: 16, 16: 15}
        unusable = set(self.missing)
        for sh, el, wr, hip, knee, ankle, side in _SIDES:
            for parent, child in ((sh, el), (el, wr), (hip, knee), (knee, ankle)):
                start, end = p[parent], p[child]
                if start is None or end is None:
                    continue
                dx, dy = (end[0] - start[0]) * mirror, end[1] - start[1]
                length = math.hypot(dx, dy)
                if not .07 * width <= length <= 2.5 * width:
                    unusable.add(child)
                    continue
                angle = math.atan2(dy, dx)
                if child >= 13:
                    angle = _clamp(angle, .87, 2.27)  # grounded, modest leg motion
                target_index = mapping.get(child, child)
                self.targets[target_index], self.seen[target_index] = angle, now
        # Lean uses current shoulders and hips together, never stale landmarks.
        self.missing = tuple(sorted(unusable))
        lean = math.atan2((a[1] - b[1]) * mirror, abs(a[0] - b[0]))
        if p[11] is not None and p[12] is not None:
            hx = (p[11][0] + p[12][0]) / 2
            hy = (p[11][1] + p[12][1]) / 2
            if hy - center[1] > width * .4:
                lean = .25 * lean + .75 * math.atan2((center[0] - hx) * mirror, hy - center[1])
        self.targets['lean'], self.seen['lean'] = _clamp(lean, -.20, .20), now
        self.targets['shift'], self.seen['shift'] = _clamp((center[0] / self.aspect - .5) * mirror * 170., -72., 72.), now
        nose = p[0]
        if nose is not None:
            self.targets['head'] = _clamp((nose[0] - center[0]) * mirror / width * .3, -.12, .12)
            self.seen['head'] = now

    def update(self, state, now):
        """Return RenderPose; now is the caller's monotonic time in seconds.

        Duplicate API frames never refresh confidence. Missing bones retain
        their last direction for 240 ms, then return to neutral over 700 ms.
        Unavailable input, lost shoulders and frozen frames share this timeout.
        Backend restart, explicit target change and implausible torso jumps
        reset every cached bone before accepting the new person's frame.
        """
        if not _finite(now):
            self.reset()
            return neutral_pose(self.mirrored)
        if self.last_time is not None and now < self.last_time:
            self.reset()
        dt = min(.10, max(0., now - self.last_time)) if self.last_time is not None else 1 / 30
        self.last_time = now
        self._observe(state, now)
        neutral = _neutral_angles()
        alpha = 1. - math.exp(-dt / .075)
        for key, default in neutral.items():
            age = now - self.seen.get(key, -math.inf)
            target = self.targets.get(key, default)
            if age > HOLD_SECONDS:
                fraction = _clamp((age - HOLD_SECONDS) / RETURN_SECONDS, 0., 1.)
                delta = target - default if key == 'shift' else _angle_delta(target, default)
                target = default + delta * (1. - fraction)
            delta = target - self.angles[key] if key == 'shift' else _angle_delta(target, self.angles[key])
            step = _clamp(delta * alpha, -(250. if key == 'shift' else 11.) * dt,
                          (250. if key == 'shift' else 11.) * dt)
            self.angles[key] += step
            # No numerical tail retains a stale gesture indefinitely.
            if age >= HOLD_SECONDS + RETURN_SECONDS + .35:
                self.angles[key] = default
        age = now - self.last_good if self.last_good is not None else math.inf
        if self.last_good is None:
            status, message = 'waiting', '请入镜，展开双肩与双臂'
        elif age > LOST_SECONDS:
            status, message = 'lost', '暂未识别人形 · 正在回到自然站姿'
        elif age > .14:
            status, message = 'holding', '正在等待新的动作'
        elif any(i in self.missing for i in (7, 8, 9, 10)):
            status, message = 'partial', '请让肩、肘、腕清晰入镜'
        elif any(i in self.missing for i in (11, 12, 13, 14, 15, 16)):
            status, message = 'partial', '上半身跟随 · 腿部保持自然站姿'
        else:
            status, message = 'tracking', '人物正在跟随你的动作'
        opacity = 1. if status != 'lost' else 1. - .35 * _clamp((age - LOST_SECONDS) / .7, 0., 1.)
        return _geometry(self.angles, self.mirrored, status=status, message=message,
                         tracking=age <= LOST_SECONDS, opacity=opacity, source_age=age,
                         missing=self.missing, source_key=self.last_key)
