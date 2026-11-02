import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from thextech.runtime import NativeChild, ReportGame, launch_spec, read_config
from thextech.selection import selected, entry


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = self.root / 'engine/bin/thextech'
        self.engine.parent.mkdir(parents=True)
        self.engine.write_bytes(b'verified engine')
        self.content = self.root / 'complete game'
        world = self.content / 'worlds/the first adventure/world.wldx'
        world.parent.mkdir(parents=True)
        world.write_text('original world', encoding='utf8')
        (self.content / 'License.txt').write_text('Original notices', encoding='utf8')
        self.data = dict(schema=1, version='1.7.4-ink1', engine=str(self.engine),
                         engine_sha256=hashlib.sha256(self.engine.read_bytes()).hexdigest(),
                         content=str(self.content), user_root=str(self.root / 'private saves'))
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.data), encoding='utf8')

    def test_complete_world_nonzero_save_and_private_input_no_system_changes(self):
        old = dict(os.environ)
        config = read_config(self.config)
        command, env, user = launch_spec(config, NS(child_env={'INK_XTECH_FD': '123'}), no_audio=True)
        self.assertEqual(command[-1], str(self.content / 'worlds/the first adventure/world.wldx'))
        self.assertEqual(command[command.index('--save-slot') + 1], '1')
        self.assertIn('--never-pause', command)
        self.assertEqual(env['INK_XTECH_FD'], '123')
        self.assertEqual(env['SDL_AUDIODRIVER'], 'dummy')
        self.assertNotIn('SDL_WINDOWID', env)
        self.assertNotIn('LD_PRELOAD', env)
        self.assertEqual(os.environ, old)
        self.assertEqual(user, self.root / 'private saves')
        self.assertFalse(user.exists())

    def test_engine_corruption_is_rejected_before_launch(self):
        self.engine.write_bytes(b'changed binary')
        with self.assertRaisesRegex(ValueError, 'verification'):
            read_config(self.config)

    def test_incomplete_content_is_rejected(self):
        (self.content / 'License.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'): read_config(self.config)

    def test_graphics_context_and_symbols_use_same_child_only_driver(self):
        driver=self.root/'libMali.so.9';driver.write_bytes(b'private test driver fixture')
        self.data['graphics_library']=str(driver)
        self.config.write_text(json.dumps(self.data),encoding='utf8')
        before=dict(os.environ)
        _,env,_=launch_spec(read_config(self.config),NS(child_env={}))
        self.assertEqual(env['SDL_VIDEO_GL_DRIVER'],str(driver))
        self.assertEqual(env['SDL_VIDEO_EGL_DRIVER'],str(driver))
        self.assertEqual(env['SDL_VIDEO_X11_FORCE_EGL'],'1')
        self.assertEqual(os.environ,before)
        driver.unlink()
        with self.assertRaisesRegex(ValueError,'graphics library'):read_config(self.config)

    def test_legacy_fallback_and_explicit_rollback(self):
        self.assertFalse(selected(self.root, {}))
        (self.root / 'thextech').mkdir()
        (self.root / 'thextech/runtime.json').write_text('{}')
        self.assertTrue(selected(self.root, {}))
        self.assertFalse(selected(self.root, {'INK_PLATFORMER_ENGINE': 'legacy'}))
        self.assertEqual(entry(self.root, {}), self.root / 'thextech/main.py')
        self.assertEqual(entry(self.root, {'INK_PLATFORMER_ENGINE': 'legacy'}), self.root / 'platformer/main.py')


class CloseTests(unittest.TestCase):
    def child(self, peer_closed=False):
        child = NativeChild.__new__(NativeChild)
        child.peer = Mock(closed=peer_closed)
        child.process = Mock(pid=12345)
        child.log = Mock()
        return child

    def test_closed_socket_still_terminates_and_reaps_owned_process(self):
        child = self.child(True)
        child.process.poll.return_value = None
        log = child.log
        with patch('thextech.runtime.os.killpg') as kill:
            child.close()
        kill.assert_called_once_with(12345, __import__('signal').SIGTERM)
        child.process.wait.assert_called_once_with(timeout=1.)
        child.peer.quit.assert_not_called()
        child.peer.close.assert_called_once()
        log.close.assert_called_once()

    def test_broken_peer_during_quit_cannot_skip_cleanup(self):
        child = self.child()
        child.process.poll.return_value = None
        child.peer.quit.side_effect = BrokenPipeError()
        with patch('thextech.runtime.os.killpg') as kill:
            child.close()
        self.assertEqual(kill.call_count, 1)
        child.peer.close.assert_called_once()
        self.assertIsNone(child.log)

    def test_stubborn_process_is_killed_after_bounded_grace(self):
        child = self.child(True)
        child.process.poll.return_value = None
        child.process.wait.side_effect = [subprocess.TimeoutExpired('owned', 1.), 0]
        with patch('thextech.runtime.os.killpg') as kill:
            child.close()
        self.assertEqual(kill.call_count, 2)
        child.peer.close.assert_called_once()


class ReportTests(unittest.TestCase):
    def test_restart_time_baseline_and_engine_rule_separation(self):
        from visitor.store import rules
        game = ReportGame()
        telemetry = dict(score=120, coins=5, lives100=300, active_ms=12345, phase=3)
        game.update(telemetry)
        self.assertEqual(game.report()['time'], 12.345)
        self.assertEqual(rules('platformer', game.report()), 'thextech-aod')
        self.assertEqual(rules('platformer', {'score': 120}), 'standard')
        game.reset_round(telemetry)
        game.update(dict(telemetry, active_ms=13345))
        self.assertEqual(game.report()['time'], 1.)
        self.assertEqual(game.report()['score'], 120)
        self.assertEqual(game.state, 'playing')
        game.update(dict(telemetry, phase=4))
        self.assertEqual(game.state, 'won')
