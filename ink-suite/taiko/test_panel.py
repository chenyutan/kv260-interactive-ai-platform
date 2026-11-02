"""Exercise real panel/host action methods without GTK, audio output or a camera."""
from collections import Counter,deque
import io
import json
import math
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch

from action_catalog import page, actions, CONFIRM
from action_dispatch import SUPPORTED_ACTIONS
from test_action_dispatch import load_class, ROOT, req
import test_action_host as host_tests
from taiko.audio_clock import SilentClock
from taiko.session import Session


class PanelActions(unittest.TestCase):
    def panel(self):
        cls, ns = load_class(ROOT/'taiko/main.py', 'TaikoPanel')
        p = cls.__new__(cls)
        p.active = False; p.stop = threading.Event()
        p.audio = SilentClock(); p.session = Session(p.audio)
        p.canvas = NS(queue_draw=Mock()); p.clear_input = Mock()
        p.synthetic_seen = set(); p.args = NS(input_offset_ms=0, audio_sink='fakesink')
        p.timing_label = NS(set_text=Mock())
        p.difficulty = NS(set_active_id=lambda v:p.change_difficulty(NS(get_active_id=lambda:v)))
        p.practice = NS(set_active_id=lambda v:p.change_practice(NS(get_active_id=lambda:v)))
        return p, ns

    def test_segment_change_seeks_with_preroll_once_and_stays_frozen(self):
        p, _ = self.panel()
        p.session.restart = Mock(wraps=p.session.restart)
        done = Mock(); p.execute_action('practice_alternate', done)
        self.assertTrue(done.call_args.args[0])
        p.session.restart.assert_called_once()
        self.assertEqual(p.session.practice, 'alternate')
        self.assertAlmostEqual(p.audio.position(), 28.8)
        self.assertGreaterEqual(min(n['time'] for n in p.session.game.notes), 31.2)
        self.assertFalse(p.audio.playing); self.assertFalse(p.session.running)
        p.execute_action('difficulty_hard', done)
        self.assertEqual(p.session.game.difficulty, 'hard')
        self.assertAlmostEqual(p.audio.position(), 28.8)
        p.execute_action('practice_full', done)
        self.assertEqual(p.session.practice, 'full')
        self.assertEqual(p.audio.position(), 0)

    def test_calibration_is_bounded_idempotent_and_does_not_seek_or_score(self):
        p, ns = self.panel()
        p.session.game.score = 1234
        p.audio.seek(20.)
        request = req('timing_earlier', 'offset-1')
        p.handle_action(request); p.handle_action(request)
        self.assertEqual(p.args.input_offset_ms, 25)
        self.assertEqual(p.session.offset, .025)
        self.assertEqual(p.session.game.score, 1234)
        self.assertEqual(p.audio.position(), 20.)
        self.assertEqual(ns['emit'].call_args.kwargs['result']['input_offset_ms'], 25)
        for _ in range(30):p.execute_action('timing_earlier', Mock())
        self.assertEqual(p.args.input_offset_ms, 500)
        for _ in range(50):p.execute_action('timing_later', Mock())
        self.assertEqual(p.args.input_offset_ms, -500)
        p.execute_action('timing_reset', Mock())
        self.assertEqual(p.args.input_offset_ms, 0)
        self.assertFalse(p.audio.playing)

    def test_playing_session_rejects_remote_settings(self):
        p, ns = self.panel();p.active = True
        p.handle_action(req('practice_finale'))
        self.assertEqual(p.session.practice, 'full')
        self.assertFalse(ns['emit'].call_args.kwargs['ok'])

    def test_bad_audio_seek_does_not_ack_success(self):
        p, _ = self.panel()
        p.audio.seek = lambda _:False
        done = Mock();p.execute_action('practice_finale', done)
        self.assertFalse(done.call_args.args[0])
        self.assertFalse(p.session.running)

    def test_profile_requires_confirmed_restart_and_failed_seek_preserves_score(self):
        p,_=self.panel();p.session.game.score=1234
        p.session.restart=Mock(wraps=p.session.restart)
        done=Mock();p.execute_action('judgment_relaxed',done)
        self.assertTrue(done.call_args.args[0]);p.session.restart.assert_called_once()
        self.assertEqual(p.session.judgment,'relaxed');self.assertEqual(p.session.game.score,0)
        self.assertIn('OK',p.session.game.counts);self.assertFalse(p.session.running)
        p.session.game.score=4321;p.audio.seek=lambda _:False
        p.execute_action('judgment_strict',done)
        self.assertFalse(done.call_args.args[0]);self.assertEqual(p.session.judgment,'relaxed')
        self.assertEqual(p.session.game.score,4321)

    def test_quick_calibration_preserves_score_and_music_position(self):
        p,ns=self.panel();p.session.game.score=3456;p.audio.seek(20.)
        request=req('timing_preset_m150','preset-1')
        p.handle_action(request);p.handle_action(request)
        self.assertEqual(p.session.offset,-.150);self.assertEqual(p.session.game.score,3456)
        self.assertEqual(p.audio.position(),20.);self.assertFalse(p.audio.playing)
        self.assertTrue(ns['emit'].call_args.kwargs['ok'])
        p.execute_action('timing_earlier',Mock());self.assertAlmostEqual(p.session.offset,-.125)
        p.execute_action('timing_preset_m100',Mock());self.assertEqual(p.session.offset,-.100)

    def test_spin_calibration_refuses_changes_during_play(self):
        p,_=self.panel();p.session.resume()
        value=[-100.]
        spin=NS(get_value=lambda:value[0],set_value=lambda v:value.__setitem__(0,v))
        p.change_timing(spin)
        self.assertEqual(p.session.offset,0.);self.assertEqual(value[0],0.)
        p.session.pause();value[0]=-100.;p.change_timing(spin)
        self.assertEqual(p.session.offset,-.100)

    def test_metrics_export_bounded_input_chain_and_hand_decisions(self):
        p,ns=self.panel();ns['percentile']=lambda values,q:0.
        p.started_at=0.;p.draw_at=[];p.observed_poses=[];p.frames=0
        p.draw_ms=[];p.compose_ms=[];p.present_ms=[];p.tick_ms=[];p.fatal_error=None
        p.args.offline_preview=False;p.args.autoplay=False
        p.renderer=NS(allocations=1,blitter=None);p.presenter=NS(backend='test',calls=0)
        p.tracking=NS(snapshot=lambda now:{})
        p.tracking_counts={};p.tracking_events=[]
        p.control=dict(hand_diagnostics={'L':{'armed':False}})
        p.canvas=NS(get_allocated_width=lambda:640,get_allocated_height=lambda:360)
        p.session.tick(10.,[dict(hand='L',timestamp=10.,id='paused')])
        snapshot=p.performance_snapshot()
        self.assertEqual(snapshot['strikes'],0);self.assertEqual(snapshot['empty_strikes'],0)
        self.assertEqual(snapshot['input_diagnostics']['counts']['paused'],1)
        self.assertEqual(snapshot['input_decisions'],p.control['hand_diagnostics'])


class GesturePages(unittest.TestCase):
    def test_new_gameplay_settings_have_reachable_six_slot_pages(self):
        h = host_tests.ActionHostTests('runTest');h.setUp();s = h.s;s.mode = 'taiko'
        for child in ('taiko_difficulty', 'taiko_practice', 'taiko_timing'):
            s.show_action_page('root')
            if child!='taiko_timing':s.choose_action('options')
            s.choose_action(child)
            self.assertEqual(s.action_page, child)
            self.assertLessEqual(len(page('taiko', child)['items']), 6)
            self.assertFalse(h.action_messages())
        for action in ('difficulty_hard','practice_full','practice_intro','practice_alternate','practice_finale'):
            self.assertIn(action, CONFIRM)
            self.assertIn(action, actions('taiko'))
            self.assertIn(action, SUPPORTED_ACTIONS['taiko'])

    def test_calibration_is_on_root_and_profile_changes_require_confirmation(self):
        self.assertIn('taiko_timing',{i['id'] for i in page('taiko')['items']})
        for action in ('timing_preset_m100','timing_preset_m150','judgment_relaxed','judgment_strict'):
            self.assertIn(action,actions('taiko'));self.assertIn(action,SUPPORTED_ACTIONS['taiko'])
        h=host_tests.ActionHostTests('runTest');h.setUp();s=h.s;s.mode='taiko'
        s.choose_action('options');s.choose_action('taiko_difficulty');s.choose_action('judgment_relaxed')
        self.assertEqual(s.action_page,'confirm:judgment_relaxed');self.assertFalse(h.action_messages())
        s.choose_action('confirm:judgment_relaxed')
        self.assertEqual(h.action_messages()[0]['action'],'judgment_relaxed')

    def test_practice_requires_confirmation_and_ack_before_resume(self):
        h = host_tests.ActionHostTests('runTest');h.setUp();s = h.s;s.mode = 'taiko'
        s.choose_action('options');s.choose_action('taiko_practice');s.choose_action('practice_finale')
        self.assertEqual(s.action_page, 'confirm:practice_finale')
        self.assertFalse(h.action_messages())
        s.choose_action('confirm:practice_finale')
        self.assertEqual(h.action_messages()[0]['action'], 'practice_finale')
        self.assertTrue(s.menu_open)
        pending = dict(s.pending_action);h.ack(pending)
        self.assertTrue(s.menu_open)
        h.complete_last();self.assertFalse(s.menu_open)


class PoseTimingAudit(unittest.TestCase):
    def trace(self):
        cls,_=load_class(ROOT/'taiko/main.py','PoseTiming',math=math,deque=deque,Counter=Counter)
        return cls()

    def test_scalar_projection_rejects_pixels_coordinates_and_nonfinite_values(self):
        trace=self.trace()
        state=dict(pid=4,sequence=7,timestamp_seconds=10.,pose_count=3,points17=[[1,2,3]],
                   pixels=b'pixels',roi_timing={'private':'x'},pipeline_ms=float('inf'),
                   stage_times={'snapshot_request_ms':12.,'frame_read_ms':float('nan'),'points17':[1,2]})
        identifier=trace.start(10.1,2);trace.finish(identifier,10.1,10.2,state,None,True,False)
        result=trace.snapshot(10.2);row=result['last_polls'][0]
        self.assertEqual(row['stage_times'],{'snapshot_request_ms':12.})
        self.assertIsNone(row['pipeline_ms']);self.assertAlmostEqual(row['source_age_at_receive'],.2)
        self.assertNotIn('points17',json.dumps(result));self.assertNotIn('pixels',json.dumps(result))
        state['stage_times']['snapshot_request_ms']=99.
        self.assertEqual(row['stage_times']['snapshot_request_ms'],12.)
        json.dumps(result,allow_nan=False)

    def test_duplicate_responses_do_not_replace_last_distinct_frames(self):
        trace=self.trace()
        for i,sequence in enumerate((1,2,2,2)):
            now=10+i*.1;ident=trace.start(now,0)
            trace.finish(ident,now,now+.02,dict(pid=4,sequence=sequence,timestamp_seconds=sequence),None,True,False)
        trace.consume(10.4,None,None,{},'pause','source_timeout',1.,1.,True)
        event=trace.snapshot(10.4)['pause_contexts'][0]
        self.assertEqual([r['sequence'] for r in event['last_received']],[2,2])
        self.assertEqual([r['sequence'] for r in event['last_distinct']],[1,2])

    def test_receive_age_and_delivery_delay_are_independently_measured(self):
        trace=self.trace();state=dict(sequence=7,timestamp_seconds=10.)
        ident=trace.start(10.25,0);trace.finish(ident,10.25,10.3,state,None,True,False)
        trace.consume(10.6,(state,10.3,ident),None,dict(reason='invalid_source'),
                      'pause','source_timeout',9.9,9.9,True)
        event=trace.snapshot(10.6)['pause_contexts'][0]
        self.assertAlmostEqual(event['polls'][0]['request_ms'],50.)
        self.assertAlmostEqual(event['polls'][0]['source_age_at_receive'],.3)
        self.assertAlmostEqual(event['ticks'][0]['delivery_ms'],300.)
        self.assertEqual(event['ticks'][0]['pending_kind'],'expired_delivery')

    def test_pauses_survive_recovery_and_all_rings_are_bounded(self):
        trace=self.trace()
        for i in range(200):
            now=10+i*.02;ident=trace.start(now,0)
            trace.finish(ident,now,now+.01,dict(sequence=i,timestamp_seconds=now),None,True,False)
            trace.consume(now+.012,None,None,{},'play','playing',now,now,True)
        for i in range(4):trace.consume(20+i,None,None,{},'pause','source_timeout',10.,10.,True)
        frozen=trace.snapshot(24.)['pause_contexts']
        trace.consume(24.,None,None,{},'resume','playing',24.,24.,False)
        self.assertEqual(trace.snapshot(24.)['pause_contexts'][-1]['polls'],frozen[-1]['polls'])
        self.assertIsNone(frozen[-1]['resume'])
        self.assertEqual(trace.snapshot(24.)['pause_contexts'][-1]['resume']['monotonic'],24.)
        self.assertEqual(len(trace.polls),64);self.assertEqual(len(trace.ticks),96)
        self.assertEqual(len(frozen),2);self.assertEqual(trace.counts['recorded_pauses'],4)
        self.assertLess(len(json.dumps(trace.snapshot(24.))),50000)

    def test_actual_tick_decisions_and_events_are_unchanged_by_observer(self):
        from taiko.test_tracking import PanelTrackingTests
        from taiko.test_input import pose
        fixture=PanelTrackingTests();plain=fixture.panel();traced=fixture.panel();traced.pose_timing=self.trace()
        for now,state in ((10.,pose(10.)),(10.1,pose(10.1)),(10.2,pose(10.2)),
                          (10.3,pose(10.3,.2)),(10.4,pose(10.4,.75)),(11.1,None),
                          (11.2,pose(11.2)),(11.4,pose(11.4))):
            fixture.tick(plain,state,now);fixture.tick(traced,state,now)
            self.assertEqual(plain.control,traced.control)
            self.assertEqual(plain.session.running,traced.session.running)
            self.assertEqual(list(plain.tracking_events),list(traced.tracking_events))
            self.assertEqual(plain.session.tick.call_args_list,traced.session.tick.call_args_list)
        contexts=traced.pose_timing.snapshot(11.4)['pause_contexts']
        self.assertEqual(len(contexts),1);self.assertEqual(contexts[0]['reason'],'source_timeout')
        self.assertTrue(traced.session.running)

    def test_actual_poll_records_failed_request_without_retaining_error_message(self):
        cls,_=load_class(ROOT/'taiko/main.py','TaikoPanel')
        p=cls.__new__(cls);p.lock=threading.Lock();p.active=True;p.generation=1;p.latest=None
        p.args=NS(pose_url='http://localhost/api/state');p.pose_timing=self.trace()
        p.stop=NS(is_set=Mock(side_effect=[False,False,True]),wait=Mock())
        opener=NS(open=Mock(side_effect=TimeoutError('sensitive arbitrary error text')))
        with patch('urllib.request.build_opener',return_value=opener):p.poll()
        row=p.pose_timing.snapshot(0.)['last_polls'][0]
        self.assertEqual(row['error_type'],'TimeoutError');self.assertTrue(row['accepted'])
        self.assertIsNone(p.latest[0]);self.assertEqual(p.latest[2],row['poll_id'])
        self.assertNotIn('sensitive',json.dumps(row))

    def test_actual_poll_discards_old_generation_but_records_the_timing(self):
        cls,_=load_class(ROOT/'taiko/main.py','TaikoPanel')
        p=cls.__new__(cls);p.lock=threading.Lock();p.active=True;p.generation=1;p.latest=None
        p.args=NS(pose_url='http://localhost/api/state');p.pose_timing=self.trace()
        p.stop=NS(is_set=Mock(side_effect=[False,False,True]),wait=Mock())
        def response(*args,**kwargs):
            p.generation+=1
            return io.BytesIO(b'{"sequence":1,"timestamp_seconds":10}')
        with patch('urllib.request.build_opener',return_value=NS(open=response)):p.poll()
        self.assertIsNone(p.latest)
        self.assertFalse(p.pose_timing.snapshot(0.)['last_polls'][0]['accepted'])

    def test_subsecond_pause_preserves_inflight_completion_and_resume_until_next_write(self):
        trace=self.trace();ident=trace.start(10.05,0)
        trace.consume(10.1,None,None,{},'pause','source_timeout',9.,9.,True)
        frozen=trace.snapshot(10.1)['pause_contexts'][0]
        state=dict(pid=4,sequence=2,timestamp_seconds=10.2)
        trace.finish(ident,10.05,10.3,state,None,True,False)
        trace.consume(10.5,(state,10.3,ident),state,{},'resume','playing',9.,10.2,False)
        for i in range(30):
            now=10.51+i*.02;ident2=trace.start(now,0)
            trace.finish(ident2,now,now+.01,dict(sequence=3+i,timestamp_seconds=now),None,True,False)
        event=trace.snapshot(11.2)['pause_contexts'][0]
        self.assertEqual(event['inflight_result']['poll_id'],ident)
        self.assertEqual(event['inflight_result']['received'],10.3)
        self.assertEqual(event['resume']['monotonic'],10.5)
        self.assertIsNone(frozen['inflight_result']);self.assertIsNone(frozen['resume'])

    def test_diagnostic_failure_cannot_change_actual_tick_or_poll(self):
        from taiko.test_tracking import PanelTrackingTests
        from taiko.test_input import pose
        fixture=PanelTrackingTests();p=fixture.panel()
        p.pose_timing=NS(consume=Mock(side_effect=RuntimeError('injected diagnostic failure')))
        for now in (10.,10.1,10.2):fixture.tick(p,pose(now),now)
        self.assertTrue(p.session.running);self.assertIsNone(p.pose_timing)
        self.assertEqual(p.pose_timing_error,'RuntimeError')
        cls,_=load_class(ROOT/'taiko/main.py','TaikoPanel')
        p=cls.__new__(cls);p.lock=threading.Lock();p.active=True;p.generation=1;p.latest=None
        p.args=NS(pose_url='http://localhost/api/state')
        p.pose_timing=NS(start=lambda *a:1,finish=Mock(side_effect=RuntimeError('injected')))
        p.stop=NS(is_set=Mock(side_effect=[False,False,True]),wait=Mock())
        with patch('urllib.request.build_opener',return_value=NS(open=lambda *a,**k:io.BytesIO(b'{"sequence":7}'))):p.poll()
        self.assertEqual(p.latest[0],{'sequence':7});self.assertEqual(len(p.latest),2)
        self.assertEqual(p.pose_timing_error,'RuntimeError')


if __name__ == '__main__':unittest.main()
