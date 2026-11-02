"""Fruit/fight/sword output controls and failure history; no hardware or sound."""
from array import array
from collections import deque
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import wave

from .game_output import CubeGameAudioDirector
from .runtime import AudioDirector, GstBackend, _load_assets
from .sword_output import SwordAudioDirector
from .test_sword_output import FakeOutput, inventory, wait_for


FIGHT_EFFECTS = ('fight_swing', 'fight_kick', 'fight_special', 'fight_hit',
                 'fight_block', 'fight_win', 'fight_lose')
FRUIT_EFFECTS = ('fruit_slice', 'fruit_double', 'fruit_freeze', 'fruit_bomb',
                 'fruit_combo', 'fruit_miss')


class GameOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tone = self.root / 'tone.wav'
        with wave.open(str(tone), 'wb') as output:
            output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            output.writeframes(array('h', [1800, -1800] * 48000).tobytes())
        entry = dict(file=tone.name, loop=True, gain=.72,
                     sha256=hashlib.sha256(tone.read_bytes()).hexdigest())
        self.manifest = dict(schema=1, sample_rate=48000, channels=2,
            tracks={'bgm_fruit': entry, 'bgm_fight': entry, 'bgm_sword': entry},
            effects={name: dict(entry, loop=False) for name in FIGHT_EFFECTS+FRUIT_EFFECTS})
        self.write_manifest()
        self.outputs = []
        self.directors = []
        self.inv = inventory()
        self.physical = True
        self.now = 100.
        self.io_threads = []

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def discover(self):
        self.io_threads.append(threading.get_ident())
        return self.inv

    def physical_check(self, route):
        self.io_threads.append(threading.get_ident())
        return self.physical and route['usb_instance'] == self.inv['devices'][0]['usb_instance']

    def factory(self, sink, **options):
        self.io_threads.append(threading.get_ident())
        output = FakeOutput(sink, **options)
        self.outputs.append(output)
        return output

    def make(self, mode='fight', **options):
        director = CubeGameAudioDirector(mode, asset_dir=self.root,
            inventory_fn=options.pop('inventory_fn', self.discover),
            physical_checker=self.physical_check,
            backend_factory=options.pop('backend_factory', self.factory),
            server_provider=lambda: 'unix:/test-pulse', clock=lambda: self.now, **options)
        self.directors.append(director)
        return director

    def state(self, director, value):
        self.assertTrue(wait_for(lambda: director.status()['state'] == value), director.status())

    def tearDown(self):
        for director in self.directors:
            director.close()
            self.assertTrue(director.wait_closed(), director.status())
        self.temp.cleanup()

    def test_fight_bgm_seven_effects_live_clock_and_30ms_worker(self):
        director = self.make();director.resume();self.state(director, 'playing')
        output = self.outputs[0]
        self.assertEqual(output.options['stream_name'], 'InkSuite fight audio')
        self.assertEqual(output.options['device'], 'cube-pulse')
        self.assertEqual(output.options['block_frames'], 1440)
        self.assertTrue(output.options['live_timestamps'])
        self.assertEqual(director.status()['mode'], 'fight')
        self.assertEqual(director.status()['sink'], 'pulsesink')
        for effect in FIGHT_EFFECTS:
            self.assertTrue(director.play(effect, key=effect))
        self.assertTrue(wait_for(lambda: director.status()['queued'] == 0))
        self.assertEqual(director.status()['accepted'], 7)
        self.assertEqual(director.status()['rejected'], 0)
        self.assertTrue(any(any(block) for block in output.blocks))
        self.assertTrue(all(len(block) == 1440 * 4 for block in output.blocks))
        self.assertEqual(len(set(self.io_threads)), 1)
        self.assertNotEqual(output.worker, threading.get_ident())

    def test_game_director_cannot_replace_rhythm_or_other_modes(self):
        for mode in ('taiko', 'ink', 'koi', '', None):
            with self.assertRaises(ValueError):
                CubeGameAudioDirector(mode, enabled=False)
        legacy = AudioDirector('taiko', enabled=False)
        self.assertEqual(legacy.status()['block_frames'], 480)
        self.assertEqual(legacy.status()['sink'], 'autoaudiosink')
        self.assertFalse(legacy.status()['worker_alive'])
        legacy.close()

    def test_fruit_visual_events_and_bgm_use_live_cube_then_recover_and_restart(self):
        from .integration import ExperienceAudio
        visual=[]
        game=NS(elapsed=0,missed=0,state='playing')
        director=self.make('fruit')
        sound=ExperienceAudio('fruit',director=director)
        app=NS(game=game,item_fx=NS(trigger=visual.append))
        sound.bind(app);sound.resume();self.state(director,'playing')
        output=self.outputs[0]
        self.assertEqual(output.options['stream_name'],'InkSuite fruit audio')
        self.assertEqual(output.options['device'],'cube-pulse')
        self.assertTrue(output.options['live_timestamps'])
        self.assertEqual(output.options['block_frames'],1440)
        self.assertTrue(wait_for(lambda:director.status()['position_seconds']>.04))
        events=[dict(kind=kind,combo=3 if kind=='normal' else 0)
                for kind in ('normal','double','ice','bomb')]
        for event in events:app.item_fx.trigger(event)
        game.missed=1;sound.tick()
        self.assertEqual(visual,events)
        self.assertEqual(director.status()['accepted'],len(FRUIT_EFFECTS))
        self.assertTrue(wait_for(lambda:director.status()['queued']==0))
        self.physical=False;self.state(director,'unavailable')
        position=director.status()['position_seconds']
        sound.pause();self.physical=True;self.now+=3.
        sound.tick();self.assertEqual(len(self.outputs),1)
        sound.resume();self.state(director,'playing')
        self.assertEqual(director.status()['output_recovery_count'],1)
        self.assertGreaterEqual(director.status()['position_seconds'],position)
        self.assertEqual(director.status()['voices'],0)
        sound.pause();app.item_fx.trigger(dict(kind='bomb',combo=0))
        sound.restart();self.state(director,'stopped')
        self.assertEqual(director.status()['queued'],0)
        sound.resume();self.state(director,'playing')
        self.assertTrue(all(len(block)==1440*4 for out in self.outputs for block in out.blocks))
        self.assertEqual(director.status()['rejected'],0)
        sound.close();self.assertTrue(director.wait_closed())
        self.assertTrue(all(out.closed for out in self.outputs))

    def test_sword_compatibility_constructor_keeps_stream_and_output_options(self):
        director = SwordAudioDirector(asset_dir=self.root, inventory_fn=self.discover,
            physical_checker=self.physical_check, backend_factory=self.factory,
            server_provider=lambda: 'unix:/test-pulse', clock=lambda: self.now)
        self.directors.append(director)
        self.assertIsInstance(director, CubeGameAudioDirector)
        director.start();self.state(director, 'playing')
        self.assertEqual(director.status()['stream_name'], 'InkSuite sword audio')
        self.assertEqual(director.status()['mode'], 'sword')
        self.assertTrue(self.outputs[0].options['live_timestamps'])
        self.assertEqual(director.status()['block_frames'], 1440)

    def test_device_failure_auto_recovers_without_resetting_music_or_replaying_effect(self):
        with patch('audio.runtime._load_assets', wraps=_load_assets) as load:
            director = self.make();director.resume();self.state(director, 'playing')
            self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > .04))
            director.play('fight_special', key='old')
            self.assertTrue(wait_for(lambda: director.status()['voices'] > 0))
            self.physical = False;self.state(director, 'unavailable')
            failed = director.status()
            self.assertEqual(failed['output_failure_count'], 1)
            self.assertEqual(failed['output_recovery_count'], 0)
            self.assertEqual(failed['recent_output_failures'][0]['operation'], 'poll')
            self.assertIn('Cube disappeared', failed['last_output_error'])
            self.assertEqual(failed['last_output_failure_at'], 100.)
            self.assertTrue(self.outputs[0].closed)
            self.inv = inventory(instance='1-1.4:1:25:57000');self.physical = True
            for _ in range(40):director.resume()
            self.assertEqual(len(self.outputs), 1)
            self.now += 3.;director.resume();self.state(director, 'playing')
            recovered = director.status()
            self.assertEqual(recovered['output_recovery_count'], 1)
            self.assertEqual(recovered['last_output_recovery_at'], 103.)
            self.assertEqual(recovered['last_output_error'], failed['last_output_error'])
            self.assertIsNone(recovered['error'])
            self.assertEqual(recovered['voices'], 0)
            self.assertEqual(recovered['queued'], 0)
            self.assertGreaterEqual(recovered['position_seconds'], failed['position_seconds'])
            self.assertEqual(recovered['target_usb_instance'], self.inv['devices'][0]['usb_instance'])
            self.assertEqual(load.call_count, 1)

    def test_pause_blocks_recovery_and_manual_retry_never_starts_audio(self):
        director = self.make();director.start();self.state(director, 'playing')
        self.physical = False;self.state(director, 'unavailable')
        director.pause();self.now += 100.;self.physical = True
        director.retry();self.state(director, 'paused')
        for _ in range(20):director.status();director.pause()
        self.assertEqual(len(self.outputs), 1)
        self.assertEqual(director.status()['output_recovery_count'], 0)
        self.assertFalse(self.outputs[0].playing)
        director.resume();self.state(director, 'playing')
        self.assertEqual(director.status()['output_recovery_count'], 1)
        director.close();self.assertTrue(director.wait_closed())
        self.assertTrue(all(output.closed for output in self.outputs))
        self.assertFalse(director.resume())
        self.assertFalse(director.retry())
        self.assertFalse(director.status()['worker_alive'])

    def test_stable_output_poll_failure_starts_fresh_three_second_cooldown(self):
        class FaultableOutput(FakeOutput):
            broken = False
            def poll(self):
                super().poll()
                if self.broken:
                    raise RuntimeError('Stable stream poll failed')
        def factory(sink, **options):
            output = FaultableOutput(sink, **options)
            self.outputs.append(output)
            return output
        with patch('audio.runtime._load_assets', wraps=_load_assets) as load:
            director = self.make(backend_factory=factory)
            director.resume();self.state(director, 'playing')
            self.assertTrue(wait_for(lambda: self.outputs[0].frames >= 3 * 1440))
            self.assertEqual(director.status()['next_retry_at'], 103.)
            # The original creation deadline has expired. This later fault
            # must not be retried immediately by normal per-frame resume.
            self.now = 110.
            self.outputs[0].broken = True
            self.state(director, 'unavailable')
            failure = director.status()
            self.assertEqual(failure['last_output_failure_at'], 110.)
            self.assertEqual(failure['next_retry_at'], 113.)
            self.assertEqual(failure['recent_output_failures'][-1]['operation'], 'poll')
            self.assertTrue(self.outputs[0].closed)
            for _ in range(100):director.resume()
            self.assertEqual(len(self.outputs), 1)
            self.assertEqual(director.status()['automatic_retries'], 0)
            self.now = 112.999
            director.resume()
            self.assertEqual(len(self.outputs), 1)
            self.assertEqual(director.status()['automatic_retries'], 0)
            self.now = 113.
            director.resume();self.state(director, 'playing')
            self.assertEqual(len(self.outputs), 2)
            self.assertEqual(director.status()['output_recovery_count'], 1)
            self.assertEqual(director.status()['automatic_retries'], 1)
            self.assertEqual(director.status()['last_output_recovery_at'], 113.)
            self.assertEqual(director.status()['queued'], 0)
            self.assertEqual(director.status()['voices'], 0)
            self.assertEqual(load.call_count, 1)

    def test_discovery_failures_are_bounded_and_history_is_a_copy(self):
        self.inv = inventory(playable=False)
        director = self.make();director.start();self.state(director, 'unavailable')
        for attempt in range(1, 12):
            self.now += 3.;director.resume()
            self.assertTrue(wait_for(lambda: director.status()['output_failure_count'] == attempt + 1))
            self.state(director, 'unavailable')
        result = director.status()
        self.assertEqual(result['output_failure_count'], 12)
        self.assertEqual(result['automatic_retries'], 11)
        self.assertEqual(len(result['recent_output_failures']), 8)
        self.assertEqual(result['recent_output_failures'][0]['serial'], 5)
        self.assertEqual(result['recent_output_failures'][-1]['operation'], 'discovery')
        result['recent_output_failures'][0]['error'] = 'changed externally'
        self.assertNotEqual(director.status()['recent_output_failures'][0]['error'], 'changed externally')
        self.assertEqual(self.outputs, [])

    def test_one_output_failure_is_not_duplicated_by_failed_pause_cleanup(self):
        class Broken(FakeOutput):
            def poll(self):
                raise RuntimeError('Pulse output failed')
            def pause(self):
                raise RuntimeError('Failed stream cannot pause')
        def factory(sink, **options):
            output = Broken(sink, **options);self.outputs.append(output);return output
        director = self.make(backend_factory=factory)
        director.start();self.state(director, 'unavailable')
        result = director.status()
        self.assertEqual(result['output_failure_count'], 1)
        self.assertEqual(result['last_output_error'], 'Pulse output failed')
        self.assertEqual(result['recent_output_failures'][0]['operation'], 'poll')
        self.assertTrue(self.outputs[0].closed)

    def test_corrupt_asset_is_not_automatically_redecoded_or_marked_as_device_failure(self):
        self.manifest['tracks']['bgm_fight']['sha256'] = '0' * 64;self.write_manifest()
        with patch('audio.runtime._load_assets', wraps=_load_assets) as load:
            director = self.make();director.start();self.state(director, 'unavailable')
            self.now += 100.
            for _ in range(100):director.resume()
            self.assertEqual(load.call_count, 1)
            self.assertEqual(director.status()['pcm_bytes'], 0)
            self.assertEqual(director.status()['output_failure_count'], 0)
            self.assertEqual(director.status()['automatic_retries'], 0)
            self.assertEqual(self.outputs, [])

    def test_stalled_discovery_and_disabled_preview_keep_public_controls_nonblocking(self):
        def forbidden():
            self.fail('Disabled preview attempted discovery')
        disabled = self.make(enabled=False, inventory_fn=forbidden)
        disabled.status();disabled.pause();disabled.retry();self.assertFalse(disabled.resume())
        self.assertFalse(disabled.status()['worker_alive'])
        entered, release = threading.Event(), threading.Event()
        def stalled():
            entered.set();release.wait(2.);return inventory()
        director = self.make(inventory_fn=stalled);director.start()
        self.assertTrue(entered.wait(2.))
        begin = time.monotonic();director.pause();result = director.status()
        self.assertLess(time.monotonic() - begin, .1)
        self.assertEqual(result['desired'], 'paused')
        release.set();self.state(director, 'paused')
        self.assertFalse(any(output.playing for output in self.outputs))

    def test_retry_interval_rejects_bool_nonfinite_or_less_than_three_seconds(self):
        for value in (True, False, 2.999, float('nan'), float('inf')):
            with self.assertRaises(ValueError):self.make(retry_interval=value)


class LiveTimestampTests(unittest.TestCase):
    def test_live_pts_follows_pipeline_after_stall_instead_of_old_frame_count(self):
        buffers = []
        class Buffer:
            @staticmethod
            def new_allocate(_allocator, _length, _params):
                return NS(fill=lambda *_: None)
        class Source:
            def emit(self, signal, buffer):
                self.signal = signal;buffers.append(buffer);return 0
        backend = GstBackend.__new__(GstBackend)
        backend.Gst = NS(Buffer=Buffer, CLOCK_TIME_NONE=-1, SECOND=1000000000,
                         FlowReturn=NS(OK=0))
        backend.source = Source();backend.frames = 0
        backend.live_timestamps = True;backend._submitted = deque(maxlen=64)
        backend._check_route = lambda: None
        elapsed = [0];backend._running_time = lambda: elapsed[0]
        backend.push(bytes(1440 * 4))
        elapsed[0] = 500000000  # producer missed many 30 ms deadlines
        backend.push(bytes(1440 * 4))
        self.assertEqual([buffer.pts for buffer in buffers], [-1, -1])
        self.assertEqual([buffer.duration for buffer in buffers], [30000000, 30000000])
        self.assertEqual([row[0] for row in backend._submitted], [0, 500000000])
        self.assertEqual(backend.frames, 2880)
        self.assertEqual(backend.source.signal, 'push-buffer')
        self.assertEqual(backend._pending_live_frames(515000000), 720)


if __name__ == '__main__':
    unittest.main()
