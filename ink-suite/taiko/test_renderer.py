import unittest
from unittest.mock import patch
try:
    import cairo
except ImportError:
    cairo=None


@unittest.skipIf(cairo is None,'Requires board Cairo; pure tests run on host')
class RendererTests(unittest.TestCase):
    def test_clap_rail_has_distinct_palms_and_side_hud(self):
        from .renderer import Renderer,LANE_X
        from .render_review import effect_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471)
        labels=[];original=renderer.text
        def collect(cr,label,x,*args,**kwargs):
            labels.append((label,x));return original(cr,label,x,*args,**kwargs)
        renderer.text=collect
        renderer.draw(cairo.Context(surface),1024,471,effect_state(),{})
        self.assertEqual(LANE_X['C'],.50)
        self.assertIn(('双手击掌',512.),labels)
        self.assertIn(('已击掌',512.),labels)
        for label in ('29','连 击','完美'):
            self.assertGreater(next(x for value,x in labels if value==label),1024*.78)
        # C has palms, B retains the drum-side 合 token, so the sprites differ.
        self.assertNotEqual(bytes(renderer.note_tiles['C'].get_data()),bytes(renderer.note_tiles['B'].get_data()))

    def test_grade_effects_follow_display_time_skip_expired_and_bound_cache(self):
        from .renderer import Renderer,FX_SPECS
        from .render_review import effect_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471);cr=cairo.Context(surface)
        for grade,count in (('Perfect',18),('Good',8),('OK',4),('Miss',0)):
            state=effect_state('C',grade,.05);state['feedback'][0]['time']=-100.
            renderer.draw(cr,1024,471,state,{})
            self.assertEqual(renderer.last_effect_targets,1)
            self.assertEqual(renderer.last_effect_particles,count)
            # Only receipt/display time controls the visual lifetime.
            for age in (-.01,FX_SPECS[grade][0]+.01):
                state['feedback'][0]['display_time']=state['time']-age
                renderer.draw(cr,1024,471,state,{})
                self.assertEqual(renderer.last_effect_targets,0)
        self.assertEqual(renderer.allocations,1)
        self.assertEqual(renderer.effect_allocations,71)
        self.assertLess(renderer.effect_cache_bytes,10*1024*1024)
        identities=tuple(id(tile) for sequence in renderer.grade_tiles.values() for tile in sequence)
        state=effect_state();state['feedback']=[]
        for lane in ('L','C','R'):
            state['feedback']+=effect_state(lane)['feedback']
        renderer.draw(cr,1024,471,state,{})
        self.assertEqual(renderer.last_effect_targets,3);self.assertEqual(renderer.last_effect_particles,54)
        self.assertEqual(identities,tuple(id(tile) for sequence in renderer.grade_tiles.values() for tile in sequence))

    def test_empty_clap_and_double_down_hit_keep_separate_feedback(self):
        from .renderer import Renderer
        from .render_review import effect_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471);cr=cairo.Context(surface)
        labels=[];original=renderer.text
        def collect(cr,label,*args,**kwargs):
            labels.append(label);return original(cr,label,*args,**kwargs)
        renderer.text=collect
        state=effect_state('C');state['feedback']=[]
        state['strike_feedback'][0].update(outcome='empty',reason='early')
        renderer.draw(cr,1024,471,state,{})
        self.assertIn('已击掌 · 偏早',labels);self.assertEqual(renderer.last_effect_targets,0)
        labels.clear();renderer.draw(cr,1024,471,effect_state('B'),{})
        self.assertEqual(renderer.last_effect_targets,2)
        self.assertIn('完美',labels);self.assertNotIn('合掌正中',labels)
        state['strike_feedback']=[];labels.clear()
        renderer.draw(cr,1024,471,state,{'clap':dict(valid=True,armed=True)})
        self.assertNotIn('双手展开 · 待合掌',labels)

    def test_pending_clap_token_drawn_over_the_gold_effect(self):
        from .renderer import Renderer
        from .render_review import effect_state
        outputs=[]
        for grade in ('Perfect','Good','OK'):
            renderer=Renderer();state=effect_state('C',grade,.10)
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471)
            renderer.draw(cairo.Context(surface),1024,471,state,{})
            surface.flush();data=bytes(surface.get_data());stride=surface.get_stride()
            # The opaque center of a pending note must be identical at all grades.
            cx,cy=512,round(471*.70);outputs.append(b''.join(data[y*stride+(cx-6)*4:y*stride+(cx+6)*4]
                    for y in range(cy-6,cy+6)))
        self.assertEqual(outputs[0],outputs[1]);self.assertEqual(outputs[1],outputs[2])

    def test_bursts_are_distinct_visible_late_and_do_not_paint_the_hud(self):
        from .renderer import Renderer,FX_SPECS
        from .render_review import effect_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471);cr=cairo.Context(surface)
        renderer.prepare(1024,471);coverage=[]
        for grade in ('Perfect','Good','OK'):
            state=effect_state('C',grade,FX_SPECS[grade][0]*.78)
            renderer.draw(cr,1024,471,state,{})
            self.assertEqual(renderer.last_effect_targets,1)
            # Count alpha coverage near the visual peak, independent of tile size.
            frame=renderer.grade_tiles[grade][2];frame.flush()
            coverage.append(sum(bytes(frame.get_data())[3::4]))
        self.assertGreater(coverage[0],coverage[1]*1.3)
        self.assertGreater(coverage[1],coverage[2]*1.3)
        # Draw only feedback into a blank surface: HUD wings and action hints
        # must remain untouched even when the Perfect cloud/ink tile is wider.
        clean=cairo.ImageSurface(cairo.FORMAT_ARGB32,1024,471);cc=cairo.Context(clean)
        renderer.frame=None;renderer.grade_effects(cc,1024,471,effect_state('L','Perfect',.25))
        clean.flush();data=bytes(clean.get_data());stride=clean.get_stride()
        for y in range(471):
            self.assertFalse(any(data[y*stride:y*stride+225*4]))
            self.assertFalse(any(data[y*stride+799*4:(y+1)*stride]))
        self.assertFalse(any(data[round(471*.87)*stride:]))

    def test_delayed_hit_and_empty_partial_show_full_received_feedback(self):
        from . import renderer as module
        from .engine import Game
        for lane,hit_at,expected_hint in (('L',2.,'已击鼓'),('L',1.,'已击鼓 · 偏早'),
                                           ('B',2.,'已击鼓 · 等另一手')):
            renderer=module.Renderer();game=Game(notes=[dict(id=1,time=2.,lane=lane)],duration=4.)
            game.pause(False);game.strike('L',hit_at,'left',display_time=hit_at+.33)
            game.advance(hit_at+.35)
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,560)
            labels=[];drums=[];original_text=renderer.text;original_paint=module.paint_tile
            def text(cr,label,*args,**kwargs):
                labels.append(label);return original_text(cr,label,*args,**kwargs)
            def paint(cr,tile,x,y,*args):
                if tile is renderer.drum_scaled:drums.append(y)
                return original_paint(cr,tile,x,y,*args)
            renderer.text=text
            with patch.object(module,'paint_tile',side_effect=paint):
                renderer.draw(cairo.Context(surface),1024,560,game.snapshot(),{})
            self.assertIn(expected_hint,labels)
            # A 330 ms transport delay must not consume the 160 ms bounce.
            self.assertGreater(drums[0],560*.67)
            if lane=='L' and hit_at==2.:
                labels.clear();game.advance(2.+.33+.70)
                renderer.draw(cairo.Context(surface),1024,560,game.snapshot(),{})
                self.assertIn('完美',labels)

    def test_native_tiles_equal_cairo_and_resize(self):
        from .renderer import Renderer
        from .engine import Game
        for w,h in ((640,360),(1024,471),(1024,560),(1280,720),(1920,1080)):
            normal=Renderer();fallback=Renderer();fallback.blitter=None
            for t in (25.,72.):
                game=Game();game.pause(False);game.advance(t)
                outputs=[]
                for renderer in (normal,fallback):
                    surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
                    renderer.draw(cairo.Context(surface),w,h,game.snapshot(),dict(notice='像素对照',finale_age=2.))
                    surface.flush();data=bytes(surface.get_data())
                    outputs.append(data[0::4]+data[1::4]+data[2::4])
                self.assertEqual(outputs[0],outputs[1],(w,h,t))

    def test_gameplay_hud_native_pixels_match_fallback(self):
        from .renderer import Renderer
        from .render_review import sample_state
        for finished in (False,True):
            outputs=[]
            for native in (True,False):
                renderer=Renderer()
                if not native:renderer.blitter=None
                surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,560)
                renderer.draw(cairo.Context(surface),1024,560,sample_state(finished),{'finale_age':2.})
                surface.flush();data=bytes(surface.get_data())
                outputs.append(data[0::4]+data[1::4]+data[2::4])
            self.assertEqual(*outputs)

    def test_hud_labels_stay_in_canvas_and_practice_uses_local_clock(self):
        from .renderer import Renderer
        from .render_review import sample_state
        for w,h in ((640,360),(1024,471),(1024,560),(1280,720),(1920,1080)):
            renderer=Renderer();record=[];original=renderer.text
            def inspect(cr,label,x,y,size=22,color=(.16,.22,.21),center=False,**kwargs):
                probe=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
                probe.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
                probe.set_font_size(size);ex=probe.text_extents(label)
                left=x-ex[2]/2+ex[0] if center else x+ex[0]
                record.append(label)
                self.assertGreaterEqual(left,-1.,(w,h,label))
                self.assertLessEqual(left+ex[2],w+1.,(w,h,label))
                self.assertGreaterEqual(y+ex[1],-1.,(w,h,label))
                self.assertLessEqual(y+ex[1]+ex[3],h+1.,(w,h,label))
                original(cr,label,x,y,size,color,center,**kwargs)
            renderer.text=inspect
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
            state=sample_state();state.update(time=29.5,start_time=28.8,duration=40.8,practice='alternate',lead_in_until=31.2)
            renderer.draw(cairo.Context(surface),w,h,state,{'hands':{'L':.1,'R':.55},'notice':'双手追踪正常'})
            self.assertIn('00:00 / 00:12',record)
            self.assertIn('准备 · 2',record)
            self.assertNotIn('双手追踪正常',record)
            renderer.draw(cairo.Context(surface),w,h,sample_state(True),{'finale_age':2.})

    def test_timing_hint_uses_the_judgment_engine_boundary(self):
        from .renderer import Renderer
        from .render_review import sample_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,560)
        record=[];original=renderer.text
        def collect(cr,label,*args,**kwargs):
            record.append(label);original(cr,label,*args,**kwargs)
        renderer.text=collect
        for timing,label in ((-35.,'节拍正好'),(35.,'节拍正好'),(-36.,'稍早 36 ms'),(36.,'稍晚 36 ms')):
            record.clear();state=sample_state();state['feedback'][-1]['timing_ms']=timing
            renderer.draw(cairo.Context(surface),1024,560,state,{})
            self.assertIn(label,record)

    def test_ok_feedback_and_four_result_columns_fit_actual_lcd_canvas(self):
        from .renderer import Renderer
        from .render_review import sample_state
        for w,h in ((640,360),(1024,471)):
            renderer=Renderer();record=[];original=renderer.text
            def inspect(cr,label,x,y,size=22,color=(.16,.22,.21),center=False,**kwargs):
                probe=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
                probe.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
                probe.set_font_size(size);ex=probe.text_extents(label)
                left=x-ex[2]/2+ex[0] if center else x+ex[0]
                box=(left,y+ex[1],left+ex[2],y+ex[1]+ex[3])
                record.append((label,box))
                self.assertGreaterEqual(box[0],-1.,(w,h,label))
                self.assertLessEqual(box[2],w+1.,(w,h,label))
                self.assertGreaterEqual(box[1],-1.,(w,h,label))
                self.assertLessEqual(box[3],h+1.,(w,h,label))
                return original(cr,label,x,y,size,color,center,**kwargs)
            renderer.text=inspect
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
            for finished in (False,True):
                state=sample_state(finished);state.update(judgment='relaxed',judgment_label='体感友好',
                    judgment_windows_ms={'Perfect':110,'Good':220,'OK':350},hit_window_ms=350,simultaneous_ms=220)
                state['counts']['OK']=9
                if not finished:state['feedback'][-1].update(grade='OK',display_time=state['time']-.02,timing_ms=300.)
                record.clear();renderer.draw(cairo.Context(surface),w,h,state,{'finale_age':2.})
                labels=[label for label,_ in record]
                self.assertIn('命中',labels)
                if finished:
                    self.assertIn('9',labels)
                    self.assertIn('体感友好 · 命中 ±350 ms · 合击 220 ms',labels)
                    columns=[next(box for label,box in record if label==key)
                             for key in ('完美','良好','命中','未命中')]
                    for left,right in zip(columns,columns[1:]):self.assertLess(left[2],right[0])
                else:
                    self.assertIn('稍晚 300 ms',labels)
                    for key in ('体感友好','完美 ±110 · 良好 ±220 ms','入拍 ±350 · 合击 220 ms'):
                        box=next(box for label,box in record if label==key)
                        self.assertGreater(box[1],h*.69)
                        self.assertLess(box[2],w*.20)
                        self.assertLess(box[3],h*.87)

    def test_judgment_window_notice_tracks_snapshot_and_old_results_still_render(self):
        from .renderer import Renderer
        from .render_review import sample_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471)
        record=[];original=renderer.text
        def collect(cr,label,*args,**kwargs):
            record.append(label);return original(cr,label,*args,**kwargs)
        renderer.text=collect
        state=sample_state();state.update(judgment='strict',judgment_label='严格节奏',
            judgment_windows_ms={'Perfect':110,'Good':220},hit_window_ms=220,simultaneous_ms=120)
        renderer.draw(cairo.Context(surface),1024,471,state,{})
        self.assertIn('严格节奏',record);self.assertIn('合击间隔 ≤120 ms',record)
        self.assertNotIn('入拍 ±350 · 合击 220 ms',record)
        # Archived strict snapshots lack the added OK counter and profile fields.
        record.clear();renderer.draw(cairo.Context(surface),1024,471,sample_state(True),{})
        self.assertIn('命中',record)
        self.assertIn('0',record)
        self.assertIn('严格节奏 · 命中 ±220 ms · 合击 120 ms',record)

    def test_notices_fit_their_allotted_width(self):
        from .renderer import Renderer
        from .render_review import sample_state
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,640,360)
        state=sample_state();state['paused']=True
        renderer.draw(cairo.Context(surface),640,360,state,dict(
            overlay='摄像头暂未就绪，请等待设备连接恢复后重新入场',
            notice='正在等待右手与左手关键点恢复，请稍候',
            audio_notice='音频设备尚未连接，可以通过转两圈呼出菜单选择无声练习'))

    def test_cache_stays_bounded(self):
        from .renderer import Renderer,TEXT_CACHE
        from .engine import Game
        renderer=Renderer();game=Game();game.pause(False)
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,640,360)
        for i in range(8):
            game.advance(i*.5);renderer.draw(cairo.Context(surface),640,360,game.snapshot(),{})
        self.assertEqual(renderer.allocations,1);self.assertLessEqual(len(TEXT_CACHE),256)

    def test_cached_hud_keeps_live_progress_and_invalidates_changed_content(self):
        from .renderer import Renderer
        from .render_review import sample_state
        cached=Renderer();reference=Renderer();reference.hud=reference._hud_vectors
        surfaces=[cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471) for _ in range(2)]
        state=sample_state();ui={}
        def compare():
            outputs=[]
            for renderer,surface in zip((cached,reference),surfaces):
                renderer.draw(cairo.Context(surface),1024,471,state,ui)
                surface.flush();data=bytes(surface.get_data())
                outputs.append(data[0::4]+data[1::4]+data[2::4])
            self.assertEqual(*outputs)
        compare();self.assertEqual(cached.hud_cache_builds,2)
        state['fever_remaining']=1.35
        compare();self.assertEqual(cached.hud_cache_builds,2)
        # Real-time bar changes need no glyph/wing rebuild. Content changes do.
        changes=(dict(combo=30),dict(accuracy=83.2),dict(rank='S'),dict(best_combo=52),
                 dict(fever_active=False,groove=42.),dict(judgment='relaxed',judgment_label='体感友好',
                    judgment_windows_ms={'Perfect':110,'Good':220,'OK':350},hit_window_ms=350,simultaneous_ms=220))
        for change in changes:
            before=cached.hud_cache_builds;state.update(change);compare()
            self.assertGreater(cached.hud_cache_builds,before)
        before=cached.hud_cache_builds;state['groove']=77.;compare()
        self.assertEqual(cached.hud_cache_builds,before)
        for key,value in (('label','双手合击'),('progress',11),('status','complete'),('earned',1200)):
            state['challenge'][key]=value;compare()
        for grade,lane,timing in (('OK','C',300.),('Good','L',-36.),('Perfect','B',35.),('Miss','C',None)):
            state['feedback'][-1].update(grade=grade,lane=lane,timing_ms=timing,display_time=state['time']-.02)
            compare()
        state['time']+=.85;compare()  # Receipt feedback expires within one tone level.
        state['time']+=.4;compare()   # A new tone level rebuilds the opaque backdrop.
        self.assertEqual(len(cached.hud_tiles),2)
        self.assertLess(cached.hud_cache_bytes,1024*1024)

    def test_finished_scroll_skips_covered_gameplay_and_cache_resizes(self):
        from .renderer import Renderer
        from .render_review import sample_state
        renderer=Renderer()
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,471)
        renderer.draw(cairo.Context(surface),1024,471,sample_state(),{})
        old_tiles=tuple(renderer.hud_tiles.values())
        with patch.object(renderer,'hud',side_effect=AssertionError('covered HUD rendered')):
            with patch.object(renderer,'notes',side_effect=AssertionError('covered notes rendered')):
                renderer.draw(cairo.Context(surface),1024,471,sample_state(True),{'finale_age':2.})
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,640,360)
        renderer.draw(cairo.Context(surface),640,360,sample_state(),{})
        self.assertEqual(renderer.allocations,2)
        self.assertTrue(all(tile not in old_tiles for tile in renderer.hud_tiles.values()))

    def test_fitted_width_cache_is_bounded_and_matches_current_font_options(self):
        from .renderer import Renderer
        renderer=Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,640,360);cr=cairo.Context(surface)
        for i in range(270):renderer.fitted_text(cr,'等待 %d'%i,100,100,15,80)
        self.assertEqual(len(renderer.fitted_widths),256)
        options=cairo.FontOptions();options.set_hint_metrics(cairo.HINT_METRICS_OFF)
        cr.set_font_options(options)
        renderer.fitted_text(cr,'等待 269',100,100,15,80)
        self.assertEqual(len(renderer.fitted_widths),256)


if __name__=='__main__':unittest.main()
