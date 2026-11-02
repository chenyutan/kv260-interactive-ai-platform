"""Input ownership independent of GTK, evdev and pointer rendering.

Sticks in reader snapshots have already passed its radial dead zone. Every
remaining stick movement owns input, even below the menu direction threshold.
Triggers have a small separate noise floor because their axes are unfiltered.
"""
import math


class InputPriority:
    NAVIGATION_BUTTONS = frozenset(('A', 'B', 'X', 'Y', 'UP', 'DOWN',
        'LEFT', 'RIGHT', 'START', 'GUIDE', 'BACK', 'LB', 'RB', 'LT', 'RT', 'L3'))

    def __init__(self, grace=3.0, trigger_deadzone=.05):
        self.grace = max(0.0, float(grace))
        self.trigger_deadzone = max(0.0, float(trigger_deadzone))
        self.connected = False
        self.until = 0.0
        self.navigation_until = 0.0
        self.focus_owner = None
        self._pointer_back_combo = False

    @staticmethod
    def _axis(axes, name):
        try:
            value = float(axes.get(name, 0.0))
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    def update(self, snapshot, now):
        self.connected = bool(snapshot.get('connected'))
        if not self.connected:
            self.until = self.navigation_until = 0.0
            self.focus_owner = None
            self._pointer_back_combo = False
            return
        held = set(snapshot.get('buttons', ()))
        controls = held | set(snapshot.get('pressed', ())) | set(snapshot.get('released', ()))
        axes = snapshot.get('axes', {})
        left = max(abs(self._axis(axes, name)) for name in ('lx', 'ly'))
        right = max(abs(self._axis(axes, name)) for name in ('rx', 'ry'))
        trigger = max(self._axis(axes, name) for name in ('lt', 'rt'))
        if controls or left > 0.0 or right > 0.0 or trigger > self.trigger_deadzone:
            self.until = now + self.grace

        # BACK+R3 is a pointer chord, including its final BACK release.
        if 'BACK' in controls and 'R3' in controls:
            self._pointer_back_combo = True
        pointer_combo = self._pointer_back_combo and 'BACK' in controls
        navigation_buttons = controls & self.NAVIGATION_BUTTONS
        if pointer_combo:
            navigation_buttons.discard('BACK')
        navigation = bool(navigation_buttons) or left > .5
        pointer = right > 0.0 or 'R3' in controls or pointer_combo
        if navigation:
            self.navigation_until = now + self.grace
            self.focus_owner = 'navigation'
        elif pointer:
            # A mouse takeover cancels an older keyboard-focus lease, also in
            # explicit gamepad mode. Only a new navigation input restores it.
            self.navigation_until = 0.0
            self.focus_owner = 'pointer'
        if 'BACK' not in held:
            self._pointer_back_combo = False

    def gestures_blocked(self, now, mode='body'):
        return mode == 'gamepad' or (self.connected and now < self.until)

    def navigation_active(self, now, mode='body'):
        if not self.connected or self.focus_owner == 'pointer':
            return False
        return mode == 'gamepad' or now < self.navigation_until
