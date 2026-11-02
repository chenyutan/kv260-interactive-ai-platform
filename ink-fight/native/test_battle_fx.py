"""Visual bounds and lifespan regressions; no GTK or Cairo required."""
import copy
import math
import unittest
from types import SimpleNamespace
import battle_fx


def game(**kw):
    return SimpleNamespace(**dict(dict(guard=False, attack=None,
                                      enemy_attack=None, effects=[]), **kw))


class Context:
    """Recording context verifies finite Cairo arguments and balanced saves."""
    def __init__(self):
        self.saved = 0
        self.calls = []
    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            for arg in args:
                if isinstance(arg, (int, float)):
                    assert math.isfinite(arg), (name, args)
            if name == 'save':self.saved += 1
            if name == 'restore':self.saved -= 1
            assert self.saved >= 0
        return call


class BattleEffectsTest(unittest.TestCase):
    def test_expired_events_and_attacks_do_not_draw(self):
        g = game(attack=dict(age=.4, duration=.4), effects=[
            dict(age=.55, lifetime=4), dict(age=.11, lifetime=.1), dict(age=-1)])
        self.assertEqual(battle_fx.bounds(g), [])
        c = Context();battle_fx.paint(c, g)
        self.assertEqual(c.calls, [])

    def test_latest_six_effects_are_bounded_below_hud(self):
        g = game(effects=[dict(age=.1, side='enemy', kind='hit') for _ in range(20)])
        boxes = battle_fx.bounds(g)
        self.assertEqual(len(boxes), 6)
        for x, y, w, h in boxes:
            self.assertGreaterEqual(y, 200)
            self.assertLessEqual(y + h, 600)
            self.assertLessEqual(w * h, 55000)

    def test_all_moves_are_distinct_and_do_not_mutate_game(self):
        strokes = []
        for move in ('left', 'uppercut_left', 'palm', 'special', 'enemy_sweep'):
            g = game(attack=dict(move=move, age=.2, windup=.14, duration=.5))
            old = copy.deepcopy(g.__dict__)
            c = Context();battle_fx.paint(c, g, 485, 745, 548)
            self.assertEqual(g.__dict__, old)
            self.assertEqual(c.saved, 0)
            self.assertTrue(any(name == 'clip' for name, _ in c.calls))
            strokes.append(repr(c.calls))
        self.assertEqual(len(set(strokes)), 5)

    def test_old_events_and_nonfinite_values_are_safe(self):
        g = game(guard=True, attack=dict(age=.2), enemy_attack=dict(age=.1), effects=[
            dict(side='player', kind='block', age=.1, perfect=True),
            dict(side='enemy', kind='hit', age=.2, damage=float('nan')),
            dict(kind='hit', age=float('inf')), None])
        c = Context();battle_fx.paint(c, g)
        self.assertEqual(c.saved, 0)

    def test_enemy_startup_is_distinct_from_active_strike(self):
        g = game(enemy_attack=dict(age=.1, windup=.8, duration=1.1, move='enemy_heavy'))
        first = battle_fx.bounds(g)
        g.enemy_attack['age'] = .85
        second = battle_fx.bounds(g)
        self.assertNotEqual(first, second)
        self.assertLess(first[0][2], second[0][2])


if __name__ == '__main__':
    unittest.main()
