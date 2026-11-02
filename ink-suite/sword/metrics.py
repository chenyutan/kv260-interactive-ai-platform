"""Optional diagnostics with one pending snapshot and no UI-thread disk I/O.

The game submits detached scalar metrics. A slow filesystem can delay or drop
diagnostics, but cannot queue old frames or make shutdown wait for storage.
"""
import json
from pathlib import Path
import sys
import threading


class LatestMetricsWriter:
    def __init__(self, path, *, sink=None):
        self.path = Path(path)
        self._sink = sink if sink is not None else self._write
        self._condition = threading.Condition()
        self._pending = None
        self._closing = False
        self.error = None
        self.submitted = self.replaced = self.written = 0
        self.finished = threading.Event()
        self._thread = threading.Thread(target=self._run,
            name='sword-metrics-writer', daemon=True)
        self._thread.start()

    def submit(self, snapshot):
        """Replace the sole pending scalar snapshot; never wait for the sink."""
        values = dict(snapshot)
        with self._condition:
            if self._closing:
                return False
            self.submitted += 1
            if self._pending is not None:
                self.replaced += 1
            self._pending = values
            self._condition.notify()
        return True

    def close(self):
        """Discard unwritten diagnostics; an in-flight OS write is not joined."""
        with self._condition:
            self._closing = True
            self._pending = None
            self._condition.notify()

    def _write(self, snapshot):
        # Serialization, mkdir, file creation and rename all run in this
        # worker. Readers continue to see a whole previous or new JSON file.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closing or self._pending is not None)
                    if self._closing:
                        return
                    values, self._pending = self._pending, None
                # Never hold the publication lock while serializing or doing
                # filesystem work. At most one write and one pending item exist.
                self._sink(values)
                with self._condition:
                    self.written += 1
        except Exception as error:
            with self._condition:
                self.error = str(error)
                self._closing = True
                self._pending = None
            try:
                print('sword metrics: ' + str(error), file=sys.stderr, flush=True)
            except (OSError, ValueError):
                pass
        finally:
            self.finished.set()
