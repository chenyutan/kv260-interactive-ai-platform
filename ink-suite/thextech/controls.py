"""Pure TheXTech action adapters; no input devices or sockets are opened.

Call ``BodyKeys.update(pose, monotonic_now, menu=False)`` or
``PadKeys.update(reader.snapshot(), monotonic_now, menu=False)`` each host tick.
Results contain ``valid``, ``reason``, ``held`` (the complete current bridge
state), ``pulses`` (new events this call only), and its 12-bit ``mask``.

The bridge consumes held state, including brief timed holds for body events.
It must not turn every name into a fresh press each tick. START belongs to the
host, so these adapters never emit engine Start. Set menu=True only while the
engine is actually in a menu/message; gameplay jumps then become Confirm.
"""
import math

from gamepad.adapters import value
from platformer.controls import PoseControls


BUTTONS = ('Up', 'Down', 'Left', 'Right', 'Jump', 'AltJump', 'Run',
           'AltRun', 'Drop', 'Start', 'Confirm', 'Back')
BITS = {name: 1 << index for index, name in enumerate(BUTTONS)}
BODY_HINT = '右手左右走、上下开门入管 · 左手抬过肩跳 · 移动时自动跑 · 转两圈打开菜单'
PAD_HINT = '左摇杆/方向键移动 · A跳 · X跑/发射 · B特殊跳 · Menu打开菜单'


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def mask_for(held):
    """Encode only known semantic actions; unknown names are programming errors."""
    result = 0
    for name in held:
        result |= BITS[name]
    return result


def result(valid, reason, held=(), pulses=(), **fields):
    held = set(held) if valid else set()
    pulses = set(pulses) if valid else set()
    return dict(fields, valid=bool(valid), reason=reason, held=held,
                pulses=pulses, mask=mask_for(held))


class _TimedEvents:
    def __init__(self):
        self.deadlines = {}

    def clear(self):
        self.deadlines.clear()

    def add(self, names, now, duration):
        for name in names:
            self.deadlines[name] = now + duration

    def held(self, now):
        self.deadlines = {name: end for name, end in self.deadlines.items() if now < end}
        return set(self.deadlines)


class BodyKeys:
    """Reuse the existing calibrated arm joystick and left-arm jump detector.

    Vertical input begins after two fresh observations and 45 ms beyond 0.30
    shoulder widths, releases at 0.17, and cannot coexist with horizontal input.
    Timed jump/confirm holds default to 140 ms. Holding the arm up cannot repeat
    them. Auto-run is an explicit body accessibility aid and can be disabled.
    """
    def __init__(self, *, tracker=None, auto_run=True, jump_hold=.14):
        if not finite(jump_hold) or not .06 <= jump_hold <= .30:
            raise ValueError('jump_hold must be between .06 and .30 seconds')
        self.tracker = PoseControls() if tracker is None else tracker
        self.auto_run = bool(auto_run)
        self.jump_hold = float(jump_hold)
        self.events = _TimedEvents()
        self.last_now = None
        self.last_source = None
        self.menu = None
        self._clear_vertical()

    def _clear_vertical(self):
        self.vertical = self.vertical_candidate = 0
        self.vertical_since = None
        self.vertical_samples = 0

    def reset(self):
        """Release all bridge actions; retain the proven personal body origin."""
        self.tracker.reset()
        self.events.clear()
        self._clear_vertical()
        self.last_now = None
        # Preserve last_source: resuming cannot reuse an already seen pose.

    def _vertical(self, dx, dy, stamp, fresh):
        if (not finite(dx) or not finite(dy) or abs(dx) > .40
                or not -.90 <= dy <= .70):
            self._clear_vertical()
            return 0
        wanted = 0
        if self.vertical and dy * self.vertical >= .17:
            wanted = self.vertical
        elif abs(dx) <= .28 and abs(dy) >= .30:
            wanted = 1 if dy > 0 else -1
        if not wanted:
            self._clear_vertical()
            return 0
        if wanted == self.vertical:
            return self.vertical
        # A reversal always releases the old action before debounce finishes.
        if not fresh:
            return 0
        if wanted != self.vertical_candidate:
            self.vertical = 0
            self.vertical_candidate = wanted
            self.vertical_since = stamp
            self.vertical_samples = 1
        else:
            self.vertical_samples += 1
            if self.vertical_samples >= 2 and stamp - self.vertical_since >= .045 - 1e-8:
                self.vertical = wanted
        return self.vertical

    def update(self, packet, now, *, menu=False):
        menu = bool(menu)
        if self.menu is not None and self.menu != menu:
            self.reset()
        self.menu = menu
        if not finite(now) or (self.last_now is not None and now < self.last_now):
            self.reset()
            return result(False, 'invalid_clock')
        self.last_now = now
        control = self.tracker.update(packet, now, confirm_context=False)
        fields = {name: control.get(name) for name in
                  ('calibration', 'calibration_progress', 'joystick_dx',
                   'joystick_dy', 'jump_ready')}
        source = (self.tracker.identity, self.tracker.sequence, self.tracker.stamp)
        fresh = source != self.last_source
        if fresh:
            self.last_source = source
        if not control.get('valid'):
            self.events.clear()
            self._clear_vertical()
            return result(False, control.get('reason', 'missing_pose'), **fields)
        if control.get('reason') in ('body_changed', 'body_frozen'):
            self.events.clear()
        held = set()
        # PoseControls itself enforces missing arms, source age, geometry, center
        # return and horizontal stop. Never reconstruct movement from raw wrists.
        usable_right = (control.get('calibration') == 'ready'
                        and control.get('reason') not in
                        ('body_changed', 'body_frozen', 'joystick_return_center'))
        vertical = self._vertical(control.get('joystick_dx'), control.get('joystick_dy'),
                                  self.tracker.stamp, fresh) if usable_right else 0
        if not usable_right:
            self._clear_vertical()
        if vertical:
            held.add('Down' if vertical > 0 else 'Up')
        elif control.get('move'):
            held.add('Right' if control['move'] > 0 else 'Left')
            if self.auto_run and not menu:
                held.add('Run')
        pulses = set()
        if fresh and control.get('jump'):
            pulses.add('Confirm' if menu else 'Jump')
            self.events.add(pulses, now, self.jump_hold)
        # A missing left arm releases its timed input immediately, even though a
        # still-visible right arm can legitimately continue steering.
        if control.get('reason') == 'missing_left_arm':
            self.events.clear()
        held.update(self.events.held(now))
        return result(True, control.get('reason', 'tracking'), held, pulses, **fields)


class PadKeys:
    """Consume an injected GamepadReader snapshot, without opening hardware.

    Event timestamps may be old while a real connected stick is held; they do
    not impose a false timeout. If supplied, sampled_at is the transport liveness
    timestamp. New attachment/resume requires neutral controls, as elsewhere in
    this suite. Menu/Start, right stick and R3 remain entirely owned by the host.
    """
    def __init__(self):
        self.events = _TimedEvents()
        self.menu = None
        self.last_now = None
        self.last_key = None
        self.last_sequence = None
        self.reset()

    def reset(self):
        self.armed = False
        self.previous = set()
        self.events.clear()
        self.last_now = None

    def _loss(self, reason):
        self.reset()
        return result(False, reason)

    def update(self, packet, now, *, menu=False):
        menu = bool(menu)
        if self.menu is not None and self.menu != menu:
            self.reset()
        self.menu = menu
        if not finite(now) or (self.last_now is not None and now < self.last_now):
            return self._loss('invalid_clock')
        self.last_now = now
        if not value(packet, 'connected', False):
            return self._loss('controller_disconnected')
        stamp = value(packet, 'timestamp', now)
        sampled_at = value(packet, 'sampled_at', now)
        if (not finite(stamp) or stamp > now + .05 or not finite(sampled_at)
                or not -.05 <= now - sampled_at <= .50):
            return self._loss('controller_expired')
        sequence = value(packet, 'sequence')
        if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0):
            return self._loss('invalid_controller_sequence')
        if (sequence is not None and self.last_sequence is not None
                and sequence < self.last_sequence):
            self.last_sequence = sequence
            return self._loss('controller_stream_reset')
        axes = value(packet, 'axes', {}) or {}
        x, y, rt, lt = (value(axes, name, 0.) for name in ('lx', 'ly', 'rt', 'lt'))
        if (not all(finite(v) for v in (x, y, rt, lt)) or abs(x) > 1 or abs(y) > 1
                or not 0 <= rt <= 1 or not 0 <= lt <= 1):
            return self._loss('invalid_controller_axes')
        raw_buttons = value(packet, 'buttons', ()) or ()
        raw_pressed = value(packet, 'pressed', ()) or ()
        if (not isinstance(raw_buttons, (set, frozenset, list, tuple))
                or not isinstance(raw_pressed, (set, frozenset, list, tuple))
                or not all(isinstance(b, str) for b in (*raw_buttons, *raw_pressed))):
            return self._loss('invalid_controller_buttons')
        buttons = set(raw_buttons)
        key = (sequence, stamp)
        fresh = key != self.last_key
        edges = buttons - self.previous
        if fresh:
            edges.update(raw_pressed)
        self.last_key, self.last_sequence = key, sequence
        self.previous = buttons
        if not self.armed:
            if buttons or raw_pressed or abs(x) >= .25 or abs(y) >= .25 or rt >= .2 or lt >= .2:
                return result(False, 'controller_release_required')
            self.armed = True
            edges.clear()
        held = set()
        for name, condition in (
                ('Left', x < -.4 or bool(buttons & {'LEFT', 'DPAD_LEFT'})),
                ('Right', x > .4 or bool(buttons & {'RIGHT', 'DPAD_RIGHT'})),
                ('Up', y < -.4 or bool(buttons & {'UP', 'DPAD_UP'})),
                ('Down', y > .4 or bool(buttons & {'DOWN', 'DPAD_DOWN'}))):
            if condition:
                held.add(name)
        for pair in ({'Left', 'Right'}, {'Up', 'Down'}):
            if pair <= held:
                held.difference_update(pair)
        mapped = {'A': 'Confirm', 'B': 'Back'} if menu else {
            'A': 'Jump', 'B': 'AltJump', 'X': 'Run', 'Y': 'AltRun',
            'BACK': 'Drop', 'RB': 'AltJump', 'LB': 'AltRun'}
        pulses = {mapped[b] for b in edges if b in mapped}
        held.update(mapped[b] for b in buttons if b in mapped)
        if not menu and rt > .25:
            held.add('Run')
        # Preserve an actual quick press/release that happened between snapshots.
        # Held buttons still retain their full variable-jump/Run semantics.
        self.events.add(pulses, now, .10)
        held.update(self.events.held(now))
        return result(True, 'controller', held, pulses)


# Convenience names for callers accustomed to the existing control modules.
BodyControls = BodyKeys
PadControls = PadKeys
