"""Synthetic HTTP + GTK photo workflow. Never reads a camera or real backend."""
import io
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from PIL import Image
from main import read_frame,save_art,EnvironmentPanel,Gtk,GLib
from photo_job import PhotoJobs, RenderCancelled
from test_photo_job import FIXTURE


class Response(io.BytesIO):
    def __init__(self,width=32,height=24,**headers):
        super().__init__(bytes([55,111,166])*width*height);self.status=200
        self.headers={'X-Preview-Width':str(width),'X-Preview-Height':str(height),'X-Preview-Stride':str(width*3),
            'X-Preview-Version':'7','X-Frame-Age-Ms':'20','X-Capture-Monotonic':'50.25','X-Capture-Sequence':'99'}
        self.headers.update(headers)


class PacketTests(unittest.TestCase):
    def test_preview_and_1080_photo(self):
        packet=read_frame(Response());self.assertEqual(packet.rgb.shape,(24,32,3));self.assertEqual(packet.version,7)
        photo=read_frame(Response(1920,1080,**{'X-Frame-Age-Ms':'2500'}),photo=True)
        self.assertEqual(photo.rgb.shape,(1080,1920,3));self.assertEqual(photo.capture_sequence,99)
        self.assertEqual(photo.capture_monotonic,50.25)
    def test_preview_counts_download_delay_but_photo_is_static(self):
        with patch('main.time.monotonic',side_effect=(10.,10.6)):
            with self.assertRaises(ValueError):read_frame(Response())
        with patch('main.time.monotonic',side_effect=(10.,10.6)):
            self.assertAlmostEqual(read_frame(Response(),photo=True).age_ms,620.)
    def test_invalid_frames_and_missing_capture_identity(self):
        for headers in ({'X-Preview-Stride':'97'},{'X-Frame-Age-Ms':'NaN'},{'X-Frame-Age-Ms':'-1'}):
            with self.assertRaises(ValueError):read_frame(Response(**headers))
        truncated=Response();truncated.truncate(31)
        with self.assertRaises(ValueError):read_frame(truncated)
        missing=Response();del missing.headers['X-Capture-Sequence']
        with self.assertRaises(KeyError):read_frame(missing,photo=True)
        absent=Response();absent.status=204;self.assertIsNone(read_frame(absent))
        with self.assertRaises(ValueError):read_frame(absent,photo=True)
    def test_optional_pose_does_not_reject_photo(self):
        for headers in ({'X-Points17':'invalid JSON'},{'X-Points17':'[[1,2,3]]'},
                        {'X-Points17':json.dumps([[.5,.5,.8]]*17),'X-Pose-Age-Ms':'151'}):
            self.assertIsNone(read_frame(Response(**headers),photo=True).points)
    def test_saved_photo_pixels_and_source_identity(self):
        art=np.full((24,32,3),(220,218,201),np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path=save_art(art,directory,dict(capture_sequence=99,capture_monotonic=50.25))
            self.assertTrue(np.array_equal(np.array(Image.open(path)),art))
            metadata=json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(metadata['capture_sequence'],99);self.assertEqual(metadata['capture_monotonic'],50.25)
            self.assertEqual(metadata['theme'],'environment');self.assertEqual(metadata['events'],[])
            self.assertEqual(sorted(p.suffix for p in Path(directory).iterdir()),['.json','.png'])


class Service:
    def __init__(self):
        self.gets=[];self.posts=[];self.post_started=threading.Event();self.post_release=threading.Event();self.post_release.set()
        self.fail=False;owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def frame(self,width,height,sequence,photo=False):
                data=bytes((180,140,100))*width*height
                self.send_response(200);self.send_header('Content-Length',str(len(data)))
                headers={'X-Preview-Width':width,'X-Preview-Height':height,'X-Preview-Stride':width*3,
                    'X-Preview-Version':sequence,'X-Frame-Age-Ms':0,'X-Capture-Monotonic':time.monotonic(),'X-Capture-Sequence':sequence}
                for key,value in headers.items():self.send_header(key,str(value))
                self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                assert self.path.startswith('/environment.rgb?after=')
                owner.gets.append(time.monotonic());self.frame(320,180,len(owner.gets))
            def do_POST(self):
                assert self.path=='/environment/photo.rgb'
                assert json.loads(self.rfile.read(int(self.headers['Content-Length'])))=={}
                owner.posts.append(time.monotonic());owner.post_started.set();owner.post_release.wait(3)
                if owner.fail:self.send_error(503);return
                self.frame(64,48,100+len(owner.posts),photo=True)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.post_release.set();self.server.shutdown();self.server.server_close()


class PhotoUiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.service=Service();self.calls=[]
        self.filter_entered=threading.Event();self.filter_release=threading.Event();self.filter_release.set()
        owner=self
        class Jobs:
            def __init__(self,**kwargs):pass
            def validate_resources(self,settings):pass
            def render(self,rgb,settings,cancelled,progress=None):
                owner.calls.append((rgb.shape,threading.get_ident()));owner.filter_entered.set()
                if progress:progress({'stage':'painting'})
                while not owner.filter_release.wait(.01):
                    if cancelled.is_set():raise RenderCancelled()
                return 255-rgb,dict(renderer='test-worker',finish_version='test-finish',cpu_threads=2)
        self.filter_patch=patch('main.PhotoJobs',Jobs);self.filter_patch.start()
        self.emit_patch=patch('main.emit');self.emit_patch.start()
        self.panel=EnvironmentPanel(SimpleNamespace(embed_socket=None,offline_preview=False,offline_image=None,
            base=self.service.base,works=Path(self.directory.name),fps=4.))
        self.window=Gtk.Window();self.window.resize(1024,600);self.window.add(self.panel);self.window.show_all();self.panel.refresh_ui()
    def tearDown(self):
        self.filter_release.set();self.service.post_release.set();self.panel.operation('shutdown');self.window.destroy()
        self.panel.preview_thread.join(2)
        for worker in self.panel.photo_threads:worker.join(4)
        for worker in self.panel.save_threads:worker.join(4)
        self.pump(.05);self.service.close();self.filter_patch.stop();self.emit_patch.stop();self.directory.cleanup()
    def pump(self,seconds=.05,until=None):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            dispatched=0
            while dispatched<8 and time.monotonic()<deadline and Gtk.events_pending():
                Gtk.main_iteration_do(False);dispatched+=1
            if until and until():return
            time.sleep(.005)
        if until:self.assertTrue(until())
    def finish_countdown(self):
        self.panel.cancel_countdown_timer()
        with self.panel.lock:self.panel.session.deadline=time.monotonic()-.01
        self.panel.countdown_tick()
    def shoot(self):
        self.assertTrue(self.panel.begin_capture());self.finish_countdown()
        self.pump(3,lambda:self.panel.session.phase=='result')
    def test_raw_viewfinder_countdown_one_post_and_static_result(self):
        self.pump(.6);self.assertEqual(self.calls,[]);self.assertEqual(self.service.posts,[])
        self.assertGreaterEqual(len(self.service.gets),2);self.assertLessEqual(len(self.service.gets),4)
        self.assertEqual(bytes(self.panel.pixbuf.get_pixels())[:3],bytes((180,140,100)))
        self.assertTrue(self.panel.begin_capture());self.assertEqual(self.panel.countdown.get_text(),'3')
        self.assertFalse(self.panel.begin_capture())
        for remaining in (2,1):
            with self.panel.lock:self.panel.session.deadline=time.monotonic()+remaining
            self.panel.countdown_tick();self.assertEqual(self.panel.countdown.get_text(),str(remaining))
        self.finish_countdown();self.pump(3,lambda:self.panel.session.phase=='result')
        self.assertEqual(len(self.service.posts),1);self.assertEqual(len(self.calls),1)
        self.assertNotEqual(self.calls[0][1],threading.get_ident())
        self.assertEqual(self.panel.current.shape,(48,64,3));self.assertEqual(self.panel.current_metadata['capture_sequence'],101)
        self.pump(.3);gets=len(self.service.gets);art=self.panel.current;self.pump(.6)
        self.assertEqual(len(self.service.gets),gets);self.assertIs(self.panel.current,art)
        self.panel.operation('pause');self.panel.operation('resume');self.pump(.3)
        self.assertEqual(self.panel.session.phase,'result');self.assertIs(self.panel.current,art)
        self.assertEqual(len(self.service.posts),1)
        self.panel.retake();self.pump(.4);self.assertEqual(self.panel.session.phase,'viewfinder')
        self.assertIsNone(self.panel.current);self.assertEqual(len(self.service.posts),1)
    def test_offline_preview_also_waits_for_the_shutter(self):
        self.panel.operation('shutdown');self.panel.preview_thread.join(2);self.window.destroy()
        self.panel=EnvironmentPanel(SimpleNamespace(embed_socket=None,offline_preview=True,offline_image=None,
            base=self.service.base,works=Path(self.directory.name),fps=4.))
        self.window=Gtk.Window();self.window.add(self.panel);self.window.show_all();self.panel.refresh_ui()
        self.pump(.4);self.assertEqual(self.calls,[]);self.assertEqual(self.service.posts,[])
        self.shoot();self.assertEqual(len(self.calls),1);self.assertEqual(self.service.posts,[])
        self.assertEqual(self.panel.current.shape,(360,640,3));self.assertEqual(self.panel.current_metadata['photo_source'],'offline')
    def test_pause_cancels_countdown_and_network_completion(self):
        self.panel.begin_capture();self.panel.operation('pause');self.panel.operation('resume');self.pump(.3)
        self.assertEqual(self.service.posts,[]);self.assertIsNone(self.panel.countdown_source)
        self.service.post_release.clear();self.panel.begin_capture();self.finish_countdown()
        self.pump(2,self.service.post_started.is_set)
        self.panel.operation('pause');self.panel.operation('resume');self.service.post_release.set();self.pump(.5)
        self.assertEqual(self.panel.session.phase,'viewfinder');self.assertEqual(self.calls,[])
        self.assertIsNone(self.panel.current);self.assertEqual(len(self.service.posts),1)
    def test_filter_is_async_and_stale_result_cannot_replace_viewfinder(self):
        self.filter_release.clear();self.panel.begin_capture();self.finish_countdown();self.pump(2,self.filter_entered.is_set)
        self.assertEqual(self.panel.session.phase,'processing')
        self.pump(.05)
        self.assertIn('绘制神经墨稿',self.panel.countdown.get_text())
        self.assertIn('已等待',self.panel.countdown.get_text())
        self.assertNotIn('%',self.panel.countdown.get_text())
        beats=[];timer=GLib.timeout_add(10,lambda:(beats.append(1),True)[1]);self.pump(.15);GLib.source_remove(timer)
        self.assertGreaterEqual(len(beats),5)
        self.panel.operation('pause');self.panel.operation('resume');self.filter_release.set();self.pump(.5)
        self.assertEqual(self.panel.session.phase,'viewfinder');self.assertIsNone(self.panel.current)
        self.assertEqual(len(self.service.posts),1)

    def test_resources_fail_before_camera_post_and_are_visible(self):
        with patch.object(self.panel.photo_jobs,'validate_resources',side_effect=FileNotFoundError('照片入墨模型缺失：test.onnx')):
            self.panel.begin_capture();self.finish_countdown();self.pump(2,lambda:self.panel.session.phase=='error')
        self.assertEqual(self.service.posts,[]);self.assertEqual(self.calls,[])
        self.assertIn('照片入墨模型缺失',self.panel.status.get_text())
        self.assertEqual(self.panel.status.get_text(),self.panel.status.get_tooltip_text())

    def test_return_callback_pauses_and_cancels_then_preserves_ready_art(self):
        callback=[];self.panel.on_back=lambda:callback.append(self.panel.session.active)
        self.filter_release.clear();self.panel.begin_capture();self.finish_countdown();self.pump(2,self.filter_entered.is_set)
        self.panel.request_menu();self.assertEqual(callback,[False]);self.assertTrue(self.panel.photo_cancel.is_set())
        self.panel.photo_threads[-1].join(2);self.assertFalse(self.panel.photo_threads[-1].is_alive())
        self.assertFalse(self.panel.photo_threads[-1].daemon)
        self.assertEqual(self.panel.session.phase,'viewfinder');self.assertIsNone(self.panel.current)
        self.panel.operation('resume');self.filter_release.set();self.shoot();art=self.panel.current
        self.panel.request_menu();self.panel.operation('resume');self.assertIs(self.panel.current,art)

    def test_old_result_and_stage_cannot_overwrite_new_photo(self):
        self.shoot();old_token=self.panel.session.generation
        old_art=self.panel.current.copy();old_metadata=dict(self.panel.current_metadata)
        self.panel.retake();self.shoot();new_art=self.panel.current;new_metadata=dict(self.panel.current_metadata)
        self.panel.deliver_photo(old_token,old_art,old_metadata)
        self.panel.photo_progress(old_token,{'stage':'painting'})
        self.panel.photo_failed(old_token,'late failure')
        self.assertIs(self.panel.current,new_art);self.assertEqual(self.panel.current_metadata,new_metadata)
        self.assertEqual(self.panel.session.phase,'result')

    def test_shutdown_cancels_worker_and_stops_timers(self):
        self.filter_release.clear();self.panel.begin_capture();self.finish_countdown();self.pump(2,self.filter_entered.is_set)
        self.panel.operation('shutdown');self.panel.photo_threads[-1].join(2)
        self.assertFalse(self.panel.photo_threads[-1].is_alive())
        self.assertTrue(self.panel.photo_cancel.is_set());self.assertIsNone(self.panel.progress_source)
        self.assertEqual(self.panel.session.phase,'closed')

    def test_real_child_is_reaped_on_return_and_next_capture_succeeds(self):
        folder=Path(self.directory.name);worker=folder/'fixture_worker.py';worker.write_text(FIXTURE)
        model=folder/'fixture.onnx';model.write_bytes(b'fixture')
        scratch=folder/'scratch';scratch.mkdir()
        self.panel.photo_jobs=PhotoJobs(worker=worker,scratch=scratch)
        self.panel.photo_settings=dict(model=str(model),wait=True)
        self.panel.begin_capture();self.finish_countdown()
        self.pump(10,lambda:self.panel.session.stage=='painting')
        self.panel.request_menu();self.panel.photo_threads[-1].join(5)
        self.assertFalse(self.panel.photo_threads[-1].is_alive())
        self.assertEqual(list(scratch.iterdir()),[])
        self.panel.operation('resume');self.panel.photo_settings['wait']=False;self.shoot()
        self.assertEqual(self.panel.current_metadata['renderer'],'fixture')
        self.assertEqual(list(scratch.iterdir()),[])
    def test_failed_post_requires_explicit_retry(self):
        self.service.fail=True;self.panel.begin_capture();self.finish_countdown();self.pump(2,lambda:self.panel.session.phase=='error')
        self.pump(.5);self.assertEqual(len(self.service.posts),1);self.assertEqual(self.calls,[])
        self.service.fail=False;self.shoot();self.assertEqual(len(self.service.posts),2);self.assertEqual(len(self.calls),1)
    def test_native_capture_archives_exact_png_without_wifi(self):
        import os
        import sys
        suite=Path(__file__).resolve().parents[1]
        sys.path.insert(0,str(suite))
        sys.path.insert(0,str(suite.parent/'bodyink-live'))
        from visitor.store import VisitorStore
        from network_share import ShareUnavailable
        store=VisitorStore(Path(self.directory.name)/'visitor-data')
        current=store.current()
        self.shoot()
        with patch.dict(os.environ,{'INKSUITE_VISITOR_SESSION':current['id'],'INK_VISITOR_DATABASE':str(store.path)}), patch('network_share.fetch_share',side_effect=ShareUnavailable('没有 Wi-Fi')):
            self.panel.save(self.panel.save_button)
            self.pump(5,lambda:not self.panel.saving_artifacts)
        arts=store.view(current['token'])['artworks']
        self.assertEqual([a['title'] for a in arts],['眼前风景 · 水墨写生'])
        saved=next(Path(self.directory.name).glob('*.png'))
        self.assertEqual(store.artwork(current['token'],arts[0]['id'])[1],saved.read_bytes())

    def test_save_binds_presented_result_and_stale_share_does_not_open(self):
        self.shoot();expected=self.panel.current.copy();artifact=self.panel.artifact_id
        entered=threading.Event();release=threading.Event();actual_save=save_art
        def delayed_save(*args):entered.set();release.wait(2);return actual_save(*args)
        share=SimpleNamespace(archive_saved=lambda *args:None, fetch_share=lambda name:dict(png=b'not-used',url='http://example.invalid/'+name))
        with patch('main.save_art',delayed_save),patch.dict('sys.modules',{'network_share':share}):
            self.panel.save(self.panel.save_button);self.pump(1,entered.is_set);self.panel.retake();release.set()
            self.pump(3,lambda:not self.panel.saving_artifacts)
        self.assertEqual(self.panel.session.phase,'viewfinder');self.assertIsNone(self.panel.share_dialog)
        files=list(Path(self.directory.name).glob('*.png'));self.assertEqual(len(files),1)
        self.assertTrue(np.array_equal(np.asarray(Image.open(files[0])),expected))
        meta=json.loads(files[0].with_suffix('.json').read_text())
        self.assertEqual(meta['artifact_id'],artifact);self.assertEqual(meta['capture_sequence'],101)
    def test_requested_local_save_finishes_on_normal_shutdown(self):
        self.shoot();entered=threading.Event();release=threading.Event();actual_save=save_art
        def delayed_save(*args):entered.set();release.wait(2);return actual_save(*args)
        with patch('main.save_art',delayed_save):
            self.panel.save(self.panel.save_button);self.pump(1,entered.is_set)
            self.assertFalse(self.panel.save_threads[-1].daemon)
            self.panel.operation('shutdown');release.set();self.panel.save_threads[-1].join(3)
        self.assertFalse(self.panel.save_threads[-1].is_alive())
        self.assertEqual(len(list(Path(self.directory.name).glob('*.png'))),1)
        self.assertEqual(len(list(Path(self.directory.name).glob('*.json'))),1)


if __name__=='__main__':unittest.main()
