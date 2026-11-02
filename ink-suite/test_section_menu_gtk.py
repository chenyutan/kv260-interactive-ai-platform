"""Actual GTK/Cairo menu checks; run under Xvfb on a GTK-capable system."""
import math,time,unittest
from types import SimpleNamespace
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
import section_catalog as catalog
from section_menu import SectionMenuView


class NativeSectionTests(unittest.TestCase):
    def setUp(self):
        self.chosen=[];self.window=Gtk.Window();self.menu=SectionMenuView(self.chosen.append,True)
        self.window.add(self.menu);self.window.resize(960,540);self.window.show_all()
        while Gtk.events_pending():Gtk.main_iteration_do(False)
    def tearDown(self):self.window.destroy()
    def test_native_each_page_draws_and_each_visible_entry_selects(self):
        for section in catalog.SECTIONS:
            self.menu.set_section(section)
            image=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
            self.menu.draw(self.menu,cairo.Context(image))
            self.assertEqual((self.menu.backbuffer.get_width(),self.menu.backbuffer.get_height()),(960,540))
            self.assertEqual(self.menu.cache_key,(960,540,self.menu.message,section))
            for choice,(a,b,c,d) in zip(self.menu.mode_choices,self.menu.rectangles):
                self.menu.click(self.menu,SimpleNamespace(button=1,x=(a+c)*self.menu.get_allocated_width()/2,y=(b+d)*self.menu.get_allocated_height()/2))
                self.assertEqual(self.chosen[-1],choice)
    def test_native_pointer_does_not_rebuild_static_cache(self):
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);cr=cairo.Context(image)
        self.menu.draw(self.menu,cr);builds=self.menu.cache_builds
        for key in range(8):
            self.menu.update_selection(dict(cursor=(.2+.01*key,.4),hover=0,progress=key/10,sample_key=key))
            self.menu.animate(time.monotonic());self.menu.draw(self.menu,cr)
        self.assertEqual(self.menu.cache_builds,builds)
        self.assertEqual(self.chosen,[])
    def test_native_page_change_clears_unfinished_selection(self):
        self.menu.update_selection(dict(cursor=(.2,.4),hover=0,progress=.99,sample_key=1))
        self.menu.set_section('health')
        self.assertIsNone(self.menu.pointer);self.assertIsNone(self.menu.hover)
        self.assertEqual(self.menu.progress,0.)
        self.assertEqual(self.menu.mode_choices[0],'baduanjin')
    def test_native_disabled_course_and_public_tools(self):
        self.menu.set_section('health');self.menu.set_availability(False,'正在等待识别服务')
        for choice in self.menu.mode_choices:
            index=self.menu.mode_choices.index(choice);a,b,c,d=self.menu.rectangles[index]
            old=len(self.chosen)
            clicked=self.menu.click(self.menu,SimpleNamespace(button=1,x=(a+c)*self.menu.get_allocated_width()/2,y=(b+d)*self.menu.get_allocated_height()/2))
            self.assertEqual(clicked,catalog.available_without_pose(choice))
            self.assertEqual(len(self.chosen),old+int(catalog.available_without_pose(choice)))
    def test_native_high_resolution_has_bounded_backbuffer(self):
        for w,h in ((1280,720),(1920,1080)):
            self.window.resize(w,h)
            while Gtk.events_pending():Gtk.main_iteration_do(False)
            image=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
            self.menu.draw(self.menu,cairo.Context(image))
            self.assertLessEqual(self.menu.backbuffer.get_width(),960)
            self.assertLessEqual(self.menu.backbuffer.get_height(),540)

    def test_native_progress_damage_matches_full_presentation_at_1080p(self):
        import numpy as np
        self.window.resize(1920,1080)
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        width,height=self.menu.get_allocated_width(),self.menu.get_allocated_height()
        shown=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height);shown_cr=cairo.Context(shown)
        self.menu.draw(self.menu,shown_cr)
        for key,progress in ((1,.2),(2,.6),(3,.9)):
            damage=[];self.menu.invalidate=damage.append
            self.menu.update_selection(dict(cursor=(.2,.4),hover=0,progress=progress,sample_key=key))
            self.menu.animate(time.monotonic())
            self.assertTrue(damage)
            shown_cr.save()
            for x,y,w,h in damage:shown_cr.rectangle(math.floor(x),math.floor(y),math.ceil(w)+2,math.ceil(h)+2)
            shown_cr.clip();self.menu.draw(self.menu,shown_cr);shown_cr.restore()
            reference=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            self.menu.draw(self.menu,cairo.Context(reference))
            shown.flush();reference.flush()
            def pixels(surface):return np.frombuffer(surface.get_data(),dtype=np.uint8).reshape(height,surface.get_stride())[:,:width*4].reshape(height,width,4)[:,:,:3]
            delta=np.abs(pixels(shown).astype(np.int16)-pixels(reference).astype(np.int16))
            self.assertLessEqual(int(delta.max()),2)

if __name__=='__main__':unittest.main()
