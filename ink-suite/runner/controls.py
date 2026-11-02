"""Body side-step, real upward jump and crouch from fresh COCO-17 samples.

Wrists only confirm on the title/result screen; running controls need shoulders
and hips. Source/identity resets establish a fresh neutral torso and never emit
an old movement edge. Screen directions follow the mirrored preview.
"""
import math
from .body_motion import BodyMotion


CONTROL_HINT = '站左走左道 · 回中走中道 · 站右走右道 · 起跳跳跃 · 下蹲滑铲'
MIRROR_HINT = '左右跟随镜像画面方向'
CALIBRATION_HINT = '自然站立片刻建立身体中心；身体站位对应三条跑道，回中即回中道'
MIRROR_DEFAULT = True


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class PoseControls:
    def __init__(self, mirrored=True, aspect=16 / 9, shared_clock=True,
                 hold_seconds=.20, source_max_age=.45, recovery_seconds=2.):
        if not _finite(aspect) or aspect <= 0:
            raise ValueError('aspect must be positive')
        if not _finite(hold_seconds) or not 0 < hold_seconds <= .2:
            raise ValueError('hold_seconds must be positive and <= .2')
        if not _finite(source_max_age) or source_max_age <= 0:
            raise ValueError('source_max_age must be positive')
        if not _finite(recovery_seconds) or recovery_seconds < source_max_age:
            raise ValueError('recovery_seconds must cover the source freshness window')
        self.mirrored = bool(mirrored)
        self.aspect = float(aspect)
        self.shared_clock = bool(shared_clock)
        self.hold_seconds = hold_seconds
        self.source_max_age = source_max_age
        self.recovery_seconds = recovery_seconds
        self.identity = self.sequence = self.stamp = self.pose_count = None
        self.last_now = self.last_seen = None
        self.reset()

    def reset(self, **_):
        """Keep source watermark; a restart cannot consume an old gesture."""
        self.last_seen = None
        self._clear_motion()

    def _clear_motion(self):
        self.body = BodyMotion()
        self.jump_armed = False
        self._confirm_since = None
        self._confirm_fired = False
        self._body_geometry = None
        self._resume_pending = False

    def _result(self, valid, reason, **pulses):
        result = dict(move=0, lane_target=None, jump=False, slide=False, confirm=False,
                      valid=bool(valid), reason=reason, jump_ready=self.jump_armed,
                      calibration=self.body.calibration,
                      calibration_progress=self.body.progress)
        result.update(pulses)
        return result

    def _loss(self, reason, now):
        if (self.last_seen is None or not _finite(now)
                or now-self.last_seen > self.recovery_seconds):
            self._clear_motion()
        else:
            self._suspend_motion()
        held = (self.last_seen is not None and _finite(now)
                and 0 <= now - self.last_seen <= self.hold_seconds)
        return self._result(held, reason)

    def _suspend_motion(self):
        self.body.suspend_observation()
        self._resume_pending = True
        self._confirm_since = None
        self._confirm_fired = False

    def _point(self, points, index):
        try:
            point = points[index]
            if (not isinstance(point, (tuple, list)) or len(point) < 3
                    or not all(_finite(v) for v in point[:3])
                    or not 0 < point[2] <= 1
                    or not all(0 <= v <= 1 for v in point[:2])):
                return None
            return ((1. - point[0] if self.mirrored else point[0]) * self.aspect,
                    float(point[1]))
        except (KeyError, IndexError, TypeError):
            return None

    @staticmethod
    def _identity(packet):
        return packet.get('pid'), packet.get('visitor_session')

    def _arm(self, points, shoulder, wrist_index, width):
        wrist = self._point(points, wrist_index)
        if wrist is None or math.dist(wrist, shoulder) > width * 2.65:
            return None
        return ((wrist[0] - shoulder[0]) / width,
                (wrist[1] - shoulder[1]) / width)



    def _confirm(self, both_up, stamp):
        if not both_up:
            self._confirm_since = None
            self._confirm_fired = False
            return False
        if self._confirm_since is None:
            self._confirm_since = stamp
        if not self._confirm_fired and stamp - self._confirm_since >= .35:
            self._confirm_fired = True
            return True
        return False


    def update(self, packet, now, **_):
        if not _finite(now):
            return self._result(False, 'invalid_clock')
        if self.last_now is not None and now < self.last_now:
            self.reset()
            self.last_now = now
            return self._result(False, 'clock_rollback')
        self.last_now = now
        if not isinstance(packet, dict):
            return self._loss('missing_pose', now)
        stamp, sequence = packet.get('timestamp_seconds'), packet.get('sequence')
        if (not _finite(stamp) or not isinstance(sequence, int)
                or isinstance(sequence, bool) or sequence < 0):
            return self._loss('invalid_source', now)
        latency = packet.get('latency_ms')
        if (latency is not None and (not _finite(latency) or latency < 0)
                or not self.shared_clock and latency is None):
            return self._loss('invalid_latency', now)
        # A corrupt future clock must not move the watermark beyond all real
        # observations. Otherwise one malformed packet can disable controls.
        if self.shared_clock and stamp - now > .05:
            return self._loss('future_source', now)
        identity = self._identity(packet)
        changed = self.identity is not None and identity != self.identity
        selection = packet.get('roi_selection') or {}
        if not isinstance(selection, dict):
            selection = {}
        continuity = selection.get('continuity') or {}
        if not isinstance(continuity, dict):
            continuity = {}
        stream_reset = bool(packet.get('stream_reset') or selection.get('stream_reset')
                            or continuity.get('stream_reset'))
        # An ROI fallback is a different estimate of the same camera frame,
        # not a new player/source. Clear velocities, never learn its side-step
        # endpoint as a new neutral stance.
        motion_gap = bool(packet.get('motion_history_discontinuity')
                          or selection.get('motion_history_discontinuity'))
        pose_count=packet.get('pose_count')
        count_ok=isinstance(pose_count,int) and not isinstance(pose_count,bool) and pose_count>=0
        counter_restart=bool(self.sequence is not None and sequence<self.sequence
            and self.stamp is not None and stamp>self.stamp
            and (count_ok and self.pose_count is not None and pose_count>self.pose_count
                 or sequence<=4 and self.sequence>=16))
        stream_reset=stream_reset or counter_restart
        if not changed and self.stamp is not None:
            if sequence == self.sequence and stamp == self.stamp:
                held = self.last_seen is not None and now - self.last_seen <= self.hold_seconds
                return self._result(held, 'duplicate_pose' if held else 'pose_timeout')
            if not stream_reset and (sequence <= self.sequence or stamp <= self.stamp):
                return self._loss('out_of_order_pose', now)
        if (changed or stream_reset or self.last_seen is None
                or now-self.last_seen > self.recovery_seconds):
            self._clear_motion()
        elif (motion_gap or self.stamp is not None and stamp-self.stamp > .30
                or now-self.last_seen > .30):
            self._suspend_motion()
        self.identity, self.sequence, self.stamp = identity, sequence, stamp
        self.pose_count=pose_count if count_ok else None
        if (packet.get('stale') or packet.get('available') is False
                or _finite(latency) and latency > self.source_max_age * 1000
                or self.shared_clock and now - stamp > self.source_max_age):
            return self._loss('source_expired', now)
        points = packet.get('points17')
        if not isinstance(points, (list, tuple)) or len(points) != 17:
            return self._loss('invalid_points', now)
        shoulders = self._point(points, 5), self._point(points, 6)
        if any(point is None for point in shoulders):
            return self._loss('missing_shoulders', now)
        width = math.dist(*shoulders)
        if not .055 <= width <= 1.1 or abs(shoulders[0][1] - shoulders[1][1]) > width * .65:
            return self._loss('implausible_shoulders', now)
        center = ((shoulders[0][0] + shoulders[1][0]) / 2,
                  (shoulders[0][1] + shoulders[1][1]) / 2)
        if self._body_geometry is not None:
            old_center, old_width = self._body_geometry
            if (not .7 <= width / old_width <= 1.4
                    or math.dist(center, old_center) > old_width * .8):
                return self._loss('body_changed', now)
        left = self._arm(points, shoulders[0], 9, width)
        right = self._arm(points, shoulders[1], 10, width)
        hips = self._point(points, 11), self._point(points, 12)
        if any(point is None for point in hips):
            return self._loss('missing_hips', now)
        hip = ((hips[0][0]+hips[1][0])/2, (hips[0][1]+hips[1][1])/2)
        if not .35 < (hip[1]-center[1])/width < 3.:
            return self._loss('implausible_torso', now)
        # Commit the reference only after the whole torso is valid. A frame
        # with narrowed shoulders but a missing/invalid hip must not replace
        # the last reliable width and reject its normal successor as a scale
        # jump (for example 1/.705 > 1.4).
        self._body_geometry = center, width
        self.last_seen = now
        both_up = left is not None and right is not None and left[1] < -.08 and right[1] < -.08
        if self._resume_pending and self.body.anchor is not None:
            self.body.resume_observation(center, hip, width, stamp)
            self._resume_pending = False
            self.jump_armed = self.body.jump_armed
            return self._result(True, 'pose_recovered')
        self._resume_pending = False
        motion = self.body.update(center, hip, width, stamp)
        self.jump_armed = self.body.jump_armed
        move, jump, slide = motion['move'], motion['jump'], motion['slide']
        lane_target = motion['lane_target']
        phase = _.get('phase')
        confirm = self._confirm(both_up if phase != 'running' else False, stamp)
        if confirm or phase in ('title', 'over'):
            move, jump, slide = 0, False, False
            lane_target = None
        reason = 'confirm' if confirm else 'jump' if jump else 'slide' if slide else 'lane' if move else 'tracking' if motion['calibration']=='ready' else 'calibrating'
        return self._result(True, reason, move=move, lane_target=lane_target,
                            jump=jump, slide=slide, confirm=confirm)


InputTracker = PoseControls
