"""Real sword outcomes drive bounded sound cues; no devices, GTK or network."""
import unittest

from .events import AudioEvents
from sword.engine import Game, STEP, MAX_SHOTS, AUDIO_EVENTS, MAX_AUDIO_COUNT
from sword.test_engine import control, advance, isolated_game
from sword.test_danmaku import visible_target_control


class SwordAudioTests(unittest.TestCase):
    def observer(self, game, **kwargs):
        self.calls = []
        result = AudioEvents('sword', lambda sound, **values: self.calls.append((sound, values['key'])), **kwargs)
        result.observe(game, enabled=False)
        return result

    def sounds(self):
        return [sound for sound, _ in self.calls]

    def quiet_game(self, duration=75):
        game = isolated_game(duration)
        game._shot_cd = 1000.
        return game

    def test_read_only_fixed_counters_reset_without_changing_renderer_snapshot(self):
        game = isolated_game()
        first_round = game.audio_round
        self.assertEqual(dict(game.audio_counts), dict.fromkeys(AUDIO_EVENTS, 0))
        with self.assertRaises(TypeError):
            game.audio_counts['shot'] = 1
        with self.assertRaises(AttributeError):
            game.audio_counts = {}
        with self.assertRaises(AttributeError):
            game.audio_round = 10
        game.update(STEP, control())
        self.assertEqual(game.audio_counts['shot'], 1)
        self.assertNotIn('audio_counts', game.snapshot())
        self.assertNotIn('audio_round', game.snapshot())
        game.start(0)
        self.assertGreater(game.audio_round, first_round)
        self.assertEqual(dict(game.audio_counts), dict.fromkeys(AUDIO_EVENTS, 0))

    def test_one_sound_per_accepted_volley_including_partial_capacity(self):
        game = isolated_game(); observer = self.observer(game)
        game.power = 4
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((len(game.shots), game.audio_counts['shot']), (4, 1))
        self.assertEqual(self.sounds(), ['sword_shot'])
        for _ in range(100): observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_shot'])
        while len(game.shots) < MAX_SHOTS: game._shot(.5, .75)
        game._shot_cd = 0.; game.update(STEP, control()); observer.observe(game)
        self.assertEqual(game.audio_counts['shot'], 1)
        self.assertEqual(self.sounds(), ['sword_shot'])
        game.shots.pop(); game._shot_cd=0.
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((len(game.shots), game.audio_counts['shot']), (MAX_SHOTS, 2))
        self.assertEqual(self.sounds(), ['sword_shot', 'sword_shot'])

    def test_real_swept_hit_and_kill_are_distinct_and_dead_target_is_silent(self):
        game = self.quiet_game(); observer = self.observer(game)
        enemy = game._entity('enemy', .5, .50, .032, health=5.6, vy=0.)
        game._shot(.5, .85)['vy'] = -80.
        game.update(STEP, control()); observer.observe(game)
        self.assertFalse(enemy['hit'])
        self.assertEqual((game.audio_counts['hit'], game.audio_counts['kill']), (1, 0))
        self.assertEqual(self.sounds(), ['sword_hit'])
        game._shot(.5, .85)['vy'] = -80.
        game.update(STEP, control()); observer.observe(game)
        self.assertTrue(enemy['hit'])
        self.assertEqual((game.kills, game.score), (1, 100))
        self.assertEqual(self.sounds(), ['sword_hit', 'sword_kill'])
        effects = [e.copy() for e in game.effects]
        game._damage(enemy, 100.); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_hit', 'sword_kill'])
        self.assertEqual(game.effects, effects)

    def test_miss_warning_and_non_damage_do_not_count_hit(self):
        game = self.quiet_game(); observer = self.observer(game)
        enemy = game._entity('enemy', .25, .50, .032, health=8., vy=0.)
        game._shot(.75, .85)['vy'] = -80.
        game.update(STEP, control()); observer.observe(game)
        game._damage(enemy, 0.); observer.observe(game)
        self.assertEqual(self.calls, [])
        enemy.update(x=.5, _origin_x=.5, warning=2.)
        game._shot(.5, .85)['vy'] = -80.
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual(game.audio_counts['hit'], 0)
        self.assertEqual(enemy['health'], 8.)
        self.assertEqual(self.calls, [])

    def test_collect_follows_collision_not_visible_pickup(self):
        game = self.quiet_game(); observer = self.observer(game)
        distant = game._entity('orb', .1, .2, .016)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual(self.calls, [])
        pickup = game._entity('power', .5, .77, .022)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((game.audio_counts['collect'], game.score, game.power), (1, 60, 2))
        self.assertEqual(self.sounds(), ['sword_collect'])
        game._collect(pickup); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_collect'])
        self.assertFalse(distant['hit'])

    def test_hurt_invulnerability_and_lethal_hit_emit_once(self):
        game = self.quiet_game(); observer = self.observer(game)
        for _ in range(3): game._bullet(.5, .78, 0., 0.)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((game.lives, game.audio_counts['hurt']), (4, 1))
        game._bullet(.5, .78, 0., 0.)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_hurt'])
        advance(game, 1.6)
        game.lives = 1
        game._bullet(game.player['x'], game.player['y'], 0., 0.)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((game.phase, game.audio_counts['lose']), ('lost', 1))
        self.assertEqual(self.sounds(), ['sword_hurt', 'sword_hurt', 'sword_lose'])
        for _ in range(100): game.update(STEP, control()); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_hurt', 'sword_hurt', 'sword_lose'])

    def test_special_failure_hold_and_pause_are_silent(self):
        game = self.quiet_game(); observer = self.observer(game)
        game.energy = .99
        game.update(STEP, control(special=True)); observer.observe(game)
        self.assertEqual(game.audio_counts['special'], 0)
        game.energy = 1.
        game.update(STEP, control(special=True)); observer.observe(game)
        self.assertEqual(self.calls, [])
        game.update(STEP, control(special=False))
        game.update(STEP, control(special=True)); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_special'])
        game.energy = 1.; game.pause()
        game.update(.25, control(special=True)); observer.observe(game)
        game.pause(False); game.update(STEP, control(special=True)); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_special'])
        self.assertEqual(game.energy, 1.)

    def test_boss_entry_not_phase_changes_and_true_kill_win_once(self):
        game = self.quiet_game(); observer = self.observer(game)
        game._boss_at = STEP
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_boss'])
        boss = next(e for e in game.entities if e['kind'] == 'boss')
        boss['health'] = boss['max_health'] * .6
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((game.boss_phase, game.audio_counts['boss']), (2, 1))
        self.assertEqual(self.sounds(), ['sword_boss'])
        boss.update(health=2.8, warning=0., x=.5, y=.225, _phase=3)
        game._shot(.5, .50)['vy'] = -50.
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((game.phase, game.kills), ('won', 1))
        self.assertEqual(self.sounds(), ['sword_boss', 'sword_kill', 'sword_win'])
        game._damage(boss, 100.); observer.observe(game)
        self.assertEqual((game.audio_counts['kill'], game.audio_counts['win']), (1, 1))

    def test_timeout_is_loss_and_first_attachment_does_not_replay_it(self):
        game = self.quiet_game(duration=STEP); observer = self.observer(game)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual((game.phase, self.sounds()), ('lost', ['sword_lose']))
        later = self.observer(game); later.observe(game)
        self.assertEqual(self.calls, [])

    def test_pause_tracking_loss_restart_and_reconnect_do_not_replay(self):
        game = isolated_game(); observer = self.observer(game)
        game.update(STEP, control()); observer.observe(game)
        first_key = self.calls[-1][1]
        game.update(.25, control()); observer.observe(game, enabled=False)
        observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_shot'])
        before = dict(game.audio_counts)
        game.pause(); game.update(.25, control()); observer.observe(game)
        game.pause(False); game.update(.25, control(valid=False)); observer.observe(game)
        self.assertEqual(dict(game.audio_counts), before)
        observer.observe(None); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_shot'])
        game.start(0); observer.observe(game)
        game.update(STEP, control()); observer.observe(game)
        self.assertEqual(self.sounds(), ['sword_shot', 'sword_shot'])
        self.assertNotEqual(first_key, self.calls[-1][1])

    def test_counter_and_mapper_work_stay_bounded_under_coalesced_updates(self):
        game = isolated_game(); observer = self.observer(game, history_limit=16)
        for _ in range(100):
            before = len(self.calls)
            game.update(.25, control()); observer.observe(game)
            self.assertLessEqual(len(self.calls) - before, 1)
        self.assertEqual(len(game.audio_counts), 9)
        self.assertLessEqual(len(observer._seen), 16)
        self.assertGreater(game.audio_counts['shot'], len(self.calls))
        game._audio_counts['shot'] = MAX_AUDIO_COUNT - 1
        for _ in range(2):
            game._shot_cd = 0.; game.update(STEP, control())
        self.assertEqual(game.audio_counts['shot'], MAX_AUDIO_COUNT)

    def test_observer_and_failing_output_preserve_full_real_game_and_rng(self):
        observed, silent = Game(seed=260), Game(seed=260)
        observed.start(0); silent.start(0)
        def unavailable(*args, **kwargs): raise RuntimeError('No audio device')
        observer = AudioEvents('sword', unavailable)
        observer.observe(observed, enabled=False)
        for index in range(75 * 30):
            inputs = visible_target_control(observed, index)
            observed.update(1 / 30, inputs); silent.update(1 / 30, inputs)
            observer.observe(observed)
            if index % 180 == 0:
                self.assertEqual(observed.snapshot(), silent.snapshot())
                self.assertEqual(observed.rng.getstate(), silent.rng.getstate())
            if observed.phase in ('won', 'lost'): break
        self.assertEqual(observed.phase, 'won')
        self.assertEqual(observed.snapshot(), silent.snapshot())
        self.assertEqual(observed.rng.getstate(), silent.rng.getstate())
        self.assertGreater(observer.callback_errors, 0)


if __name__ == '__main__': unittest.main()
