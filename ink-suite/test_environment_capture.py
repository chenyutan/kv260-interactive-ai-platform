"""No camera access: exercise on-demand shutter with synthetic immutable frames."""
import threading
import time
import unittest
import numpy as np
from environment_capture import EnvironmentCapture
from suite_control import SuiteControl


class PhotoCaptureTests(unittest.TestCase):
    def setUp(self):
        self.suite=SuiteControl();self.stop=threading.Event();self.calls=[];self.conversions=[]
        self.raw=bytes(1920*1080*2);self.sequence=1;self.points=None;self.pose_stamp=None
        self.capture=EnvironmentCapture(self.snapshot,self.convert,self.suite.snapshot,lambda:(self.points,self.pose_stamp),self.stop)
    def activate(self):self.suite.configure(dict(active=True,mode='environment',menu=False))
    def snapshot(self,**kwargs):
        self.calls.append(kwargs);self.sequence+=1
        return dict(width=1920,height=1080,stride=3840,timestamp_seconds=time.monotonic(),sequence=self.sequence),self.raw
    def convert(self,raw,width,height,stride,size):
        self.conversions.append(size)
        return np.full((size[1],size[0],3),70,np.uint8)
    def test_no_work_until_explicit_request_and_no_work_outside_page(self):
        self.assertEqual(self.calls,[]);self.assertIsNone(self.capture.preview())
        with self.assertRaises(RuntimeError):self.capture.photograph()
        self.activate();self.assertEqual(self.calls,[]);self.assertEqual(self.conversions,[])
    def test_small_unfiltered_preview_and_full_photo(self):
        self.activate();preview=self.capture.preview()
        self.assertEqual(preview[3:6],(320,180,960));self.assertEqual(len(preview[0]),320*180*3)
        start=time.monotonic();photo=self.capture.photograph()
        self.assertEqual(photo[3:6],(1920,1080,5760));self.assertGreaterEqual(photo[8],start)
        self.assertEqual(len(photo[0]),1920*1080*3);self.assertEqual(set(photo[0]),{70})
        self.assertEqual(len(self.calls),2);self.assertEqual(self.conversions,[(320,180),(1920,1080)])
        self.assertTrue(all(v['copy'] is False for v in self.calls))
    def test_shutter_rejects_pre_countdown_cache(self):
        self.activate();old=self.snapshot();old[0]['timestamp_seconds']-=1
        once=[old]
        self.capture.snapshot=lambda **kw:once.pop() if once else self.snapshot(**kw)
        start=time.monotonic();result=self.capture.photograph()
        self.assertGreaterEqual(result[8],start);self.assertEqual(len(self.conversions),1)
    def test_no_reused_stale_preview(self):
        self.activate();packet=self.snapshot();self.capture.snapshot=lambda **kw:packet
        first=self.capture.preview();self.assertIsNone(self.capture.preview(first[1]))
        packet[0]['timestamp_seconds']-=1;self.assertIsNone(self.capture.preview())
        self.assertEqual(len(self.conversions),1)
    def test_mode_change_during_photo_discards_result(self):
        self.activate()
        def convert(*args):
            image=self.convert(*args);self.suite.configure(dict(menu=True));return image
        self.capture.converter=convert
        with self.assertRaises(RuntimeError):self.capture.photograph()
        self.assertEqual(len(self.conversions),1)
    def test_missing_old_future_or_invalid_pose_does_not_block_photo(self):
        self.activate()
        for delta in (-1.,1.):
            self.points=np.ones((17,3));self.pose_stamp=time.monotonic()+delta
            self.assertIsNone(self.capture.photograph()[6])
        self.points=np.ones((17,3));self.points[0,0]=np.nan;self.pose_stamp=time.monotonic()
        photo=self.capture.photograph();self.assertEqual(photo[6][0][0],-1.)
    def test_stopping_cancels_wait_for_new_frame(self):
        self.activate()
        def no_new_frame(**kwargs):
            self.stop.set();metadata,raw=self.snapshot(**kwargs);metadata['timestamp_seconds']-=1;return metadata,raw
        self.capture.snapshot=no_new_frame
        with self.assertRaises(RuntimeError):self.capture.photograph()
        self.assertEqual(self.conversions,[])


if __name__=='__main__':unittest.main()
