"""Real SQLite + HTTP + fake ink backend; never touches a board/user canvas."""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid
import zipfile
from .store import VisitorStore,name_value
from .coordinator import FinishCoordinator
from .reporter import GameReporter
from .portal import Portal,NetworkAddress
from .layout import qr_box

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1kAAAAASUVORK5CYII=')

class Backend:
    def __init__(self):self.marks=23;self.revision=7;self.png=PNG;self.clear_calls=0;self.fail=False;self.lost_ack=False
    def __call__(self,path,data=None):
        if path=='/api/state':return json.dumps(dict(event_count=self.marks,art_revision=self.revision)).encode()
        if path=='/art.png':return self.png
        if data=={'command':'clear'}:
            self.clear_calls+=1
            if self.fail:raise OSError('simulated connection failure')
            self.marks=0;self.revision+=1;self.png=PNG+b'empty'
            if self.lost_ack:raise OSError('simulated lost clear acknowledgement')
            return b'{"accepted":true}'
        raise AssertionError(path)

class StoreCase(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.store=VisitorStore(self.temp.name);self.session=self.store.current()
    def tearDown(self):self.temp.cleanup()
    def add(self,game='fruit',status='completed',**data):
        ident=uuid.uuid4().hex;self.store.record(self.session['id'],ident,game,dict(score=100,**data),status);return ident
    def test_restart_retains_private_link_and_artwork(self):
        ident=self.store.add_art(self.session['id'],PNG,23)
        restored=VisitorStore(self.temp.name);self.assertEqual(restored.current()['token'],self.session['token'])
        self.assertEqual(restored.artwork(self.session['token'],ident)[1],PNG)
        self.assertEqual(restored.add_art(self.session['id'],PNG,23),ident)
    def test_session_privacy_and_archive_access(self):
        ident=self.store.add_art(self.session['id'],PNG,23);self.add()
        self.store.begin_finish(self.session['id'],'key','hash');new=self.store.complete_finish(self.session['id'])
        with self.assertRaises(KeyError):self.store.artwork(new['token'],ident)
        self.assertEqual(self.store.artwork(self.session['token'],ident)[1],PNG)
        self.store.rename(self.session['token'],'山河客')
        self.assertEqual(self.store.leaderboard()[0]['items'][0]['name'],'山河客')
        self.assertEqual(self.store.current()['id'],new['id'])
    def test_no_duplicate_results_and_only_completed_per_rules(self):
        ident=self.add(mode='arcade');self.add(status='interrupted',mode='arcade');self.add(mode='classic')
        self.assertFalse(self.store.record(self.session['id'],ident,'fruit',{'score':9999},'completed'))
        groups=self.store.leaderboard();self.assertEqual(len(groups),2);self.assertEqual(sum(len(g['items']) for g in groups),2)
    def test_all_five_games_and_reports(self):
        for game in ('sword','fight','fruit','koi','taiko'):self.add(game,result='胜',hp=23)
        self.store.add_art(self.session['id'],PNG,23)
        self.assertEqual(len(self.store.leaderboard()),5)
        package=self.store.report(self.session['token'],'zip')
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertIsNone(archive.testzip());self.assertEqual(len(archive.namelist()),4)
            self.assertEqual(len(json.loads(archive.read('report.json'))['rounds']),5)
    def test_name_validation_html_and_csv(self):
        for bad in ('','<script>','a\n','a'*25):
            with self.assertRaises(ValueError):name_value(bad)
        self.store.rename(self.session['token'],'=HYPERLINK("x")');self.add()
        self.assertIn(b"'=HYPERLINK",self.store.report(self.session['token'],'csv'))
        self.assertNotIn(b'<script>',self.store.report(self.session['token'],'html'))
    def test_crash_progress_is_interrupted_not_ranked(self):
        self.add(status='active');self.assertEqual(self.store.recover_rounds(),1)
        self.assertEqual(self.store.leaderboard(),[])
        self.assertEqual(self.store.view(self.session['token'])['rounds'][0]['status'],'interrupted')
    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):self.add(accuracy=float('nan'))

class FinishCase(StoreCase):
    def setUp(self):super().setUp();self.backend=Backend();self.coordinator=FinishCoordinator(self.store,'http://unused',self.backend)
    def test_archive_failure_never_clears_or_rotates(self):
        with patch.object(self.store,'report',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.coordinator.finish(self.session['id'])
        self.assertEqual(self.backend.clear_calls,0);self.assertEqual(self.backend.marks,23)
        self.assertEqual(self.store.current()['id'],self.session['id'])
    def test_finish_idempotent_old_token_still_downloads_and_names(self):
        self.add();result=self.coordinator.finish(self.session['id'])
        self.assertTrue(result['accepted']);self.assertEqual(self.backend.clear_calls,1);self.assertEqual(self.backend.marks,0)
        self.assertTrue(self.coordinator.finish(self.session['id'])['repeated']);self.assertEqual(self.backend.clear_calls,1)
        self.assertEqual(self.store.view(self.session['token'])['status'],'archived')
        self.store.rename(self.session['token'],'已归档游客');self.assertEqual(self.store.leaderboard()[0]['items'][0]['name'],'已归档游客')
    def test_photo_saved_during_zip_build_cannot_be_lost_on_finish(self):
        original=self.store.report
        def interleave(*args,**kwargs):
            zipped=original(*args,**kwargs)
            self.store.add_art(self.session['id'],PNG+b'photo',title='风景',source_name='ink-20261103-180000-000003.png')
            return zipped
        with patch.object(self.store,'report',side_effect=interleave):
            with self.assertRaisesRegex(RuntimeError,'新的作品'):self.coordinator.finish(self.session['id'])
        self.assertEqual(self.store.current()['status'],'active')
        self.coordinator.finish(self.session['id'])
        with zipfile.ZipFile(self.store.root/('archive-'+self.session['id']+'.zip')) as z:
            self.assertIn(PNG+b'photo',[z.read(name) for name in z.namelist() if name.endswith('.png')])

    def test_clear_failure_recovers_after_restart(self):
        self.backend.fail=True
        with self.assertRaises(OSError):self.coordinator.finish(self.session['id'])
        self.assertEqual(self.store.current()['status'],'ending');self.assertEqual(self.backend.marks,23)
        self.backend.fail=False;restored=VisitorStore(self.temp.name)
        FinishCoordinator(restored,'http://unused',self.backend).recover()
        self.assertNotEqual(restored.current()['id'],self.session['id']);self.assertEqual(self.backend.marks,0)
    def test_lost_clear_ack_is_not_repeated(self):
        self.backend.lost_ack=True
        with self.assertRaises(OSError):self.coordinator.finish(self.session['id'])
        self.coordinator.recover();self.assertEqual(self.backend.clear_calls,1)
    def test_changed_canvas_after_pending_finish_never_clears_new_art(self):
        self.backend.fail=True
        with self.assertRaises(OSError):self.coordinator.finish(self.session['id'])
        self.backend.fail=False;self.backend.png=PNG+b'new-art'
        with self.assertRaises(RuntimeError):self.coordinator.recover()
        self.assertEqual(self.backend.marks,23);self.assertEqual(self.store.current()['status'],'ending')

class ReporterCase(StoreCase):
    def test_terminal_is_durable_before_worker_can_take_it(self):
        blocked=threading.Event();entered=threading.Event();original=self.store.record
        def slow(*args):entered.set();blocked.wait(5.);return original(*args)
        with patch.object(self.store,'record',side_effect=slow):
            reporter=GameReporter(self.store,self.session['id'],'sword')
            game=SimpleNamespace(phase='playing',score=50,time=2.,lives=4);reporter.observe(game)
            self.assertTrue(entered.wait(2.));game.phase='won';game.score=100;reporter.observe(game)
            pending=list(Path(self.temp.name).glob('pending-*.json'));self.assertEqual(len(pending),1)
            self.assertEqual(json.loads(pending[0].read_text())[4],'completed')
            # Simulate restarting the host before the queued terminal DB write.
            restored=VisitorStore(self.temp.name);self.assertEqual(restored.replay_pending(),1)
            restored.recover_rounds();self.assertEqual(restored.leaderboard()[0]['items'][0]['score'],100)
            blocked.set();reporter.close(game)
    def test_sqlite_busy_does_not_erase_terminal_journal(self):
        reporter=GameReporter(self.store,self.session['id'],'fruit')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            game=SimpleNamespace(state='playing',score=75,elapsed=30.,mode='classic');reporter.observe(game)
            game.state='over';game.score=125;reporter.observe(game)
            self.assertEqual(len(list(Path(self.temp.name).glob('pending-*.json'))),1)
        reporter.close(game)
        restored=VisitorStore(self.temp.name);restored.replay_pending()
        self.assertEqual(restored.leaderboard()[0]['items'][0]['score'],125)
    def test_realistic_game_completion_restart_and_pause(self):
        reporter=GameReporter(self.store,self.session['id'],'sword')
        game=SimpleNamespace(phase='playing',score=50,time=2.,lives=4)
        reporter.observe(game);game.phase='paused';reporter.observe(game)
        game.phase='won';game.score=100;reporter.observe(game);reporter.observe(game)
        reporter.reset();game.phase='playing';game.time=0;reporter.observe(game)
        reporter.close(game)
        view=self.store.view(self.session['token']);self.assertEqual(len(view['rounds']),2)
        self.assertEqual(sorted(r['status'] for r in view['rounds']),['completed','interrupted'])
        self.assertEqual(self.store.leaderboard()[0]['items'][0]['score'],100)
    def test_offline_preview_cannot_publish(self):
        with patch.dict('os.environ',{'INKSUITE_VISITOR_DATA':self.temp.name,'INKSUITE_VISITOR_SESSION':self.session['id']}):
            self.assertIsNone(GameReporter.from_environment('fruit',disabled=True))

from .account_test_support import login_fixture

class HTTPCase(StoreCase):
    def setUp(self):super().setUp();self.portal=Portal(self.store,host='127.0.0.1',port=0,public_url='http://localhost').start();self.base='http://127.0.0.1:'+str(self.portal.port)
    def tearDown(self):self.portal.close();super().tearDown()
    def fetch(self,path,data=None,headers=None):
        headers=dict(headers or {})
        if getattr(self,'cookie',None):
            headers['Cookie']=self.cookie
        request=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        return urllib.request.urlopen(request,timeout=3)
    def test_full_phone_download_name_and_ranking_flow(self):
        self.add();ident=self.store.add_art(self.session['id'],PNG,23);token=self.session['token']
        with self.fetch('/v/'+token) as r:self.assertEqual(r.status,200)
        self.cookie=login_fixture(self.portal,token)
        with self.fetch('/api/visitor/'+token+'/name',{'name':'龙门客'},{'Content-Type':'application/json','X-Ink-Visitor':'1'}) as r:self.assertEqual(json.load(r)['name'],'龙门客')
        with self.fetch('/api/leaderboards') as r:self.assertEqual(json.load(r)['boards'][0]['items'][0]['name'],'龙门客')
        with self.fetch('/v/'+token+'/art/'+ident+'.png') as r:self.assertEqual(r.read(),PNG)
        with self.fetch('/v/'+token+'/report.zip') as r:self.assertIn('attachment',r.headers['Content-Disposition']);self.assertTrue(zipfile.is_zipfile(io.BytesIO(r.read())))
    def test_unauthorized_score_end_and_csrf_blocked(self):
        for path in ('/api/score','/api/finish','/api/current'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.fetch(path,{})
            self.assertEqual(error.exception.code,404)
        path='/api/visitor/'+self.session['token']+'/name'
        with self.assertRaises(urllib.error.HTTPError) as error:self.fetch(path,{'name':'bad'})
        self.assertEqual(error.exception.code,401)
        self.cookie=login_fixture(self.portal,self.session['token'])
        with self.assertRaises(urllib.error.HTTPError) as error:self.fetch(path,{'name':'bad'},{'Content-Type':'application/json'})
        self.assertEqual(error.exception.code,403)
    def test_wireless_address_changes_ethernet_ignored_and_disconnect(self):
        address=NetworkAddress(8091)
        data=[{'ifname':'eth0','addr_info':[{'local':'192.168.9.2','scope':'global'}]}, {'ifname':'wlan0','addr_info':[{'local':'192.168.43.6','scope':'global'}]}]
        with patch('subprocess.check_output',return_value=json.dumps(data).encode()):self.assertEqual(address.snapshot(True)['base_url'],'http://192.168.43.6:8091')
        data[1]['addr_info'][0]['local']='192.168.45.7'
        with patch('subprocess.check_output',return_value=json.dumps(data).encode()):self.assertEqual(address.snapshot(True)['address'],'192.168.45.7')
        with patch('subprocess.check_output',return_value=json.dumps(data[:1]).encode()):self.assertIsNone(address.snapshot(True)['base_url'])

class LayoutCase(unittest.TestCase):
    def test_low_resolution_qr_stays_inside_lower_row_and_decodes(self):
        try:
            import cv2
            import numpy as np
            from PIL import Image
        except ImportError:self.skipTest('QR raster QA requires existing Pillow/OpenCV')
        from .portal import qr_png
        url='http://192.168.43.123:8091/v/'+'A'*43
        image=Image.open(io.BytesIO(qr_png(url))).convert('RGB');modules=image.width//7
        for width,height in ((640,360),(640,420),(800,480),(960,540),(1024,550),(1920,1080)):
            with self.subTest(size=(width,height)):
                box=qr_box(width,height,modules)
                self.assertGreaterEqual(box['pitch'],3)
                self.assertGreaterEqual(box['x'],width*.355-1)
                self.assertLessEqual(box['x']+box['side'],width*.645+1)
                self.assertGreaterEqual(box['y'],height*.55)
                self.assertLessEqual(box['y']+box['side'],height*.915)
                sample=np.asarray(image.resize((int(box['side']),int(box['side'])),Image.Resampling.NEAREST))
                self.assertEqual(cv2.QRCodeDetector().detectAndDecode(sample)[0],url)

if __name__=='__main__':unittest.main()
