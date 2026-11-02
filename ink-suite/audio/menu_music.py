"""Host main-menu BGM; cached discovery in, one explicitly routed worker out.

The host decides when its main menu is visible. Feed existing connectivity
snapshots to update_inventory(); this controller never runs pactl on GTK.
Leaving the menu requests a flush/pause. status()['silenced'] acknowledges that
the PCM worker has completed it, so a host can resume a game without overlap.
"""
from pathlib import Path
import re
import threading
import time

from .runtime import AudioDirector, GstBackend


def _physical_matches(route):
    """Fresh, narrowly scoped USB identity check, only on the PCM worker."""
    from connectivity.audio_device import _read
    path = Path('/sys/bus/usb/devices') / route['usb_path']
    try:
        physical = path.resolve(strict=True)
        instance = '%s:%s:%s:%s' % (route['usb_path'], _read(path/'busnum'),
                                    _read(path/'devnum'), physical.stat().st_ino)
        return (instance == route['usb_instance'] and
                _read(path/'idVendor').lower() == '2dc8' and
                bool(re.search(r'\bcube\b', _read(path/'product'), re.I)) and
                Path(route['card_path']).exists())
    except OSError:
        return False


def _select_cube(snapshot):
    if not isinstance(snapshot, dict):
        return None
    audio = snapshot.get('audio', snapshot)
    if not isinstance(audio, dict):
        return None
    inv = audio.get('inventory', audio)
    if not isinstance(inv, dict):
        return None
    devices = inv.get('devices', [])
    if not isinstance(devices, list):
        return None
    for item in devices[:128]:
        if not isinstance(item, dict):
            continue
        if not (item.get('is_cube') and item.get('connected') and item.get('playable') and
                item.get('vendor_id') == '2dc8'):
            continue
        names = ('sink', 'usb_path', 'usb_instance', 'card_path')
        if not all(isinstance(item.get(key), str) and item[key] and len(item[key]) <= 1024 and '\0' not in item[key] for key in names):
            continue
        if not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*', item['usb_path']):
            continue
        return {key: item.get(key) for key in (*names, 'pulse_index', 'discovery_stale')}
    return None


def _identity(route):
    return tuple(route.get(key) for key in ('sink', 'usb_instance', 'card_path', 'pulse_index')) if route else None


class MenuMusic:
    """All public controls are nonblocking; no decoding, GI or subprocesses.

    Use set_menu_open(menu_open and menu_level == 'main' and not switching).
    update_inventory accepts a full ConnectivityController snapshot, its audio
    section, or audio_device.inventory(). Failed output retries are spaced at
    least retry_interval seconds and only occur when a snapshot arrives while
    the menu is open. Asset failures wait for explicit retry or a new device.
    """
    def __init__(self, *, enabled=True, asset_dir=None, master=.55, music=.48,
                 retry_interval=5.0, backend_factory=None, physical_checker=None,
                 clock=time.monotonic):
        self._lock = threading.RLock()
        self._route = None
        self._menu_open = False
        self._closed = False
        self._clock = clock
        self._retry_interval = max(1.0, float(retry_interval))
        self._next_retry = 0.0
        self._retry_count = 0
        self._backend_factory = backend_factory or GstBackend
        self._physical_checker = physical_checker or _physical_matches
        self._director = AudioDirector('ink', enabled=enabled, sink='pulsesink',
                                       asset_dir=asset_dir, music_only=True,
                                       backend_factory=self._create_backend)
        self._director.set_levels(master=master, music=music, effects=0.)

    def _create_backend(self, sink):
        # AudioDirector invokes this only on its worker, after decoding.
        from connectivity.audio_device import _pulse_environment
        with self._lock:
            route = dict(self._route) if self._route else None
            allowed = self._menu_open and not self._closed
        if not allowed or route is None or route.get('discovery_stale') or not self._physical_checker(route):
            raise RuntimeError('No verified Cube available for menu music')
        return self._backend_factory(sink, device=route['sink'],
                                     server=_pulse_environment().get('PULSE_SERVER'),
                                     route_guard=lambda: self._physical_checker(route),
                                     stream_name='InkSuite menu music', live_timestamps=True)

    def set_menu_open(self, value):
        with self._lock:
            if self._closed:
                return False
            self._menu_open = bool(value)
            if self._menu_open and self._route:
                self._director.resume()
            else:
                self._director.pause()
            return True

    def update_inventory(self, snapshot):
        route = _select_cube(snapshot)
        with self._lock:
            if self._closed:
                return False
            changed = _identity(route) != _identity(self._route)
            self._route = route
            if changed:
                self._director.pause()
                self._director.retry()
                self._next_retry = self._clock() + self._retry_interval
            state = self._director.status()
            if not self._menu_open or route is None:
                self._director.pause()
            else:
                if (not changed and state['state'] == 'unavailable' and
                        state['pcm_bytes'] and self._clock() >= self._next_retry):
                    self._retry_locked()
                self._director.resume()
            return route is not None

    def _retry_locked(self):
        self._next_retry = self._clock() + self._retry_interval
        self._retry_count += 1
        return self._director.retry()

    def retry(self):
        with self._lock:
            if self._closed:
                return False
            result = self._retry_locked()
            if self._menu_open and self._route:
                self._director.resume()
            else:
                self._director.pause()
            return result

    def status(self):
        with self._lock:
            result = self._director.status()
            result.update(menu_open=self._menu_open, connected=self._route is not None,
                          target_sink=self._route['sink'] if self._route else None,
                          stream_name='InkSuite menu music', retries=self._retry_count,
                          discovery_stale=bool(self._route and self._route.get('discovery_stale')),
                          silenced=(result['desired'] != 'playing' and not result['output_active'] and
                                    (not result['worker_alive'] or result['state'] in ('closed', 'disabled') or
                                     result['acknowledged_flush'] == result['flush_generation'])),
                          firmware_standby='unverified')
            return result

    def close(self):
        with self._lock:
            self._closed = True
            self._menu_open = False
            self._route = None
            self._director.close()

    def wait_closed(self, timeout=2.):
        """Tests/process shutdown only; never call from a GTK callback."""
        return self._director.wait_closed(timeout)
