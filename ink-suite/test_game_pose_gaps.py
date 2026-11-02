"""Actual native tick bodies with synthetic source/control metadata only."""
import ast
import importlib.util
from collections import deque
import math
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from game_source_policy import SourceGapPolicy

ROOT=Path(__file__).resolve().parent


def method(file,cls,name,scope):
    tree=ast.parse((ROOT/file).read_text(encoding='utf8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name==cls)
    fn=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(file),'exec'),scope)
    return scope[name]


def packet(t,**fields):return dict(pid=1,sequence=round(t*1000),timestamp_seconds=t,**fields)


class GamePoseGapTests(unittest.TestCase):
    def fight(self):
        clock=[10.]
        blocked=lambda p:bool(p and (p.get('suite') or {}).get('control_active')) or bool(p and (p.get('suite') or {}).get('menu'))
        scope=dict(time=NS(monotonic=lambda:clock[0]),math=math,W=1280,H=720,
                   BodyControl=NS(blocked=blocked),SourceGapPolicy=SourceGapPolicy)
        tick=method(Path('native_windows/fight.py'),'Window','tick',scope)
        p=NS(last=10.,manual_guard=False,recorder=None,walk_in_until=None,pose=None,
             args=NS(benchmark=False),source_gap=SourceGapPolicy(),help_open=False,
             gesture=NS(guard=False,valid=False,both_valid=False,hint='',consume=lambda *_:[],reset=Mock()),
             body_control=NS(consume=lambda *_a,**_kw:dict(events=[],move_axis=0,crouch_guard=False,status='lost'),reset=Mock()),
             round_export=NS(saved=True),round_save_retry=0.,lost_since=None,good_since=None,tracking_pause=False,
             body_state={},input_feedback='',feedback_until=0.,input_gate=NS(pending=dict(move='old_punch'),flush=Mock()),
             area=NS(queue_draw=Mock(),queue_draw_area=Mock(),get_allocated_width=lambda:1024,get_allocated_height=lambda:600),
             last_status='',perform=Mock(),feedback=Mock())
        p.input_gate.clear=Mock(side_effect=lambda:setattr(p.input_gate,'pending',None))
        p.cancel_input=method(Path('native_windows/fight.py'),'Window','cancel_input',scope).__get__(p)
        p.game=NS(state='playing',time=0.,countdown=0.,hp=100,enemy_hp=100,energy=0,remaining=60)
        p.game.advance=lambda dt,*_:setattr(p.game,'time',p.game.time+dt if p.game.state=='playing' else p.game.time)
        p.reader=NS(get=lambda:p.pose)
        return p,clock,tick.__get__(p)

    def test_fight_missing_all_body_joints_keeps_world_and_drops_queued_attack(self):
        p,clock,tick=self.fight()
        for n in range(1,81):
            clock[0]=10+n*.05;p.pose=packet(clock[0],points17=None);tick()
        self.assertGreater(p.game.time,3.9);self.assertEqual(p.game.state,'playing')
        self.assertFalse(p.tracking_pause);self.assertIsNone(p.input_gate.pending);p.perform.assert_not_called()

    def test_fight_candidate_does_not_freeze_but_confirmed_menu_still_does(self):
        p,clock,tick=self.fight();clock[0]=10.1
        p.pose=packet(10.1,suite=dict(control_active=True));tick();self.assertGreater(p.game.time,0.)
        before=p.game.time;clock[0]=10.2;p.pose=packet(10.2,suite=dict(menu=True));tick()
        self.assertEqual(p.game.time,before);p.perform.assert_not_called()

    def test_fight_real_source_timeout_and_manual_pause_are_distinct(self):
        p,clock,tick=self.fight();clock[0]=10.1;p.pose=packet(10.1);tick()
        for now in (10.2,11.,12.1):clock[0]=now;p.pose=None;tick();self.assertFalse(p.tracking_pause)
        clock[0]=12.7;tick();self.assertTrue(p.tracking_pause);self.assertEqual(p.game.state,'paused')
        clock[0]=12.8;p.pose=packet(12.8);tick();self.assertEqual(p.game.state,'playing')
        p.game.state='paused';p.tracking_pause=False;clock[0]=12.9;p.pose=packet(12.9);tick()
        self.assertEqual(p.game.state,'paused')

    def test_kart_missing_joints_keeps_audio_and_physics_with_neutral_action(self):
        clock=[10.];keys=('Right','Left','d','a','Shift_L','Shift_R','Down','s')
        scope=dict(time=NS(monotonic=lambda:clock[0]),math=math,
                   Gdk=NS(**{'KEY_'+key:n for n,key in enumerate(keys)}),SourceGapPolicy=SourceGapPolicy)
        tick=method(Path('kart/main.py'),'KartPanel','tick',scope)
        p=NS(stop=threading.Event(),last_tick=10.,active=True,gamepad=None,
             args=NS(offline_preview=False,hold_frame=False),lock=threading.Lock(),latest=None,
             tracker=NS(update=lambda *_:dict(valid=False,reason='body_missing',steer=0,drift=False,item=False)),
             pending=[],down=set(),last_valid=10.,entry_until=0.,sound=NS(set_playing=Mock(),events=Mock()),
             renderer=NS(event=Mock(),update_effects=Mock()),canvas=NS(queue_draw=Mock()),tick_ms=deque(),metrics_writer=None)
        p.game=NS(state='running',elapsed=0.,drain_events=lambda:[],snapshot=lambda:{})
        p.game.update=lambda dt,_:setattr(p.game,'elapsed',p.game.elapsed+dt)
        for n in range(1,81):
            clock[0]=10+n*.05;p.latest=packet(clock[0],points17=[]);tick(p)
        self.assertGreater(p.game.elapsed,3.9);self.assertFalse(p.tracking_paused)
        self.assertTrue(all(c.args[0] for c in p.sound.set_playing.call_args_list))
        self.assertFalse(p.control.get('item'));self.assertEqual(p.control.get('steer'),0)

    def test_recovered_fruit_wrist_does_not_reuse_pre_gap_collision_path(self):
        local=ROOT.parent/'standalone/fruit-slice/native/engine.py'
        source=local if local.is_file() else Path('/home/ubuntu/fruit-slice/native/engine.py')
        spec=importlib.util.spec_from_file_location('fruit_gap_engine',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        game=module.Game();game.state='playing';game.elapsed=10.
        consumer=module.PoseInput(game);points=[[.5,.5,0.]]*17
        points[9]=[.4,.5,.9];points[10]=[.6,.5,.9]
        state=packet(10.,points17=points);consumer.consume(state)
        before=dict(game.paths);self.assertEqual(set(before),{'left','right'})
        self.assertEqual(consumer.consume(state),[]);self.assertEqual(game.paths,before)
        missing=packet(10.1,points17=[]);consumer.consume(missing)
        self.assertEqual(game.paths,{})


if __name__=='__main__':unittest.main()
