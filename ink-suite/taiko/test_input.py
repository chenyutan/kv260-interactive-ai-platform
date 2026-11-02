import unittest
from .input import DrumInput


def pose(t,y=.2,side='L',seq=None):
    p=[[.5,.5,0.] for _ in range(17)]
    p[5]=[.60,.30,.9];p[6]=[.40,.30,.9]
    for hand,i,x in (('L',9,.63),('R',10,.37)):
        p[i]=[x,.30+(y if side in (hand,'B') else .9)*(.2*16/9),.9]
    return dict(pid=1,sequence=int(t*1000) if seq is None else seq,timestamp_seconds=t,points17=p)


class InputTests(unittest.TestCase):
    def test_downstroke_and_double(self):
        for side,count in (('L',1),('R',1),('B',2)):
            tracker=DrumInput();tracker.consume(pose(10,.2,side),10.)
            result=tracker.consume(pose(10.1,.75,side),10.1)
            self.assertEqual(len(result['events']),count)
            self.assertGreater(result['events'][0]['timestamp'],10.)
            self.assertLess(result['events'][0]['timestamp'],10.1)

    def test_duplicate_frame_not_rearmed(self):
        tracker=DrumInput();tracker.consume(pose(10,.2),10.)
        down=pose(10.1,.75);self.assertEqual(len(tracker.consume(down,10.1)['events']),1)
        for now in (10.11,10.15,10.2):self.assertFalse(tracker.consume(down,now)['events'])

    def test_same_sequence_retimestamped_never_strikes(self):
        tr=DrumInput();tr.consume(pose(10,.2,seq=100),10.)
        self.assertFalse(tr.consume(pose(10.1,.75,seq=100),10.1)['events'])

    def test_duplicate_pose_keeps_display_without_repeating_strike(self):
        tracker=DrumInput();raised=pose(10,.2,side='B')
        original=tracker.consume(raised,10.)
        for now in (10.016,10.032,10.048):
            duplicate=tracker.consume(raised,now)
            self.assertTrue(duplicate['valid'])
            self.assertFalse(duplicate['fresh'])
            self.assertEqual(duplicate['hands'],original['hands'])
            self.assertEqual(duplicate['events'],[])
        down=pose(10.1,.75,side='B')
        first=tracker.consume(down,10.1)
        self.assertEqual(len(first['events']),2)
        for now in (10.116,10.132):
            duplicate=tracker.consume(down,now)
            self.assertEqual(duplicate['hands'],first['hands'])
            self.assertEqual(duplicate['events'],[])
        expired=tracker.consume(down,10.35)
        self.assertFalse(expired['valid'])
        self.assertEqual(expired['hands'],{})
        self.assertEqual(expired['events'],[])

    def test_cached_display_does_not_survive_menu_loss_identity_or_staleness(self):
        for kind in ('menu','loss','identity','stale'):
            tracker=DrumInput();tracker.consume(pose(10,.2,side='B'),10.)
            state=pose(10.03,.2,side='B')
            if kind=='menu':state['suite']=dict(menu=True)
            elif kind=='loss':state=None
            elif kind=='identity':state['pid']=2
            else:state['stale']=True
            reset=tracker.consume(state,10.03)
            self.assertEqual(reset['hands'],{},kind)
            duplicate=tracker.consume(state,10.046)
            self.assertEqual(duplicate['hands'],{},kind)
            self.assertEqual(duplicate['events'],[],kind)

    def test_resume_epoch_does_not_bridge_pause_frames(self):
        tr=DrumInput();tr.reset(minimum_stamp=20.)
        tr.consume(pose(19.75,.2),20.)
        self.assertFalse(tr.consume(pose(19.85,.75),20.03)['events'])
        self.assertFalse(tr.consume(pose(20.06,.80),20.08)['events'])
        tr.consume(pose(20.1,.2),20.12)
        self.assertEqual(len(tr.consume(pose(20.2,.75),20.22)['events']),1)

    def test_enter_low_no_hit_and_raise_again(self):
        tracker=DrumInput()
        for i in range(20):self.assertFalse(tracker.consume(pose(10+i/30,.8),10+i/30)['events'])
        tracker.consume(pose(11,.2),11.);self.assertEqual(len(tracker.consume(pose(11.1,.75),11.1)['events']),1)

    def test_loss_menu_identity_jumps_clear_arm(self):
        for kind in ('loss','menu','identity','roi','pointjump'):
            tr=DrumInput();tr.consume(pose(10,.2),10.)
            p=pose(10.04,.25)
            if kind=='loss':p=None
            elif kind=='menu':p['suite']=dict(active=True,mode='taiko',control_active=True)
            elif kind=='identity':p['pid']=2
            elif kind=='roi':p['motion_history_discontinuity']=True
            elif kind=='pointjump':p['points17'][9][1]=.99
            tr.consume(p,10.04)
            q=pose(10.1,.75)
            if kind=='identity':q['pid']=2
            self.assertFalse(tr.consume(q,10.1)['events'],kind)

    def test_stale_and_gap_never_bridge(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        self.assertFalse(tr.consume(pose(10.4,.8),10.4)['events'])
        self.assertFalse(tr.consume(pose(10.5,.2),12.)['valid'])

    def test_low_confidence_does_not_infer_hand(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        p=pose(10.1,.75);p['points17'][9][2]=.05
        self.assertFalse(tr.consume(p,10.1)['events'])

    def test_shoulder_scale_only_is_not_a_strike(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        for i in range(1,7):tr.consume(pose(10+i*.04,.2+i*.066),10+i*.04)
        p=pose(10.28,.596)
        p['points17'][5][0]=.582;p['points17'][6][0]=.418
        self.assertFalse(tr.consume(p,10.28)['events'])

    def test_fast_downstroke_at_12_and_17_hz(self):
        # At these actual inference rates, one useful downstroke can cover
        # more than .8 shoulder widths between two real observations.
        for fps, distance in ((12, 1.), (17, .84)):
            tr=DrumInput();tr.consume(pose(10,.2),10.)
            at=10+1/fps
            result=tr.consume(pose(at,.2+distance),at)
            self.assertEqual(len(result['events']),1,(fps,result))
            self.assertGreater(result['events'][0]['timestamp'],10.)
            self.assertLess(result['events'][0]['timestamp'],at)
            self.assertNotIn('L',result['rejections'])
            self.assertFalse(tr.consume(pose(at,.2+distance),at+.01)['events'])

    def test_fast_sampling_does_not_admit_impossible_wrist_jump(self):
        for fps, y, x in ((12,1.7,None),(30,1.1,None),(12,1.2,.99)):
            tr=DrumInput();tr.consume(pose(10,.2),10.)
            at=10+1/fps
            changed=pose(at,y)
            if x is not None:changed['points17'][9][0]=x
            result=tr.consume(changed,at)
            self.assertFalse(result['events'],(fps,y,x))
            self.assertEqual(result['rejections']['L'],'wrist_jump')
            # Landing low after a rejected jump cannot remain armed.
            self.assertFalse(tr.consume(pose(at+.08,.8),at+.08)['events'])

    def test_rejected_upward_jump_cannot_arm_the_next_downstroke(self):
        tr=DrumInput();tr.consume(pose(10,.8),10.)
        jumped=tr.consume(pose(10.04,-.2),10.04)
        self.assertEqual(jumped['rejections']['L'],'wrist_jump')
        self.assertFalse(tr.consume(pose(10.14,.8),10.14)['events'])
        # A subsequent genuinely observed raised pose may re-arm normally.
        tr.consume(pose(10.34,.2),10.34)
        self.assertEqual(len(tr.consume(pose(10.44,.8),10.44)['events']),1)

    def test_presence_does_not_require_a_visible_wrist(self):
        tr=DrumInput();tr.consume(pose(10,.2,side='B'),10.)
        missing=pose(10.1,.8,side='B')
        missing['points17'][9][2]=missing['points17'][10][2]=.05
        result=tr.consume(missing,10.1)
        self.assertTrue(result['fresh']);self.assertTrue(result['source_fresh'])
        self.assertTrue(result['body_present']);self.assertEqual(result['body_stamp'],10.1)
        self.assertFalse(result['valid']);self.assertFalse(result['events'])
        self.assertEqual(result['reason'],'wrists_missing')
        self.assertEqual(result['rejections'],dict(L='wrist_missing',R='wrist_missing'))
        # Wrist reappearance must not bridge the missing segment.
        self.assertFalse(tr.consume(pose(10.18,.8,side='B'),10.18)['events'])

    def test_missing_shoulder_preserves_measured_body_presence_but_not_strike(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        changed=pose(10.1,.8)
        changed['points17'][5][2]=.05
        changed['points17'][7]=[.62,.45,.9]
        changed['points17'][11]=[.56,.65,.9]
        result=tr.consume(changed,10.1)
        self.assertTrue(result['body_present']);self.assertEqual(result['body_stamp'],10.1)
        self.assertTrue(result['source_fresh']);self.assertFalse(result['valid'])
        self.assertEqual(result['hands'],{});self.assertEqual(result['events'],[])
        self.assertEqual(result['reason'],'shoulder_anchor_missing')
        self.assertFalse(tr.consume(pose(10.18,.8),10.18)['events'])
        tr.consume(pose(10.26,.2),10.26)
        self.assertEqual(len(tr.consume(pose(10.34,.8),10.34)['events']),1)

    def test_hip_pair_is_independent_presence_evidence(self):
        tr=DrumInput();changed=pose(10,.2)
        changed['points17'][5][2]=changed['points17'][6][2]=.05
        changed['points17'][11]=[.56,.65,.9]
        changed['points17'][12]=[.44,.65,.9]
        result=tr.consume(changed,10.)
        self.assertTrue(result['body_present']);self.assertEqual(result['body_stamp'],10.)
        self.assertFalse(result['valid']);self.assertEqual(result['hands'],{})

    def test_presence_never_refreshes_from_wrists_or_clustered_core_noise(self):
        for kind in ('wrists_only','one_core','clustered_core'):
            tr=DrumInput();changed=pose(10,.2)
            for i in (5,6,7,8,11,12):changed['points17'][i][2]=.05
            if kind=='one_core':changed['points17'][5]=[.60,.30,.9]
            if kind=='clustered_core':
                for i in (5,7,11):changed['points17'][i]=[.5,.5,.9]
            result=tr.consume(changed,10.)
            self.assertFalse(result['body_present'],kind)
            self.assertIsNone(result['body_stamp'],kind)
            self.assertTrue(result['source_fresh'],kind)
            self.assertFalse(result['events'],kind)

    def test_duplicate_and_delayed_observations_do_not_extend_presence(self):
        tr=DrumInput();raised=pose(10,.2)
        result=tr.consume(raised,10.1)
        self.assertEqual(result['body_stamp'],10.)
        self.assertEqual(result['source_stamp'],10.)
        self.assertAlmostEqual(result['source_age'],.1)
        for now in (10.11,10.3,10.44):
            result=tr.consume(raised,now)
            self.assertFalse(result['fresh']);self.assertFalse(result['source_fresh'])
            self.assertEqual(result['body_stamp'],10.)
            self.assertEqual(result['source_stamp'],10.)
            self.assertFalse(result['events'])
        result=tr.consume(raised,10.46)
        self.assertFalse(result['body_present']);self.assertIsNone(result['body_stamp'])
        self.assertEqual(result['reason'],'source_expired')

    def test_no_current_core_evidence_does_not_refresh_last_body_stamp(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        empty=pose(10.1,.8)
        for i in (5,6,7,8,11,12):empty['points17'][i][2]=.05
        result=tr.consume(empty,10.1)
        self.assertTrue(result['source_fresh']);self.assertFalse(result['body_present'])
        self.assertEqual(result['body_stamp'],10.)
        duplicate=tr.consume(empty,10.12)
        self.assertFalse(duplicate['body_present']);self.assertFalse(duplicate['source_fresh'])
        self.assertEqual(duplicate['body_stamp'],10.)

    def test_menu_candidate_updates_presence_and_source_but_never_strikes(self):
        tr=DrumInput();tr.consume(pose(10,.2,side='B'),10.)
        candidate=pose(10.1,.8,side='B')
        candidate['suite']=dict(control_active=True,mode='taiko',active=True)
        result=tr.consume(candidate,10.1)
        self.assertTrue(result['body_present']);self.assertEqual(result['body_stamp'],10.1)
        self.assertTrue(result['source_fresh']);self.assertFalse(result['valid'])
        self.assertEqual(result['reason'],'menu_candidate');self.assertEqual(result['events'],[])
        duplicate=tr.consume(candidate,10.12)
        self.assertTrue(duplicate['body_present']);self.assertEqual(duplicate['body_stamp'],10.1)
        self.assertFalse(duplicate['source_fresh']);self.assertFalse(duplicate['events'])
        self.assertFalse(tr.consume(pose(10.18,.8,side='B'),10.18)['events'])
        tr.consume(pose(10.26,.2,side='B'),10.26)
        self.assertEqual(len(tr.consume(pose(10.34,.8,side='B'),10.34)['events']),2)

    def test_diagnostics_distinguish_unarmed_and_slow_crossing(self):
        tr=DrumInput();tr.consume(pose(10,.65),10.)
        self.assertEqual(tr.consume(pose(10.1,.75),10.1)['rejections']['L'],'not_armed')
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        tr.consume(pose(10.2,.65),10.2)
        result=tr.consume(pose(10.4,.70),10.4)
        self.assertEqual(result['rejections']['L'],'strike_speed_invalid')
        self.assertFalse(result['events'])

    def test_backward_replay_cannot_create_a_second_old_stroke(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        self.assertEqual(len(tr.consume(pose(10.1,.8),10.1)['events']),1)
        for at,y in ((9.95,.2),(10.,.2),(10.05,.8),(10.1,.8)):
            result=tr.consume(pose(at,y),10.13)
            self.assertFalse(result['events'],(at,result))
            self.assertFalse(result['source_fresh'])
            self.assertEqual(tr.high_water_stamp,10.1)
        # A genuinely new lowered pose cannot use a replayed raise.
        self.assertFalse(tr.consume(pose(10.14,.8),10.14)['events'])
        tr.consume(pose(10.22,.2),10.22)
        self.assertEqual(len(tr.consume(pose(10.3,.8),10.3)['events']),1)

    def test_loss_and_explicit_reset_preserve_same_identity_high_water(self):
        for kind in ('none','stale','missing_wrist','menu','explicit_reset','epoch_reset'):
            tr=DrumInput();tr.consume(pose(10,.2),10.)
            self.assertEqual(len(tr.consume(pose(10.1,.8),10.1)['events']),1)
            if kind=='explicit_reset':tr.reset()
            elif kind=='epoch_reset':tr.reset(minimum_stamp=10.12)
            else:
                changed=pose(10.11,.8)
                if kind=='none':changed=None
                elif kind=='stale':changed['stale']=True
                elif kind=='menu':changed['suite']=dict(menu=True)
                else:changed['points17'][9][2]=changed['points17'][10][2]=.05
                tr.consume(changed,10.11)
            water=tr.high_water_stamp
            for at,y in ((9.95,.2),(10.,.2),(10.05,.8),(10.1,.8)):
                result=tr.consume(pose(at,y),10.13)
                self.assertFalse(result['events'],(kind,at,result))
                self.assertFalse(result['source_fresh'],(kind,at))
                self.assertEqual(tr.high_water_stamp,water)

    def test_retimestamped_duplicate_disarms_without_lowering_high_water(self):
        tr=DrumInput();tr.consume(pose(10,.2,seq=100),10.)
        result=tr.consume(pose(10.02,.2,seq=100),10.02)
        self.assertEqual(result['reason'],'out_of_order_observation')
        self.assertFalse(result['source_fresh']);self.assertEqual(tr.high_water_stamp,10.)
        self.assertFalse(tr.consume(pose(10.1,.8,seq=101),10.1)['events'])

    def test_same_timestamp_different_sequence_disarms_without_replay(self):
        tr=DrumInput();tr.consume(pose(10,.2,seq=100),10.)
        result=tr.consume(pose(10,.2,seq=99),10.02)
        self.assertEqual(result['reason'],'out_of_order_observation')
        self.assertFalse(tr.consume(pose(10.1,.8,seq=101),10.1)['events'])

    def test_real_identity_restart_accepts_new_clock_without_cross_arming(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        restarted=pose(9.9,.2);restarted['pid']=2
        result=tr.consume(restarted,10.)
        self.assertEqual(result['reason'],'identity_discontinuity')
        self.assertEqual(tr.high_water_stamp,9.9)
        down=pose(9.98,.8);down['pid']=2
        self.assertFalse(tr.consume(down,10.02)['events'])
        raised=pose(10.06,.2);raised['pid']=2
        tr.consume(raised,10.06)
        down=pose(10.14,.8);down['pid']=2
        self.assertEqual(len(tr.consume(down,10.14)['events']),1)

    def test_discontinuity_flag_on_old_packet_cannot_lower_high_water(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        tr.consume(pose(10.1,.8),10.1)
        replay=pose(9.95,.2);replay['motion_history_discontinuity']=True
        result=tr.consume(replay,10.13)
        self.assertEqual(result['reason'],'out_of_order_observation')
        self.assertEqual(tr.high_water_stamp,10.1)
        self.assertFalse(tr.consume(pose(10.05,.8),10.13)['events'])

    def test_persistent_discontinuity_duplicate_is_not_fresh(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        changed=pose(10.1,.2);changed['motion_history_discontinuity']=True
        self.assertTrue(tr.consume(changed,10.1)['source_fresh'])
        for now in (10.11,10.12,10.13):
            result=tr.consume(changed,now)
            self.assertFalse(result['source_fresh']);self.assertFalse(result['events'])
            self.assertEqual(tr.high_water_stamp,10.1)

    def test_newer_failed_source_frame_prevents_intermediate_old_replay(self):
        for kind in ('stale','unavailable','menu','expired'):
            tr=DrumInput();tr.consume(pose(10,.2),10.)
            tr.consume(pose(10.1,.8),10.1)
            failed=pose(10.2,.2)
            if kind=='stale':failed['stale']=True
            elif kind=='unavailable':failed['available']=False
            elif kind=='menu':failed['suite']=dict(menu=True)
            tr.consume(failed,10.8 if kind=='expired' else 10.2)
            self.assertEqual(tr.high_water_stamp,10.2,kind)
            for at,y in ((10.12,.2),(10.18,.8)):
                result=tr.consume(pose(at,y),10.24)
                self.assertFalse(result['events'],(kind,result))
                self.assertFalse(result['source_fresh'])
                self.assertEqual(tr.high_water_stamp,10.2)

    def test_far_future_bad_packet_does_not_poison_source_watermark(self):
        tr=DrumInput();tr.consume(pose(10,.2),10.)
        self.assertFalse(tr.consume(pose(100,.8),10.1)['source_fresh'])
        self.assertEqual(tr.high_water_stamp,10.)
        self.assertFalse(tr.consume(pose(10.12,.8),10.12)['events'])
        tr.consume(pose(10.2,.2),10.2)
        self.assertEqual(len(tr.consume(pose(10.28,.8),10.28)['events']),1)

    def test_chest_height_up_down_reversal_hits_once_without_global_raise(self):
        tr=DrumInput()
        for at,y in ((10.,.95),(10.1,.72),(10.2,.70)):
            self.assertFalse(tr.consume(pose(at,y),at)['events'])
        result=tr.consume(pose(10.3,.98),10.3)
        self.assertEqual(len(result['events']),1)
        self.assertEqual(result['events'][0]['path'],'reversal')
        self.assertGreater(result['events'][0]['timestamp'],10.2)
        self.assertLess(result['events'][0]['timestamp'],10.3)
        for at,y in ((10.31,.98),(10.4,1.1),(10.5,1.25)):
            self.assertFalse(tr.consume(pose(at,y),at)['events'])
        tr.consume(pose(10.6,.95),10.6)
        self.assertEqual(len(tr.consume(pose(10.7,1.2),10.7)['events']),1)

    def test_reversal_supports_alternating_hands_and_double_hit(self):
        tr=DrumInput();all_events=[]
        for n,(left,right) in enumerate(((1.,1.),(.75,1.),(1.,.75),(1.,1.))):
            at=10.+n*.1;state=pose(at,left)
            state['points17'][10][1]=.30+right*(.2*16/9)
            all_events.extend(tr.consume(state,at)['events'])
        self.assertEqual([e['hand'] for e in all_events],['L','R'])
        tr=DrumInput();tr.consume(pose(10.,1.,side='B'),10.)
        tr.consume(pose(10.1,.75,side='B'),10.1)
        self.assertEqual(len(tr.consume(pose(10.2,1.,side='B'),10.2)['events']),2)

    def test_reversal_rejects_static_noise_slow_return_and_raising_only(self):
        cases=(
            [(10.+i*.08, .95+(.035 if i%2 else -.035)) for i in range(40)],
            [(10.+i*.1,1.1-i*.035) for i in range(20)],
            [(10.,1.),(10.1,.75),(10.3,.8),(10.5,.85),(10.7,.9),(10.9,.95),(11.1,1.)],
        )
        for samples in cases:
            tr=DrumInput()
            for at,y in samples:
                self.assertFalse(tr.consume(pose(at,y),at)['events'],(samples,at,y))

    def test_reversal_cannot_bridge_wrist_or_shoulder_loss_or_stale_gap(self):
        for kind in ('wrist','shoulder','gap','identity','replay','menu'):
            tr=DrumInput();tr.consume(pose(10.,1.),10.)
            tr.consume(pose(10.1,.75),10.1)
            changed=pose(10.15,.75)
            if kind=='wrist':changed['points17'][9][2]=.05
            elif kind=='shoulder':changed['points17'][5][2]=.05
            elif kind=='identity':changed['pid']=2
            elif kind=='replay':changed=pose(9.95,.75)
            elif kind=='menu':changed['suite']=dict(control_active=True,mode='taiko')
            if kind!='gap':tr.consume(changed,10.15)
            at=10.4 if kind=='gap' else 10.2
            lowered=pose(at,1.)
            if kind=='identity':lowered['pid']=2
            self.assertFalse(tr.consume(lowered,at)['events'],kind)

    def test_reversal_cannot_turn_body_translation_into_a_hit(self):
        tr=DrumInput()
        for n,translation in enumerate((0.,-.09,0.,.09,0.,-.09,0.)):
            at=10.+n*.1;state=pose(at,1.)
            for i in (5,6,9,10):state['points17'][i][1]+=translation
            self.assertFalse(tr.consume(state,at)['events'])

    def test_reversal_cannot_turn_shoulder_scale_jitter_into_a_hit(self):
        tr=DrumInput()
        for n,width in enumerate((.2,.26,.2,.26,.2,.26,.2)):
            at=10.+n*.1;state=pose(at,1.)
            state['points17'][5][0]=.5+width/2
            state['points17'][6][0]=.5-width/2
            self.assertFalse(tr.consume(state,at)['events'])

    def test_reversal_does_not_double_count_later_global_line_crossing(self):
        tr=DrumInput();events=[]
        # The local hit can occur while the wrist still satisfies the legacy
        # raised-position rule. It must consume both paths for this cycle.
        for n,y in enumerate((.2,-.05,.2,.4,.7,.9)):
            at=10.+n*.1;events.extend(tr.consume(pose(at,y),at)['events'])
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['path'],'reversal')

    def test_rejected_upward_jump_cannot_arm_chest_height_reversal(self):
        tr=DrumInput();tr.consume(pose(10.,1.5),10.)
        raised=tr.consume(pose(10.04,.55),10.04)
        self.assertEqual(raised['rejections']['L'],'wrist_jump')
        self.assertFalse(tr.consume(pose(10.14,.9),10.14)['events'])

    def test_slow_actual_return_to_global_raise_rearms_after_a_hit(self):
        tr=DrumInput();tr.consume(pose(10.,.2),10.)
        self.assertEqual(len(tr.consume(pose(10.1,.8),10.1)['events']),1)
        for n in range(1,16):
            at=10.1+n*.1;y=.8-n*.04
            self.assertFalse(tr.consume(pose(at,y),at)['events'])
        result=tr.consume(pose(11.7,.8),11.7)
        self.assertEqual(len(result['events']),1)
        self.assertEqual(result['events'][0]['path'],'line')

    def test_15_hz_observations_survive_60_hz_redraws_with_source_delay(self):
        for delay in (.20,.33):
            tr=DrumInput();events=[];cached_expiries=0
            # All stamps are genuine observation times; consumer time adds
            # a constant transport/inference delay. Redraws reuse that same
            # packet, rather than inventing extra source frames.
            for n,y in enumerate((1.,.75,1.,1.,.75,1.)):
                at=10.+n/15;state=pose(at,y)
                result=tr.consume(state,at+delay)
                events.extend(result['events'])
                for redraw in (1/60,2/60,3/60):
                    duplicate=tr.consume(state,at+delay+redraw)
                    self.assertFalse(duplicate['fresh']);self.assertFalse(duplicate['events'])
                    if not duplicate['valid']:cached_expiries+=1
            self.assertEqual(len(events),2,(delay,events))
            if delay==.33:self.assertGreater(cached_expiries,0)

    def test_late_15_hz_global_stroke_preserves_original_event_time(self):
        for delay in (.20,.33):
            tr=DrumInput();raised=pose(10.,.2)
            tr.consume(raised,10.+delay)
            for n in range(1,4):tr.consume(raised,10.+delay+n/60)
            down_at=10.+1/15
            result=tr.consume(pose(down_at,.8),down_at+delay)
            self.assertEqual(len(result['events']),1,delay)
            event_stamp=result['events'][0]['timestamp']
            self.assertGreater(event_stamp,10.);self.assertLess(event_stamp,down_at)

    def test_display_expiry_keeps_motion_but_true_source_expiry_clears_it(self):
        tr=DrumInput();raised=pose(10.,.2)
        tr.consume(raised,10.33)
        self.assertFalse(tr.consume(raised,10.4)['valid'])
        self.assertTrue(tr.hands['L']['armed'])
        self.assertFalse(tr.consume(raised,10.46)['valid'])
        self.assertEqual(tr.hands,{})
        self.assertFalse(tr.consume(pose(10.47,.8),10.8)['events'])

    def test_pre_resume_delayed_stream_keeps_fresh_presence_without_motion(self):
        tr=DrumInput();tr.consume(pose(19.6,.2),19.93)
        tr.reset(minimum_stamp=20.)
        for n in range(5):
            at=19.67+n/15;state=pose(at,.2 if n%2 else .8)
            result=tr.consume(state,at+.33)
            self.assertTrue(result['fresh']);self.assertTrue(result['source_fresh'])
            self.assertTrue(result['body_present']);self.assertEqual(result['body_stamp'],at)
            self.assertEqual(result['source_stamp'],at)
            self.assertEqual(result['reason'],'before_resume_epoch')
            self.assertFalse(result['valid']);self.assertFalse(result['events'])
            self.assertEqual(tr.hands,{})
            duplicate=tr.consume(state,at+.34)
            self.assertFalse(duplicate['source_fresh']);self.assertTrue(duplicate['body_present'])
            self.assertFalse(duplicate['events']);self.assertEqual(tr.hands,{})
        # A lowered first actual post-epoch wrist cannot use an old raised
        # pose. Only a later post-epoch raised sample may arm a real stroke.
        self.assertFalse(tr.consume(pose(20.02,.8),20.35)['events'])
        tr.consume(pose(20.1,.2),20.43)
        self.assertEqual(len(tr.consume(pose(20.18,.8),20.51)['events']),1)

    def test_pre_resume_epoch_does_not_bypass_source_or_identity_protection(self):
        tr=DrumInput();tr.reset(minimum_stamp=20.)
        first=tr.consume(pose(19.7,.2),20.03)
        self.assertTrue(first['body_present'])
        replay=tr.consume(pose(19.6,.8),20.04)
        self.assertEqual(replay['reason'],'out_of_order_observation')
        self.assertFalse(replay['source_fresh']);self.assertFalse(replay['events'])
        expired=tr.consume(pose(19.72,.2),20.2)
        self.assertEqual(expired['reason'],'source_expired')
        self.assertFalse(expired['body_present']);self.assertFalse(expired['source_fresh'])
        new_identity=pose(19.9,.2);new_identity['pid']=2
        changed=tr.consume(new_identity,20.23)
        self.assertEqual(changed['reason'],'identity_discontinuity')
        self.assertFalse(changed['events']);self.assertEqual(tr.hands,{})

    def test_line_event_diagnostics_preserve_original_crossing_time(self):
        tr=DrumInput();tr.consume(pose(10.,.2),10.)
        result=tr.consume(pose(10.1,.75),10.1)
        self.assertEqual(len(result['events']),1)
        event=result['events'][0]
        self.assertEqual(event['path'],'line')
        self.assertEqual(event['previous_source_stamp'],10.)
        self.assertEqual(event['source_stamp'],10.1)
        self.assertAlmostEqual(event['crossing_line'],.68)
        self.assertAlmostEqual(event['old_normalized_y'],.2)
        self.assertAlmostEqual(event['new_normalized_y'],.75)
        self.assertAlmostEqual(event['timestamp'],10.+(.68-.2)/(.75-.2)*.1)
        self.assertFalse(result['hand_diagnostics']['L']['armed'])
        self.assertTrue(result['hand_diagnostics']['L']['spent'])

    def test_local_hit_then_global_cross_is_diagnosed_already_consumed(self):
        tr=DrumInput();tr.consume(pose(10.,.2),10.)
        tr.consume(pose(10.1,-.05),10.1)
        result=tr.consume(pose(10.2,.2),10.2)
        self.assertEqual(len(result['events']),1)
        event=result['events'][0]
        self.assertEqual(event['path'],'reversal')
        self.assertEqual(event['previous_source_stamp'],10.1)
        self.assertEqual(event['source_stamp'],10.2)
        self.assertAlmostEqual(event['crossing_line'],.17)
        self.assertAlmostEqual(event['timestamp'],10.188)
        tr.consume(pose(10.3,.4),10.3)
        follow=tr.consume(pose(10.4,.7),10.4)
        self.assertFalse(follow['events'])
        self.assertEqual(follow['rejections']['L'],'already_consumed')
        self.assertEqual(follow['hand_diagnostics']['L']['rejection'],'already_consumed')
        self.assertTrue(follow['hand_diagnostics']['L']['spent'])

    def test_local_armed_global_cross_is_separate_from_true_not_armed(self):
        tr=DrumInput();tr.consume(pose(10.,1.1),10.)
        tr.consume(pose(10.1,.6),10.1)
        crossing=tr.consume(pose(10.2,.72),10.2)
        self.assertFalse(crossing['events'])
        self.assertEqual(crossing['rejections']['L'],'local_armed_global_cross')
        self.assertTrue(crossing['hand_diagnostics']['L']['local_armed'])
        self.assertFalse(crossing['hand_diagnostics']['L']['spent'])
        self.assertAlmostEqual(crossing['hand_diagnostics']['L']['local_top_y'],.6)
        down=tr.consume(pose(10.3,.9),10.3)
        self.assertEqual(len(down['events']),1)
        self.assertAlmostEqual(down['events'][0]['timestamp'],10.2+(.82-.72)/(.9-.72)*.1)
        tr=DrumInput();tr.consume(pose(10.,.6),10.)
        unarmed=tr.consume(pose(10.1,.75),10.1)
        self.assertFalse(unarmed['events'])
        self.assertEqual(unarmed['rejections']['L'],'not_armed')

    def test_hand_diagnostics_are_bounded_and_describe_only_current_observation(self):
        tr=DrumInput();state=pose(10.,1.,side='B')
        state['points17'][10][2]=.05
        result=tr.consume(state,10.)
        self.assertEqual(set(result['hand_diagnostics']),{'L','R'})
        self.assertIsNone(result['hand_diagnostics']['R']['y'])
        self.assertEqual(result['hand_diagnostics']['R']['rejection'],'wrist_missing')
        duplicate=tr.consume(state,10.01)
        self.assertEqual(duplicate['hand_diagnostics'],{})
        self.assertFalse(duplicate['fresh']);self.assertFalse(duplicate['events'])


if __name__=='__main__':unittest.main()
