"""No-speaker audio tests; optional real GStreamer check uses fakesink only."""
from array import array
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio.mixer import Clip, Mixer
from audio.runtime import AudioDirector, MemoryBackend, RATE


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class MixerTests(unittest.TestCase):
    def clip(self, value=1000, frames=960, priority=1, loop=False):
        return Clip(array('h', [value, value] * frames), loop=loop, priority=priority)

    def test_priority_steals_oldest_low_priority_and_bounds_voices(self):
        low, high = self.clip(priority=1), self.clip(priority=10)
        mixer = Mixer({}, {'low': low, 'high': high}, max_voices=2)
        self.assertTrue(mixer.play('low'))
        self.assertTrue(mixer.play('high'))
        first_high = mixer.voices[1]
        self.assertTrue(mixer.play('high'))
        self.assertEqual(len(mixer.voices), 2)
        self.assertIn(first_high, mixer.voices)
        self.assertEqual(mixer.stolen, 1)
        self.assertFalse(mixer.play('low'))
        self.assertEqual(len(mixer.voices), 2)

    def test_music_loop_pause_position_and_mute_progress(self):
        mixer = Mixer({'bgm': self.clip(frames=600, loop=True)}, {}, 'bgm')
        mixer.render(480)
        self.assertEqual(mixer.music_position, 480)
        self.assertEqual(mixer.render(480, muted=True), bytes(480 * 4))
        self.assertEqual(mixer.music_position, 360)
        mixer.rewind_music(240)
        self.assertEqual(mixer.music_position, 120)

    def test_stealing_later_voice_never_compares_numpy_pcm_arrays(self):
        low, high = self.clip(value=200, priority=1), self.clip(value=700, priority=10)
        mixer = Mixer({}, {'low': low, 'high': high}, max_voices=2)
        mixer.play('high'); mixer.play('low')
        first = mixer.voices[0]
        self.assertTrue(mixer.play('high'))
        self.assertIs(mixer.voices[0], first)
        self.assertEqual(mixer.stolen, 1)
        self.assertEqual(len(mixer.render(480)), 480 * 4)

    def test_gain_channels_saturation_and_numpy_fallback_match(self):
        outputs = []
        for use_numpy in (False, True):
            music, effect = self.clip(value=30000), self.clip(value=30000)
            mixer = Mixer({'bgm': music}, {'hit': effect}, 'bgm', use_numpy=use_numpy)
            mixer.play('hit')
            pcm = array('h')
            pcm.frombytes(mixer.render(10, music_gain=0, effects_gain=0.5, master=0.5))
            self.assertEqual(set(pcm), {7500})
            output = mixer.render(10)
            outputs.append(output)
            pcm = array('h'); pcm.frombytes(output)
            self.assertEqual(set(pcm), {32767})
        self.assertEqual(outputs[0], outputs[1])

    def test_effects_finish_and_never_loop(self):
        mixer = Mixer({}, {'hit': self.clip(frames=20, loop=True)})
        mixer.play('hit')
        mixer.render(40)
        self.assertEqual(mixer.voices, [])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        path = self.root / 'tone.wav'
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(array('h', [500, -500] * RATE).tobytes())
        entry = dict(file='tone.wav', gain=0.5, loop=True, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     duration_seconds=1, priority=1, cooldown_ms=0, title='Test tone')
        self.manifest = dict(schema=1, sample_rate=RATE, channels=2,
                             tracks={name: entry.copy() for name in ('bgm_ink', 'bgm_fruit', 'bgm_fight', 'bgm_sword')},
                             effects={'hit': entry.copy(), 'cool': dict(entry, cooldown_ms=200)})
        self.write_manifest()
        self.directors = []

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def make(self, **kwargs):
        director = AudioDirector(kwargs.pop('mode', 'ink'), sink=kwargs.pop('sink', 'memory'),
                                 asset_dir=self.root, **kwargs)
        self.directors.append(director)
        return director

    def ready(self, director):
        self.assertTrue(director.start())
        self.assertTrue(wait_for(lambda: director.status()['ready']), director.status())

    def tearDown(self):
        for director in self.directors:
            director.close()
            self.assertTrue(director.wait_closed(), director.status())
        self.temporary.cleanup()

    def test_construction_does_no_io_and_disabled_never_opens_backend(self):
        with patch('audio.runtime._load_assets', side_effect=AssertionError('UI did IO')):
            director = self.make(enabled=False)
            self.assertFalse(director.start())
            self.assertFalse(director.play('hit'))
            self.assertFalse(director.retry())
            self.assertFalse(director.status()['worker_alive'])

    def test_gstreamer_queue_reserves_a_whole_configured_block(self):
        from audio.runtime import GstBackend
        class Source:
            level = 0
            def get_property(self, name):
                self.assert_property = name
                return self.level
        for frames in (480, 1440):
            backend = GstBackend.__new__(GstBackend)
            backend.source = Source()
            backend.block_frames = frames
            backend.max_bytes = frames * 4 * 3
            backend.source.level = frames * 4 * 2
            self.assertTrue(backend.writable())
            backend.source.level += 4
            self.assertFalse(backend.writable())

    def test_optional_larger_blocks_preserve_default_and_pause_flush(self):
        default=self.make()
        self.assertEqual(default.status()['block_frames'],480)
        outputs=[]
        def factory(sink):
            backend=MemoryBackend(sink);outputs.append(backend);return backend
        director=self.make(block_frames=1440,backend_factory=factory)
        self.ready(director)
        self.assertTrue(wait_for(lambda:bool(outputs and outputs[0].blocks)))
        self.assertTrue(all(len(block)==1440*4 for block in outputs[0].blocks))
        director.pause()
        self.assertTrue(wait_for(lambda:director.status()['state']=='paused'))
        self.assertEqual(list(outputs[0].blocks),[])
        position=director.status()['position_seconds']
        time.sleep(.05)
        self.assertEqual(director.status()['position_seconds'],position)
        director.resume()
        self.assertTrue(wait_for(lambda:director.status()['position_seconds']>position))
        for invalid in (True,239,1921,480.0):
            with self.assertRaises(ValueError):self.make(block_frames=invalid)

    def test_decode_runs_off_calling_thread(self):
        from audio.runtime import _load_assets
        callers = []
        def load(*args):
            callers.append(threading.get_ident())
            return _load_assets(*args)
        with patch('audio.runtime._load_assets', side_effect=load):
            director = self.make()
            self.ready(director)
        self.assertEqual(len(callers), 1)
        self.assertNotEqual(callers[0], threading.get_ident())

    def test_pause_flushes_events_and_voices_and_freezes_bgm(self):
        output = MemoryBackend()
        director = self.make(backend_factory=lambda _: output)
        self.ready(director)
        self.assertTrue(director.play('hit', key='first'))
        self.assertTrue(wait_for(lambda: director.status()['voices'] == 1))
        director.pause()
        self.assertFalse(director.play('hit'))
        self.assertEqual(director.status()['queued'], 0)
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
        position = director.status()['position_seconds']
        self.assertEqual(director.status()['voices'], 0)
        self.assertEqual(len(output.blocks), 0)
        time.sleep(0.04)
        self.assertEqual(director.status()['position_seconds'], position)
        director.resume()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'playing'))
        self.assertGreaterEqual(director.status()['position_seconds'], position)
        self.assertEqual(director.status()['voices'], 0)

    def test_rapid_pause_resume_still_flushes_old_voices(self):
        output = MemoryBackend()
        director = self.make(backend_factory=lambda _: output)
        self.ready(director)
        director.play('hit')
        self.assertTrue(wait_for(lambda: director.status()['voices'] == 1))
        director.pause(); director.resume()
        self.assertTrue(wait_for(lambda: output.flushes >= 1 and director.status()['voices'] == 0))

    def test_queue_and_key_cache_are_bounded_and_duplicates_rejected(self):
        release, entered = threading.Event(), threading.Event()
        from audio.runtime import _load_assets
        def slow_load(*args):
            entered.set(); release.wait(1.0)
            return _load_assets(*args)
        with patch('audio.runtime._load_assets', side_effect=slow_load):
            director = self.make(queue_limit=4)
            director.start(); self.assertTrue(entered.wait(1.0))
            self.assertTrue(director.play('hit', 'one'))
            self.assertFalse(director.play('hit', 'one'))
            for key in range(20):
                director.play('hit', key)
            self.assertEqual(director.status()['queued'], 4)
            self.assertEqual(director.status()['dropped'], 17)
            director.pause()
            self.assertEqual(director.status()['queued'], 0)
            release.set()
            self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
        self.ready(director)
        for batch in range(80):
            for key in range(4):
                director.play('hit', (batch, key))
            time.sleep(0.011)
        self.assertLessEqual(director.status()['key_count'], 256)
        self.assertLessEqual(director.status()['voices'], 6)

    def test_cooldown_and_unknown_id(self):
        director = self.make()
        self.ready(director)
        director.play('cool', 'one'); director.play('cool', 'two'); director.play('missing')
        self.assertTrue(wait_for(lambda: director.status()['cooldown'] == 1))
        self.assertEqual(director.status()['rejected'], 1)

    def test_loading_events_expire_instead_of_playing_late(self):
        release, entered = threading.Event(), threading.Event()
        from audio.runtime import _load_assets
        def slow_load(*args):
            entered.set(); release.wait(1.0)
            return _load_assets(*args)
        with patch('audio.runtime._load_assets', side_effect=slow_load):
            director = self.make()
            director.start(); self.assertTrue(entered.wait(1.0))
            director.play('hit')
            time.sleep(0.27)
            release.set()
            self.assertTrue(wait_for(lambda: director.status()['ready']))
            self.assertEqual(director.status()['expired'], 1)
            self.assertEqual(director.status()['voices'], 0)

    def test_close_during_load_never_opens_output(self):
        release, entered = threading.Event(), threading.Event()
        from audio.runtime import _load_assets
        def slow_load(*args):
            entered.set(); release.wait(1.0)
            return _load_assets(*args)
        with patch('audio.runtime._load_assets', side_effect=slow_load):
            director = self.make(backend_factory=lambda _: self.fail('Output opened after close'))
            director.start(); self.assertTrue(entered.wait(1.0))
            before = time.monotonic()
            director.close()
            self.assertLess(time.monotonic() - before, 0.05)
            release.set()
            self.assertTrue(director.wait_closed())
            self.assertEqual(director.status()['state'], 'closed')

    def test_missing_device_degrades_and_explicit_retry_recovers(self):
        attempts = []
        def factory(_):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError('Test: device removed')
            return MemoryBackend()
        director = self.make(backend_factory=factory)
        director.start()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'unavailable'))
        self.assertFalse(director.play('hit'))
        self.assertIn('device removed', director.status()['error'])
        director.retry()
        self.assertTrue(wait_for(lambda: director.status()['ready']), director.status())
        self.assertEqual(len(attempts), 2)

    def test_output_error_degrades_and_drops_queued_effects(self):
        class BrokenBackend(MemoryBackend):
            def poll(self):
                if self.frames >= 960:
                    raise RuntimeError('Disconnected')
        director = self.make(backend_factory=lambda _: BrokenBackend())
        director.start()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'unavailable'))
        self.assertEqual(director.status()['queued'], 0)
        self.assertEqual(director.status()['voices'], 0)

    def test_output_backpressure_is_bounded_and_stalls_degrade(self):
        class FullBackend(MemoryBackend):
            def writable(self):
                return False
        output = FullBackend()
        director = self.make(backend_factory=lambda _: output)
        director.start()
        self.assertTrue(wait_for(lambda: director.status()['backend'] == 'memory'))
        for value in range(1000):
            director.play('hit', key=value)
        self.assertLessEqual(director.status()['queued'], 32)
        self.assertEqual(output.frames, 0)
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'unavailable'))
        self.assertIn('consume PCM', director.status()['error'])
        self.assertTrue(output.closed)
        self.assertEqual(director.status()['queued'], 0)

    def test_mute_keeps_position_advancing_and_levels_are_immediate(self):
        output = MemoryBackend()
        director = self.make(backend_factory=lambda _: output)
        self.ready(director)
        director.set_levels(music=0.4, effects=0.3, master=0.2)
        self.assertEqual(director.status()['levels'], dict(music=0.4, effects=0.3, master=0.2))
        director.mute(True)
        previous = director.status()['position_seconds']
        self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > previous + 0.04))
        self.assertEqual(output.blocks[-1], bytes(480 * 4))
        with self.assertRaises(ValueError):
            director.set_levels(music=float('nan'))

    def test_stop_releases_assets_and_close_terminates_worker(self):
        outputs = []
        def factory(_):
            output = MemoryBackend(); outputs.append(output); return output
        director = self.make(backend_factory=factory)
        self.ready(director)
        director.stop()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'stopped'))
        self.assertTrue(outputs[0].closed)
        self.assertEqual(director.status()['pcm_bytes'], 0)
        self.assertEqual(director.status()['position_seconds'], 0)
        self.ready(director)
        director.close()
        self.assertTrue(director.wait_closed())
        self.assertTrue(outputs[1].closed)
        self.assertFalse(director.start())
        self.assertEqual(director.status()['state'], 'closed')

    def test_taiko_has_no_second_bgm_clock_or_track(self):
        self.manifest['tracks'] = {}
        self.write_manifest()
        output = MemoryBackend()
        director = self.make(mode='taiko', backend_factory=lambda _: output)
        self.ready(director)
        self.assertEqual(output.blocks[-1], bytes(480 * 4))
        self.assertEqual(director.status()['position_seconds'], 0)
        director.play('hit')
        self.assertTrue(wait_for(lambda: director.status()['voices'] == 1))

    def test_sword_selects_its_own_background_music(self):
        self.manifest['tracks'] = {'bgm_sword': self.manifest['tracks']['bgm_sword']}
        self.write_manifest()
        output = MemoryBackend()
        director = self.make(mode='sword', backend_factory=lambda _: output)
        self.ready(director)
        self.assertNotEqual(output.blocks[-1], bytes(480 * 4))
        self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > 0.02))
        director.pause()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
        position = director.status()['position_seconds']
        director.resume()
        self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > position))

    def test_sword_event_storm_creates_no_threads_and_bounds_resources(self):
        output = MemoryBackend()
        director = self.make(mode='sword', backend_factory=lambda _: output)
        self.ready(director)
        worker = director._thread
        with patch('audio.runtime.threading.Thread', side_effect=AssertionError('Per-event thread creation')):
            for index in range(5000):
                director.play('hit', key=('sword', index))
        self.assertIs(director._thread, worker)
        self.assertTrue(worker.is_alive())
        self.assertLessEqual(director.status()['queued'], 32)
        self.assertGreater(director.status()['dropped'], 0)
        self.assertTrue(wait_for(lambda: director.status()['voices'] == 6))
        self.assertLessEqual(len(output.blocks), 4)
        self.assertLessEqual(director.status()['key_count'], 256)
        director.pause()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
        self.assertEqual(director.status()['voices'], 0)
        self.assertEqual(director.status()['queued'], 0)

    def test_checksum_failure_is_contained(self):
        self.manifest['effects']['hit']['sha256'] = '0' * 64
        self.write_manifest()
        director = self.make()
        director.start()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'unavailable'))
        self.assertIn('checksum', director.status()['error'])

    def test_manifest_path_escape_is_contained(self):
        self.manifest['effects']['hit']['file'] = '../outside.wav'
        self.write_manifest()
        director = self.make()
        director.start()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'unavailable'))
        self.assertIn('escapes', director.status()['error'])

    @unittest.skipUnless(importlib.util.find_spec('gi'), 'PyGObject/GStreamer not installed locally')
    def test_gstreamer_fakesink_only(self):
        director = self.make(sink='fakesink')
        self.ready(director)
        director.play('hit')
        time.sleep(0.08)
        self.assertEqual(director.status()['state'], 'playing', director.status())
        director.pause()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
        director.resume()
        self.assertTrue(wait_for(lambda: director.status()['state'] == 'playing'))

    @unittest.skipUnless(importlib.util.find_spec('gi'), 'PyGObject/GStreamer not installed locally')
    def test_taiko_fakesink_companion_never_controls_song_clock(self):
        from taiko.audio_clock import GstClock
        clock = GstClock(self.root / 'tone.wav', sink='fakesink')
        try:
            self.assertTrue(clock.play())
            director = self.make(mode='taiko', sink='fakesink')
            self.ready(director)
            director.play('hit')
            self.assertTrue(wait_for(lambda: clock.position() > 0.03))
            before = clock.position()
            director.pause()
            self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
            self.assertTrue(wait_for(lambda: clock.position() > before + 0.02))
            self.assertEqual(director.status()['position_seconds'], 0)
            clock.pause()
            self.assertFalse(clock.playing)
            self.assertTrue(clock.play())
            director.resume()
            self.assertTrue(wait_for(lambda: director.status()['state'] == 'playing'))
            self.assertTrue(clock.poll(), clock.error)
        finally:
            clock.close()


if __name__ == '__main__':
    unittest.main()
