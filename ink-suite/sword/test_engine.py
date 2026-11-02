"""Simulation lifecycle, deterministic stepping, and input safety contracts."""
import json
import unittest

from .engine import Game, MAX_EFFECTS
from .input import InputTracker
from .test_input import observation


def control(x=.50, y=.78, **kwargs):
    out = dict(x=(x - .07) / .86, y=(y - .22) / .62, valid=True,
               status='tracking', special=False)
    out.update(kwargs)
    return out


def advance(game, seconds, c=None, fps=60):
    c = c or control()
    for _ in range(round(seconds * fps)):
        game.update(1 / fps, c)


def isolated_game(duration=75):
    g = Game(duration=duration)
    g.start(0)
    g._next_spawn = g._next_elite = g._boss_at = 100000.
    return g


class EngineTests(unittest.TestCase):
    def test_ready_requires_explicit_start_and_countdown_freezes_without_hand(self):
        g = Game()
        advance(g, 5)
        self.assertEqual((g.phase, g.time, g.shots), ('ready', 0., []))
        g.start(3)
        advance(g, 1)
        self.assertAlmostEqual(g.countdown, 2.)
        self.assertEqual(g.time, 0)
        advance(g, 1, control(valid=False))
        self.assertAlmostEqual(g.countdown, 2.)
        advance(g, 2)
        self.assertEqual((g.phase, g.time), ('playing', 0.))
        self.assertFalse(g.shots)

    def test_short_and_long_tracking_loss_freeze_all_simulation(self):
        g, tracker = isolated_game(), InputTracker()
        g.update(.05, tracker.update(observation(0), 0))
        g._bullet(.4, .4, 1., .2)
        snap = g.snapshot()
        for now in (.1, .18, .3, .8, 1.2):
            g.update(.2, tracker.update(None, now))
        after = g.snapshot()
        for key in ('time', 'player', 'lives', 'entities', 'effects', 'score', 'bullets', 'shots'):
            self.assertEqual(after[key], snap[key])
        g.update(1 / 60, tracker.update(observation(1.3), 1.3))
        self.assertAlmostEqual(g.time - snap['time'], 1 / 60)

    def test_pause_discards_time_and_held_special_without_firing_on_resume(self):
        g = isolated_game()
        g.energy = 1.
        g.pause(True)
        advance(g, 3, control(special=True))
        self.assertEqual((g.time, g.energy, g.shots), (0., 1., []))
        g.pause(False)
        g.update(1 / 60, control(special=True))
        self.assertEqual(g.energy, 1.)
        self.assertAlmostEqual(g.time, 1 / 60)
        self.assertEqual(len(g.shots), 1)
        g.update(1 / 60, control(special=False))
        g.update(1 / 60, control(special=True))
        self.assertEqual(g.energy, 0.)

    def test_held_special_during_tracking_loss_is_not_replayed(self):
        g = isolated_game()
        g.energy = 1.
        g.update(.1, control(valid=False, special=True))
        g.update(.1, control(special=True))
        self.assertEqual(g.energy, 1.)
        self.assertEqual(g.special_time, 0.)

    def test_invalid_coordinates_and_nonfinite_dt_do_not_advance(self):
        g = isolated_game()
        for c in (None, {}, control(x=float('nan')), control(y=float('inf')),
                  control(status='holding'), control(status='waiting'), control(status='lost')):
            g.update(.1, c)
        for dt in (0, -1, float('nan'), float('inf'), None):
            g.update(dt, control())
        self.assertEqual((g.time, len(g.shots)), (0., 0))

    def test_large_dt_is_bounded_and_no_future_catchup(self):
        g = isolated_game()
        g.update(50., control())
        self.assertAlmostEqual(g.time, .25)
        g.update(1 / 60, control())
        self.assertAlmostEqual(g.time, .25 + 1 / 60)

    def test_full_width_control_mapping_and_small_hit_point(self):
        g = isolated_game()
        advance(g, 3, control(.93, .22))
        self.assertAlmostEqual(g.player['x'], .93, places=8)
        self.assertAlmostEqual(g.player['y'], .22, places=8)
        advance(g, 3, control(.07, .90))
        self.assertAlmostEqual(g.player['x'], .07, places=8)
        self.assertAlmostEqual(g.player['y'], .84, places=8)
        self.assertLess(g.player['y']*720+26.25, 636.5)
        self.assertLess(g.player['r'], .012)

    def test_seed_and_render_frame_rate_consistency(self):
        results = []
        for fps in (20, 30, 60, 120):
            g = Game()
            g.start(0)
            advance(g, 12, control(.36, .76), fps)
            results.append(g.snapshot())
        self.assertTrue(all(result == results[0] for result in results))

    def test_restart_clears_shots_bullets_drops_score_and_boss(self):
        g = isolated_game()
        g._spawn_boss()
        g._bullet(.2, .2, 1., .2)
        g._effect('collect', .4, .4)
        g.score, g.combo, g.power, g.graze = 30, 2, 4, 10
        g.start(3)
        self.assertEqual((g.score, g.combo, g.energy, g.lives, g.power, g.kills, g.graze),
                         (0, 0, 0., 5, 1, 0, 0))
        self.assertFalse(g.entities or g.effects or g.shots or g.bullets or g._boss_spawned)
        self.assertEqual(g.phase, 'countdown')

    def test_snapshot_json_detached_for_every_collection(self):
        g = isolated_game()
        g._entity('enemy', .3, .2, .032)
        g._bullet(.4, .4, 1., .2)
        g._shot(.5, .7)
        for _ in range(80):
            g._effect('collect', .5, .5)
        snap = g.snapshot()
        json.dumps(snap, allow_nan=False)
        self.assertEqual(len(snap['effects']), MAX_EFFECTS)
        for field in ('entities', 'effects', 'bullets', 'shots'):
            snap[field][0]['x'] = 999
            self.assertLess(getattr(g, field)[0]['x'], 1)
        snap['player']['x'] = 999
        self.assertLess(g.player['x'], 1)
        self.assertFalse(any(key.startswith('_') for e in snap['entities'] for key in e))


if __name__ == '__main__':
    unittest.main()
