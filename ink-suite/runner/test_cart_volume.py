"""Long train visuals match collisions without changing controls or other obstacles."""
import unittest
from .engine import RunnerGame


class CartVolumeTests(unittest.TestCase):
    def game(self):
        g = RunnerGame(17)
        g.start()
        g.drain_events()
        g._next_wave_distance = 1e9
        return g

    def advance(self, g, seconds):
        for _ in range(round(seconds * 120)):
            g.update(1 / 120)

    def test_forward_contact_still_begins_at_original_front_threshold(self):
        g = self.game()
        cart = g.spawn('cart', 1, .75)
        g.update(1 / 120)
        self.assertGreater(cart.z, .6)
        self.assertEqual(g.lives, 3)
        g.update(1 / 120)
        self.assertLessEqual(cart.z, .6)
        self.assertEqual(g.lives, 2)
        hit = next(e for e in g.drain_events() if e['kind'] == 'hit')
        self.assertEqual((hit['id'], hit['item_kind']), (cart.id, 'cart'))

    def test_missed_front_then_lane_entry_hits_body(self):
        g = self.game()
        cart = g.spawn('cart', 0, .5)
        self.advance(g, .15)
        self.assertLess(cart.z, -.8)
        self.assertFalse(cart.resolved)
        self.assertEqual(g.lives, 3)
        g.move(-1)
        self.advance(g, .15)
        self.assertEqual(g.lives, 2)
        self.assertTrue(cart.resolved)
        self.assertTrue(cart.contacted)
        self.assertEqual(g.counts.get('hit'), 1)

    def test_one_contact_per_train_even_after_immunity_expires(self):
        g = self.game()
        cart = g.spawn('cart', 1, .5)
        g.update(1 / 120)
        self.assertEqual(g.lives, 2)
        for _ in range(240):
            # Keep the rear overlapping for a bounded repeat-contact stress.
            cart.z = -1.
            g.update(1 / 120)
        self.assertEqual(g.lives, 2)
        self.assertEqual(g.counts.get('hit'), 1)

    def test_invulnerable_contact_is_consumed_without_damage_spam(self):
        g = self.game()
        g.invulnerable_time = .15
        cart = g.spawn('cart', 1, .4)
        g.update(1 / 120)
        self.assertTrue(cart.resolved)
        self.assertTrue(cart.contacted)
        self.assertTrue(g.snapshot()['entities'][0]['contacted'])
        self.advance(g, .4)
        self.assertEqual(g.lives, 3)
        self.assertEqual(g.counts.get('hit', 0), 0)

    def test_shield_event_identifies_consumed_cart(self):
        g = self.game()
        g.shield_time = 3
        cart = g.spawn('cart', 1, .4)
        g.update(1 / 120)
        event = next(e for e in g.drain_events() if e['kind'] == 'shield_break')
        self.assertEqual((event['id'], event['item_kind']), (cart.id, 'cart'))
        self.assertEqual(g.lives, 3)
        self.assertTrue(cart.resolved)

    def test_miss_resolves_only_when_rear_has_passed(self):
        g = self.game()
        cart = g.spawn('cart', 0, -7.8)
        g.update(1 / 120)
        self.assertFalse(cart.resolved)
        self.advance(g, .05)
        self.assertTrue(cart.resolved)
        self.assertFalse(cart.contacted)
        g.move(-1)
        self.advance(g, .15)
        self.assertEqual(g.lives, 3)

    def test_tail_retained_until_past_original_camera_retention(self):
        g = self.game()
        cart = g.spawn('cart', 0, -9)
        barrier = g.spawn('barrier', 2, -7.1)
        g.update(1 / 120)
        self.assertIn(cart, g.entities)
        self.assertNotIn(barrier, g.entities)
        cart.z = -14.3
        g.update(1 / 120)
        self.assertNotIn(cart, g.entities)

    def test_each_generated_wave_retains_clear_lane_for_full_cart_length(self):
        for seed in range(80):
            g = RunnerGame(seed)
            g.start()
            g._next_wave_distance = 1e9
            g._spawn_wave()
            clear = next(e['clear_lane'] for e in g.drain_events() if e['kind'] == 'wave')
            g.player_lane = clear
            g.player_x = g._lane_start = float(clear)
            for e in g.entities:
                e.z -= 63
            self.advance(g, .9)
            self.assertEqual(g.lives, 3, seed)

    def test_point_obstacle_missed_front_does_not_gain_train_length(self):
        for kind in ('barrier', 'arch'):
            g = self.game()
            item = g.spawn(kind, 0, .5)
            self.advance(g, .15)
            self.assertTrue(item.resolved)
            g.move(-1)
            self.advance(g, .15)
            self.assertEqual(g.lives, 3)


if __name__ == '__main__':
    unittest.main()
