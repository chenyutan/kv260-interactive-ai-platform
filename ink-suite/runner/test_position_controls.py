"""Absolute body lanes, including fresh-source and geometry safety boundaries."""
import copy
import unittest
from .controls import PoseControls
from .test_core import pose


class PositionControlsTests(unittest.TestCase):
    def setUp(self):
        self.control=PoseControls();self.now=10.;self.sequence=0

    def sample(self,dt=.05,packet=None,phase='running',**kwargs):
        self.now+=dt;self.sequence+=1
        packet=pose(self.now,self.sequence,**kwargs) if packet is None else packet
        return self.control.update(packet,self.now,phase=phase)

    def prime(self,**kwargs):
        for _ in range(9):out=self.sample(**kwargs)
        self.assertEqual(out['calibration'],'ready')

    def step(self,positions,**kwargs):
        # Hold the final clear position across the lane confirmation window.
        return [self.sample(body_x=x,**kwargs) for x in (*positions,positions[-1],positions[-1])]

    def centre(self,**kwargs):
        for _ in range(3):out=self.sample(**kwargs)
        return out

    def assert_neutral(self,out):
        self.assertIsNone(out['lane_target'])
        self.assertEqual(out['move'],0)

    def test_body_side_centre_opposite_side_targets_all_three_lanes(self):
        self.prime()
        self.assertEqual(self.step((.02,.04,.06,.08))[-1]['lane_target'],0)
        middle=self.centre(body_x=0.)
        self.assertEqual(middle['lane_target'],1)
        self.assertEqual(self.step((-.02,-.04,-.06,-.08))[-1]['lane_target'],2)
        self.assertEqual(self.centre()['lane_target'],1)

    def test_return_to_centre_needs_brief_fresh_confirmation(self):
        self.prime();self.step((.02,.04,.06,.08))
        before=self.now
        middle=self.centre(dt=1/30,body_x=0.)
        self.assertLess(self.now-before,.11)
        self.assertEqual(middle['lane_target'],1)

    def test_held_position_reports_same_target_without_relative_repeat(self):
        self.prime();self.step((.02,.04,.06,.08))
        held=[self.sample(body_x=.08) for _ in range(100)]
        self.assertTrue(all(out['lane_target']==0 for out in held))
        self.assertFalse(any(out['move'] for out in held))

    def test_initial_neutral_can_be_anywhere_on_screen(self):
        self.prime(body_x=.10)
        self.assertEqual(self.centre(body_x=.10)['lane_target'],1)
        self.assertEqual(self.step((.12,.14,.16,.18))[-1]['lane_target'],0)
        self.assertEqual(self.centre(body_x=.10)['lane_target'],1)

    def test_mirroring_and_nonmirroring_have_explicit_lane_targets(self):
        for mirrored,expected in ((True,0),(False,2)):
            with self.subTest(mirrored=mirrored):
                self.setUp();self.control=PoseControls(mirrored=mirrored);self.prime()
                self.assertEqual(self.step((.02,.04,.06,.08))[-1]['lane_target'],expected)

    def test_hysteresis_does_not_flicker_near_side_boundary(self):
        self.prime()
        self.assertEqual(self.step((.02,.04,.08))[-1]['lane_target'],0)
        for x in (.072,.075,.070,.052,.072):self.assertEqual(self.sample(body_x=x)['lane_target'],0)
        self.assertEqual(self.centre(body_x=.028)['lane_target'],1)
        for x in (.03,.04,.045,.050,.052):self.assertEqual(self.sample(body_x=x)['lane_target'],1)

    def test_crouching_side_step_still_selects_lane_and_centre(self):
        self.prime()
        rows=[self.sample(body_x=x,body_down=y) for x,y in
              ((.02,.04),(.04,.08),(.06,.12),(.08,.14),(.08,.16),(.08,.16),(.08,.16),(.08,.16))]
        self.assertEqual(rows[-1]['lane_target'],0)
        self.assertTrue(any(out['slide'] for out in rows))
        self.assertEqual(self.centre(body_x=0.,body_down=.16)['lane_target'],1)

    def test_airborne_side_step_keeps_horizontal_position_control(self):
        self.prime();self.assertTrue(self.sample(body_down=-.045)['jump'])
        side=self.step((.02,.04,.06,.08),body_down=-.045)
        self.assertEqual(side[-1]['lane_target'],0)
        self.assertEqual(self.centre(body_down=-.045)['lane_target'],1)
        self.assertFalse(any(row['jump'] for row in side))

    def test_shoulders_alone_cannot_select_a_side_or_return_from_one(self):
        self.prime()
        p=pose(self.now+.05,self.sequence+1)
        for k in (5,6):p['points17'][k][0]+=.08
        self.assertEqual(self.sample(packet=p)['lane_target'],1)
        self.step((.02,.04,.06,.08))
        p=pose(self.now+.05,self.sequence+1,body_x=.08)
        for k in (5,6):p['points17'][k][0]-=.08
        self.assertEqual(self.sample(packet=p)['lane_target'],0)

    def test_short_missing_pose_and_first_recovery_have_no_old_target(self):
        self.prime();self.step((.02,.04,.06,.08));anchor=self.control.body.anchor
        out=self.control.update(None,self.now+.1);self.assert_neutral(out)
        out=self.sample(dt=.15,body_x=.08)
        self.assertEqual(out['reason'],'pose_recovered');self.assert_neutral(out)
        self.assertEqual(self.control.body.anchor,anchor)
        self.assertEqual(self.centre(body_x=0.)['lane_target'],1)

    def test_duplicate_stale_future_and_invalid_source_never_replay_target(self):
        self.prime();self.step((.02,.04,.06,.08));p=pose(self.now+.05,self.sequence+1,body_x=.08)
        out=self.sample(packet=p);self.assertEqual(out['lane_target'],0)
        for dt in (.02,.10,.21):
            self.assert_neutral(self.control.update(p,self.now+dt))
        self.assert_neutral(self.control.update(pose(self.now+999,9999,body_x=-.08),self.now+.3))
        self.assert_neutral(self.control.update({},self.now+.31))

    def test_invalid_hip_and_geometry_outlier_never_output_a_target(self):
        self.prime()
        p=pose(self.now+.05,self.sequence+1,body_x=.04);p['points17'][11][2]=0.
        self.assert_neutral(self.sample(packet=p))
        self.assert_neutral(self.sample(body_x=.17))
        self.assert_neutral(self.sample())
        self.assertEqual(self.sample()['lane_target'],1)

    def test_title_result_and_recalibration_never_output_position_targets(self):
        self.prime()
        for phase in ('title','over'):
            out=self.sample(phase=phase,body_x=.06);self.assert_neutral(out)
        self.control.reset()
        self.assert_neutral(self.sample(body_x=.06))
        for _ in range(8):out=self.sample(body_x=.06)
        self.assertEqual(out['lane_target'],1)

    def test_no_wrists_and_ignored_identity_metadata_do_not_disable_position(self):
        self.prime()
        for x in (.02,.04,.06,.08,.08,.08):
            p=pose(self.now+.05,self.sequence+1,body_x=x)
            p['points17'][9][2]=p['points17'][10][2]=0.
            p['player_tracking']=dict(input_allowed=False,identity='unrelated',epoch=99)
            out=self.sample(packet=p)
        self.assertEqual(out['lane_target'],0)


if __name__=='__main__':unittest.main()
