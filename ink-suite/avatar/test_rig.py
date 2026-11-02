"""Saved-observation regressions plus explicit corrupt-input unit cases."""
import copy
import json
import math
from pathlib import Path
import unittest
from .rig import Rig, neutral_pose


class RigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = json.loads((Path(__file__).parent / 'fixtures' / 'recorded_poses.json').read_text(encoding='utf-8'))['samples']

    def state(self, name='resting', t=10., sequence=1):
        return dict(copy.deepcopy(self.samples[name]['state']), sequence=sequence, timestamp_seconds=t)

    def settle(self, rig, name='raised', start=10.):
        for i in range(60):
            pose = rig.update(self.state(name, start + i / 30., i), start + i / 30.)
        return pose

    def assert_finite(self, pose):
        for point in pose.joints.values():
            self.assertTrue(all(math.isfinite(v) for v in point))

    def test_fixed_bone_lengths_for_every_recorded_pose(self):
        for name in self.samples:
            pose = self.settle(Rig(), name)
            for a, b, expected in ((5, 7, 79), (6, 8, 79), (7, 9, 73), (8, 10, 73),
                                    (11, 13, 105), (12, 14, 105), (13, 15, 105), (14, 16, 105)):
                self.assertAlmostEqual(math.dist(pose.joints[a], pose.joints[b]), expected)

    def test_recorded_raise_moves_both_hands_above_shoulders(self):
        pose = self.settle(Rig())
        self.assertLess(pose.joints[9][1], pose.joints[5][1] - 60)
        self.assertLess(pose.joints[10][1], pose.joints[6][1] - 60)

    def test_default_mirror_uses_screen_direction(self):
        rig = Rig(); state = self.state('spread')
        pose = self.settle(rig, 'spread')
        p = state['points17']
        for sh, el in ((5, 7), (6, 8)):
            self.assertLessEqual((p[el][0] - p[sh][0]) * (pose.joints[el][0] - pose.joints[sh][0]), 0)

    def test_duplicate_frame_never_refreshes_tracking(self):
        rig = Rig(); s = self.state('raised'); rig.update(s, 10.)
        for i in range(1, 70): pose = rig.update(s, 10. + i / 30.)
        self.assertEqual(pose.status, 'lost')
        self.assertFalse(pose.tracking)
        self.assertEqual(pose.joints, neutral_pose().joints)
        self.assertLess(pose.opacity, 1.)

    def test_frozen_timestamp_with_changing_sequence_still_expires(self):
        rig = Rig()
        for i in range(80):
            pose = rig.update(self.state('raised', 10., i), 10. + i / 30.)
        self.assertEqual(pose.status, 'lost')
        self.assertEqual(pose.joints, neutral_pose().joints)

    def test_upper_body_shoulder_tilt_has_correct_mirror_direction(self):
        rig = Rig()
        for i in range(40):
            t = 10. + i / 30.; s = self.state('resting', t, i)
            s['points17'][5][1] = .4; s['points17'][6][1] = .35
            for j in range(11, 17): s['points17'][j] = [-1, -1, 0]
            pose = rig.update(s, t)
        self.assertGreater(pose.joints[5][1], pose.joints[6][1])

    def test_absent_state_returns_to_neutral(self):
        rig = Rig(); self.settle(rig)
        for i in range(80): pose = rig.update(None, 12. + i / 30.)
        self.assertEqual(pose.joints, neutral_pose().joints)

    def test_missing_leg_points_keep_natural_stance(self):
        rig = Rig()
        for i in range(60):
            s = self.state('raised', 10. + i / 30., i)
            for j in range(11, 17): s['points17'][j] = [-1, -1, 0]
            pose = rig.update(s, 10. + i / 30.)
        neutral = neutral_pose()
        for i in (11, 12, 13, 14, 15, 16):
            self.assertAlmostEqual(pose.joints[i][1], neutral.joints[i][1], delta=8)
        self.assertIn('上半身', pose.message)
        self.assertLess(pose.joints[9][1], pose.joints[5][1])

    def test_low_confidence_arm_is_held_then_expires(self):
        rig = Rig(); high = self.settle(rig)
        for i in range(50):
            t = 12. + i / 30.; s = self.state('raised', t, i + 100)
            for j in (7, 9): s['points17'][j][2] = .01
            pose = rig.update(s, t)
            if i == 1:
                self.assertAlmostEqual(pose.joints[9][1], high.joints[9][1], delta=2.)
        self.assertGreater(pose.joints[9][1], pose.joints[5][1] + 100)
        self.assertLess(pose.joints[10][1], pose.joints[6][1] - 60)

    def test_person_id_change_clears_all_held_bones(self):
        rig = Rig(); self.settle(rig)
        s = self.state('resting', 12., 100); s['person_id'] = 'new-person'
        s['points17'][7] = s['points17'][9] = [-1, -1, 0]
        pose = rig.update(s, 12.)
        self.assertGreater(pose.joints[9][1], pose.joints[5][1] + 100)
        self.assertEqual(rig.discontinuities, 1)

    def test_roi_discontinuity_clears_held_pose(self):
        rig = Rig(); self.settle(rig)
        s = self.state('resting', 12., 100)
        s['roi_selection'] = {'motion_history_discontinuity': True}
        for j in (7, 9): s['points17'][j] = [-1, -1, 0]
        pose = rig.update(s, 12.)
        self.assertGreater(pose.joints[9][1], pose.joints[5][1] + 100)

    def test_backend_restart_clears_held_pose(self):
        rig = Rig(); self.settle(rig)
        s = self.state('resting', 12., 100); s['pid'] = -999
        for j in (7, 9): s['points17'][j] = [-1, -1, 0]
        pose = rig.update(s, 12.)
        self.assertGreater(pose.joints[9][1], pose.joints[5][1] + 100)

    def test_torso_jump_clears_held_pose(self):
        rig = Rig(); self.settle(rig)
        s = self.state('raised', 12., 100)
        for j in (5, 6): s['points17'][j][0] -= .3
        for j in (7, 9): s['points17'][j] = [-1, -1, 0]
        pose = rig.update(s, 12.)
        self.assertGreater(pose.joints[9][1], pose.joints[5][1] + 100)

    def test_malformed_nan_infinity_zero_shoulders_never_crash(self):
        invalid = [None, [], {}, 'invalid', [[0, 0, 1]] * 17,
                   [[math.nan, .5, 1]] * 17, [[.5, math.inf, 1]] * 17,
                   [[.5, .5, 'bad']] * 17]
        for points in invalid:
            rig = Rig(); s = self.state(); s['points17'] = points
            pose = rig.update(s, 10.)
            self.assert_finite(pose); self.assertFalse(pose.tracking)

    def test_invalid_monotonic_time_and_rollback_reset(self):
        for now in (math.nan, math.inf, -math.inf, None, True, 'now'):
            pose = Rig().update(self.state(), now)
            self.assert_finite(pose)
        rig = Rig(); self.settle(rig)
        pose = rig.update(None, 1.)
        self.assertEqual(pose.joints, neutral_pose().joints)

    def test_reversed_source_time_resets(self):
        rig = Rig(); self.settle(rig)
        s = self.state('resting', 5., 100)
        for j in (7, 9): s['points17'][j] = [-1, -1, 0]
        pose = rig.update(s, 12.)
        self.assertGreater(pose.joints[9][1], pose.joints[5][1] + 100)

    def test_advertised_stale_and_excessive_latency_rejected(self):
        for flags in ({'stale': True}, {'available': False}, {'latency_ms': 1500}):
            s = dict(self.state(), **flags)
            self.assertFalse(Rig().update(s, 10.).tracking)

    def test_frame_rate_independent_following(self):
        # At steady input the normalized pose converges across timer rates.
        outputs = []
        for rate in (20, 30, 60):
            rig = Rig()
            for i in range(rate * 2):
                t = 10. + i / rate
                pose = rig.update(self.state('raised', t, i), t)
            outputs.append(pose)
        for pose in outputs[1:]:
            self.assertLess(math.dist(outputs[0].joints[9], pose.joints[9]), 1.)


if __name__ == '__main__':
    unittest.main()
