"""Real local HTTP contract tests. Temporary artwork only; no camera or board."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('tested_gallery_server',ROOT/'server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
sys.path.insert(0,str(ROOT/'integration'))
from network_share import fetch_share
from PIL import Image


class HttpGalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.works=cls.root/'works';cls.fruit=cls.root/'fruit';cls.fight=cls.root/'fight';cls.cache=cls.root/'cache'
        for path in (cls.works,cls.fruit,cls.fight,cls.cache):path.mkdir()
        cls.gallery=server.Gallery(cls.works,cls.fruit,cls.fight,cls.cache,public_url='http://192.168.43.5:8090')
        cls.http=server.Server(('127.0.0.1',0),cls.gallery)
        cls.base='http://127.0.0.1:'+str(cls.http.server_address[1])
        cls.worker=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.worker.join(2);cls.temp.cleanup()

    def setUp(self):
        for directory in (self.works,self.fruit,self.fight,self.cache):
            for path in directory.iterdir():path.unlink()
        self.gallery.catalog={};self.gallery.scanned_at=-100
        self.gallery.network_cache={};self.gallery.network_at=-100
        self.opener=build_opener(ProxyHandler({}))
        # The only platform command permitted by this server is address discovery.
        self.ip=patch.object(server.subprocess,'check_output',return_value=b'[]')
        self.ip_mock=self.ip.start();self.addCleanup(self.ip.stop)
        self.name='ink-20260924-100000-000001.png'
        self.png=self.make_art(self.name)

    def make_art(self,name,metadata=None):
        output=io.BytesIO();Image.new('RGB',(1280,720),'#eee8d8').save(output,format='PNG')
        payload=output.getvalue();(self.works/name).write_bytes(payload)
        if metadata is not None:(self.works/name).with_suffix('.json').write_text(json.dumps(metadata),encoding='utf-8')
        return payload

    def request(self,path,method='GET'):
        try:
            with self.opener.open(Request(self.base+path,method=method),timeout=3) as response:
                return response.status,response.headers,response.read()
        except HTTPError as error:
            return error.code,error.headers,error.read()

    def json(self,path):
        status,headers,body=self.request(path)
        self.assertEqual(status,200,(path,status,body))
        return json.loads(body)

    def item(self):return next(item for item in self.json('/api/artworks')['items'] if item['name']==self.name)

    def test_catalog_original_download_head_and_thumbnail(self):
        item=self.item()
        self.assertEqual(item['width'],1280);self.assertEqual(item['height'],720)
        status,headers,body=self.request(item['download_url'])
        self.assertEqual(status,200);self.assertEqual(body,self.png)
        self.assertEqual(headers['Content-Type'],'image/png')
        self.assertEqual(headers['Content-Disposition'],'attachment; filename="'+self.name+'"')
        self.assertEqual(int(headers['Content-Length']),len(body))
        status,headers,body=self.request(item['download_url'],'HEAD')
        self.assertEqual(status,200);self.assertEqual(body,b'')
        self.assertEqual(int(headers['Content-Length']),len(self.png))
        status,headers,body=self.request(item['thumbnail_url'])
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/jpeg')
        with Image.open(io.BytesIO(body)) as im:
            self.assertLessEqual(im.width,960);self.assertLessEqual(im.height,720)
        self.assertEqual(self.json('/api/artworks/'+item['id'])['name'],self.name)

    def test_share_and_native_helper_real_http(self):
        item=self.item()
        result=fetch_share(self.name,base=self.base,opener=self.opener.open)
        self.assertEqual(result['url'],'http://192.168.43.5:8090'+item['page_url'])
        self.assertEqual(result['name'],self.name)
        self.assertTrue(result['png'].startswith(b'\x89PNG'))
        homepage=fetch_share(base=self.base,opener=self.opener.open)
        self.assertEqual(homepage['url'],'http://192.168.43.5:8090/')
        self.assertIsNone(homepage['name'])

    def test_qr_decodes_to_artwork_page(self):
        try:import cv2;import numpy as np
        except ImportError:self.skipTest('OpenCV unavailable; PNG/API contract tested separately')
        item=self.item();status,headers,body=self.request('/api/artworks/'+item['id']+'/qr.png')
        self.assertEqual(status,200)
        self.assertEqual(headers.get_all('Cache-Control'),['no-store'])
        image=cv2.imdecode(np.frombuffer(body,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        text,points,_=cv2.QRCodeDetector().detectAndDecode(image)
        self.assertIsNotNone(points);self.assertEqual(text,'http://192.168.43.5:8090'+item['page_url'])

    def test_environment_artwork_retains_stylized_original_and_title(self):
        self.make_art(self.name,dict(theme='environment',events=[]))
        item=self.item()
        self.assertEqual(item['title'],'眼前风景 · 水墨写生')
        self.assertEqual(item['kind'],'environment')
        status,headers,body=self.request(item['download_url'])
        self.assertEqual(status,200);self.assertEqual(body,self.png)

    def test_illegal_paths_and_missing_files_are_404(self):
        outside=self.root/'secret.txt';outside.write_text('not-public',encoding='utf-8')
        paths=('/../secret.txt','/%2e%2e/secret.txt','/static/../server.py','/static/%2e%2e/server.py',
               '/server.py','/api/artworks/not-an-id','/artwork/'+('0'*24)+'.png',
               '/artwork/'+self.item()['id']+'.jpg','/thumb/'+self.item()['id']+'.png',
               '/api/share?name='+quote('../secret.txt'),'/api/share?name='+quote('..\\secret.txt'))
        for path in paths:
            with self.subTest(path=path):self.assertEqual(self.request(path)[0],404)

    def test_deleted_or_symlinked_source_not_served(self):
        item=self.item();source=self.works/self.name;source.unlink()
        self.assertEqual(self.request(item['image_url'])[0],404)
        outside=self.root/'outside.png';outside.write_bytes(self.png)
        try:source.symlink_to(outside)
        except OSError:self.skipTest('Symlink privilege unavailable on this host')
        self.assertEqual(self.request(item['image_url'])[0],404)
        self.gallery.scanned_at=-100
        self.assertEqual(self.json('/api/artworks')['items'],[])

    def test_cache_symlink_cannot_expose_external_file(self):
        item=self.item();outside=self.root/'secret-cache.txt';outside.write_bytes(b'PRIVATE FILE')
        target=self.cache/(item['id']+'.jpg')
        try:target.symlink_to(outside)
        except OSError:self.skipTest('Symlink privilege unavailable on this host')
        os.utime(outside,(2_000_000_000,2_000_000_000))
        status,_,body=self.request(item['thumbnail_url'])
        self.assertNotEqual(body,b'PRIVATE FILE')

    def test_malformed_metadata_does_not_poison_other_artwork(self):
        name2='ink-20260924-100001-000002.png';self.make_art(name2)
        (self.works/name2).with_suffix('.json').write_text('{malformed',encoding='utf-8')
        rows=self.json('/api/artworks')['items'];self.assertEqual(len(rows),2)
        for metadata in ({'events':None},{'events':5},{'events':{'bad':'shape'}}):
            with self.subTest(metadata=metadata):
                (self.works/name2).with_suffix('.json').write_text(json.dumps(metadata),encoding='utf-8')
                self.gallery.scanned_at=-100
                rows=self.json('/api/artworks')['items'];self.assertEqual(len(rows),2)
                self.assertEqual(next(x for x in rows if x['name']==name2)['marks'],0)
        (self.works/name2).with_suffix('.json').write_text(json.dumps({'events':[{'action':[]},{'action':{'bad':1}}]}),encoding='utf-8')
        self.gallery.scanned_at=-100
        self.assertEqual(len(self.json('/api/artworks')['items']),2)

    def test_scores_only_completed_and_public_fields(self):
        for directory,game in ((self.fruit,'fruit'),(self.fight,'fight')):
            values=[dict(round_id='ok',completed=True,score=50,result='胜',points17='must not expose',
                         completed_at='2026-09-24T10:00:00+00:00'),
                    dict(round_id='incomplete',completed=False,score=999),
                    dict(round_id='truthy',completed=1,score=999),dict(reason='diagnostic',score=999)]
            for index,value in enumerate(values):(directory/f'{index}.json').write_text(json.dumps(value),encoding='utf-8')
            (directory/'bad.json').write_text('broken',encoding='utf-8')
            rows=self.json('/api/scores?game='+game)['items']
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['round_id'],'ok');self.assertNotIn('points17',rows[0])
        self.assertEqual(self.request('/api/scores?game=unknown')[0],404)

    def test_bad_score_shape_or_nan_does_not_poison_normal_result(self):
        good=dict(round_id='good',completed=True,score=50,completed_at='2026-09-24T10:00:00+00:00')
        (self.fruit/'good.json').write_text(json.dumps(good),encoding='utf-8')
        for bad in (dict(completed=True,round_id='bad',score=1,completed_at=17),
                    dict(completed=True,round_id='bad',score=1,completed_at={'date':1}),
                    dict(completed=True,round_id='bad',score=float('nan'))):
            with self.subTest(bad=bad):
                (self.fruit/'bad.json').write_text(json.dumps(bad),encoding='utf-8')
                rows=self.json('/api/scores?game=fruit')['items']
                self.assertIn('good',[x['round_id'] for x in rows])

    def test_unusable_scores_filtered_and_limit_is_100(self):
        for index in range(102):
            (self.fruit/f'good-{index}.json').write_text(json.dumps(dict(round_id=str(index),completed=True,score=index)),encoding='utf-8')
        for index,bad in enumerate((dict(completed=True),dict(completed=True,score='bad'),dict(completed=True,score=True))):
            (self.fruit/f'bad-{index}.json').write_text(json.dumps(bad),encoding='utf-8')
        rows=self.json('/api/scores?game=fruit')['items']
        self.assertEqual(len(rows),100)
        self.assertTrue(all(isinstance(row['score'],int) and not isinstance(row['score'],bool) for row in rows))
        (self.fight/'invalid-result.json').write_text(json.dumps(dict(completed=True,result='playing')),encoding='utf-8')
        self.assertEqual(self.json('/api/scores?game=fight')['items'],[])

    def test_network_unavailable_returns_503_without_broken_local_qr(self):
        previous=self.gallery.public_url
        try:
            self.gallery.public_url=None
            self.assertEqual(self.request('/api/share')[0],503)
            self.assertEqual(self.request('/api/qr.png')[0],503)
        finally:self.gallery.public_url=previous

    def test_health_static_headers_and_no_camera_commands(self):
        self.assertEqual(self.json('/health')['camera_used'],False)
        for path in ('/','/scores','/static/app.js','/static/style.css','/static/favicon.svg'):
            status,headers,body=self.request(path)
            self.assertEqual(status,200)
            self.assertTrue(body);self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.json('/api/network');self.json('/api/share')
        for call in self.ip_mock.call_args_list:
            self.assertEqual(call.args[0],['ip','-j','-4','addr','show','up'])
        # Public portal has no mutating or camera-control endpoint.
        self.assertEqual(self.request('/api/command')[0],404)
        self.assertEqual(self.request('/preview.jpg')[0],404)
        self.assertEqual(self.request('/api/command','POST')[0],501)


if __name__=='__main__':unittest.main()
