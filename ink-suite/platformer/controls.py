"""Fresh COCO-17 observations for a comfortable, personal hand joystick.

Call ``update(latest_packet, time.monotonic(), confirm_context=...)`` every
game tick. Movement is a level; jump and confirm are single-update pulses.
The right wrist is a small joystick around a proven personal resting point.
The left wrist crossing above its shoulder line produces one jump, independent
of the right arm. Title/terminal confirmation uses a fresh comfortable hold
and downward press relative to that hold; pipe entry retains a centered press.

Only new source observations prove calibration or gestures. A repeated packet
briefly holds movement but cannot repeat or finish an event. ``reset()`` keeps
a learned resting point and the source watermark, while requiring new centered
right-hand and lowered left-hand observations before actions resume.
"""
from collections import deque
import math
from statistics import median


MIRROR_DEFAULT = True
CONTROL_HINT = '右手小幅左右走，回中或放下停 · 左手抬过肩跳，放下再跳 · 开始时右手稍停后下压'
MIRROR_HINT = '左右按镜像画面方向识别'
CALIBRATION_HINT = '右手自然弯肘，停在腰腹或下胸附近片刻'
CONFIRM_CONTEXTS = ('title', 'terminal', 'pipe')

# Distances are in shoulder widths. Camera/human trials must still tune these
# initial values for comfortable movement and actual observation noise.
CALIBRATION_SECONDS = .70
JOYSTICK_START = .24
JOYSTICK_RELEASE = .14
JOYSTICK_VERTICAL = .22
JUMP_ABOVE = -.06
JUMP_BELOW = .04
CONFIRM_CENTER_SECONDS = .20
CONFIRM_PRESS = .30
CONFIRM_PRESS_SECONDS = .12
ENTRY_HOLD_SECONDS = .30
ENTRY_HORIZONTAL_LANE = .35
_EPSILON = 1e-8


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class PoseControls:
    """A body-relative adapter with independent, loss-safe arm controls.

    Shoulder translation and mild tilt follow the body. The resting point is
    learned once from a stable, low, bent right arm, rather than the body middle
    or the shoulder itself. It never drifts toward a held walking position.
    Missing right-arm points stop travel and confirmation; missing left-arm
    points clear jump readiness. Plausible connected limbs may use a low
    positive confidence score, but zero scores or impossible geometry cannot.
    """

    def __init__(self, mirrored=MIRROR_DEFAULT, aspect=16 / 9,
                 shared_clock=True, hold_seconds=.20, source_max_age=.45):
        if not _finite(aspect) or aspect <= 0:
            raise ValueError('aspect must be positive')
        if not _finite(hold_seconds) or not 0 < hold_seconds <= .5:
            raise ValueError('hold_seconds must be between 0 and .5')
        if not _finite(source_max_age) or source_max_age <= 0:
            raise ValueError('source_max_age must be positive')
        self.mirrored = bool(mirrored)
        self.aspect = float(aspect)
        self.shared_clock = bool(shared_clock)
        self.hold_seconds = float(hold_seconds)
        self.source_max_age = float(source_max_age)
        self.identity = self.sequence = self.stamp = None
        self._confirm_context = None
        self._clear_calibration()
        self.reset()

    def reset(self, *, preserve_calibration=True):
        """Release actions; keep a resting point unless explicitly discarded.

        Source identity/sequence/time watermarks always survive. Pause/restart
        cannot reuse a packet to prove a fresh return to center or left lowering.
        """
        if not preserve_calibration:
            self._clear_calibration()
        self.last_now = self.last_seen = self.body = None
        self.joystick_dx = self.joystick_dy = None
        self._clear_direction()
        self._clear_gestures()
        self._right_requires_center = self._neutral is not None
        self._right_center_since = None
        self._right_center_samples = 0
        self._geometry_frozen = False
        self._geometry_resume_since = None
        self._geometry_resume_samples = 0
        if self._neutral is None:
            self._reset_calibration_proof()
        else:
            self.calibration = 'ready'
            self.calibration_progress = 1.

    def _clear_calibration(self, *, keep_return_guard=False):
        # The old center guards re-calibration after a geometry change, but is
        # no longer a usable joystick origin. A walking displacement must not
        # become zero just because a geometry change stopped its movement.
        old_center = getattr(self, '_neutral', None)
        old_guard = getattr(self, '_calibration_return_guard', None)
        self._calibration_return_guard = ((old_center or old_guard)
                                           if keep_return_guard else None)
        self._neutral = None
        self._calibration_width = self._calibration_angle = None
        self._geometry_frozen = False
        self._geometry_resume_since = None
        self._geometry_resume_samples = 0
        self._right_requires_center = False
        self._right_center_since = None
        self._right_center_samples = 0
        self._reset_calibration_proof()

    def _reset_calibration_proof(self):
        self._calibration_samples = deque(maxlen=160)
        self._calibration_since = None
        self.calibration = 'waiting'
        self.calibration_progress = 0.

    def _clear_direction(self):
        self.move = self._direction_candidate = 0
        self._direction_since = None
        self._direction_samples = 0

    def _clear_jump(self):
        self.history = deque(maxlen=16)
        self.jump_armed = False
        self._neutral_since = self._jump_since = None
        self._neutral_samples = self._jump_samples = 0

    def _clear_confirm(self):
        self.confirm_armed = False
        self._confirm_center_since = self._confirm_since = None
        self._confirm_center_samples = self._confirm_samples = 0
        self._entry_hold = deque(maxlen=32)
        self._entry_anchor = self._entry_release_guard = None
        self._entry_confirm_origin = None

    def _clear_gestures(self):
        self._clear_jump()
        self._clear_confirm()
        self.right_history = deque(maxlen=16)

    def _loss(self, reason):
        self._clear_direction()
        self.last_seen = self.body = None
        self.joystick_dx = self.joystick_dy = None
        self._clear_gestures()
        self._right_center_since = None
        self._right_center_samples = 0
        if self._neutral is None:
            self._reset_calibration_proof()
        return self._result(False, reason)

    def _result(self, valid, reason, jump=False, confirm=False):
        return dict(move=self.move if valid else 0, jump=bool(jump),
                    confirm=bool(confirm), valid=bool(valid), reason=reason,
                    calibration=self.calibration,
                    calibration_progress=self.calibration_progress,
                    joystick_dx=self.joystick_dx if valid else None,
                    joystick_dy=self.joystick_dy if valid else None,
                    jump_ready=bool(valid and self.jump_armed),
                    confirm_ready=bool(valid and self._confirm_context
                                       and self.confirm_armed),
                    confirm_pending=bool(valid and self._confirm_context
                                         and self._confirm_since is not None))

    def _point(self, points, index):
        try:
            point = points[index]
            if (not isinstance(point, (list, tuple)) or len(point) < 3
                    or not all(_finite(v) for v in point[:3])
                    or not 0 < point[2] <= 1
                    or not all(0 <= v <= 1 for v in point[:2])):
                return None
            x = 1 - point[0] if self.mirrored else point[0]
            return (float(x) * self.aspect, float(point[1]))
        except (TypeError, IndexError, KeyError):
            return None

    def _arm(self, points, shoulder_index, elbow_index, wrist_index, width, axes):
        shoulder, elbow, wrist = (self._point(points, i)
                                  for i in (shoulder_index, elbow_index, wrist_index))
        if shoulder is None or elbow is None or wrist is None:
            return None
        upper = math.dist(shoulder, elbow) / width
        lower = math.dist(elbow, wrist) / width
        reach = math.dist(shoulder, wrist) / width
        if not (.04 <= upper <= 1.60 and .04 <= lower <= 1.60 and reach <= 2.65):
            return None
        horizontal, vertical = axes

        def relative(point):
            delta = ((point[0] - shoulder[0]) / width,
                     (point[1] - shoulder[1]) / width)
            return (delta[0] * horizontal[0] + delta[1] * horizontal[1],
                    delta[0] * vertical[0] + delta[1] * vertical[1])

        x, y = relative(wrist)
        elbow_x, elbow_y = relative(elbow)
        return x, y, elbow_x, elbow_y, upper, lower, reach

    @staticmethod
    def _identity(state, selection):
        return (state.get('pid'), state.get('person_id', state.get('track_id',
                selection.get('person_id', selection.get('track_id')))))

    def _remember_failed(self, state):
        # New failed frames supersede older good ones, without letting invalid
        # or future metadata poison the watermark.
        if not isinstance(state, dict) or self.identity is None:
            return
        selection = state.get('roi_selection') or {}
        if not isinstance(selection, dict):
            selection = {}
        stamp, sequence = state.get('timestamp_seconds'), state.get('sequence')
        if (self._identity(state, selection) == self.identity and _finite(stamp)
                and isinstance(sequence, int) and not isinstance(sequence, bool)
                and stamp > self.stamp and sequence > self.sequence
                and (not self.shared_clock or stamp <= self.last_now + .05)):
            self.stamp, self.sequence = stamp, sequence

    @staticmethod
    def _allowed_context(context):
        if context is True:
            return 'allowed'
        return context if isinstance(context, str) and context in CONFIRM_CONTEXTS else None

    @staticmethod
    def _comfortable(right):
        x, y, _, elbow_y, upper, lower, reach = right
        return (.25 <= y <= 1.45 and abs(x) <= 1.05
                and .12 <= elbow_y <= 1.35 and reach <= 1.50
                )

    def _calibrate(self, right, width, angle, stamp):
        if not self._comfortable(right):
            self._reset_calibration_proof()
            return 'calibration_low_hand'
        guard = self._calibration_return_guard
        if guard is not None and (abs(right[0] - guard[0]) > JOYSTICK_RELEASE
                                   or abs(right[1] - guard[1]) > .18):
            self._reset_calibration_proof()
            return 'calibration_return_center'
        sample = (stamp, right[0], right[1], width, angle)
        values = list(self._calibration_samples) + [sample]
        if (max(v[1] for v in values) - min(v[1] for v in values) > .18
                or max(v[2] for v in values) - min(v[2] for v in values) > .18
                or max(v[3] for v in values) / min(v[3] for v in values) > 1.16
                or max(v[4] for v in values) - min(v[4] for v in values) > .14):
            self._reset_calibration_proof()
        if self._calibration_since is None:
            self._calibration_since = stamp
        self._calibration_samples.append(sample)
        elapsed = stamp - self._calibration_since
        self.calibration = 'calibrating'
        self.calibration_progress = min(1., max(0., elapsed / CALIBRATION_SECONDS))
        if len(self._calibration_samples) >= 6 and elapsed + _EPSILON >= CALIBRATION_SECONDS:
            self._neutral = (median(v[1] for v in self._calibration_samples),
                             median(v[2] for v in self._calibration_samples))
            self._calibration_width = median(v[3] for v in self._calibration_samples)
            self._calibration_angle = median(v[4] for v in self._calibration_samples)
            self._calibration_return_guard = None
            self.calibration, self.calibration_progress = 'ready', 1.
            self._calibration_samples.clear()
            self._calibration_since = None
            return 'calibrated'
        return 'calibrating'

    def _body_change(self, width, angle, stamp):
        """Retain the center for mild changes, discard it for obvious ones."""
        if self._neutral is None:
            return None
        ratio = width / self._calibration_width
        turn = abs(angle - self._calibration_angle)
        if not .70 <= ratio <= 1.35 or turn > .33:
            self._clear_calibration(keep_return_guard=True)
            self._clear_direction()
            self._clear_gestures()
            return 'body_changed'
        unstable = not .84 <= ratio <= 1.16 or turn > .20
        if unstable:
            if not self._geometry_frozen:
                self._clear_direction()
                self._clear_gestures()
            self._geometry_frozen = True
            self._geometry_resume_since = None
            self._geometry_resume_samples = 0
            self.calibration = 'frozen'
            return 'body_frozen'
        if self._geometry_frozen:
            if not .88 <= ratio <= 1.12 or turn > .14:
                self._geometry_resume_since = None
                self._geometry_resume_samples = 0
                return 'body_frozen'
            if self._geometry_resume_since is None:
                self._geometry_resume_since = stamp
            self._geometry_resume_samples += 1
            if (self._geometry_resume_samples < 2
                    or stamp - self._geometry_resume_since + _EPSILON < .10):
                return 'body_frozen'
            self._geometry_frozen = False
            self._geometry_resume_since = None
            self._geometry_resume_samples = 0
            self.calibration = 'ready'
            return 'body_reacquired'
        return None

    def _direction(self, right, stamp):
        dx, dy = self.joystick_dx, self.joystick_dy
        if self._right_requires_center:
            centered = abs(dx) <= .22 and abs(dy) <= .22
            if not centered:
                self._right_center_since = None
                self._right_center_samples = 0
                self._clear_direction()
                return 'joystick_return_center'
            if self._right_center_since is None:
                self._right_center_since = stamp
            self._right_center_samples += 1
            if (self._right_center_samples < 2
                    or stamp - self._right_center_since + _EPSILON < .10):
                return 'joystick_return_center'
            self._right_requires_center = False
            self._right_center_since = None
            self._right_center_samples = 0
        # A vertical displacement and dropping/raising the hand stop travel
        # immediately. Neither can serve as a new horizontal resting point.
        if abs(dy) > JOYSTICK_VERTICAL or not .20 <= right[1] <= 1.45:
            self._clear_direction()
            return 'right_hand_lowered' if dy > 0 else 'right_hand_raised'
        wanted = 0
        if self.move and dx * self.move >= JOYSTICK_RELEASE:
            wanted = self.move
        elif abs(dx) >= JOYSTICK_START:
            wanted = 1 if dx > 0 else -1
        if not wanted:
            self._clear_direction()
            return 'joystick_center'
        if wanted == self.move:
            return 'joystick_moving'
        if wanted != self._direction_candidate:
            self.move = 0
            self._direction_candidate = wanted
            self._direction_since = stamp
            self._direction_samples = 1
        else:
            self._direction_samples += 1
            if (self._direction_samples >= 2
                    and stamp - self._direction_since + _EPSILON >= .045):
                self.move = wanted
        return 'joystick_moving' if self.move else 'joystick_starting'

    def _jump(self, left, stamp):
        if left is None:
            return False
        y = left[1]
        if y >= JUMP_BELOW:
            self._jump_since = None
            self._jump_samples = 0
            if self._neutral_since is None:
                self._neutral_since = stamp
            self._neutral_samples += 1
            if (self._neutral_samples >= 2
                    and stamp - self._neutral_since + _EPSILON >= .045):
                self.jump_armed = True
            return False
        self._neutral_since = None
        self._neutral_samples = 0
        if not self.jump_armed or y > JUMP_ABOVE:
            self._jump_since = None
            self._jump_samples = 0
            return False
        if self._jump_since is None:
            self._jump_since = stamp
        self._jump_samples += 1
        if (self._jump_samples >= 2
                and stamp - self._jump_since + _EPSILON >= .045):
            self.jump_armed = False
            self._jump_since = None
            self._jump_samples = 0
            return True
        return False

    def _confirm_entry(self, right, stamp):
        """One local down-press; the user need not locate a remembered zero."""
        x, y = right[:2]
        guard = self._entry_release_guard
        if guard is not None:
            if y > guard[1] + .12 or abs(x - guard[0]) > ENTRY_HORIZONTAL_LANE:
                return False
            self._entry_release_guard = None
        anchor = self._entry_anchor
        if anchor is None:
            # Arm connectivity is already checked. A projected straight elbow
            # is common in real video and is not evidence of an unusable hold.
            if not (.25 <= y <= 1.45 and abs(x) <= 1.25):
                self._clear_confirm()
                return False
            self._entry_hold.append((stamp, x, y))
            while self._entry_hold and stamp-self._entry_hold[0][0] > ENTRY_HOLD_SECONDS + .16:
                self._entry_hold.popleft()
            rows = list(self._entry_hold)
            if (max(v[1] for v in rows)-min(v[1] for v in rows) > .28 or
                    max(v[2] for v in rows)-min(v[2] for v in rows) > .22):
                self._entry_hold.clear()
                self._entry_hold.append((stamp, x, y))
                return False
            if len(rows) >= 4 and stamp-rows[0][0] + _EPSILON >= ENTRY_HOLD_SECONDS:
                self._entry_anchor = (median(v[1] for v in rows), median(v[2] for v in rows))
                self.confirm_armed = True
            return False
        dx, dy = x-anchor[0], y-anchor[1]
        if abs(dx) > ENTRY_HORIZONTAL_LANE or dy < -.20:
            self._clear_confirm()
            return False
        if dy + _EPSILON < CONFIRM_PRESS:
            self._confirm_since = None
            self._confirm_samples = 0
            return False
        if self._confirm_since is None:
            self._confirm_since = stamp
        self._confirm_samples += 1
        if (self._confirm_samples >= 2 and
                stamp-self._confirm_since + _EPSILON >= CONFIRM_PRESS_SECONDS):
            self._clear_confirm()
            self._entry_release_guard = anchor
            self._entry_confirm_origin = anchor
            return True
        return False

    def _confirm(self, right, stamp):
        if self._confirm_context in ('title', 'terminal'):
            return self._confirm_entry(right, stamp)
        if not self._confirm_context or self._neutral is None or self._right_requires_center:
            self._clear_confirm()
            return False
        dx, dy = self.joystick_dx, self.joystick_dy
        centered = abs(dx) <= JOYSTICK_RELEASE and abs(dy) <= .14
        if centered:
            self._confirm_since = None
            self._confirm_samples = 0
            if self._confirm_center_since is None:
                self._confirm_center_since = stamp
            self._confirm_center_samples += 1
            if (self._confirm_center_samples >= 3
                    and stamp - self._confirm_center_since + _EPSILON >= CONFIRM_CENTER_SECONDS):
                self.confirm_armed = True
            return False
        self._confirm_center_since = None
        self._confirm_center_samples = 0
        # Slow downward travel is allowed after centering. Leaving its small
        # vertical lane cancels readiness and requires another centered dwell.
        if abs(dx) > .16 or dy < .14:
            self._clear_confirm()
            return False
        if not self.confirm_armed or dy + _EPSILON < CONFIRM_PRESS:
            self._confirm_since = None
            self._confirm_samples = 0
            return False
        if self._confirm_since is None:
            self._confirm_since = stamp
        self._confirm_samples += 1
        if (self._confirm_samples >= 2
                and stamp - self._confirm_since + _EPSILON >= CONFIRM_PRESS_SECONDS):
            self._clear_confirm()
            return True
        return False

    def update(self, state, now, *, confirm_context=False, allow_held_confirm=False):
        """Consume a fresh pose; legacy ``allow_held_confirm`` has no effect.

        ``confirm_context`` is False during ordinary play, and True or one of
        ``title``, ``terminal``, ``pipe`` only when a downward press is allowed.
        The old keyword avoids breaking old integrations; acquired held hands
        and two raised arms have no confirmation behavior.
        """
        context = self._allowed_context(confirm_context)
        if context != self._confirm_context:
            self._clear_confirm()
            self._confirm_context = context
        if not _finite(now):
            return self._loss('invalid_clock')
        if self.last_now is not None and now < self.last_now:
            self.reset(preserve_calibration=False)
            self.last_now = now
            return self._loss('clock_rollback')
        self.last_now = now
        if (not isinstance(state, dict) or state.get('stale')
                or state.get('available') is False):
            self._remember_failed(state)
            return self._loss('missing_pose')
        stamp, sequence = state.get('timestamp_seconds'), state.get('sequence')
        if (not _finite(stamp) or not isinstance(sequence, int)
                or isinstance(sequence, bool) or sequence < 0):
            return self._loss('invalid_source')
        latency = state.get('latency_ms')
        if ((latency is not None and (not _finite(latency) or latency < 0))
                or (not self.shared_clock and latency is None)):
            return self._loss('invalid_latency')
        if ((_finite(latency) and latency > self.source_max_age * 1000)
                or (self.shared_clock and not -.05 <= now - stamp <= self.source_max_age)):
            self._remember_failed(state)
            return self._loss('source_expired')
        selection = state.get('roi_selection') or {}
        if not isinstance(selection, dict):
            selection = {}
        identity = self._identity(state, selection)
        changed = self.identity is not None and identity != self.identity
        continuity = selection.get('continuity') or {}
        if not isinstance(continuity, dict):
            continuity = {}
        stream_reset = bool(state.get('stream_reset') or selection.get('stream_reset')
                            or continuity.get('stream_reset')
                            or state.get('motion_history_discontinuity')
                            or selection.get('motion_history_discontinuity'))
        if not changed and self.stamp is not None:
            if stamp == self.stamp and sequence == self.sequence:
                valid = self.last_seen is not None and now - self.last_seen <= self.hold_seconds
                if not valid:
                    return self._loss('pose_timeout')
                return self._result(True, 'duplicate_pose')
            if not stream_reset and (stamp <= self.stamp or sequence <= self.sequence):
                return self._loss('out_of_order_pose')
        source_gap = self.stamp is not None and stamp - self.stamp > .25 + _EPSILON
        local_gap = (self.last_seen is None
                     or now - self.last_seen > self.hold_seconds + _EPSILON)
        if changed or stream_reset:
            self._clear_calibration()
        if changed or stream_reset or source_gap or local_gap:
            self._clear_gestures()
            self.body = None
            self._clear_direction()
            if self._neutral is None:
                self._reset_calibration_proof()
        self.identity, self.sequence, self.stamp = identity, sequence, stamp
        points = state.get('points17')
        if not isinstance(points, (list, tuple)) or len(points) != 17:
            return self._loss('invalid_points')
        a, b = self._point(points, 5), self._point(points, 6)
        if a is None or b is None:
            return self._loss('missing_shoulders')
        width = math.dist(a, b)
        if not .055 <= width <= 1.10 or abs(a[1] - b[1]) > .65 * width:
            return self._loss('implausible_shoulders')
        center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        shoulder_delta = ((a[0] - b[0], a[1] - b[1]) if a[0] >= b[0]
                          else (b[0] - a[0], b[1] - a[1]))
        horizontal = (shoulder_delta[0] / width, shoulder_delta[1] / width)
        vertical = (-horizontal[1], horizontal[0])
        angle = math.atan2(horizontal[1], horizontal[0])
        right = self._arm(points, 6, 8, 10, width, (horizontal, vertical))
        left = self._arm(points, 5, 7, 9, width, (horizontal, vertical))
        body_discontinuity = False
        if self.body is not None:
            old_center, old_width = self.body
            if not .70 <= width / old_width <= 1.40:
                self._clear_calibration(keep_return_guard=True)
                body_discontinuity = True
            elif math.dist(center, old_center) > max(.14, old_width * .75):
                # Translation follows the person; abrupt jumps still clear
                # events without changing the proven personal zero.
                body_discontinuity = True
            if body_discontinuity:
                self._clear_gestures()
                self._clear_direction()
        body_reason = self._body_change(width, angle, stamp)
        if body_discontinuity and self._neutral is None:
            body_reason = 'body_changed'
        for which, arm, history in (('right', right, self.right_history),
                                     ('left', left, self.history)):
            if arm is not None and history:
                old_stamp, old_x, old_y = history[-1]
                dt = stamp - old_stamp
                if dt > 0 and math.hypot(arm[0] - old_x, arm[1] - old_y) / dt > 12:
                    if which == 'right':
                        right = None
                    else:
                        left = None
        self.joystick_dx = self.joystick_dy = None
        if right is None:
            self._clear_direction()
            self.right_history.clear()
            self._clear_confirm()
            self._right_center_since = None
            self._right_center_samples = 0
            if self._neutral is None:
                self._reset_calibration_proof()
        if left is None:
            self._clear_jump()
        if right is None and left is None:
            return self._loss('missing_arms')
        self.body, self.last_seen = (center, width), now
        calibration_reason = direction_reason = None
        if right is not None:
            if self._neutral is None and body_reason != 'body_changed':
                calibration_reason = self._calibrate(right, width, angle, stamp)
            if self._neutral is not None:
                self.joystick_dx = right[0] - self._neutral[0]
                self.joystick_dy = right[1] - self._neutral[1]
                if not self._geometry_frozen:
                    direction_reason = self._direction(right, stamp)
            self.right_history.append((stamp, right[0], right[1]))
        if left is not None:
            self.history.append((stamp, left[0], left[1]))
        jump = confirm = False
        if not self._geometry_frozen and body_reason != 'body_changed':
            jump = self._jump(left, stamp)
        if right is not None and body_reason != 'body_changed':
            if self._confirm_context in ('title','terminal') or not self._geometry_frozen:
                confirm = self._confirm(right, stamp)
        if confirm:
            self._clear_direction()
            if self._entry_confirm_origin is not None:
                # Start/restart is an explicit calibration moment, never an
                # automatic drift toward a hand held to walk during gameplay.
                self._neutral = self._entry_confirm_origin
                self._calibration_width, self._calibration_angle = width, angle
                self._calibration_return_guard = None
                self._geometry_frozen = False
                self._geometry_resume_since = None
                self._geometry_resume_samples = 0
                self.calibration, self.calibration_progress = 'ready', 1.
                self.joystick_dx = right[0]-self._neutral[0]
                self.joystick_dy = right[1]-self._neutral[1]
                self._right_requires_center = True
        # Explicit branches keep UI reasons independent from ternary precedence.
        if confirm:
            reason = 'confirm'
        elif jump:
            reason = 'jump'
        elif body_reason:
            reason = body_reason
        elif right is None:
            reason = 'missing_right_arm'
        elif left is None:
            reason = 'missing_left_arm'
        else:
            reason = calibration_reason or direction_reason or (
                'source_reacquired' if changed or stream_reset or local_gap or source_gap else 'tracking')
        return self._result(True, reason, jump, confirm)


# Keep the name familiar to the other InkSuite modules.
InputTracker = PoseControls
