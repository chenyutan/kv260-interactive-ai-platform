"""Exercise real Window handlers without creating GTK windows or a camera.

On the host these tests skip only if GTK/Cairo bindings are unavailable. On
KV260 they import main_v2 and bind its methods to a plain Python namespace;
no Gtk.main, Scene, Reader, display connection, or model is instantiated.
"""
from collections import deque
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from engine import Fight
from input_gate import InputGate

try:
    import gi
    import cairo
except ModuleNotFoundError as error:
    if error.name not in ('gi', 'cairo'):
        raise
    UI_UNAVAILABLE = str(error)
    main_v2 = None
else:
    try:
        gi.require_version('Gtk', '3.0')
    except ValueError as error:
        UI_UNAVAILABLE = str(error)
        main_v2 = None
    else:
        # Application import failures must fail tests rather than be hidden.
        import main_v2
        UI_UNAVAILABLE = ''


class Clock:
    def __init__(self):
        self.now = 10.

    def __call__(self):
        now = self.now
        self.now += .001
        return now


class PoseStub:
    """Inject recognized commands, not images, into the real tick handler."""
    def __init__(self):
        self.valid = True
        self.both_valid = True
        self.guard = False
        self.hint = ''
        self.moves = []
        self.resets = 0

    def consume(self, value):
        self.valid = value is not None
        moves, self.moves = self.moves, []
        return moves if self.valid else []

    def reset(self):
        self.moves = []
        self.valid = False
        self.guard = False
        self.resets += 1


@unittest.skipIf(main_v2 is None, UI_UNAVAILABLE)
class WindowInputTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.timer = patch.object(main_v2.time, 'monotonic', self.clock)
        self.timer.start()
        self.addCleanup(self.timer.stop)
        game = Fight()
        game.start()
        game.advance(3.)
        game.next_attack = 100.
        area = SimpleNamespace(queue_draw=Mock(), queue_draw_area=Mock(),
                               get_allocated_width=lambda: 1024,
                               get_allocated_height=lambda: 560)
        self.pose = {}
        window = SimpleNamespace(
            args=SimpleNamespace(benchmark=0), game=game, gesture=PoseStub(),
            reader=SimpleNamespace(get=lambda: self.pose), area=area,
            last=10., manual_guard=False, keys=set(), lost_since=None,
            tracking_pause=False, good_since=None, last_status='',
            help_open=False, moves_received={}, moves_accepted={},
            input_gate=InputGate(), input_feedback='', feedback_until=0.,
            input_events=deque(maxlen=100))
        for name in ('start', 'pause', 'help', 'focus_out', 'perform',
                     'feedback', 'tick', 'cancel_input'):
            setattr(window, name, MethodType(getattr(main_v2.Window, name), window))
        self.window = window

    def recovery_tail(self):
        self.window.perform('left')
        self.window.game.advance(.21)
        self.window.last = 10.21
        self.clock.now = 10.21

    def queue_from_tick(self):
        self.recovery_tail()
        self.window.gesture.moves = ['right']
        self.window.tick()
        self.assertIsNotNone(self.window.input_gate.pending)
        self.assertEqual(self.window.input_gate.pending['move'], 'right')

    def tick_at(self, now):
        self.clock.now = now
        self.window.tick()

    def test_new_queue_survives_same_tick_and_executes_once(self):
        self.queue_from_tick()
        self.assertEqual(self.window.moves_received, {'left': 1, 'right': 1})
        self.assertEqual(self.window.moves_accepted, {'left': 1})
        self.assertEqual(self.window.input_events[-1]['result'], 'queued')
        self.assertEqual(self.window.game.last_move, 'left')
        self.tick_at(10.35)
        self.assertIsNone(self.window.input_gate.pending)
        self.assertEqual(self.window.game.last_move, 'right')
        self.assertEqual(self.window.moves_accepted, {'left': 1, 'right': 1})
        self.tick_at(10.39)
        self.assertEqual(self.window.game.move_counts, {'left': 1, 'right': 1})
        self.assertEqual(self.window.moves_received, {'left': 1, 'right': 1})
        self.assertEqual([e['result'] for e in self.window.input_events],
                         ['accepted', 'queued', 'accepted'])

    def test_pause_drops_pending_and_resume_does_not_replay(self):
        self.queue_from_tick()
        self.window.pause()
        self.assertEqual(self.window.game.state, 'paused')
        self.assertIsNone(self.window.input_gate.pending)
        self.window.pause()
        self.tick_at(10.35)
        self.assertEqual(self.window.game.move_counts, {'left': 1})
        self.assertEqual(self.window.moves_accepted, {'left': 1})

    def test_lost_person_drops_pending_immediately(self):
        self.queue_from_tick()
        self.pose = None
        self.tick_at(10.25)
        self.assertIsNone(self.window.input_gate.pending)
        self.assertEqual(self.window.game.state, 'playing')
        self.pose = {}
        self.tick_at(10.35)
        self.assertEqual(self.window.game.move_counts, {'left': 1})

    def test_partial_tracking_drops_previous_pending(self):
        self.queue_from_tick()
        self.window.gesture.both_valid = False
        self.tick_at(10.25)
        self.assertIsNone(self.window.input_gate.pending)
        self.window.gesture.both_valid = True
        self.tick_at(10.35)
        self.assertEqual(self.window.game.move_counts, {'left': 1})

    def test_help_drops_input_and_does_not_count_menu_commands(self):
        self.queue_from_tick()
        self.window.help()
        self.assertEqual(self.window.game.state, 'paused')
        self.assertIsNone(self.window.input_gate.pending)
        before = dict(self.window.moves_received)
        self.window.perform('palm')
        self.assertEqual(self.window.moves_received, before)
        self.window.help()
        self.tick_at(10.35)
        self.assertEqual(self.window.game.move_counts, {'left': 1})

    def test_focus_loss_clears_keys_guard_and_pending(self):
        self.queue_from_tick()
        self.window.keys.add(10)
        self.window.manual_guard = True
        self.assertFalse(self.window.focus_out())
        self.assertFalse(self.window.keys)
        self.assertFalse(self.window.manual_guard)
        self.assertIsNone(self.window.input_gate.pending)
        self.assertEqual(self.window.game.state, 'paused')

    def test_restart_resets_buffer_and_diagnostics(self):
        self.queue_from_tick()
        self.window.start()
        self.assertEqual(self.window.game.state, 'countdown')
        self.assertIsNone(self.window.input_gate.pending)
        self.assertEqual(self.window.moves_received, {})
        self.assertEqual(self.window.moves_accepted, {})
        self.assertEqual(list(self.window.input_events), [])
        self.assertEqual(self.window.input_feedback, '')

    def test_busy_and_low_energy_have_received_but_not_accepted_counts(self):
        self.window.perform('palm')
        self.window.perform('left')
        self.window.perform('special')
        self.assertEqual(self.window.moves_received, {'palm': 1, 'left': 1, 'special': 1})
        self.assertEqual(self.window.moves_accepted, {'palm': 1})
        self.assertEqual([e['result'] for e in self.window.input_events],
                         ['accepted', 'busy', 'no_energy'])
        self.assertIsNone(self.window.input_gate.pending)

    def test_expired_queue_is_visible_but_not_accepted(self):
        self.queue_from_tick()
        self.tick_at(10.45)
        self.assertEqual(self.window.input_events[-1]['result'], 'expired')
        self.assertEqual(self.window.moves_accepted, {'left': 1})
        self.assertEqual(self.window.game.move_counts, {'left': 1})
        self.assertIn('超时', self.window.input_feedback)

    def test_live_walk_in_does_not_record_or_consume_input(self):
        self.window.args.live_test=True
        self.window.recorder=SimpleNamespace(started=False,start=Mock(),observe=Mock())
        self.window.start()
        self.window.reader.get=Mock(return_value={})
        self.tick_at(20.)
        self.window.reader.get.assert_not_called()
        self.window.recorder.start.assert_not_called()
        self.window.recorder.observe.assert_not_called()
        self.assertEqual(self.window.game.state,'countdown')

    def test_live_round_records_and_finishes_on_game_over(self):
        self.window.args.live_test=True
        rec=SimpleNamespace(started=False,closed=False,started_at=36.,start=Mock(),observe=Mock(),finish=Mock())
        def begin():rec.started=True
        rec.start.side_effect=begin;self.window.recorder=rec
        self.window.start();self.tick_at(36.)
        rec.start.assert_called_once()
        rec.observe.assert_called_once()
        self.assertEqual(self.window.game.state,'playing')
        self.window.game.state='over';self.tick_at(36.1)
        rec.finish.assert_called_once_with('round_over',self.window.input_events)

    def test_paused_walk_in_cannot_start_capture(self):
        self.window.args.live_test=True
        self.window.recorder=SimpleNamespace(started=False,start=Mock(),observe=Mock())
        self.window.start();self.window.pause()
        self.tick_at(40.)
        self.window.recorder.start.assert_not_called()
        self.assertEqual(self.window.game.state,'paused')
        self.window.pause();self.tick_at(41.)
        self.window.recorder.start.assert_not_called()
        self.assertEqual(self.window.game.state,'countdown')


if __name__ == '__main__':
    unittest.main()
