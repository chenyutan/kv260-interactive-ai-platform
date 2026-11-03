import ctypes as c
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pl_preprocess import Preprocessor, defer_dma_signals, find_uio

class DriverWaitTests(unittest.TestCase):
    def driver(self,result,status):
        p=object.__new__(Preprocessor);p.ptr=1;p.tag=17
        p.active_buffers=(object(),object())
        def wait(ptr,timeout,out):
            c.cast(out,c.POINTER(c.c_uint32))[0]=status
            return result
        p.lib=SimpleNamespace(bodyink_wait_done=wait)
        p.read=lambda offset:{8:2,0x34:17,0x38:100}[offset]
        p.write=lambda *a:None
        return p
    def test_success_verifies_tag_and_releases_only_drained_job(self):
        p=self.driver(0,2);self.assertEqual(p.wait()['tag'],17)
        self.assertIsNone(p.active_buffers)
    def test_native_error_retains_dma_buffers(self):
        p=self.driver(-1,1);owned=p.active_buffers
        with self.assertRaisesRegex(RuntimeError,'buffers retained'):p.wait()
        self.assertIs(p.active_buffers,owned)
    def test_completed_error_is_not_success(self):
        p=self.driver(0,6)
        with self.assertRaisesRegex(RuntimeError,'DMA error'):p.wait()
        self.assertIsNone(p.active_buffers)
    def test_timeout_cancels_and_drains(self):
        p=self.driver(1,1);commands=[];states=iter([1,2])
        p.read=lambda offset:17 if offset==0x34 else next(states)
        p.write=lambda *args:commands.append(args)
        with self.assertRaisesRegex(TimeoutError,'drained successfully'):p.wait(timeout=0)
        self.assertEqual(commands,[(4,2)])
        self.assertIsNone(p.active_buffers)
    def test_quarantine_requires_matching_tag_and_drained_status(self):
        p=self.driver(0,2);owned=p.active_buffers
        states=iter((2,0,3,2));tags=iter((16,17))
        p.read=lambda offset:next(states) if offset==8 else next(tags)
        observations=[]
        with patch('pl_preprocess.time.sleep',side_effect=lambda _:observations.append(p.active_buffers)):
            p.drain_before_exit()
        self.assertEqual(len(observations),3)
        self.assertTrue(all(value is owned for value in observations))
        self.assertIsNone(p.active_buffers)

    def test_wrong_completion_tag_retains_buffers(self):
        p=self.driver(0,2)
        p.read=lambda offset:0
        with self.assertRaisesRegex(RuntimeError,'tag mismatch'):p.wait()
        self.assertIsNotNone(p.active_buffers)

    def test_term_is_deferred_until_tagged_completion(self):
        p=self.driver(0,2);owned=p.active_buffers
        observed=[]
        def while_busy(_delay):
            os.kill(os.getpid(), signal.SIGTERM)
            observed.append(p.active_buffers is owned)
            p.read=lambda offset:17 if offset == 0x34 else 2
        p.read=lambda offset:1
        with self.assertRaises(SystemExit) as stopped:
            with defer_dma_signals(), patch('pl_preprocess.time.sleep', side_effect=while_busy):
                p.drain_before_exit()
        self.assertEqual(stopped.exception.code, 128+signal.SIGTERM)
        self.assertEqual(observed, [True])
        self.assertIsNone(p.active_buffers)

    def test_uio_checks_identity_count_address_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); dev=root/'uio7'; window=dev/'maps/map0';window.mkdir(parents=True)
            (dev/'name').write_text('another-ip'); (window/'size').write_text('0x1000')
            (window/'addr').write_text('0x81000000')
            with self.assertRaisesRegex(RuntimeError,'one bodyink'):find_uio(root)
            (dev/'name').write_text('bodyink-preprocess')
            self.assertEqual(find_uio(root),'/dev/uio7')
            for attr,wrong,valid in [('addr','0x81001000','0x81000000'),('size','0x100','0x1000')]:
                (window/attr).write_text(wrong)
                with self.assertRaisesRegex(RuntimeError,'map0'):find_uio(root)
                (window/attr).write_text(valid)
            duplicate=root/'uio8';duplicate.mkdir();(duplicate/'name').write_text('bodyink-preprocess')
            with self.assertRaisesRegex(RuntimeError,'one bodyink'):find_uio(root)

    def test_failed_cancellation_retains_ownership(self):
        p=self.driver(1,1);owned=p.active_buffers;p.read=lambda offset:1
        with patch('pl_preprocess.time.monotonic',side_effect=[0,2,2,4]):
            with self.assertRaisesRegex(RuntimeError,'FATAL'):p.wait(timeout=0)
        self.assertIs(p.active_buffers,owned)

if __name__=='__main__':unittest.main()
