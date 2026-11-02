"""Owned saved health evidence and terminal-only clear; no camera/provider."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from visitor.store import VisitorStore
from visitor.accounts import Accounts
from ai_assistant.service import AIService
from ai_assistant.health import sanitize_report,summarize,evidence,result
from ai_assistant.contracts import AIError
from health.course_registry import get_course

def report(course='baduanjin',ident=None,practice=12,tracked=8,reps=2):
    meta=get_course(course);lesson=next((row for row in meta['lessons'] if row['observable']),meta['lessons'][0])
    return dict(session_id=ident or uuid.uuid4().hex,course_id=course,kind=meta['report_kind'],
        mode='follow',active_seconds=practice,report_save_state='saved',report_path='/private/hidden.json',
        name='PRIVATE PERSON',pose_points=[[.1,.2]]*17,sources=['PRIVATE SOURCE'],
        lessons=[dict(id=lesson['id'],practice_seconds=practice,tracked_seconds=tracked,
                      observed_repetitions=reps,visits=1)])

class LocalClient:
    configured=True
    def __init__(self):self.messages=[]
    def complete(self,messages):
        self.messages.append(messages);proof=json.loads(messages[-1]['content'])['evidence']
        return dict(fact_ids=[proof['facts'][0]['id']],plan_id=proof['preferred_plan'])

class HeldClient(LocalClient):
    def __init__(self):super().__init__();self.entered=threading.Event();self.release=threading.Event()
    def complete(self,messages):
        self.entered.set();self.release.wait(3);return super().complete(messages)

class HealthAITests(unittest.TestCase):
    def setUp(self):
        base=Path(os.environ.get('INK_AI_TEST_TMP',str(Path(__file__).resolve().parents[2]/'temp/health-backend')))
        base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='ai-health-',dir=base)
        self.store=VisitorStore(Path(self.temp.name)/'visitors');self.session=self.store.current()
        self.accounts=Accounts(self.store);self.client=LocalClient()
        self.service=AIService(self.store,Path(self.temp.name)/'ai',client=self.client)
    def tearDown(self):
        if hasattr(self.client,'release'):self.client.release.set()
        self.service.close();self.service._worker.join(4);self.temp.cleanup()
    def owner(self,username):
        return self.accounts.register(dict(username=username,password='test-only-password',name=username))
    def next(self):
        old=self.store.current();self.store.begin_finish(old['id'],uuid.uuid4().hex,'none')
        return self.store.complete_finish(old['id'])
    def wait(self,token,ident):
        for _ in range(150):
            job=self.service.get(token,ident)
            if job['status'] in ('ready','error'):return job
            time.sleep(.01)
        self.fail('Worker did not finish')
    def test_empty_is_honest_and_never_scans_legacy_reports(self):
        (Path(self.temp.name)/'old-health.json').write_text(json.dumps(report()))
        snapshot=self.service.health_snapshot(self.session['token'],'baduanjin')
        self.assertFalse(snapshot['records_present']);self.assertFalse(snapshot['observations_ready'])
        self.assertEqual(snapshot['report_count'],0);self.assertIn('尚无归属',snapshot['summary'])
    def test_record_is_bounded_and_drops_private_raw_fields(self):
        raw=report();self.store.record_health(self.session['id'],raw)
        with self.store.connect() as db:payload=json.loads(db.execute('SELECT data FROM health_practices').fetchone()[0])
        for key in ('pose_points','name','sources','report_path'):self.assertNotIn(key,payload)
        self.assertEqual(payload['lessons'][0]['tracked_seconds'],8)
    def test_duplicate_report_updates_once_and_owner_cannot_change(self):
        raw=report();self.store.record_health(self.session['id'],raw);self.store.record_health(self.session['id'],raw)
        self.assertEqual(self.store.health_summary(self.session['token'])['report_count'],1)
        newer=self.next()
        with self.assertRaises(ValueError):self.store.record_health(newer['id'],raw)
        self.assertEqual(self.store.health_summary(newer['token'])['report_count'],0)
    def test_delayed_save_uses_captured_owner_not_current(self):
        next_session=self.next();self.store.record_health(self.session['id'],report())
        self.assertEqual(self.store.health_summary(self.session['token'])['report_count'],1)
        self.assertEqual(self.store.health_summary(next_session['token'])['report_count'],0)
    def test_account_sees_own_archived_health_only(self):
        alice=self.owner('alice-health');self.accounts.claim(alice,self.session['token'])
        self.store.record_health(self.session['id'],report())
        second=self.next();bob=self.owner('bob-health');self.accounts.claim(bob,second['token'])
        self.store.record_health(second['id'],report('taichi'))
        self.assertEqual(self.store.health_summary(second['token'],'baduanjin')['report_count'],0)
        third=self.next();self.accounts.claim(alice,third['token'])
        self.assertEqual(self.store.health_summary(third['token'],'baduanjin')['report_count'],1)
        self.assertEqual(self.store.health_summary(third['token'],'taichi')['report_count'],0)
    def test_zero_tracking_is_not_a_motion_error_or_missing_effort(self):
        self.store.record_health(self.session['id'],report(tracked=0))
        snapshot=self.store.health_summary(self.session['token'])
        self.assertTrue(snapshot['records_present']);self.assertFalse(snapshot['observations_ready'])
        self.assertEqual(len(snapshot['recorded_lessons']),1)
        self.assertIn('不据此判断做错或未做',snapshot['summary']);proof=evidence(snapshot)
        self.assertTrue(any('不能据此判断做错' in row['text'] for row in proof['facts']))
        self.assertEqual(snapshot['unobserved_lessons'],['raise'])
        self.assertEqual(snapshot['assessment_not_supported_lessons'],[])
    def assert_steps(self,plan,lesson):
        for index,text in enumerate(lesson['instructions']):
            self.assertIn(f'步骤 {index+1}：{text}',plan['tips'])
    def test_zero_or_short_observation_still_has_specific_course_advice(self):
        for tracked in (0,.1,.99):
            with self.subTest(tracked=tracked):
                snapshot=summarize([sanitize_report(report(tracked=tracked))],1,'baduanjin')
                self.assertFalse(snapshot['observations_ready'])
                proof=evidence(snapshot,'review')
                review=next(row for row in proof['plans'] if row['id']=='review_raise')
                self.assert_steps(review,get_course('baduanjin')['by_id']['raise'])
                reply=result(dict(fact_ids=['health.records','health.visibility'],plan_id=review['id']),proof)
                self.assertEqual(reply['tips'],review['tips'])
                self.assertIn('供参考',reply['tips'][0])
                self.assertNotIn('暂不提出',json.dumps(reply,ensure_ascii=False))
                self.assert_steps(snapshot['local_help'],get_course('baduanjin')['by_id']['raise'])
    def test_first_use_both_courses_gets_action_steps_without_waiting_for_a_record(self):
        for course_id,first in (('baduanjin','raise'),('taichi','prepare')):
            with self.subTest(course_id=course_id):
                snapshot=self.service.health_snapshot(self.session['token'],course_id)
                self.assertFalse(snapshot['records_present'])
                self.assertNotIn('先完成并保存一轮课程，再查看',snapshot['summary'])
                self.assert_steps(snapshot['local_help'],get_course(course_id)['by_id'][first])
                for preset in ('next','review','demo'):
                    proof=evidence(snapshot,preset)
                    plan=next(row for row in proof['plans'] if row['id']==proof['preferred_plan'])
                    self.assert_steps(plan,get_course(course_id)['by_id'][first])
                    job=self.service.submit(self.session['token'],'health',preset,project=course_id)
                    ready=self.wait(self.session['token'],job['id'])
                    self.assertEqual(ready['status'],'ready')
                    self.assert_steps(ready['result'],get_course(course_id)['by_id'][first])
    def test_each_recorded_taichi_form_is_supported_for_advice_without_motion_scoring(self):
        meta=get_course('taichi')
        for lesson in meta['lessons']:
            with self.subTest(lesson=lesson['id']):
                raw=report('taichi',reps=900,tracked=10)
                raw['lessons'][0]['id']=lesson['id']
                record=sanitize_report(raw);snapshot=summarize([record],1,'taichi')
                row=snapshot['recorded_lessons'][0]
                self.assertEqual(row['tracked_seconds'],0)
                self.assertEqual(row['observed_repetitions'],0)
                self.assertEqual(snapshot['unobserved_lessons'],[])
                self.assertEqual(snapshot['assessment_not_supported_lessons'],[lesson['id']])
                self.assertNotIn('观测不足',snapshot['summary'])
                self.assertNotIn('0 式有至少',snapshot['summary'])
                proof=evidence(snapshot,'review')
                review=next(plan for plan in proof['plans'] if plan['id']=='review_'+lesson['id'])
                self.assert_steps(review,lesson)
                self.assertTrue(any(row['id']=='health.assessment_scope' for row in proof['facts']))
    def test_mixed_supported_weak_and_not_assessed_forms_are_not_conflated(self):
        raw=report();raw['lessons'].extend([
            dict(id='bow',practice_seconds=12,tracked_seconds=.3,observed_repetitions=0,visits=1),
            dict(id='look',practice_seconds=12,tracked_seconds=10,observed_repetitions=8,visits=1)])
        snapshot=summarize([sanitize_report(raw)],1,'baduanjin')
        self.assertEqual(snapshot['observed_lessons'],['raise'])
        self.assertEqual(snapshot['unobserved_lessons'],['bow'])
        self.assertEqual(snapshot['assessment_not_supported_lessons'],['look'])
        proof=evidence(snapshot,'review')
        weak=next(fact for fact in proof['facts'] if fact['id']=='health.visibility')
        self.assertIn('左右开弓',weak['text']);self.assertNotIn('往后瞧',weak['text'])
        unassessed=next(fact for fact in proof['facts'] if fact['id']=='health.assessment_scope')
        self.assertIn('往后瞧',unassessed['text']);self.assertNotIn('左右开弓',unassessed['text'])
        review=next(plan for plan in proof['plans'] if plan['id']==proof['preferred_plan'])
        self.assertEqual(review['id'],'review_bow')
        self.assert_steps(review,get_course('baduanjin')['by_id']['bow'])
    def test_taichi_does_not_invent_repetition_counts(self):
        raw=report('taichi',reps=900);self.store.record_health(self.session['id'],raw)
        snapshot=self.store.health_summary(self.session['token'],'taichi')
        self.assertEqual(snapshot['recorded_lessons'][0]['observed_repetitions'],0)
    def test_invalid_report_and_unsafe_stats_rejected(self):
        for raw in (report(practice=float('inf')),report(tracked=-1),dict(report(),session_id='../path')):
            with self.assertRaises((ValueError,AIError)):self.store.record_health(self.session['id'],raw)
        pending=dict(report(),report_save_state='pending')
        with self.assertRaises(ValueError):self.store.record_health(self.session['id'],pending)
    def test_health_ai_selects_local_plan_and_exports_no_identity(self):
        raw=report();self.store.record_health(self.session['id'],raw)
        job=self.service.submit(self.session['token'],'health','next',project='baduanjin')
        ready=self.wait(self.session['token'],job['id']);self.assertEqual(ready['status'],'ready')
        sent=json.dumps(self.client.messages,ensure_ascii=False)
        for secret in (self.session['token'],self.session['id'],raw['session_id'],'PRIVATE PERSON','PRIVATE SOURCE','/private/hidden.json'):
            self.assertNotIn(secret,sent)
        self.assertEqual(ready['kind'],'health');self.assertEqual(ready['project'],'baduanjin')
        self.assertIn('先看',ready['result']['tips'][0])
    def test_health_cache_changes_after_new_saved_record(self):
        first=self.service.submit(self.session['token'],'health','next',project='baduanjin')
        self.wait(self.session['token'],first['id'])
        self.store.record_health(self.session['id'],report())
        second=self.service.submit(self.session['token'],'health','next',project='baduanjin')
        self.assertNotEqual(first['id'],second['id'])
    def test_owned_archived_session_can_generate_health_but_not_other_kinds(self):
        alice=self.owner('archived-health');self.accounts.claim(alice,self.session['token'])
        self.store.record_health(self.session['id'],report());self.next()
        self.assertTrue(self.store.has_account_owner(self.session['token']))
        job=self.service.submit(self.session['token'],'health','review',project='baduanjin')
        self.assertEqual(self.wait(self.session['token'],job['id'])['status'],'ready')
        self.assertTrue(self.service.capabilities()['health_history'])
        for kind,preset,project in (('creation','landscape',''),('review','overview',''),('guide','rules','fruit')):
            with self.assertRaises(AIError) as caught:self.service.submit(self.session['token'],kind,preset,project=project)
            self.assertEqual(caught.exception.code,'session_closed')
    def test_unowned_archived_and_ending_health_cannot_generate(self):
        self.next();self.assertFalse(self.store.has_account_owner(self.session['token']))
        with self.assertRaises(AIError) as caught:self.service.submit(self.session['token'],'health','next',project='baduanjin')
        self.assertEqual(caught.exception.code,'session_closed')
        current=self.store.current();alice=self.owner('ending-health');self.accounts.claim(alice,current['token'])
        self.store.begin_finish(current['id'],'fixture','fixture')
        with self.assertRaises(AIError):self.service.submit(current['token'],'health','next',project='baduanjin')
    def test_owned_health_running_when_finished_still_finishes_readonly(self):
        self.service.close();self.service._worker.join(3);self.client=HeldClient()
        self.service=AIService(self.store,Path(self.temp.name)/'held-archive',client=self.client)
        self.accounts.claim(self.owner('late-health'),self.session['token'])
        job=self.service.submit(self.session['token'],'health','next',project='baduanjin')
        self.assertTrue(self.client.entered.wait(1));self.next();self.client.release.set()
        self.assertEqual(self.wait(self.session['token'],job['id'])['status'],'ready')
    def test_model_cannot_invent_a_plan_or_new_medical_text(self):
        proof=evidence(self.store.health_summary(self.session['token']))
        for value in (dict(fact_ids=['health.records'],plan_id='new-plan'),
                      dict(fact_ids=['health.records'],plan_id=proof['preferred_plan'],summary='治疗疾病')):
            with self.assertRaises(AIError):result(value,proof)
    def test_terminal_clear_includes_unowned_archives_and_other_accounts(self):
        # Historical rows may belong to old anonymous/orphan identities.
        with self.service._db() as db:
            for owner in ('old-anonymous',self.session['id'],'another-account'):
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex,owner,'guide','rules','now','ready',uuid.uuid4().hex,'{}','{}',''))
        result=self.service.clear_all()
        self.assertEqual(result,dict(cleared=3,remaining=0,scope='terminal_all',jobs=[]))
        self.assertEqual(self.service.clear_all()['cleared'],0)
        self.assertEqual(self.store.current()['id'],self.session['id'])
    def test_terminal_clear_cancels_queued_and_late_work_cannot_reappear(self):
        self.service.close();self.service._worker.join(3);self.client=HeldClient()
        self.service=AIService(self.store,Path(self.temp.name)/'held',client=self.client)
        first=self.service.submit(self.session['token'],'health','next',project='baduanjin')
        self.assertTrue(self.client.entered.wait(1))
        second_session=self.next()
        self.service.submit(second_session['token'],'health','next',project='taichi')
        self.assertEqual(self.service.clear_all()['cleared'],2)
        self.client.release.set();self.service._queue.join()
        self.assertEqual(self.service.list_jobs(self.session['token']),[])
        self.assertEqual(self.service.list_jobs(second_session['token']),[])
        with self.service._db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
        self.assertEqual(self.service._queue.unfinished_tasks,0)
    def test_terminal_clear_also_cancels_submission_still_building_evidence(self):
        original=self.store.health_summary;entered=threading.Event();release=threading.Event();errors=[]
        def delayed(*args):
            entered.set();release.wait(3);return original(*args)
        self.store.health_summary=delayed
        def send():
            try:self.service.submit(self.session['token'],'health','next',project='baduanjin')
            except AIError as exc:errors.append(exc.code)
        request=threading.Thread(target=send);request.start()
        self.assertTrue(entered.wait(1));self.service.clear_all();release.set();request.join(3)
        self.assertFalse(request.is_alive());self.assertEqual(errors,['cleared'])
        self.assertEqual(self.service.list_jobs(self.session['token']),[])

if __name__=='__main__':unittest.main()
