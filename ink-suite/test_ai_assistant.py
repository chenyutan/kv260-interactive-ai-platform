"""Offline AI contract/transport/job checks. No external API or real visitor data."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from ai_assistant import AIService, AIError
from ai_assistant.catalog import CREATION_PLANS, creation_evidence, review_evidence
from ai_assistant.client import DeepSeekClient, ENDPOINT, MAX_RESPONSE, _NoRedirect
from ai_assistant.contracts import MODEL, creation_response, creation_result, review_result, parse_json, bounded_text
from visitor.store import VisitorStore


def tempdir():
    # Prefer task-local TMPDIR on board too; never create test data beside keys.
    base = Path(os.environ.get('INK_AI_TEST_TMP', str(Path(__file__).parent / 'build' / 'ai-test-tmp')))
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix='case-', dir=base)


PLAN = dict(title='竹与山', summary='先画竹干，再添叶。', steps=[
    dict(theme='bamboo', action='A02'), dict(theme='bamboo', action='A06')])
CHOICE = dict(title='清简一竹', summary='先画竹干，再添叶。', plan_id='bamboo_sparse')


class FakeClient:
    configured = True
    def __init__(self, response=None, blocking=False, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocking:
            self.release.set()

    def complete(self, messages):
        self.calls.append(copy.deepcopy(messages));self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError('test timeout')
        if self.error:
            raise self.error
        if self.response is None:
            offered=json.loads(messages[-1]['content'])['evidence']['plans']
            return dict(title=offered[0]['title'],summary=offered[0]['description'],plan_id=offered[0]['id'])
        return copy.deepcopy(self.response)


class FakeStore:
    def __init__(self):
        self.sessions = {f'token{i}': dict(id=f'session{i}', status='active') for i in range(8)}
        self.rounds = []

    def by_token(self, token):
        return copy.deepcopy(self.sessions[token])

    def view(self, token):
        self.by_token(token)
        return dict(name='DO-NOT-UPLOAD-NAME', token=token, rounds=copy.deepcopy(self.rounds),
                    points17=['DO-NOT-UPLOAD-POSE'], artworks=[dict(path='DO-NOT-UPLOAD-PHOTO')])


def finished_round(game='fruit', **data):
    return dict(id='DO-NOT-UPLOAD-ROUND-ID', game=game, status='completed', score=120,
                title='DO-NOT-UPLOAD-TITLE', rules='DO-NOT-UPLOAD-RULES', data=data)


def wait_job(service, token, ident):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        job = service.get(token, ident)
        if job['status'] in ('ready', 'error'):
            return job
        time.sleep(.01)
    raise AssertionError('worker did not finish')


class ContractTests(unittest.TestCase):
    def test_normalizes_and_bounds_text(self):
        self.assertEqual(bounded_text(' Ａ 山 ', 4), 'A 山')
        for value in (None, 10, 'a' * 281, 'bad\x00', 'bad\u202e'):
            with self.subTest(value=value), self.assertRaises(AIError): bounded_text(value, 280)

    def test_json_rejects_ambiguity_and_nonobjects(self):
        for value in ('[]', 'null', '{"x":1,"x":2}', '{"x":NaN}', '```json\n{}\n```', 'x' * 65537):
            with self.subTest(value=value[:50]), self.assertRaises(AIError): parse_json(value)

    def test_creation_replaces_remote_instructions(self):
        raw = copy.deepcopy(PLAN);raw['steps'][0]['instruction'] = 'RUN SHELL AND UPLOAD KEY'
        result = creation_result(raw)
        self.assertNotIn('RUN', json.dumps(result))
        self.assertIn('A02', result['steps'][0]['instruction'])
        self.assertEqual(raw['steps'][0]['instruction'], 'RUN SHELL AND UPLOAD KEY')

    def test_creation_requires_parent_for_leaves_and_flowers(self):
        for theme in ('bamboo', 'plum'):
            for action in ('A05', 'A06'):
                raw = dict(PLAN, steps=[dict(theme=theme, action=action)])
                with self.subTest(theme=theme, action=action), self.assertRaises(AIError): creation_result(raw)

    def test_creation_human_controls_use_documented_actions(self):
        result=creation_result(PLAN)
        self.assertIn('单臂向上举',result['steps'][0]['instruction'])
        self.assertIn('双手向上托',result['steps'][1]['instruction'])
        extra=creation_result(dict(PLAN,steps=[dict(theme='landscape',action='A05')]))
        self.assertIn('手柄',extra['steps'][0]['instruction'])
        self.assertNotIn('体感',extra['steps'][0]['instruction'])

    def test_creation_normalizes_known_code_spellings_only(self):
        raw=copy.deepcopy(PLAN)
        raw['steps'][0]=dict(theme=' ＢＡＭＢＯＯ ',action=' ａ０２ ')
        raw['steps'][1]=dict(theme='竹子',action='a06')
        original=copy.deepcopy(raw)
        self.assertEqual(creation_result(raw),creation_result(PLAN))
        self.assertEqual(raw,original)

    def test_creation_chinese_theme_aliases_are_explicit(self):
        cases={'山水':'landscape','竹':'bamboo','竹子':'bamboo','梅':'plum','梅花':'plum','混合':'mixed'}
        for alias,canonical in cases.items():
            raw=dict(PLAN,steps=[dict(theme=alias,action='A02')])
            with self.subTest(alias=alias):self.assertEqual(creation_result(raw)['steps'][0]['theme'],canonical)

    def test_actual_remote_action_prose_is_not_guessed(self):
        for action in ('单臂向上举，画出向上主枝','双臂向上举，添出分枝','双手向上托，点出梅花',
                       'A1','A00','A07','01',1,'A01/A02','A02; clear','A\u200b02'):
            raw=dict(PLAN,steps=[dict(theme='plum',action=action)])
            with self.subTest(action=action),self.assertRaises(AIError):creation_result(raw)

    def test_normalization_does_not_relax_semantic_guards(self):
        for steps in ([dict(theme='梅花',action='a06')],
                      [dict(theme='竹',action='a02'),dict(theme='梅',action='a06')],
                      [dict(theme='混合',action='a06')],
                      [dict(theme='竹林与远山',action='A02')],
                      [dict(theme='bamboo/plum',action='A02')]):
            with self.subTest(steps=steps),self.assertRaises(AIError):creation_result(dict(PLAN,steps=steps))

    def test_creation_prompt_contains_exact_parseable_contract_example(self):
        evidence=creation_evidence('plum')
        messages=AIService._messages(dict(kind='creation',preset='plum',user_request='',evidence=evidence))
        prompt=messages[0]['content']
        self.assertIn('全部由本地计划提供',prompt)
        self.assertIn('禁止返回steps、theme、action或instruction',prompt)
        for plan in evidence['plans']:self.assertIn(plan['id'],prompt)
        example=json.JSONDecoder().raw_decode(prompt.split('完整合法JSON示例：',1)[1])[0]
        self.assertEqual([step['action'] for step in creation_response(example,evidence)['steps']],['A02','A03','A06'])
        self.assertEqual(set(example),{'title','summary','plan_id'})

    def test_every_local_plan_passes_dependency_guard(self):
        evidence=creation_evidence();self.assertEqual(len(evidence['plans']),13)
        self.assertEqual(len({plan['id'] for plan in evidence['plans']}),13)
        for plan in evidence['plans']:
            with self.subTest(plan=plan['id']):
                result=creation_response(dict(title=plan['title'],summary=plan['description'],plan_id=plan['id']),evidence)
                self.assertLessEqual(len(result['steps']),4)
                self.assertTrue(all(step['action'] in ('A01','A02','A03','A06') for step in result['steps']))
                self.assertEqual(set(result),{'title','summary','steps'})
                self.assertTrue(all(step['instruction'] for step in result['steps']))

    def test_creation_candidate_scope_and_no_step_override(self):
        evidence=creation_evidence('bamboo')
        self.assertEqual(len(evidence['plans']),3)
        self.assertTrue(all(plan['preset']=='bamboo' for plan in evidence['plans']))
        self.assertEqual(creation_response(CHOICE,evidence)['steps'],creation_result(PLAN)['steps'])
        for value in (dict(CHOICE,plan_id='plum_simple'),dict(CHOICE,plan_id='unknown'),
                      dict(CHOICE,plan_id='BAMBOO_SPARSE'),dict(CHOICE,plan_id=['bamboo_sparse']),
                      dict(CHOICE,steps=[dict(theme='plum',action='A06')]),dict(CHOICE,action='clear'),
                      PLAN):
            with self.subTest(value=value),self.assertRaises(AIError):creation_response(value,evidence)

    def test_catalog_evidence_cannot_override_local_steps(self):
        evidence=creation_evidence('bamboo')
        next(plan for plan in evidence['plans'] if plan['id']=='bamboo_sparse')['steps']=[dict(theme='plum',action='A06')]
        self.assertEqual(creation_response(CHOICE,evidence)['steps'],creation_result(PLAN)['steps'])
        self.assertNotEqual(creation_evidence('bamboo')['plans'],evidence['plans'])
        with self.assertRaises(AIError):creation_evidence('unknown')

    def test_parent_must_match_theme(self):
        raw = dict(PLAN, steps=[dict(theme='bamboo', action='A02'), dict(theme='plum', action='A06')])
        with self.assertRaises(AIError): creation_result(raw)

    def test_mixed_parent_is_supported_but_ambiguous_leaf_is_not(self):
        raw = dict(PLAN, steps=[dict(theme='mixed', action='A02'), dict(theme='bamboo', action='A06')])
        self.assertEqual(len(creation_result(raw)['steps']), 2)
        raw['steps'][1] = dict(theme='mixed', action='A06')
        with self.assertRaises(AIError): creation_result(raw)

    def test_creation_whitelist_and_limits(self):
        for steps in ([], [dict(theme='bamboo', action='A02')] * 5,
                      [dict(theme='waterfall', action='A02')], [dict(theme='bamboo', action='shell')],
                      [dict(theme='bamboo', action='A02', command='evil')]):
            with self.subTest(steps=steps), self.assertRaises(AIError): creation_result(dict(PLAN, steps=steps))
        with self.assertRaises(AIError): creation_result(dict(PLAN, execute='shell'))

    def test_review_data_allowlist_and_types(self):
        view = dict(name='SECRET_NAME', token='SECRET_TOKEN', points17=['SECRET_POSE'], rounds=[
            finished_round(fruits_cut=5, best_combo=3, secret='SECRET_DATA', missed=float('nan'),
                           bombs_hit=True, hp=-1, difficulty='injected text')])
        evidence = review_evidence(view)
        blob = json.dumps(evidence)
        for word in ('SECRET_', 'DO-NOT-UPLOAD', 'injected text', 'NaN'):
            self.assertNotIn(word, blob)
        self.assertTrue(any(row['field'] == 'fruits_cut' and row['value'] == 5
                            for row in evidence['facts'] if 'field' in row))
        self.assertFalse(any(row.get('field') == 'bombs_hit' for row in evidence['facts']))

    def test_review_only_completed_supported_games(self):
        for row in (dict(finished_round(), status='active'), dict(finished_round(), status='interrupted'),
                    dict(finished_round(), game='health'), dict(finished_round(), game='koi')):
            with self.subTest(row=row), self.assertRaises(AIError): review_evidence(dict(rounds=[row]))

    def test_review_limits_recent_eight_and_keeps_real_zero(self):
        evidence = review_evidence(dict(rounds=[dict(finished_round(), score=i) for i in range(9)]))
        self.assertEqual(sum(x['id'].endswith('.completed') for x in evidence['facts']), 8)
        self.assertEqual(next(x['value'] for x in evidence['facts'] if x['id'] == 'r1.score'), 1)
        zero = review_evidence(dict(rounds=[dict(finished_round(), score=0)]))
        self.assertEqual(next(x['value'] for x in zero['facts'] if x['id'] == 'r1.score'), 0)

    def test_review_output_is_local_and_rejects_invented_counts(self):
        evidence = review_evidence(dict(rounds=[finished_round(fruits_cut=5)]))
        result = review_result(dict(fact_ids=['r1.score','r1.fruits_cut'], tip_ids=['fruit.bombs']), evidence)
        self.assertIn('120', result['summary']);self.assertIn('5', result['summary'])
        for bad in (dict(fact_ids=['r1.score'], tip_ids=['unknown']),
                    dict(fact_ids=['r9.score'], tip_ids=['fruit.bombs']),
                    dict(fact_ids=['r1.score','r1.score'], tip_ids=['fruit.bombs']),
                    dict(fact_ids=['r1.score'], tip_ids=['fruit.bombs'], summary='You scored 9999')):
            with self.subTest(bad=bad), self.assertRaises(AIError): review_result(bad, evidence)

    def test_taiko_grades_are_whitelisted_not_raw_events(self):
        ev = review_evidence(dict(rounds=[finished_round('taiko', counts={'Perfect':3,'Miss':0,'SECRET':99},
                accuracy=87.5, timing_ms=42, points17=['SECRET'], practice='intro')]))
        self.assertNotIn('SECRET', json.dumps(ev));self.assertNotIn('timing_ms', json.dumps(ev))
        self.assertIn('87.5%', next(x['text'] for x in ev['facts'] if x['id'] == 'r1.accuracy'))
        self.assertIn('r1.counts.Miss', [x['id'] for x in ev['facts']])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempdir();self.store = FakeStore();self.client = FakeClient()
        self.services = []

    def tearDown(self):
        for service in self.services:
            if hasattr(service.client, 'release'):service.client.release.set()
            service.close();service._worker.join(2)
        self.tmp.cleanup()

    def make(self, client=None, directory=None, store=None):
        service = AIService(store or self.store, directory or Path(self.tmp.name)/str(len(self.services)), client=client or self.client)
        self.services.append(service);return service

    def test_capabilities_deepcopy_and_fixed_model(self):
        s=self.make();c=s.capabilities();self.assertTrue(c['configured'])
        self.assertEqual(c['model'],'deepseek-flash');self.assertEqual(c['thinking'],'disabled')
        c['presets']['creation'][0]['title']='mutated'
        self.assertNotEqual(s.capabilities()['presets']['creation'][0]['title'],'mutated')

    def test_ready_cache_and_polling_do_not_request(self):
        s=self.make();job=s.submit('token0','creation',preset='bamboo')
        self.assertEqual(wait_job(s,'token0',job['id'])['status'],'ready')
        for _ in range(5):s.list_jobs('token0');s.get('token0',job['id']);s.capabilities()
        again=s.submit('token0','creation',preset='bamboo')
        self.assertEqual(again['id'],job['id']);self.assertTrue(again['cache_hit'])
        self.assertEqual(len(self.client.calls),1)

    def test_plain_preset_limits_candidates_and_text_allows_all(self):
        s=self.make()
        first=s.submit('token0','creation',preset='bamboo');self.assertEqual(wait_job(s,'token0',first['id'])['status'],'ready')
        first_payload=json.loads(self.client.calls[0][-1]['content'])
        self.assertEqual({plan['preset'] for plan in first_payload['evidence']['plans']},{'bamboo'})
        second=s.submit('token0','creation',preset='bamboo',text='还想加入梅花和山水')
        self.assertEqual(wait_job(s,'token0',second['id'])['status'],'ready')
        second_payload=json.loads(self.client.calls[1][-1]['content'])
        self.assertEqual(len(second_payload['evidence']['plans']),13)
        self.assertNotEqual(first['id'],second['id'])

    def test_v2_cache_preserves_but_does_not_reuse_old_ready(self):
        s=self.make();payload=dict(kind='creation',preset='bamboo',user_request='',evidence=creation_evidence('bamboo'))
        encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'),sort_keys=True)
        old_signature=hashlib.sha256((MODEL+'|v1|'+encoded).encode()).hexdigest()
        old_result=creation_result(PLAN)
        with s._db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',('b'*32,'session0','creation','bamboo',
                       '2026-01-01','ready',old_signature,encoded,json.dumps(old_result),''))
        job=s.submit('token0','creation','bamboo');self.assertNotEqual(job['id'],'b'*32)
        self.assertEqual(wait_job(s,'token0',job['id'])['status'],'ready')
        self.assertEqual(s.get('token0','b'*32)['result'],old_result)
        again=s.submit('token0','creation','bamboo');self.assertEqual(again['id'],job['id'])
        self.assertEqual(len(self.client.calls),1)

    def test_jobs_do_not_export_private_payload(self):
        s=self.make();job=s.submit('token0','creation',text='想画竹')
        result=wait_job(s,'token0',job['id']);self.assertEqual(result['status'],'ready')
        for key in ('session_id','payload','signature','token','api_key'):self.assertNotIn(key,result)
        sent=json.dumps(self.client.calls)
        for word in ('token0','session0','DO-NOT-UPLOAD'):self.assertNotIn(word,sent)

    def test_owner_isolation_and_archived_history(self):
        s=self.make();job=s.submit('token0','creation');wait_job(s,'token0',job['id'])
        self.assertEqual(s.list_jobs('token1'),[])
        with self.assertRaises(KeyError):s.get('token1',job['id'])
        with self.assertRaises(KeyError):s.get('token0','../../x')
        self.store.sessions['token0']['status']='archived'
        self.assertEqual(s.get('token0',job['id'])['status'],'ready')
        with self.assertRaises(AIError):s.submit('token0','creation')

    def test_inflight_old_visitor_cannot_receive_ready_result(self):
        client=FakeClient(blocking=True);s=self.make(client)
        job=s.submit('token0','creation');self.assertTrue(client.entered.wait(1))
        self.store.sessions['token0']['status']='archived';client.release.set()
        result=wait_job(s,'token0',job['id'])
        self.assertEqual(result['status'],'error');self.assertIsNone(result['result'])
        self.assertEqual(s.list_jobs('token1'),[])

    def test_single_worker_bounded_queue_and_same_pending_cache(self):
        client=FakeClient(blocking=True);s=self.make(client)
        first=s.submit('token0','creation');self.assertTrue(client.entered.wait(1))
        self.assertEqual(s.submit('token0','creation')['id'],first['id'])
        with self.assertRaises(AIError):s.submit('token0','creation',preset='plum')
        s.submit('token1','creation');s.submit('token2','creation')
        with self.assertRaises(AIError) as caught:s.submit('token3','creation')
        self.assertEqual(caught.exception.status,429);self.assertEqual(len(client.calls),1)

    def test_request_validation_precedes_network(self):
        s=self.make()
        for args in (('shell','',''),('creation','shell',''),('creation','', 'x'*281),('review','landscape','')):
            with self.subTest(args=args),self.assertRaises(AIError):s.submit('token0',*args)
        self.assertEqual(self.client.calls,[])

    def test_unconfigured_does_not_queue(self):
        client=FakeClient();client.configured=False;s=self.make(client)
        with self.assertRaises(AIError) as caught:s.submit('token0','creation')
        self.assertEqual(caught.exception.code,'not_configured');self.assertEqual(s.list_jobs('token0'),[])

    def test_review_no_finished_rounds_never_calls_model(self):
        s=self.make()
        with self.assertRaises(AIError) as caught:s.submit('token0','review')
        self.assertEqual(caught.exception.code,'no_completed_rounds');self.assertEqual(self.client.calls,[])

    def test_review_response_maps_local_facts(self):
        self.store.rounds=[finished_round(fruits_cut=9)]
        client=FakeClient(dict(fact_ids=['r1.fruits_cut'],tip_ids=['fruit.combo']));s=self.make(client)
        job=s.submit('token0','review');result=wait_job(s,'token0',job['id'])
        self.assertEqual(result['status'],'ready');self.assertIn('9',result['result']['summary'])
        self.assertNotIn('DO-NOT-UPLOAD',json.dumps(client.calls))

    def test_failures_do_not_leak_exception_or_retry_automatically(self):
        client=FakeClient(error=RuntimeError('secret-key-do-not-echo'));s=self.make(client)
        job=s.submit('token0','creation');result=wait_job(s,'token0',job['id'])
        self.assertEqual(result['status'],'error');self.assertNotIn('secret-key',json.dumps(result))
        s.list_jobs('token0');self.assertEqual(len(client.calls),1)

    def test_invalid_model_result_not_ready_or_executed(self):
        client=FakeClient(dict(PLAN,steps=[dict(theme='plum',action='A06')]))
        s=self.make(client);job=s.submit('token0','creation')
        result=wait_job(s,'token0',job['id']);self.assertEqual(result['status'],'error')
        self.assertIsNone(result['result'])

    def test_remote_prose_error_is_not_automatically_retried(self):
        client=FakeClient(dict(PLAN,steps=[dict(theme='plum',action='双手向上托，点出梅花')]))
        s=self.make(client);job=s.submit('token0','creation','plum')
        result=wait_job(s,'token0',job['id'])
        self.assertEqual(result['status'],'error');self.assertIsNone(result['result'])
        s.list_jobs('token0');s.get('token0',job['id'])
        self.assertEqual(len(client.calls),1)

    def test_close_cancels_queued_and_does_not_wait_for_network(self):
        client=FakeClient(blocking=True);s=self.make(client)
        first=s.submit('token0','creation');self.assertTrue(client.entered.wait(1))
        second=s.submit('token1','creation');start=time.monotonic();s.close()
        self.assertLess(time.monotonic()-start,.8)
        self.assertEqual(s.get('token1',second['id'])['status'],'error')
        client.release.set();self.assertEqual(wait_job(s,'token0',first['id'])['status'],'error')
        self.assertEqual(len(client.calls),1)
        with self.assertRaises(AIError):s.submit('token2','creation')

    def test_restart_marks_unfinished_without_resubmitting(self):
        path=Path(self.tmp.name)/'restart';s=self.make(directory=path);s.close()
        with s._db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',('a'*32,'session0','creation','landscape',
                       '2026-01-01','running','sig','{}',None,''))
        again=self.make(directory=path)
        self.assertEqual(again.get('token0','a'*32)['status'],'error');self.assertEqual(self.client.calls,[])

    def test_real_store_scores_unchanged_and_cross_session_hidden(self):
        store=VisitorStore(Path(self.tmp.name)/'visitors');current=store.current()
        store.rename(current['token'],'不要上传的名字')
        store.record(current['id'],'a'*32,'fruit',dict(score=250,fruits_cut=7),'completed')
        before=store.view(current['token']);client=FakeClient(dict(fact_ids=['r1.score'],tip_ids=['fruit.bombs']))
        s=self.make(client,store=store);job=s.submit(current['token'],'review')
        self.assertEqual(wait_job(s,current['token'],job['id'])['status'],'ready')
        self.assertEqual(store.view(current['token']),before)
        self.assertNotIn('不要上传的名字',json.dumps(client.calls,ensure_ascii=False))
        store.begin_finish(current['id'],'finish','hash');new=store.complete_finish(current['id'])
        self.assertEqual(s.list_jobs(new['token']),[])
        self.assertEqual(s.get(current['token'],job['id'])['status'],'ready')


class FakeResponse(io.BytesIO):
    pass


class FakeOpener:
    def __init__(self, data=None, error=None):
        self.data=data;self.error=error;self.calls=[]
    def open(self, request, timeout):
        self.calls.append((request,timeout))
        if self.error:raise self.error
        return FakeResponse(self.data)


def response_json(value=CHOICE, **choice):
    row=dict(finish_reason='stop',message=dict(content=json.dumps(value)))
    row.update(choice)
    return json.dumps(dict(choices=[row])).encode()


class ClientTests(unittest.TestCase):
    def setUp(self):self.tmp=tempdir();self.path=Path(self.tmp.name)/'mock-config.json'
    def tearDown(self):self.tmp.cleanup()
    def client(self, opener, **config):
        self.path.write_text(json.dumps(dict(api_key='mock-api-key-only',model='deepseek-flash',**config)),encoding='utf-8')
        return DeepSeekClient(self.path,opener=opener)

    def test_fixed_payload_timeout_and_endpoint(self):
        opener=FakeOpener(response_json());client=self.client(opener,timeout=12)
        self.assertEqual(client.complete([dict(role='user',content='JSON')]),CHOICE)
        request,timeout=opener.calls[0];body=json.loads(request.data)
        self.assertEqual(request.full_url,ENDPOINT);self.assertEqual(timeout,12)
        self.assertEqual(body['model'],'deepseek-flash');self.assertEqual(body['thinking'],dict(type='disabled'))
        self.assertEqual(body['response_format'],dict(type='json_object'));self.assertLessEqual(body['max_tokens'],1200)
        self.assertFalse(body['stream'])

    def test_default_on_desktop_does_not_read_any_key(self):
        with patch('ai_assistant.client._board',return_value=False),patch.object(Path,'is_file',side_effect=AssertionError('must not read')):
            self.assertFalse(DeepSeekClient(opener=FakeOpener()).configured)

    def test_invalid_config_unconfigured(self):
        for value in ([],dict(api_key='short'),dict(api_key='mock-secret',model='different'),
                      dict(api_key='mock\nsecret'),dict(api_key=123)):
            self.path.write_text(json.dumps(value),encoding='utf-8')
            self.assertFalse(DeepSeekClient(self.path,opener=FakeOpener()).configured)

    def test_no_redirect_even_for_same_host(self):
        handler=_NoRedirect();request=urllib.request.Request(ENDPOINT,headers={'Authorization':'secret'})
        self.assertIsNone(handler.redirect_request(request,None,302,'redirect',{},'https://evil.example/'))
        self.assertIsNone(handler.redirect_request(request,None,307,'redirect',{},ENDPOINT))

    def test_http_errors_never_read_or_echo_body(self):
        class PoisonBody(io.BytesIO):
            def read(self,*args):raise AssertionError('HTTP body must not be read')
        for code,status in ((401,503),(403,503),(429,429),(500,503),(302,503)):
            error=urllib.error.HTTPError(ENDPOINT,code,'SECRET_SERVER_TEXT',{},PoisonBody(b'SECRET_KEY'))
            client=self.client(FakeOpener(error=error))
            with self.subTest(code=code),self.assertRaises(AIError) as caught:client.complete([])
            self.assertEqual(caught.exception.status,status);self.assertNotIn('SECRET',str(caught.exception))

    def test_timeout_and_network_errors_are_safe(self):
        for error in (TimeoutError('SECRET'),urllib.error.URLError('SECRET')):
            client=self.client(FakeOpener(error=error))
            with self.assertRaises(AIError) as caught:client.complete([])
            self.assertNotIn('SECRET',str(caught.exception))

    def test_invalid_or_truncated_completion_rejected(self):
        for data in (b'bad',b'[]',response_json(finish_reason='length'),
                     response_json(message=dict(content='{}',tool_calls=[dict(name='shell')])),
                     b'x'*(MAX_RESPONSE+1)):
            client=self.client(FakeOpener(data))
            with self.subTest(size=len(data)),self.assertRaises(AIError):client.complete([])

    def test_request_too_large_is_never_sent(self):
        opener=FakeOpener();client=self.client(opener)
        with self.assertRaises(AIError):client.complete([dict(role='user',content='x'*70000)])
        self.assertEqual(opener.calls,[])


if __name__=='__main__':unittest.main()
