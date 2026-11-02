"""Synthetic measured geometry; no live camera or claimed human acceptance."""
import unittest
from unittest.mock import patch
from .clap_input import ClapInput
from .test_input import pose as drum_pose


def pose(t, gap, y=.55, shift=0., confidence=.9):
    state = drum_pose(t, y, 'B')
    for i, sign in ((9, 1), (10, -1)):
        state['points17'][i] = [.5 + shift + sign*gap*.1, .30+y*(.2*16/9), confidence]
    return state


class ClapTests(unittest.TestCase):
    def cycle(self, tracker, at=10., delay=0.):
        events=[]
        for step,gap in enumerate((1.4, 1.1, .8, .4, .35)):
            t=at+step*.08
            events.extend(tracker.consume(pose(t,gap),t+delay)['events'])
        return events

    def test_closing_both_hands_scores_once_at_original_crossing(self):
        tr=ClapInput();events=self.cycle(tr,delay=.33)
        self.assertEqual(len(events),1)
        hit=events[0];self.assertEqual(hit['hand'],'C')
        self.assertEqual(hit['path'],'clap')
        self.assertGreater(hit['timestamp'],10.16);self.assertLess(hit['timestamp'],10.24)
        self.assertEqual(hit['source_stamp'],10.24)
        for t in (10.35,10.4):self.assertFalse(tr.consume(pose(t,.35),t)['events'])
        self.assertEqual(len(self.cycle(tr,11.)),1)

    def test_initial_closed_hands_stationary_never_score(self):
        tr=ClapInput()
        for i in range(40):self.assertFalse(tr.consume(pose(10+i*.04,.4),10+i*.04)['events'])

    def test_small_opening_and_measured_near_contact_score(self):
        # The human recording reached gap .722 near the missed 15.6 s note.
        # This is a synthetic contiguous trajectory, not a replay claim: the
        # recorded diagnostic stream omits some Panel observations.
        tr=ClapInput();events=[]
        for i,gap in enumerate((.96,.84,.722,.71)):
            t=10+i*.10;events.extend(tr.consume(pose(t,gap),t)['events'])
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['crossing_gap'],.75)
        self.assertGreater(events[0]['timestamp'],10.10)
        self.assertLess(events[0]['timestamp'],10.20)

    def test_minimal_opening_needs_excursion_after_nominal_contact(self):
        tr=ClapInput();events=[]
        for i,gap in enumerate((.82,.76,.70,.62)):
            t=10+i*.08;events.extend(tr.consume(pose(t,gap),t)['events'])
            if i<3:self.assertFalse(events)
        self.assertEqual(len(events),1)
        self.assertAlmostEqual(events[0]['crossing_gap'],.64)
        self.assertGreater(events[0]['timestamp'],10.16)

    def test_hands_can_move_inward_on_different_observations(self):
        tr=ClapInput();events=[]
        for i,(left,right) in enumerate(((.62,-.58),(.40,-.58),(.40,-.42),(.40,-.28))):
            t=10+i*.10;state=pose(t,1.)
            state['points17'][9][0]=.5+.2*left
            state['points17'][10][0]=.5+.2*right
            events.extend(tr.consume(state,t)['events'])
        self.assertEqual(len(events),1)
        self.assertGreater(events[0]['timestamp'],10.2)

    def test_static_small_gap_jitter_and_one_hand_jitter_never_score(self):
        for kind in ('static','one_hand'):
            tr=ClapInput()
            for i,gap in enumerate((.92,.88,.91,.89,.92,.87,.72,.67)):
                t=10+i*.08;state=pose(t,gap)
                if kind=='static':state=pose(t,.80+(i%3-1)*.02)
                else:state['points17'][10][0]=.408+(i%2)*.004
                self.assertFalse(tr.consume(state,t)['events'],kind)

    def test_near_contact_without_reopening_does_not_retrigger(self):
        tr=ClapInput();events=[]
        for i,gap in enumerate((.96,.84,.72,.66,.74,.69,.73,.67)):
            t=10+i*.10;events.extend(tr.consume(pose(t,gap),t)['events'])
        self.assertEqual(len(events),1)

    def test_open_cycle_timeout_and_450ms_source_limit_still_apply(self):
        tr=ClapInput();tr.consume(pose(10,.96),10)
        for i in range(1,10):
            t=10+i*.1;self.assertFalse(tr.consume(pose(t,.96-i*.015),t)['events'])
        self.assertFalse(tr.consume(pose(11,.70),11)['events'])
        tr=ClapInput();tr.consume(pose(10,.96),10.44)
        tr.consume(pose(10.1,.84),10.54)
        out=tr.consume(pose(10.2,.70),10.650001)
        self.assertEqual(out['reason'],'source_expired')
        self.assertFalse(out['events'])

    def test_partial_wrist_occlusion_never_completes_a_clap(self):
        tr=ClapInput();tr.consume(pose(10,.96),10)
        lost=pose(10.1,.82);lost['points17'][9][2]=.19
        self.assertEqual(tr.consume(lost,10.1)['reason'],'clap_wrists_missing')
        self.assertFalse(tr.consume(pose(10.2,.70),10.2)['events'])

    def test_double_downstroke_is_not_clap(self):
        tr=ClapInput()
        for t,y in ((10.,.2),(10.1,.8),(10.2,.2),(10.3,.8)):
            self.assertFalse(tr.consume(drum_pose(t,y,'B'),t)['events'])

    def test_one_hand_crossing_fixed_hand_is_not_clap(self):
        tr=ClapInput()
        for i,gap in enumerate((1.4,1.1,.8,.4)):
            t=10+i*.08;state=pose(t,gap)
            state['points17'][10][0]=.36
            self.assertFalse(tr.consume(state,t)['events'])

    def test_translating_hands_and_shoulder_scale_jitter_do_not_score(self):
        tr=ClapInput()
        for i in range(8):
            t=10+i*.05
            self.assertFalse(tr.consume(pose(t,1.4,shift=i*.01),t)['events'])
        tr=ClapInput()
        for i,width in enumerate((.2,.24,.29,.34)):
            t=10+i*.08;state=pose(t,.8)
            state['points17'][5][0]=.5+width/2;state['points17'][6][0]=.5-width/2
            self.assertFalse(tr.consume(state,t)['events'])

    def test_loss_low_confidence_epoch_identity_and_reorder_break_chain(self):
        for kind in ('loss','low','epoch','identity','menu','reorder','late','gap'):
            tr=ClapInput();tr.consume(pose(10,1.4),10);tr.consume(pose(10.08,.8),10.08)
            if kind=='loss':tr.consume(None,10.1)
            elif kind=='low':tr.consume(pose(10.1,.7,confidence=.01),10.1)
            elif kind=='epoch':tr.reset(minimum_stamp=10.13)
            elif kind=='identity':
                changed=pose(10.1,.7);changed['pid']=2;tr.consume(changed,10.1)
            elif kind=='menu':
                menu=pose(10.1,.7);menu['suite']={'control_active':True};tr.consume(menu,10.1)
            elif kind=='reorder':tr.consume(pose(10.04,1.1),10.12)
            elif kind=='late':tr.consume(pose(10.1,.7),10.6)
            contact=pose(10.16 if kind!='gap' else 10.5,.4)
            if kind=='identity':contact['pid']=2
            self.assertFalse(tr.consume(contact,contact['timestamp_seconds'])['events'],kind)

    def test_duplicates_do_not_retimestamp_clap(self):
        tr=ClapInput();tr.consume(pose(10,1.4),10)
        for now in (10.02,10.04):tr.consume(pose(10,1.4),now)
        tr.consume(pose(10.08,.8),10.08)
        contact=pose(10.16,.4);event=tr.consume(contact,10.16)['events']
        self.assertEqual(len(event),1)
        for now in (10.18,10.20):self.assertFalse(tr.consume(contact,now)['events'])

    def test_wrist_teleport_below_waist_and_vertically_separated_never_score(self):
        for kind in ('jump','waist','vertical','side'):
            tr=ClapInput();tr.consume(pose(10,2.),10)
            state=pose(10.01,.3,y=2. if kind=='waist' else .55,shift=.24 if kind=='side' else 0.)
            if kind=='vertical':state['points17'][10][1]+=.3
            self.assertFalse(tr.consume(state,10.01)['events'],kind)


class PanelClapTests(unittest.TestCase):
    def panel(self):
        from .test_tracking import PanelTrackingTests
        helper=PanelTrackingTests('runTest');p=helper.panel(continuity=True)
        p.clap=ClapInput();helper.start(p)
        return p,helper

    def test_real_panel_delivers_C_once_without_downward_events_or_music_pause(self):
        p,helper=self.panel()
        for i,gap in enumerate((1.4,1.1,.8,.4,.35)):
            t=10.4+i*.08;helper.tick(p,pose(t,gap),t)
        events=[event for call in p.session.tick.call_args_list for event in call.args[1]]
        self.assertEqual([e['hand'] for e in events],['C'])
        p.session.pause.assert_not_called()
        self.assertEqual(p.tracking_counts['downstroke_C'],1)
        self.assertEqual(p.control['clap']['reason'],'clap_ready')

    def test_menu_candidate_cannot_bridge_closing_pair_after_return(self):
        p,helper=self.panel()
        helper.tick(p,pose(10.4,1.4),10.4)
        menu=pose(10.48,.8);menu['suite']={'active':True,'mode':'taiko','control_active':True}
        helper.tick(p,menu,10.48);helper.tick(p,pose(10.56,.4),10.56)
        self.assertFalse([e for call in p.session.tick.call_args_list for e in call.args[1]])
        p.session.pause.assert_not_called()

    def test_diagonal_clap_does_not_leak_earlier_drum_hits(self):
        p,helper=self.panel()
        for t,gap,y in ((10.4,1.4,.2),(10.48,1.1,.45),(10.56,.8,.75),(10.64,.4,.8)):
            helper.tick(p,pose(t,gap,y=y),t)
        events=[e for call in p.session.tick.call_args_list for e in call.args[1]]
        self.assertEqual([e['hand'] for e in events],['C'])

    def test_small_diagonal_clap_suppresses_precontact_downstroke(self):
        p,helper=self.panel()
        for t,gap,y in ((10.4,.96,.2),(10.48,.82,.30),(10.56,.70,.72)):
            helper.tick(p,pose(t,gap,y=y),t)
            if t==10.48:
                self.assertTrue(p.control['clap']['closing'])
                self.assertFalse([e for call in p.session.tick.call_args_list for e in call.args[1]])
        events=[e for call in p.session.tick.call_args_list for e in call.args[1]]
        self.assertEqual([e['hand'] for e in events],['C'])

    def test_downstrokes_with_slight_convergence_keep_both_drum_events(self):
        cases = (
            ((10.4,1.40,.20),(10.5,1.25,.75),(10.6,1.25,.80)),
            ((10.4,.96,.20),(10.48,.82,.72),(10.56,.96,.80)),
            ((10.4,1.40,.20),(10.48,1.30,.25),(10.56,1.20,.75),(10.64,1.20,.80)),
        )
        for rows in cases:
            with self.subTest(rows=rows):
                p,helper=self.panel()
                for t,gap,y in rows:
                    helper.tick(p,pose(t,gap,y=y),t)
                    self.assertFalse(p.control['clap']['closing'])
                events=[e for call in p.session.tick.call_args_list for e in call.args[1]]
                self.assertEqual([e['hand'] for e in events],['L','R'])
                for event in events:
                    self.assertLessEqual(event['previous_source_stamp'],event['timestamp'])
                    self.assertLessEqual(event['timestamp'],event['source_stamp'])

    def test_downward_ambiguous_prefix_keeps_drums_before_later_contact(self):
        # Its first two frames are identical to a double downstroke which
        # then reopens. Without delaying/retracting strikes, later contact
        # cannot retroactively suppress those valid earlier drum events.
        p,helper=self.panel()
        for t,gap,y in ((10.4,.96,.2),(10.48,.82,.72),(10.56,.70,.8)):
            helper.tick(p,pose(t,gap,y=y),t)
        events=[e for call in p.session.tick.call_args_list for e in call.args[1]]
        self.assertEqual([e['hand'] for e in events],['L','R','C'])

    def test_centered_double_downstroke_still_delivers_both_hands(self):
        p,helper=self.panel()
        for t,y in ((10.4,.2),(10.5,.75)):
            helper.tick(p,drum_pose(t,y,'B'),t)
        events=[e for call in p.session.tick.call_args_list for e in call.args[1]]
        self.assertEqual([e['hand'] for e in events],['L','R'])

    def test_side_closing_pair_is_not_chest_clap(self):
        p,helper=self.panel()
        for i,gap in enumerate((1.4,1.1,.8,.4)):
            t=10.4+i*.08;helper.tick(p,pose(t,gap,shift=.24),t)
        events=[e for call in p.session.tick.call_args_list for e in call.args[1]]
        self.assertEqual(events,[])


if __name__=='__main__':unittest.main()
