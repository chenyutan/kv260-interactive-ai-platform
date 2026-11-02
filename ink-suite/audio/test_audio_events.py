"""Outcome-only audio hooks: no device, camera, GTK or network access."""
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys
import unittest

from .events import AudioEvents


SUITE = Path(__file__).resolve().parents[1]


def engine(folder, name):
    path = SUITE.parent / folder / 'native'
    if not (path/'engine.py').is_file():
        path=Path('/home/ubuntu')/dict(FruitSlice='fruit-slice',InkFight='ink-fight')[folder]/'native'
    sys.path.insert(0, str(path))
    try:
        spec = importlib.util.spec_from_file_location(name, path / 'engine.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path))


class Outcomes(unittest.TestCase):
    def adapter(self, mode, **kw):
        self.calls = []
        return AudioEvents(mode, lambda sound, **fields: self.calls.append((sound, fields['key'])), **kw)

    def sounds(self):
        return [sound for sound, key in self.calls]

    def test_ink_first_packet_holds_and_reconnect_are_silent(self):
        a = self.adapter('ink')
        s = dict(pid=8, event_count=20, art_revision=30)
        a.observe(s)
        for _ in range(100): a.observe(s)
        self.assertEqual(self.calls, [])
        s.update(event_count=21, art_revision=31); a.observe(s); a.observe(s)
        self.assertEqual(self.sounds(), ['ink_stroke'])
        a.observe(None)
        s.update(event_count=25, art_revision=40); a.observe(s)
        s.update(pid=9, event_count=100, art_revision=0); a.observe(s)
        self.assertEqual(self.sounds(), ['ink_stroke'])

    def test_ink_undo_clear_and_pause_never_become_strokes(self):
        a = self.adapter('ink')
        a.observe(dict(pid=1, event_count=2, art_revision=1))
        a.observe(dict(pid=1, event_count=0, art_revision=2))
        a.observe(dict(pid=1, event_count=1, art_revision=3), enabled=False)
        a.observe(dict(pid=1, event_count=1, art_revision=3))
        a.command('save', False, key='rejected')
        a.command('save', True, key='saved'); a.command('save', True, key='saved')
        a.command('clear', True, key='cleared')
        a.command('undo', True, key='undo')
        self.assertEqual(self.sounds(), ['ink_save', 'ink_clear'])

    def test_fruit_visual_queue_survives_and_keys_deduplicate(self):
        a = self.adapter('fruit')
        game = engine('FruitSlice', 'audio_fruit_test').Game(); game.start(); game.state = 'playing'
        a.observe(game)
        for kind in ('normal', 'double', 'ice', 'bomb'):
            game.cut(game.spawn(kind))
        visual = list(game.events)
        for i, event in enumerate(game.events): a.fruit_cut(event, key=i)
        for i, event in enumerate(game.events): a.fruit_cut(event, key=i)
        self.assertEqual(list(game.events), visual)
        self.assertEqual(self.sounds(), ['fruit_slice', 'fruit_double', 'fruit_freeze', 'fruit_combo', 'fruit_bomb'])

    def test_fruit_only_actual_misses_and_no_pause_backlog(self):
        a = self.adapter('fruit'); game = SimpleNamespace(elapsed=0, missed=0, state='playing')
        a.observe(game); game.elapsed=1; game.missed=3; a.observe(game); a.observe(game)
        self.assertEqual(self.sounds(), ['fruit_miss'])
        game.state='paused'; game.missed=4; a.observe(game)
        game.state='playing'; a.observe(game)
        a.fruit_cut(dict(kind='normal'), key=5, enabled=False)
        a.fruit_cut(dict(kind='normal'), key=5)
        self.assertEqual(self.sounds(), ['fruit_miss'])

    def test_fight_accepted_moves_only_and_real_collision(self):
        a = self.adapter('fight'); game = engine('InkFight', 'audio_fight_test').Fight()
        a.observe(game); self.assertFalse(game.perform('left')); a.observe(game)
        game.start(); game.countdown=0; game.advance(.001); game.ai_enabled=False
        a.reset(); a.observe(game)
        self.assertTrue(game.perform('left')); a.observe(game)
        self.assertFalse(game.perform('right')); a.observe(game)
        self.assertEqual(self.sounds(), ['fight_swing'])
        game.advance(.13); a.observe(game)
        for _ in range(8): game.advance(.01); a.observe(game)
        self.assertEqual(self.sounds(), ['fight_swing', 'fight_hit'])
        self.assertEqual(len(game.effects), 1)
        game.advance(.3); game.enemy_x=1130.; game.player_x=150.
        self.assertTrue(game.perform('kick_left')); a.observe(game); game.advance(.6); a.observe(game)
        self.assertEqual(self.sounds(), ['fight_swing', 'fight_hit', 'fight_kick'])

    def test_fight_block_not_guard_pose_and_finish_once(self):
        a = self.adapter('fight')
        game = SimpleNamespace(time=0., state='playing', move_counts={}, effects=[], result='')
        a.observe(game)
        for _ in range(100): game.guard=True; a.observe(game)
        self.assertEqual(self.calls, [])
        effect=dict(kind='block', age=0.)
        game.effects.append(effect); a.observe(game)
        for i in range(20): effect['age']=i / 100; a.observe(game)
        game.state='finishing'; game.result='胜'; a.observe(game)
        game.state='over'; a.observe(game); a.observe(game)
        self.assertEqual(self.sounds(), ['fight_block', 'fight_win'])

    def test_fight_pause_restart_and_rejected_special(self):
        a = self.adapter('fight'); game = engine('InkFight', 'audio_fight_test2').Fight()
        game.start(); game.state='playing'; a.observe(game)
        self.assertFalse(game.perform('special')); a.observe(game)
        game.energy=100; self.assertTrue(game.perform('special')); a.observe(game, enabled=False)
        a.observe(game)
        self.assertEqual(self.calls, [])
        game.start(); game.state='playing'; a.reset(); a.observe(game)
        game.energy=100; game.perform('special'); a.observe(game)
        self.assertEqual(self.sounds(), ['fight_special'])

    def test_taiko_chart_alone_never_plays_and_each_double_hand_sounds_once(self):
        from taiko.engine import Game
        a = self.adapter('taiko')
        game = Game(notes=[dict(id=0,time=1.,lane='B'),dict(id=1,time=2.,lane='L')], duration=4.)
        game.pause(False); a.observe(game)
        game.advance(1.); a.observe(game)
        self.assertEqual(self.calls, [])
        self.assertEqual(game.strike('L',1.,'left'), 'partial'); a.observe(game)
        self.assertEqual(self.sounds(), ['taiko_left'])
        self.assertEqual(game.strike('R',1.03,'right'), 'Perfect'); a.observe(game)
        for _ in range(100): a.observe(game.snapshot())
        self.assertEqual(self.sounds(), ['taiko_left', 'taiko_right'])
        self.assertIsNone(game.strike('R',1.03,'right')); a.observe(game)
        game.advance(3.); a.observe(game); game.advance(4.); game.pause(); a.observe(game)
        self.assertEqual(self.sounds(), ['taiko_left', 'taiko_right', 'taiko_miss', 'taiko_finish'])
        self.assertEqual(game.score, 1000)

    def test_taiko_empty_strike_sounds_once_without_score_and_old_snapshot_fallback(self):
        from taiko.engine import Game
        a=self.adapter('taiko');game=Game(notes=[],duration=4.)
        game.pause(False);a.observe(game)
        game.strike('L',1.,'empty');a.observe(game);a.observe(game.snapshot())
        game.strike('L',1.,'empty');a.observe(game)
        self.assertEqual(self.sounds(),['taiko_left']);self.assertEqual(game.score,0)
        a=self.adapter('taiko');snapshot=dict(time=0.,counts={},paused=False,feedback=[])
        a.observe(snapshot)
        snapshot.update(time=1.,counts={'Perfect':1},feedback=[dict(note_id=0,lane='B',grade='Perfect')])
        before=len(self.calls);a.observe(snapshot);a.observe(snapshot)
        self.assertEqual(self.sounds()[before:],['taiko_both'])

    def test_taiko_feedback_preserved_and_no_resume_replay(self):
        from taiko.engine import Game
        a = self.adapter('taiko'); game=Game(notes=[dict(id=0,time=1.,lane='R')], duration=4.)
        game.pause(False); a.observe(game); game.strike('R',1.,'one')
        original=list(game.feedback)
        a.observe(game, enabled=False); a.observe(game)
        self.assertEqual(self.calls, []); self.assertEqual(list(game.feedback), original)
        a.reset(); a.observe(game)
        self.assertEqual(self.calls, [])
        new=Game(notes=[dict(id=0,time=1.,lane='R')], duration=4.)
        new.pause(False); a.observe(new); new.strike('R',1.,'one'); a.observe(new)
        self.assertEqual(self.sounds(), ['taiko_right'])

    def test_clap_has_audible_feedback_once_without_replaying_on_resume(self):
        from taiko.engine import Game
        a=self.adapter('taiko');game=Game(notes=[dict(id=0,time=1.,lane='C')],duration=4.)
        game.pause(False);a.observe(game);game.strike('C',1.,'clap');a.observe(game)
        a.observe(game.snapshot());game.strike('C',1.,'clap');a.observe(game)
        self.assertEqual(self.sounds(),['taiko_both'])
        game.pause(True);a.observe(game,enabled=False);game.pause(False);a.observe(game)
        self.assertEqual(self.sounds(),['taiko_both'])

    def test_taiko_small_key_history_cannot_replay_the_longer_strike_ring(self):
        from taiko.engine import Game
        a=self.adapter('taiko',history_limit=16);game=Game(notes=[],duration=4.)
        game.pause(False);a.observe(game)
        for i in range(80):
            game.strike('L',1.,i);a.observe(game);a.observe(game.snapshot())
        self.assertEqual(len(self.calls),80)
        a.observe(game,enabled=False);a.observe(game)
        self.assertEqual(len(self.calls),80)

    def test_callback_failure_never_breaks_gameplay_and_history_is_bounded(self):
        a = self.adapter('fruit', history_limit=16)
        for key in range(1000): a.fruit_cut(dict(kind='normal'), key=key)
        self.assertLessEqual(len(a._seen),16)
        keys = [key for _,key in self.calls]
        self.assertEqual(len(set(keys)),len(keys))
        def fail(*args, **kw): raise RuntimeError('missing device')
        a.emit=fail; a.fruit_cut(dict(kind='bomb'),key='last')
        self.assertEqual(a.callback_errors,1)


if __name__ == '__main__': unittest.main()
