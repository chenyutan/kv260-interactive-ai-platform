"""Pure UI regressions: real painters, synthetic state, no camera or hardware."""
import ast
import copy
import math
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import cairo

ROOT=Path(__file__).resolve().parents[1]

class FightCopyTests(unittest.TestCase):
    def scene(self):
        tree=ast.parse((ROOT/'native_windows/fight_scene.py').read_text('utf8'))
        statements=[n for n in tree.body if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('LABELS','LEGACY_MOVE_NAMES') for t in n.targets)) or isinstance(n,ast.FunctionDef) and n.name=='move_label']
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Scene')
        cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='paint']
        statements.append(cls);ns=dict(cairo=cairo,math=math)
        exec(compile(ast.Module(body=statements,type_ignores=[]),'fight_scene.py','exec'),ns)
        s=ns['Scene']();s.size=(960,540);s.s=.75;s.ox=s.oy=0
        s.surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);s.background=bytes(s.surface.get_data())
        s.character=lambda *args:430;s.labels=[];s.text=lambda c,label,*a,**kw:s.labels.append(label)
        return s,ns
    def game(self):
        return NS(state='playing',remaining=60.,hp=100,enemy_hp=100,energy=100,
                  attack={'move':'special','name':'山河归一'},enemy_attack=None,combo=0,effects=[])
    def paint(self,scene,g,**kwargs):
        before=copy.deepcopy(vars(g))
        with patch.dict(sys.modules,{'battle_fx':NS(paint=Mock())}):scene.paint(g,960,540,**kwargs)
        self.assertEqual(vars(g),before)
    def test_engine_legacy_name_cannot_override_plain_ui_name(self):
        scene,ns=self.scene();self.paint(scene,self.game())
        self.assertIn('必杀技',scene.labels);self.assertIn('必杀技已就绪',scene.labels)
        self.assertNotIn('山河归一',scene.labels)
        for key,name in ns['LABELS'].items():
            self.assertEqual(ns['move_label']({'move':key,'name':'旧招式'}),name)
        self.assertEqual(ns['move_label']({'name':'流云拳'}),'左拳')
    def test_playfield_has_no_permanent_instruction_footer_but_errors_remain(self):
        scene,_=self.scene();g=self.game()
        self.paint(scene,g,status='收拳再伸拳｜屈肘上挑｜双掌推出｜举臂防御')
        self.assertFalse(any('｜' in s or '横移进退' in s for s in scene.labels))
        self.paint(scene,g,status='手柄未连接')
        self.assertIn('手柄未连接',scene.labels)
    def test_result_contains_no_obscure_epilogue(self):
        scene,_=self.scene();g=self.game();g.state='over';g.result='落败';g.hits=4;g.blocks=2
        self.paint(scene,g);self.assertIn('失败',scene.labels)
        self.assertFalse(any('此局已定' in s or '再战' in s for s in scene.labels))

class RunnerClearHudTests(unittest.TestCase):
    def test_running_hud_bottom_band_is_transparent_and_snapshot_unchanged(self):
        from runner.native_render import HudLayer
        hud=HudLayer();snapshot=dict(state='running',score=123,coins=3,distance=20.,
            lives=3,ui=dict(control_mode='gamepad'))
        before=copy.deepcopy(snapshot);hud._draw(snapshot)
        raw=bytes(hud.surface.get_data());stride=hud.surface.get_stride()
        self.assertFalse(any(raw[493*stride:530*stride]))
        self.assertEqual(snapshot,before)
    def test_title_has_start_action_but_no_movement_tutorial(self):
        from runner.native_render import HudLayer
        hud=HudLayer();labels=[];original=hud.text
        def text(cr,label,*a,**kw):labels.append(label);return original(cr,label,*a,**kw)
        hud.text=text;hud._draw(dict(state='title',ui=dict(control_mode='gamepad')))
        self.assertIn('按 X 开始',labels)
        self.assertFalse(any('左摇杆' in value or '逐路' in value for value in labels))

class SwordClearHudTests(unittest.TestCase):
    def test_ready_overlay_and_playing_hud_have_no_instruction_bars(self):
        from sword.renderer import Renderer
        from sword.test_renderer_cache import RendererCacheTests
        renderer=Renderer();labels=[];original=renderer._text
        def text(cr,label,*a,**kw):labels.append(str(label));return original(cr,label,*a,**kw)
        renderer._text=text;surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        for phase in ('ready','playing','won','lost'):
            labels.clear();state=RendererCacheTests.scene(0);state['phase']=phase
            renderer.draw(cairo.Context(surface),960,540,state,dict(control_mode='gamepad'))
            self.assertFalse(any('左摇杆移剑' in value or '满剑意' in value or '千剑' in value or '再入山河' in value for value in labels))
            if phase=='won':self.assertIn('胜利',labels)
            if phase=='lost':self.assertIn('游戏结束',labels)

class TaikoClearHudTests(unittest.TestCase):
    def test_live_control_instructions_removed_while_grade_feedback_remains(self):
        from taiko.renderer import Renderer
        from taiko.render_review import effect_state
        renderer=Renderer();labels=[];original=renderer.text
        def text(cr,label,*a,**kw):labels.append(str(label));return original(cr,label,*a,**kw)
        renderer.text=text;surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        renderer.draw(cairo.Context(surface),960,540,effect_state(),dict(control_mode='gamepad',hands={'L':.2},clap={'valid':True,'armed':True}))
        self.assertIn('完美',labels);self.assertIn('已击掌',labels)
        self.assertFalse(any('X 左鼓' in v or '掌形落点' in v or '向下击鼓' in v or '待合掌' in v for v in labels))

if __name__=='__main__':unittest.main()
