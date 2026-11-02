import json
import os
from pathlib import Path
import queue
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from fight_evidence import EvidenceTap, PreviewQueueTap, install, owner_is_alive


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        # Never let any test reach a real signal-zero probe, on any platform.
        self.kill_guard = patch('fight_evidence.os.kill', side_effect=AssertionError('Real os.kill is forbidden in tests'))
        self.kill_guard.start()
        self.addCleanup(self.kill_guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.control = self.root / 'control.json'
        self.directory = self.root / 'session'
        self.clock = [100.]
        self.owner_alive = Mock(return_value=True)
        self.tap = EvidenceTap(self.control, clock=lambda: self.clock[0],
                               resize=lambda *a: np.zeros((360, 640, 3), np.uint8),
                               owner_alive=self.owner_alive)
        self.packet = (dict(sequence=42, timestamp_seconds=98.5, width=960,
                            height=540, stride=1920, flags=73729),
                       b'not-opened-by-fake-resizer', np.zeros((17, 3)), 0)

    def tearDown(self):
        self.tap.close()
        self.tmp.cleanup()

    def authorize(self, **changes):
        value = dict(directory=str(self.directory), expires_monotonic=190., owner_pid=os.getpid())
        value.update(changes)
        self.control.write_text(json.dumps(value), encoding='utf-8')
        self.tap.checked_at = -float('inf')

    def test_absent_control_is_idle_and_starts_no_worker(self):
        self.assertFalse(self.tap.submit(self.packet))
        self.assertIsNone(self.tap.thread)
        self.assertFalse(self.directory.exists())

    def test_invalid_expired_relative_and_unbounded_controls_rejected(self):
        for changes in (dict(expires_monotonic=99), dict(directory='relative'),
                        dict(expires_monotonic=5000), dict(expires_monotonic=float('nan'))):
            self.authorize(**changes)
            self.assertIsNone(self.tap.active())
        self.control.write_text('{', encoding='utf-8')
        self.tap.checked_at = -float('inf')
        self.assertIsNone(self.tap.active())

    def test_exact_identity_and_points_are_recorded_without_overlay(self):
        self.authorize()
        self.assertTrue(self.tap.submit(self.packet))
        self.tap.pending.join()
        rows = [json.loads(s) for s in (self.directory / 'camera-frames.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sequence'], 42)
        self.assertEqual(rows[0]['timestamp_seconds'], 98.5)
        self.assertEqual(rows[0]['points17'], np.zeros((17, 3)).tolist())
        self.assertEqual(rows[0]['recorded_size'], [640, 360])
        self.assertTrue((self.directory / rows[0]['file']).exists())
        self.clock[0] += .2
        self.assertFalse(self.tap.submit(self.packet))

    def test_queue_is_bounded_and_unlinked_control_cancels_pending_write(self):
        self.authorize()
        with patch('fight_evidence.threading.Thread'):
            self.assertTrue(self.tap.submit(self.packet))
            self.clock[0] += .2
            changed = (dict(self.packet[0], sequence=43),) + self.packet[1:]
            self.assertFalse(self.tap.submit(changed))
            self.assertEqual(self.tap.dropped, 1)
            control, record, raw = self.tap.pending.get_nowait()
            self.control.unlink()
            self.tap._write(control, record, raw)
            self.tap.pending.task_done()
        self.assertFalse(self.directory.exists())

    def test_nonfinite_points_do_not_break_evidence_stream(self):
        self.authorize()
        self.packet[2][9, 0] = np.nan
        self.assertTrue(self.tap.submit(self.packet))
        self.tap.pending.join()
        row = json.loads((self.directory / 'camera-frames.jsonl').read_text())
        self.assertIsNone(row['points17'][9][0])
        self.assertEqual(row['writer_errors'], 0)

    def test_missing_invalid_or_dead_owner_is_rejected(self):
        for owner in (None, 0, -1, True, '42'):
            self.authorize(owner_pid=owner)
            self.owner_alive.reset_mock()
            self.assertIsNone(self.tap.active())
            self.owner_alive.assert_not_called()
        self.control.write_text(json.dumps(dict(directory=str(self.directory), expires_monotonic=190.)), encoding='utf-8')
        self.tap.checked_at = -float('inf')
        self.assertIsNone(self.tap.active())
        self.authorize(owner_pid=12345)
        self.owner_alive.return_value = False
        self.assertIsNone(self.tap.active())
        self.owner_alive.assert_called_once_with(12345)

    def test_permission_error_means_owner_exists(self):
        self.authorize(owner_pid=12345)
        kill = Mock(side_effect=PermissionError)
        with patch('fight_evidence.os', SimpleNamespace(name='posix', kill=kill)):
            self.tap.owner_alive = owner_is_alive
            self.assertEqual(self.tap.active()['owner_pid'], 12345)
        kill.assert_called_once_with(12345, 0)

    def test_posix_probe_detects_dead_process(self):
        kill = Mock(side_effect=ProcessLookupError)
        with patch('fight_evidence.os', SimpleNamespace(name='posix', kill=kill)):
            self.assertFalse(owner_is_alive(12345))
        kill.assert_called_once_with(12345, 0)

    def test_nonposix_default_probe_never_calls_kill(self):
        kill = Mock(side_effect=AssertionError('Must not call kill on Windows'))
        with patch('fight_evidence.os', SimpleNamespace(name='nt', kill=kill)):
            self.assertFalse(owner_is_alive(12345))
        kill.assert_not_called()

    def test_dead_owner_cancels_already_queued_write(self):
        self.authorize()
        with patch('fight_evidence.threading.Thread'):
            self.assertTrue(self.tap.submit(self.packet))
            control, record, raw = self.tap.pending.get_nowait()
            self.owner_alive.return_value = False
            self.tap._write(control, record, raw)
            self.tap.pending.task_done()
        self.assertFalse(self.directory.exists())

    def test_preview_rate_restored_and_producer_discard_does_not_record(self):
        original = queue.Queue(maxsize=1)
        args = SimpleNamespace(preview_fps=1.)
        proxy = PreviewQueueTap(original, self.tap, args)
        self.authorize()
        proxy.put_nowait(self.packet)
        proxy.get_nowait()
        self.assertIsNone(self.tap.thread)
        proxy.put_nowait(self.packet)
        self.assertIs(proxy.get(), self.packet)
        self.assertEqual(args.preview_fps, 8.)
        self.tap.pending.join()
        self.control.unlink()
        self.clock[0] += .3
        with self.assertRaises(queue.Empty):
            proxy.get(timeout=.001)
        self.assertEqual(args.preview_fps, 1.)

    def test_install_wraps_once_and_preserves_existing_worker(self):
        class App:
            def preview_worker(self):
                self.calls += 1
                return 'original'
        app = SimpleNamespace(LiveApp=App)
        install(app, self.control)
        wrapped = App.preview_worker
        install(app, self.control)
        self.assertIs(App.preview_worker, wrapped)
        live = App()
        live.args = SimpleNamespace(preview_fps=1.)
        live.preview_queue = queue.Queue()
        live.calls = 0
        self.assertEqual(live.preview_worker(), 'original')
        self.assertEqual(live.calls, 1)
        self.assertEqual(live.args.preview_fps, 1.)
        self.assertFalse(self.directory.exists())


if __name__ == '__main__':
    unittest.main()
