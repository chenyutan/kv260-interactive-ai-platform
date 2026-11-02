"""Offline lifecycle coverage: no hardware, network changes, or audio output."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connectivity.controller import ConnectivityController, reconnect_camera, camera_inventory
from connectivity.component_policy import ComponentPolicy


class FakeAudio:
    def __init__(self):
        self.started = 0
        self.closed = 0
        self.retry_count = 0
        self.report = {'inventory': {'devices': []}, 'active': False}

    def start(self): self.started += 1
    def close(self): self.closed += 1
    def retry(self): self.retry_count += 1
    def status(self): return self.report


class CameraInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.usb=self.root/'usb';self.usb.mkdir()
        self.video=self.root/'video';self.video.mkdir()

    def device(self,vendor='0bda',product_id='5858',product='USB Camera3',port='2-1.4'):
        d=self.usb/port;d.mkdir()
        for name,value in (('idVendor',vendor),('idProduct',product_id),('product',product)):
            (d/name).write_text(value)
        return d

    def inventory(self):
        return camera_inventory(self.video,self.usb)

    def test_resetting_verified_camera_stays_physically_connected_without_video_nodes(self):
        self.device()
        result=self.inventory()
        self.assertTrue(result['connected'])
        self.assertTrue(result['recovering'])
        self.assertFalse(result['video_nodes_available'])
        self.assertEqual(result['nodes'],[])
        self.assertNotIn('ready',result)
        self.assertNotIn('capture_ready',result)
        self.assertIn('正在恢复',result['detail'])
        policy=ComponentPolicy(initial_delay=0,loss_debounce=2)
        self.assertEqual(policy.update({'camera':result},0),[])
        self.assertEqual(policy.update({'camera':result},3),[])

    def test_real_usb_unplug_still_produces_one_missing_camera_alert(self):
        d=self.device()
        policy=ComponentPolicy(initial_delay=0,loss_debounce=2)
        policy.update({'camera':self.inventory()},0)
        for p in d.iterdir():p.unlink()
        d.rmdir()
        missing=self.inventory()
        self.assertFalse(missing['connected'])
        self.assertFalse(missing['recovering'])
        self.assertEqual(policy.update({'camera':missing},1),[])
        alerts=policy.update({'camera':missing},3)
        self.assertEqual([a.key for a in alerts],['camera'])
        self.assertEqual(policy.update({'camera':missing},5),[])

    def test_usb_video_interface_supports_other_webcams_without_guessing_product(self):
        self.device(vendor='1234',product_id='5678',product='Other webcam')
        # Colons in Linux interface names are forbidden on Windows. Keep the
        # metadata real, and inject only that glob result for portable tests.
        interface=self.root/'interface';interface.mkdir()
        (interface/'bInterfaceClass').write_text('0e')
        original_glob=Path.glob
        def glob(path,pattern):
            return iter([interface]) if path==self.usb and pattern=='2-1.4:*' else original_glob(path,pattern)
        with patch.object(Path,'glob',autospec=True,side_effect=glob):
            self.assertTrue(self.inventory()['connected'])
            (interface/'bInterfaceClass').write_text('03')
            self.assertFalse(self.inventory()['connected'])

    def test_product_label_or_unrelated_usb_receiver_cannot_fake_camera_presence(self):
        self.device(vendor='2dc8',product_id='2054',product='USB Camera3')
        self.assertFalse(self.inventory()['connected'])

    def test_stale_video_class_entry_without_physical_device_does_not_fake_connection(self):
        (self.video/'video7').mkdir()
        self.assertFalse(self.inventory()['connected'])

    def test_returned_video_node_is_metadata_only_and_does_not_establish_inference_ready(self):
        d=self.device(vendor='1234',product_id='5678',product='Generic UVC')
        # Model resolved sysfs ancestry without symlinks/administrator privileges.
        self.video=d/'video4linux';self.video.mkdir()
        node=self.video/'video7';(node/'device').mkdir(parents=True)
        result=self.inventory()
        self.assertEqual(result['nodes'],['/dev/video7'])
        self.assertTrue(result['video_nodes_available'])
        self.assertFalse(result['recovering'])
        self.assertNotIn('ready',result)
        self.assertNotIn('capture_ready',result)


class ControllerTests(unittest.TestCase):
    def build(self, **kwargs):
        self.wifi = Mock()
        self.wifi.inventory.return_value = {'available': True, 'active': None}
        self.audio = FakeAudio()
        self.published, self.results, self.dispatched = [], [], []
        def dispatch(fn, *args):
            self.dispatched.append((fn, args))
            return fn(*args)
        self.controller = ConnectivityController(
            lambda value: self.published.append(value),
            lambda *args: self.results.append(args), dispatch,
            wifi=self.wifi, audio=self.audio,
            camera=kwargs.pop('camera', lambda: {'connected': True}), **kwargs)
        self.addCleanup(self.controller.close)
        return self.controller

    def test_request_deduplicates_all_mutations_and_rejects_after_close(self):
        c = self.build()
        token = c.request('connect', network={'ssid': 'test'}, password='secret')
        self.assertIsInstance(token, int)
        for operation in ('scan', 'connect', 'camera_retry', 'audio_retry'):
            self.assertIsNone(c.request(operation))
        self.assertEqual(c.requests.qsize(), 1)
        c.close()
        self.assertIsNone(c.request('scan'))

    def test_start_is_idempotent_and_audio_worker_lifetime_is_independent(self):
        c = self.build(period=100)
        observed = threading.Event()
        self.wifi.inventory.side_effect = lambda: (observed.set() or {'available': True})
        c.start()
        c.start()
        self.assertTrue(observed.wait(2))
        self.assertEqual(self.audio.started, 1)
        c.close()
        c.thread.join(2)
        self.assertFalse(c.thread.is_alive())

    def test_close_before_start_does_not_launch_audio_or_worker(self):
        c = self.build()
        c.close()
        c.start()
        self.assertEqual(self.audio.started, 0)
        self.assertIsNone(c.thread)

    def test_wifi_and_audio_absence_do_not_mark_camera_missing(self):
        c = self.build()
        c._inventory()
        snapshot = self.published[-1]
        self.assertTrue(snapshot['camera']['connected'])
        self.assertFalse(snapshot['wifi']['connected'])
        self.assertFalse(snapshot['audio']['connected'])

    def test_stale_wifi_active_without_adapter_is_not_connected(self):
        c = self.build()
        self.wifi.inventory.return_value = {'available': False, 'active': {'ssid': 'old'}}
        c._inventory()
        self.assertFalse(self.published[-1]['wifi']['connected'])

    def test_idle_cube_is_connected_but_other_sinks_do_not_count(self):
        c = self.build()
        self.audio.report = {'inventory': {'devices': [
            {'is_cube': False, 'playable': True},
            {'is_cube': True, 'playable': True, 'state': 'SUSPENDED'},
        ]}}
        c._inventory()
        self.assertTrue(self.published[-1]['audio']['connected'])
        self.audio.report['inventory']['devices'].pop()
        c._inventory()
        self.assertFalse(self.published[-1]['audio']['connected'])

    def test_inventory_errors_are_fail_closed_without_killing_monitor(self):
        camera = Mock(side_effect=OSError('device disappeared'))
        c = self.build(camera=camera)
        self.wifi.inventory.side_effect = RuntimeError('NM unavailable')
        self.audio.status = Mock(side_effect=RuntimeError('Pulse unavailable'))
        c._inventory()
        self.assertEqual(len(self.published), 1)
        self.assertFalse(self.published[-1]['camera']['connected'])
        self.assertFalse(self.published[-1]['wifi']['connected'])
        self.assertFalse(self.published[-1]['audio']['connected'])

    def test_close_suppresses_new_dispatch(self):
        c = self.build()
        c.close()
        c._emit(self.published.append, {})
        self.assertEqual(self.dispatched, [])

    def test_close_while_dequeuing_cancels_unstarted_network_mutation(self):
        c = self.build()
        class ClosingQueue:
            def get(self, timeout):
                c.close()
                return (1, 'connect', {'network': {'ssid': 'test'}})
        c.requests = ClosingQueue()
        c._work()
        self.wifi.connect.assert_not_called()
        self.wifi.inventory.assert_not_called()

    def test_worker_reports_failure_and_accepts_later_operation(self):
        c = self.build(period=100)
        completed = threading.Event()
        c.result = lambda *args: (self.results.append(args), completed.set())
        self.wifi.scan.side_effect = RuntimeError('scan failed')
        token = c.request('scan')
        c.start()
        self.assertTrue(completed.wait(2))
        self.assertEqual(self.results[0][:2], (token, 'scan'))
        self.assertFalse(self.results[0][2]['ok'])
        self.assertIsInstance(c.request('audio_retry'), int)
        c.close()
        c.thread.join(2)

    def test_reconnect_never_restarts_active_service(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0))
        self.assertTrue(reconnect_camera(runner)['ok'])
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.args[0], ['systemctl', 'is-active', 'bodyink-live.service'])

    def test_reconnect_inactive_service_only_uses_start(self):
        runner = Mock(side_effect=[SimpleNamespace(returncode=3), SimpleNamespace(returncode=0)])
        self.assertTrue(reconnect_camera(runner)['ok'])
        self.assertEqual(runner.call_args.args[0], ['sudo', '-n', 'systemctl', 'start', 'bodyink-live.service'])


if __name__ == '__main__':
    unittest.main()
