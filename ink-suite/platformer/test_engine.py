"""Scenario tests for platformer physics, outcomes and time boundaries."""
import math
import time
import unittest

from .engine import (Game, STEP, MAX_DT, RUN_SPEED, MAX_PARTICLES,
                     JUMP_SPEED, TILE, AUDIO_EVENTS, PLAYER_H, GROUND_Y)
from .levels import LevelSpec, TileSpec, EnemySpec, PipeSpec, LEVELS

GROUND_PLAYER_Y = GROUND_Y-PLAYER_H


def flat_level(extra=(), gaps=(), enemies=(), pipes=(), coins=(), width=640):
    tiles = tuple(TileSpec(c, 10) for c in range(width//TILE) if c not in gaps)
    return LevelSpec("物理场景", "bamboo", width, tiles+tuple(extra),
                     tuple(coins), tuple(enemies), tuple(pipes),
                     checkpoint_col=width//TILE-8, goal_col=width//TILE-2)


def run(game, seconds, controls=None, rate=120):
    for _ in range(round(seconds*rate)):
        game.advance(1/rate, controls or {})


def playing(level=None):
    game = Game((level or flat_level(),))
    game.start(0)
    game.advance(STEP, {})
    return game


class PlatformPhysicsTests(unittest.TestCase):
    def test_gravity_lands_exactly_and_cannot_sink(self):
        g = playing()
        g.player.update(x=100., y=5., vy=400., grounded=False)
        run(g, 1.)
        self.assertAlmostEqual(g.player["y"]+g.player["h"], 160.)
        self.assertTrue(g.player["grounded"])
        run(g, 2.)
        self.assertAlmostEqual(g.player["y"], GROUND_PLAYER_Y)
        self.assertEqual(g.lives, 3)

    def test_wall_blocks_both_directions_and_sweep_does_not_tunnel(self):
        g = playing(flat_level(extra=(TileSpec(8, 9, "brick"),)))
        g.player.update(x=108., y=GROUND_PLAYER_Y)
        run(g, .5, {"move": 1})
        self.assertAlmostEqual(g.player["x"], 128.-g.player["w"])
        g.player.update(x=150., vx=0.)
        run(g, .5, {"move": -1})
        self.assertAlmostEqual(g.player["x"], 144.)
        # Exercise the collision sweep with travel far larger than one cell.
        g.player.update(x=240.)
        g._move_x(g.player, -200.)
        self.assertAlmostEqual(g.player["x"], 144.)
        g.player.update(x=20.)
        g._move_x(g.player, 220.)
        self.assertAlmostEqual(g.player["x"], 116.)

    def test_head_hit_stops_jump_and_grants_question_coin_once(self):
        g = playing(flat_level(extra=(TileSpec(2, 7, "question", "coin"),)))
        g.advance(STEP, {"jump": True})
        run(g, .35)
        self.assertEqual(g.coins, 1)
        self.assertEqual(g._tile_grid[2, 7]["kind"], "used")
        self.assertGreaterEqual(g.player["y"], 128.)
        run(g, .5)
        g.advance(STEP, {"jump": True})
        run(g, .5)
        self.assertEqual(g.coins, 1)
        self.assertEqual(g.audio_counts["coin"], 1)

    def test_vertical_sweep_catches_floor_and_ceiling_at_high_travel(self):
        g = playing(flat_level(extra=(TileSpec(2, 5, "brick"),)))
        g.player.update(x=32., y=GROUND_PLAYER_Y, vy=-900.)
        g._move_y(g.player, -130., head=True)
        self.assertAlmostEqual(g.player["y"], 96.)
        self.assertEqual(g.player["vy"], 0.)
        g.player.update(x=80., y=0., vy=900.)
        self.assertTrue(g._move_y(g.player, 300.))
        self.assertAlmostEqual(g.player["y"], GROUND_PLAYER_Y)

    def test_sprite_height_and_head_collision_share_feet_with_two_pixel_margin(self):
        g = playing(flat_level(extra=(TileSpec(2, 7, "question", "coin"),)))
        self.assertEqual(g.player["h"], 22.)
        self.assertAlmostEqual(g.player["y"]+g.player["h"], GROUND_Y)
        g.advance(STEP, {"jump": True})
        highest = g.player["y"]
        for _ in range(20):
            g.advance(STEP, {})
            highest = min(highest, g.player["y"])
        # The rendered 24px hero shares these feet. At a ceiling stop its
        # visible top extends only two pixels above the collision face.
        self.assertAlmostEqual(highest, 128.)
        visible_top = highest+g.player["h"]-24
        self.assertAlmostEqual(128.-visible_top, 2.)

    def test_ground_enemy_turns_before_a_low_ceiling_traps_player(self):
        platform = tuple(TileSpec(col, 8, "brick") for col in range(12, 15))
        g = playing(flat_level(extra=platform, enemies=(EnemySpec(18),)))
        g.player.update(x=20.)
        run(g, 3.)
        self.assertGreaterEqual(g.enemies[0]["x"], 254.)

    def test_power_pickup_then_brick_break_and_single_protected_hit(self):
        g = playing(flat_level(extra=(TileSpec(2, 7, "question", "power"),
                                      TileSpec(6, 7, "brick"))))
        g.advance(STEP, {"jump": True})
        run(g, .15)
        self.assertEqual(len(g.powerups), 1)
        power = g.powerups[0]
        power.update(x=g.player["x"], y=g.player["y"], vx=0., vy=0.)
        g.advance(STEP, {})
        self.assertTrue(g.player["powered"])
        self.assertEqual(g.audio_counts["power"], 1)
        g.player.update(x=96., y=GROUND_PLAYER_Y, grounded=True, vy=0.)
        g.advance(STEP, {"jump": True})
        run(g, .2)
        self.assertFalse(g._tile_grid[6, 7]["solid"])
        self.assertEqual(g.audio_counts["break"], 1)
        g._hurt()
        self.assertFalse(g.player["powered"])
        self.assertEqual(g.lives, 3)
        self.assertGreater(g.player["invincible"], 1)

    def test_coin_collect_and_extra_life_are_one_shot(self):
        g = playing(flat_level(coins=((2, 9),)))
        self.assertEqual(g.coins, 1)
        run(g, .2)
        self.assertEqual(g.coins, 1)
        g.coins = 29
        g._collect_coin(30, 140)
        self.assertEqual(g.lives, 4)

    def test_descending_stomp_bounces_but_side_contact_costs_life(self):
        level = flat_level(enemies=(EnemySpec(10),))
        g = playing(level)
        enemy = g.enemies[0]
        g.player.update(x=enemy["x"], y=enemy["y"]-PLAYER_H-1., vy=250., grounded=False)
        g.advance(STEP, {})
        self.assertFalse(enemy["alive"])
        self.assertLess(g.player["vy"], 0.)
        self.assertEqual(g.stomps, 1)
        self.assertEqual(g.lives, 3)
        self.assertEqual(g.audio_counts["stomp"], 1)
        g = playing(level)
        g.player.update(x=g.enemies[0]["x"]-6, y=GROUND_PLAYER_Y, vy=0., grounded=True)
        g.advance(STEP, {})
        self.assertEqual(g.lives, 2)
        self.assertEqual(g.state, "transition")
        self.assertEqual(g.audio_counts["die"], 1)

    def test_coyote_jump_after_leaving_edge(self):
        g = playing(flat_level(gaps=tuple(range(5, 11))))
        g.player.update(x=77., y=GROUND_PLAYER_Y, vx=RUN_SPEED, grounded=True)
        run(g, .034, {"move": 1})
        self.assertFalse(g.player["grounded"])
        g.advance(STEP, {"move": 1, "jump": True})
        self.assertLess(g.player["vy"], -270.)
        self.assertEqual(g.audio_counts["jump"], 1)

    def test_late_gesture_can_jump_near_edge_but_not_long_after_falling(self):
        for delay,expected in ((.10,1),(.18,0)):
            with self.subTest(delay=delay):
                g=playing(flat_level(gaps=tuple(range(5,11))))
                g.player.update(x=77.,y=GROUND_PLAYER_Y,vx=RUN_SPEED,grounded=True)
                run(g,.034,{'move':1})
                self.assertFalse(g.player['grounded'])
                run(g,delay,{'move':1})
                g.advance(STEP,{'move':1,'jump':True})
                self.assertEqual(g.audio_counts['jump'],expected)

    def test_gesture_buffer_survives_short_landing_delay_and_expires(self):
        for height,expected in ((10.,1),(24.,0)):
            with self.subTest(height=height):
                g=playing()
                g.player.update(y=GROUND_PLAYER_Y-height,vy=0.,grounded=False)
                g._coyote=0.
                g.advance(STEP,{'jump':True})
                run(g,.3,{'jump':False})
                self.assertEqual(g.audio_counts['jump'],expected)

    def test_buffered_jump_just_before_landing_and_hold_never_repeats(self):
        g = playing()
        g.player.update(y=GROUND_PLAYER_Y-4, vy=120., grounded=False)
        g.advance(STEP, {"jump": True})
        run(g, .06, {"jump": True})
        self.assertLess(g.player["vy"], 0.)
        self.assertEqual(g.audio_counts["jump"], 1)
        run(g, 1.2, {"jump": True})
        self.assertTrue(g.player["grounded"])
        self.assertEqual(g.audio_counts["jump"], 1)
        g.advance(STEP, {"jump": False})
        g.advance(STEP, {"jump": True})
        self.assertEqual(g.audio_counts["jump"], 2)

    def test_pipe_requires_confirm_edge_and_proper_top_position(self):
        extra = tuple(TileSpec(c, r, "pipe") for c in (8, 9, 16, 17) for r in (8, 9))
        g = playing(flat_level(extra=extra, pipes=(PipeSpec(8, 16),)))
        g.player.update(x=138., y=128.-PLAYER_H, grounded=True)
        run(g, .25)
        self.assertEqual(g.state, "playing")
        self.assertEqual(g.pipes_used, 0)
        g.advance(STEP, {"confirm": True})
        self.assertEqual(g.state, "transition")
        self.assertEqual(g.transition_kind, "pipe")
        run(g, .5, {"confirm": True})
        self.assertAlmostEqual(g.player["x"], 266.)
        self.assertAlmostEqual(g.player["y"]+g.player["h"], 128.)
        self.assertEqual(g.state, "playing")
        self.assertEqual(g.pipes_used, 1)
        self.assertEqual(g.audio_counts["pipe"], 1)
        # Pressing beside a pipe, including while touching its side, does not enter.
        g.advance(STEP, {"confirm": False})
        g.player.update(x=120., y=GROUND_PLAYER_Y, grounded=True)
        g.advance(STEP, {"confirm": True})
        self.assertEqual(g.state, "playing")
        self.assertEqual(g.pipes_used, 1)

    def test_pipe_input_context_uses_exact_collision_position_and_stop(self):
        extra=tuple(TileSpec(c,r,'pipe') for c in (8,9,16,17) for r in (8,9))
        g=playing(flat_level(extra=extra,pipes=(PipeSpec(8,16),)))
        g.player.update(x=138.,y=128.-PLAYER_H,grounded=True,vx=0.)
        self.assertTrue(g.can_enter_pipe(require_stopped=True))
        for field,value in (('grounded',False),('vx',9.),('x',120.),('y',129.-PLAYER_H)):
            with self.subTest(field=field):
                old=g.player[field];g.player[field]=value
                self.assertFalse(g.can_enter_pipe(require_stopped=True))
                g.player[field]=old
        g.state='paused'
        self.assertFalse(g.can_enter_pipe(require_stopped=True))

    def test_checkpoint_fall_respawns_without_regranting_coins(self):
        g = playing(flat_level(coins=((2, 9),)))
        g.player.update(x=g.level.checkpoint_col*TILE, y=GROUND_PLAYER_Y, grounded=True)
        g.advance(STEP, {})
        self.assertTrue(g.checkpoint)
        g.player.update(y=250., vy=300., grounded=False)
        g.advance(STEP, {})
        run(g, 1.6)
        self.assertEqual(g.state, "playing")
        self.assertEqual(g.lives, 2)
        self.assertAlmostEqual(g.player["x"], g.level.checkpoint_col*TILE)
        self.assertEqual(g.coins, 1)
        self.assertEqual(g.audio_counts["checkpoint"], 1)

    def test_three_stage_completion_report_and_no_terminal_timewarp(self):
        g = Game()
        g.start(0)
        for index in range(3):
            g.player.update(x=g.level.goal_col*TILE-10., y=GROUND_PLAYER_Y, vy=0., grounded=True)
            g.advance(STEP, {})
            if index < 2:
                self.assertEqual(g.state, "transition")
                run(g, 1.7)
                self.assertEqual(g.level_index, index+1)
                self.assertEqual(g.state, "playing")
        self.assertEqual(g.state, "won")
        self.assertEqual(g.stage_clears, 3)
        self.assertEqual(g.audio_counts["clear"], 2)
        self.assertEqual(g.audio_counts["win"], 1)
        result = g.report(True)
        self.assertTrue(result["completed"])
        self.assertEqual(result["completed_stages"], 3)
        before = g.snapshot()
        g.advance(60., {"move": 1, "jump": True})
        self.assertEqual(g.snapshot(), before)

    def test_death_game_over_restart_resets_score_and_audio_round(self):
        g = playing()
        first_round = g.round_id
        for index in range(3):
            g.player.update(y=250., grounded=False)
            g.advance(STEP, {})
            if index < 2:
                run(g, 1.6)
        self.assertEqual(g.state, "over")
        self.assertEqual(g.lives, 0)
        self.assertEqual(g.audio_counts["die"], 3)
        self.assertFalse(g.report(True)["completed"])
        g.restart(0)
        self.assertGreater(g.round_id, first_round)
        self.assertEqual(g.state, "playing")
        self.assertEqual(g.score, 0)
        self.assertEqual(g.lives, 3)
        self.assertFalse(any(g.audio_counts.values()))

    def test_pause_discards_debt_and_requires_release_after_resume(self):
        g = playing()
        g.advance(STEP/2, {})
        g.pause()
        frozen = g.snapshot()
        for _ in range(3):
            g.advance(1000., {"move": 1, "jump": True, "confirm": True})
        self.assertEqual(g.snapshot(), frozen)
        g.resume()
        before_x = g.player["x"]
        g.advance(STEP, {"jump": True})
        self.assertAlmostEqual(g.player["x"], before_x)
        self.assertEqual(g.audio_counts["jump"], 0)
        g.advance(STEP, {})
        g.advance(STEP, {"jump": True})
        self.assertEqual(g.audio_counts["jump"], 1)

    def test_fixed_step_parity_30_60_120_hz(self):
        snapshots = []
        for rate in (30, 60, 120):
            g = playing()
            run(g, .5, {"move": 1}, rate)
            run(g, .3, {"move": 1, "jump": True}, rate)
            run(g, .8, {"move": 1}, rate)
            snapshots.append(g.snapshot())
        for snap in snapshots[1:]:
            self.assertEqual(snap, snapshots[0])

    def test_invalid_time_no_effect_and_catchup_is_bounded(self):
        g = playing()
        before = g.snapshot()
        for dt in (math.nan, math.inf, -1., None, True, "1"):
            self.assertEqual(g.advance(dt, {"jump": True}), before)
        t0, x0 = g.elapsed, g.player["x"]
        g.advance(3600., {"move": 1})
        self.assertLessEqual(g.elapsed-t0, MAX_DT+1e-8)
        self.assertLessEqual(g.player["x"]-x0, RUN_SPEED*MAX_DT)

    def test_snapshot_and_audio_observers_cannot_mutate_engine(self):
        g = playing()
        with self.assertRaises(TypeError):
            g.audio_counts["jump"] = 100
        snap = g.snapshot()
        snap["player"]["x"] = -100
        snap["tiles"][0]["kind"] = "broken"
        snap["audio_counts"]["jump"] = 100
        self.assertGreaterEqual(g.player["x"], 0)
        self.assertEqual(g.tiles[0]["kind"], "ground")
        self.assertEqual(g.audio_counts["jump"], 0)

    def test_snapshot_free_advance_has_identical_lifecycle_and_edge_inputs(self):
        normal, fast = playing(), playing()
        for dt, controls in ((STEP/2, {"move": 1, "jump": True}),
                             (STEP/2, {"move": 1, "jump": True}),
                             (.1, {"move": 1}), (.1, {"move": -1}),
                             (math.nan, {"jump": True}), (3600., {})):
            returned = normal.advance(dt, controls)
            self.assertIsNone(fast.advance(dt, controls, snapshot=False))
            self.assertEqual(returned, fast.snapshot())
        for operation in ("pause", "resume", "restart"):
            self.assertEqual(getattr(normal, operation)(), getattr(fast, operation)())
            for controls in ({"jump": True, "confirm": True}, {}, {"jump": True}):
                returned = normal.advance(STEP, controls)
                self.assertIsNone(fast.advance(STEP, controls, snapshot=False))
                self.assertEqual(returned, fast.snapshot())
        for state in ("won", "over"):
            normal.state = fast.state = state
            returned = normal.advance(.1, {"move": 1})
            self.assertIsNone(fast.advance(.1, {"move": 1}, snapshot=False))
            self.assertEqual(returned, fast.snapshot())

    def test_bump_animation_honors_public_tile_updates_and_repeated_head_hits(self):
        g = playing(flat_level(extra=(TileSpec(2, 7, "brick"),)))
        tile = g._tile_grid[2, 7]
        # Render observers can write the public dictionary: an optimization
        # must not require a private registration to notice its animation.
        tile["bump"] = STEP*2
        g._animate()
        self.assertEqual(tile["bump"], STEP)
        g._animate()
        self.assertEqual(tile["bump"], 0.)
        g._head_hit(tile)
        g._animate()
        self.assertEqual(tile["bump"], .16-STEP)
        g._head_hit(tile)
        for _ in range(20):
            g._animate()
        self.assertEqual(tile["bump"], 0.)
        self.assertEqual(g.audio_counts["bump"], 2)

    def test_real_maps_have_progressive_bounded_gaps_and_solid_checkpoints(self):
        self.assertEqual([l.theme for l in LEVELS], ["bamboo", "temple", "moon"])
        self.assertEqual(len({l.name for l in LEVELS}), 3)
        self.assertLess(LEVELS[0].width, LEVELS[1].width)
        self.assertLess(LEVELS[1].width, LEVELS[2].width)
        for level in LEVELS:
            cells = {(t.col, t.row) for t in level.tiles}
            self.assertIn((level.checkpoint_col, 10), cells)
            self.assertIn((level.goal_col, 10), cells)
            widest = streak = 0
            for col in range(level.width//TILE):
                streak = streak+1 if (col, 10) not in cells else 0
                widest = max(widest, streak)
            # Flight range is >80 px with the public fixed arc; widest gap64.
            self.assertLessEqual(widest, 4)

    def test_all_handcrafted_ground_routes_can_be_crossed_with_actual_jumps(self):
        # Geometry gate: keep enemies out of this scenario so it checks route
        # clearance and mandatory gaps independently from combat timing.
        for level in LEVELS:
            g = playing(level)
            for enemy in g.enemies:
                enemy["alive"] = False
            last_jump = False
            for _ in range(120*30):
                p = g.player
                right, foot = p["x"]+p["w"], p["y"]+p["h"]
                jump = False
                if p["grounded"]:
                    obstacle = any(t["solid"] and t["y"] < foot-1 and
                                   t["y"]+t["h"] > p["y"]+1 and
                                   0 <= t["x"]-right <= 18 for t in g.tiles)
                    edge = foot >= 159 and not any(
                        t["solid"] and t["y"] == 160 and
                        t["x"] <= right+5 < t["x"]+TILE for t in g.tiles)
                    jump = (obstacle or edge) and not last_jump
                last_jump = jump
                g.advance(STEP, {"move": 1, "jump": jump})
                if g.state == "playing":
                    p = g.player
                    for tile in g.tiles:
                        if not tile["solid"]:
                            continue
                        overlap = (p["x"] < tile["x"]+TILE-1e-7 and
                                   p["x"]+p["w"] > tile["x"]+1e-7 and
                                   p["y"] < tile["y"]+TILE-1e-7 and
                                   p["y"]+p["h"] > tile["y"]+1e-7)
                        self.assertFalse(overlap, (level.name, p, tile))
                if g.state in ("won", "over"):
                    break
            self.assertEqual(g.state, "won", level.name)
            self.assertEqual(g.deaths, 0, level.name)

    def test_simulation_entity_limits_and_smoke_performance(self):
        g = playing()
        started = time.perf_counter()
        for _ in range(1800):
            g.advance(STEP, {})
        duration = time.perf_counter()-started
        self.assertLess(duration, 5., "15 seconds simulation should run much faster than real time")
        self.assertLessEqual(len(g.particles), MAX_PARTICLES)
        self.assertLessEqual(len(g.enemies), 6)
        self.assertEqual(set(g.audio_counts), set(AUDIO_EVENTS))
        self.assertTrue(all(_is_finite_rect(e) for e in [g.player]+g.enemies))


def _is_finite_rect(entity):
    return all(math.isfinite(entity[k]) for k in ("x", "y", "w", "h"))


if __name__ == "__main__":
    unittest.main()
