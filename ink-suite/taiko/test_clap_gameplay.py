"""Independent clap-lane contracts; synthetic events and an in-memory clock only."""
import hashlib
import unittest
from unittest.mock import patch

from .chart import chart, DURATION
from .engine import Game
from .session import Session
from .test_gameplay import Clock as FakeClock


class ClapGameRules(unittest.TestCase):
    def game(self, lane='C', judgment='relaxed', notes=None):
        if notes is None:
            notes = [dict(id=0, time=2., lane=lane)]
        game = Game(notes=notes, duration=5., judgment=judgment)
        game.pause(False)
        return game

    def test_clap_is_one_hit_with_ordinary_grades_and_its_own_feedback(self):
        boundaries = ((0., 'Perfect', 1000), (.110, 'Perfect', 1000),
                      (.11001, 'Good', 650), (.220, 'Good', 650),
                      (.22001, 'OK', 350), (.350, 'OK', 350))
        for error, grade, points in boundaries:
            for sign in (-1, 1):
                with self.subTest(error=error, sign=sign):
                    game = self.game()
                    at = 2. + sign * error
                    self.assertEqual(game.strike('C', at, 'clap'), grade)
                    self.assertEqual(game.score, points)
                    self.assertEqual(game.combo, 1)
                    self.assertEqual(game.strikes, 1)
                    self.assertEqual(game.notes[0]['hits'], {'C': at})
                    self.assertEqual(game.counts[grade], 1)
                    self.assertEqual(game.feedback[-1]['lane'], 'C')
                    self.assertEqual(game.strike_feedback[-1]['hand'], 'C')
                    self.assertEqual(game.strike_feedback[-1]['outcome'], grade)

    def test_clap_does_not_extend_the_selected_judgment_window(self):
        for profile, error in (('strict', .22001), ('relaxed', .35001)):
            for sign in (-1, 1):
                with self.subTest(profile=profile, sign=sign):
                    game = self.game(judgment=profile)
                    self.assertIsNone(game.strike('C', 2. + sign * error, 'outside'))
                    self.assertEqual(game.score, 0)
                    self.assertEqual(game.notes[0]['status'], 'pending')
                    self.assertEqual(len(game.feedback), 0)

    def test_all_wrong_lane_pairs_leave_the_note_unhit(self):
        for lane, hands in (('C', ('L', 'R')), ('L', ('R', 'C')),
                            ('R', ('L', 'C')), ('B', ('C',))):
            for hand in hands:
                with self.subTest(lane=lane, hand=hand):
                    game = self.game(lane)
                    self.assertIsNone(game.strike(hand, 2., 'wrong'))
                    self.assertEqual(game.notes[0]['hits'], {})
                    self.assertEqual(game.notes[0]['status'], 'pending')
                    self.assertEqual(game.score, 0)
                    self.assertEqual(game.empty_strikes, 1)
                    self.assertEqual(game.last_strike_diagnostic['reason'], 'wrong_hand')
                    self.assertEqual(game.strike_feedback[-1]['hand'], hand)

    def test_clap_cannot_complete_a_half_double_or_steal_its_hand_slot(self):
        game = self.game('B')
        self.assertEqual(game.strike('L', 2., 'left'), 'partial')
        self.assertIsNone(game.strike('C', 2.01, 'clap'))
        self.assertEqual(game.notes[0]['hits'], {'L': 2.})
        self.assertEqual(game.strike('R', 2.05, 'right'), 'Perfect')
        self.assertEqual(game.notes[0]['hits'], {'L': 2., 'R': 2.05})
        self.assertEqual(game.counts['Perfect'], 1)

    def test_diagnostic_nearest_clap_excludes_double_and_drum_notes(self):
        game = self.game(notes=[dict(id=0, time=2., lane='B'),
                                dict(id=1, time=3., lane='C')])
        self.assertIsNone(game.strike('C', 2., 'clap_near_double'))
        diagnostic = game.last_strike_diagnostic
        self.assertEqual(diagnostic['nearest']['lane'], 'B')
        self.assertEqual(diagnostic['nearest_same_hand']['lane'], 'C')
        self.assertEqual(diagnostic['nearest_same_hand']['id'], 1)

    def test_duplicate_clap_never_adds_score_or_feedback(self):
        game = self.game()
        self.assertEqual(game.strike('C', 2., 'same'), 'Perfect')
        for _ in range(20):
            self.assertIsNone(game.strike('C', 2., 'same'))
        self.assertEqual(game.score, 1000)
        self.assertEqual(game.strikes, 1)
        self.assertEqual(len(game.feedback), 1)
        self.assertEqual(len(game.strike_feedback), 1)

    def test_unhit_clap_settles_once_and_cannot_be_restored_by_spam(self):
        game = self.game()
        game.advance(3.)
        self.assertEqual(game.counts['Miss'], 1)
        self.assertEqual(game.feedback[-1]['lane'], 'C')
        for index in range(20):
            self.assertIsNone(game.strike('C', 2., index))
        game.advance(4.)
        self.assertEqual(game.counts['Miss'], 1)
        self.assertEqual(game.score, 0)


class ClapSessionSafety(unittest.TestCase):
    def session(self, *, offset_ms=0., notes=None):
        now = [100.]
        clock = FakeClock(lambda: now[0])
        session = Session(clock, offset_ms=offset_ms, judgment='relaxed')
        session.game = Game(notes=notes or [dict(id=0, time=2., lane='C')],
                            duration=5., judgment='relaxed')
        self.assertTrue(session.resume())
        return session, now, clock

    def event(self, stamp=102., event_id='clap', **metadata):
        return dict(hand='C', timestamp=stamp, id=event_id, path='clap', **metadata)

    def test_event_age_450ms_boundary_is_independent_of_music_lease(self):
        for age, accepted in ((.449999, True), (.45, True), (.450001, False), (.59, False)):
            with self.subTest(age=age):
                session, now, _ = self.session()
                now[0] = 102. + age
                # A newer pose source must not replace the original clap time.
                event = self.event(source_stamp=102.1, source_sequence=7)
                session.tick(now[0], [event])
                self.assertEqual(session.game.counts['Perfect'], int(accepted))
                self.assertEqual(len(session.game.strike_feedback), int(accepted))
                row = session.input_snapshot()['recent'][-1]
                self.assertEqual(row['disposition'], 'Perfect' if accepted else 'expired')
                self.assertAlmostEqual(row['event_age'], age)
                self.assertEqual(row['event_timestamp'], 102.)

    def test_future_event_boundary_still_applies_to_clap(self):
        for ahead, accepted in ((.05, True), (.050001, False)):
            with self.subTest(ahead=ahead):
                session, now, _ = self.session()
                now[0] = 102.
                session.tick(now[0], [self.event(stamp=102. + ahead)])
                self.assertEqual(session.game.strikes, int(accepted))
                self.assertEqual(session.input_snapshot()['recent'][-1]['disposition'],
                                 'Perfect' if accepted else 'future')

    def test_real_pause_resume_rejects_pre_epoch_clap_then_accepts_new_clap(self):
        session, now, clock = self.session()
        now[0] = 101.9
        session.tick(now[0])
        session.pause()
        session.tick(now[0], [self.event(stamp=now[0], event_id='while_paused')])
        now[0] = 110.
        self.assertTrue(session.resume())
        self.assertEqual(session.resume_epoch, 110.)
        now[0] = 110.1
        session.tick(now[0], [self.event(stamp=109.95, event_id='old_epoch')])
        self.assertEqual(session.game.score, 0)
        self.assertEqual(len(session.game.strike_feedback), 0)
        self.assertEqual(session.input_snapshot()['counts']['before_epoch'], 1)
        session.tick(now[0], [self.event(stamp=110.1, event_id='new_epoch')])
        self.assertEqual(session.game.counts['Perfect'], 1)
        self.assertEqual(session.input_snapshot()['counts']['paused'], 1)
        self.assertEqual(clock.plays, 2)

    def test_dedup_is_per_hand_and_repeated_clap_has_no_second_pulse(self):
        session, now, _ = self.session(notes=[dict(id=0, time=2., lane='C'),
                                             dict(id=1, time=2., lane='L')])
        now[0] = 102.1
        clap = self.event(event_id='shared')
        drum = dict(hand='L', timestamp=102., id='shared')
        session.tick(now[0], [clap, clap, drum])
        self.assertEqual(session.game.counts['Perfect'], 2)
        self.assertEqual(session.game.strikes, 2)
        self.assertEqual([p['hand'] for p in session.game.strike_feedback], ['C', 'L'])
        self.assertEqual(session.input_snapshot()['counts']['duplicate'], 1)

    def test_delayed_clap_keeps_source_judgment_time_and_full_feedback_lifetime(self):
        session, now, _ = self.session()
        now[0] = 102.63
        session.tick(now[0], [self.event(stamp=102.30, source_stamp=102.35)])
        self.assertEqual(session.game.counts['OK'], 1)
        feedback = session.game.feedback[-1]
        pulse = session.game.strike_feedback[-1]
        self.assertEqual(feedback['lane'], 'C')
        self.assertEqual(pulse['hand'], 'C')
        self.assertAlmostEqual(feedback['time'], 2.30)
        self.assertAlmostEqual(feedback['display_time'], 2.63)
        self.assertAlmostEqual(pulse['display_time'], 2.63)
        self.assertAlmostEqual(session.input_snapshot()['recent'][-1]['judge_at'], 2.30)

    def test_positive_and_negative_offset_compensate_clap_without_seeking(self):
        for offset, received, stamp in ((200., 102.30, 102.20), (-200., 102., 101.80)):
            with self.subTest(offset=offset):
                session, now, clock = self.session(offset_ms=offset)
                now[0] = received
                session.tick(now[0], [self.event(stamp=stamp)])
                self.assertEqual(session.game.counts['Perfect'], 1)
                self.assertAlmostEqual(session.input_snapshot()['recent'][-1]['judge_at'], 2.)
                self.assertAlmostEqual(session.game.feedback[-1]['display_time'], received - 100.)
                self.assertEqual(clock.seeks, [])

    def test_max_calibration_cannot_make_an_expired_clap_score(self):
        session, now, _ = self.session(offset_ms=500.)
        now[0] = 102.96
        session.tick(now[0], [self.event(stamp=102.5, source_stamp=102.6)])
        self.assertEqual(session.input_snapshot()['recent'][-1]['disposition'], 'expired')
        self.assertEqual(session.game.strikes, 0)
        self.assertEqual(len(session.game.strike_feedback), 0)

    def test_practice_filter_keeps_claps_with_same_preroll_and_tail_safety(self):
        times = (28.8, 30., 31.1, 31.2, 40.4499, 40.4501, 40.5799, 40.5801)
        notes = [dict(id=index, time=at, lane='C') for index, at in enumerate(times)]
        for profile, expected in (('strict', [3, 4, 5, 6]), ('relaxed', [3, 4])):
            with self.subTest(profile=profile):
                now = [100.]
                clock = FakeClock(lambda: now[0])
                with patch('taiko.session.chart', return_value=notes):
                    session = Session(clock, practice='alternate', judgment=profile)
                self.assertEqual([n['id'] for n in session.game.notes], expected)
                self.assertTrue(session.resume())
                self.assertEqual(clock.seeks, [28.8])
                now[0] = 102.4
                session.tick(now[0], [self.event(stamp=now[0])])
                self.assertEqual(session.game.notes[0]['status'], 'Perfect')

    def test_actual_intro_practice_retains_and_scores_its_clap(self):
        for difficulty in ('easy', 'normal', 'hard'):
            with self.subTest(difficulty=difficulty):
                now = [100.]
                session = Session(FakeClock(lambda: now[0]), difficulty, practice='intro')
                claps = [n for n in session.game.notes if n['lane'] == 'C']
                self.assertTrue(claps)
                self.assertTrue(session.resume())
                note = claps[0]
                now[0] = 100. + note['time']
                session.tick(now[0], [self.event(stamp=now[0])])
                self.assertEqual(note['status'], 'Perfect')
                feedback = next(f for f in session.game.feedback if f['note_id'] == note['id'])
                self.assertEqual(feedback['lane'], 'C')
                self.assertEqual(feedback['grade'], 'Perfect')

    def test_actual_mixed_chart_session_replay_has_no_misses_with_bounded_delay(self):
        for difficulty in ('easy', 'normal', 'hard'):
            with self.subTest(difficulty=difficulty):
                now = [100.]
                session = Session(FakeClock(lambda: now[0]), difficulty)
                self.assertTrue(session.resume())
                expected_strikes = 0
                for index, note in enumerate(session.game.notes):
                    delay = (.18, .33)[index % 2]
                    now[0] = 100. + note['time'] + delay
                    hands = ('L', 'R') if note['lane'] == 'B' else (note['lane'],)
                    events = [dict(hand=hand, timestamp=100. + note['time'],
                                   id=(note['id'], hand), source_stamp=100. + note['time'])
                              for hand in hands]
                    session.tick(now[0], events)
                    self.assertEqual(note['status'], 'Perfect')
                    expected_strikes += len(events)
                now[0] = 100. + DURATION
                session.tick(now[0])
                self.assertTrue(session.game.finished)
                self.assertEqual(session.game.counts['Perfect'], len(session.game.notes))
                self.assertEqual(session.game.counts['Miss'], 0)
                self.assertEqual(session.game.strikes, expected_strikes)
                self.assertNotIn('invalid_event', session.input_snapshot()['counts'])


class ClapChartSafety(unittest.TestCase):
    # Frozen before the clap change: lane substitutions must not add/move beats.
    ORIGINAL_TIMELINES = {
        'easy': (57, '9413d706803faa91c17f9f13f483af47faeb9d86facb34fd5b9403b7050a2b61'),
        'normal': (69, '5cb816eb2136e2f31f90b8be806e48b63baf12c5ae541911e42db2515faa4165'),
        'hard': (87, '4bf839d2756f048ec8c32c96246632d30d96620c775abbe1202a7041c3a3a7e1'),
    }

    def test_all_difficulties_preserve_note_count_ids_and_full_time_axis(self):
        for difficulty, (count, expected_hash) in self.ORIGINAL_TIMELINES.items():
            with self.subTest(difficulty=difficulty):
                notes = chart(difficulty)
                self.assertEqual(len(notes), count)
                self.assertEqual([n['id'] for n in notes], list(range(count)))
                timeline = ','.join(format(n['time'], '.6f') for n in notes)
                self.assertEqual(hashlib.sha256(timeline.encode('ascii')).hexdigest(), expected_hash)
                self.assertEqual(notes[-1]['time'], 69.6)

    def test_claps_are_sparse_and_intro_and_double_drums_remain(self):
        expected_intro = [(2.4, 'L'), (3.6, 'R'), (4.8, 'L'), (6., 'R'),
                          (7.2, 'L'), (8.4, 'R'), (9.6, 'L')]
        for difficulty in self.ORIGINAL_TIMELINES:
            with self.subTest(difficulty=difficulty):
                notes = chart(difficulty)
                claps = [n for n in notes if n['lane'] == 'C']
                self.assertTrue(claps)
                self.assertTrue(any(n['lane'] == 'B' for n in notes))
                self.assertEqual([(n['time'], n['lane']) for n in notes if n['time'] < 10.],
                                 expected_intro)
                for clap in claps:
                    self.assertGreaterEqual(clap['time'], 10.)
                    neighbors = [n for n in notes if n['id'] != clap['id']]
                    self.assertGreaterEqual(min(abs(n['time'] - clap['time']) for n in neighbors),
                                            1.2 - 1e-9)

    def test_mixed_chart_exact_replay_judges_claps_and_doubles_independently(self):
        for difficulty in self.ORIGINAL_TIMELINES:
            with self.subTest(difficulty=difficulty):
                game = Game(difficulty)
                game.pause(False)
                expected_strikes = 0
                for note in game.notes:
                    game.advance(note['time'])
                    hands = ('L', 'R') if note['lane'] == 'B' else (note['lane'],)
                    for hand in hands:
                        game.strike(hand, note['time'], (note['id'], hand))
                        expected_strikes += 1
                    self.assertEqual(note['status'], 'Perfect')
                    if note['lane'] == 'C':
                        self.assertEqual(note['hits'], {'C': note['time']})
                game.advance(DURATION)
                self.assertTrue(game.finished)
                self.assertEqual(game.counts['Perfect'], len(game.notes))
                self.assertEqual(game.counts['Miss'], 0)
                self.assertEqual(game.strikes, expected_strikes)


if __name__ == '__main__':
    unittest.main()
