"""Offline-only rhythm HUD pictures and comparable Cairo render timings."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import time
import platform

import cairo


def sample_state(finished=False):
    counts={'Perfect':65, 'Good':6, 'Miss':2}
    accuracy=100*(counts['Perfect']+.65*counts['Good'])/sum(counts.values())
    return dict(time=72. if finished else 36.12, duration=72., start_time=0.,
                lead_in_until=2.4, practice='full', difficulty='hard',
                difficulty_label='雷鼓破阵', paused=False, finished=finished,
                score=91720, combo=29, best_combo=42, accuracy=accuracy, rank='A',
                groove=100., fever_active=not finished,
                fever_remaining=3.2 if not finished else 0., bonus_score=3600,
                counts=counts, total_notes=sum(counts.values()),
                notes=[dict(id=i, time=at, lane=lane, status='pending') for i,(at,lane) in enumerate(((36.0,'L'),(36.6,'R'),(37.2,'L'),(37.8,'R'),(39.,'B')))],
                feedback=[dict(note_id=80, lane='B', grade='Perfect', time=36.03,
                               combo=29, timing_ms=-26., timing='on_time', multiplier=2, points=2080)],
                challenge=dict(label='交替应和', progress=7, target=12, status='active', reward=1200, earned=0),
                challenges=[dict(label='交替应和', progress=12, target=12, status='complete', reward=1200, earned=1200),
                            dict(label='双手合击', progress=3, target=4, status='failed', reward=1600, earned=0),
                            dict(label='收尾齐鸣', progress=15, target=15, status='complete', reward=2400, earned=2400)])


def effect_state(lane='C',grade='Perfect',age=.16):
    """Receipt timestamps make preview FX independent from rhythm/audio time."""
    state=sample_state();state.update(time=36.12,fever_active=False,groove=72.)
    state['notes']=[dict(id=i,time=36.12+at,lane=side,status='pending')
                    for i,(at,side) in enumerate(((.0,lane),(.95,'C'),(1.35,'L'),(1.7,'R'),(2.5,'B')))]
    state['feedback']=[dict(note_id=80,lane=lane,grade=grade,time=35.4,
                            display_time=state['time']-age,combo=29,timing_ms=20.)]
    sides=('L','R') if lane=='B' else (lane,)
    state['strike_feedback']=[dict(serial=i,hand=side,time=35.4,display_time=state['time']-age,
                                  outcome='hit',reason='hit') for i,side in enumerate(sides)]
    return state


def effect_review(module,out,phase,frames):
    """Nine grade/lane PNGs plus a three-target budget stress microbenchmark."""
    renderer=module.Renderer();w,h=1024,471
    surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h);context=cairo.Context(surface)
    overview=cairo.ImageSurface(cairo.FORMAT_RGB24,w*3//2,(h+42)*3//2)
    oc=cairo.Context(overview);oc.set_source_rgb(.97,.94,.86);oc.paint()
    for row,grade in enumerate(('Perfect','Good','OK')):
        for col,lane in enumerate(('L','C','R')):
            state=effect_state(lane,grade,age=.12 if grade=='OK' else .22 if grade=='Good' else .25)
            state['notes'][0]['status']=grade
            renderer.draw(context,w,h,state,{})
            surface.write_to_png(str(out/f'{phase}-{lane}-{grade}-1024x471.png'))
            oc.save();oc.translate(col*w/2,row*(h+42)/2);oc.scale(.5,.5)
            oc.set_source_surface(surface,0,0);oc.paint()
            oc.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
            oc.set_font_size(22);oc.set_source_rgb(.16,.22,.21);oc.move_to(18,h+28)
            oc.show_text(f'{lane} · {grade}');oc.restore()
    overview.write_to_png(str(out/f'{phase}-grade-contact-sheet.png'))
    for grade in ('Perfect','Good','OK'):
        duration=module.FX_SPECS[grade][0]
        strip=cairo.ImageSurface(cairo.FORMAT_RGB24,w*2,(h+42)//2);sc=cairo.Context(strip)
        sc.set_source_rgb(.97,.94,.86);sc.paint()
        for col,fraction in enumerate((.06,.24,.55,.85)):
            age=duration*fraction;state=effect_state('C',grade,age);state['notes'][0]['status']=grade
            renderer.draw(context,w,h,state,{})
            sc.save();sc.translate(col*w/2,0);sc.scale(.5,.5);sc.set_source_surface(surface,0,0);sc.paint()
            sc.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
            sc.set_font_size(22);sc.set_source_rgb(.16,.22,.21);sc.move_to(18,h+28)
            sc.show_text(f'{grade} · {age:.2f}s');sc.restore()
        strip.write_to_png(str(out/f'{phase}-C-{grade}-motion-strip.png'))
    for grade in ('Miss',):
        renderer.draw(context,w,h,effect_state('C',grade,.10),{})
        surface.write_to_png(str(out/f'{phase}-C-{grade}-1024x471.png'))
    state=effect_state('B');state['paused']=True
    renderer.draw(context,w,h,state,{'overlay':'抬手下击鼓 · 双手向内击掌'})
    surface.write_to_png(str(out/f'{phase}-controls-1024x471.png'))
    state=effect_state();state['feedback']=[];state['strike_feedback'][0].update(outcome='empty',reason='early')
    renderer.draw(context,w,h,state,{})
    surface.write_to_png(str(out/f'{phase}-empty-clap-1024x471.png'))
    state=effect_state();state['feedback']=[];state['strike_feedback']=[]
    for side in ('L','C','R'):
        state['feedback'].extend(effect_state(side)['feedback'])
        state['strike_feedback'].extend(effect_state(side)['strike_feedback'])
    for i in range(12):renderer.draw(context,w,h,state,{})
    values=[]
    for i in range(frames):
        state['time']=36.12+(i%12)*.025
        start=time.perf_counter();renderer.draw(context,w,h,state,{});surface.flush()
        values.append((time.perf_counter()-start)*1000.)
    return dict(frames=len(values),median_ms=statistics.median(values),
                p95_ms=sorted(values)[max(0,int(len(values)*.95)-1)],
                max_grade_targets=3,max_grade_droplets=54,max_input_droplets=36,
                effect_cache_bytes=renderer.effect_cache_bytes,
                effect_tiles=renderer.effect_allocations,
                cache_builds=renderer.allocations,
                native_backend=renderer.blitter.backend if renderer.blitter else 'cairo')


def load_renderer(path,name):
    spec=importlib.util.spec_from_file_location('taiko.'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.__file__=str(Path(__file__).with_name('renderer.py'))
    return module


def compare_effects(before,after,out,phase):
    """Three before/after full-canvas grade comparisons at equal receipt age."""
    w,h=1024,471;renderers=(before.Renderer(),after.Renderer())
    sheet=cairo.ImageSurface(cairo.FORMAT_RGB24,w,(h+42)*3//2);sc=cairo.Context(sheet)
    sc.set_source_rgb(.97,.94,.86);sc.paint()
    for row,(grade,age) in enumerate((('Perfect',.25),('Good',.22),('OK',.12))):
        full=cairo.ImageSurface(cairo.FORMAT_RGB24,w*2,h+42);fc=cairo.Context(full)
        fc.set_source_rgb(.97,.94,.86);fc.paint()
        for col,renderer in enumerate(renderers):
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
            state=effect_state('C',grade,age);state['notes'][0]['status']=grade
            renderer.draw(cairo.Context(surface),w,h,state,{})
            fc.set_source_surface(surface,col*w,0);fc.paint()
            fc.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
            fc.set_font_size(22);fc.set_source_rgb(.16,.22,.21);fc.move_to(col*w+18,h+28)
            fc.show_text(('旧版' if col==0 else '增强版')+f' · {grade} · {age:.2f}s')
        full.write_to_png(str(out/f'{phase}-C-{grade}-before-after.png'))
        sc.save();sc.translate(0,row*(h+42)/2);sc.scale(.5,.5);sc.set_source_surface(full,0,0);sc.paint();sc.restore()
    sheet.write_to_png(str(out/f'{phase}-grade-before-after-sheet.png'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--renderer')
    parser.add_argument('--compare-renderer',help='Optional preserved baseline for grade comparison PNGs')
    parser.add_argument('--phase',default='after')
    parser.add_argument('--frames',type=int,default=120)
    args=parser.parse_args()
    if args.renderer:
        module=load_renderer(args.renderer,'_review_renderer')
    else:
        from . import renderer as module
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    stats={}
    for w,h in ((640,360),(1024,471),(1024,560),(1280,720),(1920,1080)):
        renderer=module.Renderer();surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        context=cairo.Context(surface);state=sample_state()
        start=time.perf_counter();renderer.prepare(w,h);prepare_ms=(time.perf_counter()-start)*1000.
        ui=dict(hands={'L':.1,'R':.55},notice='',audio_notice='',finale_age=2.)
        for i in range(12):renderer.draw(context,w,h,state,ui)
        values=[]
        for i in range(args.frames):
            state['time']=36.03+(i%12)*.025
            start=time.perf_counter();renderer.draw(context,w,h,state,ui);surface.flush()
            values.append((time.perf_counter()-start)*1000.)
        surface.write_to_png(str(out/f'{args.phase}-game-{w}x{h}.png'))
        stats[f'{w}x{h}']=dict(frames=len(values),median_ms=statistics.median(values),
                               p95_ms=sorted(values)[int(len(values)*.95)-1],
                               allocations=renderer.allocations,
                               prepare_once_ms=prepare_ms,
                               effect_cache_bytes=getattr(renderer,'effect_cache_bytes',0),
                               effect_tiles=getattr(renderer,'effect_allocations',0))
        renderer.draw(context,w,h,sample_state(True),ui)
        surface.write_to_png(str(out/f'{args.phase}-result-{w}x{h}.png'))
        if w==1024:
            state=sample_state();state.update(time=29.5,start_time=28.8,duration=41.25,
                practice='alternate',lead_in_until=31.2,score=0,combo=0,accuracy=0.,rank='--',
                best_combo=0,fever_active=False,fever_remaining=0.,groove=0.,feedback=[],
                challenge=dict(label='交替应和',progress=0,target=12,status='pending'))
            renderer.draw(context,w,h,state,ui)
            surface.write_to_png(str(out/f'{args.phase}-practice-{w}x{h}.png'))
    result=dict(environment='WSL Cairo offscreen; no board/display/camera/audio test',
                architecture=platform.machine(),cairo_version=cairo.version,timings=stats)
    if hasattr(module,'FX_SPECS'):result['three_target_effect_stress']=effect_review(module,out,args.phase,args.frames)
    if args.compare_renderer:compare_effects(load_renderer(args.compare_renderer,'_compare_renderer'),module,out,args.phase)
    if hasattr(module,'FX_SPECS'):
        result['grade_lifetimes_seconds']={key:spec[0] for key,spec in module.FX_SPECS.items()}
    (out/f'{args.phase}-render-timings.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
