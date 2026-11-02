"""Approved PNG integration: finite raster storage, hit anchors and layer order."""
from copy import deepcopy
import unittest
from unittest.mock import patch

try:
    import cairo
    from .renderer import Renderer
    from .gongbi_art import EFFECT_STEPS, MAX_SOURCE_BYTES, MAX_TILE_BYTES, MAX_TILE_COUNT, PLAYER_FRAMES
except ModuleNotFoundError as error:
    if error.name != 'cairo':raise
    cairo=None


@unittest.skipIf(cairo is None,'Requires Cairo, no display/camera required')
class GongbiArtTests(unittest.TestCase):
    @staticmethod
    def snapshot():
        return dict(phase='playing',time=3.,duration=75.,tracking=True,
            player=dict(x=.5,y=.78,r=.012,angle=0.),power=2,score=200,
            entities=[dict(id=i,kind=kind,x=.23+i*.18,y=.28,r=radius,
                           health=10.,max_health=10.,style=style)
                      for i,(kind,radius,style) in enumerate((('enemy',.032,'cinnabar'),
                          ('enemy',.032,'indigo'),('elite',.052,'gold'),('boss',.073,'xuanmo')))],
            shots=[dict(x=.5,y=.5,r=.005)],
            bullets=[dict(x=.5,y=.43,r=.012,style='cinnabar')],
            effects=[dict(x=.5,y=.43,kind='slash',age=.1,ttl=.48)],special_time=0.)

    @staticmethod
    def surface(width=960,height=540):
        return cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)

    @staticmethod
    def pixel(surface,x,y):
        surface.flush()
        offset=round(y)*surface.get_stride()+round(x)*4
        return bytes(surface.get_data())[offset:offset+3]

    def test_authored_frames_and_finite_storage_are_prepared_before_gameplay(self):
        renderer=Renderer();renderer._rebuild(1280,720)
        stats=renderer.gongbi.stats
        self.assertEqual(PLAYER_FRAMES,dict(left=0,neutral=1,right=2))
        self.assertEqual(stats['decode_count'],7)
        self.assertLessEqual(stats['source_bytes'],MAX_SOURCE_BYTES)
        self.assertLessEqual(stats['tile_bytes'],MAX_TILE_BYTES)
        self.assertLessEqual(stats['tile_count'],MAX_TILE_COUNT)
        with self.assertRaises(TypeError):stats['tile_count']=0
        for row in range(3):
            frames=[bytes(renderer.gongbi.effect(row,step).get_data()) for step in (0,4,8,12)]
            self.assertEqual(len(set(frames)),4,('authored effect beats',row))

    def test_stable_gameplay_has_no_decode_resize_or_large_surface_churn(self):
        renderer=Renderer();surface=self.surface();snap=self.snapshot()
        renderer.draw(cairo.Context(surface),960,540,snap)
        before=dict(renderer.gongbi.stats)
        with patch.object(renderer.gongbi,'_source',side_effect=AssertionError('late PNG decode')):
            for frame in range(40):
                snap['time']+=.016
                snap['effects'][0]['age']=(frame%28)/60
                snap['player']['angle']=(-.12,0,.12)[frame%3]
                renderer.draw(cairo.Context(surface),960,540,snap)
        self.assertEqual(before,dict(renderer.gongbi.stats))

    def test_background_bakes_once_per_resize_and_sources_decode_once(self):
        renderer=Renderer();snap=self.snapshot()
        for count,(width,height) in enumerate(((960,540),(960,540),(1280,720),(1280,720))):
            renderer.draw(cairo.Context(self.surface(width,height)),width,height,snap)
            self.assertEqual(renderer.gongbi.background_bakes,(1,1,2,2)[count])
            self.assertEqual(renderer.gongbi.decode_count,7)

    def test_hostile_bullet_remains_above_the_effect_peak(self):
        renderer=Renderer();snap=self.snapshot()
        actual=self.surface();reference=self.surface()
        renderer.draw(cairo.Context(actual),960,540,snap)
        snap['effects']=[]
        renderer.draw(cairo.Context(reference),960,540,snap)
        self.assertEqual(self.pixel(actual,480,.43*540),self.pixel(reference,480,.43*540))

    def test_hit_health_observer_is_bounded_and_does_not_mutate_snapshot(self):
        renderer=Renderer();snap=self.snapshot();original=deepcopy(snap)
        renderer._observe_hits(snap)
        self.assertEqual(snap,original)
        for index in range(30):
            snap['entities']=[dict(id=i,kind='enemy',x=.5,y=.4,health=100-index)
                              for i in range(30)]
            snap['time']+=.01
            renderer._observe_hits(snap)
        self.assertLessEqual(len(renderer._visual_hits),12)
        snap['time']+=.3;renderer._observe_hits(snap)
        self.assertFalse(renderer._visual_hits)

    def test_all_player_frames_keep_the_visible_collision_point(self):
        renderer=Renderer();renderer._rebuild(960,540)
        for angle in (-.12,0,.12):
            snap=self.snapshot();snap['player']['angle']=angle
            surface=self.surface();context=cairo.Context(surface);context.scale(.75,.75)
            renderer._player(context,snap)
            self.assertTrue(all(channel>180 for channel in self.pixel(surface,480,.78*540)))

    def test_pickups_have_sixteen_compact_gold_frames_without_sword_atlas(self):
        renderer=Renderer();renderer._rebuild(960,540)
        target=self.surface();context=cairo.Context(target);context.scale(.75,.75)
        allocations=renderer.collect_tile_allocations
        self.assertEqual(allocations,EFFECT_STEPS)
        variants=[]
        for step in range(EFFECT_STEPS):
            tile=renderer._effect_tile(step,'collect');tile.flush()
            self.assertLessEqual(tile.get_width(),42)
            self.assertLessEqual(tile.get_height(),42)
            raw=bytes(tile.get_data());variants.append(raw)
            for offset in range(0,len(raw),4):
                b,g,r,a=raw[offset:offset+4]
                if a>10:self.assertGreaterEqual(r,g);self.assertGreaterEqual(g,b)
        self.assertEqual(len(set(variants)),EFFECT_STEPS)
        effects=[dict(kind=('collect','orb','power')[i%3],x=.5,y=.4,age=.15,ttl=.5)
                 for i in range(40)]
        batches=[]
        def observe(context,commands):batches.extend(commands)
        with patch.object(renderer.gongbi,'effect',side_effect=AssertionError('pickup used sword atlas')):
            with patch.object(renderer,'_tile_commands',side_effect=observe):
                renderer._effects(context,dict(effects=effects,special_time=0))
        self.assertEqual(len(batches),40)
        self.assertEqual(renderer.collect_tile_allocations,allocations)

    def test_combat_effect_size_and_legacy_kind_semantics(self):
        renderer=Renderer();renderer._rebuild(1280,720)
        for row,size in enumerate(((108,88),(156,132),(360,286))):
            tile=renderer.gongbi.effect(row,4)
            self.assertEqual((tile.get_width(),tile.get_height()),size)
        target=self.surface(1280,720)
        with patch.object(renderer.gongbi,'effect',wraps=renderer.gongbi.effect) as effect:
            renderer._effects(cairo.Context(target),dict(effects=[dict(kind='kill',x=.5,y=.4,
                age=.2,ttl=.8)],special_time=0))
            self.assertEqual(effect.call_args.args,(0,4))

    def test_boss_transparent_gaps_leave_warm_background_bytes_unchanged(self):
        renderer=Renderer();renderer._rebuild(960,540)
        surface=self.surface();context=cairo.Context(surface)
        renderer._background(context,960,540,self.snapshot())
        surface.flush();before=bytes(surface.get_data())
        context.scale(.75,.75)
        renderer._entity(context,dict(kind='boss',x=.5,y=.36,r=.073,
                                     health=250,max_health=250),0)
        surface.flush();after=bytes(surface.get_data())
        tile=renderer._enemy_tile('boss',.073*720);tile.flush()
        data=bytes(tile.get_data());left=round(480-tile.get_width()/2)
        top=round(.36*540-tile.get_height()/2);checked=0
        for y in range(tile.get_height()):
            for x in range(tile.get_width()):
                if data[y*tile.get_stride()+x*4+3]==0:
                    offset=(top+y)*surface.get_stride()+(left+x)*4
                    self.assertEqual(after[offset:offset+3],before[offset:offset+3])
                    checked+=1
        self.assertGreater(checked,tile.get_width()*tile.get_height()*.05)


if __name__=='__main__':unittest.main()
