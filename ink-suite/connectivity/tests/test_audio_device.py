"""No-speaker tests for physical-device verification and worker lifetime."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connectivity.audio_device import AudioKeepalive, inventory


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def snapshot(sink='cube-output', cube=True, playable=True):
    return {'available': True, 'error': '', 'sampled_at': 123,
            'devices': [] if sink is None else [{
                'id': 'pulse:' + sink, 'name': 'Cube', 'sink': sink,
                'is_cube': cube, 'connected': True, 'playable': playable}]}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.resolved_cards = {}
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            return self.resolved_cards.get(str(path), original_resolve(path, *args, **kwargs))
        mock = patch.object(Path, 'resolve', resolve)
        mock.start()
        self.addCleanup(mock.stop)

    def usb(self, name='1-1.4', vendor='2dc8', pid='2054', product='8BitDo Cube 2', card='1'):
        path = self.root / 'bus' / 'usb' / 'devices' / name
        path.mkdir(parents=True, exist_ok=True)
        for filename, value in [('idVendor', vendor), ('idProduct', pid), ('product', product)]:
            (path / filename).write_text(value)
        if card is not None:
            interface = path / 'interface1'
            interface.mkdir()
            link = self.root / 'class' / 'sound' / ('card' + card)
            link.mkdir(parents=True, exist_ok=True)
            (link / 'device').mkdir()
            # Windows without Developer Mode cannot create sysfs-like links.
            # Resolve just this card link to its physical USB interface instead.
            self.resolved_cards[str(link / 'device')] = interface

    def scan(self, sinks):
        return inventory(sysfs_root=self.root, sink_reader=lambda timeout: sinks)

    def test_verifies_cube_using_exact_sound_card_usb_ancestor(self):
        self.usb()
        result = self.scan([{'name': 'cube-output', 'description': 'Cube 2',
                             'state': 'SUSPENDED', 'properties': {'alsa.card': '1'}}])
        self.assertTrue(result['available'])
        self.assertEqual(len(result['devices']), 1)
        cube = result['devices'][0]
        self.assertTrue(cube['is_cube'])
        self.assertTrue(cube['playable'])
        self.assertEqual(cube['usb_path'], '1-1.4')
        self.assertEqual(cube['state'], 'SUSPENDED')

    def test_vendor_and_pid_alone_do_not_authorize_controller(self):
        self.usb(pid='2056', product='8BitDo Ultimate Controller')
        device = self.scan([{'name': 'spoof-Cube-2', 'properties': {
            'alsa.card': '1', 'device.product.name': '8BitDo Cube 2',
            'device.vendor.id': '2dc8', 'device.product.id': '2054'}}])['devices'][0]
        self.assertFalse(device['is_cube'])

    def test_cube_name_on_another_vendor_is_not_cube(self):
        self.usb(vendor='1234')
        device = self.scan([{'name': 'cube-output', 'properties': {'alsa.card': '1'}}])['devices'][0]
        self.assertFalse(device['is_cube'])

    def test_unmapped_usb_cube_is_present_but_not_playable(self):
        self.usb(card=None)
        result = self.scan([])
        self.assertEqual(len(result['devices']), 1)
        self.assertTrue(result['devices'][0]['connected'])
        self.assertFalse(result['devices'][0]['playable'])
        self.assertIsNone(result['devices'][0]['sink'])

    def test_pulse_failure_preserves_physical_cube_and_bounded_error(self):
        self.usb(card=None)
        def fail(timeout):
            raise RuntimeError('Unavailable' * 100)
        result = inventory(sysfs_root=self.root, sink_reader=fail)
        self.assertFalse(result['available'])
        self.assertTrue(result['devices'][0]['is_cube'])
        self.assertLessEqual(len(result['error']), 512)

    def test_no_sysfs_link_does_not_trust_pulse_cube_properties(self):
        result = self.scan([{'name': 'cube-output', 'properties': {
            'alsa.card': '9', 'device.bus': 'usb', 'device.vendor.id': '2dc8',
            'device.product.name': '8BitDo Cube 2'}}])
        self.assertFalse(result['devices'][0]['is_cube'])

    def test_usb_disappearing_during_pulse_timeout_is_not_reported_present(self):
        self.usb(card=None)
        def unplug_then_fail(timeout):
            (self.root / 'bus' / 'usb' / 'devices' / '1-1.4' / 'idVendor').unlink()
            raise RuntimeError('pactl timed out')
        result = inventory(sysfs_root=self.root, sink_reader=unplug_then_fail)
        self.assertFalse(result['available'])
        self.assertEqual(result['devices'], [])

    def test_inactive_port_is_unplayable_and_bluetooth_is_listed(self):
        result = self.scan([{'name': 'bluez_output.address.a2dp',
            'properties': {'device.bus': 'bluetooth'}, 'active_port': 'speaker',
            'ports': [{'name': 'speaker', 'availability': 'not available'}]}])
        device = result['devices'][0]
        self.assertEqual(device['transport'], 'bluetooth')
        self.assertFalse(device['connected'])
        self.assertFalse(device['playable'])

    def test_invalid_pulse_shape_is_reported(self):
        result = self.scan({'unexpected': 'object'})
        self.assertFalse(result['available'])
        self.assertEqual(result['devices'], [])


class FakeBackend:
    def __init__(self, sink):
        self.sink = sink
        self.closed = False
        self.failure = None
        self.thread = threading.get_ident()

    def poll(self):
        if self.failure:
            raise RuntimeError(self.failure)

    def close(self):
        self.closed = True


class KeeperTests(unittest.TestCase):
    def keeper(self, inv=None, factory=None, interval=0.05, retry=0.05):
        self.backends = []
        def create(sink):
            backend = FakeBackend(sink)
            self.backends.append(backend)
            return backend
        keeper = AudioKeepalive(inventory_fn=inv or snapshot,
            backend_factory=factory or create, scan_interval=interval,
            retry_interval=retry)
        self.addCleanup(lambda: (keeper.close(), keeper.wait_closed()))
        return keeper

    def test_constructor_start_controls_and_status_do_not_scan_on_caller(self):
        caller = threading.get_ident()
        threads = []
        def scan():
            threads.append(threading.get_ident())
            return snapshot()
        keeper = self.keeper(inv=scan)
        self.assertEqual(threads, [])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        keeper.start()
        self.assertTrue(all(thread != caller for thread in threads))
        self.assertEqual(len(self.backends), 1)
        self.assertNotEqual(self.backends[0].thread, caller)
        self.assertEqual(keeper.status()['policy'], 'silent')
        self.assertEqual(keeper.status()['firmware_standby'], 'unverified')

    def test_absence_and_other_audio_outputs_are_quietly_ignored(self):
        state = snapshot(cube=False)
        keeper = self.keeper(inv=lambda: state)
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['inventory']['sampled_at'] == 123))
        self.assertEqual(self.backends, [])
        self.assertEqual(keeper.status()['state'], 'waiting')
        self.assertEqual(keeper.status()['last_error'], '')

    def test_unplug_and_replug_with_new_sink_closes_old_and_reconnects(self):
        state = [snapshot()]
        keeper = self.keeper(inv=lambda: state[0])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        first = self.backends[0]
        state[0] = snapshot(None)
        self.assertTrue(wait_for(lambda: first.closed))
        self.assertEqual(keeper.status()['state'], 'waiting')
        state[0] = snapshot('cube-new-card')
        self.assertTrue(wait_for(lambda: keeper.status()['sink'] == 'cube-new-card'))
        self.assertEqual(len(self.backends), 2)
        self.assertEqual(keeper.status()['reconnects'], 1)

    def test_disabled_stops_stream_and_enable_creates_one(self):
        keeper = self.keeper()
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        keeper.set_enabled(False)
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'disabled'))
        self.assertTrue(self.backends[0].closed)
        keeper.set_enabled(True)
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        self.assertEqual(len(self.backends), 2)

    def test_failures_back_off_and_retry_request_bypasses_backoff(self):
        calls = []
        def fail(sink):
            calls.append(time.monotonic())
            raise RuntimeError('No Pulse server')
        keeper = self.keeper(factory=fail, retry=1)
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'error'))
        time.sleep(0.15)
        self.assertEqual(len(calls), 1)
        keeper.retry()
        self.assertTrue(wait_for(lambda: len(calls) == 2))
        self.assertEqual(keeper.status()['last_error'], 'No Pulse server')

    def test_stream_bus_error_closes_and_recreates_after_backoff(self):
        keeper = self.keeper()
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        old = self.backends[0]
        old.failure = 'USB disappeared'
        self.assertTrue(wait_for(lambda: old.closed))
        self.assertTrue(wait_for(lambda: len(self.backends) == 2))
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        self.assertIsNot(self.backends[1], old)
        self.assertFalse(self.backends[1].closed)
        self.assertEqual(keeper.status()['attempts'], 2)
        self.assertEqual(keeper.status()['reconnects'], 1)

    def physical_snapshot(self, instance='cube:1:18:52914', available=True, card_path='/sys/cube/interface'):
        snap = snapshot()
        snap['available'] = available
        snap['devices'][0].update(usb_path='1-1.4', usb_instance=instance,
                                 card_path=card_path, card_paths=[card_path], pulse_index=7)
        if not available:
            snap['error'] = 'pactl timed out'
            snap['devices'][0].update(sink=None, playable=False, card_path=None)
        return snap

    def test_pulse_discovery_timeout_preserves_healthy_exact_usb_stream(self):
        current = [self.physical_snapshot()]
        keeper = self.keeper(inv=lambda: current[0])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        original = self.backends[0]
        current[0] = self.physical_snapshot(available=False)
        self.assertTrue(wait_for(lambda: keeper.status()['discovery_error'] == 'pactl timed out'))
        self.assertEqual(keeper.status()['state'], 'active')
        self.assertFalse(original.closed)
        self.assertEqual(keeper.status()['attempts'], 1)
        cached = keeper.status()['inventory']
        self.assertTrue(cached['discovery_stale'])
        self.assertFalse(cached['available'])
        # Same predicate used by the existing controller: do not falsely open
        # an unavailable-device prompt during one transient Pulse query failure.
        cubes = [d for d in cached['devices'] if d.get('is_cube') and d.get('playable')]
        self.assertEqual(len(cubes), 1)
        self.assertTrue(cubes[0]['discovery_stale'])
        self.assertEqual(cubes[0]['state'], 'STREAMING_CACHED')
        current[0] = self.physical_snapshot()
        self.assertTrue(wait_for(lambda: keeper.status()['discovery_error'] == ''))
        self.assertEqual(len(self.backends), 1)

    def test_pulse_failure_with_physical_usb_missing_closes_old_stream(self):
        current = [self.physical_snapshot()]
        keeper = self.keeper(inv=lambda: current[0])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        original = self.backends[0]
        current[0] = {'available': False, 'error': 'timeout', 'devices': []}
        self.assertTrue(wait_for(lambda: original.closed))
        self.assertEqual(keeper.status()['state'], 'waiting')
        self.assertFalse(keeper.status()['inventory'].get('discovery_stale', False))
        self.assertEqual(keeper.status()['inventory']['devices'], [])

    def test_same_usb_port_with_new_instance_does_not_preserve_stale_stream(self):
        current = [self.physical_snapshot()]
        keeper = self.keeper(inv=lambda: current[0])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        original = self.backends[0]
        current[0] = self.physical_snapshot(instance='cube:1:19:52999', available=False)
        self.assertTrue(wait_for(lambda: original.closed))
        self.assertEqual(keeper.status()['state'], 'waiting')

    def test_stale_confirmed_output_is_removed_when_backend_fails(self):
        current = [self.physical_snapshot()]
        keeper = self.keeper(inv=lambda: current[0])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        original = self.backends[0]
        current[0] = self.physical_snapshot(available=False)
        self.assertTrue(wait_for(lambda: keeper.status()['inventory'].get('discovery_stale')))
        original.failure = 'Pulse connection lost'
        self.assertTrue(wait_for(lambda: original.closed))
        status = keeper.status()
        self.assertFalse(status['inventory'].get('discovery_stale', False))
        self.assertFalse(any(d.get('playable') for d in status['inventory']['devices']))

    def test_reenumeration_with_same_sink_name_recreates_stream(self):
        current = [self.physical_snapshot()]
        keeper = self.keeper(inv=lambda: current[0])
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        original = self.backends[0]
        current[0] = self.physical_snapshot(instance='cube:1:19:52999')
        self.assertTrue(wait_for(lambda: original.closed))
        self.assertTrue(wait_for(lambda: len(self.backends) == 2))
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))

    def test_snapshot_mutation_does_not_change_cached_state(self):
        keeper = self.keeper()
        keeper.start()
        self.assertTrue(wait_for(lambda: keeper.status()['state'] == 'active'))
        status = keeper.status()
        status['inventory']['devices'].clear()
        self.assertEqual(len(keeper.status()['inventory']['devices']), 1)

    def test_close_is_nonblocking_even_during_inventory_and_cannot_restart(self):
        entered, release = threading.Event(), threading.Event()
        def scan():
            entered.set()
            release.wait(2)
            return snapshot()
        keeper = self.keeper(inv=scan)
        keeper.start()
        self.assertTrue(entered.wait(1))
        before = time.monotonic()
        keeper.close()
        self.assertLess(time.monotonic() - before, 0.1)
        self.assertFalse(keeper.retry())
        self.assertFalse(keeper.start())
        release.set()
        self.assertTrue(keeper.wait_closed())
        self.assertEqual(keeper.status()['state'], 'closed')
        self.assertEqual(self.backends, [])

    def test_close_without_start_completes_and_invalid_intervals_fail(self):
        keeper = self.keeper()
        keeper.close()
        self.assertTrue(keeper.wait_closed(0))
        self.assertFalse(keeper.start())
        with self.assertRaises(ValueError):
            AudioKeepalive(scan_interval=0)


if __name__ == '__main__':
    unittest.main()
