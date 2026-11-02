"""Bounded diagnostics under blocked/failing storage; no GTK or camera."""
import gc
import json
from pathlib import Path
import tempfile
import threading
import unittest

from sword.timeline import Timeline
from sword.analyze_timeline import analyze


class TimelineTests(unittest.TestCase):
    def test_analysis_counts_all_draws_and_does_not_call_missing_time_idle(self):
        rows = [dict(kind='header', main_thread=1)]
        def event(kind, at, cpu, **fields):
            rows.append(dict(kind=kind, at=at, cpu=cpu, thread=1, **fields))
        event('tick_begin', 10., 1., previous_interval=.016, due_lateness=0.)
        event('tick_end', 10.01, 1.01)
        event('draw_begin', 10.02, 1.01)
        event('gc_start', 10.03, 1.02, generation=2)
        event('gc_stop', 10.06, 1.05, generation=2)
        event('draw_end', 10.07, 1.06)
        event('draw_begin', 10.18, 1.07)
        event('draw_end', 10.20, 1.09)
        event('tick_begin', 10.30, 1.10, previous_interval=.30, due_lateness=.274, phase='playing')
        event('tick_end', 10.31, 1.11)
        rows.append(dict(kind='footer', overwritten=0, contended=0, pending=0))
        result = analyze(rows)
        stall = result['long_ticks'][0]
        self.assertEqual(stall['draw_callbacks_overlapping'], 2)
        self.assertAlmostEqual(stall['main_thread_cpu_ms'], 100.)
        self.assertAlmostEqual(stall['callback_union_ms'], 80.)
        self.assertAlmostEqual(stall['uninstrumented_wall_ms'], 220.)
        self.assertAlmostEqual(stall['inclusive_overlap_ms']['gc_main'], 30.)
        self.assertTrue(result['complete'])

    def test_trace_loss_or_missing_footer_is_not_presented_as_complete(self):
        self.assertFalse(analyze([dict(kind='header', main_thread=1)])['complete'])
        self.assertFalse(analyze([dict(kind='header', main_thread=1),
                                 dict(kind='footer', overwritten=1)])['complete'])

    def test_blocked_sink_does_not_block_producer_and_keeps_hard_capacity(self):
        entered, release, done = (threading.Event() for _ in range(3))
        rows = []

        def sink(row):
            if row['kind'] == 'header':
                entered.set()
                release.wait(5.)
            rows.append(row)

        trace = Timeline('unused.jsonl', capacity=16, sink=sink, observe_gc=False)
        try:
            self.assertTrue(entered.wait(1.))

            def produce():
                for index in range(2000):
                    trace.mark('timer_set', float(index), 16)
                trace.close()
                done.set()

            threading.Thread(target=produce, daemon=True).start()
            self.assertTrue(done.wait(1.), 'UI producer or close waited for blocked storage')
            self.assertEqual(trace.summary()['pending'], 16)
            self.assertEqual(trace.overwritten, 1984)
            self.assertFalse(trace.finished.is_set())
        finally:
            release.set()
            trace.close()
            self.assertTrue(trace.finished.wait(2.))
        events = [row for row in rows if row['kind'] == 'timer_set']
        self.assertEqual([row['due'] for row in events], list(map(float, range(1984, 2000))))
        self.assertEqual(rows[-1]['kind'], 'footer')
        self.assertTrue(rows[-1]['finished'])
        self.assertEqual(trace.written, 16)

    def test_contended_publication_drops_explicitly_instead_of_waiting(self):
        trace = Timeline('unused', sink=lambda row: None, observe_gc=False)
        try:
            with trace._lock:
                trace.mark('advance_begin')
            self.assertEqual(trace.contended, 1)
            self.assertEqual(trace.submitted, 0)
        finally:
            trace.close()
            self.assertTrue(trace.finished.wait(2.))

    def test_gc_is_opt_in_and_unregisters_on_close(self):
        before = list(gc.callbacks)
        events = []
        trace = Timeline('unused', sink=events.append)
        try:
            gc.collect(2)
        finally:
            trace.close()
            self.assertTrue(trace.finished.wait(2.))
        self.assertEqual(gc.callbacks, before)
        selected = [row for row in events if row['kind'].startswith('gc_') and row['thread'] == threading.get_ident()]
        self.assertTrue(any(row['kind'] == 'gc_start' and row['generation'] == 2 for row in selected))
        self.assertTrue(any(row['kind'] == 'gc_stop' and row['generation'] == 2 for row in selected))
        trace.close()
        self.assertEqual(gc.callbacks, before)

    def test_sink_failure_disables_trace_and_releases_gc_callback(self):
        before = list(gc.callbacks)
        trace = Timeline('unused', sink=lambda row: (_ for _ in ()).throw(OSError('disk unavailable')))
        self.assertTrue(trace.finished.wait(2.))
        self.assertEqual(trace.error, 'disk unavailable')
        self.assertEqual(gc.callbacks, before)
        trace.mark('advance_begin')
        self.assertEqual(trace.submitted, 0)

    def test_mutable_fields_cannot_retain_game_or_pose_objects(self):
        trace = Timeline('unused', sink=lambda row: None, observe_gc=False)
        try:
            with self.assertRaises(ValueError):
                trace.mark('poll_decoded', {'points17': []})
            with self.assertRaises(ValueError):
                trace.mark('typo')
        finally:
            trace.close()
            self.assertTrue(trace.finished.wait(2.))

    def test_real_file_has_clock_identity_and_scalar_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'new'/'trace.jsonl'
            trace = Timeline(path, observe_gc=False)
            trace.mark('tick_begin', .093, .077, 'playing', 12., at=25.)
            trace.mark('tick_end', .002, 'playing', 12.09, '')
            trace.close()
            self.assertTrue(trace.finished.wait(2.))
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(rows[0]['schema'], 'sword-timeline-v1')
            self.assertEqual(rows[1]['at'], 25.)
            self.assertEqual(rows[1]['phase'], 'playing')
            self.assertEqual(rows[1]['thread'], threading.get_ident())
            self.assertGreaterEqual(rows[1]['cpu'], 0)
            self.assertEqual(rows[-1]['written'], 2)


if __name__ == '__main__':
    unittest.main()
