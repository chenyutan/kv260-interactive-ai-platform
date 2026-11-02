"""Bounded PAL key mappings. This module never opens an input or X11 device."""
import math

KEYS = frozenset(('Up', 'Down', 'Left', 'Right', 'Return', 'Escape', 'space',
                  'Page_Up', 'Page_Down'))
BODY_HINT = '右手向四方指引行走 · 左手举过肩确认 · 左手向外伸返回 · 转两圈菜单'
PAD_HINT = '左摇杆/方向键移动 · A确认 · B返回 · X调查 · Y仙剑菜单 · LB/RB翻页 · Start体验菜单'


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class BodyKeys:
    """Right wrist is a relative four-way stick; left gestures are edge triggered.

    A released gesture must be seen after entry/recovery before confirmation can
    fire. Freshness is checked against the board's monotonic pose timestamp.
    """
    def __init__(self, mirrored=True):
        self.mirrored = mirrored
        self.sequence = self.stamp = None
        self.identity = None
        self.reset()

    def reset(self):
        self.direction = None
        self.left_armed = False
        self.last_valid = None

    def update(self, packet, now):
        empty = dict(valid=False, held=set(), pulses=set(), reason='missing_pose')
        if not finite(now) or not isinstance(packet, dict):
            self.reset(); return empty
        stamp, sequence = packet.get('timestamp_seconds'), packet.get('sequence')
        if (not finite(stamp) or not isinstance(sequence, int) or isinstance(sequence, bool)
                or sequence < 0 or not -.05 <= now - stamp <= .45
                or packet.get('stale') or packet.get('available') is False):
            self.reset(); return dict(empty, reason='expired_pose')
        if stamp == self.stamp and sequence == self.sequence:
            valid = self.last_valid is not None and now - self.last_valid <= .16
            return dict(empty, valid=valid, held={self.direction} if valid and self.direction else set(), reason='duplicate')
        if (self.stamp is not None and (stamp <= self.stamp or sequence <= self.sequence)
                and packet.get('pid') == self.identity and not packet.get('stream_reset')):
            self.reset(); return dict(empty, reason='out_of_order')
        if self.identity != packet.get('pid') or self.last_valid is None or now - self.last_valid > .20:
            self.reset()
        self.stamp, self.sequence, self.identity = stamp, sequence, packet.get('pid')
        points = packet.get('points17')
        def point(index):
            try:
                p = points[index]
                if not all(finite(x) for x in p[:3]) or len(p) < 3 or not 0 < p[2] <= 1 or not all(0 <= x <= 1 for x in p[:2]):
                    return None
                return ((1-p[0] if self.mirrored else p[0])*16/9, p[1])
            except (IndexError, TypeError, KeyError):
                return None
        ls, rs, lw, rw = [point(i) for i in (5, 6, 9, 10)]
        if ls is None or rs is None or rw is None:
            self.reset(); return dict(empty, reason='missing_arm')
        width = math.dist(ls, rs)
        if not .055 <= width <= 1.1 or abs(ls[1]-rs[1]) > width*.65 or math.dist(rs, rw) > width*2.65:
            self.reset(); return dict(empty, reason='implausible_arm')
        self.last_valid = now
        # Comfortable bent elbow is neutral; no fixed person centre calibration.
        x, y = (rw[0]-rs[0])/width, (rw[1]-rs[1])/width-.55
        candidate = ('Right' if x > 0 else 'Left') if abs(x) > abs(y) else ('Down' if y > 0 else 'Up')
        magnitude = max(abs(x), abs(y))
        self.direction = candidate if magnitude >= (.32 if candidate == self.direction else .53) else None
        pulses = set()
        if lw is not None and math.dist(ls, lw) <= width*2.65:
            lx, ly = (lw[0]-ls[0])/width, (lw[1]-ls[1])/width
            confirm = ly < -.12
            back = lx > .80 and ly > .0
            if not confirm and not back:
                self.left_armed = True
            elif self.left_armed:
                pulses.add('Return' if confirm else 'Escape'); self.left_armed = False
        else:
            self.left_armed = False
        if pulses:
            self.direction = None
        return dict(valid=True, held={self.direction} if self.direction else set(), pulses=pulses, reason='tracking')


class PadKeys:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = set()
        self.armed = False

    def update(self, packet, now):
        if not isinstance(packet, dict) or not packet.get('connected'):
            self.reset(); return dict(valid=False, held=set(), pulses=set(), reason='controller_disconnected')
        stamp = packet.get('timestamp', now)
        # evdev is event-driven: an axis held still may legitimately produce no
        # event for seconds. Connectivity is authoritative, not last-event age.
        if not finite(stamp) or not finite(now) or stamp-now > .05:
            self.reset(); return dict(valid=False, held=set(), pulses=set(), reason='controller_expired')
        buttons = set(packet.get('buttons') or ())
        axes = packet.get('axes') or {}
        x, y = axes.get('lx', axes.get('left_x', 0)), axes.get('ly', axes.get('left_y', 0))
        if not finite(x) or not finite(y):
            x = y = 0
        raw_pressed = set(packet.get('pressed') or ())
        if not self.armed:
            # Entry, host ownership transfer and reconnect all require one
            # genuinely neutral observation. A held direction is input too;
            # never walk into danger while the host still owns the controller.
            triggers = [axes.get(name, 0) for name in ('lt', 'rt')]
            neutral = (not buttons and not raw_pressed and abs(x)<.25 and abs(y)<.25
                       and all(finite(v) and v<.2 for v in triggers))
            self.previous = buttons
            if not neutral:
                return dict(valid=False, held=set(), pulses=set(), reason='controller_release_required')
            self.armed = True
            return dict(valid=True, held=set(), pulses=set(), reason='controller')
        held = set()
        for key, condition in (('Left',x < -.4 or 'LEFT' in buttons or 'DPAD_LEFT' in buttons),
                               ('Right',x > .4 or 'RIGHT' in buttons or 'DPAD_RIGHT' in buttons),
                               ('Up',y < -.4 or 'UP' in buttons or 'DPAD_UP' in buttons),
                               ('Down',y > .4 or 'DOWN' in buttons or 'DPAD_DOWN' in buttons)):
            if condition: held.add(key)
        if {'Left','Right'} <= held: held.difference_update(('Left','Right'))
        if {'Up','Down'} <= held: held.difference_update(('Up','Down'))
        mapped = dict(A='Return', B='Escape', X='space', Y='Escape', LB='Page_Up', RB='Page_Down')
        pressed = (buttons-self.previous) | raw_pressed
        pulses = {mapped[b] for b in pressed if b in mapped}
        self.previous = buttons
        return dict(valid=True, held=held, pulses=pulses, reason='controller')
