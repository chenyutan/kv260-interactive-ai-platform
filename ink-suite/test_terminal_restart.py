"""Real game engines and production menu/adapter methods; no GTK or camera.

Terminal states, idempotent action receipts and release ownership are covered
here. Native XEmbed rendering/focus delivery is additionally checked on Linux.
"""
import ast
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from test_action_dispatch import ROOT, load_class, req
import test_action_host as host_fixtures
from sword.engine import Game as SwordGame
from taiko.audio_clock import SilentClock
from taiko.session import Session


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TerminalAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fruit_path = next((p for p in (
            Path(os.environ.get('INKSUITE_TEST_FRUIT_ENGINE','missing')),
            ROOT.parent/'FruitSlice/native/engine.py',Path('/home/ubuntu/fruit-slice/native/engine.py')) if p.is_file()),None)
        fight_path = next((p for p in (
            Path(os.environ.get('INKSUITE_TEST_FIGHT_ENGINE','missing')),
            ROOT.parent/'InkFight/native/engine.py',Path('/home/ubuntu/ink-fight/native/engine.py')) if p.is_file()),None)
        if fruit_path is None or fight_path is None:
            raise FileNotFoundError('Set INKSUITE_TEST_FRUIT_ENGINE and INKSUITE_TEST_FIGHT_ENGINE to real engines')
        cls.fruit = module_at('restart_fruit_engine', fruit_path)
        fight_root = fight_path.parent
        sys.path.insert(0, str(fight_root))
        cls.fight = module_at('restart_fight_engine', fight_path)

    def embedded(self, mode):
        Controller, ns = load_class(ROOT/'embedded_runner.py', 'Controller')
        c = Controller.__new__(Controller)
        c.args = NS(mode=mode, offline_preview=True)
        c.paused = False; c.closed = False; c.before = None
        c.sound = NS(pause=Mock(), resume=Mock())
        game = self.fruit.Game() if mode == 'fruit' else self.fight.Fight()
        game.start()
        if mode == 'fruit':
            game.advance(3.1); game.advance(60.1)
            self.assertEqual(game.state, 'over')
        else:
            # Timeout is a normal real-engine terminal transition; no mock
            # end-state flags replace the engine's result/finishing logic.
            game.advance(120.)
            self.assertEqual(game.state, 'over')
        c.app = NS(game=game, last=time.monotonic(), start=game.start,
            tick=Mock(),
            area=NS(queue_draw=Mock()), pose=NS(reset=Mock()), trails={},
            gesture=NS(reset=Mock()), cancel_input=Mock(), help_open=False,
            keys=set(), reader=None, manual_guard=False, tracking_pause=False)
        return c, ns

    def test_terminal_fruit_and_fight_restart_then_release_once(self):
        for mode in ('fruit', 'fight'):
            with self.subTest(mode=mode):
                c, ns = self.embedded(mode)
                c.pause()
                self.assertTrue(c.paused)
                self.assertEqual(c.before, 'over')
                c.handle_action(req('restart', mode+'-terminal-restart'))
                receipt = ns['emit'].call_args
                self.assertEqual(receipt.args, ('action_result',))
                self.assertTrue(receipt.kwargs['ok'])
                self.assertEqual(receipt.kwargs['result']['game_state'], 'countdown')
                self.assertEqual(c.app.game.state, 'paused')
                c.app.game.advance(20.)
                self.assertEqual(c.app.game.state, 'paused')
                c.handle_action(req('restart', mode+'-terminal-restart'))
                self.assertEqual(c.app.game.countdown, 3.)
                c.resume()
                self.assertEqual(c.app.game.state, 'countdown')
                c.app.game.advance(3.1)
                self.assertEqual(c.app.game.state, 'playing')
                c.app.game.advance(.1)
                c.handle_action(req('restart', mode+'-terminal-restart'))
                # Replayed receipt must never restart an already resumed game.
                self.assertEqual(c.app.game.state, 'playing')
                c.sound.resume.assert_called_once()

    def test_start_silently_refused_cannot_send_successful_restart_receipt(self):
        c, ns = self.embedded('fight')
        c.pause(); c.app.start = Mock()  # E.g. a recorder that forbids restart.
        c.handle_action(req('restart'))
        packet = ns['emit'].call_args.kwargs
        self.assertFalse(packet['ok'])
        self.assertEqual(packet['reason'], 'restart_did_not_prepare_round')
        self.assertEqual(c.before, 'over')
        self.assertTrue(c.paused)

    def test_delayed_focus_out_cannot_pause_a_released_embedded_round(self):
        tree = ast.parse((ROOT/'embedded_runner.py').read_text(encoding='utf-8'))
        controller = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Controller')
        load = next(n for n in controller.body if isinstance(n, ast.FunctionDef) and n.name == 'load')
        window = next(n for n in load.body if isinstance(n, ast.ClassDef) and n.name == 'EmbeddedWindow')
        for mode in ('fruit', 'fight'):
            with self.subTest(mode=mode):
                c, _ = self.embedded(mode)
                c.pause(); c.execute_action('restart', Mock()); c.resume()
                game = c.app.game
                NativeWindow, _ = load_class(ROOT/'native_windows'/(mode+'.py'), 'Window')
                ns = dict(module=NS(Window=NativeWindow), owner=c)
                exec(compile(ast.Module(body=[window],type_ignores=[]),'real-embedded-window','exec'),ns)
                embedded = ns['EmbeddedWindow'].__new__(ns['EmbeddedWindow'])
                embedded.__dict__.update(c.app.__dict__)
                embedded.args=NS(benchmark=False)
                c.app=embedded
                self.assertFalse(embedded.focus_out(None, None))
                self.assertEqual(game.state, 'countdown')
                game.advance(3.1)
                self.assertEqual(game.state, 'playing')
                embedded.focus_out(None, None)
                self.assertEqual(game.state, 'playing')

    def test_terminal_sword_restart_remains_frozen_until_host_resume(self):
        Panel, ns = load_class(ROOT/'sword/main.py', 'SwordPanel', RESUME_SECONDS=3.)
        p = Panel.__new__(Panel)
        p.active = True; p.stop = threading.Event(); p.lock = threading.Lock()
        p.generation = 1; p.args = NS(embed_socket=1, entry_delay=0.)
        p.timeline = None; p.canvas = NS(queue_draw=Mock()); p.clear_input = Mock()
        p.dwell = NS(reset=Mock()); p.sound = NS(pause=Mock(), resume=Mock(), restart=Mock())
        p.game = SwordGame(duration=.1)
        p.game.start(countdown=0)
        for _ in range(20): p.game.update(.02, {'x':.5,'y':.8,'valid':True})
        self.assertIn(p.game.phase, ('won','lost'))
        p.end_started = time.monotonic(); p.end_paused_at = None
        p.operation('pause')
        p.handle_action(req('restart', 'sword-terminal-restart'))
        self.assertTrue(ns['emit'].call_args.kwargs['ok'])
        self.assertFalse(p.active); self.assertTrue(p.game.paused)
        self.assertEqual(p.game.phase, 'countdown'); self.assertIsNone(p.end_started)
        p.game.update(10., {'valid':True}); self.assertEqual(p.game.time, 0.)
        p.operation('resume')
        self.assertTrue(p.active)
        p.game.pause(False)
        for _ in range(310): p.game.update(.01, {'x':.5,'y':.8,'valid':True})
        self.assertGreater(p.game.time, 0.)
        p.sound.restart.assert_called_once()

    def test_terminal_taiko_restart_retains_judgment_and_offset(self):
        Panel, ns = load_class(ROOT/'taiko/main.py', 'TaikoPanel')
        p = Panel.__new__(Panel)
        p.active = True; p.stop = threading.Event(); p.lock = threading.Lock(); p.generation = 1
        p.args = NS(embed_socket=1, entry_delay=0.)
        p.audio = SilentClock(); p.session = Session(p.audio, offset_ms=-100, judgment='relaxed')
        p.session.resume(); p.session.game.advance(p.session.game.duration + 1)
        self.assertTrue(p.session.game.finished)
        p.canvas = NS(queue_draw=Mock()); p.clear_input = Mock(); p.write_metrics = Mock()
        p.synthetic_seen = {1,2}; p.final_at = time.monotonic()
        p.sound = NS(pause=Mock(),resume=Mock(),restart=Mock())
        p.operation('pause'); p.handle_action(req('restart', 'taiko-terminal-restart'))
        self.assertTrue(ns['emit'].call_args.kwargs['ok'])
        self.assertFalse(p.session.running); self.assertFalse(p.audio.playing)
        self.assertFalse(p.session.game.finished); self.assertEqual(p.session.game.time, 0.)
        self.assertEqual(p.session.offset, -.1); self.assertEqual(p.session.judgment, 'relaxed')
        self.assertIsNone(p.final_at); self.assertFalse(p.synthetic_seen)
        p.operation('resume'); self.assertTrue(p.active); self.assertTrue(p.session.resume())
        self.assertTrue(p.session.running); p.sound.restart.assert_called_once()


class TerminalHostTests(unittest.TestCase):
    setUp=host_fixtures.ActionHostTests.setUp
    action_messages=host_fixtures.ActionHostTests.action_messages
    dispatch_restart=host_fixtures.ActionHostTests.dispatch_restart
    ack=host_fixtures.ActionHostTests.ack
    complete_last=host_fixtures.ActionHostTests.complete_last
    def missing_response(self,*,ready,menu):
        s=self.s;s.latest=None;s.lock=threading.Lock();s.pending_state=True
        s.pipeline=NS(ready=ready,reason='service_unavailable',update=lambda state,now:None)
        s.menu_open=menu;s.child_paused=False
        return s
    def test_brief_missing_response_cannot_cancel_resumed_game_ownership(self):
        s=self.missing_response(ready=True,menu=False)
        generation=s.generation;token=s.resync_token
        s.deliver_state()
        self.assertTrue(s.backend_ready);self.assertFalse(s.menu_open)
        self.assertEqual(s.generation,generation);self.assertEqual(s.resync_token,token)
        self.assertFalse(s.child.messages());self.assertFalse(s.commands)
    def test_missing_menu_response_discards_dwell_without_resync_or_pause(self):
        s=self.missing_response(ready=True,menu=True)
        s.selector.done=True;s.deliver_state()
        self.assertTrue(s.backend_ready);self.assertTrue(s.menu_open)
        self.assertFalse(s.selector.done);self.assertIsNone(s.selector.opened)
        self.assertFalse(s.actions.available);self.assertFalse(s.menu.available)
        self.assertFalse(s.child.messages());self.assertFalse(s.commands)
    def test_persistent_missing_response_still_revokes_game_and_pauses(self):
        s=self.missing_response(ready=False,menu=False)
        s.deliver_state()
        self.assertFalse(s.backend_ready)
        self.assertEqual(s.child.messages(),[{'op':'pause'}])
    def test_restart_success_retains_terminal_menu_until_backend_ack(self):
        for mode in ('fruit','fight','sword','taiko'):
            with self.subTest(mode=mode):
                self.setUp(); s=self.s; s.mode=mode
                s.child_event(s.child,mode,s.generation,dict(event='paused',game_state='over',game_clock=60.))
                self.assertTrue(s.child_receipt['paused'])
                pending=self.dispatch_restart()
                self.ack(pending,result={'game_state':'countdown' if mode!='taiko' else 'paused'})
                self.assertTrue(s.menu_open)
                self.assertFalse(any(p.get('op')=='resume' for p in s.child.messages()))
                self.complete_last()
                self.assertFalse(s.menu_open)
                self.assertEqual(sum(p.get('op')=='resume' for p in s.child.messages()),1)
                s.child_event(s.child,mode,s.generation,dict(event='resumed',game_state='countdown',game_clock=0.))
                self.assertFalse(s.child_receipt['paused']); self.assertEqual(s.child_receipt['clock'],0.)


if __name__ == '__main__': unittest.main()
