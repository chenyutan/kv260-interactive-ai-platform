"""Bounded audio discovery and a host-lifetime silent Cube output stream.

Public keeper controls only update cached state and wake one worker. Neither GI
imports nor subprocesses run on the caller/GTK thread. The stream targets a
PulseAudio sink linked to a verified physical Cube USB device; it never changes
the default sink, volume, music position, or any application's audio pipeline.

Silence prevents PulseAudio's idle suspension while this host is running. Some
speaker firmware may ignore silent samples for its own standby timer: this
module deliberately reports that limit and does not introduce audible tones.
"""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time


MAX_DEVICES = 128
MAX_PACTL_BYTES = 512 * 1024
CUBE_VENDOR = '2dc8'
KNOWN_CUBE_PRODUCTS = frozenset(('2054', '2056'))


def _read(path):
    try:
        with Path(path).open('r', encoding='utf-8', errors='replace') as source:
            return source.read(512).strip()
    except OSError:
        return ''


def _usb_devices(sysfs):
    result = {}
    base = sysfs / 'bus' / 'usb' / 'devices'
    for path in sorted(base.glob('*'))[:MAX_DEVICES]:
        vendor = _read(path / 'idVendor').lower()
        product_id = _read(path / 'idProduct').lower()
        product = _read(path / 'product')
        # The same vendor also makes controllers. A product ID alone must not
        # turn a controller or an incompletely enumerated dongle into a speaker.
        is_cube = vendor == CUBE_VENDOR and bool(re.search(r'\bcube\b', product, re.I))
        if not vendor or not product_id:
            continue
        resolved = path.resolve()
        try:
            inode = resolved.stat().st_ino
        except OSError:
            continue
        busnum, devnum = _read(path / 'busnum'), _read(path / 'devnum')
        result[str(resolved)] = {
            'path': str(resolved), 'usb_path': path.name,
            'vendor_id': vendor, 'product_id': product_id, 'product': product,
            'serial': _read(path / 'serial'), 'is_cube': is_cube,
            'known_product_id': product_id in KNOWN_CUBE_PRODUCTS,
            'instance': '%s:%s:%s:%s' % (path.name, busnum, devnum, inode),
            'card_paths': [],
        }
    for card in sorted((sysfs / 'class' / 'sound').glob('card*'))[:MAX_DEVICES]:
        if not card.name[4:].isdigit():
            continue
        device = (card / 'device').resolve()
        for ancestor in (device, *device.parents):
            physical = result.get(str(ancestor))
            if physical is not None:
                physical['card_paths'].append(str(device))
                break
    return result


def _card_usb(card, sysfs, usb):
    if not str(card).isdigit():
        return None
    path = (sysfs / 'class' / 'sound' / ('card' + str(card)) / 'device').resolve()
    # Exact ancestor matching is required. Pulse display names and properties
    # alone cannot authorize the stream, and a missing sysfs card is rejected.
    for ancestor in (path, *path.parents):
        found = usb.get(str(ancestor))
        if found is not None:
            return dict(found, card_path=str(path))
    return None


def _pulse_environment():
    """Resolve this user's desktop Pulse socket without changing process env."""
    environment = os.environ.copy()
    uid = getattr(os, 'getuid', lambda: None)()
    runtime = environment.get('XDG_RUNTIME_DIR')
    if not runtime and uid is not None:
        candidate = Path('/run/user') / str(uid)
        if candidate.is_dir():
            runtime = str(candidate)
            environment['XDG_RUNTIME_DIR'] = runtime
    if runtime:
        socket = Path(runtime) / 'pulse' / 'native'
        if not environment.get('PULSE_SERVER') and socket.exists():
            environment['PULSE_SERVER'] = 'unix:' + str(socket)
        bus = Path(runtime) / 'bus'
        if not environment.get('DBUS_SESSION_BUS_ADDRESS') and bus.exists():
            environment['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(bus)
    return environment


def _pulse_sinks(timeout):
    # Temporary files bound resident memory and avoid PIPE backpressure. pactl
    # has a timeout; only a bounded amount of its output is accepted/read.
    import tempfile
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        process = subprocess.run(['pactl', '--format=json', 'list', 'sinks'],
                                 stdout=output, stderr=error, timeout=timeout,
                                 check=False, env=_pulse_environment())
        if process.returncode:
            error.seek(0)
            detail = error.read(512).decode('utf-8', 'replace').strip()
            raise RuntimeError(detail or 'PulseAudio is unavailable')
        if output.tell() > MAX_PACTL_BYTES:
            raise RuntimeError('Audio inventory exceeds the output limit')
        output.seek(0)
        result = json.loads(output.read(MAX_PACTL_BYTES).decode('utf-8'))
    if not isinstance(result, list) or len(result) > MAX_DEVICES:
        raise ValueError('Invalid or excessive PulseAudio inventory')
    return result


def inventory(*, sysfs_root='/sys', sink_reader=None, timeout=2.0):
    """Read-only snapshot of audio outputs and physically connected Cube USB.

    Call on a worker. `available` describes PulseAudio discovery, while a Cube
    with `connected=True, playable=False` is still enumerating or lacks a sink.
    Dependency injection permits tests without audio hardware or Linux/GI.
    """
    sysfs = Path(sysfs_root)
    usb = _usb_devices(sysfs)
    result = {'available': False, 'devices': [], 'error': '',
              'sampled_at': time.time()}
    try:
        sinks = (sink_reader or _pulse_sinks)(timeout)
        if not isinstance(sinks, list) or len(sinks) > MAX_DEVICES:
            raise ValueError('Invalid or excessive PulseAudio inventory')
        result['available'] = True
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        sinks = []
        result['error'] = str(exc)[:512]
        # A timed-out pactl call may have taken two seconds. Refresh physical
        # evidence now so a USB unplug during that wait cannot preserve a stale
        # output in the keeper's fallback path.
        usb = _usb_devices(sysfs)
    represented = set()
    for sink in sinks:
        if not isinstance(sink, dict) or not isinstance(sink.get('name'), str):
            continue
        properties = sink.get('properties', {})
        if not isinstance(properties, dict):
            properties = {}
        physical = _card_usb(properties.get('alsa.card', ''), sysfs, usb)
        if physical:
            represented.add(physical['path'])
        active_port = sink.get('active_port')
        ports = sink.get('ports', [])
        ports = ports if isinstance(ports, list) else []
        port = next((p for p in ports if isinstance(p, dict) and p.get('name') == active_port), {})
        port_missing = port.get('availability') in ('not available', 'no')
        is_cube = bool(physical and physical['is_cube'])
        name = str(sink.get('description') or sink['name'])[:256]
        result['devices'].append({
            'id': 'pulse:' + sink['name'], 'name': name, 'kind': 'audio',
            'connected': not port_missing, 'playable': not port_missing,
            'is_cube': is_cube, 'sink': sink['name'],
            'state': str(sink.get('state', 'UNKNOWN')),
            'usb_path': physical['usb_path'] if physical else None,
            'usb_instance': physical['instance'] if physical else None,
            'card_path': physical.get('card_path') if physical else None,
            'card_paths': physical['card_paths'] if physical else [],
            'pulse_index': sink.get('index'),
            'vendor_id': physical['vendor_id'] if physical else str(properties.get('device.vendor.id', '')),
            'product_id': physical['product_id'] if physical else str(properties.get('device.product.id', '')),
            'transport': 'usb' if physical else str(properties.get('device.bus', 'internal')),
        })
    for physical in usb.values():
        if not physical['is_cube'] or physical['path'] in represented:
            continue
        result['devices'].append({
            'id': 'usb:' + physical['usb_path'], 'name': physical['product'],
            'kind': 'audio', 'connected': True, 'playable': False,
            'is_cube': True, 'sink': None, 'state': 'ENUMERATING',
            'usb_path': physical['usb_path'], 'vendor_id': physical['vendor_id'],
            'product_id': physical['product_id'], 'transport': 'usb',
            'usb_instance': physical['instance'], 'card_paths': physical['card_paths'],
            'card_path': None, 'pulse_index': None,
        })
    return result


class _SilentPulseBackend:
    """One worker-owned, clocked zero-PCM stream to one explicit Pulse sink."""
    def __init__(self, sink):
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        self.Gst = Gst
        self.pipeline = Gst.Pipeline.new('inksuite-cube-keepalive')
        try:
            source = Gst.ElementFactory.make('audiotestsrc', 'silence')
            caps = Gst.ElementFactory.make('capsfilter', None)
            output = Gst.ElementFactory.make('pulsesink', 'cube-output')
            if any(item is None for item in (self.pipeline, source, caps, output)):
                raise RuntimeError('GStreamer silent PulseAudio output unavailable')
            source.set_property('wave', 4)  # GstAudioTestSrcWave.SILENCE
            source.set_property('volume', 0.0)
            source.set_property('is-live', True)
            source.set_property('samplesperbuffer', 4800)
            caps.set_property('caps', Gst.Caps.from_string(
                'audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2'))
            output.set_property('device', sink)
            server = _pulse_environment().get('PULSE_SERVER')
            if server and output.find_property('server'):
                output.set_property('server', server)
            output.set_property('sync', True)
            if output.find_property('buffer-time'):
                output.set_property('buffer-time', 200000)
            if output.find_property('latency-time'):
                output.set_property('latency-time', 100000)
            for item in (source, caps, output):
                self.pipeline.add(item)
            if not source.link(caps) or not caps.link(output):
                raise RuntimeError('Cannot link silent Cube output')
            self.bus = self.pipeline.get_bus()
            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Silent Cube output could not start')
        except Exception:
            self.close()
            raise

    def poll(self):
        G = self.Gst
        message = self.bus.pop_filtered(G.MessageType.ERROR | G.MessageType.EOS)
        if message is None:
            return
        if message.type == G.MessageType.EOS:
            raise RuntimeError('Silent Cube output stopped')
        error, _detail = message.parse_error()
        raise RuntimeError(str(error.message))

    def close(self):
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None


class AudioKeepalive:
    """Nonblocking host controls; retries absent/replugged Cube on one worker.

    `status()['inventory']` is the cached scan for the connectivity UI. Controls
    acknowledge a request; `active` means the silent stream started, not proof
    of a firmware standby exemption. `close()` does not join the GTK thread.
    """
    def __init__(self, enabled=True, *, inventory_fn=None, backend_factory=None,
                 scan_interval=3.0, retry_interval=3.0):
        if not 0.05 <= scan_interval <= 60 or not 0.05 <= retry_interval <= 60:
            raise ValueError('Keepalive intervals must be between 0.05 and 60 seconds')
        self._inventory = inventory_fn or inventory
        self._factory = backend_factory or _SilentPulseBackend
        self._scan_interval = float(scan_interval)
        self._retry_interval = float(retry_interval)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._closed = threading.Event()
        self._thread = None
        self._retry_serial = 0
        self._status = {
            'enabled': bool(enabled), 'running': False,
            'state': 'stopped', 'sink': None, 'device_id': None,
            'device_name': None, 'last_error': '', 'attempts': 0,
            'reconnects': 0, 'policy': 'silent',
            'firmware_standby': 'unverified',
            'discovery_error': '',
            'inventory': {'available': False, 'devices': [], 'error': '', 'sampled_at': None},
        }

    def start(self):
        with self._lock:
            if self._stopping.is_set():
                return False
            if self._thread is None:
                self._status['running'] = True
                self._status['state'] = 'waiting' if self._status['enabled'] else 'disabled'
                self._thread = threading.Thread(target=self._run,
                    name='inksuite-cube-keepalive', daemon=True)
                self._thread.start()
        self._wake.set()
        return True

    def retry(self):
        with self._lock:
            if self._stopping.is_set():
                return False
            self._retry_serial += 1
        self._wake.set()
        return self.start()

    def set_enabled(self, enabled):
        with self._lock:
            if self._stopping.is_set():
                return False
            self._status['enabled'] = bool(enabled)
            self._retry_serial += 1
        self._wake.set()
        return True

    def status(self):
        with self._lock:
            return copy.deepcopy(self._status)

    def close(self):
        self._stopping.set()
        self._wake.set()
        with self._lock:
            if self._thread is None:
                self._status['running'] = False
                self._status['state'] = 'closed'
                self._closed.set()

    def wait_closed(self, timeout=3.0):
        """Test/process-shutdown helper; do not call on the GTK thread."""
        return self._closed.wait(timeout)

    def _update(self, **values):
        with self._lock:
            self._status.update(values)

    @staticmethod
    def _close_backend(backend):
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass

    @staticmethod
    def _physical_still_present(selected, devices):
        """A Pulse timeout alone must not destroy an existing verified stream.

        Require the same sysfs instance (inode/bus/device number) and exact
        ALSA device path. A merely identical display name or USB port is unsafe
        after unplug/replug. No preservation occurs when physical data is absent.
        """
        if not selected or not selected.get('usb_instance') or not selected.get('card_path'):
            return False
        return any(isinstance(d, dict) and d.get('is_cube') and d.get('connected')
            and d.get('usb_path') == selected.get('usb_path')
            and d.get('usb_instance') == selected['usb_instance']
            and selected['card_path'] in d.get('card_paths', []) for d in devices)

    @staticmethod
    def _drop_retained_inventory(snapshot):
        if not snapshot.get('discovery_stale'):
            return snapshot
        devices = []
        for device in snapshot['devices']:
            if device.get('discovery_stale'):
                device = dict(device, id='usb:' + device['usb_path'],
                    playable=False, sink=None, card_path=None, pulse_index=None,
                    state='ENUMERATING', discovery_stale=False)
            devices.append(device)
        return dict(snapshot, discovery_stale=False, devices=devices)

    def _run(self):
        backend, selected = None, None
        next_scan, next_attempt, retry_serial = 0.0, 0.0, -1
        snapshot = {'available': False, 'devices': [], 'error': '', 'sampled_at': None}
        successful_starts = 0
        try:
            while not self._stopping.is_set():
                now = time.monotonic()
                with self._lock:
                    enabled = self._status['enabled']
                    requested_retry = self._retry_serial
                if requested_retry != retry_serial:
                    retry_serial = requested_retry
                    next_scan = next_attempt = 0.0
                if now >= next_scan:
                    try:
                        snapshot = self._inventory()
                        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('devices'), list):
                            raise ValueError('Invalid audio inventory snapshot')
                    except Exception as exc:
                        snapshot = {'available': False, 'devices': [],
                                    'error': str(exc)[:512], 'sampled_at': time.time()}
                    next_scan = time.monotonic() + self._scan_interval
                    candidates = [d for d in snapshot['devices'] if isinstance(d, dict)
                        and d.get('is_cube') and d.get('connected') and d.get('playable')
                        and isinstance(d.get('sink'), str) and d['sink']]
                    candidate = next((d for d in candidates if selected and d.get('id') == selected.get('id')),
                                     candidates[0] if candidates else None)
                    # Discovery can time out under CPU/I/O load while the
                    # worker-owned Pulse stream remains healthy. Preserve it
                    # only with fresh, matching physical USB/card evidence;
                    # bus polling below still handles real pipeline failures.
                    if (not snapshot.get('available') and backend is not None
                            and self._physical_still_present(selected, snapshot['devices'])):
                        try:
                            backend.poll()
                        except Exception:
                            self._close_backend(backend)
                            backend = None
                        else:
                            candidate = selected
                            retained = dict(selected, discovery_stale=True,
                                            discovery_error=snapshot.get('error', ''),
                                            state='STREAMING_CACHED')
                            # Keep the UI's confirmed physical output row while
                            # explicitly marking Pulse discovery as stale. This
                            # is valid only with both current USB/card identity
                            # and a healthy owned backend; an absent USB never
                            # produces a retained row.
                            snapshot = dict(snapshot, discovery_stale=True,
                                devices=[d for d in snapshot['devices'] if not
                                    (isinstance(d, dict) and d.get('is_cube')
                                     and d.get('usb_path') == selected.get('usb_path'))]
                                    + [retained])
                    # A changing card/sink after re-enumeration must replace the
                    # old stream; Pulse may otherwise move it to a default sink.
                    if selected and (not candidate or any(candidate.get(key) != selected.get(key)
                            for key in ('sink', 'usb_instance', 'card_path', 'pulse_index'))):
                        self._close_backend(backend)
                        backend = None
                        next_attempt = 0.0
                    selected = candidate
                    self._update(inventory=copy.deepcopy(snapshot),
                                 discovery_error=snapshot.get('error', '') if not snapshot.get('available') else '')
                if self._stopping.is_set():
                    break
                # Inventory may take up to its timeout. Honor a disable request
                # received during the scan before constructing a stream.
                with self._lock:
                    enabled = self._status['enabled']
                if not enabled:
                    self._close_backend(backend)
                    backend = None
                    self._update(state='disabled', sink=None, device_id=None,
                                 device_name=None, last_error='')
                elif selected is None:
                    self._close_backend(backend)
                    backend = None
                    self._update(state='waiting', sink=None, device_id=None,
                                 device_name=None, last_error=snapshot.get('error', ''))
                elif backend is None and time.monotonic() >= next_attempt:
                    with self._lock:
                        self._status['attempts'] += 1
                    try:
                        backend = self._factory(selected['sink'])
                        backend.poll()
                        successful_starts += 1
                        self._update(state='active', sink=selected['sink'],
                                     device_id=selected.get('id'), device_name=selected.get('name'),
                                     last_error='', reconnects=max(0, successful_starts - 1))
                    except Exception as exc:
                        self._close_backend(backend)
                        backend = None
                        next_attempt = time.monotonic() + self._retry_interval
                        self._update(state='error', sink=None, device_id=selected.get('id'),
                                     device_name=selected.get('name'), last_error=str(exc)[:512])
                elif backend is not None:
                    try:
                        backend.poll()
                    except Exception as exc:
                        self._close_backend(backend)
                        backend = None
                        next_scan = 0.0
                        next_attempt = time.monotonic() + self._retry_interval
                        snapshot = self._drop_retained_inventory(snapshot)
                        self._update(state='error', sink=None, last_error=str(exc)[:512],
                                     inventory=copy.deepcopy(snapshot))
                delay = min(0.25, max(0.01, next_scan - time.monotonic()))
                self._wake.wait(delay)
                self._wake.clear()
        finally:
            self._close_backend(backend)
            self._update(running=False, state='closed', sink=None,
                         device_id=None, device_name=None)
            self._closed.set()
