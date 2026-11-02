"""Meaningful deterministic lesson/pose failure cases, with no real camera."""
import copy,json,math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from .catalog import COURSE,MODES,reference_pose
from .engine import LessonSession,observed_points

class Clock:
    def __init__(self):self.value=100.
    def __call__(self):return self.value
    def advance(self,seconds):self.value+=seconds;return self.value

def person():
    points=[None]*17
    values={5:(.40,.30),6:(.60,.30),7:(.34,.44),8:(.66,.44),9:(.35,.58),10:(.65,.58),11:(.44,.58),12:(.56,.58),13:(.43,.75),14:(.57,.75),15:(.42,.93),16:(.58,.93)}
    for index,xy in values.items():points[index]=[*xy,.9]
    return points

def raised():
    result=person();result[9]=[.46,.12,.9];result[10]=[.54,.12,.9];result[7]=[.35,.20,.9];result[8]=[.65,.20,.9]
    return result

class SessionTests(unittest.TestCase):
    def setUp(self):self.clock=Clock();self.sequence=0
    def session(self,mode='free',index=1):
        s=LessonSession(mode,clock=self.clock)
        for _ in range(index):s.action('next')
        s.start();return s
    def frame(self,points,**extra):
        self.sequence+=1
        return dict(pid=101,sequence=self.sequence,timestamp_seconds=self.clock(),points17=copy.deepcopy(points),**extra)
    def feed(self,s,points,seconds=.30):
        for _ in range(round(seconds/.05)):
            self.clock.advance(.05);s.update(self.frame(points))
        return s.snapshot()
    def cycle(self,s):
        self.feed(s,person());self.feed(s,raised());return self.feed(s,person())

    def test_modes_and_ready_state(self):
        for mode in MODES:
            s=LessonSession(mode,clock=self.clock);self.assertEqual(s.snapshot()['status'],'ready')
        with self.assertRaises(ValueError):LessonSession('unknown')
    def test_play_can_start_ready_session(self):
        s=LessonSession('follow',clock=self.clock)
        self.assertEqual(s.action('play')['status'],'running')
    def test_learn_demo_precedes_practice(self):
        s=self.session('learn');self.assertEqual(s.snapshot()['phase'],'demo')
        self.clock.advance(COURSE[1]['demo_seconds']);self.assertEqual(s.snapshot()['phase'],'practice')
        self.assertEqual(s.snapshot()['elapsed'],0)
    def test_complete_timed_course_without_pose_never_claims_reps(self):
        s=self.session('follow',0)
        planned=sum(row['practice_seconds'] for row in COURSE)
        self.clock.advance(planned+500)
        shot=s.snapshot();self.assertEqual(shot['status'],'completed')
        report=s.report();self.assertEqual(report['observed_repetitions'],0)
        self.assertEqual(report['observed_seconds'],0)
        self.assertAlmostEqual(report['active_seconds'],planned)
        self.assertEqual(len(report['timer_finished_lessons']),10)
        self.assertTrue(report['course_timer_finished'])
    def test_free_mode_never_forces_lesson_completion(self):
        s=self.session();self.clock.advance(3600)
        shot=s.snapshot();self.assertEqual(shot['lesson_id'],'raise');self.assertEqual(shot['status'],'running')
    def test_pause_freezes_demo_clock_and_resume_does_not_fast_forward(self):
        s=self.session('learn');self.clock.advance(4);before=s.action('pause')
        self.assertAlmostEqual(before['progress'],4/COURSE[1]['demo_seconds'])
        self.clock.advance(50);during=s.snapshot()
        self.assertEqual(before['progress'],during['progress']);self.assertEqual(during['elapsed'],4)
        s.action('play');self.clock.advance(1);after=s.snapshot()
        self.assertEqual(after['elapsed'],5);self.assertEqual(after['session_elapsed'],5)
    def test_backward_clock_does_not_count_or_rewind(self):
        s=self.session();self.feed(s,person());before=s.snapshot()['session_elapsed']
        self.clock.value-=20;s.update(self.frame(raised()))
        self.assertEqual(s.snapshot()['session_elapsed'],before)
        self.assertEqual(s.report()['clock_resets'],1);self.assertEqual(s.snapshot()['repetition'],0)
    def test_observed_raise_return_counts_once(self):
        s=self.session();shot=self.cycle(s)
        self.assertEqual(shot['repetition'],1);self.assertTrue(shot['observable'])
        self.feed(s,person(),1);self.assertEqual(s.snapshot()['repetition'],1)
        self.assertGreater(s.report()['observed_seconds'],.5)
    def test_hold_endpoint_without_initial_return_does_not_count(self):
        s=self.session();self.feed(s,raised(),1);self.feed(s,person())
        self.assertEqual(s.snapshot()['repetition'],0)
    def test_missing_keypoint_breaks_cycle_instead_of_bridging(self):
        s=self.session();self.feed(s,person());self.feed(s,raised())
        missing=raised();missing[9]=None;self.feed(s,missing)
        self.feed(s,person());self.assertEqual(s.snapshot()['repetition'],0)
        self.cycle(s);self.assertEqual(s.snapshot()['repetition'],1)
    def test_low_confidence_nan_and_offscreen_are_not_errors_or_counts(self):
        for bad in ([.46,.12,.01],[math.nan,.12,.9],[1.2,.12,.9]):
            s=self.session();p=raised();p[9]=bad;shot=self.feed(s,p)
            self.assertFalse(shot['observable']);self.assertIsNone(shot['pose_points'][9]);self.assertEqual(shot['repetition'],0)
            self.assertNotIn('错误',shot['feedback'])
    def test_two_coordinate_legacy_point_is_never_assigned_fake_confidence(self):
        s=self.session();p=person();p[9]=p[9][:2]
        self.assertFalse(self.feed(s,p)['observable'])
    def test_only_finite_normalized_triples_are_observed(self):
        for value in (None,[],[None]*16,{},'invalid'):
            state=self.frame(person());state['points17']=value
            self.assertIsNone(observed_points(state,self.clock())[0])
    def test_malformed_phase_and_oversized_integer_are_nonfatal_missing_input(self):
        state=self.frame(person());state['phase']=[]
        self.assertIsNone(observed_points(state,self.clock())[0])
        state=self.frame(person());state['points17'][9][0]=10**1000
        points,_,_=observed_points(state,self.clock());self.assertIsNone(points[9])
        state=self.frame(person());state['timestamp_seconds']=10**1000
        self.assertIsNone(observed_points(state,self.clock())[0])
    def test_stale_and_future_frames_never_count(self):
        s=self.session();state=self.frame(person());state['timestamp_seconds']-=2
        self.assertFalse(s.update(state)['observable'])
        state=self.frame(person());state['timestamp_seconds']+=1
        self.assertFalse(s.update(state)['observable'])
    def test_explicit_unavailability_invalidates_existing_pose(self):
        for extra in ({'stale':True},{'available':False},{'camera_link':{'present':False}},{'phase':'lost'}):
            s=self.session();self.feed(s,person());state=self.frame(raised());state.update(extra)
            shot=s.update(state);self.assertFalse(shot['observable']);self.assertEqual(shot['pose_points'],[None]*17)
    def test_duplicate_polling_not_repetitions_or_tracked_time(self):
        s=self.session();self.feed(s,person());state=self.frame(raised());s.update(state)
        previous=s.report()['observed_seconds']
        for _ in range(9):self.clock.advance(.05);s.update(state)
        self.assertFalse(s.snapshot()['observable'])
        self.assertEqual(s.report()['observed_seconds'],previous);self.assertEqual(s.snapshot()['repetition'],0)
        for _ in range(5):
            self.clock.advance(.05)
            self.assertFalse(s.update(state)['observable'])
    def test_snapshot_alone_expires_observed_points(self):
        s=self.session();self.feed(s,person());self.assertTrue(s.snapshot()['observable'])
        self.clock.advance(.9);shot=s.snapshot();self.assertFalse(shot['observable']);self.assertEqual(shot['pose_points'],[None]*17)
    def test_motion_discontinuity_resets_partial_repetition(self):
        for extra in ({'motion_discontinuity':True},{'pose_discontinuity':True},{'motion_history_discontinuity':True},{'roi_selection':{'continuity':{'reset_motion_history':True}}}):
            s=self.session();self.feed(s,person());self.feed(s,raised());state=self.frame(raised());state.update(extra)
            s.update(state);self.feed(s,person());self.assertEqual(s.snapshot()['repetition'],0)
    def test_service_restart_does_not_bridge_partial_repetition(self):
        s=self.session();self.feed(s,person());self.feed(s,raised())
        state=self.frame(raised());state['pid']=202;s.update(state)
        self.feed(s,person());self.assertEqual(s.snapshot()['repetition'],0)
    def test_sequence_regression_does_not_bridge(self):
        s=self.session();self.feed(s,person());self.feed(s,raised())
        state=self.frame(raised());state['sequence']=0;s.update(state)
        self.feed(s,person());self.assertEqual(s.snapshot()['repetition'],0)
    def test_snapshot_is_json_serializable_with_no_nan(self):
        s=self.session();p=person();p[0]=[math.nan,math.inf,0.]
        json.dumps(self.feed(s,p),allow_nan=False)
    def test_no_precise_feedback_for_unobservable_exercises(self):
        for index in (4,7,8):
            s=self.session(index=index);self.cycle(s)
            self.assertFalse(s.snapshot()['observable']);self.assertEqual(s.snapshot()['repetition'],0)
            self.assertEqual(s.report()['observed_seconds'],0)
    def test_next_previous_repeat_are_bounded_and_preserve_paused_state(self):
        s=self.session('learn');s.action('pause');s.action('next');self.assertEqual(s.snapshot()['status'],'paused')
        s.action('previous');self.assertEqual(s.snapshot()['lesson_index'],1)
        s.action('repeat');self.assertEqual(s.snapshot()['elapsed'],0)
        for _ in range(30):s.action('previous')
        self.assertEqual(s.snapshot()['lesson_index'],0)
    def test_manual_finish_does_not_mark_unvisited_course_complete(self):
        s=self.session();self.cycle(s);s.action('finish')
        report=s.report();self.assertEqual(report['status'],'completed');self.assertEqual(report['timer_finished_lessons'],[])
        self.assertEqual(report['observed_repetitions'],1)
        self.assertEqual(report['end_reason'],'manual_finish');self.assertFalse(report['course_timer_finished'])
    def test_report_statistic_totals_are_observed_not_scheduled(self):
        s=self.session();self.cycle(s);s.action('pause');self.clock.advance(5);s.action('play')
        s.action('next');self.clock.advance(2);s.action('finish')
        report=s.report()
        self.assertAlmostEqual(report['active_seconds'],sum(r['visited_seconds'] for r in report['lessons']),places=2)
        self.assertAlmostEqual(report['observed_seconds'],sum(r['tracked_seconds'] for r in report['lessons']),places=2)
        self.assertEqual(report['observed_repetitions'],sum(r['observed_repetitions'] for r in report['lessons']))
        self.assertLessEqual(report['observed_seconds'],sum(r['practice_seconds'] for r in report['lessons']))
    def test_report_is_atomic_and_contains_no_pose_coordinates_or_diagnoses(self):
        s=self.session();self.cycle(s);s.action('finish')
        with tempfile.TemporaryDirectory() as directory:
            path=s.save_report(directory);report=json.loads(path.read_text(encoding='utf8'))
            self.assertEqual(report['session_id'],s.session_id);self.assertEqual(report['observed_repetitions'],1)
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])
            self.assertFalse(report['professional_motion_validation'])
            self.assertFalse(any(key in report for key in ('pose_points','points17','diagnosis','calories','health_score','reference_pose')))
    def test_frozen_report_retry_never_recomputes_session_or_advances_clock(self):
        s=self.session();self.cycle(s);s.action('finish');frozen=s.report();before=copy.deepcopy(frozen)
        self.clock.advance(100)
        with tempfile.TemporaryDirectory() as directory,patch.object(s,'report',side_effect=AssertionError('must not recompute')):
            path=s.save_report(directory,report=frozen)
            self.assertEqual(json.loads(path.read_text(encoding='utf8')),before)
        self.assertEqual(frozen,before)
    def test_frozen_report_rejects_foreign_session_wrong_kind_and_nonfinite_data(self):
        s=self.session();frozen=s.report()
        for update in ({'session_id':'other-session'},{'kind':'other-kind'},{'active_seconds':math.nan}):
            supplied=dict(frozen,**update)
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):s.save_report(directory,report=supplied)
                self.assertEqual(list(Path(directory).iterdir()),[])
    def test_atomic_replace_failure_preserves_previous_saved_report(self):
        s=self.session();s.action('finish');frozen=s.report()
        with tempfile.TemporaryDirectory() as directory:
            path=s.save_report(directory,report=frozen);old=path.read_bytes()
            with patch('health.engine.os.replace',side_effect=OSError('test disk failure')):
                with self.assertRaises(OSError):s.save_report(directory,report=dict(frozen,active_seconds=999))
            self.assertEqual(path.read_bytes(),old);self.assertEqual(list(Path(directory).glob('*.tmp')),[])
    def test_unknown_action_and_invalid_time_are_rejected(self):
        s=self.session()
        for action in ('medical_score','jump'):
            with self.assertRaises(ValueError):s.action(action)
        with self.assertRaises(ValueError):s.snapshot(math.nan)
    def test_bilateral_instruction_follows_each_half_cycle(self):
        s=self.session(index=2)
        self.clock.advance(COURSE[2]['cycle_seconds']*.25)
        left=s.snapshot()
        self.assertEqual(left['instruction'],COURSE[2]['instructions'][1])
        self.assertEqual(left['instruction_index'],1);self.assertEqual(left['instruction_side'],'first')
        self.clock.advance(COURSE[2]['cycle_seconds']*.5)
        right=s.snapshot()
        self.assertEqual(right['instruction'],'换另一侧：'+COURSE[2]['instructions'][1])
        self.assertEqual(right['instruction_index'],1);self.assertEqual(right['instruction_side'],'second')
    def test_changing_mode_after_start_cannot_mutate_existing_report(self):
        s=self.session()
        with self.assertRaises(ValueError):s.action('set_mode_follow')

class ReferenceTests(unittest.TestCase):
    def test_course_has_preparation_eight_moves_and_finish(self):
        self.assertEqual(len(COURSE),10);self.assertEqual(len({r['id'] for r in COURSE}),10)
        self.assertEqual(COURSE[0]['id'],'prepare');self.assertEqual(COURSE[-1]['id'],'finish')
    def test_all_schematic_frames_are_finite_in_bounds(self):
        for row in COURSE:
            for i in range(101):
                pose=reference_pose(row['id'],i/100)
                self.assertEqual(len(pose),17)
                for p in pose:
                    if p is not None:self.assertTrue(all(math.isfinite(x) and 0<=x<=1 for x in p))
    def test_cycle_is_continuous_and_bilateral_directions_differ(self):
        for row in COURSE:
            first=reference_pose(row['id'],0);last=reference_pose(row['id'],1)
            self.assertEqual(first,last)
        for identifier in ('bow','single','look','sway','fist'):
            self.assertNotEqual(reference_pose(identifier,.25),reference_pose(identifier,.75))
    def test_eight_main_movements_do_not_reuse_same_shape(self):
        shapes=[json.dumps(reference_pose(row['id'],.25 if row['id'] in ('bow','single','look','sway','fist') else .5)) for row in COURSE[1:9]]
        self.assertEqual(len(set(shapes)),8)
    def test_sway_keeps_feet_planted_and_neutral_trunk_centered(self):
        frames=[reference_pose('sway',i/100) for i in range(101)]
        for index in (13,14,15,16):self.assertEqual(len({tuple(p[index]) for p in frames}),1)
        neutral=frames[0]
        self.assertAlmostEqual((neutral[5][0]+neutral[6][0])/2,(neutral[11][0]+neutral[12][0])/2)
    def test_reference_examples_generate_visible_return_counts_for_scored_modes(self):
        for index in (1,2,3,5,6):
            clock=Clock();s=LessonSession('free',clock=clock)
            for _ in range(index):s.action('next')
            s.start();sequence=0
            for phase in (0.,0.,0.,.5 if index in (1,6) else .25,.5 if index in (1,6) else .25,.5 if index in (1,6) else .25,1.,1.,1.):
                clock.advance(.12);sequence+=1;s.update(dict(pid=101,sequence=sequence,timestamp_seconds=clock(),points17=reference_pose(COURSE[index]['id'],phase)))
            self.assertEqual(s.snapshot()['repetition'],1,COURSE[index]['id'])

if __name__=='__main__':unittest.main()
