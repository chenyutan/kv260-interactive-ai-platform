"""Offline end-to-end wrist trajectories, with no camera/audio-device access.

These tests enter through DrumInput and Session, never Game.strike/score.
They establish synthetic playability and lifecycle safety, not human accuracy.
The replay makes downstrokes only: independent C notes must remain misses.
"""
import math
import unittest

from .audio_clock import SilentClock
from .chart import chart, DURATION
from .input import DrumInput
from .session import Session
from audio.events import AudioEvents


def wrist_y(at, targets):
    """Raise, hold, downstroke across y=.68 at target, then settle.

    All positions are measured in shoulder widths. A complete stroke needs
    0.44 s; it does not teleport points onto the hit line or mutate judges.
    """
    for target in targets:
        relative = at - target
        if -.40 <= relative < -.24:
            return .94 - .80 * (relative + .40) / .16
        if -.24 <= relative < -.10:
            return .14
        if -.10 <= relative < .04:
            return .14 + .54 * (relative + .10) / .10
    return .94


def pose(source_time, sequence, left=.94, right=.94, pid=260):
    points = [[.5, .5, 0.] for _ in range(17)]
    points[5] = [.60, .30, .9]
    points[6] = [.40, .30, .9]
    for i, x, y in ((9, .63, left), (10, .37, right)):
        points[i] = [x, .30 + y * (.2 * 16 / 9), .9]
    return dict(pid=pid, sequence=sequence,
                timestamp_seconds=source_time, points17=points)


def replay(difficulty, fps, *, delay=.18, jitter=False, repeat_frames=False, practice='full',
           judgment='strict', stroke_offset=0.):
    now = [100.]
    clock = SilentClock(now=lambda: now[0])
    session = Session(clock, difficulty, practice=practice,judgment=judgment)
    tracker = DrumInput()
    emitted = []
    audio = AudioEvents('taiko', lambda sound, **kw: emitted.append((sound, kw['key'])))
    audio.observe(session.game, enabled=False)
    session.resume()
    notes = session.game.notes
    start = session.game.start_time
    targets = {hand: [n['time']+stroke_offset for n in notes if n['lane'] in (hand, 'B')]
               for hand in ('L', 'R')}
    detected = []
    for frame in range(math.ceil((session.game.duration - start) * fps) + 1):
        elapsed = frame / fps
        observed = start + elapsed
        pipeline_age = delay + (.017 * math.sin(observed * 1.7) if jitter else 0.)
        now[0] = 100. + elapsed + pipeline_age
        state = pose(100. + elapsed, frame,
                     wrist_y(observed, targets['L']), wrist_y(observed, targets['R']))
        control = tracker.consume(state, now[0])
        detected.extend(control['events'])
        session.tick(now[0], control['events'])
        audio.observe(session.game)
        if repeat_frames:
            duplicate = tracker.consume(state, now[0] + .002)
            if duplicate['events']:
                raise AssertionError('Duplicate pose produced another strike')
            session.tick(now[0], duplicate['events'])
            audio.observe(session.game)
    session.close()
    return session.game, detected, emitted


class WholeGesturePlayability(unittest.TestCase):
    def test_all_difficulties_three_observation_rates_with_pipeline_age(self):
        for difficulty in ('easy', 'normal', 'hard'):
            for fps in (15, 30, 60):
                with self.subTest(difficulty=difficulty, fps=fps):
                    game, detected, emitted = replay(difficulty, fps)
                    expected = chart(difficulty)
                    drums = [n for n in expected if n['lane'] != 'C']
                    claps = len(expected) - len(drums)
                    self.assertEqual(game.counts, dict(Perfect=len(drums), Good=0, Miss=claps))
                    self.assertTrue(game.finished)
                    self.assertEqual(game.empty_strikes, 0)
                    self.assertEqual(len(detected), sum(2 if n['lane'] == 'B' else 1 for n in drums))
                    sounds = [name for name, _ in emitted]
                    self.assertEqual(len(sounds), sum(2 if n['lane']=='B' else 1 for n in drums) + claps + 1)
                    self.assertEqual(sounds.count('taiko_finish'), 1)
                    self.assertEqual(sounds.count('taiko_miss'), claps)
                    self.assertEqual(len(set(key for _, key in emitted)), len(emitted))

    def test_jittered_latency_and_repeated_ui_frames_do_not_duplicate(self):
        for difficulty in ('easy', 'normal', 'hard'):
            with self.subTest(difficulty=difficulty):
                game, _, emitted = replay(difficulty, 30, jitter=True, repeat_frames=True)
                drums = [n for n in chart(difficulty) if n['lane'] != 'C']
                claps = len(chart(difficulty)) - len(drums)
                self.assertEqual(game.counts['Perfect'], len(drums))
                self.assertEqual(game.counts['Miss'], claps)
                self.assertEqual(len(emitted), sum(2 if n['lane']=='B' else 1 for n in drums) + claps + 1)

    def test_300ms_pipeline_is_compensated_without_widening_hit_windows(self):
        game, _, _ = replay('hard', 15, delay=.30)
        self.assertEqual(game.counts['Perfect'], sum(n['lane'] != 'C' for n in chart('hard')))
        self.assertEqual(game.counts['Miss'], sum(n['lane'] == 'C' for n in chart('hard')))

    def test_relaxed_300ms_rhythm_error_scores_ok_without_changing_detected_strokes(self):
        strict,strict_detected,_=replay('easy',15,delay=.33,stroke_offset=.30)
        relaxed,detected,_=replay('easy',15,delay=.33,stroke_offset=.30,judgment='relaxed')
        self.assertEqual(strict.counts['Miss'],len(chart('easy')))
        self.assertEqual(strict.score,0)
        drums=sum(n['lane']!='C' for n in chart('easy'))
        claps=len(chart('easy'))-drums
        self.assertEqual(relaxed.counts,dict(Perfect=0,Good=0,Miss=claps,OK=drums))
        self.assertEqual([(e['hand'],e['timestamp']) for e in detected],
                         [(e['hand'],e['timestamp']) for e in strict_detected])
        self.assertAlmostEqual(relaxed.snapshot()['accuracy'],35.*drums/len(chart('easy')))

    def test_relaxed_profile_does_not_change_perfect_pose_replay_or_sound_count(self):
        game,detected,emitted=replay('hard',30,delay=.33,repeat_frames=True,judgment='relaxed')
        drums=[n for n in game.notes if n['lane']!='C']
        claps=len(game.notes)-len(drums)
        expected=sum(2 if n['lane']=='B' else 1 for n in drums)
        self.assertEqual(game.counts,dict(Perfect=len(drums),Good=0,Miss=claps,OK=0))
        self.assertEqual(len(detected),expected)
        self.assertEqual(len(emitted),expected+claps+1)
        self.assertEqual(len(set(key for _,key in emitted)),len(emitted))

    def test_hard_has_recovery_time_for_each_arm(self):
        for hand in ('L', 'R'):
            times = [n['time'] for n in chart('hard') if n['lane'] in (hand, 'B')]
            self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), 1.2 - 1e-8)

    def test_practice_windows_play_against_absolute_music_clock(self):
        for difficulty in ('easy', 'normal', 'hard'):
            for practice in ('intro', 'alternate', 'finale'):
                with self.subTest(difficulty=difficulty, practice=practice):
                    game, detected, emitted = replay(difficulty, 30, practice=practice)
                    self.assertTrue(game.finished)
                    self.assertGreater(len(game.notes), 0)
                    drums=[n for n in game.notes if n['lane']!='C']
                    claps=len(game.notes)-len(drums)
                    self.assertEqual(game.counts, dict(Perfect=len(drums), Good=0, Miss=claps))
                    self.assertEqual(len(detected), sum(2 if n['lane']=='B' else 1 for n in drums))
                    self.assertEqual(len(emitted), sum(2 if n['lane']=='B' else 1 for n in drums) + claps + 1)
                    self.assertTrue(all(n['time'] >= game.lead_in_until for n in game.notes))


class LifecycleGestureSafety(unittest.TestCase):
    def test_menu_and_tracking_loss_cannot_join_two_halves_of_a_stroke(self):
        for interruption in ('menu', 'missing', 'identity', 'stale'):
            with self.subTest(interruption=interruption):
                tracker = DrumInput()
                tracker.consume(pose(20., 1, .14, .14), 20.18)
                bad = pose(20.033, 2, .14, .14)
                if interruption == 'menu':
                    bad['suite'] = dict(active=True, mode='taiko', control_active=True)
                elif interruption == 'missing':
                    bad = None
                elif interruption == 'identity':
                    bad['pid'] = 261
                else:
                    bad['stale'] = True
                self.assertEqual(tracker.consume(bad, 20.213)['events'], [])
                next_pid = 261 if interruption == 'identity' else 260
                self.assertEqual(tracker.consume(pose(20.10, 3, .76, .76, next_pid), 20.28)['events'], [])
                tracker.consume(pose(20.30, 4, .14, .14, next_pid), 20.48)
                self.assertEqual(len(tracker.consume(pose(20.40, 5, .76, .76, next_pid), 20.58)['events']), 2)

    def test_frozen_transport_does_not_score_or_catch_up_old_poses(self):
        now = [100.]
        clock = SilentClock(now=lambda: now[0])
        session = Session(clock)
        tracker = DrumInput()
        session.resume()
        now[0] = 102.3
        session.tick(now[0])
        tracker.consume(pose(102.2, 1, .14, .94), now[0])
        session.pause()
        frozen = session.game.time
        now[0] = 112.3
        tracker.reset(minimum_stamp=now[0])
        late = tracker.consume(pose(112.15, 2, .76, .94), now[0])
        session.tick(now[0], late['events'])
        self.assertEqual(session.game.time, frozen)
        self.assertEqual(session.game.score, 0)
        self.assertEqual(session.game.counts['Miss'], 0)
        session.resume()
        now[0] += .05
        old = tracker.consume(pose(112.20, 3, .94, .94), now[0])
        session.tick(now[0], old['events'])
        self.assertEqual(session.game.score, 0)
        self.assertAlmostEqual(session.game.time, frozen + .05)
        session.close()

    def test_error_freezes_clock_and_missing_hand_never_infers_a_hit(self):
        now = [10.]
        clock = SilentClock(now=lambda: now[0])
        session = Session(clock)
        session.resume()
        tracker = DrumInput()
        tracker.consume(pose(12.2, 1, .14, .94), 12.38)
        now[0] = 12.4
        session.tick(now[0])
        clock.error = 'synthetic unplug'
        state = pose(12.4, 2, .76, .94)
        state['points17'][9][2] = .0
        value = tracker.consume(state, 12.58)
        now[0] = 12.58
        session.tick(now[0], value['events'])
        self.assertEqual(value['events'], [])
        self.assertFalse(session.running)
        self.assertEqual(session.game.score, 0)
        self.assertAlmostEqual(session.game.time, 2.4)
        session.close()


if __name__ == '__main__':
    unittest.main()
