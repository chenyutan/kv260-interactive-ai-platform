import io
import importlib.util
import unittest
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
from pose_stream import HEADER,read_frame,fresh_frame
from suite_control import SuiteControl


class CameraToolTests(unittest.TestCase):
    def test_bounded_rgb_stream_and_short_reads(self):
        data=b'\x33'*36;stream=io.BytesIO(HEADER.pack(b'BRGB',4,3,len(data),7,180.0)+data)
        self.assertEqual(read_frame(stream),(data,4,3,7,180.0))
        with self.assertRaises(EOFError):read_frame(io.BytesIO(b'BRGB'))
        with self.assertRaises(ValueError):read_frame(io.BytesIO(HEADER.pack(b'BRGB',9999,3,9999*9,7,0)))
    def test_backend_millisecond_age_keeps_normal_frames_and_rejects_stale(self):
        data=b'\x33'*36
        for age,expected in ((20.0,True),(180.0,True),(750.0,True),(750.1,False),(3000.0,False)):
            frame=read_frame(io.BytesIO(HEADER.pack(b'BRGB',4,3,len(data),7,age)+data))
            self.assertEqual(fresh_frame(frame),expected)
    def test_actual_runtime_http_header_retains_millisecond_age(self):
        spec=importlib.util.spec_from_file_location('pose_web_contract',Path(__file__).parent/'runtime/web_ui.py')
        web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
        data=b'\x33'*36
        server=web.ViewerServer('127.0.0.1',0,lambda:{},lambda:b'',lambda:b'',lambda _:{},
            get_preview_rgb_frame=lambda after:(data,9,180.0,4,3,12) if after!=9 else None).start()
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+str(server.address[1])+'/preview.rgbstream',timeout=2) as stream:
                frame=read_frame(stream)
                self.assertEqual(frame,(data,4,3,9,180.0));self.assertTrue(fresh_frame(frame))
        finally:server.stop()
    def test_preview_keeps_gameplay_paused_and_cannot_paint(self):
        control=SuiteControl()
        control.configure(dict(active=True,mode='ink',menu=True,preview=True))
        self.assertTrue(control.snapshot()['preview']);self.assertFalse(control.ink_allowed())
        control.configure(dict(active=True,mode='ink',menu=True))
        self.assertFalse(control.snapshot()['preview']);self.assertFalse(control.ink_allowed())
    def test_invalid_preview_does_not_mutate_ownership(self):
        c=SuiteControl();old=c.snapshot()
        for command in (dict(active=True,menu=False,preview=True),dict(preview='true'),dict(active=False,menu=True,preview=True)):
            with self.assertRaises(ValueError):c.configure(command)
            self.assertEqual(c.snapshot(),old)


if __name__=='__main__':unittest.main()
