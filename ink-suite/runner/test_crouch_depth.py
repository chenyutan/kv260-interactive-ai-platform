"""Clear crouch confirmation must not turn ordinary jump preparation into slide."""
import unittest
from .controls import PoseControls
from .test_core import pose


class CrouchDepthTests(unittest.TestCase):
    def setUp(self):
        self.control=PoseControls();self.now=10.;self.sequence=0

    def sample(self,dt=.05,packet=None,**kwargs):
        self.now+=dt;self.sequence+=1
        source=pose(self.now,self.sequence,**kwargs) if packet is None else packet
        return self.control.update(source,self.now,phase='running')

    def prime(self):
        for _ in range(9):out=self.sample()
        self.assertEqual(out['calibration'],'ready')

    def deep(self):
        return [self.sample(body_down=y) for y in (.04,.08,.12,.14,.16,.16,.16,.16)]

    def test_shallow_knee_dip_can_jump_without_a_slide(self):
        self.prime()
        rows=[self.sample(body_down=y) for y in (.02,.04,.06,.08,.06,.03,0.,-.02,-.05,-.065)]
        self.assertFalse(any(row['slide'] for row in rows))
        self.assertEqual(sum(row['jump'] for row in rows),1)

    def test_deep_but_brief_prejump_dip_does_not_slide(self):
        self.prime()
        rows=[self.sample(body_down=y) for y in (.04,.08,.12,.14,.16,.06,0.,-.05)]
        self.assertFalse(any(row['slide'] for row in rows))
        self.assertEqual(sum(row['jump'] for row in rows),1)

    def test_clear_sustained_crouch_fires_once_then_requires_upright_return(self):
        self.prime()
        rows=self.deep()
        self.assertFalse(any(row['slide'] for row in rows[:6]))
        self.assertEqual(sum(row['slide'] for row in rows),1)
        self.assertFalse(any(self.sample(body_down=.16)['slide'] for _ in range(20)))
        for _ in range(6):self.sample()
        self.assertEqual(sum(row['slide'] for row in self.deep()),1)

    def test_two_deep_frames_alone_are_insufficient_even_at_low_fps(self):
        self.prime()
        first=self.sample(dt=.2,body_down=.16)
        second=self.sample(dt=.2,body_down=.16)
        third=self.sample(dt=.2,body_down=.16)
        self.assertFalse(first['slide'] or second['slide'])
        self.assertTrue(third['slide'])

    def test_bending_only_shoulders_or_hips_never_requests_slide(self):
        for joints in ((5,6),(11,12)):
            with self.subTest(joints=joints):
                self.setUp();self.prime()
                for _ in range(10):
                    p=pose(self.now+.05,self.sequence+1)
                    for k in joints:p['points17'][k][1]+=.14
                    self.assertFalse(self.sample(packet=p)['slide'])

    def test_duplicate_packets_do_not_finish_the_crouch_confirmation(self):
        self.prime();first=self.sample(body_down=.16)
        packet=pose(self.now,self.sequence,body_down=.16)
        for dt in (.02,.06,.12,.19):
            self.assertFalse(self.control.update(packet,self.now+dt,phase='running')['slide'])
        self.assertFalse(first['slide'])
        self.assertFalse(self.sample(dt=.2,body_down=.16)['slide'])
        self.assertTrue(self.sample(body_down=.16)['slide'])

    def test_missing_observation_clears_pending_crouch_not_the_standing_anchor(self):
        self.prime();anchor=self.control.body.anchor
        self.sample(body_down=.16);self.sample(body_down=.16)
        self.control.update(None,self.now+.05,phase='running')
        recovered=self.sample(dt=.1,body_down=.16)
        self.assertEqual(recovered['reason'],'pose_recovered')
        self.assertEqual(self.control.body.anchor,anchor)
        self.assertFalse(recovered['slide'])
        rows=[self.sample(body_down=.16) for _ in range(5)]
        self.assertFalse(any(row['slide'] for row in rows[:4]))
        self.assertTrue(rows[4]['slide'])

    def test_leaving_deep_zone_clears_confirmation_without_disarming_jump(self):
        self.prime()
        self.sample(body_down=.16);self.sample(body_down=.16)
        self.assertFalse(self.sample(body_down=.06)['slide'])
        self.assertFalse(self.sample(body_down=.16)['slide'])
        self.assertFalse(self.sample(body_down=.16)['slide'])
        self.sample(body_down=0.)
        self.assertTrue(self.sample(body_down=-.05)['jump'])

    def test_landing_compression_does_not_start_a_pending_slide(self):
        self.prime();self.assertTrue(self.sample(body_down=-.05)['jump'])
        rows=[self.sample(body_down=y) for y in (-.065,-.02,.05,.10,.14,.16,.12,.04,0.)]
        self.assertFalse(any(row['slide'] for row in rows))
        self.assertIsNone(self.control.body.crouch_since)

    def test_clear_crouch_keeps_absolute_lane_target_while_waiting(self):
        self.prime()
        rows=[self.sample(body_down=y,body_x=x) for y,x in
              ((.04,.08),(.08,.08),(.12,.08),(.14,.08),(.16,.08),(.16,.08),(.16,.08),(.16,.08))]
        self.assertEqual(sum(row['slide'] for row in rows),1)
        self.assertTrue(all(row['lane_target']==0 for row in rows[2:]))

    def test_source_discontinuity_recovery_cannot_complete_old_confirmation(self):
        self.prime();self.sample(body_down=.16);self.sample(body_down=.16)
        p=pose(self.now+.05,self.sequence+1,body_down=.16)
        p['motion_history_discontinuity']=True
        recovered=self.sample(packet=p)
        self.assertEqual(recovered['reason'],'pose_recovered')
        self.assertFalse(recovered['slide']);self.assertIsNone(self.control.body.crouch_since)
        self.assertFalse(self.sample(body_down=.16)['slide'])

    def test_scale_reconfirmation_clears_pending_deep_crouch(self):
        self.prime();self.sample(body_down=.16);self.sample(body_down=.16)
        self.assertIsNotNone(self.control.body.crouch_since)
        p=pose(self.now+.05,self.sequence+1,body_down=.16)
        for k in (5,6):p['points17'][k][0]=.5+(p['points17'][k][0]-.5)*1.25
        out=self.sample(packet=p)
        self.assertEqual(out['calibration'],'calibrating')
        self.assertFalse(out['slide']);self.assertIsNone(self.control.body.crouch_since)
        self.assertEqual(self.control.body.crouch_observations,0)

    def test_expired_source_and_stream_reset_clear_pending_deep_crouch(self):
        for kind in ('stale','stream_reset'):
            with self.subTest(kind=kind):
                self.setUp();self.prime();self.sample(body_down=.16);self.sample(body_down=.16)
                p=pose(self.now+.05,self.sequence+1,body_down=.16);p[kind]=True
                out=self.sample(packet=p)
                self.assertFalse(out['slide']);self.assertIsNone(self.control.body.crouch_since)
                self.assertEqual(self.control.body.crouch_observations,0)

    def jump_and_wait_above_stance(self,frames=14):
        self.prime();self.assertTrue(self.sample(body_down=-.05)['jump'])
        rows=[self.sample(body_down=-.05) for _ in range(frames)]
        self.assertTrue(self.control.body.airborne)
        return rows

    def test_returned_deep_crouch_can_slide_without_releasing_jump_latch(self):
        self.jump_and_wait_above_stance()
        rows=[self.sample(body_down=.16) for _ in range(7)]
        self.assertEqual(sum(row['slide'] for row in rows),1)
        self.assertFalse(any(row['jump'] for row in rows))
        self.assertTrue(self.control.body.airborne)

    def test_elapsed_time_while_still_high_cannot_rearm_slide_or_jump(self):
        rows=self.jump_and_wait_above_stance(frames=50)
        self.assertFalse(any(row['slide'] or row['jump'] for row in rows))
        self.assertIsNone(self.control.body.crouch_since)
        self.assertTrue(self.control.body.airborne)

    def test_landing_interval_still_blocks_deep_compression_and_rebound(self):
        self.prime();self.assertTrue(self.sample(body_down=-.05)['jump'])
        rows=[self.sample(body_down=y) for y in (.04,.10,.16,.16,.16,.12,.02,-.05)]
        self.assertFalse(any(row['slide'] or row['jump'] for row in rows))
        self.assertTrue(self.control.body.airborne)

    def test_only_one_torso_group_returning_does_not_release_slide_guard(self):
        for joints in ((5,6),(11,12)):
            with self.subTest(joints=joints):
                self.setUp();self.jump_and_wait_above_stance()
                for _ in range(8):
                    p=pose(self.now+.05,self.sequence+1,body_down=-.05)
                    for k in joints:p['points17'][k][1]+=.16
                    out=self.sample(packet=p)
                    self.assertFalse(out['slide'] or out['jump'])
                self.assertTrue(self.control.body.airborne)
                self.assertIsNone(self.control.body.crouch_since)

    def test_rising_from_returned_deep_crouch_cannot_add_a_jump(self):
        self.jump_and_wait_above_stance()
        down=[self.sample(body_down=.16) for _ in range(7)]
        self.assertEqual(sum(row['slide'] for row in down),1)
        up=[self.sample(body_down=y) for y in (.12,.08,.04,0.,0.,0.,0.,0.)]
        self.assertFalse(any(row['jump'] for row in up))

    def test_slightly_shallower_clear_crouch_still_needs_full_confirmation(self):
        self.prime()
        rows=[self.sample(body_down=.11) for _ in range(6)]
        self.assertFalse(any(row['slide'] for row in rows[:4]))
        self.assertEqual(sum(row['slide'] for row in rows),1)


if __name__=='__main__':unittest.main()
