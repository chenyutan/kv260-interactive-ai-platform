"""Synthetic 30 Hz COCO packets drive the real game at 60 Hz, no I/O."""
import unittest

from platformer.controls import PoseControls
from platformer.engine import Game
from platformer.levels import LEVELS, TILE
from platformer.test_controls import missing_arm, packet, pose


def left_raise(t):
    """A modest shoulder crossing; neither speed nor overhead reach is needed."""
    if t < .08:
        return (0., .50)
    if t < .30:
        a = (t - .08) / .22
        return (0., .50 - .70 * a)
    if t < .60:
        return (0., -.20)
    if t < .82:
        a = (t - .60) / .22
        return (0., -.20 + .70 * a)
    return (0., .50)


def calibrated_controls():
    controls=PoseControls()
    for frame in range(28):
        t=99.+frame/30
        result=controls.update(packet(t,pose((.25,.65))),t)
    assert result['calibration']=='ready'
    return controls


class GestureGameTests(unittest.TestCase):
    def test_right_steering_and_left_jump_cross_real_first_gap_at_30_to_60_hz(self):
        self.cross_gap(1, 402.)

    def test_personal_joystick_left_and_left_jump_cross_same_real_gap_in_reverse(self):
        self.cross_gap(-1, 528.)

    def cross_gap(self, direction, start_x):
        game, controls = Game(), calibrated_controls()
        game.start(0)
        # Isolate this real level's first gap from the earlier tutorial/combat.
        # The preceding pipe ends at x=400, and the gap is x=448..480.
        self.assertEqual(LEVELS[0].tiles, game.level.tiles)
        self.assertFalse(any(t.col in (28, 29) and t.row == 10 for t in game.level.tiles))
        self.assertTrue(any(t.col == 30 and t.row == 10 for t in game.level.tiles))
        game.player.update(x=start_x, y=10 * TILE - game.player['h'],
                           vx=0., vy=0., grounded=True)
        latest, previous_frame = None, None
        jump_controls = []
        for tick in range(76):
            elapsed = tick / 60
            source_frame = tick // 2
            if source_frame != previous_frame:
                source_t = source_frame / 30
                latest = packet(100. + source_t,
                    pose((.25+direction*.32,.65), left_raise(source_t)))
                previous_frame = source_frame
            result = controls.update(latest, 100. + elapsed)
            if result['jump']:
                jump_controls.append(dict(result))
            game.advance(1 / 60, result)
        self.assertEqual(len(jump_controls), 1)
        self.assertEqual(jump_controls[0]['move'], direction)
        self.assertEqual(game.audio_counts['jump'], 1)
        self.assertEqual(game.deaths, 0)
        if direction > 0:
            self.assertGreaterEqual(game.player['x'], 30 * TILE)
        else:
            self.assertLessEqual(game.player['x'] + game.player['w'], 28 * TILE)
        self.assertTrue(game.player['grounded'])

    def test_left_jump_reaches_physics_with_right_arm_missing_at_30_to_60_hz(self):
        game, controls = Game(), PoseControls()
        game.start(0)
        start_x = game.player['x']
        latest, previous_frame = None, None
        events = []
        for tick in range(76):
            elapsed = tick / 60
            source_frame = tick // 2
            if source_frame != previous_frame:
                source_t = source_frame / 30
                points = missing_arm(pose((.25,.65), left_raise(source_t)), 'right')
                latest = packet(100. + source_t, points)
                previous_frame = source_frame
            result = controls.update(latest, 100. + elapsed)
            events.append(result)
            game.advance(1 / 60, result)
        self.assertTrue(all(r['valid'] and r['move'] == 0 for r in events))
        self.assertEqual(sum(r['jump'] for r in events), 1)
        self.assertEqual(game.audio_counts['jump'], 1)
        self.assertEqual(game.deaths, 0)
        self.assertEqual(game.player['x'], start_x)
        self.assertTrue(game.player['grounded'])


if __name__ == '__main__':
    unittest.main()
