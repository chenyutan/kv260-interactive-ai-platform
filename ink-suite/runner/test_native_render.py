"""Render ABI/failure isolation tests; no camera, sound, X11 or GL required."""
import ctypes as C
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from runner.native_render import Entity, Frame, HudLayer, HudUpdate, LEFT_RECT, GOAL_RECT, MAX_ENTITIES, Renderer, cairo_bgra_to_rgba, cairo_region_to_rgba


class NativeFunction:
    def __init__(self, implementation):
        self.implementation = implementation
        self.calls = []
    def __call__(self, *args):
        self.calls.append(args)
        return self.implementation(*args)


class NativeLibrary:
    def __init__(self, *, create_ok=True, frame_ok=True, resize_ok=True, hud_ok=True, patch_ok=True,
                 info=b'{"renderer":"Mali-400 MP","version":"OpenGL ES 2.0"}'):
        def create(xid, width, height, offscreen, error, length):
            if not create_ok:error.value = b'no matching EGL visual'
            return 17 if create_ok else None
        self.rv_create = NativeFunction(create)
        self.rv_abi_version = NativeFunction(lambda:2)
        self.rv_frame_size = NativeFunction(lambda:C.sizeof(Frame))
        self.rv_entity_size = NativeFunction(lambda:C.sizeof(Entity))
        self.rv_frame = NativeFunction(lambda *args: int(frame_ok))
        self.rv_set_hud = NativeFunction(lambda *args: int(hud_ok))
        self.rv_resize = NativeFunction(lambda *args: int(resize_ok))
        self.rv_destroy = NativeFunction(lambda *args: None)
        self.rv_info = NativeFunction(lambda *args: info)
        self.rv_readback = NativeFunction(lambda *args: 1)
        self.rv_patch_hud = NativeFunction(lambda *args: int(patch_ok))
        self.rv_event = NativeFunction(lambda *args: None)
        self.rv_set_powerups = NativeFunction(lambda *args: 1)
        self.rv_material_api_version = NativeFunction(lambda: 1)
        self.rv_set_material_atlas = NativeFunction(lambda *args: 1)


class FakeHud:
    def __init__(self):self.glyphs = {};self.uploaded = False
    def update(self, snapshot, now):
        if self.uploaded:return None
        self.uploaded = True
        return HudUpdate(bytearray(960*540*4))


class FakePatchHud:
    def __init__(self):self.glyphs={};self.updates=0
    def full_upload(self):return HudUpdate(bytearray(960*540*4))
    def update(self,snapshot,now):
        self.updates+=1
        if self.updates==1:return self.full_upload()
        return HudUpdate(bytearray(232*79*4),706,18,232,79,True)


class FakeBgraHud:
    def __init__(self):self.glyphs={};self.updates=0;self.data=bytearray(960*540*4)
    def full_upload(self):return HudUpdate(memoryview(self.data),format='BGRA',stride=3840)
    def update(self,snapshot,now):
        self.updates+=1
        if self.updates==1:return self.full_upload()
        start=18*3840+706*4;span=78*3840+232*4
        return HudUpdate(memoryview(self.data)[start:start+span],706,18,232,79,True,'BGRA',3840)


class NativeRendererTests(unittest.TestCase):
    def renderer(self, library=None):
        fallback = SimpleNamespace(**{name: Mock() for name in (
            'configure_depth','render','release_native_output','warm','event','update_effects')},
            metrics=Mock(return_value={'presentation':'cairo'}))
        status = Mock()
        renderer = Renderer(on_status=status, library=library or NativeLibrary(),
                            fallback_factory=lambda: fallback, hud_factory=FakeHud,
                            material_loader=lambda:dict(pixels=bytearray([255]*4),width=1,height=1,stride=4))
        return renderer, fallback, status

    def test_abi_scalar_sizes_and_offsets(self):
        self.assertEqual(C.sizeof(Frame), 36)
        self.assertEqual(Frame.state.offset, 28)
        self.assertEqual(Frame.player_y.offset,32)
        self.assertEqual(C.sizeof(Entity), 20)
        self.assertEqual(Entity.y.offset,16)
        self.assertEqual([getattr(Entity, key).offset for key in ('kind','lane','z','collected')], [0,4,8,12])

    def test_premultiplied_channels_are_swapped_not_unpremultiplied(self):
        data = bytes([12,24,60,128]) if sys.byteorder == 'little' else bytes([128,60,24,12])
        self.assertEqual(cairo_bgra_to_rgba(data,1,1,4), bytes([60,24,12,128]))

    def test_surface_row_padding_not_uploaded_as_pixels(self):
        data = bytes([1,2,3,255,90,90,90,90,4,5,6,128,90,90,90,90])
        expected = bytes([3,2,1,255,6,5,4,128]) if sys.byteorder == 'little' else bytes([2,3,255,1,5,6,128,4])
        self.assertEqual(cairo_bgra_to_rgba(data,1,2,8), expected)

    def test_dirty_rect_copies_only_requested_pixels(self):
        data=bytes([0,0,0,0,1,2,3,255,0,0,0,0,4,5,6,128])
        expected=bytes([3,2,1,255,6,5,4,128]) if sys.byteorder=='little' else bytes([2,3,255,1,5,6,128,4])
        self.assertEqual(cairo_region_to_rgba(data,1,0,1,2,8),expected)

    def test_native_library_missing_symbol_explicitly_falls_back(self):
        renderer, fallback, status = self.renderer(SimpleNamespace())
        self.assertFalse(renderer.is_native)
        self.assertFalse(renderer.attach_window(10,960,540))
        self.assertIn('native-library-unavailable', renderer.metrics()['fallback_reason'])
        self.assertEqual(status.call_args.args[0], 'renderer_fallback')

    def test_old_native_abi_cannot_misread_elevated_entity_stride(self):
        library=NativeLibrary();library.rv_abi_version=NativeFunction(lambda:1)
        renderer,_,_=self.renderer(library)
        self.assertFalse(renderer.attach_window(10,960,540))
        self.assertFalse(library.rv_create.calls)
        self.assertIn('Incompatible native runner ABI',renderer.metrics()['fallback_reason'])

    def test_native_sizes_are_checked_even_for_same_version(self):
        library=NativeLibrary();library.rv_entity_size=NativeFunction(lambda:16)
        renderer,_,_=self.renderer(library)
        self.assertFalse(renderer.attach_window(10,960,540))
        self.assertFalse(library.rv_create.calls)

    def test_abi2_library_without_new_content_capability_is_refused(self):
        library=NativeLibrary();del library.rv_set_powerups
        renderer,_,_=self.renderer(library)
        self.assertFalse(renderer.attach_window(10,960,540))
        self.assertFalse(library.rv_create.calls)
        self.assertIn('rv_set_powerups',renderer.metrics()['fallback_reason'])

    def test_new_powerups_preserve_entity_height_and_explicit_timers(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        self.assertTrue(renderer.attach_window(10,960,540))
        snapshot=dict(state='running',player_y=4.8,jetpack_time=6.,boots_time=9.,
                      coinboost_time=12.,entities=[dict(id=i+1,kind=k,lane=1,z=3.,y=2.49)
                      for i,k in enumerate(('jetpack','springboots','coinboost'))])
        self.assertTrue(renderer.render(None,snapshot,960,540))
        self.assertEqual(library.rv_set_powerups.calls[-1],(17,6.,9.,12.))
        frame=library.rv_frame.calls[-1]
        self.assertEqual(frame[3],3)
        self.assertEqual([frame[2][i].kind for i in range(3)],[7,8,9])
        self.assertTrue(all(abs(frame[2][i].y-2.49)<1e-5 for i in range(3)))
        # A new/old snapshot without timers must clear the previous state.
        self.assertTrue(renderer.render(None,dict(state='title'),960,540))
        self.assertEqual(library.rv_set_powerups.calls[-1],(17,0.,0.,0.))

    def test_invalid_powerup_state_cannot_poison_native_context(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        self.assertTrue(renderer.render(None,dict(jetpack_time=float('nan'),
                        boots_time=-5.,coinboost_time=float('inf')),960,540))
        self.assertEqual(library.rv_set_powerups.calls[-1],(17,0.,0.,0.))
        library.rv_set_powerups=NativeFunction(lambda *a:0)
        self.assertFalse(renderer.render(None,dict(state='running'),960,540))
        self.assertIn('power-up state rejected',renderer.metrics()['fallback_reason'])

    def test_elevated_route_y_and_ramp_kind_preserved_without_animation_scale(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        snapshot=dict(player_y=2.49,jump_height=.5,entities=[
            dict(id=3,kind='ramp',lane=1,z=3.,y=0.),
            dict(id=4,kind='coin',lane=1,z=8.,y=2.49)])
        self.assertTrue(renderer.render(None,snapshot,960,540))
        call=library.rv_frame.calls[-1]
        self.assertAlmostEqual(call[1]._obj.player_y,2.49,places=5)
        self.assertAlmostEqual(call[1]._obj.jump_height,.5)
        self.assertEqual(call[2][0].kind,6)
        self.assertAlmostEqual(call[2][1].y,2.49,places=5)

    def test_context_failure_has_reason_and_no_faked_acceleration(self):
        library = NativeLibrary(create_ok=False)
        renderer, fallback, status = self.renderer(library)
        self.assertFalse(renderer.attach_window(10,960,540))
        self.assertIn('no matching EGL visual', renderer.metrics()['fallback_reason'])
        self.assertFalse(renderer.metrics()['accelerated'])
        renderer.render('cairo-target', {'state':'title'}, 960,540)
        fallback.render.assert_called_once()
        self.assertFalse(library.rv_destroy.calls)

    def test_invalid_xid_never_reaches_egl(self):
        library = NativeLibrary()
        renderer, _, _ = self.renderer(library)
        self.assertFalse(renderer.attach_window(0,960,540))
        self.assertFalse(library.rv_create.calls)

    def test_native_frame_preserves_snapshot_and_uses_bounded_scalar_copy(self):
        library = NativeLibrary()
        renderer, fallback, status = self.renderer(library)
        self.assertTrue(renderer.attach_window(10,960,540))
        snapshot = dict(state='running',distance=123.5,player_x=.8,jump_height=.7,
                        slide_phase=-1.,shield_time=2.,magnet_time=0.,
                        entities=[dict(kind='cart',lane=0,z=30.),dict(kind='coin',lane=1,z=2.,collected=True)])
        before = repr(snapshot)
        self.assertTrue(renderer.render(None,snapshot,960,540))
        args = library.rv_frame.calls[-1]
        self.assertEqual(args[3], 1)
        self.assertEqual(args[1]._obj.state, 1)
        self.assertAlmostEqual(args[1]._obj.distance, 123.5)
        self.assertEqual(args[2][0].kind, 0)
        self.assertEqual(repr(snapshot), before)
        fallback.render.assert_not_called()
        self.assertEqual(status.call_args.args[0], 'renderer_ready')
        self.assertTrue(renderer.metrics()['accelerated'])

    def test_entity_overflow_is_bounded(self):
        library = NativeLibrary();renderer, _, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.render(None,dict(entities=[dict(kind='coin',lane=1,z=float(i)) for i in range(200)]),960,540)
        self.assertEqual(library.rv_frame.calls[-1][3], MAX_ENTITIES)

    def test_stable_ids_are_packed_without_treating_resolved_train_as_hit(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.render(None,dict(distance=50,entities=[dict(id=37,kind='cart',lane=1,z=2,resolved=True)]),960,540)
        self.assertEqual(library.rv_frame.calls[-1][2][0].collected,37<<2)
        renderer.event(dict(kind='hit',item_kind='cart',id=37,lane=1))
        renderer.render(None,dict(distance=51,entities=[dict(id=37,kind='cart',lane=1,z=1)]),960,540)
        self.assertEqual(library.rv_frame.calls[-1][2][0].collected,(37<<2)|2)

    def test_train_impact_ids_are_bounded_and_cleared_on_restart_or_distance_reset(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        for entity_id in range(1,201):renderer.event(dict(kind='shield_break',item_kind='cart',id=entity_id))
        self.assertEqual(len(renderer._hit_ids),160)
        renderer.event(dict(kind='start'));self.assertFalse(renderer._hit_ids)
        renderer.render(None,dict(distance=50),960,540)
        renderer.event(dict(kind='hit',item_kind='cart',id=1))
        renderer.render(None,dict(distance=1),960,540)
        self.assertFalse(renderer._hit_ids)

    def test_invulnerable_cart_contact_still_sets_consumed_visual_flag(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.render(None,dict(entities=[dict(id=5,kind='cart',lane=1,z=2,contacted=True)]),960,540)
        self.assertEqual(library.rv_frame.calls[-1][2][0].collected,(5<<2)|2)

    def test_high_ramp_side_contact_reaches_native_consumed_flag(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.event(dict(kind='hit',item_kind='ramp',id=7,lane=1))
        renderer.render(None,dict(entities=[dict(id=7,kind='ramp',lane=1,z=-4)]),960,540)
        self.assertEqual(library.rv_frame.calls[-1][2][0].collected,(7<<2)|2)
        renderer.event(dict(kind='start'))
        renderer.render(None,dict(entities=[dict(id=8,kind='ramp',lane=1,z=-4,contacted=True)]),960,540)
        self.assertEqual(library.rv_frame.calls[-1][2][0].collected,(8<<2)|2)

    def test_invalid_entity_and_nonfinite_frame_do_not_escape_abi(self):
        library = NativeLibrary();renderer, _, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.render(None,dict(distance=float('nan'),entities=[dict(kind='bad',lane=1,z=1),
            dict(kind='coin',lane=4,z=2),dict(kind='shield',lane=0,z=4)]),960,540)
        frame = library.rv_frame.calls[-1][1]._obj
        self.assertEqual(frame.distance, 0.)
        self.assertEqual(library.rv_frame.calls[-1][3], 1)

    def test_texture_upload_only_when_hud_changes(self):
        library = NativeLibrary();renderer, _, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.render(None,{},960,540);renderer.render(None,{},960,540)
        self.assertEqual(len(library.rv_set_hud.calls), 1)
        self.assertEqual(len(library.rv_frame.calls), 2)
        self.assertEqual(library.rv_set_hud.calls[0][2:], (960,540,3840))

    def test_running_score_uses_small_patch_after_initial_full_texture(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer._hud_factory=FakePatchHud
        renderer.attach_window(10,960,540)
        renderer.render(None,{},960,540);renderer.render(None,{},960,540)
        self.assertEqual(len(library.rv_set_hud.calls),1)
        self.assertEqual(len(library.rv_patch_hud.calls),1)
        self.assertEqual(library.rv_patch_hud.calls[0][2:],(706,18,232,79,928))
        self.assertEqual(renderer.metrics()['hud_full_uploads'],1)
        self.assertEqual(renderer.metrics()['hud_patch_uploads'],1)
        self.assertEqual(renderer.metrics()['hud_bytes_uploaded'],960*540*4+232*79*4)

    def test_older_library_without_patch_uses_compatible_full_upload(self):
        library=NativeLibrary();del library.rv_patch_hud
        renderer,_,_=self.renderer(library);renderer._hud_factory=FakePatchHud
        renderer.attach_window(10,960,540)
        renderer.render(None,{},960,540);renderer.render(None,{},960,540)
        self.assertEqual(len(library.rv_set_hud.calls),2)
        self.assertEqual(renderer.metrics()['hud_patch_uploads'],0)
        self.assertFalse(renderer.metrics()['hud_patch_supported'])

    def test_bgra_zero_copy_uses_original_row_stride_and_dirty_byte_metrics(self):
        library=NativeLibrary();library.rv_set_hud_bgra=NativeFunction(lambda *args:1)
        library.rv_patch_hud_bgra=NativeFunction(lambda *args:1)
        renderer,_,_=self.renderer(library);renderer._hud_factory=FakeBgraHud
        renderer.attach_window(10,960,540)
        self.assertTrue(renderer._hud.use_bgra)
        renderer.render(None,{},960,540);renderer.render(None,{},960,540)
        self.assertEqual(len(library.rv_set_hud_bgra.calls),1)
        self.assertEqual(library.rv_patch_hud_bgra.calls[0][2:],(706,18,232,79,3840))
        pointer=library.rv_patch_hud_bgra.calls[0][1]
        self.assertEqual(C.addressof(pointer),C.addressof(C.c_uint8.from_buffer(renderer._hud.data))+18*3840+706*4)
        self.assertFalse(library.rv_set_hud.calls);self.assertFalse(library.rv_patch_hud.calls)
        self.assertEqual(renderer.metrics()['hud_bytes_uploaded'],960*540*4+232*79*4)
        self.assertEqual(renderer.metrics()['hud_texture_format'],'BGRA')

    def test_only_one_bgra_entrypoint_keeps_compatible_rgba(self):
        library=NativeLibrary();library.rv_set_hud_bgra=NativeFunction(lambda *args:1)
        renderer,_,_=self.renderer(library);renderer.attach_window(10,960,540)
        self.assertFalse(renderer._hud.use_bgra)
        self.assertTrue(renderer.render(None,{},960,540))
        self.assertFalse(library.rv_set_hud_bgra.calls)
        self.assertEqual(renderer.metrics()['hud_texture_format'],'RGBA')

    def test_texture_cannot_silently_mix_bgra_full_and_rgba_patch(self):
        library=NativeLibrary();library.rv_set_hud_bgra=NativeFunction(lambda *args:1)
        library.rv_patch_hud_bgra=NativeFunction(lambda *args:1)
        renderer,_,_=self.renderer(library);renderer._hud_factory=FakeBgraHud
        renderer.attach_window(10,960,540);renderer.render(None,{},960,540)
        renderer._hud.update=lambda *_:HudUpdate(bytearray(232*79*4),706,18,232,79,True)
        self.assertFalse(renderer.render(None,{},960,540))
        self.assertIn('format changed',renderer.metrics()['fallback_reason'])
        self.assertFalse(library.rv_patch_hud.calls)

    def test_patch_failure_cleans_context_and_reports_specific_reason(self):
        library=NativeLibrary(patch_ok=False);renderer,fallback,_=self.renderer(library)
        renderer._hud_factory=FakePatchHud;renderer.attach_window(10,960,540)
        renderer.render(None,{},960,540)
        self.assertFalse(renderer.render(None,{},960,540))
        self.assertIn('HUD texture patch failed',renderer.metrics()['fallback_reason'])
        self.assertEqual(len(library.rv_destroy.calls),1)
        fallback.render.assert_called_once()

    def test_native_events_use_only_explicit_effect_kind_and_valid_lane(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        for kind,lane in [('collect',0),('hit',1),('shield_break',2),('dodge',100),('jump',1)]:
            renderer.event(dict(kind=kind,lane=lane))
        self.assertEqual([call[1:] for call in library.rv_event.calls],[(0,0),(1,1),(2,2),(3,1)])
        self.assertEqual(renderer.metrics()['native_events_forwarded'],4)

    def test_power_collection_has_distinct_feedback_without_changing_coin_event(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        for kind in ('coin','shield','magnet','jetpack','springboots','coinboost'):
            renderer.event(dict(kind='collect',item_kind=kind,lane=1))
        self.assertEqual([call[1:] for call in library.rv_event.calls],[(0,1)]+[(4,1)]*5)

    def test_creation_error_buffer_stays_alive_until_context_destruction(self):
        library=NativeLibrary();renderer,_,_=self.renderer(library)
        renderer.attach_window(10,960,540)
        error=library.rv_create.calls[0][4]
        self.assertIs(error,renderer._error_buffer)
        renderer.release_native_output()
        self.assertIs(error,renderer._error_buffer)

    def test_resize_only_on_actual_size_change(self):
        library = NativeLibrary();renderer, _, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.render(None,{},960,540);renderer.render(None,{},1280,720)
        self.assertEqual(len(library.rv_resize.calls), 1)
        self.assertEqual(library.rv_resize.calls[0][1:], (1280,720))

    def test_resize_failure_cleans_context_and_draws_fallback(self):
        library = NativeLibrary(resize_ok=False);renderer, fallback, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        self.assertFalse(renderer.render(None,{},1280,720))
        self.assertEqual(len(library.rv_destroy.calls), 1)
        fallback.render.assert_called_once()
        self.assertEqual(renderer.metrics()['fallback_reason'], 'native-resize-failed')

    def test_frame_failure_is_permanent_and_explicit(self):
        library = NativeLibrary(frame_ok=False);renderer, fallback, status = self.renderer(library)
        renderer.attach_window(10,960,540)
        self.assertFalse(renderer.render(None,{},960,540))
        self.assertFalse(renderer.is_native)
        self.assertIn('EGL frame submission failed',renderer.metrics()['fallback_reason'])
        self.assertEqual(len(library.rv_destroy.calls),1)
        renderer.render(None,{},960,540)
        self.assertEqual(len(library.rv_frame.calls),1)
        self.assertEqual(fallback.render.call_count,2)

    def test_hud_failure_never_submits_partial_frame(self):
        library = NativeLibrary(hud_ok=False);renderer, fallback, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        self.assertFalse(renderer.render(None,{},960,540))
        self.assertFalse(library.rv_frame.calls)
        fallback.render.assert_called_once()

    def test_software_gl_is_not_reported_as_hardware_acceleration(self):
        library = NativeLibrary(info=b'{"renderer":"llvmpipe (LLVM 15)"}')
        renderer, _, status = self.renderer(library)
        self.assertTrue(renderer.attach_window(10,960,540))
        self.assertFalse(renderer.metrics()['accelerated'])
        self.assertFalse(status.call_args.kwargs['accelerated'])

    def test_native_release_is_idempotent_and_can_reattach(self):
        library = NativeLibrary();renderer, _, _ = self.renderer(library)
        renderer.attach_window(10,960,540)
        renderer.release_native_output();renderer.release_native_output()
        self.assertEqual(len(library.rv_destroy.calls),1)
        self.assertTrue(renderer.attach_window(20,1280,720))
        self.assertEqual(len(library.rv_create.calls),2)

    def test_readback_is_opt_in_not_in_normal_render(self):
        library = NativeLibrary();renderer, _, _ = self.renderer(library)
        renderer.attach_window(10,32,18)
        renderer.render(None,{},32,18)
        self.assertFalse(library.rv_readback.calls)
        self.assertEqual(len(renderer.readback()),32*18*4)
        self.assertEqual(len(library.rv_readback.calls),1)


class RealHudDirtyTests(unittest.TestCase):
    def setUp(self):
        try:self.hud=HudLayer()
        except ImportError:self.skipTest('Real Cairo available in WSL/target only')
        self.base=dict(state='running',score=125,coins=3,distance=20.,lives=3,
                       shield_time=0.,magnet_time=0.,ui=dict(control_mode='gamepad'))

    def test_life_and_power_changes_only_touch_left_status_rect(self):
        self.hud.update(self.base,0.);before=bytes(self.hud.surface.get_data())
        update=self.hud.update(dict(self.base,lives=2,shield_time=5.,magnet_time=3.),.2)
        self.assertTrue(update.patch)
        self.assertEqual((update.x,update.y,update.width,update.height),LEFT_RECT)
        after=bytes(self.hud.surface.get_data());x,y,w,h=LEFT_RECT;stride=self.hud.surface.get_stride()
        for row in range(540):
            lo,hi=row*stride,(row+1)*stride
            if not y<=row<y+h:self.assertEqual(before[lo:hi],after[lo:hi])
            else:
                self.assertEqual(before[lo:lo+x*4],after[lo:lo+x*4])
                self.assertEqual(before[lo+(x+w)*4:hi],after[lo+(x+w)*4:hi])
        self.assertEqual(self.hud.full_builds,1)
        self.assertTrue(self.hud.update(dict(self.base,lives=2),.4).patch)
        self.assertEqual(self.hud.full_builds,1)

    def test_simultaneous_status_and_score_changes_use_two_small_patches(self):
        self.hud.update(self.base,0.)
        updates=self.hud.update(dict(self.base,lives=2,score=300,shield_time=2.),.2)
        self.assertEqual(len(updates),2)
        self.assertTrue(all(item.patch for item in updates))
        self.assertEqual(sum(item.width*item.height*4 for item in updates),232*79*4+LEFT_RECT[2]*LEFT_RECT[3]*4)
        self.assertEqual(self.hud.full_builds,1)

    def test_bgra_surface_views_borrow_storage_and_never_convert_channels(self):
        self.hud.use_bgra=True
        with patch('runner.native_render.cairo_region_to_rgba',side_effect=AssertionError('Channel copy forbidden')):
            full=self.hud.update(self.base,0.)
            dirty=self.hud.update(dict(self.base,score=200),.2)
        self.assertIsInstance(full.pixels,memoryview);self.assertFalse(full.pixels.readonly)
        self.assertEqual(dirty.format,'BGRA');self.assertEqual(dirty.stride,3840)
        self.assertEqual(len(dirty.pixels),78*3840+232*4)
        data=self.hud.surface.get_data()
        self.assertEqual(C.addressof(C.c_uint8.from_buffer(dirty.pixels)),
                         C.addressof(C.c_uint8.from_buffer(data))+18*3840+706*4)

    def test_goal_progress_uses_its_own_patch_without_full_texture(self):
        self.hud.update(self.base,0.)
        dirty=self.hud.update(dict(self.base,goal_progress=3),.2)
        self.assertTrue(dirty.patch)
        self.assertEqual((dirty.x,dirty.y,dirty.width,dirty.height),GOAL_RECT)
        self.assertEqual(self.hud.full_builds,1)

    def test_combo_counter_never_rebuilds_static_hud(self):
        self.hud.update(self.base,0.)
        dirty=self.hud.update(dict(self.base,combo=11,multiplier=2),.2)
        self.assertTrue(dirty.patch)
        self.assertEqual((dirty.x,dirty.y,dirty.width,dirty.height),LEFT_RECT)
        self.assertEqual(self.hud.full_builds,1)

    def test_all_five_powerups_fit_left_patch_and_countdowns_stay_bounded(self):
        self.hud.update(self.base,0.)
        active=dict(self.base,shield_time=5.,magnet_time=4.,jetpack_time=6.,
                    boots_time=9.,coinboost_time=12.)
        update=self.hud.update(active,.2)
        self.assertEqual((update.x,update.y,update.width,update.height),LEFT_RECT)
        after=bytes(self.hud.surface.get_data())
        # Same displayed second does not redraw; no frame-rate countdown work.
        self.assertIsNone(self.hud.update(dict(active,jetpack_time=5.9),.31))
        tick=self.hud.update(dict(active,jetpack_time=4.9),1.3)
        self.assertEqual((tick.x,tick.y,tick.width,tick.height),LEFT_RECT)
        self.assertNotEqual(after,bytes(self.hud.surface.get_data()))
        self.assertEqual(self.hud.full_builds,1)

    def test_third_district_only_updates_goal_patch(self):
        self.hud.update(self.base,0.)
        update=self.hud.update(dict(self.base,district=2),.2)
        self.assertEqual((update.x,update.y,update.width,update.height),GOAL_RECT)
        self.assertEqual(self.hud.full_builds,1)

    def test_changing_goal_and_score_produces_two_bounded_patches(self):
        self.hud.update(self.base,0.)
        dirty=self.hud.update(dict(self.base,score=200,goal_progress=2),.2)
        self.assertEqual(len(dirty),2)
        self.assertEqual({(p.x,p.y,p.width,p.height) for p in dirty},{(706,18,232,79),GOAL_RECT})

    def test_live_score_and_status_changes_do_not_evict_prepared_glyphs(self):
        self.hud.update(self.base,0.)
        initial=set(self.hud.glyphs)
        for i in range(300):
            self.hud.update(dict(self.base,score=i*19,coins=i,distance=i,
                lives=1+i%3,shield_time=i%2,magnet_time=(i//2)%2),i*.11+.2)
        self.assertTrue(initial<=set(self.hud.glyphs))
        self.assertEqual(set(self.hud.glyphs),initial)
        self.assertEqual(self.hud.full_builds,1)

if __name__ == '__main__':unittest.main()
