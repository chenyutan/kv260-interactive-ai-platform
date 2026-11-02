"""Output-failure warnings freeze rhythm; no physical audio device is opened."""
from pathlib import Path
import tempfile
import time
import unittest
import wave
from types import SimpleNamespace as NS

from .audio_clock import GstClock
from .session import Session


class ReadyLifecycleTests(unittest.TestCase):
    """Native transport semantics using a deterministic backend, no output."""
    def clock(self):
        now=[0.]
        G=NS(SECOND=1000000000,State=NS(READY='ready',PAUSED='paused',
             PLAYING='playing',NULL='null',VOID_PENDING='void'),
             StateChangeReturn=NS(SUCCESS='success',ASYNC='async',FAILURE='failure'),
             Format=NS(TIME='time'),SeekFlags=NS(FLUSH=1,ACCURATE=2))
        class Pipeline:
            state='ready';pending='void';pos=0.;fail_seek=False;query_ok=True
            def __init__(self):self.transitions=[];self.seeks=[];self.timeouts=[]
            def set_state(self,state):
                self.transitions.append(state);self.state=state;self.pending='void';return 'success'
            def get_state(self,timeout):
                self.timeouts.append(timeout);return ('success',self.state,self.pending)
            def query_position(self,_):return (self.query_ok and self.state!='ready',int(self.pos*G.SECOND))
            def seek_simple(self,fmt,flags,position):
                self.seeks.append(position/G.SECOND)
                if self.fail_seek:return False
                self.pos=position/G.SECOND;return True
            def get_clock(self):return NS(get_name=lambda:'GstPulseSinkClock')
        c=GstClock.__new__(GstClock);c.Gst=G;c.now=lambda:now[0]
        c.pipeline=Pipeline();c.poll=lambda:not c.error
        c.error=None;c.eos=False;c.playing=False;c.last=0.;c.last_advance=0.
        c.closed=False;c.requested='paused';c.startup_at=None;c.started=False
        c.query_ok=None;c.prepare_seconds=0.;c.prepare_position=None
        return c,now

    def test_long_initial_idle_and_pause_release_then_reseek_real_position(self):
        c,now=self.clock();now[0]=60.
        self.assertTrue(c.play());self.assertEqual(c.pipeline.seeks,[0.])
        c.pipeline.pos=4.25;self.assertEqual(c.position(),4.25)
        c.pause();self.assertEqual(c.pipeline.state,'ready')
        now[0]+=10.;c.pipeline.pos=0.
        self.assertEqual(c.position(),4.25)
        c.pause();self.assertEqual(c.last,4.25)
        self.assertTrue(c.play());self.assertEqual(c.pipeline.seeks,[0.,4.25])
        self.assertFalse(c.error)

    def test_nonzero_seek_verifies_native_target_releases_and_reapplies_after_idle(self):
        c,now=self.clock();self.assertTrue(c.seek(28.8))
        self.assertEqual(c.pipeline.state,'ready');self.assertEqual(c.position(),28.8)
        now[0]=10.;c.pipeline.pos=0.
        self.assertTrue(c.play());self.assertEqual(c.pipeline.seeks,[28.8,28.8])
        self.assertEqual(c.prepare_position,28.8)

    def test_failed_or_unconfirmed_seek_does_not_ack_or_play(self):
        for failure in ('seek','query','position'):
            with self.subTest(failure=failure):
                c,_=self.clock()
                if failure=='seek':c.pipeline.fail_seek=True
                elif failure=='query':c.pipeline.query_ok=False
                else:
                    native=c.pipeline.seek_simple
                    def wrong(*args):native(*args);c.pipeline.pos+=.5;return True
                    c.pipeline.seek_simple=wrong
                self.assertFalse(c.seek(10.));self.assertTrue(c.error)
                self.assertEqual(c.last,0.);self.assertEqual(c.pipeline.state,'ready')
                self.assertFalse(c.play());self.assertFalse(c.playing)

    def test_preroll_and_seek_share_total_two_second_wait_budget(self):
        c,now=self.clock();native=c.pipeline.get_state
        def wait(timeout):
            result=native(timeout);now[0]+=.8;return result
        c.pipeline.get_state=wait
        self.assertTrue(c.seek(10.))
        self.assertEqual(c.pipeline.timeouts[:2],[2*c.Gst.SECOND,1200000000])
        self.assertAlmostEqual(c.prepare_seconds,1.6)

    def test_running_stall_freezes_without_relaxing_two_second_watchdog(self):
        c,now=self.clock();self.assertTrue(c.play())
        now[0]=2.1;c.position()
        self.assertIn('停止推进',c.error);self.assertFalse(c.playing)
        self.assertEqual(c.pipeline.state,'ready')

    def test_async_start_does_not_ack_or_allow_session_scoring(self):
        c,now=self.clock();native=c.pipeline.get_state
        def pending(timeout):
            if c.pipeline.state=='playing':
                now[0]+=timeout/c.Gst.SECOND;return ('async','paused','playing')
            return native(timeout)
        c.pipeline.get_state=pending;s=Session(c)
        self.assertFalse(s.resume());s.tick(now[0],[dict(hand='L',timestamp=now[0],id=1)])
        self.assertFalse(s.running);self.assertEqual(s.game.time,0.);self.assertEqual(s.game.score,0)
        self.assertIn('未就绪',c.error)
        self.assertLessEqual(c.startup_seconds,2.)

    def test_seek_after_eos_resets_target_and_close_cannot_be_resumed(self):
        c,_=self.clock();c.last=72.;c.eos=True
        self.assertTrue(c.seek(0.));self.assertFalse(c.eos);self.assertEqual(c.last,0.)
        c.close();self.assertFalse(c.play());self.assertFalse(c.seek(1.))
        self.assertEqual(c.pipeline.state,'null')

    def test_transport_status_is_nonblocking_and_has_no_position_side_effect(self):
        c,_=self.clock();c.pipeline.query_position=lambda _:self.fail('Must not query position')
        value=c.transport_status();self.assertEqual(c.pipeline.timeouts,[0])
        self.assertEqual(value['requested'],'paused');self.assertEqual(value['last_position'],0.)
        self.assertEqual(value['clock_name'],'GstPulseSinkClock')

try:
    import gi
    gi.require_version('Gst','1.0')
    from gi.repository import Gst,GLib
    Gst.init(None)
except (ImportError,ValueError):
    Gst=GLib=None


@unittest.skipIf(Gst is None,'Requires GStreamer GI; all outputs are fakesinks')
class OutputWarningTests(unittest.TestCase):
    def clock(self,sink='autoaudiosink'):
        # Exercise native Gst messages/ancestry and production poll/Session
        # methods without constructing autoaudiosink or opening a device.
        clock=GstClock.__new__(GstClock)
        clock.Gst=Gst;clock.sink=sink;clock.now=time.monotonic
        clock.error=None;clock.eos=False;clock.playing=True
        clock.last=3.;clock.last_advance=time.monotonic()
        clock.pipeline=Gst.Pipeline.new(None)
        clock.output=Gst.Bin.new('test-audio-output')
        child=Gst.ElementFactory.make('fakesink','test-output-child')
        self.assertIsNotNone(child)
        clock.output.add(child);clock.pipeline.add(clock.output)
        clock.bus=clock.pipeline.get_bus()
        self.addCleanup(clock.close)
        return clock,child

    def warning(self,clock,source,domain,code,text='test warning'):
        error=GLib.Error.new_literal(domain.quark(),text,code)
        self.assertTrue(clock.bus.post(Gst.Message.new_warning(source,error,'injected test')))

    def test_output_open_and_dummy_fallback_freeze_session_without_scoring(self):
        for source_child in (False,True):
            for code in (Gst.ResourceError.NOT_FOUND,Gst.ResourceError.BUSY,
                         Gst.ResourceError.OPEN_WRITE,Gst.ResourceError.OPEN_READ_WRITE,
                         Gst.ResourceError.WRITE):
                with self.subTest(child=source_child,code=code):
                    clock,child=self.clock()
                    session=Session(clock);session.running=True;session.game.pause(False)
                    session.resume_epoch=0.
                    before=session.game.snapshot()
                    self.warning(clock,child if source_child else clock.output,
                                 Gst.ResourceError,code,'Output unavailable')
                    session.tick(time.monotonic(),[dict(hand='L',timestamp=time.monotonic(),id=1)])
                    self.assertFalse(session.running);self.assertTrue(session.game.paused)
                    self.assertFalse(clock.playing);self.assertEqual(session.error,'Output unavailable')
                    self.assertEqual(session.game.time,before['time'])
                    self.assertEqual(session.game.score,before['score'])
                    self.assertEqual(clock.position(),3.)
                    self.assertFalse(session.resume())

    def test_queued_output_warning_blocks_play_before_first_tick(self):
        clock,_=self.clock()
        self.warning(clock,clock.output,Gst.ResourceError,Gst.ResourceError.NOT_FOUND)
        self.assertFalse(clock.play());self.assertFalse(clock.playing)

    def test_unrelated_and_benign_warnings_keep_clock_available(self):
        clock,child=self.clock()
        foreign=Gst.ElementFactory.make('fakesrc','unrelated-file-source')
        clock.pipeline.add(foreign)
        cases=((child,Gst.StreamError,Gst.StreamError.DECODE),
               (child,Gst.CoreError,Gst.CoreError.CLOCK),
               (child,Gst.ResourceError,Gst.ResourceError.SETTINGS),
               (foreign,Gst.ResourceError,Gst.ResourceError.NOT_FOUND),
               (clock.pipeline,Gst.ResourceError,Gst.ResourceError.OPEN_WRITE))
        for source,domain,code in cases:
            self.warning(clock,source,domain,code)
        self.assertTrue(clock.poll());self.assertIsNone(clock.error)
        self.assertTrue(clock.playing)
        self.assertIsNone(clock.bus.pop_filtered(Gst.MessageType.WARNING))

    def test_explicit_fakesink_ignores_device_warning_but_errors_still_fail(self):
        clock,child=self.clock('fakesink')
        self.warning(clock,child,Gst.ResourceError,Gst.ResourceError.OPEN_WRITE)
        self.assertTrue(clock.poll());self.assertTrue(clock.playing)
        error=GLib.Error.new_literal(Gst.StreamError.quark(),'Broken audio stream',Gst.StreamError.DECODE)
        self.assertTrue(clock.bus.post(Gst.Message.new_error(child,error,'injected test')))
        self.assertFalse(clock.poll());self.assertEqual(clock.error,'Broken audio stream')
        self.assertFalse(clock.playing)

    def test_real_wave_fakesink_still_advances_after_benign_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'silence.wav'
            with wave.open(str(path),'wb') as output:
                output.setparams((2,2,48000,0,'NONE','not compressed'))
                output.writeframes(b'\0'*(48000*4))
            clock=GstClock(path,'fakesink')
            try:
                self.assertTrue(clock.seek(0.))
                self.warning(clock,clock.output,Gst.CoreError,Gst.CoreError.CLOCK)
                self.assertTrue(clock.play())
                deadline=time.monotonic()+2.
                while clock.position()<.04 and not clock.error and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertIsNone(clock.error);self.assertGreaterEqual(clock.position(),.04)
                clock.pause();self.assertFalse(clock.playing)
            finally:clock.close()


if __name__=='__main__':unittest.main()
