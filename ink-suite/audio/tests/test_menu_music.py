"""Main-menu lifecycle and explicit Pulse routing; no GI or audible output."""
from array import array
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from audio.menu_music import MenuMusic
from audio.runtime import AudioDirector, GstBackend, MemoryBackend, _Cancelled


def wait_for(predicate, timeout=3.):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


def device(instance='1-1.4:1:24:56343', index=16, **values):
    row = dict(is_cube=True, connected=True, playable=True, vendor_id='2dc8',
               sink='alsa_output.usb-Actions_8BitDo_Cube_2.analog-stereo',
               usb_path='1-1.4', usb_instance=instance,
               card_path='/sys/devices/usb1/1-1/1-1.4/1-1.4:1.1', pulse_index=index)
    row.update(values)
    return {'devices': [row], 'available': True}


class FakeOutput(MemoryBackend):
    def __init__(self, sink, **values):
        super().__init__(sink)
        self.options = values
        self.thread_id = threading.get_ident()
        self.name = 'fake-explicit-pulse'

    def poll(self):
        if not self.options['route_guard']():
            raise RuntimeError('USB disappeared')


class MenuMusicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root/'bgm_ink.wav'
        pcm = array('h', [1800, -1800] * 48000).tobytes()
        with wave.open(str(path), 'wb') as out:
            out.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            out.writeframes(pcm)
        entry = dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), loop=True, gain=.72)
        # Missing effects are intentional: menu music must not load scene SFX.
        (self.root/'manifest.json').write_text(json.dumps(dict(schema=1, sample_rate=48000,
             channels=2, tracks={'bgm_ink': entry}, effects={'unused': {'file': 'missing.wav'}})))
        self.outputs = []
        self.physical = True
        self.now = 100.

    def factory(self, sink, **values):
        output = FakeOutput(sink, **values)
        self.outputs.append(output)
        return output

    def menu(self, **values):
        menu = MenuMusic(asset_dir=self.root, backend_factory=values.pop('backend_factory', self.factory),
                         physical_checker=lambda route: self.physical,
                         clock=lambda: self.now, **values)
        self.addCleanup(menu.wait_closed)
        self.addCleanup(menu.close)
        return menu

    def playing(self, menu):
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'playing'), menu.status())

    def test_main_menu_waits_for_verified_cube_and_routes_named_stream(self):
        menu = self.menu();menu.set_menu_open(True)
        for row in [device(is_cube=False), device(vendor_id='1234'), device(playable=False), {'devices': []}]:
            self.assertFalse(menu.update_inventory(row))
        self.assertEqual(self.outputs, [])
        self.assertEqual(menu.status()['pcm_bytes'], 0)
        self.assertTrue(menu.update_inventory({'audio': {'inventory': device()}}))
        self.playing(menu)
        self.assertEqual(len(self.outputs), 1)
        output = self.outputs[0]
        self.assertEqual(output.options['device'], device()['devices'][0]['sink'])
        self.assertEqual(output.options['stream_name'], 'InkSuite menu music')
        self.assertTrue(output.options['live_timestamps'])
        self.assertNotEqual(output.thread_id, threading.get_ident())
        self.assertEqual(menu.status()['levels']['effects'], 0.)
        self.assertTrue(any(output.blocks))

    def test_exit_flushes_then_return_continues_position_on_one_output(self):
        menu = self.menu();menu.update_inventory(device());menu.set_menu_open(True);self.playing(menu)
        self.assertTrue(wait_for(lambda: menu.status()['position_seconds'] > .03))
        menu.set_menu_open(False)
        self.assertTrue(wait_for(lambda: menu.status()['silenced']))
        paused = menu.status()['position_seconds'];time.sleep(.04)
        self.assertEqual(menu.status()['position_seconds'], paused)
        self.assertFalse(self.outputs[0].playing)
        menu.set_menu_open(True);self.playing(menu)
        self.assertTrue(wait_for(lambda: menu.status()['position_seconds'] > paused))
        self.assertEqual(len(self.outputs), 1)

    def test_usb_loss_stops_without_waiting_for_next_inventory_and_recovers(self):
        menu = self.menu();menu.update_inventory(device());menu.set_menu_open(True);self.playing(menu)
        self.physical = False
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        self.assertTrue(self.outputs[0].closed)
        menu.update_inventory({'devices': []});self.assertTrue(wait_for(lambda: menu.status()['silenced']))
        self.physical = True
        menu.update_inventory(device(instance='1-1.4:1:25:57000', index=17));self.playing(menu)
        self.assertEqual(len(self.outputs), 2)
        self.assertFalse(self.outputs[1].closed)

    def test_same_name_reenumeration_replaces_only_owned_output_once(self):
        menu = self.menu();menu.update_inventory(device());menu.set_menu_open(True);self.playing(menu)
        new = device(instance='1-1.4:1:25:57000', index=17)
        menu.update_inventory(new);self.assertTrue(wait_for(lambda: len(self.outputs) == 2));self.playing(menu)
        self.assertTrue(self.outputs[0].closed)
        for _ in range(100):menu.update_inventory(new)
        self.assertEqual(len(self.outputs), 2)

    def test_failed_output_retry_is_bounded_and_manual_retry_is_available(self):
        attempts = []
        def failing(*args, **values):
            attempts.append(self.now)
            raise RuntimeError('Pulse unavailable')
        menu = self.menu(backend_factory=failing);menu.update_inventory(device());menu.set_menu_open(True)
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        for _ in range(100):menu.update_inventory(device())
        self.assertEqual(len(attempts), 1)
        self.now += 5.;menu.update_inventory(device())
        self.assertTrue(wait_for(lambda: len(attempts) == 2))
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        menu.retry();self.assertTrue(wait_for(lambda: len(attempts) == 3))
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        for _ in range(100):menu.update_inventory(device())
        self.assertEqual(len(attempts), 3)

    def test_paused_menu_does_not_retry_failed_output(self):
        attempts = []
        def failing(*args, **values):attempts.append(1);raise RuntimeError('Pulse unavailable')
        menu = self.menu(backend_factory=failing);menu.update_inventory(device());menu.set_menu_open(True)
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        menu.set_menu_open(False);self.now += 100
        for _ in range(20):menu.update_inventory(device())
        self.assertTrue(wait_for(lambda: menu.status()['silenced']))
        self.assertEqual(len(attempts), 1)

    def test_cached_discovery_cannot_create_a_new_output(self):
        menu = self.menu();menu.update_inventory(device(discovery_stale=True));menu.set_menu_open(True)
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        self.assertEqual(self.outputs, [])
        menu.update_inventory(device());menu.retry();self.playing(menu)
        self.assertEqual(len(self.outputs), 1)

    def test_cached_discovery_keeps_existing_healthy_output(self):
        menu = self.menu();menu.update_inventory(device());menu.set_menu_open(True);self.playing(menu)
        menu.update_inventory(device(discovery_stale=True));time.sleep(.03)
        self.assertEqual(menu.status()['state'], 'playing')
        self.assertEqual(len(self.outputs), 1)
        self.assertTrue(menu.status()['discovery_stale'])

    def test_menu_exit_and_close_do_not_wait_for_blocked_loading(self):
        entered = threading.Event();release = threading.Event()
        def blocked(root, mode, cancelled, include_effects=True):
            entered.set();release.wait(2)
            if cancelled():raise _Cancelled()
            raise AssertionError('Paused load was not cancelled')
        with patch('audio.runtime._load_assets', side_effect=blocked):
            menu = self.menu();menu.update_inventory(device());menu.set_menu_open(True)
            self.assertTrue(entered.wait(2))
            start = time.monotonic();menu.set_menu_open(False)
            self.assertLess(time.monotonic()-start, .1)
            self.assertFalse(menu.status()['silenced'])
            start = time.monotonic();menu.close();self.assertLess(time.monotonic()-start, .1)
            release.set();self.assertTrue(menu.wait_closed(2))
        self.assertEqual(self.outputs, [])

    def test_pause_acknowledgement_is_not_an_old_paused_state(self):
        entered = threading.Event();release = threading.Event()
        class BlockingResume(FakeOutput):
            def resume(self):entered.set();release.wait(2);super().resume()
        menu = self.menu(backend_factory=lambda sink, **values: BlockingResume(sink, **values))
        menu.update_inventory(device());self.assertTrue(wait_for(lambda: menu.status()['state'] == 'paused'))
        menu.set_menu_open(True);self.assertTrue(entered.wait(2))
        menu.set_menu_open(False)
        self.assertFalse(menu.status()['silenced'])
        release.set();self.assertTrue(wait_for(lambda: menu.status()['silenced']))
        self.assertFalse(menu.status()['output_active'])

    def test_close_releases_output_and_rejects_later_controls(self):
        menu = self.menu();menu.update_inventory(device());menu.set_menu_open(True);self.playing(menu)
        menu.close();self.assertTrue(menu.wait_closed(2));self.assertTrue(self.outputs[0].closed)
        self.assertFalse(menu.set_menu_open(True));self.assertFalse(menu.update_inventory(device()));self.assertFalse(menu.retry())
        self.assertEqual(menu.status()['state'], 'closed')

    def test_close_during_backend_creation_cannot_ack_then_resume_output(self):
        entered = threading.Event();release = threading.Event();outputs = []
        def blocked_factory(sink, **values):
            output = FakeOutput(sink, **values);outputs.append(output)
            entered.set();release.wait(2);return output
        menu = self.menu(backend_factory=blocked_factory)
        menu.update_inventory(device());menu.set_menu_open(True)
        self.assertTrue(entered.wait(2));menu.close()
        self.assertFalse(menu.status()['silenced'])
        release.set();self.assertTrue(menu.wait_closed(2))
        self.assertTrue(outputs[0].closed)
        self.assertEqual(outputs[0].frames, 0)
        self.assertFalse(outputs[0].playing)
        self.assertTrue(menu.status()['silenced'])

    def test_poll_error_still_closes_output_when_pause_also_fails(self):
        outputs = []
        class BrokenPause(FakeOutput):
            def poll(self):raise RuntimeError('Output disappeared')
            def pause(self):raise RuntimeError('Cannot pause failed output')
        def factory(sink, **values):
            result = BrokenPause(sink, **values);outputs.append(result);return result
        menu = self.menu(backend_factory=factory);menu.update_inventory(device());menu.set_menu_open(True)
        self.assertTrue(wait_for(lambda: menu.status()['state'] == 'unavailable'))
        self.assertEqual(len(outputs), 1);self.assertTrue(outputs[0].closed)
        menu.set_menu_open(False);self.assertTrue(wait_for(lambda: menu.status()['silenced']))

    def test_existing_director_defaults_still_load_effects_and_use_old_factory_api(self):
        data = json.loads((self.root/'manifest.json').read_text())
        data['effects'] = {'test_effect': dict(data['tracks']['bgm_ink'], loop=False)}
        (self.root/'manifest.json').write_text(json.dumps(data))
        output = MemoryBackend()
        director = AudioDirector('ink', asset_dir=self.root, sink='memory',
                                 backend_factory=lambda sink: output)
        self.addCleanup(director.wait_closed);self.addCleanup(director.close)
        director.resume();self.assertTrue(wait_for(lambda: director.status()['state'] == 'playing'))
        self.assertTrue(director.play('test_effect', key='old-api'))
        self.assertTrue(wait_for(lambda: director.status()['voices'] == 1))
        director.pause();self.assertTrue(wait_for(lambda: director.status()['state'] == 'paused'))
        self.assertFalse(director.status()['output_active'])
        self.assertEqual(director.status()['acknowledged_flush'], director.status()['flush_generation'])


class PulseRouteTests(unittest.TestCase):
    def backend(self, current='cube', guard=lambda: True):
        backend = GstBackend.__new__(GstBackend)
        backend.device = 'cube';backend._route_guard = guard;backend._next_route_check = 0.
        backend.output = type('Output', (), {'get_property': lambda self, key: current})()
        return backend

    def test_migrated_pulse_device_is_rejected_before_more_pcm(self):
        with self.assertRaisesRegex(RuntimeError, 'moved audio'):
            self.backend(current='hdmi')._check_route()

    def test_missing_usb_is_rejected_even_if_pulse_still_reports_old_sink(self):
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            self.backend(guard=lambda: False)._check_route(force=True)

    def test_explicit_routing_rejects_other_sink_factories_before_gi_import(self):
        with self.assertRaisesRegex(ValueError, 'requires pulsesink'):
            GstBackend('autoaudiosink', device='cube')
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            GstBackend('pulsesink', device='')

    def test_healthy_route_is_local_and_allowed(self):
        self.backend()._check_route()

    def gst_fixture(self):
        elements = {}
        class Element:
            def __init__(self):self.properties = {};self.items = []
            def set_property(self, name, value):self.properties[name] = value
            def get_property(self, name):return self.properties.get(name)
            def find_property(self, name):return True
            def add(self, item):self.items.append(item)
            def link(self, other):return True
            def get_bus(self):return object()
            def set_state(self, state):self.state = state;return 'ok'
        class Properties(dict):
            def set_value(self, name, value):self[name] = value
        def make(kind, name):
            result = Element();elements[kind] = result;return result
        gst = SimpleNamespace(init=lambda args: None, SECOND=10**9,
            Pipeline=SimpleNamespace(new=lambda name: Element()),
            ElementFactory=SimpleNamespace(make=make),
            Caps=SimpleNamespace(from_string=lambda value: value),
            Structure=SimpleNamespace(new_empty=lambda name: Properties()),
            Format=SimpleNamespace(TIME='time'), State=SimpleNamespace(NULL='null', PLAYING='playing'),
            StateChangeReturn=SimpleNamespace(FAILURE='failure'))
        modules = {'gi': SimpleNamespace(require_version=lambda *args: None),
                   'gi.repository': SimpleNamespace(Gst=gst)}
        return elements, patch.dict(sys.modules, modules)

    def test_gst_explicit_device_server_and_identifiable_stream_are_configured(self):
        elements, mocked = self.gst_fixture()
        with mocked:
            backend = GstBackend('pulsesink', device='verified-cube', server='unix:/pulse/native',
                                 stream_name='InkSuite menu music', route_guard=lambda: True)
            props = elements['pulsesink'].properties
            self.assertEqual(props['device'], 'verified-cube')
            self.assertEqual(props['server'], 'unix:/pulse/native')
            self.assertEqual(props['stream-properties']['media.name'], 'InkSuite menu music')
            self.assertEqual(props['stream-properties']['application.name'], 'InkSuite menu music')
            self.assertEqual(props['stream-properties']['media.role'], 'music')
            self.assertNotIn('volume', props);self.assertNotIn('mute', props)
            backend.resume();backend.close()
            self.assertEqual(backend.pipeline.state, 'null')

    def test_default_gst_output_behavior_remains_unchanged(self):
        elements, mocked = self.gst_fixture()
        with mocked:
            backend = GstBackend('autoaudiosink')
            props = elements['autoaudiosink'].properties
            self.assertNotIn('device', props);self.assertNotIn('server', props)
            self.assertNotIn('stream-properties', props)
            self.assertFalse(elements['appsrc'].properties['do-timestamp'])
            backend._check_route();backend.close()

    def test_live_timestamps_recover_from_scheduler_stall_without_old_frame_pts(self):
        elements, mocked = self.gst_fixture()
        with mocked:
            backend = GstBackend('pulsesink', device='cube', live_timestamps=True)
            self.assertTrue(elements['appsrc'].properties['do-timestamp'])
            self.assertEqual(elements['appsrc'].properties['min-latency'], 0)
            now = [1_000_000_000];buffers = []
            class Buffer:
                def fill(self, offset, data):pass
            backend.Gst.CLOCK_TIME_NONE = -1
            backend.Gst.Buffer = SimpleNamespace(new_allocate=lambda *args: Buffer())
            backend.Gst.FlowReturn = SimpleNamespace(OK='ok')
            backend.pipeline.get_clock = lambda: SimpleNamespace(get_time=lambda: now[0])
            backend.pipeline.get_base_time = lambda: 1_000_000_000
            def push(name, buffer):
                # Model appsrc's documented do-timestamp behavior.
                if buffer.pts == -1:buffer.pts = backend._running_time()
                buffers.append(buffer);return 'ok'
            backend.source.emit = push
            backend.push(bytes(480*4));now[0] += 500_000_000
            backend.push(bytes(480*4))
            self.assertEqual([b.pts for b in buffers], [0, 500_000_000])
            self.assertEqual(backend.frames, 960)
            self.assertEqual(backend._pending_live_frames(500_000_000), 480)
            self.assertEqual(backend._pending_live_frames(510_000_000), 0)
            # The second block follows the running clock despite only 10ms of
            # logical PCM before a 500ms scheduling stall.
            self.assertGreater(buffers[1].pts, 10_000_000)
            backend.close()


if __name__ == '__main__':unittest.main()
