"""Recorded COCO17 replay on loopback; never opens a camera or production API.

The recording is a sampled HTTP state log, not every camera frame. Preserve
observed arrival gaps, repeated source keys, original source timestamp deltas,
confidence, discontinuities and menu-candidate flags. Only clock origin and
process namespace change; no interpolation or fresh frames after EOF.
"""
import argparse
import bisect
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


MAX_BYTES = 64*1024*1024
MAX_ROWS = 20000


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class RecordedPoseSource:
    def __init__(self, path, start=0.):
        self.path = Path(path)
        if not finite(start) or start < 0:
            raise ValueError('Recording start must be finite and nonnegative')
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError('Recording exceeds 64 MiB bound')
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.rows = []
        for line in raw.decode('utf-8-sig').splitlines():
            row = json.loads(line)
            if not isinstance(row, dict) or not finite(row.get('test_elapsed')):
                raise ValueError('Every recording row needs finite test_elapsed')
            if self.rows and row['test_elapsed'] < self.rows[-1]['test_elapsed']:
                raise ValueError('Recording arrival times must not roll back')
            if 'test_error' not in row and (not finite(row.get('timestamp_seconds')) or row.get('sequence') is None):
                raise ValueError('State row is missing source timestamp or sequence')
            self.rows.append(row)
            if len(self.rows) > MAX_ROWS:
                raise ValueError('Recording exceeds 20000 row bound')
        if not self.rows:
            raise ValueError('Recording is empty')
        self.times = [row['test_elapsed']-self.rows[0]['test_elapsed'] for row in self.rows]
        if start >= self.times[-1]:
            raise ValueError('Recording start must precede the final recorded arrival')
        self.start = float(start)
        self.duration = self.times[-1]-self.start
        # An affine shift retains source timestamp rollback/restart evidence;
        # it does not manufacture an index timestamp for repeated observations.
        first = next((r for r in self.rows if finite(r.get('timestamp_seconds'))), None)
        if first is None:
            raise ValueError('Recording has no source timestamps')
        first_time = first['test_elapsed']-self.rows[0]['test_elapsed']
        self.source_anchor = first['timestamp_seconds']-first_time+self.start
        self.origin = None
        self.cached_index = self.cached_state = None

    def begin(self, now):
        if self.origin is not None:
            return
        if not finite(now):
            raise ValueError('Replay origin must be finite')
        self.origin = now

    def state(self, now=None):
        now = time.monotonic() if now is None else now
        if self.origin is None or now < self.origin:
            return dict(available=False, stale=True, replay_waiting=True)
        position = self.start + now-self.origin
        index = max(0, bisect.bisect_right(self.times, position)-1)
        if index == self.cached_index:
            return self.cached_state
        original = self.rows[index]
        result = deepcopy(original)
        if 'test_error' in result:
            result.update(available=False, stale=True)
        else:
            result['pid'] = 'recorded-'+str(original.get('pid'))
            result['timestamp_seconds'] = self.origin+original['timestamp_seconds']-self.source_anchor
            suite = result.get('suite') or {}
            result['suite'] = dict(suite, active=True, mode='sword', menu=False)
        result['replay'] = dict(recorded=True, row=index, source_elapsed=self.times[index],
                               input_is_sampled_not_all_frames=True)
        self.cached_index, self.cached_state = index, result
        return result

    def description(self):
        return dict(recording=str(self.path), sha256=self.sha256, rows=len(self.rows),
                    recording_start=self.start, seconds_available=self.duration,
                    source='Recorded sampled COCO17 state, no interpolation or new EOF frames',
                    rebasing='Affine monotonic clock shift; namespaced PID; original sequence retained',
                    suite_override='Run Sword alone; active=true, mode=sword, menu=false; control_active retained',
                    original_system_load_reproduced=False, camera_accessed=False)


class ReplayServer:
    """Separate server process avoids doing fixture JSON work on Sword's GIL.

    begin() only queues a scalar command. Pipe writes happen on a worker,
    startup/cleanup waits happen outside GTK. Only 127.0.0.1 is exposed.
    """
    def __init__(self, recording, start=0.):
        from .benchmark_danmaku import read_host_ready
        self.requests = self.bytes = 0
        self.origin = None
        self.commands = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.process = subprocess.Popen([sys.executable, '-u', '-m', 'sword.replay',
            '--serve', str(Path(recording).resolve()), '--start', str(start)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            # Same bounded newline protocol reader; port occupies socket_id.
            info = read_host_ready(self.process.stdout.fileno())
            if info['host_pid'] != self.process.pid:
                raise RuntimeError('Replay server PID does not match its launched process')
            self.base = 'http://127.0.0.1:%d' % info['socket_id']
            self.description = info['recording']
        except Exception:
            self.process.terminate()
            self.process.communicate(timeout=3)
            raise
        self.worker = threading.Thread(target=self._commands, args=(self.process,),
                                       name='sword-replay-control', daemon=True)
        self.worker.start()

    def _commands(self, process):
        while not self.closed.is_set():
            try:
                op = self.commands.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                process.stdin.write((json.dumps(op)+'\n').encode('utf-8'))
                process.stdin.flush()
            except (OSError, ValueError):
                return
            if op['op'] == 'stop':
                return

    def begin(self, now):
        if self.origin is None:
            self.origin = now
            self.commands.put_nowait(dict(op='begin', at=now))

    def start(self):
        pass  # Process and bounded readiness handshake already completed.

    def close(self):
        if self.process is None:
            return
        try:
            self.commands.put_nowait(dict(op='stop'))
        except queue.Full:
            # No GTK caller reaches cleanup; don't wait for a slow pipe worker.
            self.closed.set()
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        output = self.process.stdout.read().decode('utf-8')
        error = self.process.stderr.read().decode('utf-8', errors='replace')
        self.error = error or None
        for line in output.splitlines():
            row = json.loads(line)
            if row.get('event') == 'replay_closed':
                self.requests, self.bytes = row['requests'], row['bytes']
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        self.closed.set()
        self.process = None


def serve(path, start):
    import os
    source = RecordedPoseSource(path, start)
    stats = dict(requests=0, bytes=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/api/state':
                self.send_error(404)
                return
            payload = json.dumps(source.state(), separators=(',', ':')).encode('utf-8')
            stats['requests'] += 1
            stats['bytes'] += len(payload)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
    worker.start()
    print(json.dumps(dict(socket_id=server.server_port, host_pid=os.getpid(),
                          recording=source.description())), flush=True)
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command.get('op') == 'stop':
                break
            if command.get('op') == 'begin':
                source.begin(command['at'])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(1.)
        print(json.dumps(dict(event='replay_closed', **stats)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', type=Path, required=True)
    parser.add_argument('--start', type=float, default=0.)
    args = parser.parse_args()
    serve(args.serve, args.start)
