"""Regression for real take-off preparation and brief skeleton blind spots."""
import unittest
from .controls import PoseControls
from .test_core import pose


class BodyResilienceTests(unittest.TestCase):
    def setUp(self):
        self.control=PoseControls();self.t=10.;self.seq=0

    def sample(self,dt=.05,**kw):
        self.t+=dt;self.seq+=1
        return self.control.update(pose(self.t,self.seq,**kw),self.t,phase='running')

    def prime(self,n=8,dt=.05,**kw):
        for _ in range(n):out=self.sample(dt,**kw)
        self.assertEqual(out['calibration'],'ready')

    @staticmethod
    def pulses(row):return row['move'],row['jump'],row['slide'],row['confirm']

    def test_knee_dip_then_real_takeoff_is_one_jump(self):
        self.prime()
        rows=[self.sample(body_down=y) for y in (.01,.025,.04,.025,0.,-.02,-.045,-.065,-.05)]
        self.assertEqual(sum(r['jump'] for r in rows),1)

    def test_deep_crouch_return_only_never_becomes_jump(self):
        self.prime()
        rows=[self.sample(body_down=y) for y in (.02,.04,.06,.08,.08,.06,.04,.02,0.,0.)]
        self.assertFalse(any(r['jump'] for r in rows))

    def test_three_steps_use_brief_confirmation_without_a_long_neutral_hold(self):
        self.prime()
        rows=[self.sample(body_x=x) for x in (.02,.04,.06,.08,.08,.08,.06,.03,0.,0.,0.,-.03,-.06,-.08,-.08,-.08,-.06,-.03,0.,0.,0.,.03,.06,.08,.08,.08)]
        # Each side position and each return to the centre has its own
        # absolute target after brief confirmation, with no relative extra step.
        self.assertEqual([r['lane_target'] for r in rows if r['move']],[0,1,2,1,0])
        self.assertFalse(any(self.sample(body_x=.08)['move'] for _ in range(30)))

    def test_one_missing_hip_mid_step_keeps_neutral_and_completes_step(self):
        self.prime();anchor=self.control.body.anchor
        self.sample(body_x=.02);self.sample(body_x=.03)
        self.t+=.05;self.seq+=1;p=pose(self.t,self.seq,body_x=.04);p['points17'][11][2]=0
        self.assertFalse(any(self.pulses(self.control.update(p,self.t,phase='running'))))
        recovered=self.sample(body_x=.045)
        self.assertEqual(recovered['reason'],'pose_recovered')
        self.assertEqual(self.control.body.anchor,anchor)
        rows=[self.sample(body_x=x) for x in (.055,.065,.08,.08,.08)]
        self.assertEqual([r['move'] for r in rows if r['move']],[-1])

    def test_short_source_gap_does_not_relearn_shifted_neutral(self):
        self.prime();anchor=self.control.body.anchor
        self.t+=1.2;self.seq+=1
        out=self.control.update(pose(self.t,self.seq,body_x=.07),self.t,phase='running')
        self.assertFalse(any(self.pulses(out)));self.assertEqual(out['reason'],'pose_recovered')
        self.assertEqual(self.control.body.anchor,anchor)
        self.sample(body_x=.08);self.sample(body_x=.08)
        self.assertEqual(self.sample(body_x=.08)['move'],-1)

    def test_missing_pose_never_replays_an_already_fired_held_lane(self):
        self.prime()
        self.assertEqual(sum(abs(self.sample(body_x=x)['move']) for x in (.02,.04,.06,.08,.08,.08)),1)
        self.control.update(None,self.t+.1)
        self.assertFalse(any(self.pulses(self.sample(dt=.15,body_x=.08))))
        self.assertFalse(any(self.sample(body_x=.08)['move'] for _ in range(30)))

    def test_roi_history_boundary_keeps_original_neutral(self):
        self.prime();anchor=self.control.body.anchor
        self.t+=.05;self.seq+=1;p=pose(self.t,self.seq,body_x=.025)
        p['roi_selection']={'motion_history_discontinuity':True}
        out=self.control.update(p,self.t,phase='running')
        self.assertEqual(out['reason'],'pose_recovered');self.assertFalse(any(self.pulses(out)))
        self.assertEqual(self.control.body.anchor,anchor)
        self.assertEqual(sum(abs(self.sample(body_x=x)['move']) for x in (.04,.06,.08,.08,.08)),1)

    def test_geometry_outlier_is_not_a_new_neutral(self):
        self.prime();anchor=self.control.body.anchor
        out=self.sample(body_x=.17)
        self.assertEqual(out['reason'],'body_changed');self.assertFalse(any(self.pulses(out)))
        self.assertEqual(self.control.body.anchor,anchor)
        self.assertFalse(any(self.pulses(self.sample())))
        self.assertEqual(sum(abs(self.sample(body_x=x)['move']) for x in (.02,.04,.06,.08,.08,.08)),1)

    def test_stable_roi_scale_reconfirmation_does_not_learn_side_endpoint(self):
        self.prime();anchor=self.control.body.anchor
        rows=[]
        for _ in range(8):
            self.t+=.05;self.seq+=1;p=pose(self.t,self.seq,body_x=.06)
            for joint in (5,6):p['points17'][joint][0]=.56+(p['points17'][joint][0]-.56)*1.25
            rows.append(self.control.update(p,self.t,phase='running'))
        self.assertEqual(self.control.body.anchor[0],anchor[0]);self.assertEqual(self.control.body.anchor[2],anchor[2])
        self.assertFalse(any(r['jump'] for r in rows))

    def test_five_fps_stream_calibrates_and_detects_prepared_takeoff(self):
        self.prime(n=8,dt=.2)
        rows=[self.sample(dt=.2,body_down=y) for y in (.015,.035,.015,-.02,-.05,-.065)]
        self.assertEqual(sum(r['jump'] for r in rows),1)

    def test_long_gap_still_requires_fresh_calibration(self):
        self.prime();self.t+=3.;self.seq+=1
        out=self.control.update(pose(self.t,self.seq,body_x=.07,body_down=-.06),self.t,phase='running')
        self.assertEqual(out['calibration'],'calibrating');self.assertFalse(any(self.pulses(out)))

    def test_capture_counter_restart_is_not_rejected_until_old_sequence_catches_up(self):
        for _ in range(24):
            self.t+=.05;self.seq+=1;p=pose(self.t,self.seq);p['pose_count']=100+self.seq
            self.control.update(p,self.t,phase='running')
        self.t+=.05;p=pose(self.t,1,body_x=.06);p['pose_count']=200
        out=self.control.update(p,self.t,phase='running')
        self.assertEqual(out['calibration'],'calibrating');self.assertFalse(any(self.pulses(out)))
        self.assertEqual(self.control.sequence,1)

    def test_invalid_torso_cannot_poison_the_last_reliable_shoulder_geometry(self):
        for problem in ('missing_hip','implausible_torso'):
            with self.subTest(problem=problem):
                self.setUp();self.prime();reliable=self.control._body_geometry
                self.t+=.05;self.seq+=1;p=pose(self.t,self.seq)
                for joint in (5,6):p['points17'][joint][0]=.5+(p['points17'][joint][0]-.5)*.705
                if problem=='missing_hip':p['points17'][11][2]=0.
                else:
                    for joint in (11,12):p['points17'][joint][1]=.39
                rejected=self.control.update(p,self.t,phase='running')
                self.assertIn(rejected['reason'],('missing_hips','implausible_torso'))
                self.assertEqual(self.control._body_geometry,reliable)
                recovered=self.sample()
                self.assertEqual(recovered['reason'],'pose_recovered')
                self.assertTrue(recovered['valid']);self.assertEqual(recovered['calibration'],'ready')
                self.assertFalse(any(self.pulses(recovered)))


if __name__=='__main__':unittest.main()
