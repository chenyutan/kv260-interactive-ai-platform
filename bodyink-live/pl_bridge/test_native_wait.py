"""Native poll contract on allocated memory, never actual FPGA registers."""
import ctypes as c
from pathlib import Path
import threading,time
import unittest

class WaitTests(unittest.TestCase):
    def setUp(self):
        self.lib=c.CDLL(str(Path(__file__).with_name('libpl_mmio.host.so')))
        self.lib.bodyink_wait_done.argtypes=[c.c_void_p,c.c_uint64,c.POINTER(c.c_uint32)]
        self.lib.bodyink_wait_done.restype=c.c_int
        self.lib.bodyink_write32.argtypes=[c.c_void_p,c.c_size_t,c.c_uint32]
        self.mem=(c.c_uint32*16)();self.out=c.c_uint32(99)
    def test_immediate_done_and_error_bits_preserved(self):
        for status in [2,6,10,14]:
            self.mem[2]=status
            self.assertEqual(self.lib.bodyink_wait_done(self.mem,0,c.byref(self.out)),0)
            self.assertEqual(self.out.value,status)
    def test_busy_even_with_done_is_not_drained(self):
        self.mem[2]=3;before=time.monotonic()
        self.assertEqual(self.lib.bodyink_wait_done(self.mem,2000000,c.byref(self.out)),1)
        self.assertGreaterEqual(time.monotonic()-before,.002)
        self.assertEqual(self.mem[1],0) # Never writes cancellation/start.
    def test_completion_can_arrive_from_python_thread_without_gil_deadlock(self):
        self.mem[2]=1
        def complete():
            time.sleep(.01);self.lib.bodyink_write32(self.mem,8,2)
        worker=threading.Thread(target=complete);worker.start()
        try:self.assertEqual(self.lib.bodyink_wait_done(self.mem,500000000,c.byref(self.out)),0)
        finally:worker.join()
    def test_invalid_arguments(self):
        self.assertEqual(self.lib.bodyink_wait_done(None,0,c.byref(self.out)),-1)
        self.assertEqual(self.lib.bodyink_wait_done(self.mem,0,None),-1)
        self.assertEqual(self.lib.bodyink_wait_done(self.mem,10000000001,c.byref(self.out)),-1)

if __name__=='__main__':unittest.main()
