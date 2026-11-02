"""Real loopback HTTP + SQLite, fake AI only; never uses keys or the board."""
import copy
import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from ai_assistant.contracts import AIError
from ai_assistant import AIService
from .portal import Portal
from .store import VisitorStore


class FakeAI:
    def __init__(self, store):
        self.store = store
        self.jobs = {}
        self.calls = []
        self.failure = None
        self.closed = False
        self.configured = True

    def capabilities(self):
        return dict(configured=self.configured, model='deepseek-flash', thinking='disabled',
                    presets={'creation': [dict(id='landscape', title='山水', description='画山与水')],
                             'review': [dict(id='overview', title='本次复盘', description='看看本次成绩')]})

    def submit(self, token, kind, preset='', text=''):
        if self.failure:
            raise self.failure
        self.calls.append((token, kind, preset, text))
        job = dict(id=f'{len(self.calls):032x}', kind=kind, preset=preset,
                   created='2026-10-22T12:00:00+08:00', status='queued', result=None,
                   error=None, cache_hit=False)
        self.jobs.setdefault(token, []).insert(0, job)
        return copy.deepcopy(job)

    def list_jobs(self, token):
        if self.failure:
            raise self.failure
        self.store.by_token(token)
        return copy.deepcopy(self.jobs.get(token, []))

    def get(self, token, job_id):
        for job in self.list_jobs(token):
            if job['id'] == job_id:
                return job
        raise KeyError(job_id)

    def close(self):
        self.closed = True


class PortalAICase(unittest.TestCase):
    def setUp(self):
        temporary_root=Path(__file__).resolve().parents[1]/'build'/'ai-test-tmp'
        temporary_root.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='visitor-ai-',dir=temporary_root)
        self.store = VisitorStore(self.temp.name)
        self.session = self.store.current()
        self.token = self.session['token']
        self.ai = FakeAI(self.store)
        self.portal = Portal(self.store, host='127.0.0.1', port=0,
                             public_url='http://localhost', ai=self.ai).start()
        from .account_test_support import login_fixture
        self.cookie=login_fixture(self.portal,self.token)
        self.path = '/api/visitor/' + self.token + '/ai'

    def tearDown(self):
        self.portal.close()
        self.temp.cleanup()

    def request(self, method='GET', path=None, value=None, raw=None, headers=None):
        body = json.dumps(value, ensure_ascii=False).encode() if value is not None else raw
        merged = {'Content-Type': 'application/json', 'X-Ink-Visitor': '1'}
        merged['Cookie']=self.cookie
        merged.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.portal.port, timeout=3)
        try:
            connection.request(method, path or self.path, body=body, headers=merged)
            response = connection.getresponse()
            content = response.read()
            return response.status, json.loads(content), dict(response.getheaders())
        finally:
            connection.close()

    def archive(self):
        self.store.begin_finish(self.session['id'], 'test-finish', 'fixture')
        newer=self.store.complete_finish(self.session['id'])
        from .account_test_support import claim_fixture
        claim_fixture(self.portal,self.cookie,newer['token'])
        return newer

    def test_capabilities_authenticated_no_submission(self):
        for _ in range(3):
            status, value, headers = self.request()
            self.assertEqual(status, 200)
            self.assertTrue(value['active'])
            self.assertEqual(value['jobs'], [])
            self.assertEqual(value['capabilities'], self.ai.capabilities())
            self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.ai.calls, [])
        self.assertEqual(self.request(path='/api/visitor/'+'x'*43+'/ai')[0], 404)

    def test_optional_ai_compatibility_still_authenticates(self):
        self.portal.ai = None
        status, value, _ = self.request()
        self.assertEqual(status, 200)
        self.assertFalse(value['capabilities']['configured'])
        self.assertEqual(value['jobs'], [])
        self.assertEqual(self.request('POST', value={'kind': 'creation'})[0], 503)
        self.assertEqual(self.request(path='/api/visitor/'+'x'*43+'/ai')[0], 404)
        self.assertEqual(self.request(path=self.path+'/'+'1'*32)[0], 404)

    def test_creation_and_review_submit_then_poll_only(self):
        for kind, preset in [('creation', 'landscape'), ('review', 'overview')]:
            status, value, _ = self.request('POST', value={'kind': kind, 'preset': preset, 'text': '我的想法'})
            self.assertEqual(status, 202)
            self.assertEqual(value['job']['status'], 'queued')
            job_id = value['job']['id']
            self.assertEqual(self.request(path=self.path+'/'+job_id)[1]['job']['kind'], kind)
        for _ in range(8):
            self.assertEqual(self.request()[0], 200)
        self.assertEqual(len(self.ai.calls), 2)
        self.assertEqual(self.ai.calls[-1], (self.token, 'review', 'overview', '我的想法'))

    def test_service_sanitized_error_and_unexpected_secret_not_exposed(self):
        self.ai.failure = AIError('busy', '正在生成建议，请稍候。', 429)
        status, value, _ = self.request('POST', value={'kind': 'creation'})
        self.assertEqual((status, value['code']), (429, 'busy'))
        self.ai.failure = RuntimeError('SECRET_PRIVATE_TOKEN_SHOULD_NEVER_LEAK')
        for method, body in [('GET', None), ('POST', {'kind': 'creation'})]:
            status, value, _ = self.request(method, value=body)
            self.assertEqual(status, 503)
            self.assertNotIn('SECRET', json.dumps(value))
        self.assertEqual(self.ai.calls, [])

    def test_origin_and_header_required(self):
        for headers in [{'Origin': 'https://evil.test'}, {'Origin': 'null'},
                        {'X-Ink-Visitor': ''}, {'Origin': 'http://127.0.0.1:1'}]:
            self.assertEqual(self.request('POST', value={'kind': 'creation'}, headers=headers)[0], 403)
        headers = {'Origin': f'http://127.0.0.1:{self.portal.port}'}
        self.assertEqual(self.request('POST', value={'kind': 'creation'}, headers=headers)[0], 202)
        self.assertEqual(len(self.ai.calls), 1)

    def test_json_size_format_and_unknown_commands(self):
        values = [None, [], {}, {'kind': 'clear'}, {'kind': 'creation', 'score': 999},
                  {'kind': 'creation', 'text': []}, {'kind': 'creation', 'preset': {}},
                  {'kind': 'creation', 'preset': 'p'*65}, {'kind': 'review', 'text': '字'*281}]
        for value in values:
            raw = json.dumps(value, ensure_ascii=False).encode()
            with self.subTest(value=str(value)[:60]):
                self.assertEqual(self.request('POST', raw=raw)[0], 400)
        self.assertEqual(self.request('POST', raw=b'x'*2049)[0], 413)
        self.assertEqual(self.request('POST', raw=b'{')[0], 400)
        self.assertEqual(self.request('POST', raw=b'\xff')[0], 400)
        self.assertEqual(self.request('POST', raw=b'['*1001+b']'*1001)[0], 400)
        self.assertEqual(self.request('POST', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', raw=b'{}', headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.request('POST', raw=b'{}', headers={'Content-Length': 'invalid'})[0], 400)
        self.assertEqual(self.ai.calls, [])

    def test_unicode_280_chars_accepted(self):
        self.assertEqual(self.request('POST', value={'kind': 'creation', 'text': '山'*280})[0], 202)
        self.assertEqual(len(self.ai.calls[0][-1]), 280)

    def test_ended_read_only_history_and_cross_session_isolation(self):
        job = self.request('POST', value={'kind': 'creation'})[1]['job']
        result = {'title': '山水', 'summary': '先画山，再点水。',
                  'steps': [{'theme': 'landscape', 'action': 'A01', 'instruction': '双臂展开'}]}
        self.ai.jobs[self.token][0].update(status='ready', result=result)
        newer = self.archive()
        status, value, _ = self.request()
        self.assertEqual(status, 200)
        self.assertFalse(value['active'])
        self.assertEqual(value['jobs'][0]['result'], result)
        self.assertEqual(self.request('POST', value={'kind': 'review'})[0], 409)
        self.assertEqual(self.request(path=self.path+'/'+job['id'])[0], 200)
        newer_path = '/api/visitor/'+newer['token']+'/ai'
        self.assertEqual(self.request(path=newer_path)[1]['jobs'], [])
        self.assertEqual(self.request(path=newer_path+'/'+job['id'])[0], 404)
        self.assertEqual(len(self.ai.calls), 1)

    def test_ending_rejected_and_no_remote_game_controls(self):
        self.store.begin_finish(self.session['id'], 'finish-key', 'art-hash')
        self.assertEqual(self.request('POST', value={'kind': 'review'})[0], 409)
        self.assertEqual(self.request('POST',path=self.path+'/clear',value={})[0],409)
        for suffix in ['/end', '/score', '/ai/start', '/ai/draw']:
            self.assertEqual(self.request('POST', path='/api/visitor/'+self.token+suffix,
                                          value={'command': 'clear'})[0], 404)
        self.assertEqual(self.ai.calls, [])

    def test_portal_does_not_own_shared_ai_lifecycle(self):
        self.portal.close()
        self.assertFalse(self.ai.closed)

    def test_real_service_plan_choice_keeps_public_steps_contract(self):
        class ModelFixture:
            configured=True
            def __init__(self):self.calls=[]
            def complete(self,messages):
                self.calls.append(copy.deepcopy(messages))
                return dict(title='清简一竹',summary='先画竹干，再添叶。',plan_id='bamboo_sparse')
        client=ModelFixture();service=AIService(self.store,Path(self.temp.name)/'ai',client=client)
        self.portal.ai=service
        try:
            status,value,_=self.request('POST',value=dict(kind='creation',preset='bamboo'))
            self.assertEqual(status,202);ident=value['job']['id'];deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                job=self.request(path=self.path+'/'+ident)[1]['job']
                if job['status'] in ('ready','error'):break
                time.sleep(.01)
            self.assertEqual(job['status'],'ready')
            self.assertEqual(set(job['result']),{'title','summary','steps'})
            self.assertEqual([s['action'] for s in job['result']['steps']],['A02','A06'])
            self.assertTrue(all('手柄' in s['instruction'] for s in job['result']['steps']))
            payload=json.loads(client.calls[0][-1]['content'])
            self.assertEqual({p['preset'] for p in payload['evidence']['plans']},{'bamboo'})
            self.assertEqual(self.request('POST',value=dict(kind='creation',preset='bamboo'))[1]['job']['id'],ident)
            self.assertEqual(len(client.calls),1)
        finally:
            service.close();service._worker.join(2)


if __name__ == '__main__':
    unittest.main()
