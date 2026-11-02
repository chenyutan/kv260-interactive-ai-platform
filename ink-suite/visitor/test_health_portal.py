"""Private course summaries and constrained health AI, through actual HTTP."""
import time
import unittest
from test_ai_health import report,LocalClient

class HealthPortalTests(unittest.TestCase):
    def setUp(self):
        from .test_accounts import AccountCase
        self.h=AccountCase('runTest');self.h.setUp()
        self.cookie,_=self.h.register('health-alice');self.h.claim(self.cookie)
        self.h.service.client=LocalClient()
    def tearDown(self):self.h.tearDown()
    def test_summary_requires_login_and_owner(self):
        self.h.store.record_health(self.h.first['id'],report())
        path='/api/visitor/'+self.h.first['token']+'/health'
        self.assertEqual(self.h.request('GET',path)[0],401)
        status,value,_=self.h.request('GET',path,cookie=self.cookie)
        self.assertEqual(status,200);self.assertEqual(value['health_summary']['report_count'],1)
        other,_=self.h.register('health-bob')
        self.assertEqual(self.h.request('GET',path,cookie=other)[0],403)
        self.assertEqual(self.h.request('GET',path+'?course=bad',cookie=self.cookie)[0],400)
    def test_ai_snapshot_includes_both_courses_without_raw_report(self):
        self.h.store.record_health(self.h.first['id'],report())
        status,value,_=self.h.request('GET','/api/visitor/'+self.h.first['token']+'/ai',cookie=self.cookie)
        self.assertEqual(status,200);self.assertEqual(set(value['health_summary']),{'baduanjin','taichi'})
        self.assertFalse(value['health_summary']['taichi']['records_present'])
        self.assertNotIn('PRIVATE PERSON',str(value));self.assertNotIn('/private/',str(value))
    def test_health_request_keeps_project_and_uses_saved_evidence(self):
        self.h.store.record_health(self.h.first['id'],report())
        status,value,_=self.h.request('POST','/api/visitor/'+self.h.first['token']+'/ai',
                                     dict(kind='health',project='baduanjin',preset='next'),self.cookie)
        self.assertEqual(status,202,value)
        ident=value['job']['id']
        for _ in range(100):
            job=self.h.service.get(self.h.first['token'],ident)
            if job['status'] in ('ready','error'):break
            time.sleep(.01)
        self.assertEqual(job['status'],'ready');self.assertEqual(job['project'],'baduanjin')
        self.assertEqual(job['kind'],'health')
    def test_phone_cannot_request_terminal_clear_or_fabricate_health_record(self):
        first=self.h.seed_job(self.h.first)
        self.assertEqual(self.h.request('POST','/api/account/ai/clear',{'scope':'terminal_all'},self.cookie)[0],400)
        self.assertEqual(self.h.request('POST','/api/account/ai/clear-all',{},self.cookie)[0],404)
        self.assertEqual(self.h.request('POST','/api/visitor/'+self.h.first['token']+'/health',report(),self.cookie)[0],404)
        self.assertEqual(self.h.service.get(self.h.first['token'],first)['status'],'ready')
    def test_public_projects_offer_health_presets_but_not_private_records(self):
        self.h.store.record_health(self.h.first['id'],report())
        status,value,_=self.h.request('GET','/api/projects')
        self.assertEqual(status,200);self.assertEqual({p['id'] for p in value['presets']['health']},{'next','review','demo'})
        self.assertNotIn('health_summary',value);self.assertNotIn(self.h.first['id'],str(value))
    def test_owned_archived_health_is_allowed_and_foreign_account_is_denied(self):
        self.h.store.record_health(self.h.first['id'],report());self.h.archive()
        path='/api/visitor/'+self.h.first['token']+'/ai'
        payload=dict(kind='health',project='baduanjin',preset='next')
        status,value,_=self.h.request('POST',path,payload,self.cookie)
        self.assertEqual(status,202,value)
        other,_=self.h.register('foreign-health')
        self.assertEqual(self.h.request('POST',path,payload,other)[0],403)
        self.assertEqual(self.h.request('POST',path,dict(kind='guide',project='fruit',preset='rules'),self.cookie)[0],409)
        self.assertEqual(self.h.request('POST',path+'/clear',{},self.cookie)[0],409)
        self.assertEqual(self.h.request('GET',path,cookie=self.cookie)[1]['health_summary']['baduanjin']['report_count'],1)

if __name__=='__main__':unittest.main()
