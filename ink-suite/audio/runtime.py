"""One worker, one output stream, bounded events and PCM voices.

No GI import, file decode, pipeline operation or join runs in a public control
method. A director belongs to one scene; call close() when that scene is removed.
"""
from array import array
from collections import OrderedDict, deque
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
import wave

from .mixer import Clip, Mixer, valid_gain

RATE = 48000
CHANNELS = 2
BLOCK_FRAMES = 480  # 10 ms; independent of the renderer's frame rate.
MUSIC_IDS = {"ink": "bgm_ink", "fruit": "bgm_fruit", "fight": "bgm_fight",
             "sword": "bgm_sword", "taiko": None, "platformer": "bgm_platformer", "runner": "bgm_runner"}
MAX_PCM_BYTES = 96 * 1024 * 1024
MAX_FILE_BYTES = 48 * 1024 * 1024
EVENT_TTL = 0.25


class _Cancelled(Exception):
    pass


def _load_assets(root, mode, cancelled, include_effects=True):
    """Read/verify only on the worker; reject paths outside the audio directory."""
    root = root.resolve()
    manifest_path = root / 'manifest.json'
    if manifest_path.stat().st_size > 256 * 1024:
        raise ValueError('Audio manifest exceeds 256 KiB')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or manifest.get('sample_rate') != RATE or manifest.get('channels') != CHANNELS:
        raise ValueError('Expected audio manifest schema 1, 48000 Hz, stereo')
    selected = MUSIC_IDS[mode]
    tracks, effects = {}, {}
    total = 0
    for group, output in (('tracks', tracks), ('effects', effects)):
        entries = manifest.get(group, {})
        if not isinstance(entries, dict):
            raise ValueError('Audio manifest groups must be dictionaries')
        if group == 'tracks':
            entries = {selected: entries[selected]} if selected else {}
        elif not include_effects:
            entries = {}
        for clip_id, entry in entries.items():
            if cancelled():
                raise _Cancelled()
            path = (root / entry['file']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Audio file path escapes asset directory')
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('Audio file exceeds 48 MiB: ' + clip_id)
            digest = hashlib.sha256()
            with path.open('rb') as source:
                while True:
                    data = source.read(1024 * 1024)
                    if not data:
                        break
                    digest.update(data)
                    if cancelled():
                        raise _Cancelled()
            expected = entry.get('sha256', '')
            if not re.fullmatch('[0-9a-fA-F]{64}', expected) or digest.hexdigest() != expected.lower():
                raise ValueError('Audio checksum mismatch: ' + clip_id)
            with wave.open(str(path), 'rb') as source:
                if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (RATE, CHANNELS, 2, 'NONE'):
                    raise ValueError('Expected PCM16 stereo 48000 Hz: ' + clip_id)
                size = source.getnframes() * 4
                total += size
                if not size or total > MAX_PCM_BYTES:
                    raise ValueError('Audio PCM is empty or exceeds 96 MiB total')
                data = source.readframes(source.getnframes())
                if len(data) != size:
                    raise ValueError('Truncated PCM file: ' + clip_id)
            samples = array('h')
            samples.frombytes(data)
            if sys.byteorder != 'little':
                samples.byteswap()
            output[clip_id] = Clip(samples, valid_gain(entry.get('gain', 1.0)),
                                  bool(entry.get('loop', False)), int(entry.get('priority', 0)),
                                  max(0, min(60000, int(entry.get('cooldown_ms', 0)))))
    return Mixer(tracks, effects, selected), total


class MemoryBackend:
    """Explicit silent test backend; stores only the last four PCM blocks."""
    name = 'memory'

    def __init__(self, sink='memory'):
        self.blocks = deque(maxlen=4)
        self.frames = 0
        self.closed = False
        self.playing = False
        self.flushes = 0

    def resume(self):
        self.playing = True

    def pause(self):
        self.playing = False
        self.blocks.clear()
        self.flushes += 1
        return 0

    def poll(self):
        pass

    def writable(self):
        return True

    def push(self, pcm):
        self.blocks.append(pcm)
        self.frames += len(pcm) // 4

    def close(self):
        self.closed = True
        self.playing = False
        self.blocks.clear()


class GstBackend:
    """Appsrc on one worker; no GLib loop or per-effect player process required."""
    def __init__(self, sink, *, device=None, server=None, route_guard=None,
                 stream_name=None, live_timestamps=False, block_frames=BLOCK_FRAMES):
        if isinstance(block_frames, bool) or not isinstance(block_frames, int) or not 240 <= block_frames <= 1920:
            raise ValueError('Expected audio blocks of 240..1920 frames')
        if (device is not None or server is not None or route_guard is not None) and sink != 'pulsesink':
            raise ValueError('Explicit Pulse routing requires pulsesink')
        if device is not None and (not isinstance(device, str) or not device or len(device) > 512 or '\0' in device):
            raise ValueError('Expected a nonempty Pulse sink name')
        if server is not None and (not isinstance(server, str) or not server or len(server) > 2048 or '\0' in server):
            raise ValueError('Expected a Pulse server address')
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        self.Gst = Gst
        self.name = 'gstreamer-' + sink
        self.pipeline = Gst.Pipeline.new('inksuite-audio')
        self.source = Gst.ElementFactory.make('appsrc', 'mixed-pcm')
        convert = Gst.ElementFactory.make('audioconvert', None)
        resample = Gst.ElementFactory.make('audioresample', None)
        output = Gst.ElementFactory.make(sink, 'audio-output')
        if any(item is None for item in (self.pipeline, self.source, convert, resample, output)):
            raise RuntimeError('GStreamer appsrc/audio output elements unavailable')
        self.output, self.device = output, device
        self.live_timestamps = bool(live_timestamps)
        self._submitted = deque(maxlen=64)
        self._route_guard = route_guard
        self._next_route_check = 0.0
        if device is not None:
            output.set_property('device', device)
        if server is not None:
            output.set_property('server', server)
        if stream_name is not None:
            properties = Gst.Structure.new_empty('props')
            properties.set_value('media.name', stream_name)
            properties.set_value('application.name', stream_name)
            properties.set_value('media.role', 'music')
            output.set_property('client-name', stream_name)
            output.set_property('stream-properties', properties)
        self.frames = 0
        self.block_frames = block_frames
        self.max_bytes = block_frames * 4 * 3
        self.source.set_property('caps', Gst.Caps.from_string('audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2'))
        self.source.set_property('is-live', True)
        self.source.set_property('do-timestamp', self.live_timestamps)
        self.source.set_property('format', Gst.Format.TIME)
        self.source.set_property('block', False)
        self.source.set_property('max-bytes', self.max_bytes)
        self.source.set_property('emit-signals', False)
        # Describe the bounded live producer's latency so GstBaseSink can
        # negotiate its processing deadline without an extra queue element.
        self.source.set_property('min-latency', 0 if self.live_timestamps else block_frames * Gst.SECOND // RATE)
        self.source.set_property('max-latency', 3 * block_frames * Gst.SECOND // RATE)
        if output.find_property('sync'):
            output.set_property('sync', True)
        if output.find_property('buffer-time'):
            output.set_property('buffer-time', 40000)
        if output.find_property('latency-time'):
            output.set_property('latency-time', 10000)
        for item in (self.source, convert, resample, output):
            self.pipeline.add(item)
        if not self.source.link(convert) or not convert.link(resample) or not resample.link(output):
            self.close()
            raise RuntimeError('Cannot link GStreamer audio pipeline')
        self.bus = self.pipeline.get_bus()

    def resume(self):
        self._check_route(force=True)
        if self.pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Audio output could not enter PLAYING')

    def _check_route(self, force=False):
        # Runs on the PCM worker. Pulse may rescue an unplugged stream onto a
        # fallback sink; stop feeding it as soon as that move/device loss is seen.
        now = time.monotonic()
        if self._route_guard is not None and (force or now >= self._next_route_check):
            self._next_route_check = now + .05
            if not self._route_guard():
                raise RuntimeError('Verified audio device disconnected or changed')
        if self.device is not None:
            # pulsesink updates `device` when Pulse moves a stream. This local
            # property avoids current-device's synchronous Pulse round trips.
            if self.output.get_property('device') != self.device:
                raise RuntimeError('Pulse moved audio away from the verified device')

    def pause(self):
        # READY flushes appsrc and downstream audio. Preserve the audible stream
        # position by rewinding PCM rendered ahead of the pipeline position.
        self.pipeline.set_state(self.Gst.State.PAUSED)
        ok, position = self.pipeline.query_position(self.Gst.Format.TIME)
        if self.live_timestamps:
            pending = self._pending_live_frames(position) if ok and position >= 0 else 0
        else:
            pending = max(0, self.frames - int(position * RATE // self.Gst.SECOND)) if ok and position >= 0 else 0
        self.pipeline.set_state(self.Gst.State.READY)
        self.frames = 0
        self._submitted.clear()
        return pending

    def _running_time(self):
        clock = self.pipeline.get_clock()
        return max(0, clock.get_time() - self.pipeline.get_base_time()) if clock else 0

    def _pending_live_frames(self, position):
        # Live PTS can jump after a scheduler stall. Translate the recent output
        # timeline back to logical music frames instead of treating PTS as a
        # continuously generated frame counter. At most 64 blocks are retained.
        for pts, start, count in reversed(self._submitted):
            if position >= pts:
                played = start + min(count, (position - pts) * RATE // self.Gst.SECOND)
                return max(0, self.frames - int(played))
        return max(0, self.frames - self._submitted[0][1]) if self._submitted else 0

    def poll(self):
        self._check_route()
        G = self.Gst
        while True:
            message = self.bus.pop_filtered(G.MessageType.ERROR | G.MessageType.EOS | G.MessageType.WARNING)
            if message is None:
                return
            if message.type == G.MessageType.EOS:
                raise RuntimeError('Audio output stopped unexpectedly')
            # autoaudiosink can fall back to a dummy sink with a WARNING. Surface
            # that failure rather than reporting a working physical output.
            error, detail = message.parse_error() if message.type == G.MessageType.ERROR else message.parse_warning()
            raise RuntimeError(str(error.message))

    def writable(self):
        # current-level-bytes exists since GStreamer 1.2. The sole producer
        # checks it before each fixed block, so block=False cannot grow a queue.
        return self.source.get_property('current-level-bytes') + self.block_frames * 4 <= self.max_bytes

    def push(self, pcm):
        self._check_route()
        G = self.Gst
        buffer = G.Buffer.new_allocate(None, len(pcm), None)
        buffer.fill(0, pcm)
        if self.live_timestamps:
            # Let live appsrc use the current pipeline clock. Frame-count PTS
            # falls permanently behind after CPU/GIL stalls while the producer
            # discards missed deadlines, eventually making every buffer late.
            buffer.pts = G.CLOCK_TIME_NONE
            submitted_pts = self._running_time()
        else:
            buffer.pts = self.frames * G.SECOND // RATE
        buffer.duration = (len(pcm) // 4) * G.SECOND // RATE
        result = self.source.emit('push-buffer', buffer)
        if result != G.FlowReturn.OK:
            raise RuntimeError('Audio output rejected PCM: ' + str(result))
        if self.live_timestamps:
            self._submitted.append((submitted_pts, self.frames, len(pcm) // 4))
        self.frames += len(pcm) // 4

    def close(self):
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)


class AudioDirector:
    """Fast thread-safe scene controls. `status()` is a cached dictionary.

    `play` acknowledges queue admission, not speaker playback. `start`/`resume`
    and `retry` are asynchronous. `close` never joins on the GTK thread;
    `wait_closed` is provided for tests and non-UI process shutdown only.
    """

    def __init__(self, mode, enabled=True, sink='autoaudiosink', *, asset_dir=None,
                 max_voices=6, queue_limit=32, backend_factory=None, music_only=False,
                 block_frames=BLOCK_FRAMES):
        if mode not in MUSIC_IDS:
            raise ValueError('Unsupported audio mode: ' + str(mode))
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', sink):
            raise ValueError('sink must be one GStreamer element factory name')
        if not 1 <= max_voices <= 8 or not 1 <= queue_limit <= 128:
            raise ValueError('Expected 1..8 voices and 1..128 queued effects')
        if isinstance(block_frames, bool) or not isinstance(block_frames, int) or not 240 <= block_frames <= 1920:
            raise ValueError('Expected audio blocks of 240..1920 frames')
        self.mode, self.enabled, self.sink = mode, bool(enabled), sink
        self.asset_dir = Path(asset_dir) if asset_dir is not None else Path(__file__).parent
        self.max_voices, self.queue_limit = max_voices, queue_limit
        self.music_only = bool(music_only)
        self.block_frames = block_frames
        self._backend_factory = backend_factory or (MemoryBackend if sink == 'memory' else
            lambda output: GstBackend(output, block_frames=self.block_frames))
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        self._desired = 'stopped'
        self._closed = False
        self._flush = 0
        self._reset = 0
        self._retry = 0
        self._events = deque()
        self._keys = OrderedDict()
        self._levels = dict(music=0.65, effects=0.8, master=0.8)
        self._muted = False
        self._stats = dict(state='disabled' if not enabled else 'stopped', error=None,
                           backend=None, ready=False, voices=0, position_seconds=0.0,
                           accepted=0, dropped=0, duplicate=0, expired=0, cooldown=0,
                           stolen=0, rejected=0, pcm_bytes=0, mixer=None,
                           output_active=False, acknowledged_flush=-1)

    def _ensure_thread_locked(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='InkSuiteAudio-' + self.mode, daemon=True)
            self._thread.start()

    def start(self):
        with self._lock:
            if self._closed or not self.enabled:
                return False
            if self._desired == 'playing' and self._thread is not None:
                return True
            self._desired = 'playing'
            self._ensure_thread_locked()
        self._wake.set()
        return True

    resume = start

    def pause(self):
        with self._lock:
            if self._closed or self._desired == 'paused':
                return
            self._desired = 'paused'
            self._flush += 1
            self._events.clear()
            self._keys.clear()
        self._wake.set()

    def stop(self):
        with self._lock:
            if self._closed or self._desired == 'stopped':
                return
            self._desired = 'stopped'
            self._reset += 1
            self._flush += 1
            self._events.clear()
            self._keys.clear()
        self._wake.set()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._desired = 'closed'
            self._flush += 1
            self._events.clear()
            self._keys.clear()
            if self._thread is None:
                self._stats.update(state='closed', ready=False)
        self._wake.set()

    def wait_closed(self, timeout=2.0):
        """Test/shutdown helper; never call from a GTK callback."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def play(self, effect_id, key=None):
        if not isinstance(effect_id, str) or not effect_id or len(effect_id) > 128:
            return False
        if key is not None:
            key = str(key)
            if len(key) > 256:
                key = hashlib.sha256(key.encode('utf-8')).hexdigest()
        with self._lock:
            if self._closed or not self.enabled or self._desired != 'playing' or self._stats['state'] == 'unavailable':
                return False
            identity = (effect_id, key) if key is not None else None
            if identity is not None and identity in self._keys:
                self._stats['duplicate'] += 1
                return False
            if len(self._events) >= self.queue_limit:
                self._stats['dropped'] += 1
                return False
            self._events.append((effect_id, time.monotonic(), self._flush))
            if identity is not None:
                self._keys[identity] = None
                while len(self._keys) > 256:
                    self._keys.popitem(last=False)
            self._stats['accepted'] += 1
        self._wake.set()
        return True

    def set_levels(self, music=None, effects=None, master=None):
        values = {name: valid_gain(value) for name, value in
                  (('music', music), ('effects', effects), ('master', master)) if value is not None}
        with self._lock:
            self._levels.update(values)
        self._wake.set()

    def mute(self, muted):
        with self._lock:
            self._muted = bool(muted)
        self._wake.set()

    def retry(self):
        with self._lock:
            if self._closed or not self.enabled:
                return False
            self._retry += 1
            self._flush += 1
            self._events.clear()
            self._keys.clear()
            self._ensure_thread_locked()
        self._wake.set()
        return True

    def status(self):
        with self._lock:
            result = dict(self._stats)
            result.update(mode=self.mode, enabled=self.enabled, sink=self.sink,
                          desired=self._desired, levels=dict(self._levels), muted=self._muted,
                          queued=len(self._events), queue_limit=self.queue_limit,
                          max_voices=self.max_voices, key_count=len(self._keys),
                          block_frames=self.block_frames,
                          worker_alive=bool(self._thread and self._thread.is_alive()),
                          flush_generation=self._flush)
            return result

    def _publish(self, **values):
        with self._lock:
            self._stats.update(values)

    def _run(self):
        mixer = backend = None
        previous_flush = previous_reset = previous_retry = -1
        failed = running = False
        cooldowns = {}
        next_block = 0.0
        full_since = None
        try:
            while True:
                self._wake.clear()
                with self._lock:
                    desired, closed = self._desired, self._closed
                    flush, reset, retry = self._flush, self._reset, self._retry
                    levels, muted = dict(self._levels), self._muted
                if closed:
                    break
                try:
                    if reset != previous_reset:
                        if backend is not None:
                            backend.close()
                        backend = mixer = None
                        running = failed = False
                        previous_reset = reset
                        self._publish(ready=False, voices=0, position_seconds=0.0, pcm_bytes=0,
                                      backend=None, mixer=None, error=None, output_active=False)
                    if retry != previous_retry:
                        if backend is not None:
                            if running:
                                mixer.rewind_music(backend.pause())
                            backend.close()
                        backend = None
                        running = failed = False
                        previous_retry = retry
                        self._publish(error=None)
                    if flush != previous_flush:
                        if mixer is not None:
                            mixer.clear_effects()
                        cooldowns.clear()
                        if backend is not None and running:
                            pending = backend.pause()
                            if mixer is not None:
                                mixer.rewind_music(pending)
                        running = False
                        previous_flush = flush
                        self._publish(voices=0, output_active=False, acknowledged_flush=flush)
                    if failed:
                        self._wake.wait()
                        continue
                    if desired == 'stopped':
                        self._publish(state='stopped', ready=False)
                        self._wake.wait()
                        continue
                    if desired == 'paused':
                        self._publish(state='paused', voices=0,
                                      position_seconds=mixer.music_position / RATE if mixer else 0.0)
                        self._wake.wait()
                        continue
                    if mixer is None:
                        self._publish(state='loading', ready=False, error=None)
                        def cancelled():
                            with self._lock:
                                return self._closed or self._reset != reset or self._desired != 'playing'
                        if self.music_only:
                            mixer, pcm_bytes = _load_assets(self.asset_dir, self.mode, cancelled,
                                                           include_effects=False)
                        else:
                            mixer, pcm_bytes = _load_assets(self.asset_dir, self.mode, cancelled)
                        mixer.max_voices = self.max_voices
                        self._publish(pcm_bytes=pcm_bytes, mixer='numpy' if mixer.np is not None else 'stdlib')
                        # Re-read controls before touching the output: a scene may
                        # have been paused/closed while the worker loaded assets.
                        continue
                    if backend is None:
                        backend = self._backend_factory(self.sink)
                        self._publish(backend=backend.name)
                    if not running:
                        with self._lock:
                            # Output creation can finish after a pause/close.
                            # Reserve active status before releasing this lock
                            # so the public pause barrier cannot see stale idle.
                            if self._closed or self._desired != 'playing' or self._flush != flush:
                                continue
                            self._stats['output_active'] = True
                        backend.resume()
                        running = True
                        next_block = time.monotonic()
                        full_since = None
                    backend.poll()
                    now = time.monotonic()
                    if now < next_block:
                        self._wake.wait(min(0.01, next_block - now))
                        continue
                    if not backend.writable():
                        full_since = now if full_since is None else full_since
                        if now - full_since > 1.0:
                            raise RuntimeError('Audio output did not consume PCM for one second')
                        self._wake.wait(0.005)
                        continue
                    full_since = None
                    with self._lock:
                        # Do not drain events under an obsolete pause generation.
                        if self._flush != flush or self._desired != 'playing' or self._closed:
                            continue
                        events = list(self._events)
                        self._events.clear()
                    counts = dict(expired=0, cooldown=0, rejected=0)
                    for effect_id, timestamp, generation in events:
                        if generation != flush or now - timestamp > EVENT_TTL:
                            counts['expired'] += 1
                            continue
                        clip = mixer.effects.get(effect_id)
                        if clip is None:
                            counts['rejected'] += 1
                        elif now < cooldowns.get(effect_id, 0.0):
                            counts['cooldown'] += 1
                        elif mixer.play(effect_id):
                            cooldowns[effect_id] = now + clip.cooldown_ms / 1000.0
                        else:
                            counts['rejected'] += 1
                    pcm = mixer.render(self.block_frames, levels['music'], levels['effects'], levels['master'], muted)
                    backend.push(pcm)
                    next_block = max(next_block + self.block_frames / RATE, now)
                    with self._lock:
                        for name, count in counts.items():
                            self._stats[name] += count
                        self._stats.update(state='playing', ready=True, error=None,
                                           voices=len(mixer.voices), stolen=mixer.stolen,
                                           position_seconds=mixer.music_position / RATE)
                except _Cancelled:
                    mixer = None
                    continue
                except Exception as exc:
                    if backend is not None:
                        try:
                            if running and mixer is not None:
                                mixer.rewind_music(backend.pause())
                        except Exception:
                            pass
                        # A failed pause must not skip the owned output teardown.
                        try:
                            backend.close()
                        except Exception:
                            pass
                    backend = None
                    if mixer is not None:
                        mixer.clear_effects()
                    with self._lock:
                        self._events.clear()
                        self._stats.update(state='unavailable', ready=False, voices=0,
                                           error=str(exc)[:400], backend=None, output_active=False,
                                           acknowledged_flush=self._flush)
                    failed = True
                    running = False
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass
            mixer = None
            self._publish(state='closed', ready=False, voices=0, pcm_bytes=0, backend=None, mixer=None,
                          output_active=False)
