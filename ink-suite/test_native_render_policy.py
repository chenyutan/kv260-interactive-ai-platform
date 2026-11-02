"""Input, damage and saved-art invariants for capped native rendering."""
import ast
from pathlib import Path
from types import SimpleNamespace
import math,unittest
from unittest.mock import Mock
from render_policy import RenderPolicy

ROOT=Path(__file__).resolve().parent
def fruit_method(name):
    module=ast.parse((ROOT/'native_windows/fruit.py').read_text(encoding='utf-8'))
    window=next(n for n in module.body if isinstance(n,ast.ClassDef) and n.name=='Window')
    method=next(n for n in window.body if isinstance(n,ast.FunctionDef) and n.name==name)
    env={'math':math};exec(compile(ast.Module(body=[method],type_ignores=[]),'fruit-method','exec'),env)
    return env[name]

class NativeCoordinates(unittest.TestCase):
    def test_mouse_cuts_same_world_point_at_720p_with_cap(self):
        layout=RenderPolicy().layout(1280,720)
        app=SimpleNamespace(mouse=True,use_pose=False,render_layout=layout,
            origin=(0,0),scale=.75,game=SimpleNamespace(input=Mock()),trail=Mock())
        fruit_method('motion')(app,None,SimpleNamespace(x=960,y=360))
        app.game.input.assert_called_once_with('mouse',960.,360.)
    def test_damage_maps_outward_to_display_not_internal_pixels(self):
        app=SimpleNamespace(render_layout=RenderPolicy().layout(1280,720),area=SimpleNamespace(queue_draw_area=Mock()))
        fruit_method('invalidate_render')(app,100,90,60,30)
        x,y,w,h=app.area.queue_draw_area.call_args.args
        self.assertLessEqual(x,100*4/3);self.assertGreaterEqual(x+w,160*4/3)
        self.assertLessEqual(y,120);self.assertGreaterEqual(y+h,160)
    def test_standalone_damage_remains_native(self):
        app=SimpleNamespace(area=SimpleNamespace(queue_draw_area=Mock()))
        fruit_method('invalidate_render')(app,10,20,30,40)
        app.area.queue_draw_area.assert_called_once_with(10,20,30,40)

if __name__=='__main__':unittest.main()
