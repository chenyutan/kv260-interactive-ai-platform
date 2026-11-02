"""Turn existing COCO API observations into safe, mirrored flight controls.

No camera or transport is opened here. ``now`` must be monotonic seconds.
Source timestamps are a separate clock and must strictly advance. Coordinates
are measured relative to the shoulders so stepping toward the camera does not
change the usable control range. Right wrist is COCO joint 10.
"""
import math

ASPECT = 16 / 9
HOLD_SECONDS = .240


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clip(value):
    return max(0., min(1., value))


class InputTracker:
    def __init__(self, mirrored=True, min_confidence=.20, aspect=ASPECT):
        self.mirrored = bool(mirrored)
        self.min_confidence = float(min_confidence)
        self.aspect = float(aspect)
        self.reset()

    def reset(self):
        self.x, self.y = .30, .48
        self.last_key = self.last_stamp = self.identity = None
        self.last_good = self.last_now = self.body = None
        self.body_anchors = self.anchor_stamp = None
        self._armed = self._spread = False
        self._candidate = self._candidate_since = None
        self._gesture_seen = None
        self._key_missing = False

    def _gesture_reset(self):
        self._armed = self._spread = False
        self._candidate = self._candidate_since = self._gesture_seen = None

    def _point(self, points, index):
        try:
            p = points[index]
            if len(p) < 3 or not all(_finite(v) for v in p[:3]):
                return None
            if p[2] < self.min_confidence or not all(0 <= v <= 1 for v in p[:2]):
                return None
            return float(p[0]) * self.aspect, float(p[1])
        except (TypeError, IndexError, KeyError):
            return None

    def _anchors(self, points):
        # Flight uses .20 confidence. The stricter .25 torso proof is only
        # allowed to bridge the known ROI selector's motion-history reset.
        if not isinstance(points, (list, tuple)) or len(points) != 17:
            return None
        anchors = []
        for index in (5, 6, 11, 12):
            p = points[index]
            if (not isinstance(p, (list, tuple)) or len(p) < 3 or
                    not all(_finite(v) for v in p[:3]) or p[2] < .25 or
                    not (0 <= p[0] <= 1 and 0 <= p[1] <= 1)):
                return None
            anchors.append((p[0] * self.aspect, p[1]))
        width = math.dist(anchors[0], anchors[1])
        return (anchors, width) if .055 <= width <= 1.0 else None

    def _continuous_roi(self, state, selection, identity, stamp, anchors):
        continuity = selection.get('continuity')
        sequence = state.get('sequence')
        # MusicObservation validates these source fields before its proof.
        # Keep the general flight API compatible, but require measured source
        # progress (including sequence) for this narrowly scoped exception.
        if (not isinstance(identity[0], int) or isinstance(identity[0], bool) or identity[0] <= 0 or
                not isinstance(sequence, int) or isinstance(sequence, bool) or
                self.last_key is None or not isinstance(self.last_key[1], int) or
                isinstance(self.last_key[1], bool) or sequence <= self.last_key[1] or
                state.get('motion_history_discontinuity') or identity != self.identity or
                not isinstance(continuity, dict) or not self.body_anchors or not anchors or
                self.anchor_stamp is None or not 0 < stamp - self.anchor_stamp <= .24 or
                selection.get('selector_version') != 'hourglass-causal-roi-continuity-v2-partial-recovery' or
                selection.get('reason') != 'other_roi_required' or
                selection.get('stream_reset') is not False or
                continuity.get('stream_reset') is not False or
                continuity.get('roi_changed') is not True or
                continuity.get('reliable') is not True):
            return False
        disagreement = continuity.get('disagreement_shoulders')
        if not _finite(disagreement) or not 0 <= disagreement <= .20:
            return False
        old, old_width = self.body_anchors
        current, width = anchors
        if not .70 <= width / old_width <= 1.40:
            return False
        return all(math.dist(a, b) <= min(width, old_width) * .60
                   for a, b in zip(old, current))

    def _gesture(self, points, center, width, now):
        # A neutral observation arms an edge. Arriving in a spread pose, losing
        # a wrist, changing people or replaying frames can never cast a spell.
        left, right = self._point(points, 9), self._point(points, 10)
        if left is None or right is None:
            self._gesture_reset()
            return False
        if self._gesture_seen is not None and now - self._gesture_seen > .18:
            self._gesture_reset()
        self._gesture_seen = now
        expanded = (abs(left[0] - right[0]) >= 2.05 * width
                    and (left[0] - center[0]) * (right[0] - center[0]) < 0
                    and abs(left[1] - center[1]) < .62 * width
                    and abs(right[1] - center[1]) < .62 * width)
        if self._candidate != expanded:
            self._candidate, self._candidate_since = expanded, now
        if now - self._candidate_since < .075:
            return False
        if not expanded:
            self._armed, self._spread = True, False
            return False
        if self._armed and not self._spread:
            self._spread, self._armed = True, False
            return True
        self._spread = True
        return False

    def update(self, state, now):
        if not _finite(now):
            self.reset()
            return self._result(False, False, 'waiting', missing=True, discontinuity=True,
                                reason='invalid_clock')
        continuity_reset = False
        continuous_roi = False
        continuity_reason = None
        if self.last_now is not None and now < self.last_now:
            self.reset()
            continuity_reset = True
            continuity_reason = 'clock_rollback'
        self.last_now = now
        explicit_missing = False
        special = False
        fresh = False
        if not isinstance(state, dict) or state.get('stale') or state.get('available') is False:
            explicit_missing = True
        else:
            stamp, seq = state.get('timestamp_seconds'), state.get('sequence')
            latency = state.get('latency_ms')
            if not _finite(stamp) or seq is None or (_finite(latency) and latency > 850):
                explicit_missing = True
            else:
                selection = state.get('roi_selection') or {}
                if not isinstance(selection, dict):
                    selection = {}
                identity = (state.get('pid'), state.get('person_id', state.get('track_id',
                            selection.get('person_id', selection.get('track_id')))))
                key = (state.get('pid'), seq, stamp)
                switched = self.identity is not None and identity != self.identity
                rollback = self.last_stamp is not None and stamp < self.last_stamp
                sequence_rollback = (key != self.last_key and not switched and
                    self.last_key is not None and _finite(seq) and
                    _finite(self.last_key[1]) and seq < self.last_key[1])
                continuity = selection.get('continuity')
                stream_reset = bool(state.get('stream_reset') or selection.get('stream_reset') or
                                    (isinstance(continuity, dict) and continuity.get('stream_reset')))
                discontinuity = key != self.last_key and bool(stream_reset or
                    state.get('motion_history_discontinuity') or selection.get('motion_history_discontinuity'))
                anchors = self._anchors(state.get('points17'))
                continuous_roi = (discontinuity and not switched and not rollback and
                                  not sequence_rollback and not stream_reset and
                                  self._continuous_roi(state, selection, identity, stamp, anchors))
                if switched or rollback or sequence_rollback or discontinuity:
                    self._gesture_reset()
                    self.body = None
                    if continuous_roi:
                        continuity_reason = 'measured_roi_continuity'
                    else:
                        continuity_reset = True
                        continuity_reason = ('identity_changed' if switched else
                            'source_timestamp_rollback' if rollback else
                            'source_sequence_rollback' if sequence_rollback else
                            'source_stream_reset' if stream_reset else 'unproven_roi_discontinuity')
                        self.last_good = self.body_anchors = self.anchor_stamp = None
                if rollback and not switched:
                    self.last_key, self.last_stamp = key, stamp
                    explicit_missing = True
                elif switched or self.last_stamp is None or stamp > self.last_stamp:
                    self.identity, self.last_key, self.last_stamp = identity, key, stamp
                    points = state.get('points17')
                    a, b, wrist = (self._point(points, i) for i in (5, 6, 10))
                    if a is None or b is None or wrist is None:
                        explicit_missing = True
                    else:
                        width = math.hypot(a[0] - b[0], a[1] - b[1])
                        center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                        if not .055 <= width <= 1.05:
                            explicit_missing = True
                        else:
                            if self.body is not None:
                                old_center, old_width = self.body
                                jump = math.dist(center, old_center)
                                if (jump > max(.14, old_width * .9)
                                        or not .57 <= width / old_width <= 1.75):
                                    self._gesture_reset()
                            if self.last_good is None or now - self.last_good > HOLD_SECONDS:
                                self._gesture_reset()
                            direction = -1. if self.mirrored else 1.
                            # COCO 6 is the right shoulder. Centering the hand's
                            # range there avoids a cross-body reach to steer.
                            self.x = _clip(.5 + direction * (wrist[0] - b[0]) / width / 1.6)
                            self.y = _clip(.35 + (wrist[1] - center[1]) / width / 2.6)
                            self.body, self.last_good = (center, width), now
                            self.body_anchors = anchors
                            self.anchor_stamp = stamp if anchors else None
                            fresh = True
                            special = self._gesture(points, center, width, now)
                # Keep a rejected observation rejected when the UI samples
                # the same latest-state slot again between inference frames.
                if key == self.last_key:
                    if fresh:
                        self._key_missing = False
                    elif explicit_missing:
                        self._key_missing = True
                    elif self._key_missing:
                        explicit_missing = True
                # Equal timestamps, including republished changed sequences,
                # deliberately do not update last_good or gesture state.
        age = now - self.last_good if self.last_good is not None else math.inf
        if explicit_missing:
            self._gesture_reset()
            self.body_anchors = self.anchor_stamp = None
        if age > HOLD_SECONDS:
            self._gesture_reset()
        valid = age <= HOLD_SECONDS
        status = ('waiting' if self.last_good is None else 'lost') if not valid else (
            'tracking' if fresh or (not explicit_missing and age <= .10) else 'holding')
        return self._result(valid, special and fresh and status == 'tracking', status,
                            missing=explicit_missing, discontinuity=continuity_reset,
                            continuous_roi=continuous_roi, reason=continuity_reason)

    def _result(self, valid, special, status, *, missing=False, discontinuity=False,
                continuous_roi=False, reason=None):
        return dict(x=self.x, y=self.y, valid=bool(valid), special=bool(special),
                    source_key=self.last_key, status=status,
                    observation_missing=bool(missing), continuity_reset=bool(discontinuity),
                    continuous_roi=bool(continuous_roi),
                    motion_reset=bool(discontinuity or continuous_roi), continuity_reason=reason)
