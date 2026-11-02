"""Actual Suite IPC behavior during source gaps; no GTK, camera or board IO."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import test_action_host as fixture
from startup_readiness import PipelineReadiness


class HostGameSourceTests(unittest.TestCase):
    def setUp(self):
        base=fixture.ActionHostTests();base.setUp()
        self.s,self.clock=base.s,base.clock
        s=self.s
        s.mode='fruit';s.control_mode='body';s.menu_open=False;s.menu_level='main'
        s.child_paused=True;s.health_active=False;s.visitor_busy=False
        s.device_dialog=s.device_view=s.display_dialog=None
        s.pipeline=PipelineReadiness();s.args.offline_preview=False
        s.lock=threading.Lock();s.pending_state=False;s.serial=0
        s.suppress_gesture_navigation=lambda now:False
        s.gamepad_host=None
        self.seq=0

    def deliver(self,at=None,missing=False,**changes):
        if at is not None:self.clock.now=at
        if missing:self.s.latest=None
        else:
            self.seq+=1
            s=dict(pid=123,sequence=self.seq,pose_count=self.seq,
                timestamp_seconds=self.clock.now,phase='idle',points17=fixture.pose(),
                suite=dict(active=True,mode=self.s.backend_mode(self.s.mode),menu=False,
                           input_mode='body',open_serial=0,control_active=False))
            s.update(changes);self.s.latest=s
        self.assertFalse(self.s.deliver_state())

    def start(self,mode='fruit'):
        self.s.mode=mode
        for at in (100.,100.05,100.10):self.deliver(at)
        self.assertFalse(self.s.child_paused)
        self.assertEqual(self.ops().count('resume'),1)
        self.s.child.stdin.seek(0);self.s.child.stdin.truncate(0)

    def ops(self):return [m['op'] for m in self.s.child.messages()]

    def finish_resync(self):
        if self.s.commands:
            _,callback=self.s.commands[-1]
            if callback:callback(dict(accepted=True),None)

    def test_fresh_frames_without_any_joints_never_pause_each_game(self):
        for mode in ('fight','fruit','sword','taiko','runner','platformer','kart','pal'):
            with self.subTest(mode=mode):
                self.setUp();self.start(mode)
                for tenth in range(1,61):
                    self.deliver(100.1+tenth/10,points17=None,available=False,phase='unknown')
                self.assertFalse(self.s.child_paused)
                self.assertNotIn('pause',self.ops())
                self.assertNotIn('resume',self.ops())

    def test_two_second_http_gap_does_not_pause_or_resend_resume(self):
        self.start()
        for delta in (.2,.8,1.2,2.):self.deliver(100.1+delta,missing=True)
        self.assertFalse(self.s.child_paused);self.assertEqual(self.ops(),[])
        for at in (102.15,102.2,102.25):
            self.deliver(at);self.finish_resync()
        self.assertFalse(self.s.child_paused);self.assertEqual(self.ops(),[])

    def test_true_source_stall_exceeding_grace_pauses_once(self):
        self.start()
        self.deliver(102.59,missing=True)
        self.assertEqual(self.ops(),[])
        self.deliver(102.61,missing=True)
        self.assertTrue(self.s.child_paused);self.assertEqual(self.ops(),['pause'])
        self.deliver(103.,missing=True)
        self.assertEqual(self.ops(),['pause'])

    def test_cached_packets_cannot_extend_gameplay_grace(self):
        self.start();old=dict(self.s.latest)
        for at in (100.4,101.,102.,102.7):
            self.deliver(at,**{k:old[k] for k in ('sequence','pose_count','timestamp_seconds')})
        self.assertEqual(self.ops(),['pause'])

    def test_future_packets_cannot_extend_gameplay_grace(self):
        self.start()
        for at in (100.4,101.,102.,102.7):self.deliver(at,timestamp_seconds=at+10)
        self.assertEqual(self.ops(),['pause'])

    def test_confirmed_menu_pauses_immediately_during_gap(self):
        self.start();self.deliver(100.9,missing=True)
        self.s.open_menu(main=True)
        self.assertTrue(self.s.menu_open);self.assertTrue(self.s.child_paused)
        self.assertIn('pause',self.ops())
        before=list(self.ops());self.deliver(101.,missing=True)
        self.assertEqual(self.ops(),before)

    def test_explicit_pause_removes_the_right_to_resume_without_ready_source(self):
        self.start();self.s.send_child('pause')
        self.s.pipeline.ready=False
        self.s.send_child('resume')
        self.assertTrue(self.s.child_paused);self.assertEqual(self.ops(),['pause'])

    def test_new_game_cannot_be_started_using_source_grace(self):
        self.s.update_game_source(None,self.clock.now)
        self.s.pipeline.ready=False
        self.s.send_child('resume')
        self.assertTrue(self.s.child_paused)
        self.assertNotIn('resume',self.ops())

    def test_replacement_child_does_not_inherit_old_game_continuation(self):
        self.start();old=self.s.child
        self.s.child=fixture.Child();self.s.child_paused=False;self.s.pipeline.ready=False
        self.s.send_child('resume')
        self.assertEqual(self.ops(),['pause']);self.assertEqual(old.messages(),[])

    def test_generation_change_does_not_inherit_continuation(self):
        self.start();self.s.generation+=1;self.s.pipeline.ready=False
        self.s.send_child('resume')
        self.assertEqual(self.ops(),['pause'])

    def test_device_dialog_pauses_even_inside_short_source_gap(self):
        self.start();self.s.device_dialog=object();self.s.pipeline.ready=False
        self.s.wait_for_input()
        self.assertEqual(self.ops(),['pause'])

    def test_hand_controller_disconnect_still_pauses(self):
        self.start();self.s.control_mode='gamepad'
        self.s.gamepad_host=SimpleNamespace(connected=False)
        self.deliver(100.2)
        self.assertEqual(self.ops(),['pause'])

    def test_gamepad_connection_does_not_resume_an_open_menu(self):
        self.start();self.s.control_mode='gamepad'
        self.s.gamepad_host=SimpleNamespace(connected=True);self.s.menu_open=True
        self.deliver(100.2)
        self.assertEqual(self.ops(),['pause'])

    def test_tentative_circle_does_not_pause_fight(self):
        self.start('fight')
        self.deliver(100.2,suite=dict(active=True,mode='fight',menu=False,
            input_mode='body',open_serial=0,control_active=True))
        self.assertEqual(self.ops(),[])

    def test_health_and_ink_cannot_use_game_continuation(self):
        for mode,health in (('ink',False),('fruit',True)):
            with self.subTest(mode=mode,health=health):
                self.setUp();self.start()
                self.s.mode=mode;self.s.health_active=health;self.s.pipeline.ready=False
                self.s.wait_for_input();self.assertEqual(self.ops(),['pause'])

    def test_backend_restart_can_resync_without_short_clock_pause(self):
        self.start()
        for at in (100.2,100.3,100.4):
            self.deliver(at,pid=124);self.finish_resync()
        self.assertFalse(self.s.child_paused);self.assertEqual(self.ops(),[])

    def test_pending_music_silence_does_not_turn_short_gap_into_pause(self):
        self.start();self.deliver(100.9,missing=True)
        music=SimpleNamespace(set_menu_open=Mock(),status=Mock(return_value=dict(silenced=False)),close=Mock())
        self.s.menu_music=music
        self.s.send_child('resume')
        request=self.s.music_resume_pending
        self.assertIsNotNone(request);self.assertEqual(self.ops(),[])
        music.status.return_value=dict(silenced=True)
        self.s.resume_after_music(request,102.)
        self.assertIsNone(self.s.music_resume_pending);self.assertEqual(self.ops(),[])

    def test_pending_music_callback_cannot_resume_after_manual_menu_open(self):
        self.start();self.deliver(100.9,missing=True)
        self.s.menu_music=SimpleNamespace(set_menu_open=Mock(),status=lambda:dict(silenced=False),close=Mock())
        self.s.send_child('resume');request=self.s.music_resume_pending
        self.s.menu_open=True;self.s.send_child('pause')
        self.s.resume_after_music(request,102.)
        self.assertTrue(self.s.child_paused);self.assertEqual(self.ops(),['pause'])

    def test_fresh_wrong_mode_cannot_hold_grace_forever(self):
        self.start()
        for at in (100.2,101.,102.,102.8):
            self.deliver(at,suite=dict(active=False,mode='ink',menu=False,open_serial=0))
        self.assertEqual(self.ops(),['pause'])


if __name__=='__main__':unittest.main(verbosity=2)
