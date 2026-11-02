"""Live-clocked Cube output for non-rhythm games.

Discovery, decoding and output operations stay on AudioDirector's PCM worker.
Public controls and diagnostics only access cached state. A failed output may
retry during authorized playback; pausing never authorizes a new audio stream.
"""
from collections import deque
import math
import threading
import time

from .menu_music import _physical_matches, _select_cube
from .runtime import AudioDirector, GstBackend


def _inventory():
    from connectivity.audio_device import inventory
    return inventory()


def _server():
    from connectivity.audio_device import _pulse_environment
    return _pulse_environment().get('PULSE_SERVER')


class _TrackedOutput:
    """Record one failure per output instance without changing backend controls."""
    def __init__(self, output, owner):
        self._output, self._owner = output, owner
        self._failed = False

    def __getattr__(self, name):
        return getattr(self._output, name)

    def _call(self, operation, *args):
        try:
            return getattr(self._output, operation)(*args)
        except Exception as error:
            if not self._failed:
                self._failed = True
                self._owner._record_failure(operation, error)
            raise

    def resume(self):
        result = self._call('resume')
        self._owner._record_output_ready()
        return result

    def poll(self):
        return self._call('poll')

    def writable(self):
        return self._call('writable')

    def push(self, pcm):
        return self._call('push', pcm)

    def pause(self):
        return self._call('pause')

    def close(self):
        return self._output.close()


class CubeGameAudioDirector(AudioDirector):
    """AudioDirector-compatible output for fruit, fight and sword.

    These BGM/SFX streams follow gameplay, rather than being rhythm clocks.
    A retry reuses the decoded mixer and its logical music position. Failed
    attempts are spaced by at least three seconds and only retried while the
    caller authorizes playback. Never select another speaker as a fallback.
    """
    MODES = frozenset(('fruit', 'fight', 'sword', 'platformer', 'runner'))

    def __init__(self, mode, *, enabled=True, asset_dir=None, inventory_fn=None,
                 physical_checker=None, backend_factory=None, clock=time.monotonic,
                 retry_interval=3.0, server_provider=None, max_voices=6, queue_limit=32):
        if mode not in self.MODES:
            raise ValueError('Cube game output supports fruit, fight, sword, platformer and runner only')
        if (isinstance(retry_interval, bool) or not isinstance(retry_interval, (int, float))
                or not math.isfinite(retry_interval) or retry_interval < 3.0):
            raise ValueError('Game output retry interval must be at least 3 seconds')
        self._route_lock = threading.RLock()
        self._inventory_fn = inventory_fn or _inventory
        self._physical_checker = physical_checker or _physical_matches
        self._output_factory = backend_factory or GstBackend
        self._server_provider = server_provider or _server
        self._clock = clock
        self._retry_interval = float(retry_interval)
        self._next_retry = 0.0
        self._automatic_retries = 0
        self._output_attempts = 0
        self._route = None
        self._stream_name = 'InkSuite ' + mode + ' audio'
        self._output_failures = 0
        self._output_recoveries = 0
        self._last_output_error = None
        self._last_output_failure_at = None
        self._last_output_recovery_at = None
        self._awaiting_recovery = False
        self._recent_failures = deque(maxlen=8)
        super().__init__(mode, enabled=enabled, sink='pulsesink', asset_dir=asset_dir,
                         backend_factory=self._create_backend,
                         max_voices=max_voices, queue_limit=queue_limit, block_frames=1440)

    def _record_failure(self, operation, error):
        # Called only by output creation/operations on the PCM worker. Retain
        # history after AudioDirector clears its active error during retry.
        with self._route_lock:
            now = self._clock()
            # A stream can fail long after its creation deadline. Start a
            # fresh cooldown at the fault, so repeated resume ticks cannot
            # immediately reopen it or trigger a transient fault storm.
            self._next_retry = max(self._next_retry, now + self._retry_interval)
            self._output_failures += 1
            self._last_output_error = str(error)[:400]
            self._last_output_failure_at = now
            self._awaiting_recovery = True
            self._recent_failures.append(dict(serial=self._output_failures, at=now,
                operation=operation, error=self._last_output_error,
                target_sink=self._route['sink'] if self._route else None,
                target_usb_instance=self._route['usb_instance'] if self._route else None))

    def _record_output_ready(self):
        with self._route_lock:
            if self._awaiting_recovery:
                self._output_recoveries += 1
                self._last_output_recovery_at = self._clock()
                self._awaiting_recovery = False

    def _create_backend(self, sink):
        # Factory invoked after decoding on the PCM worker. Do not hold the
        # route lock across discovery, filesystem checks or output creation.
        with self._route_lock:
            self._output_attempts += 1
            self._next_retry = max(self._next_retry, self._clock() + self._retry_interval)
            self._route = None
        operation = 'discovery'
        try:
            route = _select_cube(self._inventory_fn())
            if route is None or route.get('discovery_stale') or not self._physical_checker(route):
                raise RuntimeError('No verified Cube available for ' + self.mode + ' audio')
            with self._route_lock:
                self._route = dict(route)
            operation = 'create'
            output = self._output_factory(sink, device=route['sink'],
                server=self._server_provider(), route_guard=lambda: self._physical_checker(route),
                stream_name=self._stream_name, live_timestamps=True,
                block_frames=self.block_frames)
            return _TrackedOutput(output, self)
        except Exception as error:
            self._record_failure(operation, error)
            raise

    def start(self):
        with self._route_lock:
            state = super().status()
            if (state['enabled'] and state['state'] == 'unavailable' and state['pcm_bytes']
                    and self._clock() >= self._next_retry):
                if super().retry():
                    self._automatic_retries += 1
                    self._next_retry = self._clock() + self._retry_interval
            return super().start()

    resume = start

    def pause(self):
        with self._route_lock:
            super().pause()

    def stop(self):
        with self._route_lock:
            super().stop()

    def close(self):
        with self._route_lock:
            super().close()

    def retry(self):
        # Explicit retry does not authorize playback while a menu owns pause.
        with self._route_lock:
            self._next_retry = self._clock() + self._retry_interval
            return super().retry()

    def status(self):
        with self._route_lock:
            result = super().status()
            result.update(target_sink=self._route['sink'] if self._route else None,
                target_usb_instance=self._route['usb_instance'] if self._route else None,
                stream_name=self._stream_name, live_timestamps=True,
                output_attempts=self._output_attempts,
                automatic_retries=self._automatic_retries,
                retry_interval_seconds=self._retry_interval, next_retry_at=self._next_retry,
                output_failure_count=self._output_failures,
                output_recovery_count=self._output_recoveries,
                last_output_error=self._last_output_error,
                last_output_failure_at=self._last_output_failure_at,
                last_output_recovery_at=self._last_output_recovery_at,
                recent_output_failures=[dict(row) for row in self._recent_failures])
            return result
