"""Display uncertainty never changes measured repetitions or fills missing data."""
import copy,math,unittest
from .engine import LessonSession,observed_points,display_points
from .pose_display import finite_points,observed_transform,project_observed
from .test_engine import Clock,person,raised


class ObservedDisplayTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.sequence=0
        self.session=LessonSession('free',clock=self.clock)
        self.session.action('next');self.session.start()

    def feed(self,points,seconds=.3,**extra):
        for _ in range(round(seconds/.05)):
            self.clock.advance(.05);self.sequence+=1
            state=dict(pid=10,sequence=self.sequence,timestamp_seconds=self.clock(),points17=copy.deepcopy(points))
            state.update(extra);self.session.update(state)
        return self.session.snapshot()

    def test_weak_current_wrist_is_display_only_without_confidence_change(self):
        p=person();p[9][2]=.10
        s=self.feed(p)
        self.assertIsNone(s['pose_points'][9])
        self.assertEqual(s['display_pose_points'][9],tuple(p[9]))
        self.assertFalse(s['observable']);self.assertEqual(s['repetition'],0)
        self.assertEqual(self.session.report()['observed_seconds'],0)

    def test_missing_never_reuses_previous_wrist(self):
        p=person();self.feed(p);p[9]=[-1,-1,0]
        s=self.feed(p)
        self.assertIsNone(s['display_pose_points'][9])
        self.assertIsNone(s['pose_points'][9])

    def test_stale_frame_and_timeout_clear_both_channels(self):
        self.feed(person())
        s=self.feed(person(),timestamp_seconds=self.clock()-5)
        self.assertEqual(s['display_pose_points'],[None]*17)
        self.feed(person());self.clock.advance(.81)
        s=self.session.snapshot()
        self.assertEqual(s['display_pose_points'],[None]*17)
        self.assertEqual(s['pose_points'],[None]*17)

    def test_uncertain_torso_cannot_enable_weak_arm(self):
        p=person();p[9][2]=.10;p[11][2]=.34
        s=self.feed(p)
        self.assertIsNone(s['display_pose_points'][9])

    def test_empty_scene_does_not_become_display_skeleton(self):
        p=person()
        for point in p:
            if point is not None:point[2]=.09
        s=self.feed(p)
        self.assertEqual(s['display_pose_points'],[None]*17)

    def test_implausible_weak_limb_is_rejected(self):
        p=person();p[9]=[1.,0.,.10]
        s=self.feed(p)
        self.assertIsNone(s['display_pose_points'][9])

    def test_unobserved_elbow_cannot_connect_weak_wrist(self):
        p=person();p[7]=[-1,-1,0];p[9][2]=.10
        s=self.feed(p)
        self.assertIsNone(s['display_pose_points'][7])
        self.assertIsNone(s['display_pose_points'][9])

    def test_weak_whole_cycle_never_counts(self):
        for p in (person(),raised(),person()):
            p[9][2]=.10;p[10][2]=.10
            self.feed(p)
        self.assertEqual(self.session.report()['observed_repetitions'],0)
        self.assertEqual(self.session.report()['observed_seconds'],0)

    def test_reliable_cycle_still_counts(self):
        for p in (person(),raised(),person()):self.feed(p)
        self.assertEqual(self.session.report()['observed_repetitions'],1)

    def test_discontinuity_clears_display(self):
        self.feed(person())
        s=self.feed(person(),pose_discontinuity=True)
        self.assertEqual(s['display_pose_points'],[None]*17)


class TorsoProjectionTests(unittest.TestCase):
    box=(653,161,913,313)
    def test_lost_limbs_do_not_zoom_or_translate_remaining_body(self):
        p=person();before=project_observed(p,self.box)
        for i in (7,8,9,10,13,14,15,16):p[i]=None
        after=project_observed(p,self.box)
        for i in (5,6,11,12):self.assertEqual(before[i],after[i])
        for i in (7,8,9,10,13,14,15,16):self.assertIsNone(after[i])

    def test_wrist_movement_does_not_rescale_torso(self):
        p=person();before=project_observed(p,self.box)
        p[9]=[.1,.01,.9];p[10]=[.9,.01,.9]
        after=project_observed(p,self.box)
        for i in (5,6,11,12):self.assertEqual(before[i],after[i])

    def test_weak_wrist_retains_score(self):
        p=person();p[9][2]=.1
        self.assertEqual(project_observed(p,self.box)[9][2],.1)

    def test_natural_and_raised_wrists_have_full_marker_headroom(self):
        from .catalog import reference_pose
        poses=[person(),raised()]+[reference_pose('raise',i/100) for i in range(100)]
        a,b,c,d=self.box
        for p in poses:
            projected=project_observed(p,self.box)
            for joint in (5,6,7,8,9,10,11,12):
                point=projected[joint]
                if point is not None:
                    self.assertGreaterEqual(point[0],a+5)
                    self.assertLessEqual(point[0],c-5)
                    self.assertGreaterEqual(point[1],b+5)
                    self.assertLessEqual(point[1],d-5)

    def test_invalid_and_missing_points_stay_missing(self):
        p=person();p[9]=[float('nan'),.4,.9];p[10]=[-1,-1,.9]
        after=project_observed(p,self.box)
        self.assertIsNone(after[9]);self.assertIsNone(after[10])
        self.assertEqual(project_observed(None,self.box),(None,)*17)

    def test_mirror_keeps_height_and_shape(self):
        p=person();a=project_observed(p,self.box,mirror=False);b=project_observed(p,self.box)
        center=(self.box[0]+self.box[2])/2
        for pa,pb in zip(a,b):
            if pa is not None:
                self.assertAlmostEqual(pa[0]+pb[0],2*center)
                self.assertEqual(pa[1:],pb[1:])

if __name__=='__main__':unittest.main()
