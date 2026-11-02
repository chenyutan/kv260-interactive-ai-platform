import unittest

from engine import Fight
from input_gate import InputGate


class InputGateTests(unittest.TestCase):
    def setUp(self):
        self.game = Fight()
        self.game.start()
        self.game.advance(3.)
        self.game.next_attack = 100.
        self.gate = InputGate()

    def tail(self):
        self.assertEqual(self.gate.submit(self.game, 'left', 10.), 'accepted')
        self.game.advance(.21)

    def test_ready_attack_is_immediate(self):
        self.assertEqual(self.gate.submit(self.game, 'palm', 10.), 'accepted')
        self.assertEqual(self.game.last_move, 'palm')
        self.assertIsNone(self.gate.pending)

    def test_early_recovery_is_not_queued(self):
        self.gate.submit(self.game, 'palm', 10.)
        self.game.advance(.1)
        self.assertEqual(self.gate.submit(self.game, 'left', 10.1), 'busy')
        self.assertIsNone(self.gate.pending)
        self.game.advance(.7)
        self.assertEqual(self.gate.flush(self.game, 10.8), 'idle')
        self.assertEqual(self.game.move_counts, {'palm': 1})

    def test_rapid_alternating_punch_waits_for_animation_once(self):
        for first,second in (('left','right'),('right','left')):
            with self.subTest(first=first):
                self.setUp()
                self.assertEqual(self.gate.submit(self.game,first,10.),'accepted')
                self.game.advance(.02)
                self.assertEqual(self.gate.submit(self.game,second,10.02),'queued')
                self.game.advance(.25)
                self.assertEqual(self.gate.flush(self.game,10.27),'queued')
                self.assertEqual(self.game.attack['move'],first)
                self.game.advance(.07)
                self.assertEqual(self.gate.flush(self.game,10.34),'accepted')
                self.assertEqual(self.game.move_counts,{first:1,second:1})
                self.assertEqual(self.gate.flush(self.game,10.35),'idle')

    def test_early_alternating_buffer_does_not_enable_other_moves(self):
        for move in ('left','uppercut_left','palm','special'):
            with self.subTest(move=move):
                self.setUp();self.game.energy=100
                self.gate.submit(self.game,'left',10.)
                self.game.advance(.02)
                self.assertEqual(self.gate.submit(self.game,move,10.02),'busy')
                self.assertIsNone(self.gate.pending)

    def test_early_alternating_slot_cannot_be_overwritten_or_backlogged(self):
        self.gate.submit(self.game,'left',10.)
        self.assertEqual(self.gate.submit(self.game,'right',10.001),'queued')
        for move in ('right','left','palm'):
            self.assertEqual(self.gate.submit(self.game,move,10.1),'busy')
        self.game.advance(.34)
        self.assertEqual(self.gate.flush(self.game,10.34),'accepted')
        self.game.advance(.34)
        self.assertEqual(self.gate.flush(self.game,10.68),'idle')
        self.assertEqual(self.game.move_counts,{'left':1,'right':1})

    def test_early_alternating_buffer_expires_at_bounded_deadline(self):
        self.gate.submit(self.game,'left',10.)
        self.gate.submit(self.game,'right',10.)
        self.assertEqual(self.gate.pending['max_age'],.38)
        self.game.advance(.38)
        self.assertEqual(self.gate.flush(self.game,10.38),'expired')
        self.assertEqual(self.game.move_counts,{'left':1})

    def test_early_alternating_buffer_clears_on_pause_and_tracking_loss(self):
        for pause in (False,True):
            self.setUp();self.gate.submit(self.game,'left',10.)
            self.gate.submit(self.game,'right',10.02)
            if pause:
                self.game.state='paused'
                self.assertEqual(self.gate.flush(self.game,10.03),'inactive')
            else:self.gate.clear()
            self.game.state='playing';self.game.advance(.4)
            self.assertEqual(self.gate.flush(self.game,10.4),'idle')
            self.assertEqual(self.game.move_counts,{'left':1})

    def test_alternating_buffer_cannot_bypass_extended_cooldown(self):
        self.gate.submit(self.game,'left',10.)
        self.game.cooldown=self.game.time+.8
        self.assertEqual(self.gate.submit(self.game,'right',10.02),'busy')
        self.assertIsNone(self.gate.pending)

    def test_tail_recovery_input_fires_once(self):
        self.tail()
        self.assertEqual(self.gate.submit(self.game, 'right', 10.21), 'queued')
        self.assertEqual(self.game.last_move, 'left')
        self.assertEqual(self.gate.flush(self.game, 10.22), 'queued')
        self.game.advance(.13)
        self.assertEqual(self.gate.flush(self.game, 10.34), 'accepted')
        self.assertEqual(self.gate.flush(self.game, 10.35), 'idle')
        self.assertEqual(self.game.move_counts, {'left': 1, 'right': 1})

    def test_single_slot_does_not_overwrite_or_chain(self):
        self.tail()
        self.assertEqual(self.gate.submit(self.game, 'right', 10.21), 'queued')
        self.assertEqual(self.gate.submit(self.game, 'palm', 10.22), 'busy')
        self.game.advance(.13)
        self.assertEqual(self.gate.flush(self.game, 10.34), 'accepted')
        self.game.advance(.7)
        self.assertEqual(self.gate.flush(self.game, 11.04), 'idle')
        self.assertEqual(self.game.move_counts, {'left': 1, 'right': 1})

    def test_cooldown_past_animation_is_respected(self):
        self.gate.submit(self.game, 'palm', 10.)
        self.game.advance(.62)
        self.assertIsNone(self.game.attack)
        self.assertEqual(self.gate.submit(self.game, 'left', 10.62), 'queued')
        self.game.advance(.04)
        self.assertEqual(self.gate.flush(self.game, 10.66), 'queued')
        self.game.advance(.02)
        self.assertEqual(self.gate.flush(self.game, 10.68), 'accepted')

    def test_stalled_renderer_expires_old_input(self):
        self.tail()
        self.gate.submit(self.game, 'right', 10.21)
        self.game.advance(.2)
        self.assertEqual(self.gate.flush(self.game, 10.40), 'expired')
        self.assertEqual(self.game.move_counts, {'left': 1})

    def test_exact_expiration_boundary(self):
        self.tail()
        self.gate.submit(self.game, 'right', 10.21)
        self.game.advance(.18)
        self.assertEqual(self.gate.flush(self.game, 10.39), 'expired')

    def test_pause_or_round_end_drops_pending(self):
        for state in ('paused', 'over', 'menu', 'countdown'):
            with self.subTest(state=state):
                self.setUp()
                self.tail()
                self.gate.submit(self.game, 'right', 10.21)
                self.game.state = state
                self.assertEqual(self.gate.flush(self.game, 10.22), 'inactive')
                self.assertIsNone(self.gate.pending)
                self.assertEqual(self.gate.submit(self.game, 'left', 10.23), 'inactive')

    def test_clear_on_focus_or_tracking_loss(self):
        self.tail()
        self.gate.submit(self.game, 'right', 10.21)
        self.gate.clear()
        self.game.advance(.13)
        self.assertEqual(self.gate.flush(self.game, 10.34), 'idle')
        self.assertEqual(self.game.move_counts, {'left': 1})

    def test_special_without_energy_never_downgrades(self):
        self.assertEqual(self.gate.submit(self.game, 'special', 10.), 'no_energy')
        self.assertIsNone(self.game.attack)
        self.tail()
        self.assertEqual(self.gate.submit(self.game, 'special', 10.21), 'no_energy')
        self.assertIsNone(self.gate.pending)

    def test_special_rechecks_energy_when_flushed(self):
        self.tail()
        self.game.energy = 100
        self.assertEqual(self.gate.submit(self.game, 'special', 10.21), 'queued')
        self.game.energy = 99
        self.game.advance(.13)
        self.assertEqual(self.gate.flush(self.game, 10.34), 'no_energy')
        self.assertIsNone(self.game.attack)

    def test_buffered_special_consumes_energy_once(self):
        self.tail()
        self.game.energy = 100
        self.gate.submit(self.game, 'special', 10.21)
        self.game.advance(.13)
        self.assertEqual(self.gate.flush(self.game, 10.34), 'accepted')
        self.assertEqual(self.game.last_move, 'special')
        self.assertEqual(self.game.energy, 0)
        self.assertEqual(self.gate.flush(self.game, 10.35), 'idle')

    def test_game_reset_or_new_game_cannot_receive_old_input(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.setUp()
                self.tail()
                self.gate.submit(self.game, 'right', 10.21)
                if replacement:
                    self.game = Fight()
                self.game.start()
                self.game.advance(3.)
                self.assertEqual(self.gate.flush(self.game, 10.22), 'expired')
                self.assertEqual(self.game.move_counts, {})

    def test_invalid_clock_and_backwards_clock_fail_closed(self):
        for now in (None, float('nan'), float('inf'), 10.20):
            with self.subTest(now=now):
                self.setUp()
                self.tail()
                self.gate.submit(self.game, 'right', 10.21)
                result = self.gate.flush(self.game, now)
                self.assertIn(result, ('invalid', 'expired'))
                self.assertIsNone(self.gate.pending)

    def test_bad_move_does_not_attack(self):
        for move in ('invalid', None, []):
            self.assertEqual(self.gate.submit(self.game, move, 10.), 'invalid')
        self.assertEqual(self.game.move_counts, {})


if __name__ == '__main__':
    unittest.main()
