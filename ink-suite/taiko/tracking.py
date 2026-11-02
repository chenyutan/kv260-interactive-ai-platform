"""Music continuity is independent of whether a wrist can judge a strike.

Only timestamps of fresh, measured poses extend the grace periods. A cached
frame, missing wrist or menu candidate never generates an input event here.
"""
import math
from collections import deque
from taiko.input import DrumInput
from game_source_policy import SourceGapPolicy

_NO_SOURCE=object()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class TrackingGate:
    def __init__(self, source_grace=.65, body_grace=1., recovery=.18, observation_age=.45):
        self.source_grace, self.body_grace, self.recovery = max(2.5,source_grace), body_grace, recovery
        self.observation_age = observation_age
        self.source_gap=SourceGapPolicy(timeout=self.source_grace)
        self.reset()

    def reset(self):
        self.source_gap.reset()
        self.last_source = self.last_body = self.recovery_since = None
        self.recovery_body = None
        self.holding = True
        self.reason = 'waiting_body'

    def update(self, control, now, *, confirmed_menu=False, candidate=False,
               entering=False, offline=False, source_state=_NO_SOURCE):
        if confirmed_menu or entering:
            self.reset()
            self.reason = 'menu' if confirmed_menu else 'entry'
            return 'pause', self.reason
        if not finite(now):
            self.reset()
            self.reason = 'invalid_time'
            return 'pause', self.reason
        source = control.get('source_stamp')
        body = control.get('body_stamp')
        if control.get('fresh') and control.get('body_present') and finite(body) and -.05 <= now-body <= self.observation_age:
            self.last_body = body
        # Production uses raw stream metadata, independent of wrist/torso
        # quality. The fallback keeps pure legacy control fixtures usable.
        if source_state is _NO_SOURCE:
            source_state=(dict(pid=1,sequence=round(source*1000000),timestamp_seconds=source)
                if control.get('fresh') and control.get('source_fresh') and finite(source) else None)
        blocked=self.source_gap.update(source_state,now,offline=offline)
        self.last_source=self.source_gap.last_source
        if blocked:
            self.holding = True
            self.recovery_since = self.recovery_body = None
            self.reason = 'source_timeout'
            return 'pause', self.reason
        if self.holding:
            if not offline and (self.last_source is None or now-self.last_source>min(.45,self.observation_age)):
                self.reason='waiting_source'
                return 'hold',self.reason
            # Entry/menu timing belongs to the host. A body miss adds neither
            # a seek nor another countdown. Input detectors remain independent.
            self.holding = False
            self.recovery_since = self.recovery_body = None
            self.reason = 'playing'
            return 'resume', self.reason
        self.reason = ('menu_candidate' if candidate else
                       'wrist_unavailable' if not control.get('valid') else 'playing')
        return 'play', self.reason

    def snapshot(self, now):
        return dict(reason=self.reason, holding=self.holding,
                    source_age=now-self.last_source if self.last_source is not None else None,
                    body_age=now-self.last_body if self.last_body is not None else None,
                    source_grace=self.source_grace, body_grace=self.body_grace,
                    recovery=self.recovery, observation_age=self.observation_age)


class MusicObservation:
    """Measured stream progress for music, never a source of strike events.

    A slightly late but advancing pose may keep an already playing song alive.
    Cached/retimestamped poses never extend the lease. Original capture stamps
    remain unchanged; startup/recovery in TrackingGate still needs <=450ms
    body observations. High-water marks survive host/menu lifecycle resets.
    """
    MAX_AGE = .60

    def __init__(self):
        self.identity = self.stamp = self.sequence = None
        self.accepted_key = None
        self.retired_pids = deque(maxlen=8)
        self.reset()

    def reset(self):
        self.body_anchors = self.anchor_stamp = None
        self.control = dict(fresh=False, source_fresh=False, source_stamp=None,
                            body_present=False, body_stamp=None, valid=False,
                            reason='waiting_source')

    @staticmethod
    def _anchors(points):
        if not isinstance(points, (list, tuple)) or len(points) != 17:
            return None
        anchors = []
        for index in (5, 6, 11, 12):
            p = points[index]
            if (not isinstance(p, (list, tuple)) or len(p) < 3 or
                    not all(finite(v) for v in p[:3]) or p[2] < .25 or
                    not (0 <= p[0] <= 1 and 0 <= p[1] <= 1)):
                return None
            anchors.append((p[0] * 16 / 9, p[1]))
        width = math.dist(anchors[0], anchors[1])
        return (anchors, width) if .055 <= width <= 1.0 else None

    def _continuous_roi(self, state, selection, identity, stamp, anchors):
        # A known crop fallback may reset wrist motion while the same measured
        # torso keeps music alive. This never relaxes DrumInput's event reset.
        continuity = selection.get('continuity') if isinstance(selection, dict) else None
        if (state.get('motion_history_discontinuity') or identity != self.identity or
                not isinstance(continuity, dict) or not self.body_anchors or not anchors or
                self.anchor_stamp is None or not 0 < stamp-self.anchor_stamp <= .24 or
                selection.get('selector_version') != 'hourglass-causal-roi-continuity-v2-partial-recovery' or
                selection.get('reason') != 'other_roi_required' or
                selection.get('stream_reset') is not False or
                continuity.get('stream_reset') is not False or
                continuity.get('roi_changed') is not True or
                continuity.get('reliable') is not True):
            return False
        disagreement = continuity.get('disagreement_shoulders')
        if not finite(disagreement) or not 0 <= disagreement <= .20:
            return False
        old, old_width = self.body_anchors
        current, width = anchors
        if not .70 <= width/old_width <= 1.40:
            return False
        # Every shoulder/hip must remain near its own measured predecessor.
        return all(math.dist(a, b) <= min(width, old_width)*.60
                   for a, b in zip(old, current))

    def consume(self, state, now, presence):
        out = dict(fresh=False, source_fresh=False, source_stamp=None,
                   body_present=False, body_stamp=None, valid=False, reason='invalid_source')
        if not finite(now) or not isinstance(state, dict):
            self.body_anchors = self.anchor_stamp = None
            self.control = out
            return out
        identity = DrumInput._identity(state)
        stamp, sequence = state.get('timestamp_seconds'), state.get('sequence')
        if (not finite(stamp) or not isinstance(sequence, int) or isinstance(sequence, bool)
                or not isinstance(identity[0], int) or isinstance(identity[0], bool)
                or identity[0] <= 0 or identity[0] in self.retired_pids):
            self.body_anchors = self.anchor_stamp = None
            self.control = out
            return out
        out['source_stamp'] = stamp
        age = now - stamp
        out['source_age'] = age
        if not -.05 <= age <= self.MAX_AGE or state.get('stale') or state.get('available') is False:
            self.body_anchors = self.anchor_stamp = None
            out['reason'] = 'source_expired' if not -.05 <= age <= self.MAX_AGE else 'invalid_source'
            # Remember newer failed observations without renewing any lease.
            # A later HTTP response carrying an intermediate old frame cannot
            # revive music; a far-future invalid stamp cannot poison recovery.
            if (identity == self.identity and stamp <= now + .05 and
                    self.stamp is not None and stamp > self.stamp and sequence != self.sequence):
                self.stamp, self.sequence = stamp, sequence
                self.accepted_key = None
            elif identity == self.identity and (stamp,sequence) == (self.stamp,self.sequence):
                self.accepted_key = None
        elif self.stamp is not None and (stamp <= self.stamp or
                (identity == self.identity and sequence == self.sequence)):
            same = stamp == self.stamp and identity == self.identity and sequence == self.sequence
            out['reason'] = ('duplicate_observation' if self.accepted_key == (identity,stamp,sequence) else 'invalid_source') if same else 'out_of_order_observation'
            if not same or self.accepted_key != (identity,stamp,sequence):
                self.body_anchors = self.anchor_stamp = None
        else:
            selection = state.get('roi_selection') or {}
            changed = self.identity is not None and identity != self.identity
            discontinuity = bool(state.get('motion_history_discontinuity') or
                (isinstance(selection, dict) and selection.get('motion_history_discontinuity')))
            anchors = self._anchors(state.get('points17'))
            continuous_roi = discontinuity and self._continuous_roi(state, selection, identity, stamp, anchors)
            if changed and identity[0] != self.identity[0]:
                self.retired_pids.append(self.identity[0])
            self.identity, self.stamp, self.sequence = identity, stamp, sequence
            self.accepted_key = (identity,stamp,sequence)
            out['fresh'] = out['source_fresh'] = True
            if changed or (discontinuity and not continuous_roi):
                out['discontinuity'] = True
                out['reason'] = 'identity_discontinuity'
                self.body_anchors = self.anchor_stamp = None
            else:
                out['body_present'] = bool(presence(state.get('points17')))
                out['body_stamp'] = stamp if out['body_present'] else None
                out['reason'] = ('measured_roi_continuity' if continuous_roi else
                                 'measured_body' if out['body_present'] else 'body_missing')
                if continuous_roi:
                    out['motion_reset'] = True
                self.body_anchors = anchors if out['body_present'] else None
                self.anchor_stamp = stamp if self.body_anchors else None
        self.control = out
        return out
