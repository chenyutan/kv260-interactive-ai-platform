"""Bounded, asynchronous native lesson video playback.

Only local regular files are accepted.  Decode and all pipeline state changes
belong to one worker; GTK receives one latest-only RGB surface (640x360 for a
full demonstration, 448x252 for its silent practice reference). Audio is
silent unless a verified, explicitly named Cube Pulse route is supplied.  This
module does not capture a camera, fetch URLs, or synthesize teaching footage.
"""
import math
import threading
import time
from pathlib import Path

WIDTH, HEIGHT = 640, 360
# The practice reference is now the primary image (~423 x 238 on a 960 x 540
# screen). Decode slightly above that size, without upscaling a 320 x 180
# buffer. Full demonstrations retain 640 x 360; practice remains 15 FPS and
# latest-only with a single silent pipeline.
COMPACT_WIDTH, COMPACT_HEIGHT, COMPACT_FPS = 448, 252, 15
MAX_FILE_BYTES = 512 * 1024 * 1024


class TeachingVideoPlayer:
    """Nonblocking controls; ``frame()`` is called only by the drawing thread.

    Events contain ``event``, ``generation`` and optionally ``error``.  They are
    delivered on the GLib main context and discarded after replacement/close.
    ``frame()`` returns ``(surface, width, height)`` or None.  A pipeline factory
    may be injected for unit tests; actual playback uses Gst playbin/appsink.
    """
    def __init__(self, on_event=None, enabled=True, audio_route=None,
                 pipeline_factory=None, dispatch=None, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.on_event = on_event
        self._clock = clock
        self._factory = pipeline_factory
        self._dispatch = dispatch
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._closed = False
        self._generation = 0
        self._desired = 'stopped'
        self._path = None
        self._route = self._valid_route(audio_route)
        self._forced_muted = False
        self._compact = False
        self._raw = None
        self._pipeline_token = 0
        self._frame_serial = 0
        self._surface_serial = -1
        self._surface = None
        self._surface_data = None
        self._stats = dict(state='idle' if enabled else 'disabled', error=None,
                           position=0., duration=0., frames=0, dropped=0,
                           audio_active=False, queue_limit=1, width=WIDTH,
                           height=HEIGHT, pipeline_starts=0)
        self._stats.update(audio_muted=self._route is None,
                           audio_error=None, audio_fallbacks=0)

    @staticmethod
    def _valid_route(route):
        if not isinstance(route, dict) or route.get('discovery_stale'):
            return None
        sink = route.get('sink') or route.get('device')
        server = route.get('server')
        if not isinstance(sink, str) or not sink or len(sink) > 512:
            return None
        if server is not None and (not isinstance(server, str) or len(server) > 512):
            return None
        return dict(sink=sink, server=server)

    def set_audio_route(self, route):
        """An explicit route; never falls back to the OS default output."""
        with self._lock:
            route = self._valid_route(route)
            if route == self._route or self._closed:
                return
            self._route = route
            self._stats.update(audio_error=None, audio_muted=self._forced_muted or route is None)
            # Discovery updates are remembered for the next full demonstration.
            # They cannot unmute or restart the reference shown during practice.
            if self._forced_muted:
                return
            # The worker rebuilds at the last measured position. A device
            # change must not allow old audio to continue to the default sink.
            self._generation += 1
            self._raw = None
            self._surface = self._surface_data = None
            self._surface_serial = -1
            self._ensure_worker()
            self._wake.set()

    def _ensure_worker(self):
        if self.enabled and not self._closed and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run,
                                             name='health-video', daemon=True)
            self._thread.start()

    def play(self, path, *, muted=False, compact=False):
        """Schedule a local file from its beginning without doing file I/O."""
        with self._lock:
            if self._closed or not self.enabled:
                return False
            self._generation += 1
            self._forced_muted = bool(muted)
            self._compact = bool(compact)
            self._path = str(path)
            self._desired = 'playing'
            self._raw = None
            self._surface = self._surface_data = None
            self._surface_serial = -1
            self._stats.update(state='loading', position=0., duration=0.,
                               error=None, audio_active=False, audio_error=None,
                               audio_muted=self._forced_muted or self._route is None,
                               width=COMPACT_WIDTH if self._compact else WIDTH,
                               height=COMPACT_HEIGHT if self._compact else HEIGHT)
            self._ensure_worker()
            self._wake.set()
            return self._generation

    def play_companion(self, path):
        """Practice reference: bounded clear frames and no audio route."""
        return self.play(path, muted=True, compact=True)

    def pause(self):
        with self._lock:
            if not self._closed and self._desired == 'playing':
                self._desired = 'paused'
                self._wake.set()

    def resume(self):
        with self._lock:
            if not self._closed and self._desired == 'paused':
                self._desired = 'playing'
                self._wake.set()

    def stop(self):
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            self._desired = 'stopped'
            self._path = None
            self._raw = None
            self._surface = self._surface_data = None
            self._surface_serial = -1
            self._stats.update(state='stopped', audio_active=False,
                               position=0., duration=0., error=None)
            self._wake.set()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._desired = 'closed'
            self._raw = None
            self._surface = self._surface_data = None
            self._stats.update(state='closed', audio_active=False)
            self._wake.set()

    def wait_closed(self, timeout=3.):
        """Test/process teardown only; never call on a GTK event callback."""
        if self._thread:
            self._thread.join(timeout)
        return not self._thread or not self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            result = dict(self._stats)
            result.update(generation=self._generation, path=self._path,
                          epoch=self._generation, status=result['state'],
                          position_seconds=result['position'],
                          duration_seconds=result['duration'],
                          desired=self._desired, enabled=self.enabled,
                          forced_muted=self._forced_muted, compact=self._compact,
                          worker_alive=bool(self._thread and self._thread.is_alive()),
                          muted=result['audio_muted'])
            return result

    def frame(self):
        """Return a cached Cairo surface; no decode/file I/O on the caller."""
        packet = self.frame_packet()
        return ((packet['surface'], packet['width'], packet['height'])
                if packet is not None else None)

    def frame_packet(self):
        """Surface and timestamp from the same decoded sample, atomically.

        Querying the pipeline's current position separately can advance beyond
        the image on screen. Teaching references must use this sample's PTS.
        """
        with self._lock:
            if self._raw is None:
                return None
            serial, data, width, height, stride, pts_seconds = self._raw
            generation = self._generation
            path = self._path
            if serial == self._surface_serial:
                return dict(surface=self._surface, width=width, height=height,
                            pts_seconds=pts_seconds, generation=generation,
                            serial=serial, path=path)
            # Copy only a reference under the lock; replacement is latest-only.
        import cairo
        surface_data = bytearray(data)
        surface = cairo.ImageSurface.create_for_data(surface_data,
                        cairo.FORMAT_RGB24, width, height, stride)
        with self._lock:
            # A stop/play may have invalidated this frame while copying.
            if self._raw is None or generation != self._generation or self._closed:
                return None
            self._surface_serial = serial
            self._surface_data = surface_data
            self._surface = surface
            return dict(surface=surface, width=width, height=height,
                        pts_seconds=pts_seconds, generation=generation,
                        serial=serial, path=path)

    def _publish(self, generation, **values):
        with self._lock:
            if generation == self._generation and not self._closed:
                self._stats.update(values)

    def _event(self, name, generation, **values):
        if not self.on_event:
            return
        event = dict(event=name, generation=generation, epoch=generation,
                     status='ended' if name == 'eos' else 'playing' if name == 'loaded' else name,
                     **values)
        def deliver():
            with self._lock:
                if self._closed or generation != self._generation:
                    return False
            self.on_event(event)
            return False
        if self._dispatch:
            self._dispatch(deliver)
        else:
            from gi.repository import GLib
            GLib.idle_add(deliver)

    def _accept_frame(self, generation, data, width, height, stride, token=None,
                      pts_seconds=None):
        with self._lock:
            if (self._closed or generation != self._generation or
                    (token is not None and token != self._pipeline_token)):
                return False
            expected = ((COMPACT_WIDTH, COMPACT_HEIGHT) if self._compact else (WIDTH, HEIGHT))
            if ((width, height) != expected or stride < width * 4 or
                    stride > width * 4 + 256 or len(data) != stride * height):
                return False
            if self._raw and self._surface_serial != self._raw[0]:
                self._stats['dropped'] += 1
            self._frame_serial += 1
            if (isinstance(pts_seconds, bool) or
                    not isinstance(pts_seconds, (int, float)) or
                    not math.isfinite(pts_seconds) or pts_seconds < 0):
                pts_seconds = None
            self._raw = (self._frame_serial, data, width, height, stride, pts_seconds)
            self._stats['frames'] += 1
            return True

    def _new_pipeline(self, path, generation, route):
        import gi
        gi.require_version('Gst', '1.0')
        gi.require_version('GstVideo', '1.0')
        from gi.repository import Gst, GstVideo
        Gst.init(None)
        with self._lock:
            self._pipeline_token += 1
            token = self._pipeline_token
            compact = self._compact
        width, height = (COMPACT_WIDTH, COMPACT_HEIGHT) if compact else (WIDTH, HEIGHT)
        pipeline = Gst.ElementFactory.make('playbin', 'health-lesson-video')
        if not pipeline:
            raise RuntimeError('GStreamer playbin unavailable')
        rate = ('videorate drop-only=true ! video/x-raw,framerate={}/1 ! '.format(COMPACT_FPS)
                if compact else '')
        video_bin = Gst.parse_bin_from_description(
            'queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 ! '
            + rate + 'videoconvert ! videoscale add-borders=true ! '
            'video/x-raw,format=BGRx,width={},height={},pixel-aspect-ratio=1/1 ! '.format(width, height) +
            'appsink name=lesson_frames emit-signals=true sync=true '
            'max-buffers=1 drop=true enable-last-sample=false', True)
        sink = video_bin.get_by_name('lesson_frames')
        def sample_ready(appsink):
            sample = appsink.emit('pull-sample')
            if not sample:
                return Gst.FlowReturn.EOS
            info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
            buffer = sample.get_buffer()
            if info and buffer:
                data = buffer.extract_dup(0, buffer.get_size())
                pts = (buffer.pts / Gst.SECOND
                       if buffer.pts != Gst.CLOCK_TIME_NONE else None)
                self._accept_frame(generation, data, info.width,
                                   info.height, info.stride[0], token, pts_seconds=pts)
            return Gst.FlowReturn.OK
        sink.connect('new-sample', sample_ready)
        audio = Gst.ElementFactory.make('pulsesink' if route else 'fakesink')
        if audio is None:
            raise RuntimeError('Explicit audio sink unavailable')
        if route:
            audio.set_property('device', route['sink'])
            if route.get('server'):
                audio.set_property('server', route['server'])
            audio.set_property('sync', True)
            audio.set_property('enable-last-sample', False)
        else:
            audio.set_property('sync', True)
        pipeline.set_property('video-sink', video_bin)
        pipeline.set_property('audio-sink', audio)
        pipeline.set_property('uri', path.as_uri())
        # Audio is enabled only with an explicit Cube output.  Disable text to
        # avoid browser/plugin lookups and let the content keep its own titles.
        flags = int(pipeline.get_property('flags'))
        pipeline.set_property('flags', (flags | 1 | 2) & ~4)
        return _GstPipeline(pipeline, Gst)

    def _start_pipeline(self, candidate, generation, desired, route, position=0.):
        pipeline = None
        try:
            pipeline = (self._factory or self._new_pipeline)(candidate.resolve(), generation, route)
            pipeline.set_playing(desired == 'playing')
            if position > 0:
                pipeline.seek(position)
            self._publish(generation, state=desired, error=None,
                          audio_active=bool(route and desired == 'playing'),
                          audio_muted=route is None)
            with self._lock:
                self._stats['pipeline_starts'] += 1
            return pipeline
        except Exception:
            if pipeline:
                pipeline.close()
            raise

    def _audio_fallback(self, generation, error):
        with self._lock:
            if generation != self._generation or self._closed:
                return
            self._stats.update(audio_error=str(error), audio_muted=True,
                               audio_active=False)
            self._stats['audio_fallbacks'] += 1
        self._event('audio_unavailable', generation, error=str(error))

    def _run(self):
        pipeline = None
        current = -1
        path = None
        effective_route = None
        previous_desired = None
        try:
            while True:
                try:
                    with self._lock:
                        closed, generation = self._closed, self._generation
                        desired, requested = self._desired, self._path
                        route = ((self._route.copy() if self._route and not self._forced_muted else None)
                                 if current != generation else effective_route)
                        position = self._stats['position']
                    if closed:
                        break
                    if current != generation:
                        resume_position = position if path == requested else 0.
                        if pipeline:
                            pipeline.close()
                            pipeline = None
                        current, path = generation, requested
                        effective_route = route
                        previous_desired = None
                        if desired in ('playing', 'paused') and requested:
                            try:
                                candidate = Path(requested)
                                if not candidate.is_absolute() or not candidate.is_file():
                                    raise ValueError('Teaching video must be an existing absolute local file')
                                if candidate.stat().st_size > MAX_FILE_BYTES:
                                    raise ValueError('Teaching video exceeds 512 MiB')
                                try:
                                    pipeline = self._start_pipeline(candidate, current,
                                                    desired, route, resume_position)
                                except Exception as error:
                                    if not route:
                                        raise
                                    # A connected inventory may already be stale.
                                    # Continue the lesson silently rather than let
                                    # a missing Pulse endpoint stop video decoding.
                                    self._audio_fallback(current, error)
                                    route = effective_route = None
                                    pipeline = self._start_pipeline(candidate, current,
                                                    desired, None, resume_position)
                                self._event('loaded', current)
                            except Exception as error:
                                if pipeline:
                                    pipeline.close()
                                    pipeline = None
                                self._publish(current, state='error', error=str(error), audio_active=False)
                                self._event('error', current, error=str(error))
                    if pipeline and desired != previous_desired:
                        pipeline.set_playing(desired == 'playing')
                        self._publish(current, state=desired, audio_active=bool(route and desired == 'playing'))
                        previous_desired = desired
                    if pipeline:
                        event = pipeline.poll()
                        measured_position, duration = pipeline.times()
                        self._publish(current, position=measured_position, duration=duration)
                        if event:
                            name, message = event
                            pipeline.close()
                            pipeline = None
                            if name == 'error' and route:
                                self._audio_fallback(current, message)
                                route = effective_route = None
                                try:
                                    pipeline = self._start_pipeline(Path(requested), current,
                                            desired, None, measured_position)
                                except Exception as error:
                                    message = str(error)
                                if pipeline:
                                    continue
                            self._publish(current, state='ended' if name == 'eos' else 'error',
                                          error=message, audio_active=False)
                            self._event(name, current, **({'error': message} if message else {}))
                except Exception as error:
                    broken, pipeline = pipeline, None
                    try:
                        if broken:
                            broken.close()
                    except Exception as cleanup_error:
                        error = RuntimeError(str(error) + '; cleanup: ' + str(cleanup_error))
                    self._publish(current, state='error', error=str(error), audio_active=False)
                    self._event('error', current, error=str(error))
                    # Do not recreate the failed epoch automatically. A new
                    # play/route generation is handled by the next iteration,
                    # including a request received while close() was running.
                self._wake.wait(.02)
                self._wake.clear()
        finally:
            if pipeline:
                try:
                    pipeline.close()
                except Exception as error:
                    self._publish(current, cleanup_error=str(error))
            with self._lock:
                self._stats['audio_active'] = False


class _GstPipeline:
    def __init__(self, pipeline, gst):
        self.pipeline = pipeline
        self.Gst = gst
        self.bus = pipeline.get_bus()
        self.pending_seek = None

    def set_playing(self, playing):
        result = self.pipeline.set_state(self.Gst.State.PLAYING if playing else self.Gst.State.PAUSED)
        if result == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('GStreamer could not change playback state')

    def seek(self, seconds):
        self.pending_seek = seconds

    def poll(self):
        types = (self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS |
                 self.Gst.MessageType.ASYNC_DONE)
        message = self.bus.timed_pop_filtered(0, types)
        if message:
            if message.type == self.Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                return 'error', str(error)
            if message.type == self.Gst.MessageType.EOS:
                return 'eos', None
            if self.pending_seek is not None:
                self.pipeline.seek_simple(self.Gst.Format.TIME,
                    self.Gst.SeekFlags.FLUSH | self.Gst.SeekFlags.KEY_UNIT,
                    int(self.pending_seek * self.Gst.SECOND))
                self.pending_seek = None
        return None

    def times(self):
        values = []
        for query in (self.pipeline.query_position, self.pipeline.query_duration):
            valid, nanoseconds = query(self.Gst.Format.TIME)
            value = nanoseconds / self.Gst.SECOND if valid and nanoseconds >= 0 else 0.
            values.append(value if math.isfinite(value) else 0.)
        return tuple(values)

    def close(self):
        self.pipeline.set_state(self.Gst.State.NULL)
        self.bus.set_flushing(True)
