"""Real GTK + silent GStreamer audio lifecycle; Xvfb, no camera or speakers."""
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from audio.integration import ExperienceAudio
from . import test_main as fixture


@unittest.skipUnless(fixture.GTK_AVAILABLE,'Requires GTK under isolated Xvfb')
class SwordAudioPanelTests(unittest.TestCase):
    def setUp(self):
        self.ui=fixture.ui
        self.network=patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network in offline audio test'))
        self.network_mock=self.network.start();self.addCleanup(self.network.stop)
        self.emit=patch.object(self.ui,'emit');self.emit.start();self.addCleanup(self.emit.stop)
        args=NS(embed_socket=1,offline_preview=True,base='http://127.0.0.1:1',entry_delay=0.,fullscreen=False)
        self.panel=self.ui.SwordPanel(args)
        self.window=self.ui.Gtk.Window();self.window.add(self.panel);self.window.show_all()
        self.ui.GLib.source_remove(self.panel.timer);self.panel.timer=None
        self.assertFalse(self.panel.sound.director.status()['worker_alive'])
        initial=self.panel.sound.director.status()
        self.assertEqual(initial['sink'],'pulsesink')
        self.assertTrue(initial['live_timestamps'])
        self.assertEqual(initial['stream_name'],'InkSuite sword audio')
        self.panel.sound.close()
        self.panel.sound=ExperienceAudio('sword',sink='fakesink');self.panel.sound.bind(self.panel)

    def tearDown(self):
        self.panel.operation('shutdown');self.window.destroy()
        self.assertTrue(self.panel.sound.director.wait_closed())
        self.network_mock.assert_not_called()

    def wait(self,state):
        end=time.monotonic()+8.
        while time.monotonic()<end:
            status=self.panel.sound.director.status()
            if status['state']==state:return status
            if status['error']:self.fail(str(status))
            time.sleep(.005)
        self.fail(str(self.panel.sound.director.status()))

    def play(self):
        p=self.panel;p.operation('resume');p.entry_until=0.;p.recovery_needed=False
        p.game.start(0);p.game.pause(False);p.sound.restart();self.wait('playing')

    def frame(self):
        self.panel.last_tick=time.monotonic()-.025
        self.assertTrue(self.panel.tick())

    def test_actual_tick_shot_then_host_pause_and_resume(self):
        self.play();p=self.panel;before=p.sound.director.status()['accepted']
        self.frame()
        self.assertGreater(p.sound.director.status()['accepted'],before)
        p.operation('pause');paused=self.wait('paused');snapshot=p.game.snapshot()
        self.frame();self.assertEqual(snapshot,p.game.snapshot())
        self.assertEqual(paused['queued'],0);self.assertEqual(paused['voices'],0)
        p.operation('resume');self.frame()
        self.assertTrue(p.game.paused);self.wait('paused')  # entry countdown owns resume
        p.entry_until=0.;p.recovery_needed=False;p.recovery_until=0.;self.frame();self.wait('playing')

    def test_tracking_loss_clears_output_and_fresh_recovery_resumes(self):
        self.play();self.frame();p=self.panel
        with patch.object(p.tracker,'update',return_value=dict(valid=False,status='lost',source_key=None)):
            self.frame()
        self.assertTrue(p.game.paused);self.wait('paused')
        p.recovery_needed=False;p.recovery_until=0.;self.frame();self.wait('playing')

    def test_sound_actions_and_restart_do_not_resume_behind_menu(self):
        self.play();p=self.panel;p.operation('pause');self.wait('paused')
        sent=[];before=p.game.snapshot()
        p.execute_action('audio_music_off',lambda ok,*a,**kw:sent.append(ok))
        p.execute_action('audio_mute',lambda ok,*a,**kw:sent.append(ok))
        self.assertEqual(before,p.game.snapshot());self.assertEqual(sent,[True,True])
        self.assertEqual(p.sound.director.status()['levels']['music'],0.)
        self.assertTrue(p.sound.director.status()['muted'])
        p.execute_action('restart',lambda ok,*a,**kw:sent.append(ok))
        self.wait('stopped');self.assertTrue(p.game.paused)
        p.operation('resume');self.frame();self.wait('paused')
        self.assertEqual(p.sound.director.status()['voices'],0)

if __name__=='__main__':unittest.main()
