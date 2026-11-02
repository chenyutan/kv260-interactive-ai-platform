"""Offline project knowledge, owned clear and in-flight cancellation checks."""
import copy
import json
import threading
import time
import unittest
from pathlib import Path
from ai_assistant.contracts import AIError,guide_result
from ai_assistant.projects import PROJECT_IDS,project_catalog,project_help,guide_evidence
from ai_assistant.service import AIService
from test_ai_assistant import FakeStore,tempdir,wait_job


class ProjectClient:
    configured=True
    def __init__(self,blocked=False,error=None):
        self.calls=[];self.entered=threading.Event();self.release=threading.Event();self.error=error
        if not blocked:self.release.set()
    def complete(self,messages):
        self.calls.append(copy.deepcopy(messages));self.entered.set()
        if not self.release.wait(4):raise TimeoutError('fixture expired')
        if self.error:raise self.error
        payload=json.loads(messages[-1]['content']);evidence=payload['evidence']
        if payload['kind']=='creation':
            plan=evidence['plans'][0]
            return dict(title=plan['title'],summary=plan['description'],plan_id=plan['id'])
        return dict(fact_ids=[evidence['facts'][0]['id']],tip_ids=[evidence['tips'][0]['id']])


def await_idle(service,seconds=3):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if service._queue.unfinished_tasks==0:return
        time.sleep(.01)
    raise AssertionError('Worker did not finish')


class KnowledgeTests(unittest.TestCase):
    def test_exact_implemented_project_coverage_and_offline_help(self):
        from section_catalog import CONTENT,TITLES
        rows=project_catalog();expected=sum((CONTENT[s] for s in ('entertainment','health','creation')),())
        self.assertEqual(set(PROJECT_IDS),set(expected)|{'system'});self.assertEqual(len(rows),13)
        for row in rows:
            with self.subTest(project=row['id']):
                if row['id']=='system':
                    self.assertEqual((row['title'],row['section']),('项目介绍','system'))
                else:
                    self.assertEqual(row['title'],TITLES[row['id']])
                    self.assertIn(row['id'],CONTENT[row['section']])
                help=row['help'];self.assertEqual(set(help),{'title','summary','tips'})
                self.assertIn('本地说明',help['title']);self.assertTrue(help['summary']);self.assertTrue(help['tips'])
        rows[0]['help']['tips'][0]='mutated'
        self.assertNotEqual(project_catalog()[0]['help']['tips'][0],'mutated')

    def test_system_overview_separates_official_model_local_logic_and_cloud_roles(self):
        text=json.dumps(project_help('system'),ensure_ascii=False)
        for phrase in ('技术原理','应用场景','FPGA PL','PS','官方 Hourglass','DPU','ARM','DeepSeek','不是由 DeepSeek 识别关节','不提供医疗诊断'):
            self.assertIn(phrase,text)
        for preset,label in (('rules','项目概览'),('controls','技术原理'),('tips','应用场景')):
            evidence=guide_evidence('system',preset)
            self.assertEqual(evidence['focus_description'],label)
            selected=next(row for row in evidence['facts'] if row['category']==preset)
            result=guide_result(dict(fact_ids=[selected['id']],tip_ids=['system.tip.1']),evidence)
            self.assertEqual(result['title'],'项目介绍 · '+label)
            self.assertIn(selected['text'],result['summary'])

    def test_controls_and_rules_use_actual_entry_cards(self):
        from entry_help import BODY,RULES
        from experience_names import GAMEPAD_HELP,COMMON_GAMEPAD_HELP
        for project in ('sword','fight','fruit','taiko','platformer','runner','pal','kart','ink'):
            text=json.dumps(guide_evidence(project),ensure_ascii=False)
            for value in (*BODY[project],*RULES[project],*GAMEPAD_HELP[project],*COMMON_GAMEPAD_HELP):
                self.assertIn(value,text)

    def test_health_uses_current_course_and_all_twenty_four_taichi_lessons(self):
        from health.course_registry import get_course
        for project in ('baduanjin','taichi'):
            course=get_course(project);evidence=guide_evidence(project)
            lessons=[r for r in evidence['facts'] if r['category']=='lesson']
            self.assertEqual(len(lessons),len(course['lessons']))
            for lesson in course['lessons']:
                row=next(r for r in lessons if r['id']==project+'.lesson.'+lesson['id'])
                self.assertIn(lesson['name'],row['text']);self.assertIn(lesson['assessment_note'],row['text'])
            text=json.dumps(evidence,ensure_ascii=False)
            self.assertIn('不能诊断',text);self.assertIn('不与自己的动作逐帧同步',text)
        self.assertEqual(len(get_course('taichi')['lessons']),24)

    def test_local_help_works_without_any_configuration_or_visitor(self):
        for project in PROJECT_IDS:
            self.assertGreater(len(project_help(project)['summary']),20)
        for invalid in ('unknown','koi','../../file',None,{},True):
            with self.subTest(invalid=invalid),self.assertRaises(AIError):project_help(invalid)

    def test_model_cannot_fabricate_controls_or_use_another_project(self):
        evidence=guide_evidence('fruit')
        valid=dict(fact_ids=[evidence['facts'][0]['id']],tip_ids=[evidence['tips'][0]['id']])
        result=guide_result(valid,evidence)
        self.assertEqual(set(result),{'title','summary','tips'})
        self.assertIn(evidence['facts'][0]['text'],result['summary'])
        for bad in (dict(valid,text='arbitrary'),dict(valid,fact_ids=['fight.rules.1']),
                    dict(valid,tip_ids=['fruit.tip.999']),dict(valid,fact_ids=[]),
                    dict(valid,fact_ids=valid['fact_ids']*2),dict(valid,tip_ids=valid['tip_ids']*2),
                    dict(valid,tip_ids=[{}]),dict(valid,fact_ids=[None]),
                    dict(valid,fact_ids=[r['id'] for r in evidence['facts'][:6]])):
            with self.subTest(bad=bad),self.assertRaises(AIError):guide_result(bad,evidence)

    def test_scope_is_limited_and_contains_no_user_or_runtime_information(self):
        for project in PROJECT_IDS:
            evidence=guide_evidence(project,'controls')
            self.assertEqual(evidence['project'],project)
            self.assertEqual(evidence['focus'],'controls')
            for row in evidence['facts']+evidence['tips']:self.assertTrue(row['id'].startswith(project+'.'))
            self.assertNotIn('token',json.dumps(evidence));self.assertNotIn('api_key',json.dumps(evidence))
        with self.assertRaises(AIError):guide_evidence('fruit','arbitrary')


class ProjectServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempdir();self.store=FakeStore();self.services=[]
    def tearDown(self):
        for service in self.services:
            service.client.release.set();service.close();service._worker.join(2)
        self.tmp.cleanup()
    def make(self,client=None):
        s=AIService(self.store,Path(self.tmp.name)/str(len(self.services)),client=client or ProjectClient())
        self.services.append(s);return s

    def test_all_projects_complete_and_public_contract_does_not_include_payload(self):
        s=self.make()
        for project in PROJECT_IDS:
            job=s.submit('token0','guide','rules',project=project)
            result=wait_job(s,'token0',job['id'])
            self.assertEqual(result['status'],'ready');self.assertEqual(result['project'],project)
            self.assertEqual(set(result['result']),{'title','summary','tips'})
            self.assertNotIn('payload',result);self.assertNotIn('session_id',result)
        caps=s.capabilities();self.assertEqual(len(caps['projects']),13)
        self.assertEqual([x['id'] for x in caps['presets']['guide']],['rules','controls','tips'])
        caps['projects'][0]['help']['summary']='mutated'
        self.assertNotEqual(s.capabilities()['projects'][0]['help']['summary'],'mutated')
        self.assertEqual((caps['model'],caps['thinking']),('deepseek-flash','disabled'))

    def test_guide_defaults_validation_and_cache_are_project_specific(self):
        s=self.make();a=s.submit('token0','guide',project='fruit');wait_job(s,'token0',a['id'])
        self.assertEqual(a['preset'],'rules')
        self.assertEqual(s.submit('token0','guide',project='fruit')['id'],a['id'])
        b=s.submit('token0','guide',project='fight');wait_job(s,'token0',b['id'])
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(len(s.client.calls),2)
        for kind,project,preset in [('guide','','rules'),('guide','unknown','rules'),('guide','fruit','landscape'),('review','fruit','overview')]:
            with self.subTest(args=(kind,project,preset)),self.assertRaises(AIError):
                s.submit('token0',kind,preset,project=project)
        self.assertEqual(len(s.client.calls),2)

    def test_free_question_is_bounded_and_uses_static_evidence_only(self):
        s=self.make();job=s.submit('token0','guide',text='左手和右手怎么操作？',project='taiko')
        self.assertEqual(wait_job(s,'token0',job['id'])['status'],'ready')
        sent=json.dumps(s.client.calls,ensure_ascii=False)
        self.assertIn('左手和右手怎么操作',sent)
        for private in ('DO-NOT-UPLOAD','token0','session0'):self.assertNotIn(private,sent)
        with self.assertRaises(AIError):s.submit('token0','guide',text='字'*281,project='taiko')

    def test_clear_is_owned_idempotent_and_preserves_scores_and_other_visitors(self):
        s=self.make()
        a=s.submit('token0','guide',project='fruit');wait_job(s,'token0',a['id'])
        b=s.submit('token1','guide',project='fight');wait_job(s,'token1',b['id'])
        before=copy.deepcopy(self.store.view('token0'));self.assertEqual(s.clear('token0'),dict(cleared=1,jobs=[]))
        self.assertEqual(s.clear('token0'),dict(cleared=0,jobs=[]))
        self.assertEqual(s.list_jobs('token0'),[]);self.assertEqual(s.get('token1',b['id'])['status'],'ready')
        self.assertEqual(self.store.view('token0'),before)
        with self.assertRaises(KeyError):s.get('token0',a['id'])
        with self.assertRaises(KeyError):s.clear('not-a-token')
        self.store.sessions['token1']['status']='archived'
        with self.assertRaises(AIError) as e:s.clear('token1')
        self.assertEqual(e.exception.status,409);self.assertEqual(len(s.list_jobs('token1')),1)

    def test_clear_running_late_success_cannot_reappear_and_new_request_survives(self):
        client=ProjectClient(blocked=True);s=self.make(client)
        old=s.submit('token0','guide',project='fruit');self.assertTrue(client.entered.wait(1))
        self.assertEqual(s.clear('token0')['cleared'],1)
        new=s.submit('token0','guide',project='fruit');self.assertNotEqual(old['id'],new['id'])
        client.release.set();self.assertEqual(wait_job(s,'token0',new['id'])['status'],'ready');await_idle(s)
        self.assertEqual([r['id'] for r in s.list_jobs('token0')],[new['id']])
        self.assertEqual(len(client.calls),2);self.assertEqual(s._queue.unfinished_tasks,0)

    def test_clear_queued_frees_capacity_and_never_calls_model_for_deleted_job(self):
        client=ProjectClient(blocked=True);s=self.make(client)
        a=s.submit('token0','guide',project='fruit');self.assertTrue(client.entered.wait(1))
        old=s.submit('token1','guide',project='fight');other=s.submit('token2','guide',project='kart')
        self.assertEqual(s._queue.qsize(),2);s.clear('token1');self.assertEqual(s._queue.qsize(),1)
        new=s.submit('token1','guide',project='pal');self.assertEqual(s._queue.qsize(),2)
        client.release.set();self.assertEqual(wait_job(s,'token1',new['id'])['status'],'ready');await_idle(s)
        with self.assertRaises(KeyError):s.get('token1',old['id'])
        self.assertEqual([json.loads(c[-1]['content'])['project'] for c in client.calls],['fruit','kart','pal'])
        self.assertEqual(s.get('token0',a['id'])['status'],'ready');self.assertEqual(s.get('token2',other['id'])['status'],'ready')

    def test_clear_inflight_error_does_not_reappear_or_retry(self):
        client=ProjectClient(blocked=True,error=RuntimeError('SECRET'));s=self.make(client)
        s.submit('token0','guide',project='taiko');self.assertTrue(client.entered.wait(1))
        s.clear('token0');client.release.set();await_idle(s)
        self.assertEqual(s.list_jobs('token0'),[]);self.assertEqual(len(client.calls),1)

    def test_offline_failure_finishes_once_and_local_help_stays_available(self):
        s=self.make(ProjectClient(error=ConnectionError('PRIVATE')))
        job=s.submit('token0','guide',project='photo_ink');result=wait_job(s,'token0',job['id'])
        self.assertEqual(result['status'],'error');self.assertNotIn('PRIVATE',json.dumps(result))
        s.list_jobs('token0');s.capabilities();self.assertEqual(len(s.client.calls),1)
        self.assertTrue(project_help('photo_ink')['summary'])
        s.client.configured=False;s.clear('token0')
        with self.assertRaises(AIError):s.submit('token0','guide',project='photo_ink')
        self.assertEqual(len(s.capabilities()['projects']),13);self.assertEqual(s.list_jobs('token0'),[])

    def test_legacy_rows_read_without_migration_or_project(self):
        s=self.make()
        with s._db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',('f'*32,'session0','creation','bamboo',
                '2026-01-01','ready','sig','{}','{"title":"旧回答","summary":"保留"}',''))
        row=s.get('token0','f'*32);self.assertEqual(row['project'],'');self.assertEqual(row['result']['title'],'旧回答')


if __name__=='__main__':unittest.main()
