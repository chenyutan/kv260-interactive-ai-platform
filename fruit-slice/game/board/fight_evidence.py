"""Opt-in evidence tap on the existing pose preview queue; never opens a camera.

A live control file is the recording authorization. No file means idle. Frame
data retains the immutable snapshot mapping until the bounded worker is done.
"""
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time

CONTROL = Path('/run/user/1000/ink-fight-evidence.json')


def owner_is_alive(pid):
    """Signal zero is a POSIX existence probe, but is unsafe on Windows."""
    if os.name != 'posix':
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class EvidenceTap:
    def __init__(self, control=CONTROL, *, clock=time.monotonic, resize=None, owner_alive=None):
        self.control = Path(control)
        self.clock = clock
        self.resize = resize
        self.owner_alive = owner_alive if owner_alive is not None else owner_is_alive
        self.pending = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = None
        self.next_at = 0.
        self.cached = None
        self.checked_at = -math.inf
        self.directory = None
        self.index = 0
        self.last_identity = None
        self.dropped = 0
        self.errors = 0

    def _read_control(self):
        try:
            if self.control.stat().st_size > 4096:
                return None
            value = json.loads(self.control.read_text(encoding='utf-8'))
            directory = Path(value['directory'])
            expires = float(value['expires_monotonic'])
            owner_pid = value['owner_pid']
            if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
                return None
            if not self.owner_alive(owner_pid):
                return None
            remaining = expires - self.clock()
            if not directory.is_absolute() or not math.isfinite(expires) or not 0 < remaining <= 1200:
                return None
            return {'directory': directory, 'expires_monotonic': expires, 'owner_pid': owner_pid}
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def active(self):
        now = self.clock()
        if now - self.checked_at >= .25:
            self.cached = self._read_control()
            self.checked_at = now
        if self.cached and now < self.cached['expires_monotonic']:
            return self.cached
        return None

    def submit(self, packet):
        control = self.active()
        now = self.clock()
        if self.stop.is_set() or not control or now < self.next_at:
            return False
        # The app schedules preview at 8 Hz already. Allow a little delivery
        # jitter here; a second strict 125-ms gate can accidentally halve it.
        self.next_at = now + .110
        metadata, raw, points, roi_index = packet
        identity = (str(control['directory']), metadata['sequence'], metadata['timestamp_seconds'])
        if identity == self.last_identity:
            return False
        # Mapping is immutable and reference-counted. Do not close or copy the
        # producer's raw data, and do not let disk work block inference.
        point_rows = points.tolist() if hasattr(points, 'tolist') else points
        point_rows = [[float(v) if math.isfinite(float(v)) else None for v in row]
                      for row in point_rows]
        record = dict(sequence=int(metadata['sequence']),
                      timestamp_seconds=float(metadata['timestamp_seconds']),
                      width=int(metadata['width']), height=int(metadata['height']),
                      stride=int(metadata['stride']),
                      flags=int(metadata['flags']) if metadata.get('flags') is not None else None,
                      points17=point_rows, nonfinite_points_encoded_as_null=True,
                      roi_index=int(roi_index), producer_monotonic=now,
                      pose_pid=os.getpid())
        try:
            self.pending.put_nowait((dict(control), record, raw))
        except queue.Full:
            self.dropped += 1
            return False
        self.last_identity = identity
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='fight-evidence', daemon=True)
            self.thread.start()
        return True

    def _write(self, control, record, raw):
        current = self._read_control()
        if self.stop.is_set() or not current or current != control:
            return
        from PIL import Image
        if self.resize is None:
            from movenet import YuyvPreprocessor
            self.resize = YuyvPreprocessor().resize
        started = self.clock()
        rgb = self.resize(raw, record['width'], record['height'], record['stride'], (640, 360))
        resized = self.clock()
        directory = control['directory']
        if directory != self.directory:
            self.directory = directory
            self.index = 0
        if self.index >= 1200:
            return
        directory.mkdir(parents=True, exist_ok=True)
        name = 'camera-%05d.jpg' % self.index
        self.index += 1
        # Exclusive creation prevents silently replacing earlier evidence.
        with (directory / name).open('xb') as stream:
            Image.fromarray(rgb, 'RGB').save(stream, format='JPEG', quality=85)
        finished = self.clock()
        record.update(file=name, recorded_size=[640, 360],
                      queue_wait_ms=(started-record['producer_monotonic'])*1000,
                      resize_ms=(resized-started)*1000,
                      jpeg_write_ms=(finished-resized)*1000,
                      written_monotonic=finished, dropped_frames=self.dropped,
                      writer_errors=self.errors,
                      source='pose_preview_queue_exact_inference_frame')
        with (directory / 'camera-frames.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')

    def _run(self):
        while not self.stop.is_set():
            try:
                control, record, raw = self.pending.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                self._write(control, record, raw)
            except Exception:
                self.errors += 1
                if self.errors <= 3:
                    logging.exception('Optional fight evidence write failed')
            finally:
                # Drop our reference; the preview worker may still own raw.
                raw = None
                self.pending.task_done()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2.)
        while True:
            try:
                self.pending.get_nowait()
                self.pending.task_done()
            except queue.Empty:
                break


class PreviewQueueTap:
    """Tap only consumer get(), never producer get_nowait() when discarding."""
    def __init__(self, original, evidence, args):
        self.original = original
        self.evidence = evidence
        self.args = args
        self.original_fps = args.preview_fps

    def get(self, *args, **kwargs):
        self.args.preview_fps = 8. if self.evidence.active() else self.original_fps
        packet = self.original.get(*args, **kwargs)
        try:
            self.evidence.submit(packet)
        except Exception:
            logging.exception('Optional evidence tap failed; preview continues')
        return packet

    def __getattr__(self, name):
        return getattr(self.original, name)


def install(app, control=CONTROL):
    """Install idle hook once. The short-lived control file is the only opt-in."""
    if getattr(app.LiveApp, '_fight_evidence_installed', False):
        return
    original = app.LiveApp.preview_worker

    def preview_worker(self):
        evidence = EvidenceTap(control)
        tap = PreviewQueueTap(self.preview_queue, evidence, self.args)
        self.preview_queue = tap
        try:
            return original(self)
        finally:
            self.args.preview_fps = tap.original_fps
            evidence.close()

    app.LiveApp.preview_worker = preview_worker
    app.LiveApp._fight_evidence_installed = True
