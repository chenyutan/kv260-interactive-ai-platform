"""Headless invariants for simulation and fresh-observation gesture safety.

Run: python -B -m unittest runner.test_core -v (from the ``new`` directory).
"""
import copy
import math
import unittest

from .engine import RunnerGame
from .controls import PoseControls


def pose(t=1., seq=1, *, left_y=.58, right_x=.42, right_y=.58,
         body_down=0., body_x=0., confidence=.8):
    points = [[.5, .3, confidence] for _ in range(17)]
    points[5] = [.58, .35 + body_down, confidence]
    points[6] = [.42, .35 + body_down, confidence]
    points[7] = [.60, .48 + body_down, confidence]
    points[8] = [.40, .48 + body_down, confidence]
    points[9] = [.59, left_y + body_down, confidence]
    points[10] = [right_x, right_y + body_down, confidence]
    points[11] = [.55, .66 + body_down, confidence]
    points[12] = [.45, .66 + body_down, confidence]
    for index in range(5,13):points[index][0] += body_x
    return dict(timestamp_seconds=t, sequence=seq, points17=points,
                available=True, latency_ms=0., pid=123)


class EngineTests(unittest.TestCase):
    def run_for(self, game, seconds, dt=1 / 60):
        for _ in range(round(seconds / dt)):
            game.update(dt)

    def empty(self):
        game = RunnerGame(10)
        game.start()
        game._next_wave_distance = 1e9
        return game

    def test_seed_restart_and_report(self):
        a, b = RunnerGame(4), RunnerGame(4)
        a.start()
        b.start()
        self.run_for(a, 4)
        self.run_for(b, 4)
        self.assertEqual(a.snapshot(), b.snapshot())
        a.restart()
        self.assertEqual((a.lives, a.score, a.player_lane), (3, 0, 1))
        self.assertEqual(a.report()['mode'], 'runner')
        self.assertEqual(a.report()['counts'], {'start': 1})

    def test_title_confirm_and_pause_stop_clock(self):
        game = RunnerGame()
        game.update(1)
        self.assertEqual(game.distance, 0)
        game.update(0, {'confirm': True})
        self.assertEqual(game.state, 'running')
        game.pause()
        before = game.snapshot()
        self.run_for(game, 1)
        self.assertEqual(before, game.snapshot())
        game.resume()
        game.update(.1)
        self.assertGreater(game.distance, 0)

    def test_lane_bounds_and_smooth_tween(self):
        game = self.empty()
        game.move(-1)
        game.update(.075)
        self.assertAlmostEqual(game.player_x, .5, places=6)
        game.update(.075)
        self.assertEqual(game.player_x, 0)
        self.assertFalse(game.move(-1))
        game.move(1)
        game.update(.05)
        current = game.player_x
        game.move(1)
        self.assertAlmostEqual(game.player_x, current)
        self.run_for(game, .15)
        self.assertEqual(game.player_x, 2)

    def test_jump_and_slide_duration(self):
        game = self.empty()
        self.assertTrue(game.jump())
        game.update(.20)
        self.assertGreater(game.jump_height, .6)
        self.assertFalse(game.slide())
        self.run_for(game, .7)
        self.assertFalse(game.jumping)
        self.assertTrue(game.slide())
        self.run_for(game, .8)
        self.assertFalse(game.sliding)

    def test_barrier_requires_jump(self):
        game = self.empty()
        game.spawn('barrier', 1, 2.7)
        game.jump()
        self.run_for(game, .3)
        self.assertEqual(game.lives, 3)
        self.assertEqual(game.counts.get('dodge'), 1)
        other = self.empty()
        other.spawn('barrier', 1, 1.)
        other.update(.15)
        self.assertEqual(other.lives, 2)

    def test_arch_requires_slide_cart_requires_lane(self):
        game = self.empty()
        game.spawn('arch', 1, 1.)
        game.slide()
        game.update(.15)
        self.assertEqual(game.lives, 3)
        game.spawn('cart', 1, 2.)
        game.update(.2)
        self.assertEqual(game.lives, 2)
        other = self.empty()
        other.spawn('cart', 1, 2.8)
        other.move(-1)
        other.update(.25)
        self.assertEqual(other.lives, 3)

    def test_shield_and_magnet_pickups(self):
        game = self.empty()
        game.spawn('shield', 1, 1)
        game.update(.1)
        self.assertGreater(game.shield_time, 0)
        game.spawn('cart', 1, 1)
        game.update(.1)
        self.assertEqual(game.lives, 3)
        self.assertEqual(game.shield_time, 0)
        game.spawn('magnet', 1, 1)
        game.update(.1)
        game.spawn('coin', 0, 5)
        game.spawn('coin', 2, 5)
        game.update(.01)
        self.assertEqual(game.coins, 2)
        self.assertGreaterEqual(game.score, 40)

    def test_three_hits_end_once(self):
        game = self.empty()
        for _ in range(3):
            game.spawn('cart', 1, 1)
            game.update(.15)
            self.run_for(game, 1.4)
        self.assertEqual(game.state, 'over')
        self.assertEqual(game.lives, 0)
        self.assertEqual(game.counts['over'], 1)
        before = game.distance
        game.update(.25)
        self.assertEqual(game.distance, before)
        game.update(0, {'confirm': True})
        self.assertEqual(game.lives, 3)
        self.assertEqual(game.state, 'running')

    def test_extreme_dt_bounded_no_tunnelling(self):
        game = self.empty()
        for dt in (math.nan, math.inf, -math.inf, -3., 'bad', True):
            game.update(dt)
        self.assertEqual(game.distance, 0.)
        game.spawn('cart', 1, 1.2)
        game.update(1000000.)
        self.assertEqual(game.lives, 2)
        self.assertLess(game.distance, 3.)
        self.assertGreater(game.dropped_seconds, 999999.)

    def test_all_waves_have_clear_lane_and_warning(self):
        game = self.empty()
        for _ in range(200):
            game.entities.clear()
            game._spawn_wave()
            occupied = {e.lane for e in game.entities if e.kind in game.OBSTACLES}
            self.assertLess(len(occupied), 3)
            self.assertTrue(all(e.z / game.MAX_SPEED > 1.8 for e in game.entities))

    def test_bounded_events_entities(self):
        game = self.empty()
        for i in range(2000):
            game.spawn('coin', i % 3, i)
            game._event('diagnostic')
        self.assertEqual(len(game.entities), game.MAX_ENTITIES)
        self.assertEqual(len(game.events), game.MAX_EVENTS)
        self.assertEqual(len(game.drain_events()), game.MAX_EVENTS)
        self.assertEqual(len(game.events), 0)

    def test_invalid_actions_dont_move_or_jump(self):
        game = self.empty()
        game.update(.1, {'move': 1, 'jump': True, 'valid': False})
        self.assertEqual(game.player_lane, 1)
        self.assertFalse(game.jumping)


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.control = PoseControls()
        self.t = 1.
        self.seq = 0

    def sample(self, **kwargs):
        self.t += .05
        self.seq += 1
        return self.control.update(pose(self.t, self.seq, **kwargs), self.t)

    def prime(self, count=7, **kwargs):
        return [self.sample(**kwargs) for _ in range(count)]

    def test_low_confidence_body_has_short_automatic_stance_capture(self):
        out = self.sample(right_x=.6, confidence=.001)
        self.assertTrue(out['valid'])
        self.assertEqual(out['calibration'], 'calibrating')
        self.assertEqual(self.prime(right_x=.6,confidence=.001)[-1]['calibration'],'ready')

    def test_body_rise_one_jump_until_landed(self):
        self.prime()
        self.assertTrue(self.sample(body_down=-.045)['jump'])
        self.assertFalse(any(x['jump'] for x in self.prime(10, body_down=-.045)))
        self.prime(4)
        self.assertTrue(self.sample(body_down=-.045)['jump'])

    def test_first_high_pose_does_not_jump(self):
        self.assertFalse(any(x['jump'] for x in self.prime(8, left_y=.28)))

    def test_confirm_requires_new_fresh_hold_and_rearms(self):
        outputs = self.prime(12, left_y=.25, right_y=.25)
        self.assertEqual(sum(x['confirm'] for x in outputs), 1)
        self.assertFalse(any(x['jump'] for x in outputs))
        self.prime(2)
        self.assertEqual(sum(x['confirm'] for x in self.prime(10, left_y=.25, right_y=.25)), 1)

    def test_duplicate_does_not_finish_confirm_or_repeat_jump(self):
        self.prime()
        packet = pose(self.t + .05, self.seq + 1, body_down=-.045)
        first = self.control.update(packet, self.t + .05)
        self.assertTrue(first['jump'])
        for offset in (.06, .10, .15, .24):
            out = self.control.update(packet, self.t + offset)
            self.assertFalse(out['jump'] or out['confirm'] or out['move'] or out['slide'])
        self.assertFalse(self.control.update(packet, self.t + .5)['valid'])

    def test_stale_missing_nan_zero_confidence_no_actions(self):
        self.prime()
        for packet in (None, {}, {'stale': True}, pose(self.t + .05, self.seq + 1, confidence=0.)):
            out = self.control.update(packet, self.t + .05)
            self.assertFalse(out['jump'] or out['slide'] or out['confirm'] or out['move'])
        self.t += .1
        self.seq += 2
        packet = pose(self.t, self.seq, left_y=.28)
        packet['points17'][9][1] = math.nan
        out = self.control.update(packet, self.t)
        self.assertFalse(out['jump'])

    def test_small_jitter_no_lane_pulses(self):
        result = [self.sample(right_x=.42 + (.003 if i % 2 else -.003)) for i in range(60)]
        self.assertEqual(sum(abs(x['move']) for x in result), 0)

    def test_right_hand_flicks_no_longer_move_the_body_controlled_runner(self):
        self.prime()
        a = [self.sample(right_x=x)['move'] for x in (.405, .39, .375, .36)]
        self.assertFalse(any(a))
        self.assertFalse(any(x['move'] for x in self.prime(10, right_x=.36)))
        b = [self.sample(right_x=x)['move'] for x in (.38, .40, .42, .44)]
        self.assertFalse(any(b))

    def test_body_descent_slide_once_and_requires_return(self):
        self.prime()
        out = [self.sample(body_down=y) for y in (.04, .08, .12, .14, .16, .16, .16, .16)]
        self.assertEqual(sum(x['slide'] for x in out), 1)
        self.assertFalse(any(x['slide'] for x in self.prime(10, body_down=.16)))
        self.prime(4)
        out = [self.sample(body_down=y) for y in (.04, .08, .12, .14, .16, .16, .16, .16)]
        self.assertEqual(sum(x['slide'] for x in out), 1)

    def test_hip_motion_without_shoulders_is_not_slide(self):
        self.prime()
        for index in range(5):
            self.t += .05
            self.seq += 1
            packet = pose(self.t, self.seq)
            packet['points17'][11][1] += .02 * index
            packet['points17'][12][1] += .02 * index
            self.assertFalse(self.control.update(packet, self.t)['slide'])

    def test_short_loss_holds_valid_only_then_times_out(self):
        self.prime()
        self.assertTrue(self.control.update(None, self.t + .1)['valid'])
        self.assertFalse(self.control.update(None, self.t + .21)['valid'])

    def test_out_of_order_and_source_gap_never_create_jump(self):
        self.prime()
        packet = pose(self.t - .1, self.seq - 1, left_y=.28)
        self.assertFalse(self.control.update(packet, self.t)['jump'])
        self.t += 1.
        self.seq += 1
        self.assertFalse(self.control.update(pose(self.t, self.seq, left_y=.28), self.t)['jump'])

    def test_future_timestamp_cannot_poison_source_watermark(self):
        self.prime()
        out = self.control.update(pose(self.t + 99999, self.seq + 99999,
                                      left_y=.28), self.t)
        self.assertFalse(out['jump'] or out['confirm'] or out['move'])
        self.assertNotEqual(self.control.sequence, self.seq + 99999)
        self.assertTrue(self.sample()['valid'])
        self.prime()
        self.assertTrue(self.sample(body_down=-.045)['jump'])

    def test_missing_hips_never_synthesizes_crouch(self):
        self.prime()
        self.t += .05
        self.seq += 1
        packet = pose(self.t, self.seq, body_down=.08)
        packet['points17'][11] = [.55, .66, 0]
        self.assertFalse(self.control.update(packet, self.t)['slide'])

    def test_identity_change_and_reset_need_fresh_jump(self):
        self.prime()
        self.control.reset()
        self.assertFalse(self.sample(left_y=.28)['jump'])
        self.prime()
        self.t += .05
        self.seq += 1
        packet = pose(self.t, self.seq, left_y=.28)
        packet['pid'] = 999
        self.assertFalse(self.control.update(packet, self.t)['jump'])

    def test_separate_source_clock_requires_latency(self):
        self.control = PoseControls(shared_clock=False)
        packet = pose(20., 3)
        packet.pop('latency_ms')
        self.assertFalse(self.control.update(packet, 1.)['valid'])
        packet['latency_ms'] = 20.
        packet['sequence'] = 4
        packet['timestamp_seconds'] = 20.1
        self.assertTrue(self.control.update(packet, 1.1)['valid'])


if __name__ == '__main__':
    unittest.main()
