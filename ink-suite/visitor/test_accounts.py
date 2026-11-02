"""Real HTTP/SQLite account isolation; temporary local data, no board/API key."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from .accounts import Accounts,AccountError,COOKIE
from .portal import Portal
from .store import VisitorStore
from ai_assistant.service import AIService

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1kAAAAASUVORK5CYII=')

class LocalClient:
    configured=False
    def complete(self,*args):raise AssertionError('No external AI call allowed')

class AccountCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='account-test-')
        self.store=VisitorStore(Path(self.temp.name)/'visitors')
        self.first=self.store.current()
        self.service=AIService(self.store,Path(self.temp.name)/'ai',client=LocalClient())
        self.portal=Portal(self.store,host='127.0.0.1',port=0,ai=self.service).start()

    def tearDown(self):
        self.portal.close();self.service.close();self.service._worker.join(2)
        self.temp.cleanup()

    def request(self,method,path,values=None,cookie=None,headers=None,raw=None):
        body=json.dumps(values).encode() if values is not None else raw
        h={'Content-Type':'application/json','X-Ink-Visitor':'1'}
        if cookie:h['Cookie']=cookie
        h.update(headers or {})
        c=http.client.HTTPConnection('127.0.0.1',self.portal.port,timeout=4)
        try:
            c.request(method,path,body=body,headers=h);r=c.getresponse();data=r.read()
            if r.getheader('Content-Type','').startswith('application/json'):data=json.loads(data)
            return r.status,data,dict(r.getheaders())
        finally:c.close()

    def register(self,name='alice'):
        status,value,headers=self.request('POST','/api/account/register',dict(username=name,password='test-password-123',name=name))
        self.assertEqual(status,201,value)
        cookie=headers['Set-Cookie'].split(';')[0]
        return cookie,value

    def claim(self,cookie,session=None):
        token=(session or self.first)['token']
        status,value,_=self.request('POST','/api/account/claim',dict(token=token),cookie)
        self.assertEqual(status,200,value)
        return value

    def archive(self):
        old=self.store.current();self.store.begin_finish(old['id'],'fixture','fixture')
        return self.store.complete_finish(old['id'])

    def seed_job(self,session):
        ident=uuid.uuid4().hex
        with self.service._db() as db:
            db.execute('INSERT INTO jobs(id,session_id,kind,preset,created,status,signature,payload,result) VALUES(?,?,?,?,?,?,?,?,?)',
                (ident,session['id'],'creation','landscape','2026-10-24T00:00:00Z','ready',ident,'{}','{}'))
        return ident

    def test_legacy_data_survives_but_registration_cannot_read_it(self):
        art=self.store.add_art(self.first['id'],PNG,3)
        self.seed_job(self.first)
        before=self.store.view(self.first['token']);self.archive()
        cookie,state=self.register()
        self.assertEqual(state['sessions'],[]);self.assertIsNone(state['active_token'])
        self.assertEqual(self.request('GET','/api/account',cookie=cookie)[1]['sessions'],[])
        self.assertEqual(self.request('POST','/api/account/claim',dict(token=self.first['token']),cookie)[0],409)
        self.assertEqual(self.request('GET','/api/visitor/'+self.first['token'],cookie=cookie)[0],409)
        self.assertEqual(self.store.artwork(self.first['token'],art)[1],PNG)
        self.assertEqual(self.store.view(self.first['token'])['artworks'],before['artworks'])
        self.assertEqual(len(self.service.list_jobs(self.first['token'])),1)

    def test_registration_hash_login_cookie_and_logout_revocation(self):
        cookie,state=self.register();self.assertTrue(state['authenticated'])
        with self.store.connect() as db:
            row=dict(db.execute('SELECT * FROM accounts').fetchone())
            login=dict(db.execute('SELECT * FROM account_logins').fetchone())
        self.assertNotIn('test-password-123',json.dumps(row));self.assertEqual(len(row['salt']),32)
        self.assertNotEqual(login['digest'],cookie.split('=',1)[1]);self.assertEqual(len(login['digest']),64)
        self.assertEqual(self.request('POST','/api/account/login',dict(username='ALICE',password='wrong-password'))[0],401)
        status,value,h=self.request('POST','/api/account/login',dict(username='ALICE',password='test-password-123'))
        self.assertEqual(status,200);self.assertEqual(value['account'],state['account'])
        self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Lax',h['Set-Cookie'])
        newer=h['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('POST','/api/account/logout',{},newer)[0],200)
        self.assertEqual(self.request('GET','/api/account',cookie=newer)[1],dict(authenticated=False))

    def test_claim_is_explicit_single_owner_and_survives_restart(self):
        a,_=self.register();b,_=self.register('bob')
        self.assertEqual(self.request('GET','/api/visitor/'+self.first['token'],cookie=a)[0],409)
        claim=self.claim(a);self.assertFalse(claim['repeated'])
        self.assertTrue(self.claim(a)['repeated'])
        self.assertEqual(self.request('POST','/api/account/claim',dict(token=self.first['token']),b)[0],403)
        self.assertEqual(self.request('GET','/api/visitor/'+self.first['token'],cookie=b)[0],403)
        self.assertEqual(self.request('GET','/api/visitor/'+self.first['token'],cookie=a)[0],200)
        restored=Accounts(VisitorStore(self.store.root))
        account=restored.resolve(a.split('=',1)[1]);self.assertEqual(len(restored.sessions(account['id'])['sessions']),1)

    def test_two_simultaneous_claims_never_steal(self):
        a,_=self.register();b,_=self.register('bob')
        def claim(cookie):return self.request('POST','/api/account/claim',dict(token=self.first['token']),cookie)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(claim,(a,b)))
        self.assertEqual(sorted(codes),[200,403])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0],1)

    def test_every_private_media_and_api_is_checked_before_dispatch(self):
        a,_=self.register();b,_=self.register('bob');self.claim(a)
        art=self.store.add_art(self.first['id'],PNG,2);token=self.first['token'];ident=uuid.uuid4().hex
        paths=('/api/visitor/'+token,'/api/visitor/'+token+'/photos',
               '/api/visitor/'+token+'/ai','/api/visitor/'+token+'/ai/'+ident,
               f'/v/{token}/art/{art}.png',f'/v/{token}/photos/{ident}/source.png',
               f'/v/{token}/photos/{ident}/result.png',f'/v/{token}/report.png',
               f'/v/{token}/report-images.zip',f'/v/{token}/report.zip',f'/v/{token}/report.json')
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET',path)[0],401)
                self.assertEqual(self.request('GET',path,cookie=b)[0],403)
                self.assertEqual(self.request('GET',path,headers={'X-Forwarded-For':'127.0.0.1','X-Ink-Internal':'1'})[0],401)
        for suffix in ('/name','/photos','/ai','/ai/clear'):
            path='/api/visitor/'+token+suffix
            self.assertEqual(self.request('POST',path,{},cookie=b)[0],403)
            self.assertEqual(self.request('POST',path,{})[0],401)
        self.assertEqual(self.request('GET',f'/v/{token}/art/{art}.png',cookie=a)[1],PNG)
        self.assertEqual(self.request('GET',f'/v/{token}/report.json',cookie=a)[0],200)

    def test_owned_history_lists_no_foreign_or_unclaimed_tokens(self):
        a,_=self.register();self.claim(a);second=self.archive()
        b,_=self.register('bob');self.claim(b,second)
        third=self.archive()
        sa=self.request('GET','/api/account',cookie=a)[1]
        sb=self.request('GET','/api/account',cookie=b)[1]
        self.assertEqual([r['token'] for r in sa['sessions']],[self.first['token']])
        self.assertEqual([r['token'] for r in sb['sessions']],[second['token']])
        self.assertIsNone(sa['active_token']);self.assertIsNone(sb['active_token'])
        self.assertNotIn(third['token'],json.dumps(sa)+json.dumps(sb))
        self.assertEqual(self.request('GET','/api/account/sessions?offset=0&limit=1',cookie=a)[1]['has_more'],False)
        self.assertEqual(self.request('GET','/api/account/sessions?limit=999',cookie=a)[0],400)

    def test_owner_clear_includes_archived_and_running_but_keeps_other_account(self):
        a,_=self.register();self.claim(a);j1=self.seed_job(self.first)
        second=self.archive();self.claim(a,second);j2=self.seed_job(second)
        third=self.archive();b,_=self.register('bob');self.claim(b,third);j3=self.seed_job(third)
        art=self.store.add_art(third['id'],PNG,2)
        with self.service._db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(j1,))
        status,value,_=self.request('POST','/api/account/ai/clear',{},a)
        self.assertEqual((status,value['cleared']),(200,2))
        self.assertEqual(self.service.list_jobs(self.first['token']),[])
        self.assertEqual(self.service.list_jobs(second['token']),[])
        self.assertEqual(self.service.get(third['token'],j3)['id'],j3)
        self.service._finish(j1,'ready',{})
        self.assertEqual(self.service.list_jobs(self.first['token']),[])
        self.assertEqual(self.store.artwork(third['token'],art)[1],PNG)
        self.assertEqual(self.request('POST','/api/account/ai/clear',{},a)[1]['cleared'],0)

    def test_native_clear_for_unclaimed_play_stays_in_its_scope(self):
        self.seed_job(self.first);second=self.archive();self.seed_job(second)
        self.assertEqual(self.service.clear_owner(second['token'])['cleared'],1)
        self.assertEqual(len(self.service.list_jobs(self.first['token'])),1)

    def test_account_writes_require_same_origin_header_and_bounded_body(self):
        a,_=self.register();self.claim(a)
        for headers in ({'Origin':'https://evil.test'},{'Origin':'null'},{'X-Ink-Visitor':''}):
            self.assertEqual(self.request('POST','/api/account/logout',{},a,headers=headers)[0],403)
        for value in ([],dict(token=self.first['token'],account_id='f'*32),None):
            self.assertEqual(self.request('POST','/api/account/claim',value,a,raw=b'null')[0],400)
        self.assertEqual(self.request('POST','/api/account/logout',{},a,headers={'Transfer-Encoding':'chunked'})[0],400)
        self.assertEqual(self.request('POST','/api/account/logout',raw=b'a'*3000,cookie=a)[0],400)
        self.assertEqual(self.request('POST','/api/account/ai/clear',{},None)[0],401)

    def test_empty_account_clear_and_offline_project_info(self):
        a,_=self.register()
        self.assertEqual(self.request('POST','/api/account/ai/clear',{},a)[1],dict(cleared=0,jobs=[]))
        status,value,_=self.request('GET','/api/projects')
        self.assertEqual(status,200);self.assertGreaterEqual(len(value['projects']),12)
        self.assertNotIn('jobs',value);self.assertNotIn('token',json.dumps(value))

    def test_cookie_expiry_and_unknown_sessions_fail_closed(self):
        a,_=self.register();self.claim(a)
        with self.store.connect() as db:db.execute('UPDATE account_logins SET expires=?',(time.time()-1,))
        self.assertEqual(self.request('GET','/api/account',cookie=a)[1],dict(authenticated=False))
        self.assertEqual(self.request('GET','/api/visitor/'+self.first['token'],cookie=a)[0],401)
        b,_=self.register('bob')
        self.assertEqual(self.request('GET','/api/visitor/'+'x'*43,cookie=b)[0],404)

    def test_username_normalization_validation_and_rate_limit(self):
        self.register()
        self.assertEqual(self.request('POST','/api/account/register',dict(username='ＡＬＩＣＥ',password='password123'))[0],409)
        for name,secret in (('xx','password123'),('bad user','password123'),('ok_name','short'),('x'*33,'password123')):
            self.assertEqual(self.request('POST','/api/account/register',dict(username=name,password=secret))[0],400)
        # No password work after the bounded attempt budget is exhausted.
        for _ in range(35):last=self.request('POST','/api/account/login',dict(username='alice',password='wrong-password'))
        self.assertEqual(last[0],429)

    def test_cutover_seals_old_active_content_without_modifying_records(self):
        from .account_migrate import seal_legacy
        art=self.store.add_art(self.first['id'],PNG,3)
        self.seed_job(self.first)
        before=self.store.view(self.first['token'])
        with self.service._db() as db:ai_before=[tuple(r) for r in db.execute('SELECT * FROM jobs ORDER BY id')]
        result=seal_legacy(self.store.path)
        self.assertTrue(result['core_preserved']);self.assertEqual(result['sealed_count'],1)
        cookie,_=self.register()
        for method,path,value in (('POST','/api/account/claim',dict(token=self.first['token'])),
                                  ('GET','/api/visitor/'+self.first['token'],None)):
            status,body,_=self.request(method,path,value,cookie)
            self.assertEqual((status,body['code']),(409,'legacy_session'))
            self.assertIn('在板端结束本次游玩',body['message'])
        self.assertEqual(self.store.view(self.first['token']),before)
        self.assertEqual(self.store.artwork(self.first['token'],art)[1],PNG)
        with self.service._db() as db:self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM jobs ORDER BY id')],ai_before)
        fresh=self.archive()
        self.claim(cookie,fresh)
        self.assertEqual(self.request('GET','/api/visitor/'+fresh['token'],cookie=cookie)[0],200)
        self.assertEqual(self.request('GET','/api/account',cookie=cookie)[1]['sessions'][0]['token'],fresh['token'])
        repeated=seal_legacy(self.store.path)
        self.assertTrue(repeated['repeated']);self.assertEqual(repeated['sealed_count'],1)
        self.assertEqual(self.request('GET','/api/visitor/'+fresh['token'],cookie=cookie)[0],200)

    def test_cutover_keeps_owned_history_and_undo_only_removes_its_markers(self):
        from .account_migrate import seal_legacy
        cookie,_=self.register();self.claim(cookie)
        second=self.archive();self.seed_job(second)
        result=seal_legacy(self.store.path)
        self.assertEqual(result['sealed_count'],1)
        self.assertEqual(self.request('GET','/api/visitor/'+self.first['token'],cookie=cookie)[0],200)
        self.assertEqual(self.request('POST','/api/account/claim',dict(token=second['token']),cookie)[1]['code'],'legacy_session')
        undone=seal_legacy(self.store.path,undo=True)
        self.assertTrue(undone['core_preserved']);self.assertEqual(undone['sealed_count'],1)
        self.claim(cookie,second)
        self.assertEqual(len(self.service.list_jobs(second['token'])),1)

    def test_cutover_accepts_original_database_before_account_tables_exist(self):
        from .account_migrate import seal_legacy
        old=VisitorStore(Path(self.temp.name)/'original-before-upgrade')
        session=old.current();old.add_art(session['id'],PNG,3)
        with old.connect() as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())
        result=seal_legacy(old.path)
        self.assertEqual(result['sealed_count'],1);self.assertTrue(result['core_preserved'])
        accounts=Accounts(old);account=accounts.register(dict(username='new-user',password='test-password-123'))
        with self.assertRaises(AccountError) as error:accounts.claim(account,session['token'])
        self.assertEqual(error.exception.code,'legacy_session')

    def test_public_board_contains_registered_nicknames_and_scores_only(self):
        cookie,_=self.register();self.claim(cookie)
        self.store.record(self.first['id'],uuid.uuid4().hex,'fruit',dict(score=300),status='completed')
        anonymous=self.archive()
        self.store.record(anonymous['id'],uuid.uuid4().hex,'fruit',dict(score=999999),status='completed')
        body=self.request('GET','/api/leaderboards',cookie=cookie)[1]
        self.assertEqual(len(body['boards']),1)
        self.assertEqual(body['boards'][0]['items'][0]['score'],300)
        self.assertEqual(body['boards'][0]['items'][0]['name'],'alice')
        self.assertEqual(set(body['boards'][0]['items'][0]),{'rank','name','score','completed_at'})
        self.assertNotIn(self.first['token'],json.dumps(body));self.assertNotIn(anonymous['token'],json.dumps(body))
        self.assertEqual(self.request('GET','/api/leaderboards')[0],401)

if __name__=='__main__':unittest.main()
