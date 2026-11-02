"""Storage-stall regressions; pure Python, no GTK, camera or board required."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from sword.metrics import LatestMetricsWriter


class MetricsWriterTests(unittest.TestCase):
    def test_blocked_storage_has_one_latest_slot_and_does_not_block_submit(self):
        entered, release, latest_written, producer_done = (threading.Event() for _ in range(4))
        writes = []

        def sink(value):
            if not writes:
                entered.set()
                release.wait(5.)
            writes.append(value['frame'])
            if value['frame'] == 2000:
                latest_written.set()

        writer = LatestMetricsWriter('unused.json', sink=sink)
        try:
            writer.submit({'frame': 0})
            self.assertTrue(entered.wait(2.))

            def producer():
                for frame in range(1, 2001):
                    writer.submit({'frame': frame})
                producer_done.set()

            thread = threading.Thread(target=producer, daemon=True)
            thread.start()
            self.assertTrue(producer_done.wait(1.), 'submit waited for the blocked sink')
            self.assertFalse(release.is_set())
            self.assertEqual(writer.submitted, 2001)
            self.assertEqual(writer.replaced, 1999)
            release.set()
            self.assertTrue(latest_written.wait(2.))
            self.assertEqual(writes, [0, 2000])
        finally:
            release.set()
            writer.close()
            self.assertTrue(writer.finished.wait(2.))

    def test_close_returns_while_sink_is_blocked_and_discards_pending(self):
        entered, release, closed = (threading.Event() for _ in range(3))
        writes = []

        def sink(value):
            entered.set()
            release.wait(5.)
            writes.append(value['frame'])

        writer = LatestMetricsWriter('unused.json', sink=sink)
        try:
            writer.submit({'frame': 1})
            self.assertTrue(entered.wait(2.))
            writer.submit({'frame': 2})
            threading.Thread(target=lambda: (writer.close(), closed.set()), daemon=True).start()
            self.assertTrue(closed.wait(1.), 'close waited for storage')
            self.assertFalse(writer.finished.is_set())
            self.assertFalse(writer.submit({'frame': 3}))
        finally:
            release.set()
            writer.close()
            self.assertTrue(writer.finished.wait(2.))
        self.assertEqual(writes, [1])

    def test_failed_storage_disables_writer_without_retries(self):
        attempts = []

        def sink(value):
            attempts.append(value)
            raise OSError('simulated full disk')

        writer = LatestMetricsWriter('unused.json', sink=sink)
        self.assertTrue(writer.submit({'frame': 1}))
        self.assertTrue(writer.finished.wait(2.))
        self.assertEqual(writer.error, 'simulated full disk')
        self.assertFalse(writer.submit({'frame': 2}))
        self.assertEqual(len(attempts), 1)
        writer.close()

    def test_atomic_file_and_directory_are_written_by_worker(self):
        written = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'new'/ 'metrics.json'
            writer = LatestMetricsWriter(path)
            original = writer._sink

            def sink(value):
                self.assertNotEqual(threading.get_ident(), submit_thread)
                original(value)
                written.set()

            submit_thread = threading.get_ident()
            writer._sink = sink
            try:
                self.assertFalse(path.parent.exists())
                snapshot = {'phase': 'playing', 'frame': 12, 'label': '御剑'}
                writer.submit(snapshot)
                self.assertTrue(written.wait(2.))
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), snapshot)
                self.assertFalse(path.with_suffix('.json.tmp').exists())
            finally:
                writer.close()
                self.assertTrue(writer.finished.wait(2.))


if __name__ == '__main__':
    unittest.main()
