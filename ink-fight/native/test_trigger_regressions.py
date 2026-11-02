"""Gesture false-negative regressions plus nearby negative controls.

All inputs are synthetic projections, never camera/player acceptance evidence.
"""
import random
import unittest
from engine import Gesture

CHAMBER=(.1,.45,.05,.35)
GUARD=(.35,.1,.1,-.45)
EXTENDED=(.65,0.,1.15,-.02)

def pose(t,left=CHAMBER,right=CHAMBER,bad=None):
    p=[[.5,.5,.9] for _ in range(17)]
    p[5]=[.4,.4,.9];p[6]=[.6,.4,.9]
    for si,ei,wi,arm in ((5,7,9,left),(6,8,10,right)):
        sh=p[si]
        p[ei]=[sh[0]+arm[0]*.2,sh[1]+arm[1]*.2,.9]
        p[wi]=[sh[0]+arm[2]*.2,sh[1]+arm[3]*.2,.9]
    if bad is not None:p[bad][2]=.19
    return dict(pid=1,sequence=round(t*1000),timestamp_seconds=t,points17=p)

def lerp(a,b,f):return tuple(x+(y-x)*f for x,y in zip(a,b))

def trace(start,end,duration=.4,fps=30,other_start=CHAMBER,other_end=None,bad_at=None,bad_joint=10):
    other_end=other_start if other_end is None else other_end
    n=round(duration*fps)
    return [pose(1+i/fps,lerp(start,end,i/n),lerp(other_start,other_end,i/n),
            bad_joint if i==bad_at else None) for i in range(n+1)]

def moves(seq):
    g=Gesture();commands=[]
    for s in seq:commands.extend(g.consume(s))
    return commands

class TriggerRegressions(unittest.TestCase):
    def test_horizontal_punch_and_palm_from_face_guard(self):
        end=(.575,-.225,1.15,-.45)
        self.assertEqual(moves(trace(GUARD,end)),['left'])
        self.assertEqual(moves(trace(GUARD,end,other_start=GUARD,other_end=end)),['palm'])

    def test_non_attacking_wrist_dropout_does_not_swallow_punch(self):
        self.assertEqual(moves(trace(CHAMBER,EXTENDED,bad_at=10)),['left'])

    def test_attacking_wrist_dropout_discards_unseen_motion(self):
        self.assertEqual(moves(trace(CHAMBER,EXTENDED,bad_at=10,bad_joint=9)),[])

    def test_single_arm_missing_never_guards_or_performs_double_move(self):
        g=Gesture();seq=trace(CHAMBER,EXTENDED,other_end=EXTENDED)
        out=[]
        for s in seq:
            s['points17'][10][2]=.19;out.extend(g.consume(s))
            self.assertTrue(g.valid);self.assertFalse(g.both_valid);self.assertFalse(g.guard)
        self.assertEqual(out,['left'])

    def test_bent_palm_has_strong_travel_not_locked_elbow_requirement(self):
        end=(.575,.33,1.15,-.02)
        for fps in (15,24,30,60):
            self.assertEqual(moves(trace(CHAMBER,end,fps=fps,other_end=end)),['palm'])

    def test_uppercut_allows_small_counterbalancing_hand(self):
        a=(-.25,.45,.12,.40);b=(-.25,.35,.10,-.35)
        other=(.1,.21,.05,.11)
        self.assertEqual(moves(trace(a,b,other_end=other)),['uppercut_left'])

    def test_uppercut_can_start_from_shoulder_level(self):
        self.assertEqual(moves(trace((-.25,.45,.12,-.04),(-.25,.35,.10,-.60))),['uppercut_left'])

    def test_one_long_upward_swing_is_only_one_uppercut(self):
        self.assertEqual(moves(trace((-.25,.45,.12,.40),(-.25,.35,.10,-1.10),duration=.8)),
                         ['uppercut_left'])

    def test_synchronous_bent_arm_raise_never_becomes_uppercut(self):
        a=(-.25,.45,.12,.40);b=(-.25,.35,.10,-.60)
        for fps in (15,30,60):
            self.assertEqual(moves(trace(a,b,fps=fps,other_start=a,other_end=b)),[])

    def test_special_allows_slow_threshold_crossing_then_does_not_repeat(self):
        near=(0.,-.48,.02,-.94);end=(0.,-.48,.02,-.96)
        seq=[pose(1),pose(1.1,near,near)]
        seq.extend(pose(1.2+i*.05,end,end) for i in range(20))
        self.assertEqual(moves(seq),['special'])

    def test_slow_entire_special_raise_uses_observed_displacement(self):
        a=(0.,.15,.02,.30);b=(0.,-.60,.02,-1.2)
        self.assertEqual(moves(trace(a,b,duration=3.,other_start=a,other_end=b)),['special'])

    def test_entering_with_hands_up_does_not_generate_special(self):
        a=(0.,-.48,.02,-.97)
        self.assertEqual(moves([pose(1+i/30,a,a) for i in range(60)]),[])

    def test_face_level_fists_with_lowered_elbows_are_guard_not_special(self):
        face=(.35,.38,.08,-1.12)
        for fps in (15,30,60):
            g=Gesture();out=[]
            for s in trace(CHAMBER,face,fps=fps,other_end=face):
                out.extend(g.consume(s))
            for i in range(fps):
                out.extend(g.consume(pose(1.5+i/fps,face,face)))
                self.assertTrue(g.guard)
            self.assertNotIn('special',out)

    def test_slightly_asynchronous_raise_to_face_guard_does_not_uppercut(self):
        face=(.35,.38,.08,-1.12)
        for fps in (15,30,60):
            for delay in (.04,.066,.09):
                for leading in ('left','right'):
                    g=Gesture();out=[]
                    for i in range(round(.9*fps)+1):
                        elapsed=i/fps
                        early=lerp(CHAMBER,face,min(1.,elapsed/.3))
                        late=lerp(CHAMBER,face,max(0.,min(1.,(elapsed-delay)/.3)))
                        left,right=(early,late) if leading=='left' else (late,early)
                        out.extend(g.consume(pose(1+elapsed,left,right)))
                    with self.subTest(fps=fps,delay=delay,leading=leading):
                        self.assertEqual(out,[])
                        self.assertTrue(g.guard)

    def test_only_one_elbow_overhead_does_not_generate_special(self):
        face=(.35,.38,.08,-1.12);overhead=(0.,-.65,.02,-1.2)
        g=Gesture();out=[]
        for s in trace(CHAMBER,overhead,other_end=face):out.extend(g.consume(s))
        self.assertNotIn('special',out)

    def test_guard_release_margin_requires_entry_and_real_bent_arms(self):
        g=Gesture();edge=(.2,.9,.1,.30);raised=(.2,.9,.1,.05)
        g.consume(pose(1,edge,edge));self.assertFalse(g.guard)
        g.consume(pose(1.1,raised,raised));self.assertTrue(g.guard)
        g.consume(pose(1.2,edge,edge));self.assertTrue(g.guard)
        lowered=(.2,.9,.1,.5)
        g.consume(pose(1.3,lowered,lowered));self.assertFalse(g.guard)
        g.consume(pose(1.4,edge,edge));self.assertFalse(g.guard)
        g.consume(pose(1.5,raised,raised));self.assertTrue(g.guard)
        g.consume(pose(1.6,raised,raised,bad=9));self.assertFalse(g.guard)

    def test_punch_can_ease_out_naturally(self):
        seq=[pose(1),pose(1.1,(.4,0,.8,0)),pose(1.2,(.45,0,.93,0)),
             pose(1.3,(.49,0,.98,0)),pose(1.4,(.52,0,1.04,0)),
             pose(1.5,(.545,0,1.09,0)),pose(1.6,(.565,0,1.13,0))]
        self.assertEqual(moves(seq),['left'])

    def test_long_wait_after_subthreshold_movement_does_not_fire_late(self):
        seq=[pose(1),pose(1.1,(.4,0,.8,0))]
        seq.extend(pose(1.2+i*.1,(.4,0,.8,0)) for i in range(8))
        seq.extend(pose(2+i*.1,(.4+.01*i,0,.8+.02*i,0)) for i in range(8))
        self.assertEqual(moves(seq),[])

    def test_static_bent_extended_arms_with_noise_do_not_fire(self):
        rng=random.Random(281);a=(.575,.33,1.15,-.02);seq=[]
        for i in range(180):
            s=pose(1+i/30,a,a)
            for j in (5,6,7,8,9,10):
                s['points17'][j][0]+=rng.uniform(-.002,.002)
                s['points17'][j][1]+=rng.uniform(-.002,.002)
            seq.append(s)
        self.assertEqual(moves(seq),[])

    def test_teleporting_wrist_does_not_generate_a_strike(self):
        self.assertEqual(moves([pose(1),pose(1+1/60,EXTENDED),pose(1+2/60,EXTENDED)]),[])

    def test_guard_small_sideways_and_vertical_motion_does_not_attack(self):
        a=GUARD;b=(.45,.08,.3,-.6)
        self.assertEqual(moves(trace(a,b,other_start=a,other_end=b)),[])

    def test_twenty_hz_variable_speed_stop_and_repeat_needs_retraction(self):
        profiles={
            'left':(CHAMBER,EXTENDED,False),
            'palm':(CHAMBER,(.575,.33,1.15,-.02),True),
            'uppercut_left':((-.25,.45,.12,.40),(-.25,.35,.10,-.35),False),
        }
        # Accelerate through the middle, ease out at the end, hold, retract,
        # then repeat. The hold must not be mistaken for fresh attacks.
        forward=(0.,.07,.20,.38,.60,.77,.88,.95,.98,1.)
        values=list(forward)+[1.]*12+[.8,.6,.4,.2,0.,0.]+list(forward)+[1.]*12
        for move,(start,end,two_hands) in profiles.items():
            with self.subTest(move=move):
                seq=[]
                for i,a in enumerate(values):
                    arm=lerp(start,end,a)
                    seq.append(pose(1+i/20,arm,arm if two_hands else CHAMBER))
                self.assertEqual(moves(seq),[move,move])

    def test_shoulder_loss_clears_both_normalizations(self):
        g=Gesture();g.consume(pose(1));s=pose(1.1,EXTENDED);s['points17'][5][2]=.10
        self.assertEqual(g.consume(s),[]);self.assertFalse(g.valid);self.assertFalse(g.both_valid)
        self.assertEqual(g.consume(pose(1.2,EXTENDED)),[])

    def test_weak_shoulder_needs_recent_geometry_and_reliable_same_arm(self):
        g=Gesture();s=pose(1);s['points17'][5][2]=.16
        self.assertEqual(g.consume(s),[]);self.assertFalse(g.valid)
        g.consume(pose(1.1))
        s=pose(1.2);s['points17'][5][2]=.16
        self.assertEqual(g.consume(s),[]);self.assertTrue(g.valid)
        s=pose(1.3);s['points17'][5][2]=.16;s['points17'][7][2]=.10
        self.assertEqual(g.consume(s),[]);self.assertFalse(g.valid)

    def test_weak_shoulder_does_not_extend_its_own_trust(self):
        g=Gesture();g.consume(pose(1))
        for t in (1.1,1.2):
            s=pose(t);s['points17'][5][2]=.16;g.consume(s)
        s=pose(1.4);s['points17'][5][2]=.16
        self.assertEqual(g.consume(s),[]);self.assertFalse(g.valid)

if __name__=='__main__':unittest.main()
