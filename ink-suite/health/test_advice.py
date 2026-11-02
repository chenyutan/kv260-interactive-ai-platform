"""Observed coaching cues: persistence, uncertainty and state-boundary tests."""
import copy
import json
import math
import unittest

from .advice import AdviceObserver
from .engine import LessonSession
from .test_engine import Clock, person, raised


class AdviceTests(unittest.TestCase):
    def setUp(self):
        self.observer = AdviceObserver()
        self.stamp = 100.

    def feed(self, points, lesson='raise', seconds=1.5, step=.05, **kwargs):
        result = None
        for _ in range(round(seconds/step)):
            self.stamp += step
            result = self.observer.update(lesson, points, self.stamp, **kwargs)
        return result

    def asymmetric_raise(self):
        points = raised()
        points[9][1], points[10][1] = .10, .27
        return points

    def test_steady_visible_raise_describes_actual_observation(self):
        shot = self.feed(raised())
        self.assertEqual(shot['code'], 'raise_seen')
        self.assertEqual(shot['kind'], 'observation')
        self.assertEqual(shot['joints'], [9, 10])
        self.assertTrue(shot['evidence']['both_wrists_above_shoulders'])

    def test_short_leading_hand_is_not_a_posture_correction(self):
        shot = self.feed(self.asymmetric_raise(), seconds=.6)
        self.assertEqual(shot['code'], 'none')
        self.assertEqual(self.feed(raised())['code'], 'raise_seen')

    def test_sustained_visible_asymmetry_gives_soft_adjustment(self):
        shot = self.feed(self.asymmetric_raise())
        self.assertEqual(shot['code'], 'raise_height_difference')
        self.assertEqual(shot['kind'], 'suggestion')
        self.assertIn('舒适范围', shot['text'])
        self.assertGreater(shot['evidence']['wrist_height_difference_torso'], .38)

    def test_resolution_clears_old_advice_immediately(self):
        self.feed(self.asymmetric_raise())
        self.stamp += .05
        shot = self.observer.update('raise', raised(), self.stamp)
        self.assertEqual(shot['code'], 'none')

    def test_alternating_lead_does_not_accumulate_a_suggestion(self):
        for _ in range(5):
            self.feed(self.asymmetric_raise(), seconds=.25)
            shot = self.feed(raised(), seconds=.25)
            self.assertEqual(shot['code'], 'none')

    def test_one_hand_raised_and_other_low_does_not_claim_both_below_shoulders(self):
        points = raised()
        points[10][1] = .6
        shot = self.feed(points)
        self.assertEqual(shot['code'], 'raise_transition')
        self.assertNotIn('双腕目前在肩线下方', shot['text'])

    def test_unilateral_move_does_not_receive_bilateral_raise_warning(self):
        points = raised()
        points[10][1] = .6
        shot = self.feed(points, lesson='single')
        self.assertEqual(shot['code'], 'single_seen')

    def test_single_both_high_requires_persistence(self):
        self.assertEqual(self.feed(raised(), lesson='single', seconds=.8)['code'], 'none')
        shot = self.feed(raised(), lesson='single', seconds=.7)
        self.assertEqual(shot['code'], 'single_both_high')
        self.assertIn('若已完成换侧', shot['text'])

    def test_missing_wrist_is_visibility_not_wrong_movement(self):
        points = self.asymmetric_raise()
        points[9] = None
        shot = self.feed(points)
        self.assertEqual(shot['code'], 'visibility_missing')
        self.assertEqual(shot['kind'], 'visibility')
        self.assertEqual(shot['joints'], [9])
        self.assertIn('练习建议', shot['text'])
        self.assertNotIn('动作有误', shot['text'])

    def test_missing_required_leg_uses_leg_specific_visibility(self):
        points = person()
        points[13] = None
        shot = self.feed(points, lesson='sway', required=tuple(range(5, 15)))
        self.assertEqual(shot['joints'], [13])
        self.assertIn('腿部', shot['text'])

    def test_low_confidence_still_gives_tentative_action_advice(self):
        points = self.asymmetric_raise()
        points[9][2] = .25
        shot = self.feed(points)
        self.assertEqual(shot['code'], 'uncertain_raise_height_difference')
        self.assertEqual(shot['kind'], 'tentative')
        self.assertEqual(shot['evidence']['uncertain_joint_ids'], [9])
        self.assertIn('仅供参考', shot['text'])
        self.assertIn('舒适范围', shot['text'])
        self.assertNotIn('暂不提出', shot['text'])

    def test_missing_points_always_include_concrete_lesson_steps(self):
        for course, lesson, phrase in (('baduanjin', 'raise', '向上舒展'),
                                       ('taichi', 'cloud', '划弧')):
            self.observer = AdviceObserver(course)
            shot = self.feed([None]*17, lesson=lesson)
            self.assertIn('练习建议', shot['text'])
            self.assertIn(phrase, shot['text'])
            self.assertNotIn('动作错误', shot['text'])

    def test_nan_outside_and_legacy_pairs_are_missing_evidence(self):
        for point in ([math.nan, .1, .9], [1.2, .1, .9], [.4, .1],
                      [True, .1, .9], [10**1000, .1, .9]):
            self.observer.reset()
            points = raised()
            points[9] = point
            shot = self.feed(points)
            self.assertEqual(shot['code'], 'visibility_missing')

    def test_frame_border_prompts_reframing_not_smaller_joint_angles(self):
        points = raised()
        points[9][0] = .01
        shot = self.feed(points)
        self.assertEqual(shot['code'], 'frame_edge')
        self.assertEqual(shot['kind'], 'visibility')
        self.assertEqual(shot['evidence']['edge_joint_ids'], [9])

    def test_sparse_or_frozen_stamps_never_satisfy_debounce(self):
        for _ in range(20):
            shot = self.observer.update('raise', self.asymmetric_raise(), self.stamp)
            self.assertEqual(shot['code'], 'none')
        self.observer.reset()
        for _ in range(10):
            self.stamp += .6
            shot = self.observer.update('raise', self.asymmetric_raise(), self.stamp)
            self.assertEqual(shot['code'], 'none')

    def test_backwards_time_clears_old_cue(self):
        self.feed(raised())
        shot = self.observer.update('raise', raised(), self.stamp-3)
        self.assertEqual(shot['code'], 'none')

    def test_phase_and_lesson_change_reset_previous_evidence(self):
        self.feed(raised())
        self.stamp += .05
        self.assertEqual(self.observer.update('raise', raised(), self.stamp, phase='paused')['code'], 'none')
        self.stamp += .05
        self.assertEqual(self.observer.update('single', raised(), self.stamp)['code'], 'none')

    def test_suggestion_topics_cannot_flicker_quickly(self):
        self.feed(raised(), seconds=.8)
        points = raised()
        points[9] = None
        shot = self.feed(points, seconds=.75)
        self.assertEqual(shot['code'], 'none')
        shot = self.feed(points, seconds=.9)
        self.assertEqual(shot['code'], 'visibility_missing')

    def test_snapshot_cannot_mutate_internal_state(self):
        shot = self.feed(raised())
        shot['joints'].clear()
        shot['evidence']['both_wrists_above_shoulders'] = False
        other = self.observer.snapshot()
        self.assertEqual(other['joints'], [9, 10])
        self.assertTrue(other['evidence']['both_wrists_above_shoulders'])

    def test_no_assessment_of_head_handshape_or_heel(self):
        for lesson, code, key in (('look', 'look_scope', 'head_direction_observed'),
                                  ('fist', 'fist_scope', 'hand_shape_observed'),
                                  ('heel', 'heel_scope', 'heel_lift_observed')):
            self.observer.reset()
            shot = self.feed(raised(), lesson=lesson)
            self.assertEqual(shot['code'], code)
            self.assertEqual(shot['kind'], 'scope')
            self.assertFalse(shot['evidence'][key])

    def test_bow_only_comments_on_height_after_lateral_extension(self):
        self.assertEqual(self.feed(person(), lesson='bow')['code'], 'bow_retracted_seen')
        self.observer.reset()
        points = person()
        points[9][:2], points[10][:2] = [.15, .55], [.6, .3]
        shot = self.feed(points, lesson='bow')
        self.assertEqual(shot['code'], 'bow_height_difference')
        self.assertIn('舒适高度', shot['text'])

    def test_sway_reports_planar_offset_not_real_weight_or_spine(self):
        points = person()
        points[5][0], points[6][0] = .5, .7
        shot = self.feed(points, lesson='sway')
        self.assertEqual(shot['code'], 'sway_seen')
        self.assertEqual(set(shot['evidence']), {'planar_torso_offset'})

    def test_bend_reports_hands_not_lumbar_or_touching_toes(self):
        points = person()
        points[9][1] = points[10][1] = .85
        shot = self.feed(points, lesson='bend')
        self.assertEqual(shot['code'], 'bend_descent_seen')
        self.assertIn('不以触足为目标', shot['text'])
        self.assertEqual(set(shot['evidence']), {'wrists_below_hips_torso'})

    def test_all_lesson_messages_are_json_serializable_and_no_scores(self):
        for identifier in ('prepare', 'raise', 'bow', 'single', 'look', 'sway', 'bend', 'fist', 'heel', 'finish'):
            self.observer.reset()
            shot = self.feed(person(), lesson=identifier)
            json.dumps(shot, ensure_ascii=False, allow_nan=False)
            self.assertFalse(any(k in shot for k in ('score', 'health_score', 'diagnosis', 'calories')))


class SessionAdviceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sequence = 0
        self.session = LessonSession('free', clock=self.clock)
        self.session.action('next')
        self.session.start()

    def feed(self, points, seconds=1.5, **extra):
        shot = None
        for _ in range(round(seconds/.05)):
            self.clock.advance(.05)
            self.sequence += 1
            state = dict(pid=1, sequence=self.sequence,
                         timestamp_seconds=self.clock(), points17=copy.deepcopy(points))
            state.update(extra)
            shot = self.session.update(state)
        return shot

    def test_engine_snapshot_adds_specific_advice_without_altering_counts(self):
        shot = self.feed(raised())
        self.assertEqual(shot['advice']['code'], 'raise_seen')
        self.assertEqual(shot['repetition'], 0)

    def test_stale_discontinuous_or_identity_reset_cannot_keep_old_advice(self):
        for change in ({'stale': True}, {'motion_history_discontinuity': True}, {'pid': 2}):
            self.feed(raised())
            self.clock.advance(.05)
            self.sequence += 1
            state = dict(pid=1, sequence=self.sequence, timestamp_seconds=self.clock(), points17=raised())
            state.update(change)
            shot = self.session.update(state)
            self.assertEqual(shot['advice']['code'], 'course_guidance')
            self.assertEqual(shot['advice']['evidence']['basis'], 'course_steps')
            self.assertFalse(shot['advice']['evidence']['pose_error_confirmed'])

    def test_pause_next_and_snapshot_expiration_clear_advice(self):
        self.feed(raised())
        self.assertEqual(self.session.action('pause')['advice']['code'], 'none')
        self.session.action('play')
        self.feed(raised())
        self.assertEqual(self.session.action('next')['advice']['code'], 'course_guidance')
        self.feed(raised())
        self.clock.advance(.9)
        self.assertEqual(self.session.snapshot()['advice']['code'], 'course_guidance')

    def test_very_weak_or_absent_input_does_not_disable_guidance_or_award_counts(self):
        for raw in ([None]*17, [[.5, .5, .01] for _ in range(17)]):
            shot = self.feed(raw)
            self.assertTrue(shot['advice']['text'])
            self.assertIn('练习建议', shot['advice']['text'])
            self.assertEqual(shot['repetition'], 0)

    def test_demo_has_no_coaching_or_error_comparison(self):
        self.session = LessonSession('learn', clock=self.clock)
        self.session.action('next')
        self.session.start()
        self.assertEqual(self.feed(raised())['advice']['code'], 'none')


if __name__ == '__main__':
    unittest.main()
