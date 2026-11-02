"""Music continuity, real loss and stale-input safety with deterministic poses."""
from collections import Counter, deque
import math
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from taiko.input import DrumInput
from taiko.tracking import TrackingGate,MusicObservation
from taiko.test_input import pose
from test_action_dispatch import load_class, ROOT


def observed(t, *, wrist=True, body=True):
    return dict(fresh=True, source_fresh=True, source_stamp=t,
                body_present=body, body_stamp=t if body else None, valid=wrist)


class TrackingTests(unittest.TestCase):
    def start(self):
        g=TrackingGate()
        self.assertEqual(g.update(observed(10),10)[0],'hold')
        self.assertEqual(g.update(observed(10.1),10.1)[0],'hold')
        self.assertEqual(g.update(observed(10.2),10.2)[0],'resume')
        return g

    def test_music_continues_when_both_wrists_temporarily_missing(self):
        g=self.start()
        for i in range(1,41):
            t=10.2+i*.05
            self.assertEqual(g.update(observed(t,wrist=False),t),('play','wrist_unavailable'))

    def test_short_body_loss_does_not_seek_or_pause_the_song(self):
        g=self.start()
        for t in (10.3,10.5,10.8):
            self.assertEqual(g.update(observed(t,wrist=False,body=False),t)[0],'play')
        self.assertEqual(g.update(observed(10.9),10.9)[0],'play')

    def test_person_really_leaving_pauses_after_grace(self):
        g=self.start()
        self.assertEqual(g.update(observed(11.21,body=False),11.21),('pause','body_timeout'))

    def test_cached_frame_cannot_extend_source_or_body_presence(self):
        g=self.start();cached=observed(10.2);cached['fresh']=False
        for t in (10.3,10.5,10.8):self.assertEqual(g.update(cached,t)[0],'play')
        self.assertEqual(g.update(cached,10.851),('pause','source_timeout'))

    def test_future_and_stale_observations_do_not_restart(self):
        for t in (9.,20.):
            g=TrackingGate()
            self.assertEqual(g.update(observed(t),10)[0],'pause')

    def test_menu_candidate_suppresses_hits_without_interrupting_song(self):
        g=self.start()
        for t in (10.3,10.4,10.8,11.2):
            self.assertEqual(g.update(observed(t),t,candidate=True),('play','menu_candidate'))
        self.assertEqual(g.update(observed(11.3),11.3,confirmed_menu=True),('pause','menu'))
        self.assertTrue(g.holding);self.assertIsNone(g.last_body)

    def test_initial_candidate_cannot_start_song(self):
        g=TrackingGate()
        for t in (10.,10.2,10.4):self.assertEqual(g.update(observed(t),t,candidate=True)[0],'hold')
        self.assertEqual(g.update(observed(10.5),10.5)[0],'hold')
        self.assertEqual(g.update(observed(10.7),10.7)[0],'resume')

    def test_recovery_needs_new_measured_body_frames(self):
        g=self.start();g.update({},11.)
        self.assertEqual(g.update(observed(11.1),11.1)[0],'hold')
        cached=observed(11.1);cached['fresh']=False
        self.assertEqual(g.update(cached,11.29)[0],'hold')
        self.assertEqual(g.update(observed(11.3),11.3)[0],'resume')

    def test_long_gap_during_recovery_restarts_short_countdown(self):
        g=TrackingGate();g.update(observed(10.),10.)
        self.assertEqual(g.update({},10.3)[0],'hold')
        self.assertEqual(g.update(observed(10.4),10.4)[0],'hold')
        self.assertEqual(g.update(observed(10.6),10.6)[0],'resume')

    def test_continuous_delayed_poses_resume_despite_faster_cached_ui_ticks(self):
        for delay in (.20,.33,.44):
            g=TrackingGate();resumes=0;last_index=None
            for i in range(90):
                now=10+i/60.;index=i//4;stamp=10+index/15.-delay
                control=observed(stamp);control['fresh']=index!=last_index;last_index=index
                decision,_=g.update(control,now)
                resumes+=decision=='resume'
            self.assertEqual(resumes,1,delay)
            self.assertFalse(g.holding,delay)

    def test_new_body_failure_resets_recovery_but_duplicate_frame_does_not(self):
        g=TrackingGate();g.update(observed(10.),10.2)
        cached=observed(10.);cached['fresh']=False
        g.update(cached,10.3);self.assertEqual(g.recovery_since,10.)
        g.update(observed(10.1,body=False),10.3);self.assertIsNone(g.recovery_since)
        self.assertEqual(g.update(observed(10.2),10.4)[0],'hold')
        self.assertEqual(g.update(observed(10.4),10.6)[0],'resume')

    def test_entry_delay_and_host_menu_always_freeze(self):
        g=self.start()
        self.assertEqual(g.update(observed(10.3),10.3,entering=True),('pause','entry'))
        self.assertIsNone(g.last_source)
        self.assertEqual(g.update(observed(10.4),10.4)[0],'hold')


class PanelTrackingTests(unittest.TestCase):
    def panel(self,continuity=False):
        cls,namespace=load_class(ROOT/'taiko/main.py','TaikoPanel',math=math)
        p=cls.__new__(cls);p.stop=threading.Event();p.lock=threading.Lock()
        p.active=True;p.args=NS(offline_preview=False,quit_after=0,autoplay=False)
        p.started_at=9.;p.entry_until=0.;p.latest=None;p.state=None
        p.tracker=DrumInput();p.tracking=TrackingGate();p.input_hold=True
        if continuity:
            p.music_observer=MusicObservation()
            p.tracking=TrackingGate(source_grace=.90,observation_age=MusicObservation.MAX_AGE)
        p.control={};p.tracking_counts=Counter();p.tracking_events=deque(maxlen=64)
        p.observed_poses=deque(maxlen=300);p.tick_ms=deque(maxlen=600)
        p.audio=NS(error=None,position=lambda:0.)
        def resume():p.session.running=True;return True
        def pause():p.session.running=False
        p.session=NS(running=False,resume=Mock(side_effect=resume),pause=Mock(side_effect=pause),
                     tick=Mock(),error=None,game=NS(finished=False))
        p.canvas=NS(queue_draw=Mock());p.write_metrics=Mock();p.final_at=None
        return p

    def tick(self,p,state,t):
        p.latest=(state,t)
        with patch('time.monotonic',return_value=t):p.tick()

    def start(self,p):
        for t in (10.,10.1,10.2):self.tick(p,pose(t),t)
        self.assertTrue(p.session.running);p.session.pause.reset_mock();p.session.tick.reset_mock()

    def test_missing_wrists_keep_real_tick_running_without_any_hits(self):
        p=self.panel();self.start(p)
        for i in range(1,21):
            t=10.2+i*.05;s=pose(t)
            s['points17'][9][2]=s['points17'][10][2]=.05
            self.tick(p,s,t)
        p.session.pause.assert_not_called();self.assertTrue(p.session.running)
        self.assertTrue(all(call.args[1]==[] for call in p.session.tick.call_args_list))

    def test_confirmed_menu_and_source_loss_pause_but_candidate_does_not(self):
        p=self.panel();self.start(p)
        s=pose(10.3);s['suite']=dict(active=True,mode='taiko',control_active=True)
        self.tick(p,s,10.3);p.session.pause.assert_not_called()
        self.assertEqual(p.session.tick.call_args.args[1],[])
        s=pose(10.4);s['suite']=dict(active=True,mode='taiko',menu=True)
        self.tick(p,s,10.4);self.assertFalse(p.session.running)
        self.assertEqual(p.tracking_events[-1]['reason'],'menu')

    def test_retained_old_pose_event_is_never_scored_after_real_loss(self):
        p=self.panel();self.start(p)
        self.tick(p,None,11.0);self.assertFalse(p.session.running)
        p.session.tick.reset_mock()
        self.tick(p,pose(11.1,.2),11.1);self.tick(p,pose(11.3,.75),11.3)
        self.assertTrue(p.session.running)
        self.assertEqual(p.session.tick.call_args.args[1],[])
        self.assertFalse(p.tracker.consume(pose(11.15,.2),11.31)['events'])

    def test_audio_failure_does_not_auto_retry_at_render_frequency(self):
        p=self.panel();p.session.resume=Mock(return_value=False)
        for i in range(21):self.tick(p,pose(10+i*.1),10+i*.1)
        p.session.resume.assert_called_once()
        p.session.tick.assert_not_called()

    def test_audio_error_stays_latched_across_source_loss_and_recovery(self):
        p=self.panel();self.start(p)
        p.session.error='failed';p.audio.error='failed';p.session.running=False
        p.session.resume.reset_mock()
        self.tick(p,None,11.)
        for t in (11.1,11.3,11.5):self.tick(p,pose(t),t)
        p.session.resume.assert_not_called()
        self.assertFalse(p.session.running)


class MusicContinuityTests(unittest.TestCase):
    def setUp(self):
        self.fixture=PanelTrackingTests()
        self.p=self.fixture.panel(continuity=True)
        self.fixture.start(self.p)

    def tick(self,state,now):
        self.fixture.tick(self.p,state,now)

    def test_advancing_460_to_590ms_poses_keep_song_without_hits_or_new_epoch(self):
        for index in range(1,25):
            stamp=10.2+index*.08
            self.tick(pose(stamp,.75 if index%2 else .2),stamp+(.46,.52,.59)[index%3])
            self.assertTrue(self.p.session.running)
            self.assertFalse(self.p.control['source_fresh'])
            self.assertEqual(self.p.control['events'],[])
            self.assertEqual(self.p.session.tick.call_args.args[1],[])
            self.assertEqual(self.p.tracking.last_source,stamp)
        self.p.session.pause.assert_not_called()
        self.assertEqual(self.p.session.resume.call_count,1)
        self.assertEqual(self.p.tracker.minimum_stamp,10.2)

    def test_music_cache_does_not_prolong_lease_despite_successful_packets(self):
        for now in (10.4,10.6,10.8,11.101):self.tick(pose(10.2),now)
        self.assertFalse(self.p.session.running)
        self.assertEqual(self.p.tracking_events[-1]['reason'],'source_timeout')
        self.assertEqual(self.p.tracking.last_source,10.2)

    def test_real_body_loss_and_http_failure_still_pause(self):
        for index in range(1,12):
            now=10.2+index*.1;state=pose(now)
            for point in state['points17']:point[2]=.01
            self.tick(state,now)
        self.assertEqual(self.p.tracking_events[-1]['reason'],'body_timeout')
        other=self.fixture.panel(continuity=True);self.fixture.start(other)
        self.fixture.tick(other,None,11.101)
        self.assertFalse(other.session.running)

    def test_600ms_only_stream_cannot_start_or_resume_music(self):
        p=self.fixture.panel(continuity=True)
        for i in range(10):self.fixture.tick(p,pose(10+i*.08),10+i*.08+.55)
        p.session.resume.assert_not_called()
        self.assertFalse(p.session.running)
        self.tick(None,11.101)
        for i in range(10):self.tick(pose(11.2+i*.08),11.2+i*.08+.55)
        self.assertFalse(self.p.session.running)
        self.assertEqual(self.p.session.resume.call_count,1)

    def test_recovery_needs_strict_new_body_then_clears_pre_pause_motion(self):
        self.tick(None,11.101)
        self.tick(pose(11.2,.2),11.3)
        self.tick(pose(11.4,.75),11.5)
        self.assertTrue(self.p.session.running)
        self.assertEqual(self.p.session.tick.call_args.args[1],[])
        self.assertEqual(self.p.tracker.minimum_stamp,11.5)
        self.tick(pose(11.3,.2),11.51)
        self.assertFalse(self.p.control['events'])

    def test_new_backend_hard_pauses_and_retired_backend_cannot_reenter(self):
        new=pose(10.3);new['pid']=2
        self.tick(new,10.32)
        self.assertFalse(self.p.session.running)
        self.assertEqual(self.p.tracking_events[-1]['reason'],'identity_discontinuity')
        self.assertIsNone(self.p.tracking.last_body)
        self.tick(pose(10.31),10.33)
        self.assertIsNone(self.p.tracking.last_source)
        for t in (10.4,10.6):
            new=pose(t);new['pid']=2;self.tick(new,t+.02)
        self.assertTrue(self.p.session.running)
        for stamp,y in ((10.7,.2),(10.8,.2),(10.9,.75)):
            self.tick(pose(stamp,y),stamp+.02)
            self.assertEqual(self.p.tracking.last_source,10.6)
            self.assertEqual(self.p.session.tick.call_args.args[1],[])
            self.assertFalse(self.p.control['events'])
        current=pose(11.,.75);current['pid']=2;self.tick(current,11.02)
        self.assertEqual(self.p.session.tick.call_args.args[1],[])

    def test_missing_backend_identity_cannot_build_strict_strike_chain(self):
        for stamp,y in ((10.3,.2),(10.4,.75)):
            state=pose(stamp,y);state.pop('pid');self.tick(state,stamp+.02)
            self.assertEqual(self.p.session.tick.call_args.args[1],[])
        self.tick(pose(10.5,.75),10.52)
        self.assertEqual(self.p.session.tick.call_args.args[1],[])

    def test_roi_identity_change_and_explicit_discontinuity_reset_recovery(self):
        state=pose(10.3);state['person_id']=4;self.tick(state,10.32)
        self.assertFalse(self.p.session.running)
        self.assertIsNone(self.p.tracking.last_body)
        state=pose(10.4);state['person_id']=4;self.tick(state,10.42)
        state=pose(10.5);state['person_id']=4;state['motion_history_discontinuity']=True
        self.tick(state,10.52)
        self.assertIsNone(self.p.tracking.recovery_since)
        self.assertFalse(self.p.session.running)

    def test_stale_new_source_prevents_intermediate_replay_and_future_does_not_poison(self):
        state=pose(10.4);state['stale']=True;self.tick(state,10.42)
        self.tick(pose(10.3),10.45)
        self.assertEqual(self.p.tracking.last_source,10.2)
        self.assertEqual(self.p.music_observer.control['reason'],'out_of_order_observation')
        self.tick(pose(50.),10.5)
        self.tick(pose(10.5),10.6)
        self.assertEqual(self.p.tracking.last_source,10.5)

    def test_failed_key_cannot_reappear_as_an_accepted_duplicate_and_arm(self):
        for flag in ('stale','available'):
            p=self.fixture.panel(continuity=True);self.fixture.start(p)
            failed=pose(10.3,.2);failed[flag]=True if flag=='stale' else False
            self.fixture.tick(p,failed,10.32)
            self.fixture.tick(p,pose(10.3,.2),10.33)
            self.assertEqual(p.music_observer.control['reason'],'invalid_source')
            self.fixture.tick(p,pose(10.4,.75),10.42)
            self.assertEqual(p.session.tick.call_args.args[1],[])

    def test_good_cached_ticks_between_real_observations_preserve_strike_chain(self):
        state=pose(10.3,.2);self.tick(state,10.32)
        for now in (10.33,10.34,10.35):self.tick(state,now)
        self.tick(pose(10.4,.75),10.42)
        self.assertEqual(len(self.p.session.tick.call_args.args[1]),1)

    def test_sequence_retimestamp_and_lifecycle_reset_cannot_refresh_old_source(self):
        self.tick(pose(10.4,seq=10200),10.5)
        self.assertEqual(self.p.tracking.last_source,10.2)
        self.p.music_observer.reset();self.p.tracking.reset()
        self.tick(pose(10.2),10.51)
        self.assertIsNone(self.p.tracking.last_source)
        self.assertFalse(self.p.session.running)

    def test_candidate_suppresses_strikes_and_confirmed_menu_pauses_immediately(self):
        state=pose(10.3);state['suite']=dict(active=True,mode='taiko',control_active=True)
        self.tick(state,10.8)
        self.assertTrue(self.p.session.running)
        self.assertEqual(self.p.session.tick.call_args.args[1],[])
        state=pose(10.4);state['suite']=dict(active=True,mode='taiko',menu=True)
        self.tick(state,10.85)
        self.assertFalse(self.p.session.running)
        self.assertEqual(self.p.tracking_events[-1]['reason'],'menu')

    def test_total_delivery_delay_over_240ms_is_still_discarded(self):
        self.p.latest=(pose(10.3),10.31)
        with patch('time.monotonic',return_value=10.56):self.p.tick()
        self.assertIsNone(self.p.state)
        self.assertFalse(self.p.music_observer.control['fresh'])
        self.assertEqual(self.p.session.tick.call_args.args[1],[])

    def test_original_fresh_downstroke_still_scores_once(self):
        self.tick(pose(10.3,.2),10.32)
        self.tick(pose(10.4,.75),10.42)
        events=self.p.session.tick.call_args.args[1]
        self.assertEqual(len(events),1)
        self.tick(pose(10.4,.75),10.43)
        self.assertEqual(self.p.session.tick.call_args.args[1],[])



class RoiMusicContinuityTests(unittest.TestCase):
    # The second switch in the authorized round has both measured endpoints.
    # The first switch's immediately preceding points were not recorded.
    BEFORE = ((.5738281012,.2593750059,.5874640942),(.4666015506,.2531250119,.5448236465),
              (.5474609137,.578125,.3650864959),(.4771484435,.5625,.3632690012))
    AFTER = ((.5834960938,.265625,.6426702142),(.4736328125,.24609375,.5964576006),
             (.5439453125,.5625,.356819123),(.4736328125,.546875,.4031840861))

    def state(self, t, switched=False, y=.2):
        state=pose(t,y,side='B')
        for index,point in zip((5,6,11,12), self.AFTER if switched else self.BEFORE):
            state['points17'][index]=list(point)
        if switched:
            state['roi_selection']=dict(
                selector_version='hourglass-causal-roi-continuity-v2-partial-recovery',
                reason='other_roi_required', motion_history_discontinuity=True, stream_reset=False,
                continuity=dict(reason='other_roi_required', reliable=True, roi_changed=True,
                                stream_reset=False, disagreement_shoulders=.1212478363))
        return state

    def observe(self, next_state, gap=.150034):
        observer=MusicObservation();presence=DrumInput()._presence
        observer.consume(self.state(10),10.02,presence)
        return observer,observer.consume(next_state,10+gap+.02,presence)

    def test_recorded_close_torso_crop_switch_keeps_music_only(self):
        _,out=self.observe(self.state(10.150034,True))
        self.assertFalse(out.get('discontinuity'))
        self.assertTrue(out['body_present']);self.assertTrue(out['motion_reset'])
        self.assertEqual(out['reason'],'measured_roi_continuity')

    def test_identity_stream_unknown_crop_and_large_body_jump_still_pause(self):
        for case in ('pid','person','stream','top_level','version','reason','uncertain','disagreement','jump','scale','missing_hip'):
            state=self.state(10.150034,True)
            selection=state['roi_selection'];continuity=selection['continuity']
            if case=='pid':state['pid']=2
            elif case=='person':state['person_id']=2
            elif case=='stream':continuity['stream_reset']=True
            elif case=='top_level':state['motion_history_discontinuity']=True
            elif case=='version':selection['selector_version']='unknown'
            elif case=='reason':selection['reason']='new_person'
            elif case=='uncertain':continuity['reliable']=False
            elif case=='disagreement':continuity['disagreement_shoulders']=.3
            elif case=='jump':
                for i in (5,6,11,12):state['points17'][i][0]+=.2
            elif case=='scale':state['points17'][5][0]+=.2
            elif case=='missing_hip':state['points17'][11][2]=.01
            with self.subTest(case=case):
                _,out=self.observe(state)
                self.assertTrue(out.get('discontinuity'),out)
                self.assertFalse(out['body_present'])

    def test_long_gap_reset_and_stale_source_cannot_borrow_old_body(self):
        _,out=self.observe(self.state(10.250001,True),gap=.250001)
        self.assertTrue(out.get('discontinuity'))
        for invalidate in ('reset','stale'):
            observer=MusicObservation();presence=DrumInput()._presence
            observer.consume(self.state(10),10.02,presence)
            if invalidate=='reset':observer.reset()
            else:
                state=self.state(10.05);state['stale']=True
                observer.consume(state,10.07,presence)
            out=observer.consume(self.state(10.15,True),10.17,presence)
            self.assertTrue(out.get('discontinuity'))

    def test_roi_source_duplicate_is_not_a_new_body_lease(self):
        observer,out=self.observe(self.state(10.150034,True))
        repeated=observer.consume(self.state(10.150034,True),10.2,DrumInput()._presence)
        self.assertFalse(repeated['fresh']);self.assertFalse(repeated['body_present'])
        self.assertEqual(repeated['reason'],'duplicate_observation')

    def test_out_of_order_packet_breaks_roi_anchor_chain(self):
        observer=MusicObservation();presence=DrumInput()._presence
        observer.consume(self.state(10),10.02,presence)
        observer.consume(self.state(9.99),10.04,presence)
        out=observer.consume(self.state(10.15,True),10.17,presence)
        self.assertTrue(out.get('discontinuity'))

    def test_oversized_shoulders_cannot_supply_roi_anchor_tolerance(self):
        observer=MusicObservation();presence=DrumInput()._presence
        before=self.state(10);after=self.state(10.15,True)
        for state in (before,after):
            state['points17'][5][0]=.95;state['points17'][6][0]=.05
        observer.consume(before,10.02,presence)
        out=observer.consume(after,10.17,presence)
        self.assertTrue(out.get('discontinuity'))

    def test_panel_keeps_song_but_strict_motion_reset_cannot_score_jump(self):
        fixture=PanelTrackingTests();panel=fixture.panel(continuity=True);fixture.start(panel)
        fixture.tick(panel,self.state(10.3),10.32)
        fixture.tick(panel,self.state(10.450034,True,y=.75),10.47)
        panel.session.pause.assert_not_called()
        self.assertTrue(panel.session.running)
        self.assertEqual(panel.control['reason'],'identity_discontinuity')
        self.assertEqual(panel.session.tick.call_args.args[1],[])
        self.assertEqual(panel.music_observer.control['reason'],'measured_roi_continuity')

if __name__=='__main__':unittest.main()
