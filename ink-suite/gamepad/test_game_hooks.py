"""Production native lifecycle methods, without devices, GTK or HTTP."""
from collections import deque
import math
from pathlib import Path
import threading
from types import SimpleNamespace as NS
from types import ModuleType
import unittest
from unittest.mock import Mock,patch

from gamepad.adapters import GamepadControls
from gamepad.test_adapters import Reader
from test_action_dispatch import load_class

ROOT=Path(__file__).resolve().parent.parent


class EmbeddedHooks(unittest.TestCase):
    def make(self,mode):
        gdk=NS(KEY_Left=1,KEY_Right=2,KEY_Down=3)
        cls,ns=load_class(ROOT/'embedded_runner.py','Controller',Gdk=gdk)
        c=cls.__new__(cls);c.args=NS(mode=mode,offline_preview=False)
        r=Reader();c.gamepad=GamepadControls(mode,r);c.gamepad_hold=None;c.gamepad_notice=''
        c.game_world=(1280,720);c.sound=NS(pause=Mock(),resume=Mock())
        a=NS(game=NS(state='playing',paths={},pause=Mock(),input=Mock()),trails={},
             keys=set(),manual_guard=False,manual_jump=False,perform=Mock(),trail=Mock(),start=Mock(),
             area=NS(queue_draw=Mock(),queue_draw_area=Mock(),get_allocated_width=lambda:960,get_allocated_height=lambda:540),
             cancel_input=Mock(),last=100.)
        a.game.pause.side_effect=lambda:setattr(a.game,'state','paused')
        c.gamepad.sample(100)
        return c,r,a
    def test_fruit_goes_to_real_collision_and_trail_not_pose(self):
        c,r,a=self.make('fruit');r.set(('A',),lx=.8)
        original=Mock(return_value=True)
        c.gamepad_tick(a,original)
        original.assert_called_once();a.game.input.assert_called_once();a.trail.assert_called_once()
        self.assertEqual(a.game.input.call_args.args[0],'gamepad')
    def test_fruit_release_has_no_collision_input(self):
        c,r,a=self.make('fruit');r.set(lx=1.)
        c.gamepad_tick(a,Mock(return_value=True));a.game.input.assert_not_called()
    def test_fight_attacks_go_through_perform_and_input_gate(self):
        c,r,a=self.make('fight');r.set(('X','B','A','LB'),lx=-1.,ly=1.)
        c.gamepad_tick(a,Mock(return_value=True))
        self.assertEqual([v.args[0] for v in a.perform.call_args_list],['left','kick_left'])
        self.assertTrue(a.manual_jump);self.assertTrue(a.manual_guard);self.assertEqual(a.keys,{1,3})
    def test_disconnect_freezes_then_neutral_reconnect_preserves_state(self):
        c,r,a=self.make('fight');r.s['connected']=False;tick=Mock(return_value=True)
        c.gamepad_tick(a,tick)
        self.assertEqual(a.game.state,'paused');self.assertEqual(c.gamepad_hold,'playing');tick.assert_not_called()
        r.set(('A',));c.gamepad_tick(a,tick);tick.assert_not_called()
        r.set();c.gamepad_tick(a,tick)
        self.assertEqual(a.game.state,'playing');self.assertFalse(a.manual_jump);tick.assert_called_once()
        c.sound.pause.assert_called_once();c.sound.resume.assert_called_once()


class PanelHooks(unittest.TestCase):
    def panel(self,kind):
        if kind=='runner':
            from runner.test_main import load_main
            ui=load_main()
        else:
            from platformer.test_main import _headless_main
            ui=_headless_main()
        r=Reader();g=GamepadControls(kind,r)
        reporter=ModuleType('visitor.reporter');reporter.attach=Mock(return_value=NS())
        args=NS(embed_socket=None,offline_preview=False,seed=3,entry_delay=0.,metrics=None,benchmark=0,base='http://127.0.0.1:1')
        with patch.object(ui,'create_gamepad',return_value=g),patch.dict('sys.modules',{'visitor.reporter':reporter}),\
             patch.object(ui.threading,'Thread',side_effect=AssertionError('gamepad must not poll pose')),\
             patch.object(ui.time,'monotonic',return_value=100.):
            p=ui.RunnerPanel(args) if kind=='runner' else ui.PlatformerPanel(args)
        self.assertIsNone(p.poll_thread);self.assertTrue(p.sound.enabled);reporter.attach.assert_called_once()
        return ui,p,r
    def test_runner_gamepad_keeps_live_audio_reporting_without_pose_thread(self):
        ui,p,r=self.panel('runner')
        try:
            with patch.object(ui.time,'monotonic',return_value=100.01):p.tick()
            r.set(('X',))
            with patch.object(ui.time,'monotonic',return_value=100.03):p.tick()
            self.assertEqual(p.game.state,'running')
            r.s['connected']=False;old=p.game.elapsed
            p.pending.append(('jump',True))  # A queued key cannot revive a disconnected pad.
            with patch.object(ui.time,'monotonic',return_value=100.05):p.tick()
            self.assertEqual(p.game.elapsed,old);self.assertTrue(p.tracking_paused)
        finally:p.operation('shutdown')
    def test_platformer_gamepad_keeps_live_audio_reporting_without_pose_thread(self):
        ui,p,r=self.panel('platformer')
        try:
            with patch.object(ui.time,'monotonic',return_value=100.01):p.tick()
            r.set(('X',))
            with patch.object(ui.time,'monotonic',return_value=100.03):p.tick()
            self.assertFalse(p.ready);self.assertEqual(p.game.state,'countdown')
            r.s['connected']=False
            with patch.object(ui.time,'monotonic',return_value=100.05):p.tick()
            self.assertEqual(p.game.state,'paused');self.assertIn('未连接',p.snapshot['ui']['notice'])
        finally:p.operation('shutdown')
    def test_sword_direct_input_uses_real_game_without_tracker(self):
        from sword.engine import Game
        cls,ns=load_class(ROOT/'sword/main.py','SwordPanel',math=math)
        p=cls.__new__(cls);r=Reader();p.gamepad=GamepadControls('sword',r);p.gamepad.sample(100)
        p.game=Game(seed=2);p.entry_until=0.;p.sound=NS(restart=Mock());p.stop=threading.Event()
        p.active=True;p.args=NS(embed_socket=1);p.dwell=NS(reset=Mock());p.canvas=NS(queue_draw=Mock())
        p.tracker=NS(update=Mock(side_effect=AssertionError('no pose')))
        p.recovery_needed=False;p.recovery_until=0.;p.last_tick=100.
        r.set(('A',));p.advance_gamepad(101,.02)
        self.assertEqual(p.game.phase,'countdown');self.assertFalse(p.game.paused)
        r.s['connected']=False;p.advance_gamepad(101.02,.02)
        self.assertTrue(p.game.paused);self.assertIn('未连接',p.notice);p.tracker.update.assert_not_called()
    def test_taiko_reuses_session_clock_and_valid_edges(self):
        from taiko.audio_clock import SilentClock
        from taiko.session import Session
        cls,ns=load_class(ROOT/'taiko/main.py','TaikoPanel',math=math)
        p=cls.__new__(cls);r=Reader();p.gamepad=GamepadControls('taiko',r);p.gamepad.sample(100)
        p.audio=SilentClock();p.session=Session(p.audio);p.entry_until=0.;p.final_at=None
        p.sound=NS(tick=Mock());p.canvas=NS(queue_draw=Mock());p.tick_ms=deque(maxlen=100)
        p.timing_spin=NS(set_sensitive=Mock());p.write_metrics=Mock()
        with patch.object(ns['time'],'perf_counter',return_value=1.):
            p.tick_gamepad(101,1.)
            self.assertTrue(p.session.running)
            p.session.tick=Mock();r.set(('X','B','Y'))
            p.tick_gamepad(101.02,1.)
            events=p.session.tick.call_args.args[1]
            self.assertEqual([e['hand'] for e in events],['L','R','C'])
            r.s['connected']=False;p.tick_gamepad(102,1.)
            self.assertFalse(p.session.running);self.assertIn('未连接',p.overlay)


if __name__=='__main__':unittest.main()
