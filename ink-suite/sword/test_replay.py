"""Recorded timing/identity and EOF behavior; no camera or GTK required."""
from copy import deepcopy
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.request

from sword.benchmark_danmaku import arguments
from sword.benchmark_panel import synthetic_pose
from sword.input import InputTracker
from sword.replay import RecordedPoseSource, ReplayServer


class RecordedReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.jsonl'
        self.rows = []
        for index, elapsed in enumerate((0., .1, .2, .3, .8, 1.)):
            row = synthetic_pose(80.+elapsed, index, .4+index*.01, .8)
            row.update(pid=100, test_elapsed=elapsed+5.)
            self.rows.append(row)
        # A recorder polling faster than the source can repeat a frame.
        self.rows[2].update(sequence=1, timestamp_seconds=80.1)
        self.rows[3]['suite']['control_active'] = True
        self.write()

    def write(self):
        self.path.write_text('\n'.join(json.dumps(row) for row in self.rows), encoding='utf-8')

    def test_retains_duplicate_keys_and_arrival_gaps_without_interpolation(self):
        source = RecordedPoseSource(self.path)
        self.assertFalse(source.state(500.)['available'])
        source.begin(500.)
        before = source.state(500.15)
        repeated = source.state(500.25)
        self.assertEqual(before['sequence'], repeated['sequence'])
        self.assertEqual(before['timestamp_seconds'], repeated['timestamp_seconds'])
        self.assertEqual(repeated['points17'], self.rows[2]['points17'])
        self.assertEqual(repeated['pid'], 'recorded-100')
        gap = source.state(500.7)
        self.assertTrue(gap['suite']['control_active'])
        self.assertEqual(gap['replay']['row'], 3)
        self.assertEqual(source.state(500.7), gap)

    def test_eof_never_fabricates_new_frames_or_sustains_tracking(self):
        source = RecordedPoseSource(self.path)
        source.begin(500.)
        tracker = InputTracker()
        last = source.state(501.001)
        self.assertTrue(tracker.update(last, 501.001)['valid'])
        self.assertEqual(source.state(510.), last)
        self.assertFalse(tracker.update(source.state(510.), 510.)['valid'])

    def test_start_offset_is_affine_and_does_not_change_recorded_points(self):
        original = deepcopy(self.rows)
        source = RecordedPoseSource(self.path, .25)
        source.begin(600.)
        result = source.state(600.)
        self.assertAlmostEqual(result['timestamp_seconds'], 599.85)
        self.assertEqual(result['sequence'], 1)
        self.assertEqual(result['points17'], original[2]['points17'])
        self.assertEqual(result['latency_ms'], original[2]['latency_ms'])
        self.assertAlmostEqual(source.description()['seconds_available'], .75)
        self.assertEqual(source.rows, original)

    def test_source_restart_and_timestamp_rollback_evidence_are_retained(self):
        self.rows[4].update(pid=101, timestamp_seconds=2., sequence=0)
        self.write()
        source = RecordedPoseSource(self.path)
        source.begin(500.)
        a, b = source.state(500.31), source.state(500.81)
        self.assertNotEqual(a['pid'], b['pid'])
        self.assertLess(b['timestamp_seconds'], a['timestamp_seconds'])
        self.assertEqual(b['sequence'], 0)

    def test_transport_error_rows_remain_missing_input_at_their_original_time(self):
        self.rows[3] = dict(test_elapsed=5.3, test_error='HTTP timeout')
        self.write()
        source = RecordedPoseSource(self.path)
        source.begin(500.)
        state = source.state(500.4)
        self.assertTrue(state['stale'])
        self.assertFalse(state['available'])
        self.assertNotIn('points17', state)

    def test_invalid_order_nonfinite_or_out_of_range_start_is_rejected(self):
        for start in (-1., float('nan'), 1.):
            with self.assertRaises(ValueError):
                RecordedPoseSource(self.path, start)
        self.rows[3]['test_elapsed'] = 0.
        self.write()
        with self.assertRaisesRegex(ValueError, 'roll back'):
            RecordedPoseSource(self.path)

    def test_replay_cli_is_explicit_and_never_silently_adds_warmup_or_stress(self):
        parsed = arguments(['--scene', 'replay', '--recording', str(self.path), '--output', 'unused'])
        self.assertEqual(parsed.warmup, 0)
        for flags in (['--scene', 'replay'], ['--recording', str(self.path)],
                      ['--scene', 'replay', '--recording', str(self.path), '--warmup', '2'],
                      ['--scene', 'replay', '--recording', str(self.path), '--stress-collisions']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments(flags+['--output', 'unused'])

    @unittest.skipUnless(os.name == 'posix', 'Bounded pipe readiness uses Linux select, like GTK/XEmbed')
    def test_separate_loopback_process_has_no_frames_until_begun_and_retains_eof_key(self):
        server = ReplayServer(self.path)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(server.base+'/api/state', timeout=1.) as response:
                self.assertFalse(json.load(response)['available'])
            server.begin(time.monotonic()-2.)
            deadline = time.monotonic()+2.
            state = {}
            while time.monotonic() < deadline:
                with opener.open(server.base+'/api/state', timeout=1.) as response:
                    state = json.load(response)
                if state.get('replay'):
                    break
                time.sleep(.01)
            self.assertEqual(state['sequence'], 5)
            with opener.open(server.base+'/api/state', timeout=1.) as response:
                self.assertEqual(json.load(response)['timestamp_seconds'], state['timestamp_seconds'])
        finally:
            server.close()
        self.assertGreaterEqual(server.requests, 3)
        self.assertIsNone(server.error)


if __name__ == '__main__':
    unittest.main()
