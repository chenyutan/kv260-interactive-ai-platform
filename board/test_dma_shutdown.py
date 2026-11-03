"""Linux regression for the production shutdown path with an outstanding AXI transaction."""
import multiprocessing
import os
from pathlib import Path
import queue
import signal
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def run_service(entry, draining, acknowledged, freed):
    runtime = ROOT / entry
    base = ROOT / 'bodyink-live'
    sys.path[:0] = [str(runtime), str(runtime/'pl_bridge'), str(base), str(base/'pl_bridge')]
    import app
    import pl_adapter
    from pl_preprocess import Preprocessor

    assert Path(pl_adapter.__file__).resolve().parent == runtime/'pl_bridge'
    application = object.__new__(app.LiveApp)
    application.input_size = (256, 256)
    application.stop = threading.Event()
    application.failure = application.action_process = None
    application.args = SimpleNamespace(preprocess_mode='prefetch', preprocess_backend='pl')
    application.wait_prepare_credit = lambda: True
    application.frame_rois = lambda _: [(0, 0, 2, 2)] * 2
    application.viewer = SimpleNamespace(start=lambda: None, stop=lambda: None)

    def capture():
        metadata = dict(width=2, height=2, stride=4)
        application.preprocess_queue.put((metadata, b'12345678'))
        application.stop.wait()

    def no_inference_packet(timeout):
        application.stop.wait(timeout)
        raise queue.Empty

    application.capture = capture
    application.preview_worker = application.paint_worker = application.stop.wait
    application.take_inference_packet = no_inference_packet
    adapter = object.__new__(pl_adapter.PLAdapter)
    driver = object.__new__(Preprocessor)
    driver.active_buffers = None
    driver.tag = 22

    def status(offset):
        draining.set()
        if offset == 0x34:
            return driver.tag
        return 2 if acknowledged.is_set() else 1

    def inject_fault(*args, **kwargs):
        driver.active_buffers = (object(), object())
        raise RuntimeError('simulated unresponsive DMA')

    driver.read = status
    driver.write = lambda *args: None
    driver.close = lambda: None
    adapter.pp = driver
    adapter.plans = {}
    adapter.device = SimpleNamespace(close=freed.set)
    adapter.source = adapter.output = adapter.source_view = adapter.output_view = None
    adapter.resize = inject_fault
    signal.signal(signal.SIGTERM, lambda *_: application.stop.set())
    with patch.object(pl_adapter, 'PLAdapter', return_value=adapter):
        try:
            application.run()
        except RuntimeError as error:
            if 'required runtime worker failed' in str(error):
                return
            raise
    raise AssertionError('application failed to report the injected DMA error')


@unittest.skipUnless(sys.platform.startswith('linux'), 'exercises a Linux service signal')
class TestShutdown(unittest.TestCase):
    def test_signal_cannot_drop_outstanding_buffers(self):
        ctx = multiprocessing.get_context('fork')
        for entry in ('bodyink-live', 'ink-suite/runtime'):
            with self.subTest(entry=entry):
                draining, acknowledged, freed = (ctx.Event() for _ in range(3))
                child = ctx.Process(target=run_service, args=(entry, draining, acknowledged, freed))
                child.start()
                try:
                    self.assertTrue(draining.wait(10), 'worker never reached DMA quarantine')
                    os.kill(child.pid, signal.SIGTERM)
                    self.assertFalse(freed.wait(3.3), 'released before matching completion')
                    self.assertTrue(child.is_alive())
                    acknowledged.set()
                    child.join(6)
                    self.assertEqual(child.exitcode, 0)
                    self.assertTrue(freed.is_set())
                finally:
                    acknowledged.set()
                    child.join(5)
                    if child.is_alive():
                        child.kill()
                        child.join()


if __name__ == '__main__':
    unittest.main()
