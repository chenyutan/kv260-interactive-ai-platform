"""Non-blocking readiness of the camera-to-pose stream, not person detection.

``now`` must use the same monotonic clock as ``timestamp_seconds`` (the GUI
and production API both run on the board). HTTP success alone is insufficient:
several recent capture identities and completed inference counts must advance.
No camera images, skeleton confidences, timers, threads, or I/O are used here.
"""
from collections.abc import Mapping
import math


class PipelineReadiness:
    """Gate gesture input until a live capture and inference stream is proven.

    Call ``update(state, time.monotonic())`` on every response, including ``None``
    on HTTP failure. A proven stream tolerates a bounded transport miss without
    rearming the host; consumers still receive no pose and cannot create input.
    Duplicate polling is harmless for up to ``stall_seconds``;
    it never contributes to the three-observation startup confirmation. Legacy
    synthetic APIs may omit ``pose_count``, camera metadata, and ``phase``.
    """

    # app.py uses lost only for stalled inference and camera_link_slow for an
    # unusable capture link. The action engine's unknown and not_ready instead
    # describe person quality / action templates and must remain acceptable.
    _BLOCKED_PHASES = frozenset((
        'lost', 'camera_unavailable', 'camera_link_slow', 'error', 'failed',
        'stopped', 'stopping', 'unavailable', 'service_unavailable', 'restarting',
        'disconnected', 'offline', 'initializing', 'loading', 'starting',
        'warming_up',
    ))

    def __init__(self, observations=3, warmup_seconds=.08,
                 max_frame_age=.75, future_tolerance=.10, stall_seconds=.40,
                 transport_grace_seconds=.75):
        if (isinstance(observations, bool) or not isinstance(observations, int)
                or observations < 3):
            raise ValueError('At least three observations are required')
        limits = (warmup_seconds, max_frame_age, future_tolerance, stall_seconds,transport_grace_seconds)
        if any(self._number(value) is None or value < 0 for value in limits):
            raise ValueError('Readiness time limits must be finite and nonnegative')
        if warmup_seconds <= 0 or stall_seconds <= 0 or transport_grace_seconds <= 0:
            raise ValueError('Warmup and stall times must be positive')
        self.observations = observations
        self.warmup_seconds = float(warmup_seconds)
        self.max_frame_age = float(max_frame_age)
        self.future_tolerance = float(future_tolerance)
        self.stall_seconds = float(stall_seconds)
        self.transport_grace_seconds = float(transport_grace_seconds)
        self.reset()

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            return float(value) if math.isfinite(value) else None
        except (OverflowError, ValueError):
            return None

    @classmethod
    def _counter(cls, value):
        number = cls._number(value)
        return int(value) if number is not None and number >= 0 and number.is_integer() else None

    def reset(self, reason='warming_up'):
        """Clear all confirmation history, e.g. on service loss or GUI reopen."""
        self.ready = False
        self.reason = reason
        self._identity = None
        self._samples = 0
        self._first_stamp = None
        self._first_now = None
        self._last_progress = None
        self._last_now = None
        self._transport_gap = False

    def _clear(self, reason):
        self.reset(reason)
        return False

    def _accept(self, identity, now):
        self._identity = identity
        self._last_progress = now
        if self._samples == 0:
            self._first_now = now
            self._first_stamp = identity[2]
        self._samples += 1
        self.ready = (self._samples >= self.observations
                      and now - self._first_now + 1e-9 >= self.warmup_seconds
                      and identity[2] - self._first_stamp + 1e-9 >= self.warmup_seconds)
        self.reason = 'ready' if self.ready else 'warming_up'
        return self.ready

    def update(self, state, now):
        """Keep short missing HTTP samples distinct from explicit capture loss."""
        now = self._number(now)
        if now is None:
            return self._clear('service_unavailable')
        if self._last_now is not None and now < self._last_now:
            self.reset('warming_up')
        self._last_now = now
        if state is None:
            if (self.ready and self._last_progress is not None
                    and now-self._last_progress < self.transport_grace_seconds):
                self._transport_gap = True
                self.reason = 'transport_retry'
                return True
            return self._clear('service_unavailable')
        if not isinstance(state, Mapping):
            return self._clear('service_unavailable')
        transport_gap = self._transport_gap
        self._transport_gap = False

        link = state.get('camera_link')
        if isinstance(link, Mapping) and link.get('present') is False:
            return self._clear('camera_missing')
        phase = state.get('phase')
        if isinstance(phase, str) and phase in self._BLOCKED_PHASES:
            if phase == 'camera_unavailable':
                return self._clear('camera_missing')
            if phase in ('warming_up', 'initializing', 'loading', 'starting'):
                return self._clear('warming_up')
            return self._clear('service_unavailable')

        pid = state.get('pid')
        sequence = self._counter(state.get('sequence'))
        stamp = self._number(state.get('timestamp_seconds'))
        has_pose_count = 'pose_count' in state
        pose_count = self._counter(state.get('pose_count')) if has_pose_count else None
        if (not isinstance(pid, (int, str)) or isinstance(pid, bool) or not pid
                or sequence is None or stamp is None or (has_pose_count and not pose_count)):
            return self._clear('warming_up')
        age = now - stamp
        if age > self.max_frame_age or age < -self.future_tolerance:
            return self._clear('stale_frame')

        identity = (pid, sequence, stamp, pose_count)
        previous = self._identity
        if previous is None:
            return self._accept(identity, now)
        pid_changed = identity[0] != previous[0]
        counter_mode_changed = (pose_count is None) != (previous[3] is None)
        regressed = (sequence < previous[1] or stamp < previous[2]
                     or (has_pose_count and previous[3] is not None and pose_count < previous[3]))
        if pid_changed or counter_mode_changed or regressed:
            self.reset('warming_up')
            self._last_now = now
            return self._accept(identity, now)

        advanced = (sequence > previous[1] and stamp > previous[2]
                    and (not has_pose_count or pose_count > previous[3]))
        if (transport_gap and self.ready and advanced
                and now-self._last_progress < self.transport_grace_seconds):
            # Only a genuinely new, fresh, same-process capture+inference pair
            # confirms transport recovery. A cached/frozen response cannot do so.
            self._last_progress = now
        # Compare consecutive observations, including partially updated cached
        # responses. A changing timestamp cannot disguise a frozen capture or
        # inference counter, nor count as progress toward startup.
        self._identity = identity
        stalled = now - self._last_progress >= self.stall_seconds
        if stalled:
            self.ready = False
            self.reason = 'stream_stalled'
            self._samples = 0
        if advanced:
            return self._accept(identity, now)
        if self.ready:self.reason = 'ready'
        return self.ready
