"""Conservative single-player ownership, without additional inference or storage.

This is continuity, not biometric re-identification. Similar clothes, a smooth
substitution, or an undetected occlusion can be indistinguishable to a single
pose model. All appearance features are transient and never enter diagnostics.
"""
import math
import threading
import uuid
import numpy as np


def masked_pose():
    p = np.zeros((17, 3), dtype=np.float32)
    p[:, :2] = -1
    return p


def torso(points, width, height):
    if getattr(points, 'shape', None) != (17, 3):
        return None
    q = [points[i].tolist() for i in (5, 6, 11, 12)]
    if any(not all(math.isfinite(v) for v in row) or row[2] < .3 or not (0 <= row[0] <= 1 and 0 <= row[1] <= 1) for row in q):
        return None
    sx,sy=(q[0][0]+q[1][0])*.5,(q[0][1]+q[1][1])*.5
    hx,hy=(q[2][0]+q[3][0])*.5,(q[2][1]+q[3][1])*.5
    aspect=height/width
    span=math.hypot(q[0][0]-q[1][0],(q[0][1]-q[1][1])*aspect)
    length=math.hypot(hx-sx,(hy-sy)*aspect)
    if span < 18 / width or span > .5 or length < .035 or hy-sy < .045:
        return None
    return [(sx+hx)*.5,(sy+hy)*.5*aspect,span,length]


def appearance_yuyv(raw, points, width, height, stride):
    """Sample 45 existing pixels inside the torso. No camera call, image copy or IO."""
    if torso(points, width, height) is None:
        return None
    try:
        q=[points[i].tolist() for i in (5,6,11,12)]
        top=[(q[0][j]+q[1][j])*.5 for j in (0,1)]
        bottom=[(q[2][j]+q[3][j])*.5 for j in (0,1)]
        half=abs(q[0][0]-q[1][0])*.22
        frame=memoryview(raw).cast('B')
        if len(frame)<height*stride:return None
        features = []
        for fraction in (.30, .53, .76):
            cx=top[0]*(1-fraction)+bottom[0]*fraction
            cy=top[1]*(1-fraction)+bottom[1]*fraction
            xs=[min(width-1,max(0,round((cx+half*j/2)*width))) for j in range(-2,3)]
            ys=[min(height-1,max(0,round((cy+.008*j)*height))) for j in range(-1,2)]
            yy,uu,vv=[],[],[]
            for y in ys:
                offset=y*stride
                for x in xs:
                    pair=offset+(x//2)*4
                    yy.append(frame[offset+x*2]);uu.append(frame[pair+1]);vv.append(frame[pair+3])
            features.extend([sorted(channel)[7]/255. for channel in (yy,uu,vv)])
        return features
    except (ValueError, TypeError, IndexError, BufferError):
        return None


def arm_overlap_bands(points):
    """Intersect confident elbow-wrist segments with the existing sample boxes.

    This uses the actual three 5-by-3 sampling footprints, without enlarging
    them or changing any pose coordinate/score. No pixel or feature is exposed.
    """
    if getattr(points, 'shape', None) != (17, 3):
        return (False, False, False)
    q = [points[i].tolist() for i in (5, 6, 11, 12)]
    if any(not all(math.isfinite(v) for v in row) or row[2] < .3
           or not (0 <= row[0] <= 1 and 0 <= row[1] <= 1) for row in q):
        return (False, False, False)
    top = [(q[0][j]+q[1][j])*.5 for j in (0, 1)]
    bottom = [(q[2][j]+q[3][j])*.5 for j in (0, 1)]
    half = abs(q[0][0]-q[1][0])*.22
    segments = []
    for elbow, wrist in ((7, 9), (8, 10)):
        pair = [points[i].tolist() for i in (elbow, wrist)]
        if all(all(math.isfinite(v) for v in row) and row[2] >= .3
               and 0 <= row[0] <= 1 and 0 <= row[1] <= 1 for row in pair):
            segments.append(pair)

    def intersects(pair, x0, y0, x1, y1):
        lower, upper = 0., 1.
        for axis, low, high in ((0, x0, x1), (1, y0, y1)):
            start = pair[0][axis]
            delta = pair[1][axis]-start
            if abs(delta) < 1e-12:
                if not low <= start <= high:
                    return False
                continue
            a, b = sorted(((low-start)/delta, (high-start)/delta))
            lower, upper = max(lower, a), min(upper, b)
            if lower > upper:
                return False
        return True

    overlaps = []
    for fraction in (.30, .53, .76):
        cx = top[0]*(1-fraction)+bottom[0]*fraction
        cy = top[1]*(1-fraction)+bottom[1]*fraction
        overlaps.append(bool(any(intersects(pair, cx-half, cy-.008, cx+half, cy+.008)
                                 for pair in segments)))
    return tuple(overlaps)


class PlayerTracker:
    def __init__(self, acquire_seconds=.8, recover_seconds=.35, lost_seconds=2.,
                 long_recover_seconds=1.2, max_observation_gap=.75,
                 min_observations=4):
        self.lock = threading.RLock()
        self.acquire_seconds, self.recover_seconds, self.lost_limit = acquire_seconds, recover_seconds, lost_seconds
        self.long_recover_seconds = max(1., float(long_recover_seconds))
        self.max_observation_gap = float(max_observation_gap)
        self.min_observations = max(4, int(min_observations))
        limits = (acquire_seconds, recover_seconds, lost_seconds,
                  self.long_recover_seconds, self.max_observation_gap)
        if any(not math.isfinite(value) or value <= 0 for value in limits):
            raise ValueError('Tracking time limits must be positive and finite')
        self.epoch = 0
        self.session = None
        self._clear(False)

    def _clear(self, armed):
        self.armed = armed
        self.identity = None
        self.allowed = False
        self.reason = 'awaiting_player' if armed else 'awaiting_session'
        self.last_time = self.last_seen = self.loss_started = None
        self.anchor = self.last_geometry = self.candidate_geometry = self.candidate_appearance = None
        self.geometry = None
        self.recovery_required = False
        self.lost = 0.
        self._reset_candidate()
        self._begin_evidence(None)
        self.last_candidate_rejection_timestamp_seconds = None
        self.last_candidate_rejection_evidence = {}

    def _begin_evidence(self, timestamp, fresh=False):
        self.evidence_timestamp_seconds = timestamp
        self.evidence = dict.fromkeys((
            'raw_torso_ok', 'roi_unambiguous', 'center_ok', 'scale_ok', 'geometry_ok',
            'anchor_normal_ok', 'anchor_recovery_ok', 'band0_match', 'band1_match',
            'band2_match', 'arm_overlap0', 'arm_overlap1', 'arm_overlap2',
            'arm_occlusion_ignored', 'candidate_stable', 'observation_fresh',
            'observation_gap_ok', 'strict_matching'), False)
        self.evidence['observation_fresh'] = bool(fresh)

    def _record_rejection(self, timestamp):
        self.last_candidate_rejection_timestamp_seconds = timestamp
        self.last_candidate_rejection_evidence = dict(self.evidence)

    def _matching_evidence(self, bands, strict):
        self.evidence['strict_matching'] = bool(strict)
        for index in range(3):
            self.evidence['band%d_match' % index] = bool(bands[index])

    def _reset_candidate(self):
        self.candidate_since = None
        self.candidate_count = 0
        self.candidate_geometry = self.candidate_appearance = None
        self.candidate_ignored_band = None

    def configure(self, command):
        session = command.get('player_session', self.session)
        action = command.get('player_action')
        if session is not None and (not isinstance(session, str) or not 1 <= len(session) <= 128):
            raise ValueError('player_session must be a nonempty opaque string of at most 128 characters')
        if action not in (None, 'release', 'acquire'):
            raise ValueError('Invalid player_action')
        with self.lock:
            changed = session != self.session
            if changed or action == 'release' or (action == 'acquire' and self.identity is None and not self.armed):
                self.session = session
                self.epoch += 1
                self._clear(session is not None and action != 'release')
                return True
            # acquire cannot silently replace the current player's identity.
            return False

    def snapshot(self, timestamp=None):
        with self.lock:
            age = self.lost
            clock_valid = True
            if timestamp is not None:
                try:
                    stamp = float(timestamp)
                    clock_valid = (math.isfinite(stamp) and
                                   (self.last_time is None or stamp >= self.last_time))
                    if self.last_seen is not None and clock_valid:
                        age = max(age, stamp - self.last_seen)
                except (TypeError, ValueError, OverflowError):
                    clock_valid = False
            hold_crop = (self.identity is not None and self.last_seen is not None
                         and not self.recovery_required and clock_valid
                         and age <= self.lost_limit)
            elapsed = (max(0., self.last_time - self.candidate_since)
                       if self.last_time is not None and self.candidate_since is not None else 0.)
            return dict(identity=self.identity, epoch=self.epoch, session=self.session,
                        locked=self.identity is not None, active=self.allowed, input_allowed=self.allowed,
                        quality='confirmed' if self.allowed else 'blocked', lost_seconds=round(self.lost, 3),
                        reason=self.reason, requires_new_session=False,
                        recovery_required=self.recovery_required, hold_crop=hold_crop,
                        recovery_samples=self.candidate_count,
                        recovery_seconds=round(elapsed, 3), policy='single-player-continuity-v2',
                        revision='arm-occlusion-v1',
                        evidence_timestamp_seconds=self.evidence_timestamp_seconds,
                        evidence=dict(self.evidence),
                        last_candidate_rejection_timestamp_seconds=self.last_candidate_rejection_timestamp_seconds,
                        last_candidate_rejection_evidence=dict(self.last_candidate_rejection_evidence))

    def permits(self, epoch=None):
        with self.lock:
            return self.allowed and (epoch is None or epoch == self.epoch)

    def _gate(self, allowed, reason):
        if self.allowed != allowed:
            self.epoch += 1
        self.allowed, self.reason = allowed, reason

    @staticmethod
    def _appearance_valid(a):
        if a is None:
            return False
        try:
            return len(a) == 9 and all(math.isfinite(x) for x in a)
        except (TypeError, ValueError):
            return False

    @classmethod
    def _appearance_bands(cls, a, b, recovery=False):
        if not cls._appearance_valid(a) or not cls._appearance_valid(b):
            return (False, False, False)
        return tuple(bool(all(abs(a[i]-b[i]) <= (.19 if i%3 == 0 else .065 if recovery else .085)
                              for i in range(band*3, band*3+3))) for band in range(3))

    @classmethod
    def _appearance_agrees(cls, a, b, recovery=False):
        return all(cls._appearance_bands(a, b, recovery))

    @staticmethod
    def _geometry_agrees(a, b, distance=.08, scale=1.38):
        if a is None or b is None:
            return False
        return math.hypot(a[0]-b[0],a[1]-b[1])<=distance and all(1/scale<=a[i]/b[i]<=scale for i in (2,3))

    @staticmethod
    def _scale_agrees(a, b, scale=1.8):
        return a is not None and b is not None and all(
            1/scale <= a[i]/b[i] <= scale for i in (2, 3))

    def _candidate(self, valid, geometry, appearance, timestamp, previous, strict=True,
                   ignored_band=None):
        # Compare with the first candidate, rather than a moving reference that
        # could drift gradually to another person. Duplicate/invalid frames
        # never reach this method and cannot advance its observation count.
        candidate_bands = self._appearance_bands(appearance, self.candidate_appearance, strict)
        appearance_stable = all(candidate_bands[index] for index in range(3) if index != ignored_band)
        stable = (valid and self.candidate_since is not None
                  and previous is not None
                  and timestamp - previous <= self.max_observation_gap
                  and self._geometry_agrees(geometry, self.candidate_geometry, .045, 1.2)
                  and ignored_band == self.candidate_ignored_band and appearance_stable)
        self.evidence['candidate_stable'] = bool(stable)
        if not valid or (self.candidate_count and not stable):
            self._record_rejection(timestamp)
        if not valid:
            self._reset_candidate()
        elif stable:
            self.candidate_count += 1
        else:
            self.candidate_since = timestamp
            self.candidate_count = 1
            self.candidate_geometry = geometry.copy()
            self.candidate_appearance = np.asarray(appearance).copy()
            self.candidate_ignored_band = ignored_band

    def _confirmed_candidate(self, timestamp, seconds):
        return (self.candidate_count >= self.min_observations
                and self.candidate_since is not None
                and timestamp - self.candidate_since + 1e-9 >= seconds)

    def _discontinuity(self, reason):
        self._reset_candidate()
        if self.identity is not None:
            self.recovery_required = True
        self.evidence['strict_matching'] = bool(self.identity is not None)
        if reason != 'waiting_original_player':
            self._record_rejection(self.evidence_timestamp_seconds)
        self._gate(False, reason)

    def _confirm_owner(self, geometry, timestamp):
        # The appearance anchor and identity are immutable until an explicit
        # session/release command. Reconfirmation changes only continuity.
        self.last_geometry = geometry.copy()
        self.last_seen = timestamp
        self.loss_started = None
        self.lost = 0.
        self.recovery_required = False
        self._reset_candidate()
        self._gate(True, 'owner_reconfirmed')

    def observe(self, points, timestamp, width, height, appearance, alternatives=(), expected_epoch=None):
        with self.lock:
            if expected_epoch is not None and expected_epoch != self.epoch:
                return masked_pose(), self.snapshot()
            try:
                t = float(timestamp)
                dimensions_valid = (math.isfinite(width) and math.isfinite(height)
                                    and width > 0 and height > 0)
            except (TypeError, ValueError, OverflowError):
                t, dimensions_valid = float('nan'), False
            # Invalid observations must not poison the next finite timestamp.
            if not math.isfinite(t) or not dimensions_valid:
                self._begin_evidence(None)
                self._discontinuity('invalid_observation')
                return masked_pose(), self.snapshot()
            self._begin_evidence(t, True)
            previous = self.last_time
            self.evidence['observation_gap_ok'] = bool(previous is None or
                0 < t-previous <= self.max_observation_gap)
            if previous is not None and t == previous:
                self.evidence['observation_fresh'] = False
                self._discontinuity('duplicate_observation')
                return masked_pose(), self.snapshot()
            changed_clock = previous is not None and t < previous
            changed_geometry = self.geometry is not None and self.geometry != (width, height)
            self.last_time = t
            self.geometry = (width, height)
            if changed_clock or changed_geometry:
                self.evidence['observation_fresh'] = False
                if changed_clock:
                    # Rebase the observation clock once. Never wait for it to
                    # catch up to a timestamp from the former capture process.
                    self.last_seen = None
                    self.loss_started = t
                    self.lost = 0.
                self._discontinuity('stream_discontinuity')
                return masked_pose(), self.snapshot()
            if not self.armed:
                return masked_pose(), self.snapshot()
            g = torso(points, width, height)
            ambiguous = any(torso(other, width, height) is not None and g is not None and
                            not self._geometry_agrees(g, torso(other, width, height), .055, 1.5)
                            for other in alternatives)
            normal_bands = self._appearance_bands(appearance, self.anchor, False)
            strict_bands = self._appearance_bands(appearance, self.anchor, True)
            overlaps = arm_overlap_bands(points)
            self.evidence.update(raw_torso_ok=g is not None, roi_unambiguous=not ambiguous,
                center_ok=bool(g is not None and .38 <= g[0] <= .62),
                scale_ok=bool(self._scale_agrees(g, self.last_geometry)),
                anchor_normal_ok=bool(all(normal_bands)),
                anchor_recovery_ok=bool(all(strict_bands)))
            for index in range(3):
                self.evidence['arm_overlap%d' % index] = bool(overlaps[index])
            if self.identity is None:
                self._matching_evidence(strict_bands, True)
                self.evidence['geometry_ok'] = bool(g is not None and
                    (self.candidate_geometry is None or self._geometry_agrees(g, self.candidate_geometry, .045, 1.2)))
                valid = (g is not None and .38 <= g[0] <= .62
                         and self._appearance_valid(appearance) and not ambiguous)
                self._candidate(valid, g, appearance, t, previous)
                if valid and self._confirmed_candidate(t, self.acquire_seconds):
                    self.identity = uuid.uuid4().hex[:16]
                    self.anchor = np.asarray(self.candidate_appearance).copy()
                    self.last_geometry = g.copy()
                    self.last_seen = t
                    self._reset_candidate()
                    self._gate(True, 'player_locked')
                else:
                    self._gate(False, 'ambiguous_candidates' if ambiguous else 'stand_center_to_claim')
            else:
                reference = self.last_seen if self.last_seen is not None else self.loss_started
                self.lost = max(0., t - reference) if reference is not None else 0.
                if self.lost > self.lost_limit and not self.recovery_required:
                    self._discontinuity('waiting_original_player')
                gap = t - previous if previous is not None else 0.
                if gap > self.max_observation_gap:
                    self._reset_candidate()
                    self._gate(False, 'observation_gap')
                elif gap > .4 and self.allowed:
                    # A 3 FPS stream remains continuous. A longer pause still
                    # cancels gesture history and requires owner confirmation.
                    self._gate(False, 'observation_gap')
                if self.recovery_required:
                    self._matching_evidence(strict_bands, True)
                    self.evidence['geometry_ok'] = bool(g is not None and
                        (self.candidate_geometry is None or self._geometry_agrees(g, self.candidate_geometry, .045, 1.2)))
                    valid = (not ambiguous and g is not None and .38 <= g[0] <= .62
                             and self._scale_agrees(g, self.last_geometry)
                             and self._appearance_agrees(appearance, self.anchor, True))
                    self._candidate(valid, g, appearance, t, previous)
                    if valid and self._confirmed_candidate(t, self.long_recover_seconds):
                        self._confirm_owner(g, t)
                    else:
                        self._gate(False, 'ambiguous_candidates' if ambiguous else
                                   'reconfirming_original_player' if valid else 'waiting_original_player')
                else:
                    recovery = not self.allowed
                    self._matching_evidence(normal_bands, False)
                    dt = min(.15, max(.0, t - self.last_seen))
                    distance = .035 + .65 * dt if not recovery else .065
                    geometry_ok = self._geometry_agrees(g, self.last_geometry, distance)
                    self.evidence['geometry_ok'] = bool(geometry_ok)
                    mismatches = [index for index in range(3) if not normal_bands[index]]
                    # Exempt at most one *actually crossed* sampling band only
                    # for this recently confirmed owner and its original local
                    # geometry. Initial claim and long recovery remain strict.
                    ignore_band = bool(self.lost <= self.lost_limit and not ambiguous and geometry_ok
                        and len(mismatches) == 1 and overlaps[mismatches[0]])
                    self.evidence['arm_occlusion_ignored'] = ignore_band
                    valid = bool(not ambiguous and geometry_ok and (all(normal_bands) or ignore_band))
                    if not valid:
                        self._record_rejection(t)
                        self._reset_candidate()
                        self._gate(False, 'ambiguous_candidates' if ambiguous else 'owner_not_confirmed')
                    elif recovery:
                        # A one-frame loss must not tighten the same owner's
                        # previously valid .085 chroma drift to .065. The long
                        # recovery branch above retains its strict thresholds.
                        # Keep the ignored band fixed across this candidate
                        # window; moving the exception to another band starts
                        # fresh confirmation rather than accumulating votes.
                        self._candidate(True, g, appearance, t, previous, strict=False,
                                        ignored_band=mismatches[0] if ignore_band else None)
                        if self._confirmed_candidate(t, self.recover_seconds):
                            self._confirm_owner(g, t)
                        else:
                            self._gate(False, 'reconfirming_original_player')
                    else:
                        self.last_geometry = g.copy()
                        self.last_seen = t
                        self.lost = 0.
                        self._gate(True, 'player_locked')
            return np.asarray(points).copy() if self.allowed else masked_pose(), self.snapshot()
