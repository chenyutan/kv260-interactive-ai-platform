"""Native menu AI entry rendering/input regression; private Xvfb, no devices."""
import importlib.util
import math
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
import numpy as np
import section_catalog as catalog
from section_menu import SectionMenuView


def pixels(surface):
    surface.flush()
    return np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(surface.get_height(),surface.get_stride())[:,:surface.get_width()*4].reshape(surface.get_height(),surface.get_width(),4)[:,:,:3].copy()


class AIEntryTests(unittest.TestCase):
    def setUp(self):
        self.chosen=[]
        self.menu=SectionMenuView(self.chosen.append,True)
        self.window=Gtk.Window();self.window.add(self.menu)
        self.window.resize(960,540);self.window.show_all()
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        self.out=Path(os.environ['AI_ENTRY_QA_DIR']) if os.environ.get('AI_ENTRY_QA_DIR') else None
        if self.out:self.out.mkdir(parents=True,exist_ok=True)

    def tearDown(self):
        self.window.destroy()

    def render(self,width=960,height=540,name=None):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
        self.menu.paint(cairo.Context(image),(width,height))
        if self.out and name:image.write_to_png(str(self.out/(name+'.png')))
        return image

    def test_unchanged_hitbox_and_no_back_overlap_all_sections(self):
        self.assertEqual(catalog.AI_RECT,(.510,.055,.715,.155))
        for width,height in ((960,540),(1280,720)):
            for section in catalog.SECTIONS:
                self.menu.set_section(section)
                index=self.menu.mode_choices.index('ai_assistant')
                expected=catalog.HOME_AI_RECT if section=='home' else catalog.AI_RECT
                self.assertEqual(self.menu.rectangles[index],expected)
                a,b,c,d=expected
                self.assertGreaterEqual((d-b)*height,54)
                self.assertAlmostEqual((c-a)*width,196.8*width/960)
                for px,py in (((a+c)/2,(b+d)/2),(a+3/width,b+3/height),(c-3/width,d-3/height)):
                    self.assertEqual(self.menu.hit_index(px,py),index)
                for other,rect in zip(self.menu.mode_choices,self.menu.rectangles):
                    if other=='ai_assistant':continue
                    e,f,g,h=rect
                    self.assertFalse(a<g and e<c and b<h and f<d,other)
                if section!='home':
                    self.assertIn('section_home',self.menu.mode_choices)
                    self.assertGreaterEqual((catalog.BACK_RECT[0]-c)*width,19)

    def test_ai_stays_clickable_without_pose_and_other_targets_unchanged(self):
        self.menu.set_availability(False,'等待识别服务')
        for section in catalog.SECTIONS:
            self.menu.set_section(section)
            a,b,c,d=catalog.HOME_AI_RECT if section=='home' else catalog.AI_RECT
            event=SimpleNamespace(button=1,x=(a+c)*self.menu.get_allocated_width()/2,y=(b+d)*self.menu.get_allocated_height()/2)
            self.assertTrue(self.menu.click(None,event));self.assertEqual(self.chosen[-1],'ai_assistant')
            if section!='home':
                e,f,g,h=catalog.BACK_RECT
                self.menu.click(None,SimpleNamespace(button=1,x=(e+g)*self.menu.get_allocated_width()/2,y=(f+h)*self.menu.get_allocated_height()/2))
                self.assertEqual(self.chosen[-1],'section_home')

    def test_static_entry_render_all_sections_sizes(self):
        for width,height in ((960,540),(1280,720)):
            for section in catalog.SECTIONS:
                self.menu.set_section(section)
                image=self.render(width,height,f'{section}-{width}')
                data=pixels(image);a,b,c,d=catalog.HOME_AI_RECT if section=='home' else catalog.AI_RECT
                crop=data[math.ceil(b*height)+3:math.floor(d*height)-3,math.ceil(a*width)+3:math.floor(c*width)-3]
                # The surface is BGR: blue-dark body plus a distinctly light title.
                self.assertGreater(float(crop[:,:,0].mean()),float(crop[:,:,2].mean())+12)
                self.assertGreater(np.count_nonzero((crop>205).all(axis=2)),80)
                self.assertEqual(self.menu.mode_choices[self.menu.hit_index((a+c)/2,(b+d)/2)],'ai_assistant')

    def test_ai_focus_moves_off_entry_and_does_not_rebuild_cache(self):
        self.menu.set_section('creation')
        baseline=pixels(self.render(name='creation-idle'))
        builds=self.menu.cache_builds
        ai=self.menu.mode_choices.index('ai_assistant')
        a,b,c,d=catalog.AI_RECT
        for sample,progress in enumerate((.1,.4,.8),1):
            self.menu.update_selection(dict(cursor=((a+c)/2,(b+d)/2),hover=ai,progress=progress,sample_key=('body',sample)))
            self.menu.pointer=None;self.menu.trail.clear()  # compare focus, not ink cursor pixels
            self.render(name='creation-ai-focus' if sample==3 else None)
            self.assertEqual(self.menu.hover,ai);self.assertEqual(self.menu.progress,progress)
        self.assertGreater(np.count_nonzero(pixels(self.render())!=baseline),0)
        e,f,g,h=catalog.BACK_RECT
        self.menu.update_selection(dict(cursor=((e+g)/2,(f+h)/2),hover=self.menu.mode_choices.index('section_home'),progress=.6,sample_key=('pad',4)))
        self.menu.pointer=None;self.menu.trail.clear()
        moved=pixels(self.render(name='creation-back-focus'))
        self.assertTrue(np.array_equal(moved[math.floor(b*540)-2:math.ceil(d*540)+2,math.floor(a*960)-2:math.ceil(c*960)+2],baseline[math.floor(b*540)-2:math.ceil(d*540)+2,math.floor(a*960)-2:math.ceil(c*960)+2]))
        self.assertEqual(self.menu.cache_builds,builds)
        self.assertEqual(self.chosen,[])

    def test_other_menu_pixels_identical_to_verified_baseline(self):
        baseline_path=os.environ.get('AI_ENTRY_BASELINE')
        if not baseline_path:self.skipTest('Set AI_ENTRY_BASELINE to the copied original source for pixel comparison')
        spec=importlib.util.spec_from_file_location('_ai_entry_baseline',baseline_path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        # Resolve the exact same assets as production; only source differs.
        module.__file__=str(Path(__file__).with_name('section_menu.py'))
        original=module.SectionMenuView(lambda _:None,True)
        try:
            for width,height in ((960,540),(1280,720)):
                for section in catalog.SECTIONS:
                    self.menu.set_section(section);original.set_section(section)
                    current=pixels(self.render(width,height))
                    reference=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
                    original.paint(cairo.Context(reference),(width,height));old=pixels(reference)
                    a,b,c,d=catalog.AI_RECT;mask=np.ones((height,width),dtype=bool)
                    mask[math.floor(b*height)-2:math.ceil(d*height)+2,math.floor(a*width)-2:math.ceil(c*width)+2]=False
                    if section=='home':
                        # The home-only brand marks intentionally replace the
                        # old seal. Every pixel outside this header slot and
                        # the AI entry must still match the deployed baseline.
                        mask[:math.ceil(.207*height)+2]=False
                    self.assertTrue(np.array_equal(current[mask],old[mask]),(section,width))
        finally:original.destroy()


if __name__=='__main__':unittest.main()
