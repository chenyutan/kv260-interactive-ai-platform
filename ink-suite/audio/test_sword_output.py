"""Sword routing/recovery on one real PCM worker; no GI, devices or camera."""
from array import array
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from .runtime import MemoryBackend, _load_assets
from .sword_output import SwordAudioDirector


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


def inventory(instance='1-1.4:1:24:56343', **values):
    row = dict(is_cube=True, connected=True, playable=True, vendor_id='2dc8',
               sink='cube-pulse', usb_path='1-1.4', usb_instance=instance,
               card_path='/sys/devices/usb1/1-1/1-1.4/card', pulse_index=16)
    row.update(values)
    return dict(available=True, devices=[row])


class FakeOutput(MemoryBackend):
    def __init__(self, sink, **options):
        super().__init__(sink)
        self.options = options
        self.worker = threading.get_ident()
        self.name = 'test-explicit-pulse'

    def poll(self):
        if not self.options['route_guard']():
            raise RuntimeError('Physical Cube disappeared')


class SwordOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tone = self.root / 'tone.wav'
        with wave.open(str(tone), 'wb') as output:
            output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            output.writeframes(array('h', [1200, -1200] * 48000).tobytes())
        entry = dict(file=tone.name, loop=True, gain=.72,
                     sha256=hashlib.sha256(tone.read_bytes()).hexdigest())
        self.manifest = dict(schema=1, sample_rate=48000, channels=2,
                             tracks={'bgm_sword': entry},
                             effects={name: dict(entry, loop=False) for name in (
                                 'sword_shot', 'sword_hit', 'sword_kill', 'sword_hurt',
                                 'sword_collect', 'sword_special', 'sword_boss', 'sword_win', 'sword_lose')})
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

    def make(self, **options):
        director = SwordAudioDirector(asset_dir=self.root,
            inventory_fn=options.pop('inventory_fn', self.discover),
            physical_checker=options.pop('physical_checker', self.physical_check),
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

    def test_construction_status_and_disabled_controls_do_not_discover(self):
        def forbidden():
            self.fail('Device discovery on caller')
        director = self.make(enabled=False, inventory_fn=forbidden)
        director.status();director.pause();director.retry()
        self.assertFalse(director.start())
        self.assertFalse(director.resume())
        self.assertEqual(self.outputs, [])

    def test_bgm_and_nine_effects_use_named_live_cube_stream_on_one_worker(self):
        director = self.make();director.start();self.state(director, 'playing')
        self.assertEqual(len(self.outputs), 1)
        output = self.outputs[0]
        self.assertEqual(output.options['device'], 'cube-pulse')
        self.assertEqual(output.options['server'], 'unix:/test-pulse')
        self.assertEqual(output.options['stream_name'], 'InkSuite sword audio')
        self.assertTrue(output.options['live_timestamps'])
        self.assertEqual(output.options['block_frames'],1440)
        self.assertTrue(all(len(block)==1440*4 for block in output.blocks))
        self.assertEqual(director.sink, 'pulsesink')
        self.assertEqual(director.mode, 'sword')
        for name in self.manifest['effects']:
            self.assertTrue(director.play(name, key=name))
        self.assertTrue(wait_for(lambda: director.status()['voices'] > 0))
        self.assertTrue(any(output.blocks))
        self.assertEqual(len(set(self.io_threads)), 1)
        self.assertNotEqual(output.worker, threading.get_ident())
        self.assertEqual(director.status()['target_sink'], 'cube-pulse')
        self.assertEqual(director.status()['rejected'], 0)

    def test_non_cube_stale_missing_or_unphysical_route_never_opens_output(self):
        for values in (dict(is_cube=False), dict(vendor_id='1234'), dict(playable=False),
                       dict(discovery_stale=True), dict(sink=None)):
            self.inv = inventory(**values)
            director = self.make();director.start();self.state(director, 'unavailable')
            self.assertEqual(director.status()['target_sink'], None)
            director.close();self.assertTrue(director.wait_closed())
        self.inv = inventory();self.physical = False
        director = self.make();director.start();self.state(director, 'unavailable')
        self.assertEqual(self.outputs, [])

    def test_output_retry_is_bounded_and_keeps_loaded_music(self):
        attempts = []
        def fail(sink, **options):
            attempts.append(self.now)
            raise RuntimeError('Pulse unavailable')
        with patch('audio.runtime._load_assets', wraps=_load_assets) as load:
            director = self.make(backend_factory=fail);director.start();self.state(director, 'unavailable')
            self.assertGreater(director.status()['pcm_bytes'], 0)
            for _ in range(100):director.resume()
            self.assertEqual(attempts, [100.])
            self.now += 2.99;director.start();self.assertEqual(attempts, [100.])
            self.now += .01;director.resume()
            self.assertTrue(wait_for(lambda: len(attempts) == 2));self.state(director, 'unavailable')
            for _ in range(100):director.resume()
            self.assertEqual(attempts, [100., 103.])
            self.assertEqual(load.call_count, 1)
            self.assertEqual(director.status()['automatic_retries'], 1)

    def test_short_device_loss_recovers_without_replaying_sfx_or_resetting_bgm(self):
        with patch('audio.runtime._load_assets', wraps=_load_assets) as load:
            director = self.make();director.start();self.state(director, 'playing')
            self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > .04))
            director.play('sword_special', key='old')
            self.assertTrue(wait_for(lambda: director.status()['voices'] == 1))
            self.physical = False;self.state(director, 'unavailable')
            before = director.status()['position_seconds']
            self.assertTrue(self.outputs[0].closed)
            self.inv = inventory(instance='1-1.4:1:25:57000');self.physical = True
            self.now += 3.;director.resume();self.state(director, 'playing')
            self.assertEqual(len(self.outputs), 2)
            self.assertGreaterEqual(director.status()['position_seconds'], before)
            self.assertEqual(director.status()['voices'], 0)
            self.assertEqual(director.status()['queued'], 0)
            self.assertEqual(load.call_count, 1)
            self.assertEqual(director.status()['target_usb_instance'], self.inv['devices'][0]['usb_instance'])

    def test_pause_disallows_automatic_recovery_and_retry_cannot_resume(self):
        director = self.make();director.start();self.state(director, 'playing')
        self.physical = False;self.state(director, 'unavailable')
        director.pause();self.now += 100.;self.physical = True
        for _ in range(30):director.status();director.pause()
        self.assertEqual(len(self.outputs), 1)
        director.retry();self.state(director, 'paused')
        time.sleep(.03)
        self.assertEqual(len(self.outputs), 1)
        self.assertFalse(self.outputs[0].playing)
        director.resume();self.state(director, 'playing')
        self.assertEqual(len(self.outputs), 2)

    def test_same_sink_usb_reenumeration_rejects_old_identity_then_rediscovers(self):
        director = self.make();director.start();self.state(director, 'playing')
        first = director.status()['target_usb_instance']
        self.inv = inventory(instance='1-1.4:1:25:57000')
        self.state(director, 'unavailable')
        self.assertTrue(self.outputs[0].closed)
        self.assertEqual(director.status()['target_usb_instance'], first)
        self.now += 3.;director.resume();self.state(director, 'playing')
        self.assertEqual(len(self.outputs), 2)
        self.assertNotEqual(director.status()['target_usb_instance'], first)
        self.assertEqual(director.status()['target_sink'], 'cube-pulse')

    def test_pause_flushes_effects_and_resume_preserves_music_on_same_output(self):
        director = self.make();director.start();self.state(director, 'playing')
        self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > .04))
        director.play('sword_special', key='first')
        self.assertTrue(wait_for(lambda: director.status()['voices'] == 1))
        director.pause();self.state(director, 'paused')
        before = director.status()['position_seconds']
        time.sleep(.04)
        self.assertEqual(director.status()['position_seconds'], before)
        self.assertEqual(director.status()['voices'], 0)
        self.assertFalse(director.play('sword_shot'))
        director.resume();self.state(director, 'playing')
        self.assertTrue(wait_for(lambda: director.status()['position_seconds'] > before))
        self.assertEqual(len(self.outputs), 1)
        self.assertEqual(director.status()['automatic_retries'], 0)

    def test_asset_failure_does_not_automatically_decode_again(self):
        self.manifest['tracks']['bgm_sword']['sha256'] = '0' * 64;self.write_manifest()
        with patch('audio.runtime._load_assets', wraps=_load_assets) as load:
            director = self.make();director.start();self.state(director, 'unavailable')
            self.assertEqual(director.status()['pcm_bytes'], 0)
            self.now += 100.
            for _ in range(100):director.resume()
            self.assertEqual(load.call_count, 1)
            self.assertEqual(self.outputs, [])
            self.assertEqual(director.status()['automatic_retries'], 0)

    def test_discovery_stall_does_not_block_public_pause_or_status(self):
        entered, release = threading.Event(), threading.Event()
        def stalled():
            entered.set();release.wait(2.)
            return inventory()
        director = self.make(inventory_fn=stalled);director.start()
        self.assertTrue(entered.wait(2.))
        begin = time.monotonic();director.pause();state = director.status()
        self.assertLess(time.monotonic() - begin, .1)
        self.assertEqual(state['desired'], 'paused')
        release.set();self.state(director, 'paused')
        self.assertFalse(any(output.playing for output in self.outputs))

    def test_retry_interval_rejects_fast_or_nonfinite_values(self):
        for value in (0., 2.999, float('nan'), float('inf')):
            with self.assertRaises(ValueError):self.make(retry_interval=value)


if __name__ == '__main__':
    unittest.main()
