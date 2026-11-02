"""Causal, template-based body actions; never accepts a video name or action hint.

Adapter input order is [x, y, score] in ORIGINAL image coordinates, with COCO's
17-joint indexing. The caller must undo cropping/padding and map the model's
joint order first. Scores retain their model-specific semantics; this module
performs no pose inference and makes no pretrained-model accuracy claim.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import ctypes
import math
import os
from pathlib import Path
import numpy as np

ACTIONS = ("A01", "A02", "A03", "A04", "A05", "A06")
BODY_IDS = (5, 6, 7, 8, 9, 10, 11, 12)
ENGINE_VERSION = "causal_body_templates_v3_rearm_and_split_continuity"
DEFAULT_CONFIG = {
    "enabled_actions": None,
    "min_score": .30, "min_shoulder_pixels": 18.,
    "min_distal_score": None,
    "ready_seconds": .45, "hold_seconds": .45, "return_seconds": .45,
    "stable_radius": .025, "terminal_radius": .06, "onset_distance": .065,
    "start_distance": .23, "endpoint_distance": .28,
    "endpoint_hysteresis": .04, "progress_hysteresis": .05,
    "min_progress": .72, "min_duration": .25, "max_duration": 12.,
    "max_tracking_gap": .20, "max_sample_gap": .16,
    "max_distance": .22, "min_margin": .018, "origin_weight": .35,
    "minimum_excursion": .16, "max_reverse_fraction": .32,
}

DTW_BACKEND = 'numpy'
NATIVE_DTW_ERROR = None
_NATIVE_DTW = None
_NATIVE_LIBRARY = None


def _load_native_dtw():
    global _NATIVE_DTW, _NATIVE_LIBRARY, DTW_BACKEND, NATIVE_DTW_ERROR
    configured = os.environ.get('BODYINK_DTW_LIBRARY')
    path = Path(configured) if configured else Path(__file__).with_name('libbodyink_dtw.so')
    if not path.is_file():
        if configured:
            NATIVE_DTW_ERROR = 'configured_library_missing'
        return
    try:
        _NATIVE_LIBRARY = ctypes.CDLL(str(path.resolve()))
        function = _NATIVE_LIBRARY.bodyink_dtw_cost
        function.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int, ctypes.c_int]
        function.restype = ctypes.c_double
        _NATIVE_DTW = function
        DTW_BACKEND = 'native'
    except (OSError, AttributeError) as exc:
        NATIVE_DTW_ERROR = type(exc).__name__ + ':' + str(exc)


_load_native_dtw()


def quality_thresholds(config):
    """Model-native score cutoffs for shoulder/elbow/wrist/hip core ordering.

    None keeps the historical all-joint cutoff. A separate distal cutoff is an
    explicit model profile, not a probability conversion or a filled-in score.
    """
    torso = float(config['min_score'])
    distal = config.get('min_distal_score')
    distal = torso if distal is None else float(distal)
    if not (math.isfinite(torso) and math.isfinite(distal) and min(torso,distal)>=0):
        raise ValueError('invalid_native_score_thresholds')
    return np.array([torso,torso,distal,distal,distal,distal,torso,torso],float)


def normalize_frame(points17, width, height, scale=None, min_score=.30, min_distal_score=None):
    """Return hip-centered eight joints and measured shoulder width, or reason."""
    p = np.asarray(points17, dtype=float)
    if p.shape != (17, 3):
        return None, None, "expected_17x3_xy_score"
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        return None, None, "invalid_image_dimensions"
    p = p[list(BODY_IDS)]
    thresholds = (quality_thresholds({'min_score':min_score,'min_distal_score':min_distal_score})
                  if np.isscalar(min_score) else np.asarray(min_score,float))
    if thresholds.shape != (8,) or not np.isfinite(thresholds).all() or (thresholds<0).any():
        return None, None, 'invalid_native_score_thresholds'
    if not np.isfinite(p).all() or (p[:, 2] < thresholds).any():
        return None, None, "body_keypoints_not_reliable"
    if ((p[:, :2] < 0) | (p[:, :2] > 1)).any():
        return None, None, "body_out_of_frame"
    xy = p[:, :2] * [width, height]
    measured = float(np.linalg.norm(xy[0] - xy[1]))
    divisor = measured if scale is None else float(scale)
    if divisor <= 1e-6:
        return None, measured, "degenerate_shoulders"
    return (xy - xy[6:8].mean(0)) / divisor, measured, "ok"


def descriptor(poses, times, count=32):
    """Interpolation is for classification only, never the ink velocity path."""
    p = np.asarray(poses, float).reshape(-1, 16)
    t = np.asarray(times, float)
    if len(t) < 4 or len(t) != len(p) or not np.isfinite(p).all():
        raise ValueError("insufficient_trajectory")
    if not np.isfinite(t).all() or not (np.diff(t) > 0).all():
        raise ValueError("nonmonotonic_timestamps")
    ts = np.linspace(t[0], t[-1], count)
    return np.stack([np.interp(ts, t, p[:, j]) for j in range(16)], axis=1)


def mirror_pose(pose):
    p = np.asarray(pose, float).reshape(-1, 8, 2)[:, [1, 0, 3, 2, 5, 4, 7, 6]].copy()
    p[:, :, 0] *= -1
    return p.reshape(np.asarray(pose).shape)


def dtw_distance(a, b):
    global _NATIVE_DTW, DTW_BACKEND, NATIVE_DTW_ERROR
    a, b = np.asarray(a).reshape(-1, 16), np.asarray(b).reshape(-1, 16)
    local = np.sqrt(np.mean((a[:, None] - b[None]) ** 2, axis=-1))
    band = max(6, abs(len(a) - len(b)))
    if _NATIVE_DTW is not None:
        costs = np.ascontiguousarray(local, dtype=np.float64)
        result = float(_NATIVE_DTW(costs.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(a), len(b), band))
        if math.isfinite(result) and result >= 0:
            return result
        # A rejected native input cannot become a zero-distance perfect match.
        NATIVE_DTW_ERROR = 'native_returned_invalid_distance'
        _NATIVE_DTW = None
        DTW_BACKEND = 'numpy'
    return _dtw_cost_numpy(local, band)


def _dtw_cost_numpy(local, band):
    """Same recurrence, with the within-row dependency handled by prefix min.

    row[j] = cost[j] + min(previous[j], previous[j-1], row[j-1]).
    This removes the Python inner loop even when no native library is installed.
    """
    n, m = local.shape
    previous = np.full(m + 1, np.inf)
    previous[0] = 0.
    for i in range(n):
        start, stop = max(0, i - band), min(m, i + band + 1)
        cells = local[i, start:stop]
        prefix = np.cumsum(cells)
        base = np.minimum(previous[start + 1:stop + 1], previous[start:stop])
        current = np.full(m + 1, np.inf)
        current[start + 1:stop + 1] = prefix + np.minimum.accumulate(base - prefix + cells)
        previous = current
    return float(previous[m] / (n + m))


def trajectory_speeds(poses, times, action, max_gap=.16, gap_times=()):
    """Causal three-sample median; real dt and fixed shoulder scale are retained.

    A04 uses shoulder-center displacement relative to the hips (NOT radians/s).
    A02/A05 use the more active wrist, selected from the completed trajectory.
    Unmeasurable derivatives remain None rather than fabricated zero velocity.
    """
    p, t = np.asarray(poses, float), np.asarray(times, float)
    if action == "A04":
        c = p[:, :2].mean(1)[:, None, :]
        active = "torso"
    elif action in ("A02", "A05"):
        excursions = np.linalg.norm(np.percentile(p[:, 4:6], 95, axis=0) - np.percentile(p[:, 4:6], 5, axis=0), axis=1)
        side = int(np.argmax(excursions))
        c = p[:, 4 + side:5 + side]
        active = "left" if side == 0 else "right"
    else:
        c, active = p[:, 4:6], "both"
    smooth = np.empty_like(c)
    segment_start = 0
    gaps = np.asarray(gap_times, float)
    broken = [False] + [bool(((gaps > t[i - 1]) & (gaps <= t[i])).any()) for i in range(1, len(t))]
    for i in range(len(c)):
        if i and (t[i] - t[i - 1] > max_gap or broken[i]):
            segment_start = i
        smooth[i] = np.median(c[max(segment_start, i - 2):i + 1], axis=0)
    speeds = [None]
    for i, dt in enumerate(np.diff(t), start=1):
        value = float(np.linalg.norm(smooth[i] - smooth[i - 1], axis=-1).mean() / dt) if 0 < dt <= max_gap and not broken[i] else None
        speeds.append(value)
    return speeds, active


def _rms(x):
    return float(np.sqrt(np.mean(np.asarray(x) ** 2)))


@dataclass
class _Template:
    action: str
    sequence: np.ndarray
    start: np.ndarray
    end: np.ndarray
    delta: np.ndarray
    energy: float
    mirrored: bool

    def progress(self, pose, start):
        change = pose - start
        if self.action != 'A04':
            change = change - change[..., :2, :].mean(axis=-2, keepdims=True)
        return np.mean(change * self.delta, axis=(-2, -1)) / self.energy

    def pose_error(self, pose, target):
        # Stationary hips must not dilute a large moving-wrist error.
        amplitude = np.linalg.norm(self.delta, axis=1)
        moving = amplitude >= max(.08, float(amplitude.max()) * .25)
        # Torso/shoulder-width ratios differ between people. All four arm joints
        # still matter: ignoring the nominally stationary hand lets an A03 ready
        # posture incorrectly arm an A02 and never recover.
        relative_error = (pose - pose[:2].mean(0)) - (target - target[:2].mean(0))
        error = max(_rms(relative_error[2:6]), _rms(relative_error[moving]))
        if self.action == 'A04':
            torso, reference = pose[:2].mean(0), target[:2].mean(0)
            angle = math.atan2(torso[0], -torso[1])
            angle_reference = math.atan2(reference[0], -reference[1])
            axis = pose[0] - pose[1]
            axis /= max(float(np.linalg.norm(axis)), .1)
            arms = pose[4:6] - pose[:2]
            outward = np.array([np.dot(arms[0], axis), -np.dot(arms[1], axis)])
            # A04 permits both horizontal and downward-outstretched arms.
            # Both arms must stay outside the torso, while torso tilt is the
            # discriminating geometry; do not memorize one person's arm angle.
            error = max(float(np.maximum(.65 - outward, 0).max()), abs(angle - angle_reference))
        return error


class ActionEngine:
    """One person, one complete action at a time; all state transitions causal.

    templates: mapping A01..A06 -> [M,32,16] or [M,32,8,2], or np.load result.
    speed_calibration: mapping action -> [p10,p90] in shoulder-widths/second.
    Missing classes/calibrations disable submission instead of silently creating
    an incomplete closed-set classifier. Distances/margins are not probabilities.
    """
    def __init__(self, templates, speed_calibration, *, config=None):
        self.config = dict(DEFAULT_CONFIG)
        if config:
            unknown = set(config) - set(self.config)
            if unknown:
                raise ValueError("unknown_config:" + ",".join(sorted(unknown)))
            self.config.update(config)
        self.score_thresholds = quality_thresholds(self.config)
        enabled=self.config.get('enabled_actions')
        if enabled is None:
            self.enabled_actions=ACTIONS
        else:
            if (not isinstance(enabled,(list,tuple)) or not 2<=len(enabled)<=len(ACTIONS)
                    or len(set(enabled))!=len(enabled) or any(a not in ACTIONS for a in enabled)):
                raise ValueError('enabled_actions must contain 2..6 distinct known action IDs')
            self.enabled_actions=tuple(a for a in ACTIONS if a in enabled)
            self.config['enabled_actions']=list(self.enabled_actions)
        self.templates = []
        self.calibration = {}
        self.readiness_errors = []
        templates = {} if templates is None else templates
        speed_calibration = {} if speed_calibration is None else speed_calibration
        for action in self.enabled_actions:
            try:
                arr = np.asarray(templates[action], float)
                if arr.shape in ((32, 16), (32, 8, 2)):
                    arr = arr.reshape(1, 32, 16)
                arr = arr.reshape(-1, 32, 16)
                if not len(arr) or not np.isfinite(arr).all():
                    raise ValueError()
                for original in arr:
                    for reflected in (False, True):
                        seq = mirror_pose(original) if reflected else original.copy()
                        start = seq[0].reshape(8, 2)
                        end = seq[-1].reshape(8, 2)
                        delta = end - start
                        if action != 'A04':
                            delta = delta - delta[:2].mean(0)
                            delta[6:] = 0.
                        energy = float(np.mean(delta ** 2))
                        if energy < self.config["minimum_excursion"] ** 2:
                            continue
                        self.templates.append(_Template(action, seq, start, end, delta, energy, reflected))
                if not any(t.action == action for t in self.templates):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                self.readiness_errors.append("missing_or_invalid_templates:" + action)
            try:
                lo, hi = map(float, speed_calibration[action])
                if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo < hi):
                    raise ValueError()
                self.calibration[action] = [lo, hi]
            except (KeyError, ValueError, TypeError):
                self.readiness_errors.append("missing_or_invalid_speed_calibration:" + action)
        self._cache_start_geometry()
        self.reset()

    def _cache_start_geometry(self):
        """Fixed template geometry used by the hot per-frame preparation scan."""
        if not self.templates:
            self._start_geometry = None
            return
        targets = np.stack([t.start for t in self.templates])
        amplitude = np.linalg.norm(np.stack([t.delta for t in self.templates]), axis=2)
        moving = amplitude >= np.maximum(.08, amplitude.max(axis=1)*.25)[:, None]
        self._start_geometry = (targets-targets[:, :2].mean(axis=1, keepdims=True),
                                moving, np.array([t.action=='A04' for t in self.templates]),
                                targets[:, :2].mean(axis=1))
        self._start_native=None
        self._native_stability=None
        path=Path(os.environ.get('BODYINK_GEOMETRY_LIBRARY',Path(__file__).with_name('libbodyink_geometry.so')))
        if path.is_file():
            try:
                lib=ctypes.CDLL(str(path.resolve()));fn=lib.bodyink_start_errors
                double=np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS')
                byte=np.ctypeslib.ndpointer(dtype=np.uint8,flags='C_CONTIGUOUS')
                fn.argtypes=[double,double,byte,byte,double,ctypes.c_int,double];fn.restype=ctypes.c_int
                relative,moving,torso_class,reference=self._start_geometry
                angles=np.array([math.atan2(x,-y) for x,y in reference])
                self._start_native=(lib,fn,np.ascontiguousarray(relative),moving.astype(np.uint8),torso_class.astype(np.uint8),angles)
                if hasattr(lib,'bodyink_stability_radius'):
                    stable=lib.bodyink_stability_radius
                    stable.argtypes=[double,ctypes.c_int];stable.restype=ctypes.c_double
                    self._native_stability=stable
            except (OSError,AttributeError):pass

    def _start_matches(self, pose):
        native=getattr(self,'_start_native',None)
        if native is not None and np.isfinite(pose).all() and np.max(np.abs(pose))<1e4:
            _,fn,targets,moving,torso,angles=native
            errors=np.empty(len(self.templates),np.float64)
            if fn(np.ascontiguousarray(pose,dtype=np.float64),targets,moving,torso,angles,len(errors),errors)==0:
                threshold=self.config['start_distance']
                # Conservative ambiguity band: retain the original entire
                # NumPy/scalar gate at threshold boundaries or invalid output.
                if np.isfinite(errors).all() and not (np.abs(errors-threshold)<=1e-9).any():
                    return [self.templates[i] for i in np.flatnonzero(errors<=threshold)]
        return self._start_matches_numpy(pose)

    def _start_matches_numpy(self, pose):
        """Same pose_error gate, evaluated across fixed templates in one batch.

        Values at the threshold use the original scalar calculation to retain
        boundary decisions despite harmless floating-point reduction ordering.
        """
        if self._start_geometry is None:
            return []
        targets, moving, torso_class, reference = self._start_geometry
        torso = pose[:2].mean(0)
        error = (pose-torso)[None, :, :]-targets
        square = error**2
        arm_error = np.sqrt(square[:, 2:6].mean(axis=(1, 2)))
        moving_error = np.sqrt((square*moving[:, :, None]).sum(axis=(1, 2))/np.maximum(moving.sum(axis=1)*2,1))
        errors = np.maximum(arm_error, moving_error)
        if torso_class.any():
            axis = pose[0]-pose[1]
            axis /= max(float(np.linalg.norm(axis)), .1)
            arms = pose[4:6]-pose[:2]
            outward = np.array([np.dot(arms[0],axis),-np.dot(arms[1],axis)])
            arm_limit = float(np.maximum(.65-outward,0).max())
            angle = math.atan2(torso[0],-torso[1])
            # math.atan2 matches the original scalar A04 implementation.
            for i in np.flatnonzero(torso_class):
                errors[i] = max(arm_limit,abs(angle-math.atan2(reference[i,0],-reference[i,1])))
        threshold = self.config['start_distance']
        for i in np.flatnonzero(np.abs(errors-threshold)<=1e-12):
            errors[i] = self.templates[i].pose_error(pose,self.templates[i].start)
        return [self.templates[i] for i in np.flatnonzero(errors<=threshold)]

    def reset(self):
        self.phase = "idle" if not self.readiness_errors else "not_ready"
        self.last_timestamp = None
        self.last_valid = None
        self.last_pose = None
        self.scale = None
        self.measured_scales = deque(maxlen=60)
        self.window = deque()
        self.start_pose = None
        self.start_candidates = []
        self.trajectory = []
        self.hold_start = None
        self.endpoint_pose = None
        self.ready_at = None
        self.last_rejection = None
        self.had_terminal_candidate = False
        self.missing_times = []
        self.cycle = getattr(self, 'cycle', 0)

    def _result(self, quality, reason, event=None, **extra):
        result = dict(phase=self.phase, quality=quality, event=event, reason=reason,
                      engine=ENGINE_VERSION, last_rejection=self.last_rejection,
                      dtw_backend=DTW_BACKEND, native_dtw_error=NATIVE_DTW_ERROR)
        result.update(extra)
        return result

    def _discard(self, reason):
        self.last_rejection = reason
        self.phase = "return"
        self.trajectory = []
        self.start_candidates = []
        self.window.clear()
        self.hold_start = None
        self.endpoint_pose = None

    def _prepare_again(self):
        """A fresh held preparation must not inherit a stale pose scale/anchor."""
        self.phase='prepare'
        self.scale=None
        self.measured_scales.clear()
        self.window.clear()
        self.start_pose=None
        self.start_candidates=[]
        self.trajectory=[]
        self.hold_start=None
        self.ready_at=None
        self.had_terminal_candidate=False
        self.missing_times=[]

    def _tracking_lost(self,reason):
        if self.phase in ('idle','prepare','ready'):
            # No execution has begun: discard all partial preparation and
            # require a NEW stable start, without demanding an unrelated
            # neutral return pose. Execution/hold/return still cancel safely.
            self._prepare_again()
            self.phase='idle'
            self.last_rejection='preparation_tracking_lost_reset'
        else:
            self._discard(reason)
        self.scale=None
        self.measured_scales.clear()

    def _stable(self, seconds, radius=None):
        if len(self.window) < 4 or self.window[-1][0] - self.window[0][0] < seconds - 1e-6:
            return False
        poses = np.stack([p for _, p in self.window])
        native=getattr(self,'_native_stability',None)
        threshold=self.config['stable_radius'] if radius is None else radius
        if native is not None and np.isfinite(poses).all() and np.max(np.abs(poses))<1e4:
            value=native(np.ascontiguousarray(poses,dtype=np.float64),len(poses))
            # Float64 rounding differs across NumPy versions. The guard is
            # much larger than roundoff at the bounded coordinate magnitude.
            # Near every gate, run the unchanged original calculation below.
            if math.isfinite(value) and abs(value-threshold)>1e-9:
                return value<=threshold
        center = np.median(poses, axis=0)
        return float(np.percentile(np.sqrt(np.mean((poses - center) ** 2, axis=(1, 2))), 90)) <= (self.config["stable_radius"] if radius is None else radius)

    @staticmethod
    def _neutral(p):
        shoulders = p[:2].mean(0)
        wrists = p[4:6]
        # Hands below elbows/chest and near the body, upright torso. This is only
        # a rearming gate, never a class prediction.
        return bool((wrists[:, 1] > shoulders[1] + .7).all() and
                    (abs(wrists[:, 0]) < 1.1).all() and abs(shoulders[0]) < .35)

    def _endpoint_candidates(self, p):
        candidates = []
        continuing=self.hold_start is not None
        minimum=self.config['min_progress']-(self.config['progress_hysteresis'] if continuing else 0.)
        distance=self.config['endpoint_distance']+(self.config['endpoint_hysteresis'] if continuing else 0.)
        for template in self.start_candidates:
            progress = float(template.progress(p, self.start_pose))
            endpoint_error = template.pose_error(p, template.end)
            if minimum <= progress <= 1.65 and endpoint_error <= distance:
                candidates.append((template, progress, endpoint_error))
        return candidates

    def _terminal_stable(self, terminal_candidates):
        if not self._stable(self.config['hold_seconds'], self.config['terminal_radius']):
            return False
        times = np.asarray([t for t, _ in self.window])
        poses = np.stack([p for _, p in self.window])
        centered = times - times.mean()
        # A small slow tail can fit a spatial radius. It is not terminal while
        # directional progress still has a sustained trend across the window.
        for template, _, _ in terminal_candidates:
            progress = template.progress(poses, self.start_pose)
            slope = float(np.dot(centered, progress - progress.mean()) / max(np.dot(centered, centered), 1e-9))
            if abs(slope) <= .045:
                return True
        return False

    def _prepared_motion_still(self):
        if len(self.window) < 4:
            return False
        times = np.array([t for t, _ in self.window])
        poses = np.stack([p for _, p in self.window])
        centered = times - times.mean()
        for template in self.start_candidates:
            progress = template.progress(poses, self.start_pose)
            slope = float(np.dot(centered, progress - progress.mean()) / max(np.dot(centered, centered), 1e-9))
            if abs(slope) > .045:
                return False
        return True

    def _complete(self, decision_time, terminal_candidates):
        # Exclude terminal dwell; timestamps are captured, never inferred from FPS.
        usable = [(t, p) for t, p in self.trajectory if t <= self.hold_start + 1e-9]
        times = np.array([t for t, _ in usable])
        poses = np.stack([p for _, p in usable])
        if len(times) < 5 or times[-1] - times[0] < self.config["min_duration"]:
            return None, "execution_too_short"
        # A short confidence gap can preserve action identity while its velocity
        # remains unmeasurable. Do not conflate the classifier continuity budget
        # with the stricter derivative budget used by trajectory_speeds below.
        if np.max(np.diff(times)) > self.config["max_tracking_gap"]+1e-9:
            return None, "execution_tracking_gap"
        seq = descriptor(poses, times)
        def compare(template):
            # Retain part of the absolute prepared posture, but give the actual
            # motion more weight than constant torso/limb-proportion offsets.
            offset = 1. - self.config['origin_weight']
            return dtw_distance(seq - offset * seq[0], template.sequence - offset * template.sequence[0])
        eligible = {t.action for t, _, _ in terminal_candidates}
        scores = {a: min(compare(t) for t in self.templates if t.action == a) for a in self.enabled_actions}
        ranked = sorted(scores, key=scores.get)
        action = ranked[0]
        dist, margin = scores[action], scores[ranked[1]] - scores[action]
        if action not in eligible:
            return None, "class_disagrees_with_prepared_endpoint"
        if dist > self.config["max_distance"]:
            return None, "trajectory_outside_templates"
        if margin < self.config["min_margin"]:
            return None, "ambiguous_action"
        options = [(t, progress, err) for t, progress, err in terminal_candidates if t.action == action]
        template, progress, endpoint_error = min(options, key=lambda item: compare(item[0]))
        signed = template.progress(poses, self.start_pose)
        # Use ~0.1s samples to reduce per-frame jitter before counting reversals.
        sample_times = np.arange(times[0], times[-1], .10)
        signed = np.interp(np.r_[sample_times, times[-1]], times, signed)
        steps = np.diff(signed)
        forward = float(np.maximum(steps - .012, 0).sum())
        reverse = float(np.maximum(-steps - .012, 0).sum())
        if reverse > max(.12, forward * self.config["max_reverse_fraction"]):
            return None, "reversal_or_control_circle"
        speeds, active = trajectory_speeds(poses, times, action, self.config["max_sample_gap"], self.missing_times)
        gaps = [[float(times[i - 1]), float(times[i])] for i in range(1, len(times)) if speeds[i] is None]
        self.cycle += 1
        event = dict(action=action, event_id=self.cycle, times=times.tolist(), pose=poses.tolist(),
                     speeds=speeds, speed_unit="shoulder_widths/s", active_part=active,
                     speed_calibration=self.calibration[action], distance=dist, margin=margin,
                     start_time=float(times[0]), end_time=float(times[-1]),
                     decision_time=float(decision_time), ready_time=float(self.ready_at),
                     shoulder_scale_pixels=float(self.scale), endpoint_error=endpoint_error,
                     progress=progress, reverse_fraction=reverse / max(forward, 1e-9),
                     tracking_gaps=gaps,
                     reason="prepared_directed_motion_terminal_hold", engine=ENGINE_VERSION)
        return event, "action_completed"

    def update(self, points17, timestamp, width, height):
        quality = dict(valid=False, min_score=0., reason="not_checked")
        if self.readiness_errors:
            self.phase = "not_ready"
            return self._result(quality, ";".join(self.readiness_errors))
        try:
            timestamp = float(timestamp)
        except (ValueError, TypeError):
            self.reset()
            return self._result(quality, "invalid_timestamp")
        if not math.isfinite(timestamp) or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            self.reset()
            return self._result(quality, "nonmonotonic_timestamp_reset")
        previous_timestamp = self.last_timestamp
        self.last_timestamp = timestamp
        if previous_timestamp is not None and timestamp - previous_timestamp > self.config["max_tracking_gap"]+1e-9:
            self._tracking_lost("sample_stream_interrupted")
        p, measured, reason = normalize_frame(points17, width, height, self.scale, self.score_thresholds)
        if measured is not None and measured < self.config["min_shoulder_pixels"]:
            p, reason = None, "body_too_small"
        if p is not None and self.scale is not None and not .65 <= measured / self.scale <= 1.5:
            p, reason = None, "body_scale_changed_reposition"
        if p is None:
            if self.phase in ('ready', 'execute'):
                self.missing_times.append(timestamp)
                self.missing_times = [t for t in self.missing_times if t > timestamp - self.config['max_duration'] - 1.]
            quality["reason"] = reason
            if self.last_valid is not None and timestamp - self.last_valid > self.config["max_tracking_gap"]+1e-9:
                self._tracking_lost("tracking_lost_cycle_cancelled")
            saved_phase = self.phase
            self.phase = "unknown"
            output = self._result(quality, reason, suspended_phase=saved_phase)
            self.phase = saved_phase
            return output
        if self.last_valid is not None and timestamp-self.last_valid>self.config['max_tracking_gap']+1e-9:
            # Also check the first recovered valid sample: all preceding missing
            # samples may have individually stopped just short of the budget.
            self._tracking_lost('tracking_lost_cycle_cancelled')
            p,measured,reason=normalize_frame(points17,width,height,self.scale,self.score_thresholds)
        quality = dict(valid=True, min_score=float(np.asarray(points17)[list(BODY_IDS), 2].min()), reason="ok")
        self.last_valid = timestamp
        self.last_pose = p
        self.window.append((timestamp, p.copy()))
        window_seconds = max(self.config["ready_seconds"], self.config["hold_seconds"], self.config["return_seconds"])
        # Preserve the first frame just before the time boundary.
        while len(self.window) > 2 and self.window[1][0] < timestamp - window_seconds:
            self.window.popleft()
        if self.phase in ("hold", "return"):
            moved_from_end = self.endpoint_pose is None or _rms(p - self.endpoint_pose) > .12
            if moved_from_end:
                self.phase = "return"
            if self.phase == "return" and self._neutral(p) and self._stable(self.config["return_seconds"]):
                self.phase = "idle"
                self.scale = None
                self.measured_scales.clear()
                self.window.clear()
                self.start_pose = None
                return self._result(quality, "returned_to_neutral")
            return self._result(quality, "return_to_neutral_before_next_action")
        if self.phase in ("idle", "prepare"):
            self.measured_scales.append(measured)
            close = self._start_matches(p)
            self.phase = "prepare" if close else "idle"
            if close and self._stable(self.config["ready_seconds"]):
                self.scale = float(np.median(self.measured_scales))
                p, _, _ = normalize_frame(points17, width, height, self.scale, self.score_thresholds)
                # Use the observed stable past, without following future slow
                # motion and gradually absorbing it into the start reference.
                self.start_pose = np.median(np.stack([v for _, v in self.window]), axis=0)
                self.start_candidates = self._start_matches(self.start_pose)
                if not self.start_candidates:
                    self._prepare_again()
                    return self._result(quality,'prepared_start_not_consistent')
                self.ready_at = timestamp
                self.had_terminal_candidate = False
                self.missing_times = []
                self.phase = "ready"
                self.trajectory = [(timestamp, p.copy())]
                self.window.clear()
            return self._result(quality, "prepared_start_held" if self.phase == "ready" else "hold_a_prepared_start")
        if self.phase == "ready":
            moved = _rms(p - self.start_pose)
            self.trajectory.append((timestamp, p.copy()))
            progresses = [float(t.progress(p, self.start_pose)) for t in self.start_candidates]
            maximum_progress = max(progresses, default=-1.)
            if moved < self.config["onset_distance"] or maximum_progress < .08:
                # A bounded pre-roll retains slow onset without collecting an
                # arbitrarily long held start or causing a false time-out later.
                preroll = .15 if maximum_progress < .02 else .8
                while len(self.trajectory) > 2 and self.trajectory[1][0] < timestamp - preroll:
                    self.trajectory.pop(0)
                if moved > .25 and maximum_progress < -.03:
                    self._prepare_again()
                    return self._result(quality, 'preparation_adjusted_rearm')
                return self._result(quality, "waiting_for_directed_motion")
            directed = [t for t, progress in zip(self.start_candidates, progresses) if progress > .025]
            if not directed:
                # A small opposite adjustment is preparation, not a completed
                # failed cycle that would force the user to lower both hands.
                self._prepare_again()
                return self._result(quality, 'preparation_adjusted_rearm')
            self.start_candidates = directed
            self.phase = "execute"
            self.hold_start = None
            self.window.clear()
            return self._result(quality, "directed_motion_started")
        if self.phase == "execute":
            self.trajectory.append((timestamp, p.copy()))
            if timestamp - self.trajectory[0][0] > self.config["max_duration"]:
                self._discard("execution_timeout")
                return self._result(quality, self.last_rejection)
            terminals = self._endpoint_candidates(p)
            if not terminals:
                self.hold_start = None
                if not self.had_terminal_candidate and self._stable(self.config['ready_seconds']) and self._prepared_motion_still():
                    starts = self._start_matches(p)
                    # A preparation adjustment may have been measured from a
                    # neutral pose that happened to arm early. Judge this new
                    # held start against its actual template start, not the
                    # obsolete observed anchor; otherwise a long preparation
                    # hold remains an execution until it times out.
                    closest_start=min(starts,key=lambda v:v.pose_error(p,v.start)) if starts else None
                    advanced=float(closest_start.progress(p,closest_start.start)) if closest_start else 1.
                    new_start = starts and {v.action for v in starts}.isdisjoint({v.action for v in self.start_candidates})
                    if starts and (advanced < .25 or new_start):
                        self._prepare_again()
                        return self._result(quality, 'held_preparation_rearmed')
                return self._result(quality, "continue_to_terminal_pose")
            self.had_terminal_candidate = True
            if self.hold_start is None:
                self.hold_start = timestamp
                self.window.clear()
                self.window.append((timestamp, p.copy()))
                return self._result(quality, "terminal_pose_candidate")
            if self._terminal_stable(terminals):
                event, outcome = self._complete(timestamp, terminals)
                if event is None:
                    # An intermediate pause can resemble another terminal.
                    # A rejected prefix must not prevent the actual later end.
                    self.last_rejection = outcome
                    self.hold_start = None
                    self.window.clear()
                    return self._result(quality, outcome)
                self.phase = "hold"
                self.endpoint_pose = p.copy()
                self.trajectory = []
                self.window.clear()
                return self._result(quality, outcome, event)
            # A candidate endpoint can still move (especially FAST->SLOW). Slide
            # the dwell origin forward until the whole recent window is stable.
            if self.window and timestamp - self.hold_start > self.config["hold_seconds"] and not self._terminal_stable(terminals):
                self.hold_start = self.window[0][0]
            return self._result(quality, "waiting_for_terminal_stability")
        raise RuntimeError("invalid_engine_phase:" + self.phase)
