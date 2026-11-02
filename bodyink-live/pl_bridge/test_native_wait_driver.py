import ctypes as c
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pl_preprocess import Preprocessor

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
        p.read=lambda offset:next(states)
        p.write=lambda *args:commands.append(args)
        with self.assertRaisesRegex(TimeoutError,'drained successfully'):p.wait(timeout=0)
        self.assertEqual(commands,[(4,2)])
        self.assertIsNone(p.active_buffers)
    def test_failed_cancellation_retains_ownership(self):
        p=self.driver(1,1);owned=p.active_buffers;p.read=lambda offset:1
        with patch('pl_preprocess.time.monotonic',side_effect=[0,2,2,4]):
            with self.assertRaisesRegex(RuntimeError,'FATAL'):p.wait(timeout=0)
        self.assertIs(p.active_buffers,owned)

if __name__=='__main__':unittest.main()
