import random
import unittest

from front_gesture import FrontGesture


IDLE = (.2, 1., .4, 1.1)
THRUST = (.05, .15, .05, .4)


def pose(t, left=IDLE, right=IDLE, missing=(), pid=1):
    points = [[-1., -1., 0.] for _ in range(17)]
    points[5] = [.62, .35, .9]
    points[6] = [.38, .35, .9]
    for sh, elbow, wrist, arm in ((5, 7, 9, left), (6, 8, 10, right)):
        x, y, ex, ey = arm
        sx, sy = points[sh][:2]
        points[elbow] = [sx+ex*.24, sy+ey*.24, .9]
        points[wrist] = [sx+x*.24, sy+y*.24, .9]
    for index in missing:
        points[index][2] = 0.
    return dict(pid=pid, sequence=round(t*1000), timestamp_seconds=t, points17=points)


class FrontGestureTests(unittest.TestCase):
    def setUp(self):
        self.g = FrontGesture()

    def seed(self, t=1.):
        for dt in (0., .05, .1):
            self.assertEqual(self.g.consume(pose(t+dt)), [])

    def punch(self, hand='left', t=1.):
        events = []
        for dt, arm in ((.15, THRUST), (.20, (.05,.12,.05,.39)),
                        (.25, (.05,.10,.05,.4)), (.30, (.05,.25,.05,.45)),
                        (.35, (.05,.25,.05,.45)), (.40, (.05,.25,.05,.45))):
            events += self.g.consume(pose(t+dt, **{hand: arm}))
        return events

    def test_front_punch_without_sideways_extension(self):
        self.seed()
        self.assertEqual(self.punch(), ['left'])
        self.assertIn('left', self.g.blocking_hands)

    def test_held_fist_does_not_repeat(self):
        self.seed()
        self.assertEqual(self.punch('right'), ['right'])
        for i in range(20):
            self.assertEqual(self.g.consume(pose(1.45+i*.05, right=THRUST)), [])
        self.assertIn('right', self.g.blocking_hands)

    def test_retraction_rearms(self):
        self.seed()
        self.assertEqual(self.punch(), ['left'])
        for t in (1.45,1.5,1.6,1.7):
            self.assertEqual(self.g.consume(pose(t)), [])
        self.assertNotIn('left', self.g.blocking_hands)
        self.assertEqual(self.punch(t=1.6), ['left'])

    def test_high_guard_forward_down_return_rearms_second_punch(self):
        guard=(.05,-.35,.05,.95)
        strike=(.05,.10,.05,.40)
        for t in (1.,1.05,1.10):
            self.assertEqual(self.g.consume(pose(t,left=guard)), [])
        first=[]
        for i in range(6):
            first += self.g.consume(pose(1.15+i*.05,left=strike))
        self.assertEqual(first,['left'])
        self.assertIn('left',self.g.blocking_hands)
        for i in range(8):
            self.assertEqual(self.g.consume(pose(1.45+i*.05,left=guard)), [])
        self.assertNotIn('left',self.g.blocking_hands)
        second=[]
        for i in range(6):
            second += self.g.consume(pose(1.85+i*.05,left=strike))
        self.assertEqual(second,['left'])

    def test_high_guard_forward_down_hold_or_partial_return_does_not_repeat(self):
        guard=(.05,-.35,.05,.95)
        strike=(.05,.10,.05,.40)
        for t in (1.,1.05,1.10):
            self.g.consume(pose(t,left=guard))
        first=[]
        for i in range(6):
            first += self.g.consume(pose(1.15+i*.05,left=strike))
        self.assertEqual(first,['left'])
        for i in range(10):
            self.assertEqual(self.g.consume(pose(1.45+i*.05,left=strike)), [])
        # The arm projection has recovered, but the wrist has not returned
        # close to its high guard or reversed enough away from the thrust.
        partial=(.05,-.05,.05,.95)
        for i in range(10):
            self.assertEqual(self.g.consume(pose(1.95+i*.05,left=partial)), [])
        self.assertIn('left',self.g.blocking_hands)
        for i in range(10):
            self.assertEqual(self.g.consume(pose(2.45+i*.05,left=strike)), [])

    def test_two_hand_asynchronous_observations_emit_one_palm(self):
        self.seed()
        out = []
        frames = [pose(1.15, left=THRUST),
                  pose(1.20, left=THRUST, right=THRUST, missing=(10,)),
                  pose(1.25, left=THRUST, right=THRUST),
                  pose(1.30, left=THRUST, right=THRUST),
                  pose(1.35, left=THRUST, right=THRUST),
                  pose(1.40, left=THRUST, right=THRUST),
                  pose(1.45, left=THRUST, right=THRUST)]
        for frame in frames:
            out += self.g.consume(frame)
        self.assertEqual(out, ['palm'])
        self.assertEqual(self.g.blocking_hands, {'left','right'})

    def test_no_firing_on_missing_current_wrist(self):
        self.seed()
        self.g.consume(pose(1.15, left=THRUST))
        self.g.consume(pose(1.20, left=THRUST))
        self.g.consume(pose(1.25, left=THRUST))
        self.assertEqual(self.g.consume(pose(1.35, left=THRUST, missing=(9,))), [])
        self.assertEqual(self.g.consume(pose(1.4, left=THRUST)), ['left'])

    def test_long_missing_hand_clears_candidates_without_interpolation(self):
        self.seed()
        self.g.consume(pose(1.15, left=THRUST))
        for t in (1.2, 1.3, 1.4):
            self.assertEqual(self.g.consume(pose(t, left=THRUST, missing=(9,))), [])
        self.assertNotIn('left', self.g.blocking_hands)
        self.assertEqual(self.g.consume(pose(1.45, left=THRUST)), [])

    def test_reappearance_obeys_same_200ms_grace_as_missing_frames(self):
        for returned,expected in ((1.44,['left']),(1.45,['left']),(1.46,[])):
            with self.subTest(returned=returned):
                self.g=FrontGesture();self.seed()
                for t in (1.15,1.20,1.25):self.g.consume(pose(t,left=THRUST))
                self.assertEqual(self.g.consume(pose(1.35,left=THRUST,missing=(9,))),[])
                self.assertEqual(self.g.consume(pose(returned,left=THRUST)),expected)

    def test_real_upward_arc_is_not_front_punch(self):
        self.seed()
        out = self.g.consume(pose(1.15, right=(.05,.3,.05,.6)))
        out += self.g.consume(pose(1.20, right=(.05,-.35,.05,.3)))
        out += self.g.consume(pose(1.25, right=(.05,-1.0,.05,.1)))
        self.assertEqual(out, [])
        self.assertNotIn('right', self.g.blocking_hands)

    def test_overhead_raise_and_lower_is_not_palm(self):
        self.seed()
        out = []
        for i,y in enumerate((.5,0.,-.5,-1.,-1.5,-1.5,-1.,-.5,0.,.5,1.)):
            arm=(.2,y,.4,y+.3)
            out += self.g.consume(pose(1.15+i*.05,left=arm,right=arm))
        self.assertEqual(out, [])

    def test_static_guard_with_small_landmark_noise(self):
        rng=random.Random(42)
        for i in range(200):
            arm=tuple(v+rng.uniform(-.025,.025) for v in IDLE)
            self.assertEqual(self.g.consume(pose(1.+i*.04,left=arm,right=arm)), [])

    def test_entering_with_extended_hands_never_strikes(self):
        for i in range(30):
            self.assertEqual(self.g.consume(pose(1.+i*.04,left=THRUST,right=THRUST)), [])

    def test_isolated_elbow_collapse_does_not_strike(self):
        self.seed()
        self.assertEqual(self.g.consume(pose(1.15,left=THRUST)), [])
        for t in (1.2,1.25,1.3,1.4):
            self.assertEqual(self.g.consume(pose(t)), [])

    def test_coordinated_face_guard_blocks_legacy_attacks(self):
        # Bent elbows stay down as both fists come up alongside the face.
        self.seed()
        raised=(.10,-.35,.25,1.10)
        events=[]
        for t,left,right in ((1.15,raised,IDLE), (1.20,raised,raised),
                             (1.25,raised,raised), (1.30,raised,raised),
                             (1.40,raised,raised), (1.50,raised,raised)):
            events += self.g.consume(pose(t,left=left,right=right))
        self.assertEqual(events,[])
        self.assertEqual(self.g.guard_hands,{'left','right'})
        self.assertEqual(self.g.blocking_hands,{'left','right'})
        self.assertFalse(self.g.pending)

    def test_fast_complete_punch_survives_return_during_pair_window(self):
        self.seed()
        events=[]
        # Two observed thrust poses followed by a full measured return. The
        # short pairing window must not erase a completed single-arm stroke.
        for t,arm in ((1.15,THRUST),(1.18,THRUST),(1.23,IDLE),
                      (1.28,IDLE),(1.32,IDLE)):
            events += self.g.consume(pose(t,left=arm))
        self.assertEqual(events,['left'])

    def test_completed_punch_still_requires_a_current_observation(self):
        self.seed()
        for t,arm in ((1.15,THRUST),(1.18,THRUST),(1.23,IDLE)):
            self.assertEqual(self.g.consume(pose(t,left=arm)),[])
        self.assertEqual(self.g.consume(pose(1.33,missing=(9,))),[])
        self.assertEqual(self.g.consume(pose(1.38)),['left'])

    def test_short_observed_high_guard_return_rearms_without_hold(self):
        guard=(.05,-.35,.05,.95)
        strike=(.05,.10,.05,.40)
        for t in (1.,1.05,1.10):
            self.g.consume(pose(t,left=guard))
        events=[]
        for i in range(6):
            events += self.g.consume(pose(1.15+i*.05,left=strike))
        self.assertEqual(events,['left'])
        self.assertEqual(self.g.consume(pose(1.45,left=guard)),[])
        self.assertEqual(self.g.consume(pose(1.50,left=guard)),[])
        self.assertNotIn('left',self.g.blocking_hands)
        events=[]
        for i in range(6):
            events += self.g.consume(pose(1.55+i*.05,left=strike))
        self.assertEqual(events,['left'])

    def test_compact_depth_thrust_needs_repeated_two_segment_shortening(self):
        chamber=(0.,.72,.35,1.22)
        compact=(.10,.75,.15,.88)
        compact_second=(.10,.75,.15,.94)
        for t in (1.,1.05,1.10):
            self.assertEqual(self.g.consume(pose(t,left=chamber)),[])
        events=[]
        for t,arm in ((1.15,compact),(1.18,compact_second),(1.23,chamber),
                      (1.28,chamber),(1.32,chamber)):
            events += self.g.consume(pose(t,left=arm))
        self.assertEqual(events,['left'])

    def test_one_compact_landmark_collapse_is_not_a_depth_thrust(self):
        chamber=(0.,.72,.35,1.22)
        compact=(.10,.75,.15,.88)
        for t in (1.,1.05,1.10):
            self.g.consume(pose(t,left=chamber))
        self.assertEqual(self.g.consume(pose(1.15,left=compact)),[])
        for i in range(10):
            self.assertEqual(self.g.consume(pose(1.20+i*.04,left=chamber)),[])

    def test_lowering_face_guard_is_not_forward_down_thrust_or_palm(self):
        guard=(.02,-.10,-.45,1.65)
        chamber=(.02,.80,-.30,1.25)
        for t in (1.,1.05,1.10):
            self.assertEqual(self.g.consume(pose(t,left=guard,right=guard)),[])
        for i in range(1,9):
            blend=i/8.
            arm=tuple(a+(b-a)*blend for a,b in zip(guard,chamber))
            self.assertEqual(self.g.consume(pose(1.10+i*.05,left=arm,right=arm)),[])
        for t in (1.55,1.60):
            self.assertEqual(self.g.consume(pose(t,left=chamber,right=chamber)),[])
        # The subsequent simultaneous thrust is one palm, with no prematurely
        # spent single-hand lock left behind by lowering the guard.
        events=[]
        for i in range(6):
            events += self.g.consume(pose(1.65+i*.05,left=THRUST,right=THRUST))
        self.assertEqual(events,['palm'])

    def test_observed_face_guard_rearms_after_previous_low_chamber_punch(self):
        self.seed()
        self.assertEqual(self.punch('right'),['right'])
        guard=(.10,-.35,.25,1.10)
        for t in (1.45,1.50):
            self.assertEqual(self.g.consume(pose(t,left=guard,right=guard)),[])
        self.assertNotIn('right',self.g.locked)
        events=[]
        for i in range(6):
            events += self.g.consume(pose(1.55+i*.05,left=guard,right=(.05,.10,.05,.40)))
        self.assertEqual(events,['right'])

    def test_return_from_upward_arc_cannot_reuse_pre_raise_chamber(self):
        self.seed()
        events=[]
        for t,arm in ((1.15,(.05,.30,.05,.60)),
                      (1.20,(.05,-.40,.05,.15)),
                      (1.25,(.05,-1.00,.05,.00)),
                      (1.30,(.05,-.78,.05,.19)),
                      (1.35,(.05,-.10,.05,.60)),
                      (1.40,(.05,.34,.05,.80)),
                      (1.45,(.05,.91,.05,1.00)),
                      (1.50,IDLE),(1.55,IDLE),(1.60,IDLE)):
            events += self.g.consume(pose(t,right=arm))
        self.assertEqual(events,[])

    def test_repeated_elbow_collapse_with_fixed_wrist_never_attacks(self):
        from engine import Gesture
        base=(.10,-.35,.25,1.10)
        collapse=(.10,-.35,.10,.50)
        for recognizer in (FrontGesture,Gesture):
            with self.subTest(recognizer=recognizer.__name__):
                g=recognizer();events=[]
                for t,arm in ((1.,base),(1.05,base),(1.10,base),
                              (1.15,collapse),(1.20,collapse),
                              (1.25,base),(1.30,base),(1.35,base)):
                    events += g.consume(pose(t,left=arm))
                self.assertEqual(events,[])

    def test_elbow_collapse_with_only_small_wrist_noise_never_attacks(self):
        from engine import Gesture
        base=(.10,-.35,.25,1.10)
        for recognizer in (FrontGesture,Gesture):
            with self.subTest(recognizer=recognizer.__name__):
                g=recognizer();events=[]
                for t,arm in ((1.,base),(1.05,base),(1.10,base),
                              (1.15,(.12,-.33,.10,.50)),
                              (1.20,(.08,-.37,.10,.50)),
                              (1.25,base),(1.30,base),(1.35,base)):
                    events += g.consume(pose(t,left=arm))
                self.assertEqual(events,[])

    def test_retracting_left_then_extending_right_stay_two_punches(self):
        self.seed()
        returning=(.05,.50,.05,.75)
        events=[]
        for t,left,right in ((1.15,THRUST,IDLE),(1.20,THRUST,IDLE),
                             (1.25,returning,THRUST),(1.30,IDLE,THRUST),
                             (1.35,IDLE,THRUST),(1.40,IDLE,THRUST),
                             (1.45,IDLE,THRUST),(1.50,IDLE,THRUST)):
            events += self.g.consume(pose(t,left=left,right=right))
        self.assertEqual(events,['left','right'])

    def test_other_bent_arm_chamber_adjustment_does_not_add_a_strike(self):
        chamber=(-.30,1.10,-.45,1.05)
        adjusted=(-.30,.80,-.35,.73)
        for t in (1.,1.05,1.10):
            self.assertEqual(self.g.consume(pose(t,right=chamber)),[])
        events=[]
        for i in range(8):
            events += self.g.consume(pose(1.15+i*.05,left=THRUST,right=adjusted))
        self.assertEqual(events,['left'])

    def test_simultaneous_fast_thrust_and_return_preserves_one_palm(self):
        self.seed()
        events=[]
        for t,arm in ((1.15,THRUST),(1.18,THRUST),(1.23,IDLE),
                      (1.28,IDLE),(1.32,IDLE)):
            events += self.g.consume(pose(t,left=arm,right=arm))
        self.assertEqual(events,['palm'])

    def test_invalid_and_repeated_frames_do_not_advance_candidate(self):
        self.seed()
        frame=pose(1.15,left=THRUST)
        self.g.consume(frame)
        for _ in range(20):
            self.assertEqual(self.g.consume(frame), [])
        self.assertEqual(self.g.pending['left']['frames'],1)
        self.assertEqual(self.g.consume(None), [])
        self.assertFalse(self.g.blocking_hands)

    def test_new_pid_and_large_time_gap_clear_input(self):
        for gap,pid in ((.3,1),(.05,2)):
            with self.subTest(gap=gap,pid=pid):
                self.setUp();self.seed()
                self.g.consume(pose(1.15,left=THRUST))
                self.assertEqual(self.g.consume(pose(1.15+gap,left=THRUST,pid=pid)), [])
                self.assertFalse(self.g.blocking_hands)

    def test_continuous_observed_weak_shoulder_can_be_supported(self):
        self.g.consume(pose(1.))
        frame=pose(1.1)
        frame['points17'][6][2]=.15
        original=list(frame['points17'][6])
        self.assertEqual(self.g.consume(frame), [])
        self.assertEqual(self.g.supported_shoulders, {6})
        self.assertEqual(frame['points17'][6],original)
        self.assertEqual(self.g.trusted_shoulders[6]['t'],1.)

    def test_entering_with_weak_shoulder_is_not_supported(self):
        frame=pose(1.)
        frame['points17'][6][2]=.15
        self.g.consume(frame)
        self.assertFalse(self.g.supported_shoulders)

    def test_weak_shoulder_does_not_refresh_trusted_anchor(self):
        self.g.consume(pose(1.))
        for t in (1.1,1.2,1.3):
            frame=pose(t);frame['points17'][6][2]=.15
            self.g.consume(frame)
            self.assertEqual(self.g.supported_shoulders,{6})
        frame=pose(1.35);frame['points17'][6][2]=.15
        self.g.consume(frame)
        self.assertFalse(self.g.supported_shoulders)
        self.assertEqual(self.g.trusted_shoulders[6]['t'],1.)

    def test_weak_shoulder_jump_or_width_change_is_rejected(self):
        for dx,dy in ((0.,.14),(-.1,0.)):
            with self.subTest(dx=dx,dy=dy):
                self.setUp();self.g.consume(pose(1.))
                frame=pose(1.1);p=frame['points17'][6]
                p[0]+=dx;p[1]+=dy;p[2]=.15
                self.g.consume(frame)
                self.assertFalse(self.g.supported_shoulders)

    def test_weak_shoulder_needs_other_shoulder_and_same_arm(self):
        for index in (5,8,10):
            with self.subTest(index=index):
                self.setUp();self.g.consume(pose(1.))
                frame=pose(1.1);frame['points17'][6][2]=.15
                frame['points17'][index][2]=.15
                self.g.consume(frame)
                self.assertFalse(self.g.supported_shoulders)

    def test_confidence_at_or_below_hard_floor_is_never_supported(self):
        for confidence in (.12,.1,0.):
            with self.subTest(confidence=confidence):
                self.setUp();self.g.consume(pose(1.))
                frame=pose(1.1);frame['points17'][6][2]=confidence
                self.g.consume(frame)
                self.assertFalse(self.g.supported_shoulders)


if __name__ == '__main__':
    unittest.main()
