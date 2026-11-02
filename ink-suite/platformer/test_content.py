"""Playable content gates: real inputs, bonus exits, and a physical rescue."""
import unittest
from .engine import Game, STEP, TILE, PLAYER_H, GROUND_Y, _overlap
from .levels import LEVELS


def route_input(game, last_jump=False):
    """Simple public-geometry driver, no entity mutations or invulnerability."""
    p = game.player
    right, foot = p['x'] + p['w'], p['y'] + p['h']
    jump = False
    if p['grounded']:
        obstacle = any(t['solid'] and t['y'] < foot-1 and
                       t['y']+t['h'] > p['y']+1 and 0 <= t['x']-right <= 18
                       for t in game.tiles)
        edge = foot >= 159 and not any(t['solid'] and t['y'] == 160 and
                    t['x'] <= right+5 < t['x']+TILE for t in game.tiles)
        enemy = any(e['alive'] and -3 <= e['x']-right <= 32 and
                    abs(e['y']+e['h']-foot) < 20 for e in game.enemies)
        jump = (obstacle or edge or enemy) and not last_jump
    return {'move': 1, 'jump': jump}


def full_replay():
    game = Game(); game.start(0)
    controls, last = [], False
    for _ in range(120*150):
        control = route_input(game, last)
        last = control['jump']
        controls.append(control)
        game.advance(STEP, control, snapshot=False)
        if game.state in ('won', 'over'):
            break
    return game, controls


class ContentTests(unittest.TestCase):
    def test_complete_three_stage_rescue_with_live_enemies_and_replay(self):
        game, controls = full_replay()
        self.assertEqual(game.state, 'won')
        self.assertTrue(game.rescued)
        self.assertEqual(game.stage_clears, 3)
        self.assertGreater(game.stomps, 0)
        self.assertGreater(game.coins, 0)
        self.assertGreater(game.audio_counts['checkpoint'], 0)
        self.assertGreater(game.lives, 0)
        replay = Game(); replay.start(0)
        for control in controls:
            replay.advance(STEP, control, snapshot=False)
        self.assertEqual(replay.report(), game.report())
        self.assertEqual(replay.snapshot(), game.snapshot())

    def test_every_bonus_pipe_can_be_jumped_onto_and_returns_to_main_route(self):
        for level in LEVELS:
            game = Game((level,)); game.start(0)
            for enemy in game.enemies:
                enemy['alive'] = False  # Isolate pipe geometry, combat tested above.
            pipe = game.pipes[0]
            game.player.update(x=pipe['x']-32, y=GROUND_Y-PLAYER_H, grounded=True)
            game.advance(STEP, {'move': 1, 'jump': True})
            for _ in range(140):
                move = 1 if game.player['x']+6 < pipe['x']+13 else 0
                game.advance(STEP, {'move': move})
                if game.can_enter_pipe(require_stopped=True):
                    break
            self.assertTrue(game.can_enter_pipe(require_stopped=True), level.name)
            game.advance(STEP, {'confirm': True})
            for _ in range(60):game.advance(STEP, {})
            self.assertEqual(game.pipes_used, 1)
            self.assertLess(game.player['y']+PLAYER_H, pipe['y'])
            self.assertTrue(game.player['grounded'])
            self.assertFalse(any(_overlap(game.player,t) for t in game.tiles if t['solid']))
            coins = game.coins
            returned, last = False, False
            for _ in range(300):
                control = route_input(game, last); last = control['jump']
                game.advance(STEP, control)
                returned |= game.player['grounded'] and abs(game.player['y']+PLAYER_H-GROUND_Y) < .5
            self.assertGreater(game.coins, coins, level.name)
            self.assertEqual(game.deaths, 0, level.name)
            self.assertTrue(returned, level.name)

    def test_hidden_bricks_grant_five_coins_once_after_real_head_hit(self):
        for level in LEVELS:
            game = Game((level,)); game.start(0)
            for enemy in game.enemies:enemy['alive'] = False
            tile = next(t for t in game.tiles if t['content'] == 'secret')
            game.player.update(x=tile['x']+2, y=GROUND_Y-PLAYER_H, grounded=True)
            before = game.coins
            game.advance(STEP, {'jump': True})
            for _ in range(80):game.advance(STEP, {})
            self.assertEqual(game.coins-before, 5, level.name)
            self.assertEqual(game.secrets_found, 1)
            game.advance(STEP, {'jump':True})
            for _ in range(80):game.advance(STEP,{})
            self.assertEqual(game.coins-before, 5)

    def test_castle_entrance_and_flying_past_princess_do_not_win(self):
        game = Game((LEVELS[2],)); game.start(0)
        for enemy in game.enemies:enemy['alive'] = False
        game.player.update(x=game.level.castle_col*TILE+20, grounded=True)
        game.advance(STEP,{})
        self.assertEqual(game.state, 'playing')
        self.assertTrue(game.checkpoint)
        game.player.update(x=game.level.princess_col*TILE, y=80, grounded=False)
        game.advance(STEP,{})
        self.assertEqual(game.state, 'playing')
        game.player.update(y=GROUND_Y-PLAYER_H, grounded=True, vy=0.)
        game.advance(STEP,{})
        self.assertEqual(game.state, 'won')
        self.assertTrue(game.report()['princess_rescued'])
        self.assertEqual(game.audio_counts['win'], 1)
        game.advance(1., {'confirm':True})
        self.assertEqual(game.audio_counts['win'], 1)


if __name__ == '__main__':
    unittest.main()
