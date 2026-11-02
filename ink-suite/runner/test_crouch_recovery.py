"""End-to-end recovery of crouches, keeping source and engine safety gates."""
import unittest
from .controls import PoseControls
from .engine import RunnerGame
from .test_core import pose


class CrouchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.c = PoseControls()
        self.g = RunnerGame(20261026)
        self.g.start()
        self.g._next_wave_distance = 1e9
        self.t = 10.
        self.seq = 0

    def sample(self, dy=0., width=1., *, dt=.05, phase='running', bad=False):
        self.t += dt
        self.seq += 1
        packet = pose(self.t, self.seq, body_down=dy)
        for index in (5, 6):
            packet['points17'][index][0] = .5+(packet['points17'][index][0]-.5)*width
        if bad:
            packet['points17'][11][2] = 0.
        action = self.c.update(packet, self.t, phase=phase)
        self.g.update(dt, action)
        return action

    def prime(self):
        for _ in range(10):
            self.sample()

    def test_narrow_crouch_reaches_game_engine_once(self):
        self.prime()
        actions = [self.sample(.12, .78) for _ in range(14)]
        self.assertEqual(sum(a['slide'] for a in actions), 1)
        self.assertEqual(self.g.report()['counts'].get('slide'), 1)

    def test_stance_offset_allows_a_second_executed_crouch(self):
        self.prime()
        for _ in range(14):
            self.sample(.12)
        for _ in range(20):
            self.sample(.03)
        actions = [self.sample(.12) for _ in range(14)]
        self.assertEqual(sum(a['slide'] for a in actions), 1)
        self.assertEqual(self.g.report()['counts'].get('slide'), 2)
        self.assertFalse(any(a['jump'] for a in actions))

    def test_narrow_to_normal_held_crouch_does_not_repeat(self):
        self.prime()
        rows = [self.sample(.12, .78) for _ in range(14)]
        rows += [self.sample(.12) for _ in range(40)]
        self.assertEqual(sum(a['slide'] for a in rows), 1)
        self.assertEqual(self.g.report()['counts'].get('slide'), 1)

    def test_missing_torso_does_not_complete_pending_narrow_crouch(self):
        self.prime()
        self.sample(.12, .78)
        self.sample(.12, .78)
        self.sample(.12, .78, bad=True)
        recovery = self.sample(.12, .78)
        self.assertEqual(recovery['reason'], 'pose_recovered')
        self.assertFalse(recovery['slide'])
        self.assertFalse(any(self.sample(.12, .78)['slide'] for _ in range(2)))

    def test_duplicate_source_cannot_finish_narrow_crouch(self):
        self.prime()
        self.sample(.12, .78)
        packet = pose(self.t, self.seq, body_down=.12)
        for index in (5, 6):
            packet['points17'][index][0] = .5+(packet['points17'][index][0]-.5)*.78
        for offset in (.03, .07, .12, .18):
            self.assertFalse(self.c.update(packet, self.t+offset, phase='running')['slide'])
        self.assertFalse(self.g.sliding)

    def test_title_and_result_cannot_emit_narrow_crouch(self):
        for phase in ('title', 'over'):
            with self.subTest(phase=phase):
                self.setUp()
                self.prime()
                rows = [self.sample(.12, .78, phase=phase) for _ in range(14)]
                self.assertFalse(any(a['slide'] or a['jump'] or a['move'] for a in rows))

    def test_short_prejump_dip_after_relaxed_return_does_not_slide(self):
        self.prime()
        for _ in range(14):
            self.sample(.12)
        for _ in range(20):
            self.sample(.03)
        rows = [self.sample(y) for y in (.04, .08, .12, .06, .02, -.02, -.05, -.065)]
        self.assertFalse(any(a['slide'] for a in rows))
        self.assertEqual(sum(a['jump'] for a in rows), 1)

    def test_five_fps_narrow_crouch_needs_fresh_observations(self):
        for _ in range(8):
            self.sample(dt=.2)
        rows = [self.sample(.12, .78, dt=.2) for _ in range(8)]
        self.assertFalse(any(a['slide'] for a in rows[:3]))
        self.assertEqual(sum(a['slide'] for a in rows), 1)

    def test_narrow_crouch_after_jump_recovers_without_another_jump(self):
        self.prime()
        self.assertTrue(self.sample(-.05)['jump'])
        for _ in range(16):
            self.sample(-.05)
        rows = [self.sample(.12, .78) for _ in range(18)]
        self.assertEqual(sum(a['slide'] for a in rows), 1)
        self.assertFalse(any(a['jump'] for a in rows))


if __name__ == '__main__':
    unittest.main()
