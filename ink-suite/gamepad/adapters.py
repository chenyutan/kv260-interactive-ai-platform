"""Bounded native game inputs, independent of cameras and pose confidence.

START and the right stick belong to the host. No model samples are fabricated.
On ownership transfer or reconnect, release buttons/left stick before gameplay.
"""
import math
import os
import time


def enabled():
    return os.environ.get('INK_CONTROL_MODE', 'body').lower() == 'gamepad'


def create_gamepad(mode, *, disabled=False):
    return GamepadControls(mode) if enabled() and not disabled else None


def value(sample, key, default=None):
    return sample.get(key, default) if isinstance(sample, dict) else getattr(sample, key, default)


def axis(axes, name):
    v = value(axes, name, 0.)
    return max(-1., min(1., float(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0.


def deadzone(v, threshold=.22):
    return 0. if abs(v) <= threshold else math.copysign((abs(v)-threshold)/(1-threshold), v)


class GamepadControls:
    def __init__(self, mode, reader=None):
        if reader is None:
            from gamepad.device import GamepadReader
            reader = GamepadReader()
        self.reader, self.mode = reader, mode
        self.x, self.y = .5, .68
        self.ink_index = 0
        self.punch_side = self.kick_side = 0
        self.connected = False
        self.closed = False
        self.reset()

    def reset(self):
        self.armed = False
        self.lane = 0
        self.last_at = None
        self.last_buttons = set()
        self.last_sequence = None

    def close(self):
        if not self.closed:
            self.closed = True
            self.reader.close()

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        out = dict(valid=False, connected=False, reason='手柄未连接', pressed=set(), buttons=set(),
                   lx=0., ly=0., rt=0., lt=0., sequence=None, timestamp=now, dt=0.)
        if self.closed:
            return out
        sample = self.reader.snapshot()
        connected = bool(value(sample, 'connected', False))
        if not connected:
            self.connected = False
            self.reset()
            return out
        buttons = set(value(sample, 'buttons', ()) or ())
        axes = value(sample, 'axes', {})
        lx, ly, rt, lt = (axis(axes, name) for name in ('lx', 'ly', 'rt', 'lt'))
        sequence = value(sample, 'sequence', None)
        stamp = value(sample, 'timestamp', now)
        # A held connected input is legal even without a newly received event.
        # Event time is used only for edges and must never judge a future hit.
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            stamp = now
        stamp = min(now, max(now-.1, stamp))
        if not self.connected:
            self.armed = False
        self.connected = True
        edges = set(value(sample, 'pressed', ()) or ())
        edges |= buttons-self.last_buttons
        if sequence is not None and sequence == self.last_sequence:
            edges.clear()
        self.last_sequence = sequence
        self.last_buttons = buttons
        dt = min(.05, max(0., now-self.last_at)) if self.last_at is not None else 0.
        self.last_at = now
        if not self.armed:
            neutral = not buttons and abs(lx)<.25 and abs(ly)<.25 and rt<.2 and lt<.2
            if neutral:
                self.armed = True
            else:
                out.update(connected=True, reason='手柄已连接 · 松开按键和左摇杆')
                return out
            edges.clear()
        directions=buttons|edges
        if 'LEFT' in directions: lx = -1.
        if 'RIGHT' in directions: lx = 1.
        if 'UP' in directions: ly = -1.
        if 'DOWN' in directions: ly = 1.
        out.update(valid=True, connected=True, reason='gamepad', pressed=edges-{'START','BACK','GUIDE','R3'},
                   buttons=buttons, lx=deadzone(lx), ly=deadzone(ly), rt=rt, lt=lt,
                   sequence=sequence, timestamp=stamp, dt=dt)
        return out

    def runner(self, now=None):
        s = self.sample(now)
        move = 0
        direction = -1 if s['lx']<-.4 else 1 if s['lx']>.4 else 0
        if not direction:
            self.lane = 0
        elif s['valid'] and direction != self.lane:
            move, self.lane = direction, direction
        return dict(s, move=move, jump='A' in s['pressed'],
                    slide='B' in s['pressed'] or 'DOWN' in s['pressed'],
                    confirm=bool(s['pressed'] & {'A','X'}))

    def platformer(self, now=None):
        s = self.sample(now)
        return dict(s, move=s['lx'], jump='A' in s['buttons'] and s['valid'],
                    confirm=bool(s['pressed'] & {'X','B','DOWN'}), calibration='ready',
                    calibration_progress=1., jump_ready='A' not in s['buttons'])

    def sword(self, now=None):
        s = self.sample(now)
        if s['valid']:
            self.x = max(0., min(1., self.x+s['lx']*s['dt']*.95))
            self.y = max(0., min(1., self.y+s['ly']*s['dt']*.95))
        return dict(s, x=self.x, y=self.y, status='tracking' if s['valid'] else 'lost',
                    special='X' in s['pressed'] or 'RB' in s['pressed'],
                    confirm=bool(s['pressed'] & {'A','Y'}), source_key=('gamepad',s['sequence']))

    def taiko(self, now=None):
        s = self.sample(now)
        events = []
        if s['valid']:
            for button, hands in (('X','L'),('B','R'),('Y','C'),('A','LR')):
                if button in s['pressed']:
                    for hand in hands:
                        events.append(dict(hand=hand, timestamp=s['timestamp'],
                            id=('gamepad', s['sequence'], button, hand)))
        return dict(s, events=events, hands={}, status='X左鼓 · B右鼓 · Y拍掌 · A双鼓')

    def fight(self, now=None):
        s = self.sample(now)
        moves = []
        if s['valid']:
            if 'X' in s['pressed']:
                moves.append(('left','right')[self.punch_side]); self.punch_side ^= 1
            if 'B' in s['pressed']:
                moves.append(('kick_left','kick_right')[self.kick_side]); self.kick_side ^= 1
            if 'Y' in s['pressed']: moves.append('palm')
            if 'RB' in s['pressed']: moves.append('special')
        return dict(s, moves=moves, move_axis=s['lx'], crouch=s['ly']>.45,
                    guard='LB' in s['buttons'] or s['ly']>.45,
                    jump='A' in s['pressed'] or 'UP' in s['pressed'],
                    confirm=bool(s['pressed'] & {'A','X'}))

    def fruit(self, now=None):
        s = self.sample(now)
        if s['valid']:
            self.x = max(0., min(1., self.x+s['lx']*s['dt']*1.65))
            self.y = max(0., min(1., self.y+s['ly']*s['dt']*1.65))
        return dict(s, x=self.x, y=self.y, slicing=s['valid'] and (s['rt']>.25 or 'A' in s['buttons']),
                    confirm=bool(s['pressed'] & {'X','Y'}))

    def ink(self, now=None):
        s = self.sample(now)
        if s['valid']:
            if 'LB' in s['pressed']: self.ink_index = (self.ink_index-1)%6
            if 'RB' in s['pressed']: self.ink_index = (self.ink_index+1)%6
        return dict(s, action='A%02d'%(self.ink_index+1), draw='A' in s['pressed'],
                    speed=max(.08, min(1., max(math.hypot(s['lx'], s['ly']), s['rt']))))
