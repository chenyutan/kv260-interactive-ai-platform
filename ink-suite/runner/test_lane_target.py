"""Absolute body lanes, with unchanged relative keyboard/gamepad actions."""
import unittest

from .engine import RunnerGame


class LaneTargetTests(unittest.TestCase):
    def game(self):
        game=RunnerGame(10);game.start();game._next_wave_distance=1e9
        game.drain_events()
        return game

    def test_returning_body_to_center_returns_from_both_side_lanes(self):
        game=self.game()
        for target in (2,1,0,1):
            game.update(.15,dict(valid=True,lane_target=target))
            self.assertEqual(game.player_lane,target)
            self.assertAlmostEqual(game.player_x,target)
        self.assertEqual([e['lane'] for e in game.drain_events()],[2,1,0,1])

    def test_held_target_does_not_repeat_events_or_restart_interpolation(self):
        game=self.game()
        for _ in range(30):game.update(.01,dict(valid=True,lane_target=2))
        self.assertAlmostEqual(game.player_x,2)
        self.assertEqual(game._lane_elapsed,game.LANE_SECONDS)
        self.assertEqual([e['kind'] for e in game.drain_events()],['lane'])

    def test_direct_left_to_right_and_reverse_are_continuous(self):
        game=self.game();game.set_lane(0);game.update(.15);game.drain_events()
        self.assertTrue(game.set_lane(2));self.assertEqual(game.player_x,0)
        game.update(.075);self.assertAlmostEqual(game.player_x,1)
        current=game.player_x;self.assertTrue(game.set_lane(0))
        self.assertEqual(game.player_x,current)
        game.update(.15);self.assertAlmostEqual(game.player_x,0)
        self.assertEqual([e['direction'] for e in game.drain_events()],[1,-1])

    def test_target_owns_lane_even_when_move_disagrees(self):
        game=self.game()
        game.update(.1,dict(valid=True,lane_target=0,move=1))
        self.assertEqual(game.player_lane,0)
        game.update(.1,dict(valid=True,lane_target=0,move=1))
        self.assertEqual(game.player_lane,0)
        self.assertEqual(game.counts['lane'],1)

    def test_explicit_bad_or_missing_target_never_falls_back_to_move(self):
        game=self.game()
        for target in (None,True,False,-1,3,.0,1.,'1',[],{}):
            with self.subTest(target=target):
                self.assertFalse(game.set_lane(target))
                game.update(.01,dict(valid=True,lane_target=target,move=1))
                self.assertEqual(game.player_lane,1)
        self.assertNotIn('lane',game.counts)

    def test_relative_actions_without_target_keep_the_original_interface(self):
        game=self.game()
        game.update(.01,dict(valid=True,move=1));self.assertEqual(game.player_lane,2)
        game.update(.01,dict(valid=True,move=-1));self.assertEqual(game.player_lane,1)
        game.update(.01,dict(valid=True,move=-1));self.assertEqual(game.player_lane,0)
        game.update(.01,dict(valid=True,move=-1));self.assertEqual(game.player_lane,0)
        self.assertEqual(game.counts['lane'],3)

    def test_invalid_pose_and_non_running_states_cannot_move(self):
        game=self.game();game.update(.01,dict(valid=False,lane_target=2))
        self.assertEqual(game.player_lane,1)
        for phase in ('title','paused','over'):
            with self.subTest(phase=phase):
                game.state=phase
                self.assertFalse(game.set_lane(2))
                game.update(.1,dict(valid=True,lane_target=2))
                self.assertEqual(game.player_lane,1)

    def test_body_target_can_coexist_with_jump_or_slide(self):
        game=self.game();game.update(0,dict(valid=True,lane_target=0,jump=True))
        self.assertEqual(game.player_lane,0);self.assertTrue(game.jumping)
        other=self.game();other.update(0,dict(valid=True,lane_target=2,slide=True))
        self.assertEqual(other.player_lane,2);self.assertTrue(other.sliding)


if __name__=='__main__':unittest.main()
