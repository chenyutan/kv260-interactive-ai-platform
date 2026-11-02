"""One ordered action job overlaps the next pose job; no DPU in this worker."""
import importlib.util
import multiprocessing
import os
from pathlib import Path
import sys
import time


def _snapshot(engine):
    return dict(phase=engine.phase, last_rejection=engine.last_rejection,
                prepared_actions=sorted({t.action for b in engine._branches for t in b.start_candidates}))


def _worker(connection, engine_root, templates, calibration, config):
    try:
        # Finish numeric deserialization startup before declaring ready.
        # Otherwise the first array can pay NumPy import after the handshake.
        import numpy
        root = Path(engine_root)
        spec = importlib.util.spec_from_file_location('action_process_engine', root / 'flow_action_engine.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        engine = module.ActionEngine(templates, calibration, config=config)
        connection.send(('ready', dict(pid=os.getpid(), config=engine.config,
            enabled_actions=list(engine.enabled_actions), readiness_errors=engine.readiness_errors,
            snapshot=_snapshot(engine))))
        while True:
            request = connection.recv()
            if request[0] == 'close':
                break
            if request[0] == 'reset':
                engine.reset()
                connection.send(('reset', _snapshot(engine)))
                continue
            _, sequence, points, timestamp, width, height, metadata = request
            wall = time.perf_counter_ns(); cpu = time.thread_time_ns()
            result = engine.update(points, timestamp, width, height)
            connection.send(('result', dict(sequence=sequence, result=result, metadata=metadata,
                snapshot=_snapshot(engine), wall_ms=(time.perf_counter_ns()-wall)/1e6,
                cpu_ms=(time.thread_time_ns()-cpu)/1e6, pid=os.getpid())))
    except EOFError:
        pass
    except BaseException as exc:
        try: connection.send(('error', repr(exc)))
        except (OSError, EOFError): pass
    finally:
        connection.close()


class ActionProcess:
    """At most one outstanding job, never skip or reorder an accepted pose."""
    def __init__(self, engine_root, templates, calibration, config, timeout=3):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        self.connection = parent; self.pending = None; self.sequence = 0
        self.timeout = timeout; self.closed = False
        self.process = context.Process(target=_worker,
            args=(child, str(engine_root), templates, calibration, config), daemon=True)
        self.process.start(); child.close()
        try:
            self.info = self._receive('ready', 20)
            self.snapshot = self.info['snapshot']
        except BaseException:
            self.close(); raise

    def _receive(self, expected, timeout=None):
        if not self.connection.poll(self.timeout if timeout is None else timeout):
            raise TimeoutError('Action worker did not complete '+expected)
        try: kind, value = self.connection.recv()
        except (EOFError, OSError) as exc:
            raise RuntimeError('Action worker disconnected') from exc
        if kind == 'error': raise RuntimeError('Action worker failed: '+value)
        if kind != expected: raise RuntimeError('Unexpected action protocol: '+kind)
        return value

    def submit(self, points, timestamp, width, height, metadata):
        if self.closed: raise RuntimeError('Action worker is closed')
        if self.pending is not None: raise RuntimeError('Previous action has not been consumed')
        self.sequence += 1
        self.connection.send(('update', self.sequence, points, timestamp, width, height, dict(metadata)))
        self.pending = self.sequence

    def finish(self):
        if self.pending is None: return None
        value = self._receive('result')
        if value['sequence'] != self.pending: raise RuntimeError('Action result order changed')
        self.pending = None; self.snapshot = value['snapshot']
        return value

    def finish_ready(self):
        if self.pending is None or not self.connection.poll(0): return None
        return self.finish()

    def reset(self):
        # Consume and deliberately discard an old epoch before acknowledging reset.
        self.finish()
        self.connection.send(('reset',))
        self.snapshot = self._receive('reset')

    def close(self):
        if self.closed: return
        self.closed = True
        try:
            self.connection.send(('close',))
            self.process.join(.5)
        except (OSError, EOFError): pass
        if self.process.is_alive():
            self.process.terminate(); self.process.join(1)
        if self.process.is_alive():
            self.process.kill(); self.process.join(1)
        self.connection.close()
