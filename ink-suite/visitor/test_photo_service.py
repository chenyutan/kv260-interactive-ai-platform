"""Still-photo service tests use generated fixtures, never camera/user artwork."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

from .photo_service import MAX_UPLOAD, PhotoError, PhotoService, normalize_photo
from .portal import Portal
from .store import VisitorStore


def photograph(size=(96,64), mode='RGB', color=(48,128,181), format='PNG', **kwargs):
    image=Image.new(mode,size,color)
    stream=io.BytesIO();image.save(stream,format=format,**kwargs)
    return stream.getvalue()


def render(rgb,settings,cancelled,progress):
    progress({'stage':'铺色','progress':.5})
    output=rgb.copy();output[:,:,0]=203
    return output,{'fixture':True,'style':settings['style']}


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='photo-test-')
        self.store=VisitorStore(self.temp.name);self.session=self.store.current()
        self.service=PhotoService(self.store,renderer=render)
    def tearDown(self):
        self.service.close();self.temp.cleanup()
    def wait_job(self,job=None,timeout=4):
        due=time.monotonic()+timeout
        while time.monotonic()<due:
            jobs=self.service.list_jobs(self.session['token'])
            found=next((item for item in jobs if job is None or item['id']==job['id']),None)
            if found and found['status'] in ('completed','failed'):return found
            time.sleep(.01)
        self.fail('Photo job did not complete')
    def test_result_is_added_to_existing_visitor_downloads(self):
        job=self.service.submit_bytes(self.session['token'],photograph(),'color')
        done=self.wait_job(job)
        self.assertEqual(done['status'],'completed');self.assertEqual(done['stage'],'作品已完成')
        self.assertTrue(done['result_url'].startswith('/v/'+self.session['token']))
        self.assertEqual(done['style'],'color');self.assertGreaterEqual(done['elapsed_s'],0)
        self.assertEqual(len(self.store.view(self.session['token'])['artworks']),1)
        _,png=self.store.artwork(self.session['token'],done['art_id'])
        self.assertEqual(png,self.service.read_image(self.session['token'],done['id'],'result'))
        self.assertEqual(Image.open(io.BytesIO(png)).getpixel((0,0)),(203,128,181))
    def test_duplicate_hosts_cannot_invalidate_live_job(self):
        with self.assertRaises(PhotoError):PhotoService(self.store,renderer=render)
    def test_static_photo_security_and_error_paths(self):
        for content in (b'',b'not an image',b'<svg><image/></svg>'):
            with self.subTest(length=len(content)),self.assertRaises(PhotoError):normalize_photo(content)
        with self.assertRaises(PhotoError) as error:normalize_photo(b'X'*(MAX_UPLOAD+1))
        self.assertEqual(error.exception.status,413)
        with self.assertRaises(PhotoError):normalize_photo(photograph(size=(16,16)))
        with patch('visitor.photo_service.MAX_PIXELS',1000):
            with self.assertRaises(PhotoError) as error:normalize_photo(photograph())
            self.assertEqual(error.exception.status,413)
        with self.assertRaises(PhotoError):normalize_photo(photograph(format='BMP'))
        images=[Image.new('RGB',(64,64),c) for c in ('red','blue')]
        out=io.BytesIO();images[0].save(out,format='WEBP',save_all=True,append_images=images[1:],duration=100,loop=0)
        with self.assertRaises(PhotoError):normalize_photo(out.getvalue())
        jpeg=photograph(format='JPEG')
        with self.assertRaises(PhotoError):normalize_photo(jpeg[:-18])
    def test_exif_rotation_transparency_and_renderer_bound(self):
        exif=Image.Exif();exif[274]=6
        rotated=normalize_photo(photograph(size=(120,64),format='JPEG',exif=exif))
        self.assertEqual(rotated.shape,(120,64,3))
        transparent=normalize_photo(photograph(mode='RGBA',color=(255,0,0,0)))
        self.assertEqual(tuple(transparent[0,0]),(248,244,231))
        large=normalize_photo(photograph(size=(2560,1440)))
        self.assertEqual(large.shape,(1080,1920,3))
    def test_invalid_session_style_and_local_path(self):
        with self.assertRaises(KeyError):self.service.submit_bytes('not-a-token',photograph())
        with self.assertRaises(PhotoError):self.service.submit_bytes(self.session['token'],photograph(),'cloud')
        with self.assertRaises(PhotoError):self.service.submit_path(self.session['token'],Path(self.temp.name)/'missing.png')
        path=Path(self.temp.name)/'photo.png';path.write_bytes(photograph())
        self.assertEqual(self.wait_job(self.service.submit_path(self.session['token'],path,'ink'))['status'],'completed')
    def test_only_one_job_and_close_cancels_renderer(self):
        entered=threading.Event()
        def blocked(rgb,settings,cancelled,progress):
            entered.set();cancelled.wait(3)
            return rgb,{}
        self.service._renderer=blocked
        job=self.service.submit_bytes(self.session['token'],photograph())
        self.assertTrue(entered.wait(1))
        with self.assertRaises(PhotoError) as error:self.service.submit_bytes(self.session['token'],photograph())
        self.assertEqual(error.exception.status,409)
        self.service.close()
        self.assertFalse(self.service._thread.is_alive())
        self.assertEqual(self.wait_job(job)['status'],'failed')
        self.assertEqual(self.store.view(self.session['token'])['artworks'],[])
        with self.assertRaises(PhotoError):self.service.submit_bytes(self.session['token'],photograph())
    def test_archive_during_processing_does_not_publish_to_new_session(self):
        entered=threading.Event();release=threading.Event()
        def blocked(rgb,settings,cancelled,progress):entered.set();release.wait(2);return rgb,{}
        self.service._renderer=blocked
        job=self.service.submit_bytes(self.session['token'],photograph())
        self.assertTrue(entered.wait(1))
        self.store.begin_finish(self.session['id'],'finish','fixture')
        fresh=self.store.complete_finish(self.session['id']);release.set()
        self.assertEqual(self.wait_job(job)['status'],'failed')
        self.assertEqual(self.store.view(fresh['token'])['artworks'],[])
        self.assertEqual(self.store.view(self.session['token'])['artworks'],[])
        self.assertEqual(self.service.list_jobs(fresh['token']),[])
        with self.assertRaises(KeyError):self.service.read_image(fresh['token'],job['id'],'source')
        with self.assertRaises(PhotoError):self.service.submit_bytes(self.session['token'],photograph())
    def test_completed_photos_survive_archive_and_restart(self):
        done=self.wait_job(self.service.submit_bytes(self.session['token'],photograph()))
        self.store.begin_finish(self.session['id'],'finish','fixture');fresh=self.store.complete_finish(self.session['id'])
        png=self.service.read_image(self.session['token'],done['id'],'result')
        self.service.close();self.service=PhotoService(self.store,renderer=render)
        self.assertEqual(self.service.read_image(self.session['token'],done['id'],'result'),png)
        self.assertEqual(self.service.list_jobs(self.session['token'])[0]['status'],'completed')
        with self.assertRaises(KeyError):self.service.read_image(fresh['token'],done['id'],'result')
    def test_ending_session_cancels_even_without_progress_callbacks(self):
        entered=threading.Event()
        def silent(rgb,settings,cancelled,progress):
            entered.set();cancelled.wait(3)
            return rgb,{}
        self.service._renderer=silent
        job=self.service.submit_bytes(self.session['token'],photograph())
        self.assertTrue(entered.wait(1))
        self.store.begin_finish(self.session['id'],'finish','fixture')
        done=self.wait_job(job,timeout=2)
        self.assertEqual(done['status'],'failed')
        self.assertEqual(self.store.view(self.session['token'])['artworks'],[])
    def test_recovery_never_leaves_infinite_processing(self):
        done=self.wait_job(self.service.submit_bytes(self.session['token'],photograph()))
        self.service.close()
        with self.store.connect() as db:db.execute("UPDATE photo_jobs SET status='processing' WHERE id=?",(done['id'],))
        self.service=PhotoService(self.store,renderer=render)
        recovered=self.service.list_jobs(self.session['token'])[0]
        self.assertEqual(recovered['status'],'failed');self.assertIn('中断',recovered['error'])
    def test_renderer_failure_is_safe_and_next_upload_can_run(self):
        def fail(*args):raise RuntimeError('/private/absolute/path internal failure')
        self.service._renderer=fail
        failed=self.wait_job(self.service.submit_bytes(self.session['token'],photograph()))
        self.assertEqual(failed['status'],'failed');self.assertNotIn('/private',failed['error'])
        with self.assertRaises(KeyError):self.service.read_image(self.session['token'],failed['id'],'result')
        self.service._renderer=render
        self.assertEqual(self.wait_job(self.service.submit_bytes(self.session['token'],photograph()))['status'],'completed')
    def test_output_validation_and_storage_limits(self):
        self.service._renderer=lambda *args:(np.zeros((32,32,4),dtype=np.uint8),{})
        self.assertEqual(self.wait_job(self.service.submit_bytes(self.session['token'],photograph()))['status'],'failed')
        self.service.max_jobs=1
        with self.assertRaises(PhotoError) as error:self.service.submit_bytes(self.session['token'],photograph())
        self.assertEqual(error.exception.status,409)
        self.service.max_jobs=20;self.service.max_storage=1
        with self.assertRaises(PhotoError) as error:self.service.submit_bytes(self.session['token'],photograph())
        self.assertEqual(error.exception.status,503)
    def test_normalized_source_does_not_contain_phone_exif(self):
        exif=Image.Exif();exif[270]='phone-private-fixture'
        job=self.service.submit_bytes(self.session['token'],photograph(format='JPEG',exif=exif))
        self.wait_job(job)
        image=Image.open(io.BytesIO(self.service.read_image(self.session['token'],job['id'],'source')))
        self.assertFalse(image.getexif());self.assertNotIn('exif',image.info)


class HTTPCase(ServiceCase):
    def setUp(self):
        super().setUp()
        self.portal=Portal(self.store,host='127.0.0.1',port=0,public_url='http://localhost',photos=self.service).start()
        self.base='http://127.0.0.1:'+str(self.portal.port)
        from .account_test_support import login_fixture
        self.cookie=login_fixture(self.portal,self.session['token'])
    def tearDown(self):self.portal.close();super().tearDown()
    def request(self,path,data=None,headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.base+path,data=data,headers={'Cookie':self.cookie,**(headers or {})}),timeout=5)
    def test_http_upload_list_download_and_ownership(self):
        token=self.session['token'];endpoint='/api/visitor/'+token+'/photos'
        with self.request(endpoint+'?style=ink',photograph(),{'X-Ink-Visitor':'1','Content-Type':'image/png'}) as response:
            self.assertEqual(response.status,202);job=json.load(response)['job']
        done=self.wait_job(job)
        with self.request(endpoint) as response:self.assertEqual(json.load(response)['jobs'][0]['id'],done['id'])
        with self.request(done['result_url']+'?download=1') as response:
            self.assertEqual(response.headers['Content-Type'],'image/png');self.assertIn('attachment',response.headers['Content-Disposition'])
            self.assertTrue(response.read().startswith(b'\x89PNG'))
        self.store.begin_finish(self.session['id'],'f','h');fresh=self.store.complete_finish(self.session['id'])
        from .account_test_support import claim_fixture
        claim_fixture(self.portal,self.cookie,fresh['token'])
        with self.assertRaises(urllib.error.HTTPError) as error:self.request(done['source_url'].replace(token,fresh['token']))
        self.assertEqual(error.exception.code,404)
    def test_http_csrf_invalid_content_and_archive_rejection(self):
        endpoint='/api/visitor/'+self.session['token']+'/photos'
        for headers,status in (({'Content-Type':'image/png'},403),
                               ({'X-Ink-Visitor':'1','Content-Type':'image/png','Origin':'https://evil.example'},403),
                               ({'X-Ink-Visitor':'1','Content-Type':'text/plain'},415)):
            with self.subTest(headers=headers),self.assertRaises(urllib.error.HTTPError) as error:self.request(endpoint,photograph(),headers)
            self.assertEqual(error.exception.code,status)
        with self.assertRaises(urllib.error.HTTPError) as error:self.request(endpoint+'?style=cloud',photograph(),{'X-Ink-Visitor':'1','Content-Type':'image/png'})
        self.assertEqual(error.exception.code,400)
        self.store.begin_finish(self.session['id'],'f','h')
        with self.assertRaises(urllib.error.HTTPError) as error:self.request(endpoint,photograph(),{'X-Ink-Visitor':'1','Content-Type':'image/png'})
        self.assertEqual(error.exception.code,409)


if __name__=='__main__':unittest.main()
