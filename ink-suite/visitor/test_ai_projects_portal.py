"""Real loopback HTTP/SQLite project guide and clear contracts; no provider calls."""
import json
import time
import unittest
from pathlib import Path
from ai_assistant.service import AIService
from ai_assistant.projects import PROJECT_IDS
from test_ai_projects import ProjectClient,await_idle


class ProjectPortalTests(unittest.TestCase):
    def setUp(self):
        from visitor.test_ai_portal import PortalAICase
        self.h=PortalAICase();self.h.setUp()
        self.client=ProjectClient()
        self.service=AIService(self.h.store,Path(self.h.temp.name)/'project-ai',client=self.client)
        self.h.portal.ai=self.service

    def tearDown(self):
        self.client.release.set();self.service.close();self.service._worker.join(2)
        self.h.tearDown()

    def request(self,*args,**kwargs):return self.h.request(*args,**kwargs)

    def submit(self,project='fruit',path=None):
        status,value,_=self.request('POST',path=path,value=dict(kind='guide',project=project,preset='rules'))
        self.assertEqual(status,202);return value['job']

    def test_all_projects_and_offline_help_over_real_http(self):
        status,value,_=self.request()
        self.assertEqual(status,200)
        self.assertEqual({p['id'] for p in value['capabilities']['projects']},set(PROJECT_IDS))
        for project in PROJECT_IDS:
            job=self.submit(project);await_idle(self.service)
            status,value,_=self.request(path=self.h.path+'/'+job['id'])
            self.assertEqual(status,200);self.assertEqual(value['job']['project'],project)
            self.assertEqual(value['job']['status'],'ready')
            self.assertEqual(set(value['job']['result']),{'title','summary','tips'})
        self.assertEqual(len(self.client.calls),len(PROJECT_IDS))

    def test_invalid_project_fields_and_280_character_limit(self):
        for value in (dict(kind='guide'),dict(kind='guide',project='koi'),
                      dict(kind='guide',project=[]),dict(kind='guide',project='fruit',command='start'),
                      dict(kind='creation',project='fruit'),dict(kind='guide',project='fruit',text='字'*281)):
            self.assertEqual(self.request('POST',value=value)[0],400)
        self.assertEqual(self.client.calls,[])
        self.assertEqual(self.request('POST',value=dict(kind='guide',project='fruit',text='字'*280))[0],202)
        await_idle(self.service);self.assertEqual(len(self.client.calls),1)

    def test_clear_requires_same_origin_header_and_exact_body(self):
        self.submit();await_idle(self.service);path=self.h.path+'/clear'
        for headers in ({'Origin':'https://evil.test'},{'Origin':'null'},{'X-Ink-Visitor':''}):
            self.assertEqual(self.request('POST',path=path,value={},headers=headers)[0],403)
        for value in (None,[],dict(project='fruit'),dict(token=self.h.token)):
            self.assertEqual(self.request('POST',path=path,raw=json.dumps(value).encode())[0],400)
        self.assertEqual(len(self.service.list_jobs(self.h.token)),1)
        status,value,_=self.request('POST',path=path,value={},headers={'Origin':f'http://127.0.0.1:{self.h.portal.port}'})
        self.assertEqual((status,value),(200,dict(cleared=1,jobs=[])))
        self.assertEqual(self.request('POST',path=path,value={})[1],dict(cleared=0,jobs=[]))
        self.assertEqual(self.request()[1]['jobs'],[])
        self.assertEqual(self.request('POST',path='/api/visitor/'+'x'*43+'/ai/clear',value={})[0],404)

    def test_clear_running_response_cannot_reappear_and_does_not_repeat_api(self):
        self.client.release.clear();job=self.submit();self.assertTrue(self.client.entered.wait(2))
        self.assertEqual(self.request('POST',value=dict(kind='guide',project='taiko'))[0],429)
        self.assertEqual(self.request('POST',path=self.h.path+'/clear',value={})[1]['cleared'],1)
        self.client.release.set();await_idle(self.service)
        self.assertEqual(self.request(path=self.h.path+'/'+job['id'])[0],404)
        self.assertEqual(self.request()[1]['jobs'],[]);self.assertEqual(len(self.client.calls),1)
        self.submit('sword');await_idle(self.service)
        self.assertEqual(len(self.request()[1]['jobs']),1);self.assertEqual(len(self.client.calls),2)

    def test_clear_active_only_keeps_archived_visitor_and_store_state(self):
        original=self.submit();await_idle(self.service);newer=self.h.archive()
        new_path='/api/visitor/'+newer['token']+'/ai'
        self.submit('sword',new_path);await_idle(self.service)
        before=self.h.store.view(newer['token'])
        self.assertEqual(self.request('POST',path=self.h.path+'/clear',value={})[0],409)
        self.assertEqual(self.request('POST',path=new_path+'/clear',value={})[1]['cleared'],1)
        self.assertEqual(self.request(path=self.h.path+'/'+original['id'])[0],200)
        self.assertEqual(self.h.store.view(newer['token']),before)
        self.assertEqual(self.request(path=new_path)[1]['jobs'],[])

    def test_unconfigured_keeps_local_help_and_clear_usable(self):
        self.submit();await_idle(self.service);self.client.configured=False
        status,value,_=self.request();self.assertEqual(status,200)
        self.assertFalse(value['capabilities']['configured'])
        self.assertEqual(len(value['capabilities']['projects']),len(PROJECT_IDS))
        self.assertEqual(self.request('POST',value=dict(kind='guide',project='fruit'))[0],503)
        self.assertEqual(self.request('POST',path=self.h.path+'/clear',value={})[1]['cleared'],1)
        self.assertEqual(len(self.client.calls),1)


if __name__=='__main__':unittest.main()
