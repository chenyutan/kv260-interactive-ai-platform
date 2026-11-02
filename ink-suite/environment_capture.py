"""On-demand frames from the existing camera daemon; never a V4L2 owner.

Preview and photos are explicit requests. There is no environment render loop,
capture timer, retained raw-frame queue, or camera/file IO at construction.
"""
import math
import threading
import time

import numpy as np


class EnvironmentCapture:
    def __init__(self, snapshot, converter, mode, pose, stop):
        self.snapshot = snapshot
        self.converter = converter
        self.mode = mode
        self.pose = pose
        self.stop = stop
        self.busy = threading.Lock()

    def _epoch(self):
        state = self.mode()
        if self.stop.is_set() or not state['active'] or state['mode'] != 'environment' or state['menu']:
            return None
        return state['epoch']

    def _check(self, epoch):
        if self._epoch() != epoch:
            raise RuntimeError('Photograph canceled: environment page is not active')

    def _frame(self, metadata, raw, full_size, epoch):
        width, height, stride = (int(metadata[k]) for k in ('width', 'height', 'stride'))
        if (width, height) not in ((1920, 1080), (1280, 720), (960, 540)) or stride != width * 2 or len(raw) != stride * height:
            raise ValueError('Unsupported or incomplete YUYV snapshot')
        stamp = float(metadata['timestamp_seconds'])
        sequence = int(metadata['sequence'])
        if not math.isfinite(stamp) or sequence < 0:
            raise ValueError('Invalid capture timestamp or sequence')
        size = (width, height) if full_size else (320, round(height * 320 / width))
        # The converter creates an independent RGB buffer before raw mmap is
        # released. One lock prevents simultaneous photo/preview conversion.
        rgb = self.converter(raw, width, height, stride, size)
        points, pose_stamp = self.pose()
        age = (stamp - pose_stamp) * 1000 if pose_stamp is not None else -1.
        if points is None or not math.isfinite(age) or not 0 <= age <= 150:
            points, age = None, -1.
        else:
            points = np.asarray(points)
            points = np.nan_to_num(points, nan=-1., posinf=-1., neginf=-1.).tolist() if points.shape == (17, 3) else None
            if points is None:age = -1.
        self._check(epoch)
        # Timestamp-derived preview versions survive a camera sequence restart.
        version = int(round(stamp * 1_000_000))
        return (rgb.tobytes(), version, max(0., (time.monotonic() - stamp) * 1000),
                size[0], size[1], size[0] * 3, points, age, stamp, sequence)

    def preview(self, after=-1):
        epoch = self._epoch()
        if epoch is None or not self.busy.acquire(blocking=False):return None
        try:
            metadata, raw = self.snapshot(timeout=.7, copy=False)
            stamp = float(metadata['timestamp_seconds'])
            age = time.monotonic() - stamp
            version = int(round(stamp * 1_000_000)) if math.isfinite(stamp) else -1
            if not math.isfinite(age) or age < -.05 or age > .35 or version <= after:return None
            return self._frame(metadata, raw, False, epoch)[:8]
        finally:
            self.busy.release()

    def photograph(self):
        # Called by POST only, after the UI countdown. A cache/preview from
        # before this exact request must never be used as the photograph.
        requested = time.monotonic()
        epoch = self._epoch()
        if epoch is None:raise RuntimeError('Open the environment page before taking a photograph')
        if not self.busy.acquire(timeout=1.):raise RuntimeError('Camera snapshot is busy; please retry')
        try:
            deadline = requested + 3.
            while not self.stop.is_set():
                self._check(epoch)
                remaining = deadline - time.monotonic()
                if remaining <= 0:raise TimeoutError('No new camera frame after the shutter request')
                metadata, raw = self.snapshot(timeout=min(1., remaining), copy=False)
                stamp = float(metadata['timestamp_seconds'])
                now = time.monotonic()
                if math.isfinite(stamp) and requested <= stamp <= now + .05 and now - stamp <= .35:
                    return self._frame(metadata, raw, True, epoch)
                self.stop.wait(.01)
            raise RuntimeError('Camera service is stopping')
        finally:
            self.busy.release()
