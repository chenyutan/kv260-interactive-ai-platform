"""Actual bullet-hell rules: collision, score, power, boss and full outcomes."""
import math
import random
import unittest

from .engine import (ASPECT, STEP, MAX_BULLETS, MAX_SHOTS, MAX_ENTITIES,
                     MAX_ENEMIES, MAX_EFFECTS, Game)
from .test_engine import advance, control, isolated_game


def visible_target_control(game, index):
    """Synthetic accessible aiming policy; no health, score or world mutations.

    Track nearby visible foes, collect visible power drops, and lead the
    slowly moving boss by the projectile travel time. This is a repeatable
    rules-completion probe, not a claim about a person's camera performance.
    """
    boss = next((e for e in game.entities if e['kind'] == 'boss' and not e['hit']), None)
    targets = [e for e in game.entities if e['kind'] in ('enemy', 'elite')
               and not e['hit'] and .03 < e['y'] < .55]
    drops = [e for e in game.entities if e['kind'] == 'power' and not e['hit'] and e['y'] > .3]
    if boss:
        x = boss['x'] + .19 * .54 * math.cos(boss['age'] * .54) * .34
    elif drops:
        x = max(drops, key=lambda e: e['y'])['x']
    elif targets:
        x = min(targets, key=lambda e: abs(e['x'] - game.player['x']))['x']
    else:
        x = .5
    return control(x, .76, special=game.energy >= 1. and index % 2 == 0)


class DanmakuTests(unittest.TestCase):
    def test_autofire_travels_up_and_power_adds_a_bounded_spread(self):
        g = isolated_game()
        g.update(STEP, control())
        self.assertEqual(len(g.shots), 1)
        self.assertLess(g.shots[0]['vy'], 0)
        g.shots.clear()
        g.power, g._shot_cd = 4, 0.
        g.update(STEP, control())
        self.assertEqual(len(g.shots), 4)
        self.assertEqual(len({s['vx'] for s in g.shots}), 4)
        self.assertTrue(all(s['vy'] < 0 for s in g.shots))

    def test_fast_friendly_projectile_sweeps_enemy_and_awards_kill_once(self):
        g = isolated_game()
        e = g._entity('enemy', .5, .50, .032, health=2.8, vy=0.)
        s = g._shot(.5, .85)
        s['vy'] = -80.
        g.update(STEP, control())
        self.assertTrue(e['hit'])
        self.assertEqual((g.kills, g.score), (1, 100))
        g._damage(e, 100)
        advance(g, .4)
        self.assertEqual((g.kills, g.score), (1, 100))
        self.assertEqual(sum(e['kind'] == 'orb' for e in g.entities), 1)

    def test_fast_enemy_projectile_sweeps_small_player_hit_point(self):
        g = isolated_game()
        b = g._bullet(.5, .45, math.pi / 2, 90.)
        g.update(STEP, control())
        self.assertTrue(b['hit'])
        self.assertEqual(g.lives, 4)
        self.assertEqual(g.graze, 0)

    def test_graze_is_once_per_bullet_and_does_not_hurt(self):
        g = isolated_game()
        b = g._bullet(.5 + .035 / ASPECT, .78, 0., 0.)
        advance(g, .2)
        self.assertEqual((g.lives, g.score, g.graze), (5, 12, 1))
        self.assertAlmostEqual(g.energy, .035)
        self.assertTrue(b['grazed'])
        advance(g, .8)
        self.assertEqual((g.score, g.graze), (12, 1))

    def test_five_lives_hit_invulnerability_and_loss_are_real(self):
        g = isolated_game()
        for hit in range(5):
            for _ in range(3):
                g._bullet(g.player['x'], g.player['y'], 0., 0.)
            advance(g, .05)
            self.assertEqual(g.lives, 4 - hit)
            if hit < 4:
                advance(g, 1.6)
        self.assertEqual(g.phase, 'lost')
        frozen = (g.time, g.score, len(g.shots))
        advance(g, 2)
        self.assertEqual((g.time, g.score, len(g.shots)), frozen)

    def test_fatal_enemy_collision_stops_later_pickups_in_the_same_step(self):
        g = isolated_game()
        g.lives, g.score, g.power, g.energy = 1, 27, 3, .2
        g._shot_cd = 1000.
        g._entity('enemy', .5, .78, .032, vy=0.)
        pickup = g._entity('power', .5, .78, .022, vy=0.)
        g.update(STEP, control())
        self.assertEqual((g.phase, g.lives, g.score, g.power), ('lost', 0, 27, 2))
        self.assertEqual(g.energy, .2)
        self.assertFalse(pickup['hit'])
        self.assertEqual((g.audio_counts['hurt'], g.audio_counts['lose'],
                          g.audio_counts['collect']), (1, 1, 0))
        self.assertEqual([e['kind'] for e in g.effects], ['hurt'])
        frozen = g.snapshot()
        advance(g, 2.)
        self.assertEqual(g.snapshot(), frozen)

    def test_every_third_enemy_and_elite_drop_collectible_power(self):
        g = isolated_game()
        for _ in range(3):
            e = g._entity('enemy', .3, .3, .032)
            g._damage(e, 8.)
        drops = [e for e in g.entities if e['kind'] == 'power']
        self.assertEqual(len(drops), 1)
        elite = g._entity('elite', .6, .3, .052)
        g._damage(elite, 36.)
        self.assertEqual(sum(e['kind'] == 'power' for e in g.entities), 2)
        score = g.score
        for _ in range(5):
            p = g._entity('power', g.player['x'], g.player['y'], .022)
            g._collect(p)
            g._collect(p)
        self.assertEqual(g.power, 4)
        self.assertEqual(g.score - score, 5 * 60)

    def test_physical_power_pickup_and_damage_downgrade(self):
        g = isolated_game()
        g._entity('power', .5, .77, .022)
        advance(g, .1)
        self.assertEqual(g.power, 2)
        self.assertEqual(g.score, 60)
        self.assertFalse(any(e['kind'] == 'power' for e in g.entities))
        g._hurt()
        self.assertEqual(g.power, 1)

    def test_power_pickup_feedback_reports_only_a_real_upgrade_once(self):
        for initial_power, kind, final_power, effect_kind in (
                (1, 'power', 2, 'power'), (3, 'power', 4, 'power'),
                (4, 'power', 4, 'collect'), (2, 'orb', 2, 'collect')):
            with self.subTest(initial_power=initial_power, kind=kind):
                g = isolated_game()
                g.power = initial_power
                pickup = g._entity(kind, .5, .78, .022)
                g._collect(pickup)
                g._collect(pickup)
                self.assertEqual(g.power, final_power)
                self.assertEqual([e['kind'] for e in g.effects], [effect_kind])
                self.assertEqual(g.audio_counts['collect'], 1)
                self.assertEqual(g.score, 60 if kind == 'power' else 25)

    def test_special_requires_full_energy_rising_edge_clears_and_damages(self):
        g = isolated_game()
        g._spawn_boss()
        boss = next(e for e in g.entities if e['kind'] == 'boss')
        g._bullet(.5, .5, 1., .2)
        g.energy = .99
        g.update(STEP, control(special=True))
        self.assertEqual(g.special_time, 0.)
        self.assertTrue(g.bullets)
        g.update(STEP, control(special=False))
        g.energy = 1.
        g.update(STEP, control(special=True))
        self.assertEqual(g.energy, 0.)
        self.assertFalse(g.bullets)
        self.assertAlmostEqual(boss['health'], boss['max_health'] * .78)
        self.assertEqual(g.phase, 'playing')
        self.assertGreater(g.special_time, 1.)
        g.energy = 1.
        hp = boss['health']
        advance(g, .2, control(special=True))
        self.assertEqual((g.energy, boss['health']), (1., hp))

    def test_boss_has_entry_warning_and_distinct_three_phase_patterns(self):
        g = isolated_game()
        g._spawn_boss()
        g._shot_cd = 1000.
        g.player['invulnerable'] = 100.
        boss = next(e for e in g.entities if e['kind'] == 'boss')
        advance(g, 1.0)
        self.assertFalse(g.bullets)
        advance(g, .8)
        self.assertEqual(g.boss_phase, 1)
        self.assertEqual(len(g.bullets), 9)
        self.assertTrue(all(b['vy'] > 0 for b in g.bullets))
        boss['health'] = boss['max_health'] * .60
        g.update(STEP, control())
        self.assertEqual(g.boss_phase, 2)
        self.assertFalse(g.bullets)
        self.assertGreater(boss['warning'], .9)
        advance(g, 1.1)
        self.assertEqual(len(g.bullets), 19)
        self.assertTrue(any(b['vy'] < 0 for b in g.bullets))
        boss['health'] = boss['max_health'] * .30
        g.update(STEP, control())
        self.assertEqual(g.boss_phase, 3)
        advance(g, 1.1)
        self.assertEqual(len(g.bullets), 14)
        self.assertEqual({b['style'] for b in g.bullets}, {'indigo', 'cinnabar'})

    def test_timeout_with_living_boss_is_failure_not_free_victory(self):
        g = isolated_game(duration=2.)
        g._spawn_boss()
        g._shot_cd = 1000.
        advance(g, 3, control(.07))
        self.assertEqual((g.phase, g.time, g.kills), ('lost', 2., 0))
        self.assertGreater(next(e for e in g.entities if e['kind'] == 'boss')['health'], 0)

    def test_real_boss_kill_is_the_only_victory_and_scores_once(self):
        g = isolated_game()
        g._spawn_boss()
        boss = next(e for e in g.entities if e['kind'] == 'boss')
        # Finish phase three after its warning; changing HP from phase one
        # correctly starts a fresh transition shield instead.
        boss.update(health=2.8, warning=0., x=.5, y=.225, _phase=3)
        g.boss_phase = 3
        s = g._shot(.5, .50)
        s['vy'] = -50.
        g.update(STEP, control())
        self.assertEqual((g.phase, g.kills), ('won', 1))
        self.assertGreaterEqual(g.score, 2500)
        score = g.score
        g._damage(boss, 100.)
        advance(g, 2.)
        self.assertEqual(g.score, score)

    def test_all_object_budgets_reject_growth_and_reserve_boss_slot(self):
        g = isolated_game()
        for _ in range(1000):
            g._bullet(.3, .3, 0., 0.)
            g._shot(.3, .6)
            g._entity('enemy', .3, .3, .032)
            g._effect('slash', .3, .3)
        self.assertEqual((len(g.bullets), len(g.shots), len(g.entities), len(g.effects)),
                         (MAX_BULLETS, MAX_SHOTS, MAX_ENEMIES, MAX_EFFECTS))
        for _ in range(100):
            g._entity('orb', .3, .3, .016)
        self.assertEqual(len(g.entities), MAX_ENTITIES)
        g.entities.clear()
        for _ in range(MAX_ENTITIES):
            g._entity('orb', .3, .3, .016)
        g._spawn_boss()
        self.assertEqual(len(g.entities), MAX_ENTITIES)
        self.assertEqual(sum(e['kind'] == 'boss' for e in g.entities), 1)

    def test_full_entity_budget_reuses_a_defeated_elite_slot_for_its_drop(self):
        g = isolated_game()
        elite = g._entity('elite', .5, .3, .052)
        for _ in range(MAX_ENTITIES - 1):
            g._entity('orb', .1, .1, .016)
        g._damage(elite, 36.)
        self.assertEqual(len(g.entities), MAX_ENTITIES)
        self.assertTrue(all(not e['hit'] for e in g.entities))
        self.assertEqual(sum(e['kind'] == 'power' for e in g.entities), 1)
        g._damage(elite, 36.)
        self.assertEqual((g.kills, g.audio_counts['kill']), (1, 1))
        self.assertEqual(sum(e['kind'] == 'power' for e in g.entities), 1)
        self.assertIsNone(g._entity('orb', .1, .1, .016))

    def test_full_budget_drop_reuse_keeps_active_collision_grid_indices(self):
        g = isolated_game()
        enemies = [g._entity('enemy', .2 + i * .11, .45, .032, health=2.8)
                   for i in range(6)]
        for _ in range(MAX_ENTITIES - len(enemies)):
            g._entity('orb', .1, .1, .016)
        for e in enemies:
            shot = g._shot(e['x'], .60)
            shot['vy'] = -25.
        # Six enemies and shots enter the sparse-grid path. Each kill must
        # replace a corpse with its drop without changing that grid's indices.
        g._collisions(STEP, g.player['x'], g.player['y'])
        self.assertTrue(all(e['hit'] for e in enemies))
        self.assertTrue(all(s['hit'] for s in g.shots))
        self.assertEqual((g.kills, g.audio_counts['kill']), (6, 6))
        self.assertEqual(len(g.entities), MAX_ENTITIES)
        self.assertTrue(all(not e['hit'] for e in g.entities))
        self.assertEqual(sum(e['kind'] == 'power' for e in g.entities), 2)

    def test_full_budget_special_keeps_all_tuple_targets_and_drop_budget(self):
        g = isolated_game()
        enemies = [g._entity('enemy', .2, .3, .032) for _ in range(MAX_ENEMIES)]
        for _ in range(MAX_ENTITIES - MAX_ENEMIES):
            g._entity('orb', .1, .1, .016)
        g.energy = 1.
        g._cast()
        self.assertTrue(all(e['hit'] for e in enemies))
        self.assertEqual((g.kills, g.audio_counts['kill']), (MAX_ENEMIES, MAX_ENEMIES))
        self.assertEqual(g.audio_counts['special'], 1)
        self.assertEqual(len(g.entities), MAX_ENTITIES)
        self.assertTrue(all(not e['hit'] for e in g.entities))
        self.assertEqual(sum(e['kind'] == 'power' for e in g.entities), MAX_ENEMIES // 3)

    def test_spatial_grid_matches_independent_exhaustive_sweeps(self):
        rng = random.Random(92371)
        for trial in range(100):
            g = isolated_game()
            g.player['invulnerable'] = 100.
            for i in range(MAX_ENEMIES):
                e = g._entity('enemy', rng.uniform(.02, .98), rng.uniform(.05, .95),
                              rng.uniform(.016, .073), health=1000.)
                e['_old_x'] += rng.uniform(-.14, .14)
                e['_old_y'] += rng.uniform(-.14, .14)
                if i == 0 and trial % 3 == 0:
                    e['_old_x'] = -20.  # Exercise bounded huge-entity fallback.
            for i in range(MAX_SHOTS):
                s = g._shot(rng.uniform(-.05, 1.05), rng.uniform(-.05, 1.05))
                s['vx'], s['vy'] = rng.uniform(-6., 6.), rng.uniform(-6., 6.)
                if i == 0 and trial % 3 == 0:
                    s['vy'] = -300.  # Exercise huge-projectile fallback.
            expected_health = [e['health'] for e in g.entities]
            expected_hits = []
            dt = 1 / 30
            for s in g.shots:
                hit = False
                for index, e in enumerate(g.entities):
                    x0, y0 = (s['x'] - e['_old_x']) * ASPECT, s['y'] - e['_old_y']
                    x1 = (s['x'] + s['vx'] * dt - e['x']) * ASPECT
                    y1 = s['y'] + s['vy'] * dt - e['y']
                    dx, dy = x1 - x0, y1 - y0
                    length = dx * dx + dy * dy
                    t = 0. if length < 1e-18 else max(0., min(1., -(x0 * dx + y0 * dy) / length))
                    distance = (x0 + t * dx) ** 2 + (y0 + t * dy) ** 2
                    if distance <= (s['r'] + e['r']) ** 2:
                        expected_health[index] -= s['damage']
                        hit = True
                        break
                expected_hits.append(hit)
            g._collisions(dt, g.player['x'], g.player['y'])
            self.assertEqual([s['hit'] for s in g.shots], expected_hits, trial)
            self.assertEqual([e['health'] for e in g.entities], expected_health, trial)

    def test_full_default_level_can_be_won_by_aiming_without_cheats(self):
        for fps in (30, 60):
            with self.subTest(fps=fps):
                g = Game(seed=260)
                g.start(0)
                phases, peak_power, elite_seen = set(), 1, False
                for i in range(75 * fps):
                    g.update(1 / fps, visible_target_control(g, i))
                    phases.add(g.boss_phase)
                    peak_power = max(peak_power, g.power)
                    elite_seen |= any(e['kind'] == 'elite' for e in g.entities)
                    self.assertLessEqual(len(g.bullets), MAX_BULLETS)
                    self.assertLessEqual(len(g.shots), MAX_SHOTS)
                    if g.phase in ('won', 'lost'):
                        break
                self.assertEqual(g.phase, 'won')
                self.assertEqual(phases, {0, 1, 2, 3})
                self.assertTrue(elite_seen)
                self.assertEqual(peak_power, 4)
                self.assertGreater(g.time, 58.)
                self.assertLess(g.time, 75.)
                self.assertGreater(g.lives, 0)
                self.assertGreater(g.kills, 30)

    def test_stationary_hand_does_not_win_by_waiting(self):
        g = Game(seed=260)
        g.start(0)
        advance(g, 76, control(.50, .76))
        self.assertEqual(g.phase, 'lost')


if __name__ == '__main__':
    unittest.main()
