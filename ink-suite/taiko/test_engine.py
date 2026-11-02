import unittest
from .engine import Game,GOOD,INPUT_GRACE
from .chart import chart,DURATION
from .audio_clock import SilentClock
from .session import Session
from .input import DrumInput


class Rules(unittest.TestCase):
    def game(self,lane='L',judgment='strict'):
        g=Game(notes=[dict(id=0,time=2.,lane=lane)],duration=4.,judgment=judgment);g.pause(False);return g

    def test_strict_default_retains_original_windows_and_count_contract(self):
        g=self.game();snap=g.snapshot()
        self.assertEqual(g.counts,dict(Perfect=0,Good=0,Miss=0))
        self.assertEqual(snap['judgment'],'strict')
        self.assertEqual(snap['judgment_windows_ms'],dict(Perfect=110.,Good=220.))
        self.assertEqual(snap['hit_window_ms'],220.)
        self.assertEqual(snap['simultaneous_ms'],120.)

    def test_relaxed_preserves_inner_grades_and_adds_bounded_ok(self):
        for error,grade in ((.110,'Perfect'),(.11001,'Good'),(.220,'Good'),
                            (.22001,'OK'),(.350,'OK'),(.35001,None)):
            for sign in (-1,1):
                with self.subTest(error=error,sign=sign):
                    g=self.game(judgment='relaxed')
                    self.assertEqual(g.strike('L',2+error*sign,'one'),grade)
                    self.assertEqual(g.strikes,1)
                    self.assertEqual(len(g.strike_feedback),1)
                    self.assertEqual(g.score,350 if grade=='OK' else 650 if grade=='Good' else 1000 if grade=='Perfect' else 0)
        snap=self.game(judgment='relaxed').snapshot()
        self.assertEqual(snap['judgment_label'],'体感友好')
        self.assertEqual(snap['judgment_windows_ms'],dict(Perfect=110.,Good=220.,OK=350.))
        self.assertEqual(snap['hit_window_ms'],350.)
        self.assertEqual(snap['simultaneous_ms'],220.)

    def test_relaxed_double_changes_only_simultaneity_and_outer_window(self):
        for gap,expected in ((.120,'Perfect'),(.150,'Perfect'),(.220,'Perfect'),(.22001,'Miss')):
            g=self.game('B',judgment='relaxed')
            self.assertEqual(g.strike('L',2-gap/2,'l'),'partial')
            self.assertEqual(g.strike('R',2+gap/2,'r'),expected)
            self.assertEqual(len(g.strike_feedback),2)
            self.assertEqual(sum(g.counts.values()),1)
        g=self.game('B',judgment='relaxed')
        g.strike('L',2.25,'l');self.assertEqual(g.strike('R',2.35,'r'),'OK')
        self.assertAlmostEqual(g.feedback[-1]['timing_ms'],350.)
        g=self.game('B');g.strike('L',1.925,'l')
        self.assertEqual(g.strike('R',2.075,'r'),'Miss')

    def test_relaxed_miss_settlement_uses_outer_window_with_same_grace(self):
        g=self.game(judgment='relaxed')
        self.assertEqual(g.input_grace,INPUT_GRACE)
        g.advance(2+GOOD+INPUT_GRACE+.01)
        self.assertEqual(g.counts['Miss'],0)
        g.advance(2+.350+INPUT_GRACE-.00001)
        self.assertEqual(g.counts['Miss'],0)
        g.advance(2+.350+INPUT_GRACE+.00001)
        self.assertEqual(g.counts['Miss'],1)
        self.assertIsNone(g.strike('L',2.30,'cannot_restore_miss'))
        self.assertEqual(g.counts['OK'],0)

    def test_relaxed_empty_wrong_hand_keeps_feedback_separate_from_score(self):
        g=self.game(judgment='relaxed')
        self.assertIsNone(g.strike('R',2.30,'wrong'))
        self.assertEqual(g.last_strike_diagnostic['reason'],'wrong_hand')
        self.assertIsNone(g.strike('L',2.36,'late'))
        self.assertEqual(g.last_strike_diagnostic['reason'],'late')
        self.assertEqual(g.score,0);self.assertEqual(g.strikes,2)
        self.assertEqual(len(g.feedback),0);self.assertEqual(len(g.strike_feedback),2)

    def test_unknown_judgment_is_rejected(self):
        for invalid in ('easy','RELAXED','',None,[]):
            with self.assertRaises(ValueError):
                self.game(judgment=invalid)

    def test_early_perfect_and_late_good(self):
        for offset,expected in ((-.10,'Perfect'),(.10,'Perfect'),(-.21,'Good'),(.21,'Good')):
            g=self.game();self.assertEqual(g.strike('L',2+offset,'one'),expected)

    def test_outside_window_never_scores(self):
        for at in (0.,1.77,2.23,3.):
            g=self.game();self.assertIsNone(g.strike('L',at,'one'));self.assertEqual(g.score,0)

    def test_wrong_hand_and_spam(self):
        g=self.game();self.assertIsNone(g.strike('R',2.,'one'))
        self.assertEqual(g.strike('L',2.,'one'),'Perfect')
        for i in range(100):g.strike('L',2.,str(i))
        self.assertEqual(g.counts['Perfect'],1);self.assertEqual(g.score,1000)

    def test_double_requires_distinct_hands(self):
        g=self.game('B');self.assertEqual(g.strike('L',2.,'1'),'partial')
        self.assertIsNone(g.strike('L',2.01,'2'));self.assertEqual(g.score,0)
        self.assertEqual(g.strike('R',2.07,'3'),'Perfect');self.assertEqual(g.combo,1)

    def test_double_too_apart_misses(self):
        g=self.game('B');g.strike('L',1.90,'l');self.assertEqual(g.strike('R',2.05,'r'),'Miss')
        self.assertEqual(g.score,0)

    def test_one_hand_double_times_out_once(self):
        g=self.game('B');g.strike('L',2.,'l');g.advance(2+GOOD+INPUT_GRACE+.01);g.advance(3.)
        self.assertEqual(g.counts['Miss'],1)

    def test_delayed_observation_grace_not_window_extension(self):
        g=self.game();g.advance(2.30);self.assertEqual(g.strike('L',2.05,'late-receive'),'Perfect')
        g=self.game();g.advance(2.30);self.assertIsNone(g.strike('L',2.30,'actually-late'))

    def test_pause_clears_partial_and_blocks(self):
        g=self.game('B');g.strike('L',2.,'l');g.pause();g.advance(3.);g.strike('R',2.,'r')
        self.assertEqual(g.time,0.);self.assertEqual(g.score,0)
        g.pause(False);self.assertEqual(g.strike('R',2.,'fresh'),'partial')

    def test_full_chart_exact_replay(self):
        for difficulty in ('easy','normal'):
            g=Game(difficulty);g.pause(False)
            for note in chart(difficulty):
                g.advance(note['time'])
                for hand in ('L','R') if note['lane']=='B' else (note['lane'],):
                    g.strike(hand,note['time'],(note['id'],hand))
            g.advance(DURATION)
            self.assertTrue(g.finished);self.assertEqual(g.counts['Perfect'],len(chart(difficulty)))
            self.assertEqual(g.counts['Miss'],0)

    def test_blank_round_still_finishes(self):
        g=Game();g.pause(False);g.advance(DURATION)
        self.assertTrue(g.finished);self.assertEqual(g.counts['Miss'],len(chart()))

    def test_backward_time_rejected_and_id_memory_bounded(self):
        g=self.game();g.advance(1.)
        with self.assertRaises(ValueError):g.advance(.5)
        for i in range(10000):g.strike('L',0.,i)
        self.assertLessEqual(len(g.seen),512)


class ClockAndSession(unittest.TestCase):
    def setUp(self):
        self.now=[10.];self.clock=SilentClock(now=lambda:self.now[0])
        self.session=Session(self.clock);self.session.resume()

    def test_master_clock_not_tick_dt(self):
        self.now[0]+=2.4;self.session.tick(self.now[0],[dict(hand='L',timestamp=self.now[0],id=1)])
        self.assertAlmostEqual(self.session.game.time,2.4);self.assertEqual(self.session.game.counts['Perfect'],1)

    def test_pose_age_and_calibration(self):
        self.now[0]+=2.55
        self.session.tick(self.now[0],[dict(hand='L',timestamp=self.now[0]-.15,id=1)])
        self.assertEqual(self.session.game.counts['Perfect'],1)

    def test_three_hundred_ms_pipeline_delay(self):
        self.now[0]+=2.65;self.session.tick(self.now[0])
        self.assertEqual(self.session.game.counts['Miss'],0)
        self.now[0]+=.05
        self.session.tick(self.now[0],[dict(hand='L',timestamp=self.now[0]-.30,id='delayed')])
        self.assertEqual(self.session.game.counts['Perfect'],1)

    def test_pause_no_catchup_and_restart(self):
        self.now[0]+=1;self.session.tick(self.now[0]);self.session.pause();self.now[0]+=100
        self.session.tick(self.now[0]);self.assertEqual(self.session.game.time,1.)
        self.session.resume();self.now[0]+=.1;self.session.tick(self.now[0]);self.assertAlmostEqual(self.session.game.time,1.1)
        self.assertTrue(self.session.restart());self.assertEqual(self.session.game.time,0.);self.assertEqual(self.clock.position(),0.)

    def test_error_freezes_and_explicit_replace_recovers(self):
        self.now[0]+=1.;self.session.tick(self.now[0]);self.clock.error='device disconnected'
        self.now[0]+=1.;self.session.tick(self.now[0]);self.assertFalse(self.session.running)
        self.assertEqual(self.session.game.time,1.)
        clock=SilentClock(now=lambda:self.now[0]);self.assertTrue(self.session.replace_clock(clock))
        self.session.resume();self.now[0]+=.3;self.session.tick(self.now[0]);self.assertAlmostEqual(self.session.game.time,1.3)

    def test_pause_period_source_cannot_score_after_resume(self):
        self.now[0]=12.5;self.session.tick(self.now[0]);self.session.pause()
        self.now[0]=20.;self.session.resume();self.now[0]=20.04
        self.session.tick(self.now[0],[dict(hand='L',timestamp=19.86,id='paused-source')])
        self.assertEqual(self.session.game.score,0)

    def test_positive_calibration_extends_settlement_only(self):
        now=[10.];clock=SilentClock(now=lambda:now[0]);session=Session(clock,offset_ms=500);session.resume()
        now[0]=13.1;session.tick(now[0]);self.assertEqual(session.game.counts['Miss'],0)
        now[0]=13.2;session.tick(now[0],[dict(hand='L',timestamp=12.9,id='delayed-calibrated')])
        self.assertEqual(session.game.counts['Perfect'],1)


class InputFeedbackDiagnostics(unittest.TestCase):
    def session(self,lane='L'):
        self.now=[10.];clock=SilentClock(now=lambda:self.now[0]);session=Session(clock)
        session.game=Game(notes=[dict(id=0,time=2.,lane=lane)],duration=4.)
        session.resume();return session

    def test_empty_and_partial_each_have_neutral_feedback_without_score(self):
        s=self.session();self.now[0]=11.
        s.tick(11.,[dict(hand='L',timestamp=11.,id='empty',path='reversal')])
        self.assertEqual(s.game.score,0);self.assertEqual(s.game.empty_strikes,1)
        self.assertEqual(s.game.strike_feedback[-1]['outcome'],'empty')
        row=s.input_snapshot()['recent'][-1]
        self.assertEqual(row['reason'],'early');self.assertEqual(row['path'],'reversal')
        self.assertIsNone(row['source_stamp']);self.assertIsNone(row['source_sequence'])
        self.assertEqual(row['nearest_same_hand']['delta_ms'],-1000.)
        s=self.session('B');self.now[0]=12.
        s.tick(12.,[dict(hand='L',timestamp=12.,id='left')])
        self.assertEqual(s.game.score,0);self.assertEqual(s.game.strike_feedback[-1]['outcome'],'partial')
        self.assertEqual(s.input_snapshot()['recent'][-1]['reason'],'waiting_other_hand')

    def test_delayed_judgement_preserves_score_time_and_starts_full_feedback(self):
        s=self.session();self.now[0]=12.33
        s.tick(12.33,[dict(hand='L',timestamp=12.,id='one',path='line',source_stamp=12.04,
                         previous_source_stamp=11.98,source_sequence=17)])
        grade=s.game.feedback[-1];pulse=s.game.strike_feedback[-1]
        self.assertEqual(grade['grade'],'Perfect');self.assertAlmostEqual(grade['time'],2.)
        self.assertAlmostEqual(grade['display_time'],2.33)
        self.assertAlmostEqual(pulse['display_time'],s.game.time)
        row=s.input_snapshot()['recent'][-1]
        self.assertAlmostEqual(row['event_age'],.33);self.assertAlmostEqual(row['judge_at'],2.)
        self.assertEqual(row['source_stamp'],12.04);self.assertEqual(row['source_sequence'],17)

    def test_relaxed_late_ok_uses_original_compensated_time_and_feedback_clock(self):
        now=[10.];s=Session(SilentClock(now=lambda:now[0]),judgment='relaxed')
        s.game=Game(notes=[dict(id=0,time=2.,lane='L')],duration=4.,judgment='relaxed')
        s.resume();now[0]=12.63
        event=dict(hand='L',timestamp=12.30,id='body_downstroke',source_stamp=12.35)
        s.tick(now[0],[event,event])
        self.assertEqual(s.game.counts['OK'],1);self.assertEqual(s.game.score,350)
        self.assertEqual(len(s.game.strike_feedback),1)
        self.assertAlmostEqual(s.game.feedback[-1]['time'],2.30)
        self.assertAlmostEqual(s.game.feedback[-1]['display_time'],2.63)
        self.assertEqual(s.input_diagnostics[-2]['judgment'],'relaxed')
        self.assertEqual(s.input_diagnostics[-2]['hit_window_ms'],350.)
        self.assertAlmostEqual(s.input_diagnostics[-2]['event_age'],.33)
        self.assertEqual(s.input_diagnostics[-1]['reason'],'duplicate')

    def test_expired_epoch_duplicate_and_paused_never_make_feedback(self):
        s=self.session();self.now[0]=12.33
        good=dict(hand='L',timestamp=12.,id='same')
        s.tick(12.33,[good,good,dict(hand='L',timestamp=11.8,id='expired')])
        self.assertEqual(len(s.game.strike_feedback),1);self.assertEqual(s.game.score,1000)
        s.resume_epoch=12.3
        s.tick(12.33,[dict(hand='L',timestamp=12.2,id='old_epoch')])
        s.pause();s.tick(12.33,[dict(hand='L',timestamp=12.33,id='paused')])
        self.assertEqual(len(s.game.strike_feedback),1);self.assertEqual(s.game.score,1000)
        counts=s.input_snapshot()['counts']
        for reason in ('Perfect','duplicate','expired','before_epoch','paused'):
            self.assertEqual(counts[reason],1)

    def test_interpolated_expired_event_is_diagnosed_not_retimed(self):
        s=self.session();self.now[0]=12.46
        s.tick(12.46,[dict(hand='L',timestamp=12.,source_stamp=12.06,id='old_crossing')])
        self.assertEqual(s.game.score,0);self.assertEqual(len(s.game.strike_feedback),0)
        row=s.input_snapshot()['recent'][-1]
        self.assertEqual(row['reason'],'expired');self.assertAlmostEqual(row['event_age'],.46)

    def test_diagnostics_distinguish_wrong_hand_and_double_timing(self):
        s=self.session();self.now[0]=12.
        s.tick(12.,[dict(hand='R',timestamp=12.,id=1)])
        self.assertEqual(s.input_snapshot()['recent'][-1]['reason'],'wrong_hand')
        s=self.session('B');self.now[0]=12.05
        s.tick(12.05,[dict(hand='L',timestamp=11.90,id=1),dict(hand='R',timestamp=12.05,id=2)])
        row=s.input_snapshot()['recent'][-1]
        self.assertEqual(row['reason'],'double_hand_gap');self.assertAlmostEqual(row['double_gap_ms'],150.)
        self.assertEqual(s.game.counts['Miss'],1);self.assertEqual(len(s.game.strike_feedback),2)

    def test_diagnostics_and_pulses_stay_bounded(self):
        s=self.session();self.now[0]=11.
        for i in range(200):s.tick(11.,[dict(hand='L',timestamp=11.,id=i)])
        self.assertEqual(len(s.input_diagnostics),64);self.assertEqual(len(s.game.strike_feedback),64)
        self.assertEqual(s.input_counts['empty'],200);self.assertEqual(s.game.score,0)

    def test_calibration_outside_song_is_neutral_feedback_without_score(self):
        s=self.session();s.set_offset_ms(500);self.now[0]=10.1
        s.tick(10.1,[dict(hand='L',timestamp=10.1,id='real_early')])
        self.assertEqual(s.game.score,0);self.assertEqual(len(s.game.strike_feedback),1)
        self.assertEqual(s.input_snapshot()['recent'][-1]['reason'],'outside_interval')
        self.assertAlmostEqual(s.input_snapshot()['recent'][-1]['judge_at'],-.4)


class WholePoseReplay(unittest.TestCase):
    def test_original_pose_time_drives_both_charts_at_three_rates(self):
        # Independent physical wrist trajectory, not direct score injection.
        # Every frame arrives 180 ms after observation; no audio/device access.
        # These downstrokes must hit L/R/B while leaving independent C unhit.
        for difficulty in ('easy','normal'):
            notes=chart(difficulty)
            for fps in (15,30,60):
                now=[10.];clock=SilentClock(now=lambda:now[0]);session=Session(clock,difficulty);session.resume()
                tracker=DrumInput()
                for index in range(int(72*fps)+1):
                    t=index/fps;now[0]=10+t;source=t-.18
                    points=[[.5,.5,0.] for _ in range(17)]
                    points[5]=[.60,.30,.9];points[6]=[.40,.30,.9]
                    for hand,j,x in (('L',9,.63),('R',10,.37)):
                        y=1.
                        for n in notes:
                            if n['lane'] not in (hand,'B'):continue
                            relative=source-n['time']
                            if -.55<=relative<-.35:y=1.-.85*(relative+.55)/.20
                            elif -.35<=relative<-.12:y=.15
                            elif -.12<=relative<.04:y=.15+(relative+.12)*(.53/.12)
                        points[j]=[x,.30+y*(.2*16/9),.9]
                    value=tracker.consume(dict(pid=260,sequence=index,timestamp_seconds=10+source,points17=points),now[0])
                    session.tick(now[0],value['events'])
                drums=sum(n['lane']!='C' for n in notes)
                self.assertEqual(session.game.counts['Perfect'],drums,(difficulty,fps,session.game.counts))
                self.assertEqual(session.game.counts['Miss'],len(notes)-drums)
                self.assertTrue(session.game.finished)


if __name__=='__main__':unittest.main()
