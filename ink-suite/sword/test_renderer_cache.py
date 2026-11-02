"""HUD reuse must be pixel-identical to a fresh transparent layer each update.

Uses only generated scene state, no GTK/display/camera. Run on the board to
exercise the same NEON source cache used in production; Cairo fallback is also
checked, including score/life/energy decreases and resolution changes.
"""
from copy import deepcopy
import math
import os
import unittest
from unittest.mock import patch

try:
    import cairo
    from sword.renderer import Renderer
except ModuleNotFoundError as error:
    if error.name != 'cairo':
        raise
    cairo = None


@unittest.skipIf(cairo is None, 'Requires Cairo; no X display is necessary')
class RendererCacheTests(unittest.TestCase):
    @staticmethod
    def scene(index):
        # Every scene changes the old HUD cache key; the fresh-layer reference
        # therefore represents its previous allocation-per-update behavior.
        return dict(phase='playing',time=3.+index*1.05,duration=75.,score=1000-index*37,
            combo=(15,1,7,0)[index%4],energy=(1.,0.,.65,.1)[index%4],
            lives=(5,0,3,1)[index%4],scene=index%3,progress=index*.07,
            player=dict(x=.35,y=.47,angle=.17),entities=[],effects=[],
            special_time=0.,tracking=True,power=index%4+1,kills=index*3,graze=index*11,
            wave_name=('竹海纸鸢','云山妖印','天门决战')[index%3],boss_name='镇山印',boss_phase=index%3,
            bullets=[dict(x=.15+i*.02,y=.20+(i%5)*.03,r=.007,style=i%4) for i in range(20)],
            shots=[dict(x=.33+i*.014,y=.55,r=.005) for i in range(4)])

    @staticmethod
    def pixels(surface):
        surface.flush()
        data=bytes(surface.get_data())
        return data[0::4]+data[1::4]+data[2::4]

    def compare_layers(self, native):
        class FreshHudRenderer(Renderer):
            def _hud(self,context,snapshot,ui):
                self.hud_surface=None
                self.hud_key=None
                super()._hud(context,snapshot,ui)

        with patch.dict(os.environ,{'INK_SWORD_DISABLE_NEON':'0' if native else '1'}):
            reused, fresh=Renderer(),FreshHudRenderer()
        if native and not reused.blitter.enabled:
            self.skipTest('Native source-cache coverage requires the compiled AArch64 helper')
        width,height=800,432
        a=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
        b=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
        first_hud=None
        for index in range(12):
            snapshot=self.scene(index)
            reused.draw(cairo.Context(a),width,height,deepcopy(snapshot))
            fresh.draw(cairo.Context(b),width,height,deepcopy(snapshot))
            self.assertEqual(self.pixels(a),self.pixels(b),'stale pixels at HUD update '+str(index))
            if first_hud is None:
                first_hud=reused.hud_surface
            self.assertIs(reused.hud_surface,first_hud)
        self.assertEqual(reused.hud_surface_allocations,1)
        self.assertEqual(fresh.hud_surface_allocations,12)
        if native:
            self.assertGreater(reused.blitter.native_calls,0)
            cached=[entry[0] for entry in reused.blitter._sources.values()]
            self.assertEqual(sum(item is first_hud for item in cached),1)

    def test_reused_hud_matches_fresh_pixels_with_cairo(self):
        self.compare_layers(False)

    def test_reused_hud_matches_fresh_pixels_with_native_source_cache(self):
        self.compare_layers(True)

    def test_hud_allocation_changes_only_for_canvas_resize(self):
        renderer=Renderer()
        for index,(width,height) in enumerate(((800,432),(800,432),(1024,552),(1024,552),(800,432))):
            target=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            renderer.draw(cairo.Context(target),width,height,self.scene(index))
            self.assertEqual(renderer.hud_surface_allocations,(1,1,2,2,3)[index])
            self.assertEqual((renderer.hud_surface.get_width(),renderer.hud_surface.get_height()),(width,height))

    def compare_partial_hud_to_full_clear(self,native):
        class FullClearHudRenderer(Renderer):
            def _paint_hud_tile(self,*args):
                return False  # Previous general Cairo/NEON adapter path.
            def _hud_context(self):
                context=cairo.Context(self.hud_surface)
                context.set_operator(cairo.OPERATOR_CLEAR);context.paint()
                context.set_operator(cairo.OPERATOR_OVER)
                context.scale(self.size[0]/1280,self.size[1]/720)
                return context
        with patch.dict(os.environ,{'INK_SWORD_DISABLE_NEON':'0' if native else '1'}):
            partial,reference=Renderer(),FullClearHudRenderer()
        if native and not partial.blitter.enabled:self.skipTest('AArch64 helper required')
        for width,height in ((800,432),(1024,556),(1280,720)):
            first=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            second=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            for index in range(8):
                snapshot=self.scene(index)
                if index in (1,2,5):
                    snapshot['entities']=[dict(kind='boss',x=.5,y=.24,r=.073,
                                              health=100-index*11,max_health=100)]
                partial.draw(cairo.Context(first),width,height,deepcopy(snapshot))
                reference.draw(cairo.Context(second),width,height,deepcopy(snapshot))
                self.assertEqual(self.pixels(first),self.pixels(second),(width,height,index))

    def test_partial_hud_clear_matches_full_reference_cairo(self):
        self.compare_partial_hud_to_full_clear(False)

    def test_partial_hud_clear_matches_full_reference_neon(self):
        self.compare_partial_hud_to_full_clear(True)

    def test_panorama_buffer_reused_and_matches_fresh_opaque_paint(self):
        renderer=Renderer()
        expected_allocations=0
        for width,height in ((800,432),(901,503),(1280,720)):
            expected_allocations+=1
            actual=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            expected=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
            for index,(phase,special) in enumerate((('playing',0),('playing',1),('ready',0),('won',0),('playing',0))):
                snapshot=dict(time=index*9.17,duration=120,phase=phase,special_time=special)
                renderer._background(cairo.Context(actual),width,height,snapshot)
                shade=.22 if phase in ('ready','won','lost') else .12 if special else None
                panorama=renderer.shaded_backgrounds.get(shade,renderer.background)
                pan=(renderer.background.get_width()-width)*snapshot['time']/120
                context=cairo.Context(expected)
                context.set_source_surface(panorama,-round(pan),0);context.paint()
                self.assertEqual(self.pixels(actual),self.pixels(expected))
                self.assertEqual(renderer.viewport_surface_allocations,expected_allocations)

    def test_real_seal_size_and_all_visual_angles_are_warmed(self):
        renderer=Renderer()
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,800,432)
        context=cairo.Context(target)
        renderer.draw(context,800,432,self.scene(0))
        before=set(renderer.cache)
        context.scale(800/1280,432/720)
        for step in range(-2,3):
            renderer._sprite(context,'seal',600,300,.04*720*2.15,step*math.pi/90)
        self.assertEqual(before,set(renderer.cache),'ordinary seals still trigger cold rasterization')
        self.assertTrue(any(key[0]=='seal' and key[1]==60 for key in before))

    def test_new_gameplay_fields_invalidate_hud_independently(self):
        renderer=Renderer()
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,800,432)
        snapshot=self.scene(0)
        renderer.draw(cairo.Context(target),800,432,snapshot)
        previous=renderer.hud_key
        for field,value in [('power',4),('kills',91),('graze',80),('wave_name','天门决战')]:
            snapshot[field]=value
            renderer.draw(cairo.Context(target),800,432,snapshot)
            self.assertNotEqual(renderer.hud_key,previous,field)
            previous=renderer.hud_key
        snapshot['entities']=[dict(kind='boss',x=.5,y=.2,r=.10,health=100,max_health=100)]
        renderer.draw(cairo.Context(target),800,432,snapshot)
        self.assertNotEqual(renderer.hud_key,previous)
        previous=renderer.hud_key
        snapshot['entities'][0]['health']=20
        renderer.draw(cairo.Context(target),800,432,snapshot)
        self.assertNotEqual(renderer.hud_key,previous)
        snapshot['entities']=[]
        renderer.draw(cairo.Context(target),800,432,snapshot)
        self.assertEqual(renderer.hud_surface_allocations,1)


if __name__=='__main__':
    unittest.main()
