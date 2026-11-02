"""Body controls must follow the player, never a hand or an old identity edge."""
import copy
import math
import unittest
from .controls import PoseControls
from .test_core import pose


class BodyControlsTests(unittest.TestCase):
    def setUp(self):
        self.control=PoseControls();self.t=10.;self.seq=0

    def sample(self, *, dt=.05, packet=None, phase='running', **kw):
        self.t+=dt;self.seq+=1
        value=pose(self.t,self.seq,**kw) if packet is None else packet
        return self.control.update(value,self.t,phase=phase)

    def prime(self,n=8,**kw):return [self.sample(**kw) for _ in range(n)]
    def fields(self,out):return out['move'],out['jump'],out['slide'],out['confirm']
    def tracking(self,identity='alice',epoch=1,**kw):
        return dict(identity=identity,epoch=epoch,locked=True,active=True,input_allowed=True,**kw)
    def tracked(self,track,**kw):
        p=pose(self.t+.05,self.seq+1,**kw);p['player_tracking']=track
        return self.sample(packet=p)

    def test_standing_anywhere_calibrates_without_a_hand_target(self):
        self.assertEqual(self.prime(body_x=.13,right_x=.63)[-1]['calibration'],'ready')
        self.assertFalse(any(any(self.fields(row)) for row in self.prime(12,body_x=.13,right_x=.63)))

    def test_mirrored_body_left_on_source_is_screen_right_once(self):
        self.prime()
        out=[self.sample(body_x=x) for x in (-.015,-.035,-.055,-.075,-.075,-.075)]
        self.assertEqual(sum(row['move']==1 for row in out),1)
        self.assertFalse(any(row['move']==-1 for row in out))
        self.assertFalse(any(row['move'] for row in self.prime(40,body_x=-.075)))

    def test_nonmirrored_directions_match_source(self):
        self.control=PoseControls(mirrored=False);self.prime()
        self.assertEqual([self.sample(body_x=x)['move'] for x in (.02,.04,.06,.08,.08,.08)].count(1),1)

    def test_return_to_neutral_targets_middle_before_same_side_again(self):
        self.prime()
        first=[self.sample(body_x=x) for x in (.02,.04,.06,.08,.08,.08)]
        self.assertEqual([row['move'] for row in first].count(-1),1)
        middle=self.prime(5)
        self.assertEqual([row['move'] for row in middle if row['move']],[1])
        self.assertTrue(all(row['lane_target']==1 for row in middle[2:]))
        second=[self.sample(body_x=x) for x in (.02,.04,.06,.08,.08,.08)]
        self.assertEqual([row['move'] for row in second].count(-1),1)

    def test_crossing_directly_to_other_side_does_not_double_trigger(self):
        self.prime();self.prime(4,body_x=.08)
        self.assertFalse(any(row['move'] for row in self.prime(8,body_x=-.08)))

    def test_slow_real_side_step_is_not_required_to_be_a_flick(self):
        self.prime()
        out=[self.sample(body_x=x*.002) for x in range(44)]
        self.assertEqual(sum(abs(row['move']) for row in out),1)

    def test_hand_flicks_and_arm_raises_do_not_move_or_jump(self):
        self.prime()
        for x,y in ((.4,.58),(.35,.58),(.3,.58),(.4,.2),(.4,.1)):
            out=self.sample(right_x=x,right_y=y,left_y=y)
            self.assertEqual(self.fields(out),(0,False,False,False))

    def test_running_jump_works_even_with_both_arms_raised(self):
        self.prime(left_y=.2,right_y=.2)
        self.assertTrue(self.sample(body_down=-.045,left_y=.2,right_y=.2)['jump'])

    def test_no_wrists_needed_for_lanes_jump_or_slide(self):
        def packet(dx=0.,dy=0.):
            p=pose(self.t+.05,self.seq+1,body_x=dx,body_down=dy)
            p['points17'][9][2]=p['points17'][10][2]=0.
            return p
        for _ in range(8):self.assertTrue(self.sample(packet=packet())['valid'])
        self.assertEqual([self.sample(packet=packet(x))['move'] for x in (.02,.04,.06,.08,.08,.08)].count(-1),1)
        for _ in range(5):self.sample(packet=packet())
        self.assertTrue(self.sample(packet=packet(dy=-.045))['jump'])
        for _ in range(16):self.sample(packet=packet())
        self.assertEqual(sum(self.sample(packet=packet(dy=d))['slide']
                             for d in (.04,.08,.12,.14,.16,.16,.16,.16)),1)

    def test_shoulder_lean_without_hip_translation_is_not_lane(self):
        self.prime()
        for amount in (.02,.04,.06,.08):
            p=pose(self.t+.05,self.seq+1)
            for k in (5,6):p['points17'][k][0]+=amount
            self.assertFalse(self.sample(packet=p)['move'])

    def test_one_torso_group_rising_is_not_a_jump(self):
        for indices in ((5,6),(11,12)):
            self.setUp();self.prime()
            for amount in (.02,.04,.06):
                p=pose(self.t+.05,self.seq+1)
                for k in indices:p['points17'][k][1]-=amount
                self.assertFalse(self.sample(packet=p)['jump'])

    def test_real_body_rise_fires_once_and_landing_rearms(self):
        self.prime()
        out=[self.sample(body_down=y) for y in (-.01,-.025,-.045,-.06,-.07)]
        self.assertEqual(sum(row['jump'] for row in out),1)
        self.assertFalse(any(row['jump'] for row in self.prime(6,body_down=-.06)))
        self.prime(8)
        self.assertTrue(self.sample(body_down=-.045)['jump'])

    def test_small_body_jitter_is_not_a_jump_or_slide(self):
        self.prime()
        out=[self.sample(body_down=(.008 if i%2 else -.008),body_x=(.005 if i%2 else -.005)) for i in range(120)]
        self.assertFalse(any(any(self.fields(row)) for row in out))

    def test_crouch_and_rise_back_up_does_not_jump(self):
        self.prime()
        down=[self.sample(body_down=y) for y in (.04,.08,.12,.14,.16,.16,.16,.16)]
        self.assertEqual(sum(row['slide'] for row in down),1)
        self.assertFalse(any(row['slide'] for row in self.prime(10,body_down=.16)))
        up=[self.sample(body_down=y) for y in (.12,.08,.04,0.,0.,0.,0.)]
        self.assertFalse(any(row['jump'] for row in up))
        self.assertTrue(self.sample(body_down=-.045)['jump'])

    def test_jump_landing_compression_is_not_slide(self):
        self.prime();self.assertTrue(self.sample(body_down=-.045)['jump'])
        out=[self.sample(body_down=y) for y in (-.07,-.04,.01,.04,.07,.08,.04,.01,0.)]
        self.assertFalse(any(row['slide'] for row in out))

    def test_crouch_does_not_turn_a_small_horizontal_sway_into_a_lane(self):
        self.prime()
        # A true sideways step can now change lanes during a crouch; only
        # small incidental sway should stay in the centre lane.
        out=[self.sample(body_down=y,body_x=-y*.2) for y in (.02,.04,.06,.08)]
        self.assertFalse(any(row['move'] for row in out))
        self.assertTrue(all(row['lane_target']==1 for row in out))

    def test_camera_loss_and_recovery_do_not_replay_an_edge(self):
        self.prime();self.assertTrue(self.sample(body_down=-.045)['jump'])
        self.t+=.1
        self.assertFalse(any(self.fields(self.control.update(None,self.t))))
        out=self.sample(body_down=-.06)
        self.assertFalse(any(self.fields(out)))
        self.assertEqual(out['calibration'],'ready')

    def test_initial_air_pose_or_pose_after_stream_reset_never_jumps(self):
        self.assertFalse(any(row['jump'] for row in self.prime(12,body_down=-.07)))
        p=pose(self.t+.05,self.seq+1,body_down=-.1);p['stream_reset']=True
        self.assertFalse(any(self.fields(self.sample(packet=p))))

    def test_tracking_identity_epoch_switch_does_not_reset_neutral(self):
        for _ in range(8):self.tracked(self.tracking())
        for track in (self.tracking(epoch=2),self.tracking(identity='bob',epoch=3)):
            out=self.tracked(track)
            self.assertTrue(out['valid']);self.assertEqual(out['calibration'],'ready')
        self.assertTrue(self.tracked(self.tracking(epoch=4),body_down=-.045)['jump'])

    def test_false_tracking_gate_does_not_block_measured_body_motion(self):
        for _ in range(8):self.tracked(self.tracking())
        denied=self.tracking();denied['input_allowed']=False
        out=self.tracked(denied,body_down=-.045)
        self.assertTrue(out['valid']);self.assertTrue(out['jump'])

    def test_malformed_player_tracking_is_ignored(self):
        for track in (None,[],{},dict(input_allowed=True),dict(self.tracking(),epoch=True),dict(self.tracking(),identity=''),dict(self.tracking(),active=False)):
            with self.subTest(track=track):
                self.setUp();self.prime()
                out=self.tracked(track,body_down=-.045)
                self.assertTrue(out['valid']);self.assertTrue(out['jump'])

    def test_pid_or_explicit_visitor_change_rebuilds_motion_without_old_edge(self):
        for change in ({'pid':999},{'visitor_session':'new-visitor'}):
            with self.subTest(change=change):
                self.setUp();self.prime();self.assertTrue(self.sample(body_down=-.045)['jump'])
                p=pose(self.t+.05,self.seq+1,body_down=-.07,body_x=-.08);p.update(change)
                out=self.sample(packet=p)
                self.assertEqual(out['calibration'],'calibrating');self.assertFalse(any(self.fields(out)))

    def test_person_track_and_roi_identity_metadata_do_not_reset_controls(self):
        self.prime()
        p=pose(self.t+.05,self.seq+1,body_down=-.045)
        p.update(person_id='someone',track_id='other',roi_selection=dict(person_id='third',track_id='fourth'),
            player_tracking=dict(identity='bad',input_allowed=False,epoch=999))
        out=self.sample(packet=p)
        self.assertTrue(out['valid']);self.assertTrue(out['jump'])

    def test_title_body_motions_cannot_move_before_start(self):
        self.prime(8,phase='title')
        for kw in (dict(body_x=.08),dict(body_down=-.05),dict(body_down=.08)):
            self.assertFalse(any(self.fields(self.sample(phase='title',**kw))))

    def test_title_confirmation_still_works_and_is_not_repeated(self):
        self.assertEqual(sum(row['confirm'] for row in self.prime(15,phase='title',left_y=.2,right_y=.2)),1)
        self.assertFalse(any(row['jump'] for row in self.prime(10,phase='title',left_y=.2,right_y=.2)))

    def test_depth_scale_change_needs_new_baseline_not_jump(self):
        self.prime()
        p=pose(self.t+.05,self.seq+1,body_down=-.045)
        for k in (5,6):p['points17'][k][0]=.5+(p['points17'][k][0]-.5)*1.25
        out=self.sample(packet=p)
        self.assertFalse(any(self.fields(out)));self.assertEqual(out['calibration'],'calibrating')

    def test_15fps_and_30fps_body_jumps_are_detected(self):
        for dt in (1/15,1/30):
            self.setUp()
            for _ in range(20):self.sample(dt=dt)
            outputs=[self.sample(dt=dt,body_down=-min(.065,i*dt*.45)) for i in range(1,9)]
            self.assertEqual(sum(row['jump'] for row in outputs),1)


if __name__=='__main__':unittest.main()
