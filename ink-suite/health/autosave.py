"""Bounded, single-writer practice checkpoints and final report persistence.

The UI submits immutable statistics only. One writer serializes filesystem IO,
so an older checkpoint cannot replace a newer final record. Completion delivery
is drained by the UI; the worker never calls GTK or changes a lesson session.
"""
from collections import OrderedDict
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

_MONOTONIC = time.monotonic
MAX_REPORT_BYTES = 256 * 1024
MAX_PRACTICES = 8
_IDENT = re.compile(r'^[a-f0-9]{32}$')


def atomic_report(directory, report):
    """Write the existing per-course filename atomically, never an extra round."""
    identifier = report.get('session_id')
    course = report.get('course_id', 'baduanjin')
    if (not isinstance(identifier, str) or not _IDENT.fullmatch(identifier)
            or course not in ('baduanjin', 'taichi')
            or report.get('kind') != course + '_practice'):
        raise ValueError('Invalid practice report identity')
    payload = json.dumps(report, ensure_ascii=False, indent=2,
                         allow_nan=False).encode('utf8')
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError('Practice report exceeds the statistics limit')
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Practice report directory cannot be a link')
    directory.mkdir(parents=True, exist_ok=True)
    name = ('taichi-' if course == 'taichi' else '') + identifier
    destination = directory/(name + '.json')
    descriptor, temporary = tempfile.mkstemp(prefix='.' + name + '-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if hasattr(os, 'O_DIRECTORY'):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return destination


class ReportWriter:
    """At most one pending snapshot per practice, with final sealing."""
    def __init__(self, notify=None):
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._results = OrderedDict()
        self._current = None
        self._sealed = OrderedDict()
        self._revision = 0
        self._closed = False
        self._thread = None
        self._notify = notify

    def submit(self, directory, report, *, final=False, save=None):
        # Freeze on the caller; the worker cannot observe later lesson changes.
        snapshot = copy.deepcopy(report)
        payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode('utf8')
        if len(payload) > MAX_REPORT_BYTES:
            raise ValueError('Practice report exceeds the statistics limit')
        identifier = snapshot.get('session_id')
        if not isinstance(identifier, str) or not _IDENT.fullmatch(identifier):
            raise ValueError('Invalid practice report identity')
        with self._condition:
            if self._closed:
                raise RuntimeError('Practice report writer is closed')
            if identifier in self._sealed and not final:
                return None
            occupied = set(self._pending) | set(self._results)
            if self._current is not None:
                occupied.add(self._current['session_id'])
            if identifier not in occupied and len(occupied) >= MAX_PRACTICES:
                raise RuntimeError('Practice report writer queue is full')
            self._revision += 1
            revision = self._revision
            if final:
                self._sealed[identifier] = True
                # The view accepts requests only for its current session. Keep
                # recent final seals bounded while all in-flight practices
                # remain protected; there can be at most MAX_PRACTICES of them.
                while len(self._sealed) > 128:
                    oldest = next(iter(self._sealed))
                    if oldest in occupied:
                        self._sealed.move_to_end(oldest)
                    else:
                        self._sealed.pop(oldest)
            self._pending[identifier] = dict(
                session_id=identifier, revision=revision, final=bool(final),
                report=snapshot, directory=str(directory), save=save or atomic_report)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='health-autosave', daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return revision

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if not self._pending:
                    return
                _, job = self._pending.popitem(last=False)
                self._current = job
            result = {key:value for key,value in job.items() if key != 'save'}
            try:
                directory = Path(job['directory'])
                if directory.is_symlink():
                    raise ValueError('Practice report directory cannot be a link')
                path = job['save'](directory, copy.deepcopy(job['report']))
                result.update(path=str(path), error=None, saved_at=_MONOTONIC())
            except Exception as error:
                result.update(path=None, error=str(error), saved_at=None)
            with self._condition:
                self._current = None
                self._results[job['session_id']] = result
                self._condition.notify_all()
            if self._notify:
                try:
                    self._notify()
                except Exception:
                    # A torn-down GTK loop cannot undo a durable filesystem write.
                    pass

    def results(self):
        with self._condition:
            rows = list(self._results.values())
            self._results.clear()
            return rows

    def flush(self, timeout=1.5):
        """Tests/shutdown only: bounded wait, never used by a normal UI action."""
        deadline = _MONOTONIC() + max(0., float(timeout))
        with self._condition:
            while self._current is not None or self._pending:
                remaining = deadline - _MONOTONIC()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, timeout=1.5):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            worker = self._thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(max(0., float(timeout)))
        return worker is None or not worker.is_alive()

    def status(self):
        with self._condition:
            return dict(pending=len(self._pending), writing=self._current is not None,
                        results=len(self._results), queue_limit=MAX_PRACTICES,
                        worker_alive=bool(self._thread and self._thread.is_alive()),
                        closed=self._closed)
