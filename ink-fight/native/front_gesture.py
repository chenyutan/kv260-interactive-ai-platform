"""Short-window, 2-D evidence for punches/palms directed toward the camera.

This does not reconstruct depth, read fingers, or classify an open palm. A
coincident pair of front thrusts is the game's palm command. Candidate hands
are exposed so the caller can defer conflicting legacy uppercut commands.
Coordinates use the existing recognizer's shoulder-width normalized image
plane. A visible elbow/forearm projection change and a wrist motion are both
required. Missing observations are never interpolated.
"""
import math
from collections import deque

# Limit is measured between reliable observations, including camera cadence.
# A current real observation is required before any preserved candidate fires.
MISSING_GRACE=.20


class FrontGesture:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last = None
        self.history = {hand: deque() for hand in ('left', 'right')}
        self.pending = {}
        self.locked = {}
        self.last_seen = {}
        self.blocking_hands = set()
        self.guard_hands = set()
        self.trusted_shoulders = {}
        self.supported_shoulders = set()

    def _clear_hand(self, hand):
        self.history[hand].clear()
        self.pending.pop(hand, None)
        self.locked.pop(hand, None)
        self.last_seen.pop(hand, None)

    def _missing(self, hand, t):
        if hand not in self.last_seen or t-self.last_seen[hand] > MISSING_GRACE+1e-9:
            self._clear_hand(hand)

    @staticmethod
    def _point(points, index, threshold=.20):
        if index >= len(points):
            return None
        p = points[index]
        if (not isinstance(p, (list, tuple)) or len(p) < 3
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p[:3])
                or not 0 <= p[0] <= 1 or not 0 <= p[1] <= 1 or p[2] < threshold):
            return None
        return p

    def _shoulders(self, points, t):
        """Use an observed weak shoulder only with recent measured support.

        These are the current coordinates, not a held or extrapolated point.
        Weak observations never refresh the trusted time/position/width.
        """
        self.supported_shoulders = set()
        raw = {i: self._point(points, i, .12) for i in (5, 6)}
        strong = {i: self._point(points, i) for i in (5, 6)}
        if all(strong.values()):
            a, b = strong[5], strong[6]
            width = math.hypot(a[0]-b[0], (a[1]-b[1])*9./16.)
            if width >= .045:
                for i in (5, 6):
                    self.trusted_shoulders[i] = dict(t=t, point=tuple(strong[i]), width=width)
            return strong[5], strong[6]
        if not all(raw.values()):
            return strong[5], strong[6]
        a, b = raw[5], raw[6]
        width = math.hypot(a[0]-b[0], (a[1]-b[1])*9./16.)
        for i, other, elbow, wrist in ((5,6,7,9),(6,5,8,10)):
            p, anchor = raw[i], self.trusted_shoulders.get(i)
            if strong[i] is not None or p[2] <= .12 or strong[other] is None or not anchor:
                continue
            if not (0 <= t-anchor['t'] <= .30+1e-9
                    and self._point(points, elbow) is not None
                    and self._point(points, wrist) is not None
                    and .75 <= width/anchor['width'] <= 1.30):
                continue
            old = anchor['point']
            movement = math.hypot(p[0]-old[0], (p[1]-old[1])*9./16.)
            if movement <= .30*anchor['width']:
                strong[i] = p
                self.supported_shoulders.add(i)
        return strong[5], strong[6]

    @staticmethod
    def _zone(m):
        return abs(m['x']) < .95 and -.68 < m['y'] < .96 and .05 < m['ey'] < 1.4

    @staticmethod
    def _compact(m, a):
        # Elbow projection alone can flicker while the fist is stationary.
        # Require measured wrist travel beyond small landmark noise as well;
        # two frames of an elbow-only collapse are not a punch.
        return (-.65 < a['y'] < 1.65 and a['upper'] > .72 and a['ey'] > .5
                and math.hypot(m['x']-a['x'], (m['y']-a['y'])*9./16.) > .08
                and m['upper'] < min(1.0, .80*a['upper'])
                and a['upper']-m['upper'] > .24
                and a['ey']-m['ey'] > .15 and a['fore']-m['fore'] > .25)

    @staticmethod
    def _evidence(m, a):
        if not (-.65 < a['y'] < 1.65 and a['upper'] > .72 and a['ey'] > .5):
            return False
        rising_wrist = a['y'] - m['y'] > .25
        # A bent chamber can move as one piece while the opposite hand strikes.
        # Require a substantial upper-arm collapse, changed forearm shape, or
        # lateral wrist travel rather than a small whole-arm adjustment.
        shortened = (m['upper'] < min(.98, a['upper'] * .74) and a['ey'] - m['ey'] > .16
                     and (a['upper']-m['upper'] > .45
                          or abs(m['fore']-a['fore']) > .20 or abs(m['x']-a['x']) > .20))
        forearm_change = (m['y'] < .2 and m['fore'] - a['fore'] > .45
                          and m['upper'] < 1.05 and m['ey'] < .95
                          and a['ey'] - m['ey'] > .16)
        # A high guard may thrust forward/down: elbow goes up while the wrist
        # goes down, and both arm segments shorten in the image plane.
        downward_thrust = (-.65 < a['y'] < .25 and m['y'] - a['y'] > .25
                           and m['upper'] < min(.90, a['upper']*.70)
                           and a['upper'] - m['upper'] > .33
                           and a['ey'] - m['ey'] > .20
                           and a['fore'] - m['fore'] > .45)
        return ((rising_wrist and (shortened or forearm_change)) or downward_thrust
                or FrontGesture._compact(m, a))

    def _fire(self, hands, t):
        for hand in hands:
            p = self.pending.pop(hand)
            self.locked[hand] = dict(anchor=p['anchor'], peak_y=p['peak_y'],
                                     since=t, recovered=None)

    def consume(self, state):
        if not isinstance(state, dict) or state.get('stale') or state.get('available') is False:
            self.reset()
            return []
        t = state.get('timestamp_seconds')
        points = state.get('points17')
        if (not isinstance(t, (int, float)) or not math.isfinite(t)
                or not isinstance(points, (list, tuple))):
            self.reset()
            return []
        key = (state.get('pid'), state.get('sequence'), t)
        if key == self.last:
            return []
        if self.last and (key[0] != self.last[0] or not 0 < t - self.last[2] <= .20):
            self.reset()
        self.last = key
        ls, rs = self._shoulders(points, t)
        if ls is None or rs is None:
            for hand in self.history:
                self._missing(hand, t)
            self.blocking_hands = set(self.pending) | set(self.locked)
            return []
        width = math.hypot(ls[0] - rs[0], ls[1] - rs[1])
        if width < .045:
            for hand in self.history:
                self._clear_hand(hand)
            self.blocking_hands = set()
            return []
        current = {}
        for hand, si, ei, wi in (('left', 5, 7, 9), ('right', 6, 8, 10)):
            sh = ls if si == 5 else rs
            el, wr = self._point(points, ei), self._point(points, wi)
            if el is None or wr is None:
                self._missing(hand, t)
                continue
            if hand in self.last_seen and t-self.last_seen[hand] > MISSING_GRACE+1e-9:
                self._clear_hand(hand)
            self.last_seen[hand] = t
            x, y = (wr[0] - sh[0]) / width, (wr[1] - sh[1]) / width
            ex, ey = (el[0] - sh[0]) / width, (el[1] - sh[1]) / width
            m = dict(t=t, x=x, y=y, ex=ex, ey=ey, width=width,
                     upper=math.hypot(ex, ey), fore=math.hypot(x-ex, y-ey))
            history = self.history[hand]
            while history and t - history[0]['t'] > .70:
                history.popleft()
            previous = history[-1] if history else None
            if previous and (not .65 < width / previous['width'] < 1.55
                             or math.hypot(x-previous['x'], y-previous['y']) > 1.7):
                self._clear_hand(hand)
                history = self.history[hand]
                previous = None
            current[hand] = m
            lock = self.locked.get(hand)
            if lock:
                a = lock['anchor']
                low_guard = m['y'] > max(.45, lock['peak_y']+.40)
                # Forward/down from a high guard returns upward to its anchor.
                # The old low-guard-only check could keep this stroke locked
                # forever even after a complete observed return. Require both
                # a reversal away from the strike and proximity to that guard;
                # simply holding the extended fist cannot rearm it.
                high_guard_return = (a['y'] < .25 and lock['peak_y'] > a['y']+.25
                                     and m['y'] < min(a['y']+.18, lock['peak_y']-.25)
                                     and abs(m['x']-a['x']) < .35
                                     and m['ey'] > a['ey']-.18)
                # A noisy chamber can briefly overestimate the upper arm.
                # Cap its recovery threshold, then require a second real
                # observation rather than a deliberate 160 ms guard hold.
                recovered = m['upper'] > min(.78*a['upper'], .95) and (low_guard or high_guard_return)
                if recovered:
                    if lock['recovered'] is None:
                        lock['recovered'] = t
                    elif t-lock['recovered'] >= .045:
                        self._clear_hand(hand)
                        history = self.history[hand]
                else:
                    lock['recovered'] = None
                history.append(m)
                continue
            p = self.pending.get(hand)
            if p and p.get('completed'):
                if t-p['start'] > .46:
                    self.pending.pop(hand, None)
            elif p:
                a = p['anchor']
                p['frames'] += 1
                old_peak = p['peak_y']
                p['peak_y'] = min(old_peak, m['y'])
                if m['upper'] < p['peak_marker_upper']-.03:
                    p['peak_time'] = t
                    p['peak_marker_upper'] = m['upper']
                p['peak_upper'] = min(p['peak_upper'], m['upper'])
                p['peak_dx'] = max(p['peak_dx'], abs(m['x']-a['x']))
                anchors = [a] + [v for v in history if v['t'] <= p['start']]
                supported = any(self._evidence(m, v) for v in anchors)
                if any(self._compact(m, v) for v in anchors):
                    p['compact'] += 1
                if supported:
                    p['strong'] += 1
                # A fast measured out-and-back stroke can finish during the
                # pair window. Preserve it only with repeated thrust evidence
                # and substantial wrist travel; a one-frame elbow collapse or
                # small chamber adjustment must still cancel on return.
                returned = (m['upper'] > min(.86*a['upper'], p['peak_upper']+.30)
                            and m['y'] > old_peak+.08)
                completed = (returned and p['strong'] >= 2
                             and (t-p['start'] >= .06 or (p['compact'] >= 2 and t-a['t'] >= .10))
                             and (p['compact'] >= 2 or abs(a['y']-p['peak_y']) > .65
                                  or (p['peak_dx'] > .20 and a['y']-p['peak_y'] > .25
                                      and p['peak_upper'] < .74*a['upper'])))
                if completed:
                    p['completed'] = True
                    if p['ready'] is None:
                        p['ready'] = t
                elif not self._zone(m) or t-p['start'] > .46 or returned:
                    self.pending.pop(hand, None)
                else:
                    vy = (m['y']-previous['y'])/(t-previous['t']) if previous else -100.
                    rebound = m['y']-p['peak_y'] > .08
                    stopped_rising = p['strong'] >= 2 and vy > -1.2
                    if p['frames'] >= 2 and t-p['start'] >= .08 and (rebound or stopped_rising):
                        if p['ready'] is None:
                            p['ready'] = t
            elif previous and self._zone(m):
                anchors = [a for a in history if .045 <= t-a['t'] <= .65 and self._evidence(m, a)]
                if anchors:
                    a = max(anchors, key=lambda v: v['upper']-m['upper'])
                    self.pending[hand] = dict(anchor=a, start=t, peak_y=m['y'],
                                               frames=1, strong=1, ready=None, peak_upper=m['upper'], peak_time=t, peak_marker_upper=m['upper'],
                                               peak_dx=abs(m['x']-a['x']),
                                               compact=int(self._compact(m,a)))
            # Leaving the front-thrust zone upward starts a different arc.
            # Do not reuse its pre-raise chamber as a fresh punch while the
            # hand lowers back through this zone. Keep the current real frame.
            if m['y'] <= -.68 or m['ey'] <= .05:
                history.clear()
            history.append(m)
        # A coordinated face guard has wrists above bent, downward elbows.
        # Forearm lengthening alone also occurs here; it is not forward thrust.
        # Keep the measured history for a subsequent punch from this guard,
        # while exposing both hands to suppress conflicting legacy uppercuts.
        guard = (len(current) == 2 and all(
            m['y'] < .20 and m['ey'] > .50 and m['fore'] > .90
            and m['ey']-m['y'] > .65 for m in current.values()))
        self.guard_hands = set(current) if guard else set()
        if guard:
            for hand in self.guard_hands:
                self.pending.pop(hand, None)
                lock = self.locked.get(hand)
                if lock and current[hand]['upper'] > min(.78*lock['anchor']['upper'], .95):
                    if lock.get('guard_return') is None:
                        lock['guard_return'] = t
                    elif t-lock['guard_return'] >= .045:
                        self._clear_hand(hand)
                        self.history[hand].append(current[hand])
        else:
            for lock in self.locked.values():
                lock.pop('guard_return',None)
        out = []
        left, right = self.pending.get('left'), self.pending.get('right')
        # Pending windows may overlap for alternating punches. A palm needs
        # actual same-frame extension from both hands near coincident peaks.
        # Remember that observation so a fast simultaneous return still counts.
        both_observed = left and right and 'left' in current and 'right' in current
        if (both_observed and abs(left['peak_time']-right['peak_time']) <= .12
                and all(p['strong'] >= 2 and current[hand]['upper'] <= min(1.05,p['peak_upper']+.25)
                        for hand,p in (('left',left),('right',right)))):
            left['simultaneous_with'] = right['start']
            right['simultaneous_with'] = left['start']
        if (both_observed and abs(left['start']-right['start']) <= .18
                and abs(left['peak_time']-right['peak_time']) <= .12
                and left.get('simultaneous_with') == right['start']
                and right.get('simultaneous_with') == left['start']
                and left['ready'] is not None and right['ready'] is not None):
            self._fire(('left', 'right'), t)
            out = ['palm']
        else:
            # Emit separate strokes in observed extension order. A later
            # candidate must not jump ahead during the earlier pairing window.
            ready = sorted(((hand,p) for hand,p in self.pending.items()
                            if hand in current and p['ready'] is not None),
                           key=lambda item: item[1]['peak_time'])
            for hand,p in ready:
                # Pairing time runs from observed onset, not from the peak;
                # fast retracting fists must not wait through a second delay.
                if t-p['start'] >= .16:
                    self._fire((hand,), t)
                    out = [hand]
                break
        self.blocking_hands = set(self.pending) | set(self.locked) | self.guard_hands
        return out
