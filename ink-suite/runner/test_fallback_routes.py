"""Emergency Cairo routes use the engine's physical support and entity heights."""
import copy
import importlib.util
from pathlib import Path
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import sys

from .engine import RunnerGame

try:
    from . import render as fallback
except ModuleNotFoundError as error:
    if error.name not in ('cairo','cairocffi'):raise
    # These geometry checks also run on a Windows host without Cairo. Load a
    # private module rather than poisoning runner.render for other test suites.
    spec=importlib.util.spec_from_file_location('runner._fallback_route_geometry_tests',Path(__file__).with_name('render.py'))
    fallback=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,cairo=ModuleType('cairo')):spec.loader.exec_module(fallback)


class NullContext:
    def __getattr__(self,name):return lambda *args,**kwargs:None


class FallbackRouteTests(unittest.TestCase):
    def entity(self,kind,z=0.,lane=1,y=0.):
        return dict(kind=kind,z=z,lane=lane,y=y,collected=False)

    def scene_renderer(self):
        renderer=fallback.Renderer.__new__(fallback.Renderer)
        renderer.frame=object();renderer.converted_background=object()
        renderer.fx=[];renderer.flash=0.;renderer.software=True
        renderer.scaled={};renderer.texts={};renderer.native={};renderer.cache_builds=0
        renderer.pixel_format=0
        renderer.ensure_frame=Mock();renderer.road=Mock();renderer.sprite=Mock()
        renderer._text=Mock();renderer.digits=Mock()
        return renderer

    def draw(self,renderer,snapshot):
        fake=SimpleNamespace(Context=lambda surface:NullContext(),FORMAT_RGB16_565=16)
        with patch.object(fallback,'cairo',fake):renderer.render(NullContext(),snapshot,960,540)

    def test_height_projection_scales_with_depth_in_metres(self):
        for z in (0.,6.,36.,72.):
            ground=fallback.Renderer.project(1,z)
            elevated=fallback.Renderer.project(1,z,RunnerGame.ROOF_HEIGHT)
            self.assertAlmostEqual(ground[1]-elevated[1],RunnerGame.ROOF_HEIGHT*fallback.METRE_PIXELS*ground[2])
            self.assertEqual(ground[0],elevated[0])

    def test_ramp_begins_on_ground_and_finishes_at_exact_roof_height(self):
        shape=fallback.Renderer.route_geometry(self.entity('ramp',10,y=.4))
        self.assertEqual(shape['near_z'],10.)
        self.assertEqual(shape['far_z'],10.+RunnerGame.RAMP_LENGTH)
        self.assertAlmostEqual(shape['near_height'],.4)
        self.assertAlmostEqual(shape['far_height'],.4+RunnerGame.ROOF_HEIGHT)

    def test_ramp_and_cart_have_identical_shared_roof_edge(self):
        ramp=fallback.Renderer.route_geometry(self.entity('ramp',10,lane=0,y=.25))
        cart=fallback.Renderer.route_geometry(self.entity('cart',10+RunnerGame.RAMP_LENGTH,lane=0,y=.25))
        self.assertEqual(ramp['top'][1],cart['top'][0])
        self.assertEqual(ramp['top'][2],cart['top'][3])

    def test_partial_ramp_is_clipped_at_its_true_surface_height(self):
        shape=fallback.Renderer.route_geometry(self.entity('ramp',-3,lane=0))
        self.assertEqual(shape['near_z'],fallback.NEAR_Z)
        self.assertAlmostEqual(shape['near_height'],RunnerGame.ROOF_HEIGHT/6.)
        pose=fallback.Renderer.player_pose(dict(player_x=0.,player_y=RunnerGame.ROOF_HEIGHT/2.,entities=[self.entity('ramp',-3,lane=0)]))
        near=tuple((shape['top'][0][i]+shape['top'][3][i])/2 for i in (0,1))
        far=tuple((shape['top'][1][i]+shape['top'][2][i])/2 for i in (0,1))
        cross=(pose['x']-near[0])*(far[1]-near[1])-(pose['foot_y']-near[1])*(far[0]-near[0])
        self.assertAlmostEqual(cross,0.)
        self.assertGreaterEqual(pose['foot_y'],min(near[1],far[1]))
        self.assertLessEqual(pose['foot_y'],max(near[1],far[1]))

    def test_full_cart_length_remains_visible_after_its_front_passes(self):
        cart=self.entity('cart',-5)
        self.assertTrue(fallback.Renderer.entity_visible(cart))
        shape=fallback.Renderer.route_geometry(cart)
        self.assertAlmostEqual(shape['far_z'],-5.+RunnerGame.CART_LENGTH)
        self.assertIsNotNone(shape)
        self.assertFalse(fallback.Renderer.entity_visible(self.entity('cart',fallback.NEAR_Z-RunnerGame.CART_LENGTH-.01)))

    def test_contacted_route_solids_are_consumed_and_do_not_support_feet(self):
        for kind in ('ramp','cart'):
            entity=dict(self.entity(kind,-1),contacted=True,resolved=True)
            self.assertFalse(fallback.Renderer.entity_visible(entity))
            pose=fallback.Renderer.player_pose(dict(player_y=RunnerGame.ROOF_HEIGHT,entities=[entity]))
            self.assertEqual(pose['support'],0.)
            renderer=self.scene_renderer()
            with patch.object(renderer,'route',wraps=renderer.route) as routes:
                self.draw(renderer,dict(state='running',entities=[entity]))
            routes.assert_not_called()

    def test_roof_player_and_shadow_use_actual_support_height(self):
        height=.4+RunnerGame.ROOF_HEIGHT
        pose=fallback.Renderer.player_pose(dict(player_y=height,entities=[self.entity('cart',-1,y=.4)]))
        self.assertAlmostEqual(pose['foot_y'],fallback.Renderer.project(1,0,height)[1])
        self.assertAlmostEqual(pose['foot_y'],pose['shadow_y'])
        self.assertFalse(pose['airborne'])

    def test_jump_from_roof_is_not_applied_twice(self):
        roof=RunnerGame.ROOF_HEIGHT
        snapshot=dict(player_y=roof+RunnerGame.JUMP_METRES,jump_height=1.,entities=[self.entity('cart',-1)])
        pose=fallback.Renderer.player_pose(snapshot)
        self.assertAlmostEqual(pose['shadow_y']-pose['foot_y'],105.)
        self.assertAlmostEqual(pose['support'],roof)
        self.assertTrue(pose['airborne'])

    def test_old_snapshot_without_player_y_keeps_original_jump_height(self):
        pose=fallback.Renderer.player_pose(dict(jump_height=.5))
        self.assertAlmostEqual(pose['shadow_y']-pose['foot_y'],52.5)

    def test_falling_after_roof_exit_does_not_put_shadow_in_air(self):
        pose=fallback.Renderer.player_pose(dict(player_y=1.8,jump_height=0.,entities=[]))
        self.assertEqual(pose['support'],0.)
        self.assertTrue(pose['airborne'])
        self.assertGreater(pose['shadow_y'],pose['foot_y'])

    def test_render_routes_are_real_geometry_and_all_items_use_entity_y(self):
        renderer=self.scene_renderer()
        entities=[self.entity('ramp',-3),self.entity('cart',3)]
        entities += [self.entity(kind,12+i,y=RunnerGame.ROOF_HEIGHT)
                     for i,kind in enumerate(('coin','shield','magnet','barrier','arch'))]
        snapshot=dict(state='running',player_y=RunnerGame.ROOF_HEIGHT/2.,entities=entities)
        before=copy.deepcopy(snapshot)
        with patch.object(renderer,'route',wraps=renderer.route) as routes:self.draw(renderer,snapshot)
        self.assertEqual(routes.call_count,2)
        calls={call.args[1]:call.args for call in renderer.sprite.call_args_list}
        self.assertNotIn('ramp',calls);self.assertNotIn('cart',calls)
        for entity in entities[2:]:
            expected=fallback.Renderer.project(entity['lane'],entity['z'],entity['y'])
            self.assertEqual(calls[entity['kind']][2:],expected)
        hero=next(args for kind,args in calls.items() if kind.startswith('hero'))
        self.assertAlmostEqual(hero[3],fallback.Renderer.project(1,0,snapshot['player_y'])[1])
        self.assertEqual(snapshot,before)

    def test_simulated_ramp_climb_snapshot_matches_drawn_surface(self):
        game=RunnerGame();game.start();game._next_wave_distance=float('inf')
        ramp=game.spawn('ramp',1,1.)
        game.spawn('cart',1,1.+RunnerGame.RAMP_LENGTH)
        for _ in range(50):game.update(1./120.)
        self.assertLess(ramp.z,0.)
        self.assertGreater(ramp.z,-RunnerGame.RAMP_LENGTH)
        pose=fallback.Renderer.player_pose(game.snapshot())
        self.assertAlmostEqual(pose['height'],RunnerGame.ROOF_HEIGHT*(-ramp.z)/RunnerGame.RAMP_LENGTH)
        self.assertAlmostEqual(pose['foot_y'],pose['shadow_y'])
        self.assertFalse(pose['airborne'])

    def test_compatibility_metrics_identify_the_physical_route_renderer(self):
        renderer=self.scene_renderer()
        with patch.object(fallback,'cairo',SimpleNamespace(FORMAT_RGB16_565=16)):
            metrics=renderer.metrics()
        self.assertTrue(metrics['compatibility_rendering'])
        self.assertEqual(metrics['route_geometry'],'ramp-roof-metres-v1')


if __name__=='__main__':unittest.main(verbosity=2)
