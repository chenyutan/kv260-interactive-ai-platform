"""Light active side steps and a local guard against continuous return rebound."""
import unittest
from .controls import PoseControls
from .test_core import pose


class LaneReturnGuardTests(unittest.TestCase):
    def setUp(self):
        self.control=PoseControls();self.now=10.;self.sequence=0
        for _ in range(12):self.sample(0.)

    def sample(self,offset,dt=.05,edit=None):
        self.now+=dt;self.sequence+=1
        p=pose(self.now,self.sequence,body_x=-offset*.16)
        if edit:edit(p)
        return self.control.update(p,self.now,phase='running')

    def hold(self,offset,count=8):return [self.sample(offset)for _ in range(count)]

    def returned(self):
        self.hold(.65)
        for offset in (.4,.2,0.,-.15,-.30):self.sample(offset)
        self.assertEqual(self.control.body.lane_target,1)
        self.assertEqual(self.control.body.return_from_lane,2)

    def test_light_side_steps_work_both_directions_from_initial_centre(self):
        for offset,target in ((-.38,0),(.38,2)):
            with self.subTest(target=target):
                self.setUp();rows=self.hold(offset)
                self.assertEqual([r['lane_target']for r in rows[:3]],[1,1,target])
                self.assertEqual(sum(r['move']!=0 for r in rows),1)

    def test_continuous_shallow_return_rebound_does_not_expire_into_a_lane(self):
        self.returned()
        rows=self.hold(-.38,100)
        self.assertFalse(any(row['lane_target']==0 for row in rows))
        self.assertFalse(any(row['move']for row in rows))

    def test_short_gap_preserves_return_direction_but_clears_confirmation(self):
        self.returned();self.hold(-.38,10)
        self.now+=.05
        self.assertIsNone(self.control.update(None,self.now,phase='running')['lane_target'])
        self.assertEqual(self.control.body.return_from_lane,2)
        self.assertIsNone(self.control.body.lane_candidate)
        self.assertEqual(len(self.control.body.return_centre),0)
        self.assertIsNone(self.sample(-.38)['lane_target'])
        self.assertFalse(any(row['lane_target']==0 for row in self.hold(-.38,15)))

    def test_duplicate_packets_cannot_create_a_settled_centre(self):
        self.returned();self.sample(.1)
        p=pose(self.now,self.sequence,body_x=-.1*.16)
        for delay in (.05,.10,.20):
            out=self.control.update(p,self.now+delay,phase='running')
            self.assertIsNone(out['lane_target']);self.assertEqual(out['move'],0)
        self.assertEqual(self.control.body.return_from_lane,2)
        self.assertEqual(len(self.control.body.return_centre),1)

    def test_single_clear_cross_frame_still_requires_fresh_confirmation(self):
        self.returned();self.sample(-.52)
        self.assertFalse(any(row['lane_target']==0 for row in self.hold(-.38,15)))

    def test_settled_wide_centre_releases_a_light_opposite_step(self):
        self.returned();self.hold(.20)
        self.assertIsNone(self.control.body.return_from_lane)
        self.assertEqual(self.hold(-.38)[-1]['lane_target'],0)

    def test_same_side_light_reentry_does_not_need_a_centre_hold(self):
        self.returned()
        for offset in (-.1,.1,.3):self.sample(offset)
        self.assertEqual(self.hold(.38)[-1]['lane_target'],2)

    def test_clear_opposite_cross_does_not_need_a_centre_hold(self):
        self.returned()
        self.assertEqual(self.hold(-.60)[-1]['lane_target'],0)

    def test_scale_reconfirmation_clears_pending_settle_but_keeps_return_direction(self):
        self.returned();self.sample(.1)
        self.assertEqual(len(self.control.body.return_centre),1)
        def scale(p):
            for k in (5,6):p['points17'][k][0]=.5+(p['points17'][k][0]-.5)*1.25
        out=self.sample(.1,edit=scale)
        self.assertEqual(out['calibration'],'calibrating')
        self.assertIsNone(out['lane_target'])
        self.assertEqual(len(self.control.body.return_centre),0)
        self.assertEqual(self.control.body.return_from_lane,2)

    def test_one_centre_frame_after_gap_does_not_release_a_protected_cross(self):
        self.returned();self.sample(.1)
        self.now+=.05;self.control.update(None,self.now,phase='running')
        self.assertIsNone(self.sample(.1)['lane_target'])
        self.sample(.1)
        self.assertFalse(any(row['lane_target']==0 for row in self.hold(-.38,10)))


if __name__=='__main__':unittest.main()
