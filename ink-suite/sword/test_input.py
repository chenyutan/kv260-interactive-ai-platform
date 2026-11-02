"""Offline safety checks for observed-frame controls and gesture edges."""
import json
import unittest

from .input import InputTracker


def observation(stamp, *, spread=False, pid=7, person=1, dx=0., scale=1.):
    points = [[.5, .5, .9] for _ in range(17)]
    points[5] = [.42, .40, .9]
    points[6] = [.58, .40, .9]
    points[9] = [.73 if spread else .54, .40 if spread else .64, .9]
    points[10] = [.27 if spread else .44, .40 if spread else .60, .9]
    for p in points:
        p[0], p[1] = .5 + (p[0] - .5) * scale + dx, .4 + (p[1] - .4) * scale
    return dict(points17=points, timestamp_seconds=1000. + stamp, sequence=round(stamp * 1000),
                pid=pid, person_id=person, available=True, stale=False, latency_ms=25)


def roi_observation(stamp, **kwargs):
    pose = observation(stamp, **kwargs)
    pose['roi_selection'] = dict(
        selector_version='hourglass-causal-roi-continuity-v2-partial-recovery',
        reason='other_roi_required', motion_history_discontinuity=True, stream_reset=False,
        continuity=dict(stream_reset=False, roi_changed=True, reliable=True,
                        disagreement_shoulders=.10))
    return pose


class InputTests(unittest.TestCase):
    def test_default_waiting_and_bad_numbers(self):
        t = InputTracker()
        self.assertEqual(t.update(None, 0.)['status'], 'waiting')
        self.assertFalse(t.update({}, float('nan'))['valid'])
        broken = observation(0)
        broken['points17'][10][0] = float('inf')
        self.assertFalse(t.update(broken, 0)['valid'])

    def test_right_wrist_mirror_and_body_relative_scale(self):
        a = InputTracker().update(observation(0), 0)
        b = InputTracker().update(observation(0, scale=.8, dx=.03), 0)
        self.assertAlmostEqual(a['x'], b['x'])
        self.assertAlmostEqual(a['y'], b['y'])
        self.assertGreater(a['x'], .5)
        unmirrored = InputTracker(mirrored=False).update(observation(0), 0)
        self.assertAlmostEqual(1 - a['x'], unmirrored['x'])
        self.assertFalse(a['special'])
        json.dumps(a, allow_nan=False)

    def test_hand_under_right_shoulder_is_horizontal_center(self):
        pose = observation(0)
        pose['points17'][10][0] = pose['points17'][6][0]
        self.assertEqual(InputTracker().update(pose, 0)['x'], .5)

    def test_repeated_discontinuity_frame_retains_control_hold(self):
        t = InputTracker()
        t.update(observation(0), 0)
        pose = observation(.05)
        pose['motion_history_discontinuity'] = True
        self.assertTrue(t.update(pose, .05)['valid'])
        self.assertTrue(t.update(pose, .10)['valid'])
        self.assertFalse(t.update(pose, .30)['valid'])

    def test_strict_same_person_roi_change_keeps_measured_flight(self):
        t = InputTracker()
        before = t.update(observation(0), 0)
        pose = roi_observation(.1, dx=.01, scale=1.05)
        pose['points17'][10][0] += .03
        changed = t.update(pose, .1)
        self.assertEqual(changed['status'], 'tracking')
        self.assertTrue(changed['valid'])
        self.assertFalse(changed['observation_missing'])
        self.assertFalse(changed['continuity_reset'])
        self.assertTrue(changed['continuous_roi'])
        self.assertTrue(changed['motion_reset'])
        self.assertEqual(changed['continuity_reason'], 'measured_roi_continuity')
        self.assertNotEqual(changed['x'], before['x'])
        self.assertEqual(changed['source_key'], (7, 100, 1000.1))
        self.assertFalse(changed['special'])
        cached = t.update(pose, .15)
        self.assertFalse(cached['continuous_roi'])
        self.assertFalse(cached['motion_reset'])
        self.assertFalse(cached['continuity_reset'])
        json.dumps(changed, allow_nan=False)

    def test_continuous_roi_discards_armed_and_pending_gesture(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                t = InputTracker()
                t.update(observation(0), 0)
                t.update(observation(.1), .1)
                if pending:
                    t.update(observation(.12, spread=True), .12)
                switched = t.update(roi_observation(.15, spread=True), .15)
                self.assertTrue(switched['continuous_roi'])
                self.assertFalse(switched['special'])
                self.assertFalse(t.update(observation(.25, spread=True), .25)['special'])
                t.update(observation(.30), .30)
                t.update(observation(.40), .40)
                t.update(observation(.45, spread=True), .45)
                self.assertTrue(t.update(observation(.55, spread=True), .55)['special'])

    def test_roi_continuity_metadata_must_prove_known_safe_fallback(self):
        cases = [
            ('version', 'selector_version', 'unknown'),
            ('reason', 'reason', 'same_roi'),
            ('stream', 'stream_reset', True),
            ('missing_stream', 'stream_reset', None),
            ('continuity_missing', 'continuity', None),
            ('continuity_bad', 'continuity', []),
        ]
        nested_cases = [
            ('stream', 'stream_reset', True),
            ('missing_stream', 'stream_reset', None),
            ('unchanged', 'roi_changed', False),
            ('changed_integer', 'roi_changed', 1),
            ('unreliable', 'reliable', False),
            ('reliable_integer', 'reliable', 1),
            ('disagreement_large', 'disagreement_shoulders', .201),
            ('disagreement_negative', 'disagreement_shoulders', -.01),
            ('disagreement_nan', 'disagreement_shoulders', float('nan')),
            ('disagreement_string', 'disagreement_shoulders', '.1'),
            ('disagreement_bool', 'disagreement_shoulders', True),
        ]
        for nested, mutations in ((False, cases), (True, nested_cases)):
            for name, key, value in mutations:
                with self.subTest(nested=nested, case=name):
                    t = InputTracker(); t.update(observation(0), 0)
                    pose = roi_observation(.1, spread=True)
                    target = pose['roi_selection']['continuity'] if nested else pose['roi_selection']
                    target[key] = value
                    result = t.update(pose, .1)
                    self.assertTrue(result['continuity_reset'])
                    self.assertFalse(result['continuous_roi'])
                    self.assertTrue(result['motion_reset'])
                    self.assertFalse(result['special'])

    def test_roi_continuity_never_overrides_identity_or_source_reset(self):
        for mode in ('person', 'pid', 'timestamp', 'sequence', 'top_history', 'top_stream'):
            with self.subTest(mode=mode):
                t = InputTracker(); t.update(observation(.1), .1)
                pose = roi_observation(.2, spread=True)
                if mode == 'person': pose['person_id'] = 2
                if mode == 'pid': pose['pid'] = 8
                if mode == 'timestamp': pose['timestamp_seconds'] = 999.
                if mode == 'sequence': pose['sequence'] = 1
                if mode == 'top_history': pose['motion_history_discontinuity'] = True
                if mode == 'top_stream': pose['stream_reset'] = True
                result = t.update(pose, .2)
                self.assertTrue(result['continuity_reset'])
                self.assertFalse(result['continuous_roi'])
                self.assertFalse(result['special'])

    def test_roi_exception_requires_valid_advancing_source_identity(self):
        for mode in ('missing_pid', 'bool_pid', 'float_pid', 'zero_pid',
                     'same_sequence', 'new_bool_sequence', 'new_float_sequence',
                     'old_bool_sequence', 'old_float_sequence'):
            with self.subTest(mode=mode):
                old, pose = observation(0), roi_observation(.1)
                if mode.endswith('pid'):
                    pid = dict(missing_pid=None, bool_pid=True, float_pid=7., zero_pid=0)[mode]
                    old['pid'] = pose['pid'] = pid
                elif mode == 'same_sequence': pose['sequence'] = old['sequence']
                elif mode == 'new_bool_sequence': pose['sequence'] = True
                elif mode == 'new_float_sequence': pose['sequence'] = 100.
                elif mode == 'old_bool_sequence': old['sequence'] = False
                elif mode == 'old_float_sequence': old['sequence'] = 0.
                t = InputTracker(); t.update(old, 0)
                result = t.update(pose, .1)
                self.assertTrue(result['continuity_reset'])
                self.assertFalse(result['continuous_roi'])
        old, pose = observation(0), roi_observation(.1)
        old.pop('person_id'); pose.pop('person_id')
        t = InputTracker(); t.update(old, 0)
        self.assertTrue(t.update(pose, .1)['continuous_roi'])

    def test_roi_proof_needs_recent_measured_anchor_history(self):
        for mode in ('no_prior', 'long_gap', 'equal_stamp', 'missing_between', 'old_bad_hip'):
            with self.subTest(mode=mode):
                t = InputTracker()
                old = observation(0)
                if mode == 'old_bad_hip': old['points17'][11][2] = .249
                if mode != 'no_prior': t.update(old, 0)
                if mode == 'missing_between': t.update(None, .05)
                pose = roi_observation(.252 if mode == 'long_gap' else .1)
                if mode == 'equal_stamp': pose['timestamp_seconds'] = old['timestamp_seconds']
                result = t.update(pose, .1)
                self.assertTrue(result['continuity_reset'])
                self.assertFalse(result['continuous_roi'])
                self.assertFalse(result['special'])

    def test_roi_proof_requires_each_old_and_new_torso_anchor(self):
        for older in (False, True):
            for joint in (5, 6, 11, 12):
                for invalid in ('confidence', 'coordinate', 'nan', 'shape'):
                    with self.subTest(older=older, joint=joint, invalid=invalid):
                        old, pose = observation(0), roi_observation(.1)
                        target = (old if older else pose)['points17']
                        if invalid == 'confidence': target[joint][2] = .249
                        if invalid == 'coordinate': target[joint][0] = -1.
                        if invalid == 'nan': target[joint][1] = float('nan')
                        if invalid == 'shape': target[joint] = [.5, .5]
                        t = InputTracker(); t.update(old, 0)
                        result = t.update(pose, .1)
                        self.assertTrue(result['continuity_reset'])
                        self.assertFalse(result['continuous_roi'])
                        self.assertFalse(result['special'])
        old, pose = observation(0), roi_observation(.1)
        for points in (old['points17'], pose['points17']):
            for joint in (5, 6, 11, 12): points[joint][2] = .25
        pose['points17'][10][2] = .20
        t = InputTracker(); t.update(old, 0)
        result = t.update(pose, .1)
        self.assertTrue(result['continuous_roi'])
        self.assertEqual(result['status'], 'tracking')

    def test_roi_proof_checks_scale_and_each_anchor_distance(self):
        for mode in ('small', 'large', 'shoulder', 'hip'):
            with self.subTest(mode=mode):
                t = InputTracker(); t.update(observation(0), 0)
                pose = roi_observation(.1, scale=.69 if mode == 'small' else
                                       1.41 if mode == 'large' else 1.)
                if mode in ('shoulder', 'hip'):
                    pose['points17'][5 if mode == 'shoulder' else 11][1] += .18
                result = t.update(pose, .1)
                self.assertTrue(result['continuity_reset'])
                self.assertFalse(result['continuous_roi'])

    def test_bad_roi_control_stays_missing_on_repeated_source_key(self):
        t = InputTracker(); t.update(observation(0), 0)
        pose = roi_observation(.1); pose['points17'][10][2] = 0.
        for now in (.1, .12, .15):
            result = t.update(pose, now)
            self.assertTrue(result['observation_missing'])
            self.assertNotEqual(result['status'], 'tracking')
            self.assertFalse(result['special'])
        pose['points17'][10][2] = .9
        self.assertTrue(t.update(pose, .18)['observation_missing'])
        self.assertEqual(t.update(observation(.2), .2)['status'], 'tracking')

    def test_identity_change_cannot_inherit_control_hold(self):
        for mode in ('person', 'pid'):
            with self.subTest(mode=mode):
                t = InputTracker(); t.update(observation(0), 0)
                pose = roi_observation(.1, person=2 if mode == 'person' else 1,
                                       pid=8 if mode == 'pid' else 7)
                pose['points17'][10][2] = 0.
                result = t.update(pose, .1)
                self.assertFalse(result['valid'])
                self.assertTrue(result['observation_missing'])
                self.assertTrue(result['continuity_reset'])
                self.assertFalse(t.update(pose, .12)['valid'])

    def test_short_hold_then_loss(self):
        t = InputTracker()
        first = t.update(observation(0), 0)
        held = t.update(None, .10)
        self.assertTrue(held['valid'])
        self.assertEqual(held['status'], 'holding')
        self.assertEqual((held['x'], held['y']), (first['x'], first['y']))
        self.assertFalse(held['special'])
        self.assertFalse(t.update(None, .241)['valid'])

    def test_cached_healthy_hold_is_distinct_from_missing_observation(self):
        t = InputTracker(); pose = observation(0)
        t.update(pose, 0)
        healthy = t.update(pose, .14)
        missing = t.update(None, .15)
        self.assertEqual(healthy['status'], 'holding')
        self.assertFalse(healthy['observation_missing'])
        self.assertTrue(missing['observation_missing'])
        self.assertFalse(healthy['special'])

    def test_identity_and_history_changes_report_one_continuity_reset(self):
        for mode in ('person', 'pid', 'rollback', 'sequence', 'history', 'roi_history'):
            with self.subTest(mode=mode):
                t = InputTracker(); t.update(observation(.1), .1)
                pose = observation(.2)
                if mode == 'person': pose['person_id'] = 2
                if mode == 'pid': pose['pid'] = 8
                if mode == 'rollback': pose['timestamp_seconds'] = 900.
                if mode == 'sequence': pose['sequence'] = 1
                if mode == 'history': pose['motion_history_discontinuity'] = True
                if mode == 'roi_history': pose['roi_selection'] = dict(motion_history_discontinuity=True)
                changed = t.update(pose, .2)
                self.assertTrue(changed['continuity_reset'])
                self.assertFalse(changed['special'])
                self.assertFalse(t.update(pose, .22)['continuity_reset'])

    def test_repeated_rejected_pose_never_becomes_a_healthy_cached_pose(self):
        for joint in (5, 6, 10):
            with self.subTest(joint=joint):
                t = InputTracker(); t.update(observation(0), 0)
                broken = observation(.05); broken['points17'][joint][2] = 0.
                for now in (.05, .066, .09, .14, .25):
                    result = t.update(broken, now)
                    self.assertTrue(result['observation_missing'])
                    self.assertNotEqual(result['status'], 'tracking')
                    self.assertFalse(result['special'])
                repaired = t.update(observation(.30), .30)
                self.assertEqual(repaired['status'], 'tracking')
                self.assertFalse(repaired['observation_missing'])

    def test_repeated_timestamp_cannot_refresh_freshness(self):
        t = InputTracker()
        t.update(observation(0), 0)
        for i in range(1, 10):
            s = observation(0)
            s['sequence'] = i
            out = t.update(s, i * .04)
            self.assertFalse(out['special'])
        self.assertFalse(out['valid'])
        self.assertEqual(out['source_key'], (7, 0, 1000.))

    def test_duplicate_frames_do_not_complete_gesture_debounce(self):
        t = InputTracker()
        t.update(observation(0), 0)
        t.update(observation(.1), .1)
        pose = observation(.14, spread=True)
        self.assertFalse(t.update(pose, .14)['special'])
        self.assertFalse(t.update(pose, .23)['special'])
        self.assertTrue(t.update(observation(.24, spread=True), .24)['special'])

    def test_spread_rising_edge_once_and_rearm(self):
        t = InputTracker()
        outputs = []
        for now, spread in [(0., False), (.1, False), (.15, True), (.24, True),
                            (.30, True), (.40, True), (.45, False), (.54, False),
                            (.6, True), (.69, True)]:
            outputs.append(t.update(observation(now, spread=spread), now)['special'])
        self.assertEqual([i for i, x in enumerate(outputs) if x], [3, 9])

    def test_arriving_in_spread_pose_does_not_cast(self):
        t = InputTracker()
        for now in (0., .1, .2, .3):
            self.assertFalse(t.update(observation(now, spread=True), now)['special'])

    def test_loss_and_switch_disarm_spread(self):
        for mode in ('missing', 'person', 'restart', 'jump', 'rollback'):
            with self.subTest(mode=mode):
                t = InputTracker()
                t.update(observation(0), 0)
                t.update(observation(.1), .1)
                if mode == 'missing':
                    t.update(None, .12)
                for now in (.15, .25, .35):
                    s = observation(now, spread=True, person=2 if mode == 'person' else 1,
                                    pid=9 if mode == 'restart' else 7, dx=.3 if mode == 'jump' else 0)
                    if mode == 'rollback':
                        s['timestamp_seconds'] -= 5.
                    self.assertFalse(t.update(s, now)['special'])

    def test_stale_latency_and_partial_hand_freeze(self):
        for flag in ('stale', 'latency', 'wrist'):
            with self.subTest(flag=flag):
                t = InputTracker()
                t.update(observation(0), 0)
                s = observation(.1)
                if flag == 'stale':
                    s['stale'] = True
                elif flag == 'latency':
                    s['latency_ms'] = 1200
                else:
                    s['points17'][10][2] = 0.
                self.assertEqual(t.update(s, .1)['status'], 'holding')
                self.assertFalse(t.update(s, .3)['valid'])

    def test_body_confidence_and_bounds(self):
        t = InputTracker()
        s = observation(0)
        s['points17'][10] = [0., 1., .9]
        out = t.update(s, 0.)
        self.assertEqual((out['x'], out['y']), (1., 1.))
        s = observation(.1)
        s['points17'][5][2] = .01
        self.assertEqual(t.update(s, .1)['status'], 'holding')


if __name__ == '__main__':
    unittest.main()
