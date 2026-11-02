"""Projectile batching, visible hit cue and bounded immutable cache regression.
No camera, service, display or learned-model dependency is used.
"""
import os
import unittest
from unittest.mock import patch
try:
    import cairo
    from sword.renderer import Renderer, W, H
except ModuleNotFoundError as error:
    if error.name!='cairo':raise
    cairo=None


@unittest.skipIf(cairo is None,'Requires Cairo, no X display required')
class DanmakuRendererTests(unittest.TestCase):
    def renderer(self,native=False):
        with patch.dict(os.environ,{'INK_SWORD_DISABLE_NEON':'0' if native else '1'}):
            result=Renderer()
        result._rebuild(1024,552)
        return result

    @staticmethod
    def pixels(surface):
        surface.flush();raw=bytes(surface.get_data())
        return raw[0::4]+raw[1::4]+raw[2::4]

    @staticmethod
    def projectiles():
        return dict(shots=[dict(x=.3+i*.01,y=.4,r=.005) for i in range(20)],
                    bullets=[dict(x=-.02+(i%25)*.045,y=-.02+(i//25)*.12,
                                  r=(.006,.009,.012)[i%3],style=i%4) for i in range(240)])

    def compare_batch(self,native):
        renderer=self.renderer(native)
        if native and not renderer.blitter.enabled:self.skipTest('AArch64 helper required')
        first=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        second=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        a,b=cairo.Context(first),cairo.Context(second)
        for context in (a,b):context.set_source_rgb(.96,.94,.84);context.paint()
        renderer._frame_blitter=renderer.blitter.prepare_frame(a)
        a.scale(1024/W,552/H)
        snapshot=self.projectiles()
        renderer._projectiles(a,snapshot)
        renderer._frame_blitter=None
        # Independent simple Cairo reference, including off-canvas bullets.
        for shot,collection in ((True,snapshot['shots']),(False,snapshot['bullets'])):
            for item in collection:
                tile=renderer._projectile_tile(item.get('style',0),item['r']*H,shot)
                x=round(item['x']*1024-tile.get_width()/2)
                y=round(item['y']*552-tile.get_height()/2)
                b.save();b.rectangle(x,y,tile.get_width(),tile.get_height());b.clip()
                b.set_source_surface(tile,x,y);b.paint();b.restore()
        self.assertEqual(self.pixels(first),self.pixels(second))
        if native:self.assertGreater(renderer.blitter.native_calls,0)

    def test_batch_matches_simple_cairo_reference(self):self.compare_batch(False)
    def test_batch_matches_cairo_with_native_helper(self):self.compare_batch(True)

    def test_rejected_native_batch_falls_back_without_losing_projectiles(self):
        from types import SimpleNamespace
        renderer=self.renderer()
        first=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        second=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        for surface in (first,second):
            context=cairo.Context(surface);context.set_source_rgb(.96,.94,.84);context.paint()
        snapshot=self.projectiles()
        calls=[]
        def unsupported_batch(commands):
            calls.append(len(commands));return False
        renderer._frame_blitter=SimpleNamespace(target=first,paint_tiles=unsupported_batch,
                                               paint_tile=lambda *args:False)
        context=cairo.Context(first);context.scale(1024/W,552/H)
        renderer._projectiles(context,snapshot)
        renderer._frame_blitter=None
        context=cairo.Context(second);context.scale(1024/W,552/H)
        renderer._projectiles(context,snapshot)
        self.assertEqual(calls,[len(snapshot['shots'])+len(snapshot['bullets'])])
        self.assertEqual(self.pixels(first),self.pixels(second))

    def test_repeated_bullet_storm_has_no_fonts_or_surface_churn(self):
        renderer=self.renderer()
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        context=cairo.Context(target);context.scale(1024/W,552/H)
        snapshot=self.projectiles()
        renderer._projectiles(context,snapshot)
        allocations=renderer.projectile_tile_allocations
        with patch.object(renderer,'_text',side_effect=AssertionError('text in projectile loop')):
            for _ in range(40):renderer._projectiles(context,snapshot)
        self.assertEqual(allocations,renderer.projectile_tile_allocations)
        self.assertLessEqual(len(renderer.projectile_cache),96)

    def test_projectile_cache_bounded_with_unusual_radii_and_styles(self):
        renderer=self.renderer()
        for i in range(500):renderer._projectile_tile(i,.7+i*.061,i%5==0)
        self.assertLessEqual(len(renderer.projectile_cache),96)
        # Every source stays a small tile, never a screen-size alpha plane.
        self.assertLess(max(t.get_width()*t.get_height() for t in renderer.projectile_cache.values()),24000)

    def test_engine_bullet_styles_have_distinct_visible_fill_colors(self):
        renderer=self.renderer()
        # Sample the colored interior away from highlights, symbol strokes,
        # and the dark border. The three real engine strings must not alias.
        colors=[]
        for style in ('ink','cinnabar','indigo'):
            tile=renderer._projectile_tile(style,7)
            tile.flush();data=bytes(tile.get_data())
            x,y=tile.get_width()//2+3,tile.get_height()//2
            at=y*tile.get_stride()+x*4
            colors.append(tuple(data[at:at+3]))
            self.assertGreater(data[at+3],240)
        self.assertEqual(len(set(colors)),3,colors)

    def test_every_effect_in_engine_budget_is_rendered(self):
        from sword.engine import MAX_EFFECTS
        renderer=self.renderer()
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        context=cairo.Context(target);context.scale(1024/W,552/H)
        effects=[dict(kind='spark',x=.1+(i%10)*.08,y=.2+(i//10)*.14,
                      age=.2,ttl=.8) for i in range(MAX_EFFECTS)]
        counts=[]
        original=renderer._tile_commands
        def observe_commands(context,commands):
            counts.append(len(commands));original(context,commands)
        with patch.object(renderer,'_tile_commands',side_effect=observe_commands):
            renderer._effects(context,dict(effects=effects,special_time=0))
        self.assertEqual(sum(counts),MAX_EFFECTS)
        self.assertEqual(MAX_EFFECTS,40)

    def test_effect_batch_preserves_interleaved_label_painter_order(self):
        renderer=self.renderer()
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        context=cairo.Context(target);context.scale(1024/W,552/H)
        effects=[dict(kind=kind,x=.5,y=.5,age=.2,ttl=.8)
                 for kind in ('slash','slash','collect','slash','hurt','slash')]
        events=[]
        def commands(context,items):
            if items:events.append(('tiles',len(items)))
        def text(context,label,*args,**kwargs):events.append(('text',label))
        with patch.object(renderer,'_tile_commands',side_effect=commands),patch.object(renderer,'_text',side_effect=text):
            renderer._effects(context,dict(effects=effects,special_time=0))
        self.assertEqual(events,[('tiles',3),('text','能量'),('tiles',2),('text','受到攻击'),('tiles',1)])

    def compare_effect_batches_with_previous_per_tile_painter(self,native):
        class IndividualTileRenderer(Renderer):
            def _tile_commands(self,context,commands):
                for tile,left,top in commands:
                    context.save();context.identity_matrix()
                    context.rectangle(left,top,tile.get_width(),tile.get_height());context.clip()
                    self._paint_tile(context,tile,left,top);context.restore()
        with patch.dict(os.environ,{'INK_SWORD_DISABLE_NEON':'0' if native else '1'}):
            batched,reference=Renderer(),IndividualTileRenderer()
        if native and not batched.blitter.enabled:self.skipTest('AArch64 helper required')
        first=cairo.ImageSurface(cairo.FORMAT_RGB24,800,432)
        second=cairo.ImageSurface(cairo.FORMAT_RGB24,800,432)
        for case in range(3):
            snapshot=self.projectiles()
            kinds=('slash',) if case==0 else ('slash','hit','collect','power','hurt')
            snapshot.update(phase='playing',time=case*1.7+6,duration=120,score=1010+case,
                energy=.47,power=4,lives=3,scene=1,tracking=True,
                player=dict(x=.43,y=.75,r=.009,angle=-1.57),entities=[],
                effects=[dict(kind=kinds[i%len(kinds)],x=.22+(i%8)*.07,y=.32+(i//8)*.065,
                              age=.07+(i%8)*.04,ttl=.8) for i in range(40)],
                special_time=.8 if case==2 else 0)
            batched.draw(cairo.Context(first),800,432,snapshot)
            reference.draw(cairo.Context(second),800,432,snapshot)
            self.assertEqual(self.pixels(first),self.pixels(second),case)

    def test_effect_batches_match_previous_order_with_cairo(self):
        self.compare_effect_batches_with_previous_per_tile_painter(False)

    def test_effect_batches_match_previous_order_with_native_helper(self):
        self.compare_effect_batches_with_previous_per_tile_painter(True)

    def test_enemy_cache_is_bounded_and_all_new_entities_draw(self):
        renderer=self.renderer()
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
        context=cairo.Context(target);context.scale(1024/W,552/H)
        for index,kind in enumerate(('enemy','elite','boss','power','orb')):
            renderer._entity(context,dict(kind=kind,x=.1+index*.18,y=.5,r=.035,
                                         health=3,max_health=5,style=index),0)
        self.assertGreater(sum(self.pixels(target)),0)
        for i in range(100):renderer._enemy_tile('enemy',8+i*.5,i%4)
        self.assertLessEqual(len(renderer.enemy_cache),32)

    def test_player_hit_cue_remains_centered_at_all_playfield_edges(self):
        renderer=self.renderer()
        for x,y in ((.07,.22),(.93,.90),(.5,.78)):
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,552)
            context=cairo.Context(surface);context.scale(1024/W,552/H)
            renderer._player(context,dict(time=2,player=dict(x=x,y=y,r=.012),power=4))
            surface.flush();data=bytes(surface.get_data())
            at=round(y*552)*surface.get_stride()+round(x*1024)*4
            self.assertTrue(all(value>180 for value in data[at:at+3]),'missing bright collision center')

if __name__=='__main__':unittest.main()
