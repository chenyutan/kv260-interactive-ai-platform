"""Opt-in bounded event timeline; no serialization/storage on the GTK thread.

This is instrumentation, not an optimization. Wall and per-thread CPU clocks
are recorded together. Gaps outside callbacks are *unattributed*, not proof of
rendering or scheduling faults. A slow disk drops oldest records explicitly.
"""
from collections import deque
import gc
import itertools
import json
import os
from pathlib import Path
import threading
import time


CAPACITY = 16384
EVENT_FIELDS = {
    'timer_set': ('due', 'delay_ms'),
    'tick_begin': ('previous_interval', 'due_lateness', 'phase', 'game_time'),
    'tick_end': ('duration', 'phase', 'game_time', 'pause_reason'),
    'advance_begin': (), 'advance_end': (),
    'input_begin': (), 'input_end': ('status', 'fresh', 'sequence'),
    'game_begin': ('dt', 'held'), 'game_end': (),
    'metrics_begin': (), 'metrics_end': (),
    'draw_begin': ('phase', 'game_time'), 'compose_end': (),
    'convert_end': (), 'draw_end': ('duration',),
    'operation_queued': ('operation',), 'operation_begin': ('operation',),
    'poll_begin': ('generation',), 'poll_headers': (),
    'poll_decoded': ('sequence',), 'poll_end': ('published', 'ok'),
    'gc_start': ('generation',), 'gc_stop': ('generation', 'collected', 'uncollectable'),
}


class Timeline:
    """Fixed scalar tuples on producers; bounded batches on one writer.

    Producer locking is nonblocking, including reentrant GC callbacks. Lost
    records are counted, never allowed to queue unboundedly or block gameplay.
    close() never joins storage; finish waiting belongs after Gtk.main exits.
    """
    def __init__(self, path, *, capacity=CAPACITY, sink=None, observe_gc=True):
        if not 8 <= capacity <= 131072:
            raise ValueError('Timeline capacity must be between 8 and 131072')
        self.path = Path(path)
        self.capacity = capacity
        self._rows = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.finished = threading.Event()
        self.closing = False
        self.error = None
        self.submitted = self.written = self.overwritten = self.contended = 0
        self._sequence = itertools.count(1)
        self.main_thread = threading.get_ident()
        self._sink = sink
        self._gc_callback = self._gc if observe_gc else None
        if self._gc_callback is not None:
            gc.callbacks.append(self._gc_callback)
        self._thread = threading.Thread(target=self._run, name='sword-timeline-writer', daemon=True)
        self._thread.start()

    def mark(self, kind, *values, at=None):
        if self.closing:
            return
        fields = EVENT_FIELDS.get(kind)
        if fields is None or len(values) != len(fields):
            raise ValueError('Unknown timeline event or mismatched scalar fields: '+str(kind))
        # Do not retain mutable game/pose objects. No points or camera images
        # enter this trace. Validation is bounded by <=4 scalar values.
        if any(value is not None and not isinstance(value, (str, bool, int, float)) for value in values):
            raise ValueError('Timeline fields must be scalar')
        stamp = time.monotonic() if at is None else at
        cpu, thread = time.thread_time(), threading.get_ident()
        # Allocate GC-tracked containers before acquiring our short lock; GC
        # callbacks must not lose their own start event to that same lock.
        row = (next(self._sequence), kind, stamp, cpu, thread, values)
        if not self._lock.acquire(blocking=False):
            self.contended += 1
            return
        try:
            if self.closing:
                return
            self.submitted += 1
            if len(self._rows) == self.capacity:
                self.overwritten += 1
            self._rows.append(row)
        finally:
            self._lock.release()

    def _gc(self, phase, info):
        if phase == 'start':
            self.mark('gc_start', info.get('generation'))
        elif phase == 'stop':
            self.mark('gc_stop', info.get('generation'), info.get('collected'), info.get('uncollectable'))

    def close(self):
        self.closing = True
        if self._gc_callback is not None:
            try:
                gc.callbacks.remove(self._gc_callback)
            except ValueError:
                pass
            self._gc_callback = None
        self._wake.set()

    def summary(self):
        return dict(path=str(self.path), capacity=self.capacity, submitted=self.submitted,
                    written=self.written, overwritten=self.overwritten, contended=self.contended,
                    pending=len(self._rows), finished=self.finished.is_set(), error=self.error)

    def _run(self):
        output = None
        try:
            if self._sink is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                output = self.path.open('w', encoding='utf-8', buffering=65536)
                sink = lambda row: output.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n')
            else:
                sink = self._sink
            sink(dict(kind='header', schema='sword-timeline-v1', pid=os.getpid(),
                      main_thread=self.main_thread, capacity=self.capacity,
                      clock='monotonic seconds and per-thread CPU seconds', fields=EVENT_FIELDS,
                      note='Opt-in instrumentation; may affect timings. No physical scanout measurement.'))
            while True:
                rows = []
                with self._lock:
                    for _ in range(min(512, len(self._rows))):
                        rows.append(self._rows.popleft())
                for seq, kind, stamp, cpu, thread, values in rows:
                    sink(dict(kind=kind, seq=seq, at=stamp, cpu=cpu, thread=thread,
                              **dict(zip(EVENT_FIELDS[kind], values))))
                    self.written += 1
                if self.closing and not self._rows:
                    break
                if output is not None:
                    output.flush()
                # Yield between batches: JSON work is diagnostic load too.
                self._wake.wait(.1)
                self._wake.clear()
            summary = self.summary()
            summary['finished'] = True
            sink(dict(kind='footer', **summary))
        except Exception as error:
            self.error = str(error)
            self.close()
        finally:
            if output is not None:
                try:
                    output.close()
                except OSError as error:
                    self.error = str(error)
            self.finished.set()
