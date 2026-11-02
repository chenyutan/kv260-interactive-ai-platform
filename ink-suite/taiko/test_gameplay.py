"""Gameplay outcomes and transport-boundary regressions; no GTK/audio devices."""
import math
import unittest

from .audio_clock import SilentClock
from .chart import chart, PRACTICE
from .engine import Game, GOOD, INPUT_GRACE, FEVER_DURATION
from .session import Session


def perform(game, note, offset=0.):
    at = note['time'] + offset
    for hand in ('L', 'R') if note['lane'] == 'B' else (note['lane'],):
        game.strike(hand, at, (note['id'], hand))


class Clock(SilentClock):
    def __init__(self, now):
        super().__init__(now=now)
        self.seeks = []
        self.pauses = 0
        self.plays = 0
        self.fail_seek = False
        self.fail_play = False

    def seek(self, seconds):
        self.seeks.append(seconds)
        if self.fail_seek:
            self.error = 'seek failed'
            return False
        return super().seek(seconds)

    def pause(self):
        self.pauses += 1
        return super().pause()

    def play(self):
        self.plays += 1
        if self.fail_play:
            self.error = 'play failed'
            return False
        return super().play()


class Gameplay(unittest.TestCase):
    def sequence(self, count=25, interval=1.):
        notes = [dict(id=i, time=1+i*interval, lane='L') for i in range(count)]
        game = Game(notes=notes, duration=notes[-1]['time'] + 2)
        game.pause(False)
        return game

    def test_existing_chart_counts_and_first_ten_seconds_preserved(self):
        self.assertEqual(len(chart('easy')), 57)
        self.assertEqual(len(chart('normal')), 69)
        expected = [(2.4, 'L'), (3.6, 'R'), (4.8, 'L'), (6., 'R'),
                    (7.2, 'L'), (8.4, 'R'), (9.6, 'L'), (10.8, 'C')]
        for level in ('easy', 'normal'):
            self.assertEqual([(n['time'], n['lane']) for n in chart(level)[:8]], expected)
            self.assertEqual(chart(level)[-1]['time'], 69.6)

    def test_hard_is_full_song_and_has_three_complete_challenges(self):
        game = Game('hard')
        game.pause(False)
        self.assertGreater(len(game.notes), len(chart('normal')))
        for note in game.notes:
            game.advance(note['time'])
            perform(game, note)
        game.advance(72)
        snap = game.snapshot()
        self.assertEqual(snap['rank'], 'S')
        self.assertEqual(snap['accuracy'], 100.)
        self.assertEqual(snap['bonus_score'], 5200)
        self.assertEqual([c['status'] for c in snap['challenges']], ['complete']*3)
        self.assertTrue(game.finished)

    def test_fever_charges_then_doubles_and_expires_on_music_time(self):
        game = self.sequence()
        for note in game.notes[:13]:
            game.advance(note['time'])
            perform(game, note)
        self.assertEqual(game.groove, 0)
        self.assertEqual(game.feedback[-1]['multiplier'], 1)  # Triggering note.
        self.assertAlmostEqual(game.snapshot()['fever_remaining'], FEVER_DURATION)
        game.advance(14.)
        perform(game, game.notes[13])
        self.assertEqual(game.feedback[-1]['points'], 2040)
        self.assertEqual(game.feedback[-1]['multiplier'], 2)
        self.assertEqual(game.groove, 0)
        game.advance(17.8)
        self.assertFalse(game.snapshot()['fever_active'])
        perform(game, game.notes[17])
        self.assertEqual(game.feedback[-1]['multiplier'], 1)
        self.assertEqual(game.groove, 8)

    def test_pause_cannot_advance_fever_or_create_scores(self):
        game = self.sequence()
        for note in game.notes[:13]:
            game.advance(note['time'])
            perform(game, note)
        game.pause()
        before = game.snapshot()
        for i in range(100):
            game.advance(1000+i)
            game.strike('L', 14., i)
        after = game.snapshot()
        self.assertEqual(after, before)

    def test_miss_drops_groove_and_empty_hits_never_charge(self):
        game = self.sequence()
        for note in game.notes[:4]:
            game.advance(note['time'])
            perform(game, note)
        self.assertEqual(game.groove, 32)
        score = game.score
        for i in range(100):
            game.strike('R', 4., i)
        self.assertEqual(game.score, score)
        self.assertEqual(game.groove, 32)
        game.advance(5.7)
        self.assertEqual(game.groove, 8)
        self.assertEqual(game.combo, 0)

    def test_timing_reports_worst_hand_and_miss_has_no_fake_measurement(self):
        game = Game(notes=[dict(id=1, time=2., lane='B')], duration=4.)
        game.pause(False)
        game.strike('L', 1.85, 'left')
        game.strike('R', 1.94, 'right')
        self.assertEqual(game.feedback[-1]['grade'], 'Good')
        self.assertEqual(game.feedback[-1]['timing'], 'early')
        self.assertAlmostEqual(game.feedback[-1]['timing_ms'], -150.)
        game = self.sequence(1)
        game.advance(3.)
        self.assertIsNone(game.feedback[-1]['timing_ms'])
        self.assertEqual(game.feedback[-1]['timing'], 'miss')

    def test_accuracy_only_uses_judged_notes(self):
        game = self.sequence(4)
        self.assertEqual(game.snapshot()['rank'], '--')
        perform(game, game.notes[0])
        self.assertEqual(game.snapshot()['accuracy'], 100.)
        perform(game, game.notes[1], .2)
        self.assertAlmostEqual(game.snapshot()['accuracy'], 82.5)
        self.assertEqual(game.snapshot()['rank'], 'B')
        game.advance(3.7)
        self.assertAlmostEqual(game.snapshot()['accuracy'], 55.)
        self.assertEqual(game.snapshot()['rank'], 'C')
        game.advance(6.)
        self.assertAlmostEqual(game.snapshot()['accuracy'], 41.25)
        self.assertEqual(game.snapshot()['rank'], 'D')

    def test_relaxed_ok_is_a_lower_weight_hit_with_combo_groove_and_fever(self):
        notes=[dict(id=i,time=1+i,lane='L') for i in range(5)]
        game=Game(notes=notes,duration=7.,judgment='relaxed');game.pause(False)
        perform(game,game.notes[0])
        perform(game,game.notes[1],.2)
        perform(game,game.notes[2],.3)
        self.assertEqual(game.counts,dict(Perfect=1,Good=1,Miss=0,OK=1))
        self.assertEqual(game.score,2000);self.assertEqual(game.combo,3)
        self.assertEqual(game.best_combo,3);self.assertEqual(game.groove,16)
        self.assertAlmostEqual(game.snapshot()['accuracy'],200/3)
        game.fever_until=6.
        perform(game,game.notes[3],.3)
        self.assertEqual(game.feedback[-1]['points'],700)
        self.assertEqual(game.feedback[-1]['multiplier'],2)
        self.assertEqual(game.groove,16)
        game.advance(7.)
        self.assertEqual(game.counts['Miss'],1);self.assertEqual(game.combo,0)
        self.assertEqual(game.groove,0)

    def test_relaxed_ok_retains_combo_bonus_and_completes_phrases(self):
        game=Game('hard',judgment='relaxed');game.pause(False)
        for note in game.notes:
            game.advance(note['time'])
            perform(game,note,.3)
        game.advance(72.)
        self.assertEqual(game.counts['OK'],len(game.notes))
        self.assertEqual(game.counts['Miss'],0)
        self.assertEqual(game.best_combo,len(game.notes))
        self.assertEqual(game.bonus_score,5200)
        self.assertEqual([c['status'] for c in game.challenges],['complete']*3)
        self.assertAlmostEqual(game.snapshot()['accuracy'],35.)

    def test_failed_phrase_is_not_awarded_or_restored_by_spam(self):
        game = Game('hard')
        game.pause(False)
        skipped = next(n for n in game.notes if n['time'] >= 31.2)
        for note in game.notes:
            game.advance(note['time'])
            if note is not skipped:
                perform(game, note)
        for i in range(100):
            game.strike(skipped['lane'], skipped['time'], i)
        game.advance(72)
        self.assertEqual(game.challenges[0]['status'], 'failed')
        self.assertEqual(game.bonus_score, 4000)

    def test_ending_settles_every_pending_note_once(self):
        game = Game(notes=[dict(id=0, time=3.8, lane='B')], duration=4.)
        game.pause(False)
        game.strike('L', 3.8, 'only-one')
        game.advance(4.)
        game.advance(9.)
        self.assertEqual(game.counts, dict(Perfect=0, Good=0, Miss=1))
        self.assertTrue(all(n['status'] != 'pending' for n in game.notes))

    def test_invalid_intervals_are_rejected(self):
        for start, end in ((2., 2.), (3., 2.), (-1., 2.), (0., math.inf), (math.nan, 3.)):
            with self.assertRaises(ValueError):
                Game(notes=[], start_time=start, duration=end)
        with self.assertRaises(ValueError):
            Game(notes=[dict(id=0, time=1., lane='L')], start_time=2., duration=4.)


class PracticeSession(unittest.TestCase):
    def setUp(self):
        self.now = [100.]
        self.clock = Clock(lambda: self.now[0])

    def test_every_difficulty_and_section_prerolls_and_restarts(self):
        for difficulty in ('easy', 'normal', 'hard'):
            for practice, (start, end) in PRACTICE.items():
                session = Session(self.clock, difficulty, practice=practice)
                self.assertEqual(session.game.time, start)
                self.assertTrue(session.restart())
                self.assertEqual(self.clock.seeks[-1], start)
                self.assertAlmostEqual(self.clock.position(), start)
                self.assertTrue(all(start <= n['time'] < end-GOOD for n in session.game.notes))
                if practice in ('alternate', 'finale'):
                    self.assertTrue(all(n['time'] >= start+2.4 for n in session.game.notes))
                self.assertTrue(session.resume())
                session.close()

    def test_nonzero_section_must_seek_before_first_play(self):
        session = Session(self.clock, 'hard', practice='alternate')
        self.assertEqual(self.clock.position(), 0.)
        self.assertTrue(session.resume())
        self.assertAlmostEqual(self.clock.position(), 28.8)
        self.assertEqual(self.clock.seeks, [28.8])

    def test_judgment_switch_is_paused_untouched_and_does_not_seek_or_retime(self):
        session=Session(self.clock)
        self.assertEqual(session.judgment,'strict')
        session.resume();self.now[0]+=1.;session.tick(self.now[0])
        with self.assertRaises(RuntimeError):session.set_judgment('relaxed')
        session.pause()
        before=(session.game.time,self.clock.position(),list(self.clock.seeks),session.offset)
        self.assertTrue(session.set_judgment('relaxed'))
        self.assertEqual(session.game.judgment,'relaxed')
        self.assertEqual((session.game.time,self.clock.position(),self.clock.seeks,session.offset),before)
        self.assertEqual(session.game.counts['OK'],0)
        session.resume();self.now[0]+=.2;session.tick(self.now[0])
        self.assertAlmostEqual(session.game.time,1.2)

    def test_judgment_switch_rejects_any_prior_strike_or_settled_note(self):
        for prior in ('empty','partial','scored','miss'):
            with self.subTest(prior=prior):
                clock=Clock(lambda:self.now[0]);session=Session(clock)
                session.game=Game(notes=[dict(id=0,time=2.,lane='B' if prior=='partial' else 'L')],duration=4.)
                session.resume()
                if prior=='miss':session.game.advance(3.)
                else:session.game.strike('L',1. if prior=='empty' else 2.,prior)
                session.pause();old=session.game
                with self.assertRaises(RuntimeError):session.set_judgment('relaxed')
                self.assertIs(session.game,old);self.assertEqual(session.judgment,'strict')
                self.assertTrue(session.restart())
                self.assertTrue(session.set_judgment('relaxed'))
                self.assertEqual(session.game.score,0)
                self.assertEqual(session.game.strikes,0)

    def test_profile_validation_and_same_profile_never_rebuild_used_round(self):
        for invalid in ('unknown',None,[]):
            with self.assertRaises(ValueError):Session(self.clock,judgment=invalid)
        session=Session(self.clock);old=session.game
        with self.assertRaises(ValueError):session.set_judgment('unknown')
        self.assertIs(session.game,old);self.assertEqual(session.judgment,'strict')
        session.resume();session.game.strike('L',2.4,'one');session.pause()
        with self.assertRaises(RuntimeError):session.set_judgment('strict')

    def test_relaxed_practice_tail_filter_and_restart_keep_selected_profile(self):
        for practice,(start,end) in PRACTICE.items():
            session=Session(self.clock,'hard',practice=practice,judgment='relaxed')
            self.assertTrue(all(max(start,session.game.lead_in_until)<=n['time']<end-.350 for n in session.game.notes))
            self.assertEqual(session.game.input_grace,INPUT_GRACE)
            self.assertTrue(session.restart());self.assertEqual(session.game.judgment,'relaxed')
            self.assertEqual(session.game.counts['OK'],0)
            session.close()

    def test_relaxed_last_ok_with_delay_and_calibration_settles_once(self):
        session=Session(self.clock,'hard',offset_ms=500,practice='alternate',judgment='relaxed')
        session.resume();note=session.game.notes[-1]
        music_at=note['time']+.35+.45+.5
        self.now[0]=100.+music_at-session.game.start_time
        session.tick(self.now[0],[dict(hand=note['lane'],timestamp=self.now[0]-.45,id='last_ok')])
        self.assertEqual(session.game.notes[-1]['status'],'OK')
        self.assertAlmostEqual(session.game.input_grace,.95)
        self.assertAlmostEqual(session.game.duration,41.75)
        session.pause();before=(session.game.time,list(self.clock.seeks))
        session.set_offset_ms(-500)
        self.assertEqual((session.game.time,self.clock.seeks),before)
        for invalid in (-501,501,math.nan,math.inf):
            with self.assertRaises(ValueError):session.set_offset_ms(invalid)

    def test_late_last_hit_with_max_positive_calibration_is_not_swallowed(self):
        session = Session(self.clock, 'hard', offset_ms=500, practice='alternate')
        self.assertTrue(session.resume())
        note = session.game.notes[-1]
        self.assertEqual(note['time'], 40.2)
        # Last Good is observed 450 ms late, with 500 ms user calibration.
        # Real music clock is already beyond the 40.8 second chart cutoff.
        music_at = note['time'] + .21 + .45 + .5
        self.now[0] = 100. + music_at - 28.8
        event = dict(hand=note['lane'], timestamp=self.now[0]-.45, id='last')
        session.tick(self.now[0], [event])
        self.assertEqual(session.game.notes[-1]['status'], 'Good')
        self.assertFalse(session.game.finished)
        self.assertAlmostEqual(session.game.duration, 41.75)
        self.now[0] = 100. + 41.75 - 28.8
        session.tick(self.now[0])
        self.assertTrue(session.game.finished)
        self.assertTrue(all(n['status'] != 'pending' for n in session.game.notes))

    def test_section_eof_cannot_catch_a_note_from_before_the_section(self):
        session = Session(self.clock, practice='alternate')
        session.resume()
        self.assertIsNone(session.game.strike('L', 28., 'old'))
        self.assertEqual(session.game.score, 0)
        self.clock.eos = True
        session.tick(self.now[0])
        self.assertTrue(session.game.finished)

    def test_offset_changes_grace_without_seek_or_time_change(self):
        session = Session(self.clock, practice='alternate')
        session.resume()
        self.now[0] += 3.
        session.tick(self.now[0])
        before = (session.game.time, self.clock.position(), list(self.clock.seeks))
        session.set_offset_ms(500)
        self.assertEqual((session.game.time, self.clock.position(), self.clock.seeks), before)
        self.assertAlmostEqual(session.game.input_grace, .95)
        self.assertAlmostEqual(session.game.duration, 41.75)
        session.set_offset_ms(-500)
        self.assertAlmostEqual(session.game.input_grace, INPUT_GRACE)
        for invalid in (-501, 501, math.nan, math.inf):
            with self.assertRaises(ValueError):
                session.set_offset_ms(invalid)

    def test_pause_and_resume_native_calls_are_idempotent(self):
        session = Session(self.clock)
        for _ in range(50):
            session.pause()
        self.assertEqual(self.clock.pauses, 0)
        for _ in range(50):
            self.assertTrue(session.resume())
        self.assertEqual(self.clock.plays, 1)
        for _ in range(50):
            session.pause()
        self.assertEqual(self.clock.pauses, 1)

    def test_seek_failure_preserves_old_score_and_blocks_new_round(self):
        session = Session(self.clock)
        session.resume()
        self.now[0] += 2.4
        session.tick(self.now[0], [dict(hand='L', timestamp=self.now[0], id='one')])
        old_score = session.game.score
        session.practice = 'alternate'
        self.clock.fail_seek = True
        self.assertFalse(session.restart())
        self.assertEqual(session.game.score, old_score)
        self.assertFalse(session.running)
        self.assertFalse(session.resume())
        self.assertEqual(session.game.practice, 'full')
        clock = Clock(lambda: self.now[0])
        self.assertTrue(session.replace_clock(clock))
        self.assertTrue(session.resume())
        self.assertEqual(session.game.practice, 'alternate')
        self.assertEqual(session.game.score, 0)
        self.assertAlmostEqual(clock.position(), 28.8)

    def test_finished_game_can_recover_failed_restart_then_audio_replacement(self):
        session = Session(self.clock)
        session.resume()
        self.now[0] += 72.
        session.tick(self.now[0])
        self.assertTrue(session.game.finished)
        self.clock.fail_seek = True
        session.practice = 'finale'
        self.assertFalse(session.restart())
        clock = Clock(lambda: self.now[0])
        self.assertTrue(session.replace_clock(clock))
        self.assertTrue(session.resume())
        self.assertFalse(session.game.finished)
        self.assertEqual(session.game.time, 57.6)

    def test_play_failure_never_runs_or_scores(self):
        session = Session(self.clock)
        self.clock.fail_play = True
        self.assertFalse(session.resume())
        self.assertFalse(session.running)
        self.assertTrue(session.game.paused)
        self.assertEqual(self.clock.pauses, 1)
        self.now[0] += 3.
        session.tick(self.now[0], [dict(hand='L', timestamp=self.now[0], id='false')])
        self.assertEqual(session.game.score, 0)

    def test_delivery_frame_rate_does_not_change_fever_or_phrase_score(self):
        outcomes = []
        for fps in (15, 30, 60):
            now = [100.]
            session = Session(SilentClock(now=lambda: now[0]), 'hard')
            session.resume()
            notes = chart('hard')
            index = 0
            for frame in range(72*fps+1):
                music = frame/fps
                now[0] = 100. + music
                events = []
                while index < len(notes) and notes[index]['time'] + .18 <= music:
                    note = notes[index]
                    for hand in ('L', 'R') if note['lane'] == 'B' else (note['lane'],):
                        events.append(dict(hand=hand, timestamp=100.+note['time'],
                                           id=(note['id'], hand)))
                    index += 1
                session.tick(now[0], events)
            outcomes.append((session.game.score, session.game.bonus_score, session.game.counts))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0], outcomes[2])
        self.assertEqual(outcomes[0][2]['Miss'], 0)


if __name__ == '__main__':
    unittest.main()
