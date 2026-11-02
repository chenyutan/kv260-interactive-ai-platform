"""Opt-in diagnostic invariants; synthetic COCO17 only, no live source IO."""
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from .diagnostics import RunnerDiagnostic, maintain_practice, source_packet
from .engine import RunnerGame
from .test_core import pose
from .test_main import load_main

TEMP_ROOT=Path(__file__).resolve().parents[3]/'qa'/'runner-diagnostic'/'tmp'


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True,exist_ok=True)
        self.temp=TemporaryDirectory(dir=TEMP_ROOT);self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'input.jsonl'

    def rows(self):
        return [json.loads(s) for s in self.path.read_text(encoding='utf8').splitlines()]

    def test_writer_flushes_matching_tick_and_summary_without_private_fields(self):
        d=RunnerDiagnostic(self.path,90,started=10,entry_until=35)
        packet=pose(36,9);packet.update(visitor={'token':'forbidden'},account='forbidden',
            suite=dict(menu=False,control_active=True,session='forbidden'))
        d.record(now=36,dt=.02,source=packet,raw_action=dict(valid=True,move=1,jump=True),
            effective_action=dict(valid=True,move=1,jump=True,reason='jump'),
            tracker_before={'body':{'anchor':[.5,.6,.2]}},tracker_after={'body':{'jump_armed':False}},
            game_before={'state':'running','player_lane':1,'jumping':False},
            game_after={'state':'running','player_lane':2,'jumping':True},
            events=[dict(kind='lane',direction=1),dict(kind='jump')],entered=True,
            tracking_paused=False,generation=2)
        self.assertTrue(d.close('duration_limit'))
        rows=self.rows();tick=next(r for r in rows if r['kind']=='input_tick')
        self.assertEqual(tick['source']['sequence'],9)
        self.assertEqual(len(tick['source']['points17']),17)
        self.assertEqual(tick['source']['source_age_seconds'],0)
        self.assertEqual(tick['game_before']['player_lane'],1)
        self.assertEqual(tick['game_after']['player_lane'],2)
        self.assertEqual([e['kind'] for e in tick['game_events']],['lane','jump'])
        self.assertNotIn('forbidden',self.path.read_text(encoding='utf8'))
        self.assertEqual(rows[-1]['kind'],'writer_summary')
        self.assertEqual(rows[-1]['dropped'],0)
        self.assertEqual(rows[-1]['submitted'],rows[-1]['written'])
        self.assertEqual(rows[-1]['executed'],dict(lane=1,jump=1))
        self.assertIn('右',d.hud());self.assertIn('跳',d.hud())

    def test_ready_restarts_full_entry_countdown_and_duration(self):
        d=RunnerDiagnostic(self.path,90,started=10,entry_until=35)
        d.begin_entry(100,25)
        self.assertEqual(d.entry_until,125);self.assertEqual(d.deadline,215)
        self.assertFalse(d.expired(214.999));self.assertTrue(d.expired(215))
        self.assertTrue(d.close())

    def test_no_overwrite_and_invalid_duration(self):
        self.path.write_text('keep',encoding='utf8')
        with self.assertRaises(FileExistsError):RunnerDiagnostic(self.path)
        for duration in (0,181,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):RunnerDiagnostic(self.path,duration)
        self.assertEqual(self.path.read_text(encoding='utf8'),'keep')

    def test_bounded_queue_drops_without_blocking(self):
        # Hold only worker startup, so actual queue behavior is deterministic.
        with patch('runner.diagnostics.threading.Thread.start'):
            d=RunnerDiagnostic(self.path,capacity=1)
        try:
            self.assertFalse(d.submit(dict(kind='overflow')))
            self.assertEqual(d.dropped,1);self.assertEqual(d.queue.qsize(),1)
        finally:d.stream.close()

    def test_bad_io_cannot_report_success(self):
        with patch('runner.diagnostics.threading.Thread.start'):
            d=RunnerDiagnostic(self.path)
        d.stream.close();d.stop.set();d._write()
        self.assertIn('ValueError',d.error)
        # Not a running Thread, but close must expose the error via its result.
        d.thread=SimpleNamespace(is_alive=lambda:False,join=lambda _:None)
        self.assertFalse(d.close())

    def test_nonfinite_source_metadata_stays_json_safe(self):
        p=pose(4,1);p['points17'][0][0]=float('nan');p['latency_ms']=float('inf')
        data=source_packet(p,5)
        self.assertIsNone(data['points17'][0][0]);self.assertIsNone(data['latency_ms'])
        json.dumps(data,allow_nan=False)

    def test_practice_keeps_obstacles_but_removes_buffs_and_death(self):
        game=RunnerGame();game.start();game.spawn('barrier',1,10)
        for kind in game.POWERUPS:game.spawn(kind,1,10)
        game.state='over';game.lives=0;game.jetpack_time=5;game.boots_time=5
        self.assertTrue(maintain_practice(game))
        self.assertEqual(game.state,'running');self.assertEqual(game.lives,3)
        self.assertEqual([e.kind for e in game.entities],['barrier'])
        self.assertEqual(game.jetpack_time,0);self.assertEqual(game.boots_time,0)
        self.assertTrue(game.jump())

    def panel(self):
        ui=load_main();self.now=100.
        clock=patch.object(ui.time,'monotonic',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        args=SimpleNamespace(embed_socket=None,offline_preview=False,seed=4,
            entry_delay=25.,metrics=None,benchmark=0,diagnostic_log=self.path,diagnostic_seconds=90.)
        with patch.object(ui.RunnerPanel,'poll',return_value=None),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('no network')):
            panel=ui.RunnerPanel(args)
        self.addCleanup(lambda:panel.operation('shutdown'))
        return ui,panel

    def test_real_panel_waits_for_ready_auto_starts_resets_baseline_and_closes(self):
        ui,panel=self.panel();self.assertIsNone(panel.reporter);self.assertIsNone(panel.gamepad)
        self.now=140.;panel.tick();self.assertEqual(panel.game.state,'title')
        panel.begin_diagnostic_entry();self.assertEqual(panel.entry_until,165.)
        panel.tracker.body.anchor=(.1,.1,.1)
        self.now=164.9;panel.latest=pose(self.now,1);panel.tick()
        self.assertEqual(panel.game.state,'title')
        self.now=165.;panel.latest=pose(self.now,2);panel.tick()
        self.assertEqual(panel.game.state,'running');self.assertIsNone(panel.tracker.body.anchor)
        self.assertEqual(panel.control['reason'],'diagnostic_entry_completed')
        self.assertEqual(panel.diagnostic.deadline,255.)
        self.now=255.
        with contextlib.redirect_stdout(io.StringIO()) as output:self.assertFalse(panel.tick())
        self.assertTrue(panel.stop.is_set());self.assertTrue(panel.diagnostic_ok)
        self.assertNotIn('runner_round_completed',output.getvalue())
        self.assertEqual(self.rows()[-1]['reason'],'duration_limit')

    def test_real_pose_decision_and_engine_execution_share_sequence_row(self):
        ui,panel=self.panel();panel.begin_diagnostic_entry();self.now=125.;panel.tick()
        panel.game._next_wave_distance=1e9
        for seq in range(1,10):
            self.now+=.05;panel.latest=pose(self.now,seq);panel.tick()
        self.now+=.05;panel.latest=pose(self.now,10,body_down=-.05);panel.tick()
        self.assertTrue(panel.game.jumping)
        with contextlib.redirect_stdout(io.StringIO()):panel.operation('shutdown')
        tick=next(r for r in self.rows() if r['kind']=='input_tick' and (r['source'] or {}).get('sequence')==10)
        self.assertTrue(tick['raw_action']['jump']);self.assertTrue(tick['action']['jump'])
        self.assertFalse(tick['game_before']['jumping']);self.assertTrue(tick['game_after']['jumping'])
        self.assertTrue(any(e['kind']=='jump' for e in tick['game_events']))
        self.assertIn('anchor',tick['tracker_before']['body'])
        self.assertIn('body_geometry',tick['tracker_after'])


if __name__=='__main__':unittest.main()
