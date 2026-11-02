import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

import numpy as np

from photo_job import PhotoJobs, RenderCancelled


# This protocol fixture represents a long model computation; it never imports ORT.
FIXTURE = '''
import json, os, sys, time
from pathlib import Path
import numpy as np
p=Path(sys.argv[1]); cfg=json.loads((p/'settings.json').read_text())
if cfg.get('ignore_term'):
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(p/'status.json').write_text(json.dumps({'stage':'painting','pid':os.getpid()}))
if cfg.get('fail'): raise RuntimeError('intentional decoder failure')
if cfg.get('wait'): time.sleep(30)
np.save(p/'output.npy',np.load(p/'input.npy',allow_pickle=False),allow_pickle=False)
(p/'result.json').write_text(json.dumps({'renderer':'fixture'}))
'''


class JobLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scratch = self.root / 'scratch'; self.scratch.mkdir()
        self.worker = self.root / 'worker.py'; self.worker.write_text(FIXTURE)
        self.manager = PhotoJobs(worker=self.worker, scratch=self.scratch)
        self.rgb = np.full((24, 32, 3), 101, np.uint8)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_clean(self):
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_success_preserves_pixels_and_cleans_raw_photo(self):
        result, record = self.manager.render(self.rgb, {}, threading.Event())
        np.testing.assert_array_equal(result, self.rgb)
        self.assertFalse(result.flags.writeable)
        self.assertEqual(record['renderer'], 'fixture')
        self.assert_clean()

    def test_cancel_kills_busy_worker_and_allows_next_photo(self):
        cancel = threading.Event(); seen = []
        def on_progress(status):
            seen.append(status)
            cancel.set()
        with self.assertRaises(RenderCancelled):
            self.manager.render(self.rgb, {'wait': True}, cancel, on_progress)
        self.assertEqual(seen[0]['stage'], 'painting')
        self.assert_clean()
        self.manager.render(self.rgb, {}, threading.Event())
        self.assert_clean()

    def test_repeated_click_cannot_start_parallel_inference(self):
        started = threading.Event(); cancel = threading.Event(); failures = []
        def long_job():
            try:
                self.manager.render(self.rgb, {'wait': True}, cancel, lambda s: started.set())
            except RenderCancelled:
                pass
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=long_job); thread.start()
        try:
            self.assertTrue(started.wait(10))
            with self.assertRaisesRegex(RuntimeError, 'already'):
                self.manager.render(self.rgb, {}, threading.Event())
        finally:
            cancel.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assert_clean()

    def test_timeout_reaps_worker_and_releases_slot(self):
        with self.assertRaises(TimeoutError):
            self.manager.render(self.rgb, {'wait': True}, threading.Event(), timeout=.25)
        self.assert_clean()
        self.manager.render(self.rgb, {}, threading.Event())

    def test_failure_is_reported_instead_of_old_or_weak_result(self):
        with self.assertRaisesRegex(RuntimeError, 'intentional decoder failure'):
            self.manager.render(self.rgb, {'fail': True}, threading.Event())
        self.assert_clean()

    def test_already_cancelled_does_not_launch(self):
        event = threading.Event(); event.set()
        with self.assertRaises(RenderCancelled):
            self.manager.render(self.rgb, {}, event)
        self.assert_clean()

    def test_missing_resources_are_explicit_and_never_start_a_worker(self):
        model = self.root / 'missing.onnx'
        with self.assertRaisesRegex(FileNotFoundError, '模型缺失'):
            self.manager.validate_resources({'model': str(model)})
        model.write_bytes(b'test-model')
        self.manager.runtime = self.root / 'missing-runtime'
        with self.assertRaisesRegex(FileNotFoundError, '运行库缺失'):
            self.manager.validate_resources({'model': str(model)})
        self.assert_clean()

    @unittest.skipIf(os.name == 'nt', 'POSIX worker ignores SIGTERM to exercise kill/wait')
    def test_uncooperative_worker_is_killed_and_reaped(self):
        event = threading.Event(); seen = []
        def cancel(status):
            seen.append(status)
            event.set()
        with self.assertRaises(RenderCancelled):
            self.manager.render(self.rgb, {'wait': True, 'ignore_term': True}, event, cancel)
        with self.assertRaises(ProcessLookupError):
            os.kill(seen[0]['pid'], 0)
        self.assert_clean()


if __name__ == '__main__':
    unittest.main()
