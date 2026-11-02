"""Course isolation, timing and observed-only Tai Chi guidance regression."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from . import catalog, taichi_catalog
from .advice import AdviceObserver
from .course_registry import COURSE_IDS, get_course, get_courses
from .engine import LessonSession
from .test_engine import Clock, person, raised
from .voice import _cue


ORDER = ('prepare', 'parting', 'crane', 'brush', 'lute', 'repulse',
         'grasp_left', 'grasp_right', 'single_whip', 'cloud', 'single_whip_2',
         'high_pat', 'kick_right', 'peaks', 'kick_left', 'snake_left',
         'snake_right', 'shuttle', 'needle', 'fan', 'turn_punch', 'close',
         'cross', 'finish')
NAMES = ('起势', '左右野马分鬃', '白鹤亮翅', '左右搂膝拗步', '手挥琵琶', '左右倒卷肱',
         '左揽雀尾', '右揽雀尾', '单鞭', '云手', '单鞭', '高探马', '右蹬脚',
         '双峰贯耳', '转身左蹬脚', '左下势独立', '右下势独立', '左右穿梭',
         '海底针', '闪通臂', '转身搬拦捶', '如封似闭', '十字手', '收势')



class CourseRegistryTests(unittest.TestCase):
    def test_existing_baduanjin_registry_is_compatible(self):
        course = get_course()
        self.assertEqual(course['id'], 'baduanjin')
        self.assertEqual(course['lessons'], catalog.COURSE)
        self.assertIs(course['reference_pose'], catalog.reference_pose)
        self.assertEqual(course['report_kind'], 'baduanjin_practice')
        self.assertTrue(course['repetition_counting'])

    def test_taichi_is_the_complete_ordered_twenty_four_forms(self):
        course = get_course('taichi')
        self.assertEqual(tuple(row['id'] for row in course['lessons']), ORDER)
        self.assertEqual(tuple(row['name'] for row in course['lessons']), NAMES)
        self.assertEqual(set(course['by_id']), set(ORDER))
        self.assertEqual(course['report_kind'], 'taichi_practice')
        self.assertFalse(course['repetition_counting'])

    def test_course_payloads_are_independent(self):
        first = get_course('taichi')
        first['lessons'][0]['name'] = 'changed'
        first['sources'][0]['title'] = 'changed'
        second = get_course('taichi')
        self.assertEqual(second['lessons'][0]['name'], '起势')
        self.assertNotEqual(second['sources'][0]['title'], 'changed')
        self.assertIs(second['by_id']['prepare'], second['lessons'][0])

    def test_courses_do_not_share_video_directory_or_report_kind(self):
        courses = get_courses()
        self.assertEqual(tuple(course['id'] for course in courses), COURSE_IDS)
        self.assertEqual(len({course['report_kind'] for course in courses}), 2)
        self.assertEqual(len({course['video_directory'] for course in courses}), 2)
        self.assertEqual(courses[1]['video_directory'].name, 'taichi-video')

    def test_unknown_course_is_never_silently_baduanjin(self):
        for identifier in ('unknown', '', None, 1):
            with self.assertRaises(ValueError):
                get_course(identifier)
            with self.assertRaises(ValueError):
                LessonSession(course_id=identifier)

    def test_taichi_no_automated_quality_or_repetition_claim(self):
        for item in get_course('taichi')['lessons']:
            self.assertFalse(item['observable'])
            self.assertFalse(item['count_repetitions'])
        self.assertIn('不自动计', taichi_catalog.ASSESSMENT_NOTICE)

    def test_lower_body_lessons_require_real_knee_and_ankle_points(self):
        course = get_course('taichi')
        for identifier in ('brush', 'parting', 'kick_right', 'kick_left', 'snake_left', 'snake_right', 'grasp_left', 'grasp_right', 'shuttle', 'needle', 'turn_punch'):
            self.assertTrue(set((13, 14, 15, 16)).issubset(course['by_id'][identifier]['required_joints']))


class TaichiReferenceTests(unittest.TestCase):
    def test_all_frames_finite_bounded_and_seventeen_slots(self):
        for identifier in ORDER:
            for step in range(101):
                pose = taichi_catalog.reference_pose(identifier, step/100.)
                self.assertEqual(len(pose), 17)
                for point in pose:
                    if point is not None:
                        self.assertTrue(all(math.isfinite(value) and 0 <= value <= 1 for value in point))

    def test_cycle_and_bilateral_boundaries_do_not_snap(self):
        for identifier in ORDER:
            self.assertEqual(taichi_catalog.reference_pose(identifier, 0.),
                             taichi_catalog.reference_pose(identifier, 1.))
        for identifier in taichi_catalog.BILATERAL:
            before = taichi_catalog.reference_pose(identifier, .5-1e-6)
            after = taichi_catalog.reference_pose(identifier, .5+1e-6)
            distances = [math.dist(left, right) for left, right in zip(before, after) if left is not None]
            self.assertLess(max(distances), 1e-6)

    def test_main_forms_have_distinct_shapes_and_mirrored_second_side(self):
        shapes = []
        for identifier in ORDER[1:-1]:
            phase = .25 if identifier in taichi_catalog.BILATERAL else .5
            pose = taichi_catalog.reference_pose(identifier, phase)
            shapes.append(json.dumps(pose))
        self.assertEqual(len(set(shapes)), len(shapes)-1)  # Single whip intentionally recurs.
        for identifier in taichi_catalog.BILATERAL:
            self.assertNotEqual(taichi_catalog.reference_pose(identifier, .25),
                                taichi_catalog.reference_pose(identifier, .75))

    def test_phase_bounds_and_nonfinite_are_safe(self):
        base = taichi_catalog.reference_pose('cloud', 0.)
        for value in (-10., math.nan, math.inf):
            self.assertEqual(taichi_catalog.reference_pose('cloud', value), base)
        self.assertEqual(taichi_catalog.reference_pose(0, .3),
                         taichi_catalog.reference_pose('prepare', .3))
        with self.assertRaises(ValueError):
            taichi_catalog.reference_pose('raise')


class TaichiSessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sequence = 0

    def session(self, mode='free', index=0):
        session = LessonSession(mode, clock=self.clock, course_id='taichi')
        for _ in range(index):
            session.action('next')
        session.start()
        return session

    def feed(self, session, points, seconds=1.5, **extra):
        for _ in range(round(seconds/.05)):
            self.clock.advance(.05)
            self.sequence += 1
            state = dict(pid=1, sequence=self.sequence,
                         timestamp_seconds=self.clock(), points17=copy.deepcopy(points))
            state.update(extra)
            shot = session.update(state)
        return shot

    def test_snapshot_identifies_course_and_matching_schematic(self):
        session = self.session(index=9)
        shot = session.snapshot()
        self.assertEqual(shot['course_id'], 'taichi')
        self.assertEqual(shot['course_title'], '太极指导')
        self.assertEqual(shot['lesson_name'], '云手')
        self.assertEqual(shot['lesson_count'], 24)
        self.assertFalse(shot['repetition_counting'])
        self.assertEqual(shot['reference_pose'], taichi_catalog.reference_pose('cloud', 0.))

    def test_learn_shows_demo_and_stops_at_lesson_boundary_when_requested(self):
        session = self.session('learn')
        session.stop_at_lesson_boundary = True
        self.assertEqual(session.snapshot()['phase'], 'demo')
        self.clock.advance(12.)
        self.assertEqual(session.snapshot()['phase'], 'practice')
        self.clock.advance(1000.)
        shot = session.snapshot()
        self.assertEqual(shot['lesson_id'], 'parting')
        self.assertEqual(shot['phase'], 'demo')
        self.assertEqual(shot['elapsed'], 0.)

    def test_timed_completion_does_not_invent_seen_moves(self):
        session = self.session('follow')
        planned = sum(row['practice_seconds'] for row in taichi_catalog.COURSE)
        self.clock.advance(planned+1000.)
        report = session.report()
        self.assertEqual(report['kind'], 'taichi_practice')
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['active_seconds'], planned)
        self.assertEqual(report['observed_repetitions'], 0)
        self.assertEqual(report['observed_seconds'], 0.)
        self.assertTrue(report['course_timer_finished'])
        self.assertEqual(len(report['timer_finished_lessons']), 24)
        self.assertFalse(report['professional_motion_validation'])
        json.dumps(report, ensure_ascii=False, allow_nan=False)

    def test_free_mode_and_pause_resume_preserve_selected_course(self):
        session = self.session(index=15)
        self.clock.advance(500.)
        shot = session.action('pause')
        self.assertEqual(shot['lesson_id'], 'snake_left')
        self.clock.advance(1000.)
        self.assertEqual(session.snapshot()['elapsed'], shot['elapsed'])
        session.action('resume')
        self.clock.advance(1.)
        self.assertEqual(session.snapshot()['elapsed'], shot['elapsed']+1.)
        self.assertEqual(session.snapshot()['course_id'], 'taichi')

    def test_reference_animation_or_actual_raised_points_never_manufacture_form_counts(self):
        for index in range(len(ORDER)):
            session = self.session(index=index)
            identifier = ORDER[index]
            for phase in (0., .25, .5, .75, 1.):
                self.feed(session, taichi_catalog.reference_pose(identifier, phase), .3)
            self.feed(session, raised(), .4)
            report = session.report()
            self.assertEqual(report['observed_repetitions'], 0)
            self.assertEqual(report['observed_seconds'], 0.)
            self.assertFalse(session.snapshot()['observable'])

    def test_stale_and_tracking_gap_clear_advice_and_actual_points(self):
        session = self.session(index=5)
        self.assertEqual(self.feed(session, person())['advice']['code'], 'taichi_repulse_scope')
        for change in ({'stale': True}, {'motion_discontinuity': True}):
            shot = self.feed(session, person(), .05, **change)
            self.assertEqual(shot['advice']['code'], 'course_guidance')
            self.assertEqual(shot['advice']['evidence']['basis'], 'course_steps')
            self.assertEqual(shot['pose_points'], [None]*17)
            self.feed(session, person())

    def test_course_specific_report_save_rejects_wrong_kind(self):
        session = self.session()
        session.action('finish')
        report = session.report()
        with tempfile.TemporaryDirectory() as folder:
            path = session.save_report(folder, report)
            self.assertTrue(path.name.startswith('taichi-'))
            self.assertEqual(json.loads(path.read_text(encoding='utf8'))['kind'], 'taichi_practice')
            bad = dict(report, kind='baduanjin_practice')
            with self.assertRaises(ValueError):
                session.save_report(folder, bad)

    def test_bilateral_instruction_side_is_named_without_wrong_course_text(self):
        session = self.session(index=5)
        self.clock.advance(5.)
        self.assertEqual(session.snapshot()['instruction_side'], 'first')
        self.clock.advance(10.)
        self.assertEqual(session.snapshot()['instruction_side'], 'second')
        self.assertTrue(session.snapshot()['instruction'].startswith('换另一侧：'))
        self.assertNotIn('托天', session.snapshot()['instruction'])


class TaichiAdviceAndVoiceTests(unittest.TestCase):
    def feed(self, identifier, points, required=None):
        observer = AdviceObserver(course_id='taichi')
        shot = None
        for index in range(1, 41):
            shot = observer.update(identifier, points, 100+index*.05, required=required)
        return shot

    def test_missing_or_low_confidence_joints_are_visibility_not_wrong_form(self):
        points = person()
        points[9] = None
        self.assertEqual(self.feed('cloud', points)['kind'], 'visibility')
        points = person()
        points[9][2] = .25
        shot = self.feed('cloud', points)
        self.assertEqual(shot['code'], 'uncertain_taichi_cloud_transition')
        self.assertEqual(shot['kind'], 'tentative')
        self.assertIn('仅供参考', shot['text'])

    def test_cloud_describes_wrist_positions(self):
        points = person()
        points[9][1], points[10][1] = .3, .5
        shot = self.feed('cloud', points)
        self.assertEqual(shot['code'], 'taichi_cloud_heights_seen')
        self.assertEqual(shot['kind'], 'observation')
        self.assertEqual(set(shot['evidence']), {'planar_wrist_height_difference_torso'})

    def test_raised_knee_is_not_a_balance_or_weight_bearing_score(self):
        points = person()
        points[13][1] = .6
        for identifier in ('kick_right', 'kick_left', 'snake_left', 'snake_right'):
            shot = self.feed(identifier, points, required=tuple(range(5, 17)))
            self.assertEqual(shot['code'], 'taichi_knee_height_seen')
            self.assertFalse(shot['evidence']['weight_bearing_observed'])

    def test_projected_wrist_proximity_never_claims_hand_contact(self):
        points = person()
        points[9][0], points[10][0] = .49, .51
        shot = self.feed('cross', points)
        self.assertEqual(shot['code'], 'taichi_cross_near')
        self.assertFalse(shot['evidence']['hand_contact_observed'])

    def test_taichi_all_lesson_advice_serializes_without_scores(self):
        for row in taichi_catalog.COURSE:
            shot = self.feed(row['id'], person(), required=row['required_joints'])
            self.assertNotEqual(shot['code'], 'none')
            json.dumps(shot, ensure_ascii=False, allow_nan=False)
            self.assertFalse(set(shot) & {'score', 'calories', 'diagnosis', 'health_score'})

    def test_taichi_overviews_have_course_specific_identity(self):
        for identifier in ORDER:
            shot = dict(course_id='taichi', status='running', lesson_id=identifier,
                        instruction_index=0, phase='practice', session_id='taichi-test')
            selected=_cue(shot)
            self.assertEqual(selected[0],identifier+':0')
            self.assertEqual(selected[1][0],'taichi-overview-v1')
        self.assertEqual(_cue(dict(course_id='taichi',status='completed')),
                         ('complete',('completed','taichi',None)))
        self.assertEqual(_cue(dict(status='running', lesson_id='prepare',
                                   instruction_index=0, phase='practice'))[0], 'prepare:0')


if __name__ == '__main__':
    unittest.main()
