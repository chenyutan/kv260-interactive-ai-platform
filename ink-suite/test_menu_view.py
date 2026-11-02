"""Menu cache, visual freshness and hidden-page regression under Xvfb."""
import math
import unittest
from types import SimpleNamespace
import cairo
import numpy as np
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
import menu_view


def assert_damage_presentation(case,partial,reference,rectangles):
    """No stale pixels; bounded Cairo bilinear clip rounding is permitted."""
    partial.flush();reference.flush()
    w,h=partial.get_width(),partial.get_height()
    def pixels(surface):
        return np.frombuffer(surface.get_data(),dtype=np.uint8).reshape(h,surface.get_stride())[:,:w*4].reshape(h,w,4)[:,:,:3]
    # RGB24's fourth byte is unused. Compare all displayed colour components.
    delta=np.abs(pixels(partial).astype(np.int16)-pixels(reference).astype(np.int16))
    different=np.any(delta!=0,axis=2);dirty=np.zeros((h,w),dtype=bool)
    for x,y,rw,rh in rectangles:
        left,top=math.floor(x),math.floor(y)
        right,bottom=left+math.ceil(rw)+2,top+math.ceil(rh)+2
        dirty[max(0,top):min(h,bottom),max(0,left):min(w,right)]=True
    case.assertEqual(int((different&~dirty).sum()),0,'Pixels outside damage changed or retained an old brush')
    layout=case.menu.render_policy.layout(w,h)
    # pixman/Cairo quantizes bilinear samples slightly differently when the
    # target has disjoint clips. The synthetic board probe measured <=2/255
    # RGB difference, entirely inside damage; uncapped 1:1 blits stay exact.
    tolerance=2 if layout.render_size!=layout.output_size else 0
    case.assertLessEqual(int(delta.max()),tolerance,'Visible clipped presentation differs from complete frame')


class RendererTests(unittest.TestCase):
    def test_unready_cards_do_not_select_and_clear_old_dwell(self):
        self.send(1);self.advance(.03)
        self.menu.set_availability(False,'正在等待摄像头与识别数据…')
        x,y,_=menu_view.geometry(self.menu.get_allocated_width(),self.menu.get_allocated_height())[1][1]
        self.menu.click(self.menu,SimpleNamespace(button=1,x=x,y=y))
        self.assertEqual(self.chosen,[]);self.assertIsNone(self.menu.pointer)
        self.assertIsNone(self.menu.hover);self.assertEqual(self.menu.progress,0.)
        self.menu.set_availability(True,'识别已就绪')
        self.menu.click(self.menu,SimpleNamespace(button=1,x=x,y=y))
        self.assertEqual(self.chosen,['sword'])
    def test_second_card_selects_sword(self):
        self.assertEqual(menu_view.TITLES[1],'御剑山河')
        x,y,_=menu_view.geometry(self.menu.get_allocated_width(),self.menu.get_allocated_height())[1][1]
        self.menu.click(self.menu,SimpleNamespace(button=1,x=x,y=y))
        self.assertEqual(self.chosen,['sword'])
    def test_every_visible_card_uses_the_dwell_geometry(self):
        from radial_layout import MODES
        for mode,(x0,y0,x1,y1) in zip(MODES,self.menu.rectangles):
            self.menu.click(self.menu,SimpleNamespace(button=1,
                x=self.menu.get_allocated_width()*(x0+x1)/2,
                y=self.menu.get_allocated_height()*(y0+y1)/2))
            self.assertEqual(self.chosen[-1],mode)
    def test_circle_corner_is_inert_and_center_opens_camera_tool(self):
        a,b,c,d=self.menu.rectangles[1]
        for x,y in ((a,b),(.5,.49)):
            self.menu.click(self.menu,SimpleNamespace(button=1,x=x*self.menu.get_allocated_width(),y=y*self.menu.get_allocated_height()))
        self.assertEqual(self.chosen,['pose_view'])
    def setUp(self):
        self.clock=100.;self.original_time=menu_view.time
        menu_view.time=SimpleNamespace(monotonic=lambda:self.clock)
        self.chosen=[];self.window=Gtk.Window();self.menu=menu_view.MenuView(self.chosen.append)
        self.window.add(self.menu);self.window.resize(1024,550);self.window.show_all()
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        self.surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,550);self.cr=cairo.Context(self.surface)
    def tearDown(self):
        self.window.destroy();menu_view.time=self.original_time
    def send(self,key,point=(.2,.3),progress=.25):
        self.menu.update_selection(dict(cursor=point,hover=0,progress=progress,sample_key=key))
    def advance(self,seconds):
        self.clock+=seconds;self.menu.animate(self.clock)
    def test_cache_and_visual_only_smoothing(self):
        self.menu.draw(self.menu,self.cr);builds=self.menu.cache_builds
        self.send(1);self.advance(.03)
        self.send(2,(.4,.4));self.advance(.03)
        self.assertGreater(self.menu.pointer[0],.2);self.assertLess(self.menu.pointer[0],.4)
        self.assertGreaterEqual(len(self.menu.trail),2)
        for _ in range(6):self.advance(.03);self.menu.draw(self.menu,self.cr)
        self.assertEqual(self.menu.cache_builds,builds)
        self.assertEqual(self.menu.progress,.25);self.assertEqual(self.chosen,[])
        self.menu.message+=' 测试';self.menu.draw(self.menu,self.cr)
        self.assertEqual(self.menu.cache_builds,builds+1)
    def test_duplicate_input_expires_without_phantom_trail(self):
        self.send(8);self.advance(.03)
        for _ in range(10):self.send(8);self.advance(.03)
        self.assertIsNone(self.menu.pointer);self.assertFalse(self.menu.trail)
        self.assertIsNone(self.menu.hover);self.assertEqual(self.menu.progress,0.)
        self.send(9,(.8,.8));self.advance(.03)
        self.assertEqual(len(self.menu.trail),1)
    def test_cleared_pointer_does_not_reappear_from_same_observation(self):
        self.send(8);self.advance(.03)
        self.menu.update_selection({});self.advance(.03)
        self.send(8);self.advance(.03)
        self.assertIsNone(self.menu.pointer)
        self.send(9);self.advance(.03)
        self.assertIsNotNone(self.menu.pointer)
    def test_disconnect_and_hidden_page_stop_drawing(self):
        self.send(1);self.advance(.03);self.send(2,(.4,.4));self.advance(.03)
        self.menu.update_selection({});self.advance(.03)
        self.assertIsNone(self.menu.pointer);self.assertFalse(self.menu.trail)
        self.menu.hide();self.assertIsNone(self.menu.timer)
        invalidations=[];self.menu.invalidate=invalidations.append
        self.send(3);self.advance(.03)
        self.assertEqual(invalidations,[])
        self.menu.show();self.assertIsNotNone(self.menu.timer)
    def test_brush_composites_into_opaque_image_before_display(self):
        painted=[];buffers=[];original=self.menu.paint
        def observe(cr,*args):
            painted.append(cr.get_target());buffers.append(self.menu.backbuffer);return original(cr,*args)
        self.menu.paint=observe
        self.send(1);self.advance(.03);self.menu.draw(self.menu,self.cr)
        self.send(2,(.4,.4));self.advance(.03);self.menu.draw(self.menu,self.cr)
        self.assertTrue(painted)
        self.assertTrue(all(isinstance(surface,cairo.ImageSurface) for surface in painted))
        self.assertTrue(all(surface.get_format()==cairo.FORMAT_RGB24 for surface in painted))
        self.assertIs(buffers[0],buffers[-1])
    def test_partial_brush_and_expiry_match_full_repaint(self):
        self.send(1);self.advance(.03);self.menu.draw(self.menu,self.cr)
        for key,point in ((2,(.4,.4)),(3,(.44,.48)),(4,None)):
            rectangles=[];self.menu.invalidate=rectangles.append
            self.send(key,point);self.advance(.03)
            self.assertTrue(rectangles)
            self.cr.save()
            # Gtk damage uses the integer-expanded rectangles from invalidate.
            for x,y,w,h in rectangles:self.cr.rectangle(math.floor(x),math.floor(y),math.ceil(w)+2,math.ceil(h)+2)
            self.cr.clip();self.menu.draw(self.menu,self.cr);self.cr.restore()
            bounded_frame=bytes(self.menu.backbuffer.get_data())
            reference=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,550)
            # Compare two complete presentation paths, including the bounded
            # composition and final scale; paint alone omits that final scale.
            self.menu.draw(self.menu,cairo.Context(reference))
            self.assertEqual(bounded_frame,bytes(self.menu.backbuffer.get_data()),'Clipped draw changed the bounded composition')
            assert_damage_presentation(self,self.surface,reference,rectangles)


if __name__=='__main__':unittest.main()
