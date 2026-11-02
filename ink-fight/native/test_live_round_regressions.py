"""Measured pose replay with windows assigned by independent visual review.

This is a regression fixture from one session, not held-out model accuracy.
"""
import json
import copy
from pathlib import Path
import unittest
from engine import Gesture

FIXTURE=Path(__file__).with_name('fixtures')/'front_round.json'
GUARD_FIXTURE=FIXTURE.with_name('front_guard_round.json')
MIXED_FIXTURE=FIXTURE.with_name('front_mixed_round.json')
ALTERNATING_FIXTURE=FIXTURE.with_name('front_alternating_round.json')


def replay(fixture=None):
    fixture=fixture or json.loads(FIXTURE.read_text(encoding='utf-8'))
    g=Gesture();events=[]
    for pose in fixture['poses']:
        for move in g.consume(pose):
            events.append({'t':pose['timestamp_seconds']-fixture['time_origin'],'move':move})
    return fixture,events


class LiveRoundRegressions(unittest.TestCase):
    def assert_reviewed_windows(self,fixture,events):
        for window in fixture['expected_windows']:
            if not window['strict_regression']:continue
            begin,end=window['round_window_seconds']
            with self.subTest(window=window['id']):
                expected=window.get('expected_moves',[window.get('expected_move')])
                self.assertEqual([e['move'] for e in events if begin<=e['t']<=end],expected)
        for window in fixture['negative_windows']:
            begin,end=window['round_window_seconds']
            with self.subTest(negative=window['round_window_seconds']):
                self.assertFalse([e for e in events if begin<=e['t']<=end])

    def test_independently_viewed_front_actions_and_retractions(self):
        fixture,events=replay()
        self.assert_reviewed_windows(fixture,events)

    def test_same_motion_translated_and_rescaled_in_frame(self):
        fixture,_=replay()
        for scale,dx,dy in ((.9,.02,.01),(1.1,-.02,-.01)):
            transformed=copy.deepcopy(fixture)
            for pose in transformed['poses']:
                if pose:
                    for point in pose['points17']:
                        point[0]=.5+(point[0]-.5)*scale+dx
                        point[1]=.5+(point[1]-.5)*scale+dy
            _,events=replay(transformed)
            with self.subTest(scale=scale):self.assert_reviewed_windows(transformed,events)

    def test_fast_coordinated_overhead_raise_is_not_a_landmark_teleport(self):
        _,events=replay()
        high=[e for e in events if 3.5<=e['t']<=6.5]
        self.assertEqual([e['move'] for e in high],['special'])

    def test_second_live_round_compact_punches_and_guard_transitions(self):
        fixture=json.loads(GUARD_FIXTURE.read_text(encoding='utf-8'))
        _,events=replay(fixture)
        self.assert_reviewed_windows(fixture,events)

    def test_second_live_round_held_face_guard_remains_defensive(self):
        fixture=json.loads(GUARD_FIXTURE.read_text(encoding='utf-8'))
        g=Gesture();checked=0
        for pose in fixture['poses']:
            g.consume(pose)
            t=pose['timestamp_seconds']-fixture['time_origin']
            if any(a<=t<=b for a,b in ((6.4,8.5),(17.5,18.5),(21.,23.8))):
                if g.both_valid:
                    self.assertTrue(g.guard,msg=f'guard dropped at {t:.3f}s')
                    checked+=1
        self.assertGreater(checked,100)

    def test_second_live_round_position_and_scale_do_not_change_commands(self):
        fixture=json.loads(GUARD_FIXTURE.read_text(encoding='utf-8'))
        _,reference=replay(fixture)
        for scale,dx,dy in ((.9,.02,.01),(1.1,-.02,-.01)):
            transformed=copy.deepcopy(fixture)
            for pose in transformed['poses']:
                for point in pose['points17']:
                    if 0<=point[0]<=1 and 0<=point[1]<=1:
                        point[0]=.5+(point[0]-.5)*scale+dx
                        point[1]=.5+(point[1]-.5)*scale+dy
            _,events=replay(transformed)
            self.assertEqual(events,reference)

    def test_third_live_round_palm_alternating_punches_and_guard_release(self):
        fixture=json.loads(MIXED_FIXTURE.read_text(encoding='utf-8'))
        _,events=replay(fixture)
        self.assert_reviewed_windows(fixture,events)

    def test_third_live_round_position_and_scale_do_not_change_commands(self):
        fixture=json.loads(MIXED_FIXTURE.read_text(encoding='utf-8'))
        _,reference=replay(fixture)
        for scale,dx,dy in ((.9,.02,.01),(1.1,-.02,-.01)):
            transformed=copy.deepcopy(fixture)
            for pose in transformed['poses']:
                for point in pose['points17']:
                    if 0<=point[0]<=1 and 0<=point[1]<=1:
                        point[0]=.5+(point[0]-.5)*scale+dx
                        point[1]=.5+(point[1]-.5)*scale+dy
            _,events=replay(transformed)
            self.assertEqual(events,reference)


    def test_fourth_live_round_alternating_punches_are_not_palms(self):
        fixture=json.loads(ALTERNATING_FIXTURE.read_text(encoding='utf-8'))
        _,events=replay(fixture)
        # Keep the independently reviewed miss below as an expected failure,
        # not a false passing label. The comparison report still counts it.
        checked=copy.deepcopy(fixture)
        checked['expected_windows']=[w for w in fixture['expected_windows'] if w['id']!='left_32s']
        self.assert_reviewed_windows(checked,events)
        self.assertFalse([e for e in events if e['move']=='palm'])

    @unittest.expectedFailure
    def test_known_left32_visual_punch_lacks_usable_landmark_projection(self):
        fixture=json.loads(ALTERNATING_FIXTURE.read_text(encoding='utf-8'))
        _,events=replay(fixture)
        self.assertEqual([e['move'] for e in events if 31.85<=e['t']<=32.7],['left'])

    def test_fourth_live_round_position_and_scale_do_not_change_commands(self):
        fixture=json.loads(ALTERNATING_FIXTURE.read_text(encoding='utf-8'))
        _,reference=replay(fixture)
        for scale,dx,dy in ((.9,.02,.01),(1.1,-.02,-.01)):
            transformed=copy.deepcopy(fixture)
            for pose in transformed['poses']:
                if not pose:continue
                for point in pose['points17']:
                    if 0<=point[0]<=1 and 0<=point[1]<=1:
                        point[0]=.5+(point[0]-.5)*scale+dx
                        point[1]=.5+(point[1]-.5)*scale+dy
            _,events=replay(transformed)
            self.assertEqual(events,reference)


if __name__=='__main__':unittest.main()
