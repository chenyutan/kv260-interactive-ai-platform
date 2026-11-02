"""State/click regression with a lightweight base; no visual or GTK claims."""
import importlib.util
from collections import deque
from pathlib import Path
from types import ModuleType,SimpleNamespace
import sys,unittest
from unittest.mock import patch


class Base:
    def __init__(self,choose,visitor_controls=False):
        self.choose=choose;self.visitor_controls=visitor_controls;self.pointer=self.target=None
        self.selection_enabled=True;self.trail=deque();self.hover=None;self.progress=0.
        self.sample_key=None;self.dirty_previous=[];self.cache_key='old';self.static_surface='old'
    def clear_pointer(self):self.pointer=self.target=None;self.trail.clear()
    def set_size_request(self,*_):pass
    def queue_draw(self):self.queued=True
    def get_allocated_width(self):return 960
    def get_allocated_height(self):return 540


fake_menu=ModuleType('menu_view');fake_menu.MenuView=Base;fake_menu.text=lambda *args,**kwargs:None
fake_cairo=ModuleType('cairo')
spec=importlib.util.spec_from_file_location('_section_menu_state_test',Path(__file__).with_name('section_menu.py'))
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'menu_view':fake_menu,'cairo':fake_cairo}):spec.loader.exec_module(module)


class StateTests(unittest.TestCase):
    def setUp(self):self.chosen=[];self.menu=module.SectionMenuView(self.chosen.append,True)
    def click_choice(self,choice):
        index=self.menu.mode_choices.index(choice);a,b,c,d=self.menu.rectangles[index]
        return self.menu.click(None,SimpleNamespace(button=1,x=(a+c)*480,y=(b+d)*270))
    def test_page_change_clears_prior_pointer_dwell_damage_and_cache(self):
        self.menu.pointer=(.5,.5);self.menu.target=(.6,.6);self.menu.trail.append((.5,.5,1))
        self.menu.hover=1;self.menu.progress=.95;self.menu.sample_key=17;self.menu.dirty_previous=[(0,0,10,10)]
        self.assertTrue(self.menu.set_section('health'))
        self.assertIsNone(self.menu.pointer);self.assertIsNone(self.menu.target)
        self.assertFalse(self.menu.trail);self.assertIsNone(self.menu.hover);self.assertEqual(self.menu.progress,0.)
        self.assertIsNone(self.menu.sample_key);self.assertFalse(self.menu.dirty_previous)
        self.assertIsNone(self.menu.cache_key);self.assertIsNone(self.menu.static_surface)
        self.assertIsNone(self.menu.previous_hover);self.assertEqual(self.menu.previous_progress,0.)
    def test_camera_unready_still_allows_sections_and_public_tools(self):
        self.menu.selection_enabled=False
        for choice in ('section_health','pose_view','visitor_download','visitor_finish'):self.assertTrue(self.click_choice(choice))
        self.assertEqual(self.chosen,['section_health','pose_view','visitor_download','visitor_finish'])
        self.menu.set_section('health');self.assertTrue(self.click_choice('baduanjin'))
        self.assertTrue(self.click_choice('section_home'))
        self.menu.set_section('entertainment');self.assertFalse(self.click_choice('fruit'))
    def test_ready_can_choose_course_and_existing_game(self):
        self.menu.set_section('health');self.assertTrue(self.click_choice('baduanjin'))
        self.menu.set_section('entertainment');self.assertTrue(self.click_choice('fruit'))
        self.assertEqual(self.chosen,['baduanjin','fruit'])
    def test_invalid_section_is_atomic_and_does_not_mutate_page(self):
        before=self.menu.mode_choices
        with self.assertRaises(ValueError):self.menu.set_section('wrong')
        self.assertEqual(self.menu.section,'home');self.assertEqual(self.menu.mode_choices,before)
    def test_non_primary_click_and_margins_are_inert(self):
        self.assertFalse(self.menu.click(None,SimpleNamespace(button=3,x=300,y=300)))
        self.assertFalse(self.menu.click(None,SimpleNamespace(button=1,x=0,y=0)))
        self.assertEqual(self.chosen,[])

if __name__=='__main__':unittest.main()
