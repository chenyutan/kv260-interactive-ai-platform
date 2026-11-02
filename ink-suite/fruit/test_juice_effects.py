import math
import unittest
from types import SimpleNamespace
import cairo
from fruit.juice_effects import (CutEffects,ItemEffects,MAX_JUICE_HITS,
    DROPS_PER_HIT,MAX_PARTICLES,MAX_CACHE_BYTES,MAX_JUICE_CACHE_BYTES,MAX_BACKGROUND_BYTES,SPLASH_LIFETIME,BURST_FRAMES)
from fruit.render_regions import merge_damage,intersects,apply_clip,clip_damage


class JuiceTests(unittest.TestCase):
    def test_dense_effect_population_and_lifetime_are_bounded(self):
        fx=CutEffects()
        for i in range(800):
            fx.trigger(dict(kind='normal',fruit='watermelon',x=640,y=360,combo=i,cut_angle=0.))
        self.assertEqual(len(fx.items),MAX_JUICE_HITS)
        self.assertEqual(len(fx.items)*DROPS_PER_HIT,MAX_PARTICLES)
        fx.advance(SPLASH_LIFETIME+.01)
        self.assertEqual(fx.items,[])

    def test_normal_effect_does_not_consume_item_events(self):
        fx=CutEffects();fx.trigger(dict(kind='bomb'))
        self.assertEqual(fx.items,[])

    def test_particle_damage_covers_trajectories(self):
        fx=CutEffects();fx.trigger(dict(kind='normal',fruit='peach',x=500,y=400))
        for frame in range(100):
            t=SPLASH_LIFETIME*frame/100
            fx.items[0]['age']=t;x,y,w,h=fx.bounds()[0]
            t=(min(BURST_FRAMES-1,int(t/SPLASH_LIFETIME*BURST_FRAMES))+.5)/BURST_FRAMES*SPLASH_LIFETIME
            for i in range(DROPS_PER_HIT):
                a=i*2.399963+.31;speed=120+(i%3)*54
                px=500+math.cos(a)*speed*t;py=400+math.sin(a)*speed*t*.77+86*t*t
                self.assertTrue(x<=px-12<=px+12<=x+w)
                self.assertTrue(y<=py-12<=py+12<=y+h)

    def test_special_hits_do_not_stack_without_bound(self):
        fx=ItemEffects()
        for i in range(300):
            fx.trigger(dict(kind=('ice','double','bomb')[i%3],x=600,y=360,delta=-30))
        self.assertEqual(len(fx.items),3)
        fx.advance(2.)
        self.assertEqual(fx.items,[])

    def test_gold_twinkles_continue_after_hit_for_original_multiplier_duration(self):
        game=SimpleNamespace(state='playing',elapsed=2.,freeze_until=0.,double_until=10.)
        fx=ItemEffects();fx.prepare(.75)
        fx.trigger(dict(kind='double',x=640,y=360));fx.advance(2.);fx.sync(game)
        self.assertEqual(fx.items,[])
        self.assertEqual(len(fx.bounds()[:-1]),8)
        self.assertLess(sum(w*h for _,_,w,h in fx.bounds()[:-1]),1280*720*.01)
        frames=[]
        for at in (2.,2.125):
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);c=cairo.Context(surface);c.scale(.75,.75)
            game.elapsed=at;fx.sync(game);fx.paint(c);surface.flush();frames.append(bytes(surface.get_data()))
        self.assertNotEqual(*frames)
        game.elapsed=10.;fx.sync(game);self.assertEqual(fx.bounds(),[])

    def test_item_status_uses_original_clocks_and_does_not_mutate_game(self):
        game=SimpleNamespace(state='playing',elapsed=2.,freeze_until=7.,double_until=10.)
        original=vars(game).copy();fx=ItemEffects();fx.sync(game)
        self.assertEqual(fx.background_key(),(True,True))
        self.assertEqual(vars(game),original)
        game.elapsed=8.;fx.sync(game);self.assertEqual(fx.background_key(),(False,True))
        game.state='over';fx.sync(game);self.assertEqual(fx.background_key(),(False,False))

    def test_cache_budget_and_reuse(self):
        fx=CutEffects();fx.prepare(.75)
        first=fx.cache.allocations
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);c=cairo.Context(surface);c.scale(.75,.75)
        fx.trigger(dict(kind='normal',fruit='watermelon',x=600,y=360))
        for i in range(20):fx.paint(c)
        self.assertEqual(fx.cache.allocations,first)
        self.assertLessEqual(fx.cache.bytes,MAX_JUICE_CACHE_BYTES)
        items=ItemEffects();items.prepare(.75)
        for kind in ('ice','double','bomb'):
            items.trigger(dict(kind=kind,x=640,y=360));items.paint(c)
        self.assertLessEqual(items.cache.bytes,MAX_CACHE_BYTES)
        fx.prepare(.5);self.assertLessEqual(fx.cache.bytes,MAX_JUICE_CACHE_BYTES)

    def test_warm_themes_do_not_evict_hit_frames(self):
        source=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        items=ItemEffects();items.prepare(.75)
        allocations=items.cache.allocations;cache_keys=set(items.cache.tiles)
        items.prepare_backgrounds(source,960,540)
        self.assertEqual(items.cache.allocations,allocations)
        self.assertEqual(set(items.cache.tiles),cache_keys)
        self.assertEqual(len(items.backgrounds),3)
        self.assertLessEqual(items.background_bytes,MAX_BACKGROUND_BYTES)
        background_allocations=items.background_allocations
        items.freeze=True;items.background(source,960,540)
        items.double=True;items.background(source,960,540)
        self.assertEqual(items.background_allocations,background_allocations)

    def test_blue_and_gold_are_visible_over_entire_paper(self):
        source=cairo.ImageSurface(cairo.FORMAT_RGB24,320,180);c=cairo.Context(source)
        c.set_source_rgb(.97,.95,.88);c.paint();source.flush()
        fx=ItemEffects();fx.prepare(.25);fx.freeze=True
        blue=fx.background(source,320,180);blue.flush()
        normal=bytes(source.get_data());cold=bytes(blue.get_data())
        at=90*source.get_stride()+160*4
        self.assertGreater(cold[at],normal[at]) # little-endian B channel
        self.assertLess(cold[at+2],normal[at+2])
        before=fx.cache.allocations
        self.assertIs(blue,fx.background(source,320,180));self.assertEqual(fx.cache.allocations,before)
        fx.freeze=False;fx.double=True
        gold=fx.background(source,320,180);gold.flush()
        self.assertGreater(bytes(gold.get_data())[12*gold.get_stride()+160*4+2],normal[12*source.get_stride()+160*4+2])


class DamageTests(unittest.TestCase):
    def test_merge_is_clipped_disjoint_and_finite(self):
        rects=merge_damage([(-4,-4,20,20),(10,10,20,20),(70,70,5,5)],100,100)
        self.assertEqual(len(rects),2)
        self.assertTrue(all(x>=0 and y>=0 and x+w<=100 and y+h<=100 for x,y,w,h in rects))
        self.assertFalse(intersects(*rects))

    def test_over_budget_and_invalid_damage_fall_back_safely(self):
        rects=[(i*4,0,1,1) for i in range(40)]
        self.assertEqual(merge_damage(rects,200,100),[(0,0,200,100)])
        self.assertEqual(merge_damage([(math.nan,0,1,1)],20,20),[(0,0,20,20)])
        self.assertEqual(merge_damage([(0,0,90,90)],100,100),[(0,0,100,100)])

    def test_damage_does_not_write_outside_clip(self):
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,80,50);c=cairo.Context(surface)
        c.set_source_rgb(.3,.4,.5);c.paint();surface.flush();before=bytes(surface.get_data())
        rect=(10,10,12,14);apply_clip(c,[rect]);c.set_source_rgb(1,0,0);c.paint();surface.flush()
        after=bytes(surface.get_data());stride=surface.get_stride()
        for y in range(50):
            for x in range(80):
                off=y*stride+x*4
                if not (10<=x<22 and 10<=y<24):self.assertEqual(after[off:off+4],before[off:off+4])
        self.assertEqual(clip_damage(c,80,50),[rect])

    def test_dense_damage_stops_once_full_paint_is_required(self):
        def dense():
            yield (0,0,90,90)
            raise AssertionError('A full repaint must not merge an irrelevant tail')
        self.assertEqual(merge_damage(dense(),100,100),[(0,0,100,100)])


if __name__=='__main__':unittest.main()
