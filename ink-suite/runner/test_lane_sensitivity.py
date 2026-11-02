"""Brief target confirmation rejects return overshoot without a centre ritual."""
import unittest
from .controls import PoseControls
from .test_core import pose


class LaneSensitivityTests(unittest.TestCase):
    def setUp(self):
        self.control=PoseControls();self.stamp=10.;self.sequence=0
        for _ in range(12):self.sample()

    def sample(self,offset=0.,dt=.05,edit=None,**kwargs):
        self.stamp+=dt;self.sequence+=1
        p=pose(self.stamp,self.sequence,body_x=-offset*.16,**kwargs)
        if edit:edit(p)
        return self.control.update(p,self.stamp,phase='running')

    def hold(self,offset,count=6,**kwargs):
        return [self.sample(offset,**kwargs) for _ in range(count)]

    def test_small_opposite_overshoot_returns_to_centre_without_an_opposite_lane(self):
        self.assertEqual(self.hold(.65)[-1]['lane_target'],2)
        rows=[self.sample(x,dt=1/30) for x in (.45,.25,.05,-.15,-.34,-.40,-.34,-.20,-.05,0.)]
        rows+=self.hold(0.)
        self.assertFalse(any(row['lane_target']==0 for row in rows))
        self.assertEqual(rows[-1]['lane_target'],1)

    def test_one_large_return_overshoot_is_not_a_confirmed_side(self):
        self.hold(.65)
        rows=[self.sample(x,dt=1/30) for x in (.3,0.,-.3,-.52,-.3,0.)]
        rows+=self.hold(0.)
        self.assertFalse(any(row['lane_target']==0 for row in rows))
        self.assertEqual(rows[-1]['lane_target'],1)

    def test_centre_does_not_require_exact_original_stance(self):
        self.hold(.65)
        rows=self.hold(.20)
        self.assertEqual(rows[-1]['lane_target'],1)
        self.assertEqual(sum(row['move']!=0 for row in rows),1)

    def test_deliberate_opposite_side_needs_no_stop_in_centre(self):
        self.hold(.65)
        rows=[self.sample(x) for x in (.4,.1,-.2,-.65)]
        rows+=self.hold(-.65)
        self.assertEqual(rows[-1]['lane_target'],0)
        self.assertEqual(sum(row['lane_target']==0 and row['move']<0 for row in rows),1)

    def test_duplicate_packets_cannot_finish_a_lane_candidate(self):
        self.sample(.65)
        p=pose(self.stamp,self.sequence,body_x=-.65*.16)
        for delay in (.01,.02,.03,.04,.10):
            out=self.control.update(p,self.stamp+delay,phase='running')
            self.assertIsNone(out['lane_target']);self.assertEqual(out['move'],0)
        self.assertEqual(self.control.body.lane_target,1)
        self.assertEqual(self.control.body.lane_candidate_observations,1)

    def test_missing_frame_clears_pending_and_recovery_frame_is_neutral(self):
        self.sample(.65);anchor=self.control.body.anchor
        self.stamp+=.05
        self.assertIsNone(self.control.update(None,self.stamp,phase='running')['lane_target'])
        self.assertIsNone(self.control.body.lane_candidate)
        self.assertIsNone(self.sample(.65)['lane_target'])
        self.assertEqual(self.control.body.anchor,anchor)
        rows=self.hold(.65,3)
        self.assertEqual([row['lane_target'] for row in rows],[1,1,2])

    def test_source_gap_cannot_complete_a_pending_side(self):
        self.sample(.65)
        recovered=self.sample(.65,dt=.31)
        self.assertEqual(recovered['reason'],'pose_recovered')
        self.assertIsNone(recovered['lane_target'])
        rows=self.hold(.65,3)
        self.assertEqual([row['lane_target'] for row in rows],[1,1,2])

    def test_scale_change_clears_a_pending_side(self):
        self.sample(.65)
        def scale(p):
            for k in (5,6):p['points17'][k][0]=.5+(p['points17'][k][0]-.5)*1.25
        out=self.sample(.65,edit=scale)
        self.assertEqual(out['calibration'],'calibrating')
        self.assertIsNone(out['lane_target']);self.assertIsNone(self.control.body.lane_candidate)

    def test_shoulder_only_sway_cannot_start_or_finish_a_side(self):
        def shoulder(p):
            for k in (5,6):p['points17'][k][0]-=.65*.16
        for _ in range(8):self.assertEqual(self.sample(edit=shoulder)['lane_target'],1)

    def test_shoulders_returning_alone_cannot_return_from_a_side(self):
        self.hold(.65)
        def shoulder(p):
            for k in (5,6):p['points17'][k][0]+=.65*.16
        for _ in range(8):self.assertEqual(self.sample(.65,edit=shoulder)['lane_target'],2)

    def test_candidate_observations_must_be_consecutive_in_the_same_zone(self):
        for _ in range(8):
            self.assertEqual(self.sample(.65)['lane_target'],1)
            self.assertEqual(self.sample(.3)['lane_target'],1)
        self.hold(.65)
        for _ in range(8):
            self.assertEqual(self.sample(0.)['lane_target'],2)
            self.assertEqual(self.sample(.3)['lane_target'],2)

    def test_side_confirmation_is_bounded_at_five_to_thirty_fps(self):
        for fps in (5,15,20,30):
            with self.subTest(fps=fps):
                self.setUp();dt=1/fps;start=self.stamp
                rows=[self.sample(.38,dt=dt) for _ in range(8)]
                first=next(i for i,row in enumerate(rows) if row['lane_target']==2)
                delay=first*dt
                self.assertGreaterEqual(delay,.08-1e-6)
                self.assertLessEqual(delay,.08+dt+1e-6)
                self.assertEqual(sum(row['move']!=0 for row in rows),1)

    def test_jump_and_crouch_keep_active_side_selection(self):
        for down in (-.05,.16):
            with self.subTest(down=down):
                self.setUp()
                rows=[self.sample(x,body_down=down) for x in (.15,.3,.38,.38,.38,.38,.38,.38)]
                self.assertEqual(rows[-1]['lane_target'],2)
                self.assertEqual(sum(row['move']!=0 for row in rows),1)
                self.assertEqual(self.hold(.20,body_down=down)[-1]['lane_target'],1)


if __name__=='__main__':unittest.main()
