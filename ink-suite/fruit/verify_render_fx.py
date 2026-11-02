"""Offline real-Cairo visual review and damage/full-frame equivalence audit.

Windows may use cairocffi solely for this audit; the board uses native pycairo.
The GTK shim only constructs an unshown Window object for painting. No loop,
camera state, backend request, service, or visitor report is used.
"""
import argparse
from collections import OrderedDict,deque
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace,ModuleType
import unittest
from unittest.mock import patch


def prepare_cairo(deps=None):
    try:import cairo
    except ImportError:
        if deps:sys.path.insert(0,str(deps))
        if os.name=='nt':
            dll=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin'
            if dll.is_dir():globals()['_dll_directory']=os.add_dll_directory(str(dll))
        import cairocffi as cairo
        sys.modules['cairo']=cairo
    return cairo


class Area:
    def __init__(self,w,h):self.w,self.h=w,h
    def get_allocated_width(self):return self.w
    def get_allocated_height(self):return self.h
    def get_visual(self):return None


def window(source,root,width,height):
    original=root.parent/'FruitSlice/native/main.py'
    if not original.is_file():original=Path('/home/ubuntu/fruit-slice/native/main.py')
    if not original.is_file():raise RuntimeError('Original fruit dependencies/assets not found')
    sys.path.insert(0,str(original.parent));sys.path.insert(0,str(root))
    gi=ModuleType('gi');gi.require_version=lambda *a:None
    repository=ModuleType('gi.repository');repository.Gtk=SimpleNamespace(Window=object)
    repository.Gdk=SimpleNamespace();repository.GLib=SimpleNamespace()
    sys.modules['gi']=gi;sys.modules['gi.repository']=repository
    spec=importlib.util.spec_from_file_location('fruit_offscreen_'+str(abs(hash(str(source)))),original)
    module=importlib.util.module_from_spec(spec)
    exec(compile(Path(source).read_bytes(),str(source),'exec'),module.__dict__)
    app=object.__new__(module.Window)
    app.game=module.Game();app.game.start(7);app.game.state='playing';app.game.countdown=0
    app.score_style=module.ScoreStyle();app.item_fx=module.ItemEffects();app.cut_fx=module.CutEffects()
    app.trails={};app.halves=[];app.splashes=[];app.rotations=OrderedDict();app.rotation_bytes=0
    app.blitter=module.AlphaBlitter() if hasattr(module,'AlphaBlitter') else None
    app.presenter=module.RGB565Presenter() if hasattr(module,'RGB565Presenter') else None
    app.text_tiles=OrderedDict();app.damage_pixels=deque(maxlen=600)
    app.background=None;app.cache_key=None;app.scale=1.;app.origin=(0,0);app.frames=0
    app.render_times=deque(maxlen=600);app.play_draws=deque(maxlen=3600);app.area=Area(width,height)
    app.use_pose=False;app.reader=SimpleNamespace(get=lambda:None)
    app._load();app.rebuild(width,height)
    return app,module


def fixture(app,timepoint,dense=False,special=None):
    g=app.game;g.elapsed=timepoint;g.objects=[]
    kinds=('peach','mandarin','watermelon','apple','strawberry','dragonfruit')
    count=16 if dense else 4
    for i in range(count):
        g.spawn('normal',fruit=kinds[i%6],x=90+(i%8)*156+12*math.sin(timepoint*2+i),
                y=180+(i//8)*230+70*math.sin(timepoint*1.1+i),angle=(i*.36+timepoint*.22))
    app.halves=[[kinds[i%6],i%2,180+i*130+15*math.sin(timepoint+i),
        390+40*math.cos(timepoint+i),0,0,timepoint*.9+i,.7] for i in range(8 if dense else 2)]
    app.splashes=[];app.cut_fx.clear();app.item_fx.clear()
    for i in range(8 if dense else 2):
        app.cut_fx.trigger(dict(kind='normal',fruit=kinds[i%6],x=140+(i%4)*300,
            y=275+(i//4)*215,cut_angle=i*.5,combo=4))
    app.cut_fx.advance(.14+(timepoint%.22))
    g.freeze_until=g.double_until=0
    if special:
        if special=='ice':g.freeze_until=timepoint+5
        if special=='double':g.double_until=timepoint+8
        app.item_fx.trigger(dict(kind=special,x=640,y=370,delta=-30));app.item_fx.advance(.2)
    if hasattr(app.item_fx,'sync'):app.item_fx.sync(g)
    now=time.monotonic()
    app.trails={'right':deque([(180+i*30,430-i*12,now-.013*(15-i)) for i in range(16)],maxlen=20)}


def pixels(surface):
    surface.flush();return bytes(surface.get_data())


def render(app,cairo,width,height):
    out=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
    app.draw(app.area,cairo.Context(out))
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    p.add_argument('--test-deps');p.add_argument('--baseline');p.add_argument('--frames',type=int,default=100)
    p.add_argument('--visual-reference',help='Previously accepted painter for exact visual preservation audit')
    a=p.parse_args();cairo=prepare_cairo(a.test_deps);root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root));out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    tests=unittest.defaultTestLoader.discover(str(root/'fruit'),pattern='test_juice_effects.py',top_level_dir=str(root))
    result=unittest.TextTestRunner(verbosity=2).run(tests)
    report=dict(camera_used=False,backend_used=False,gtk_loop_used=False,
        source='real Cairo offscreen image surfaces',tests=result.testsRun,tests_passed=result.wasSuccessful(),
        hardware_fps_validated=False,benchmarks={})
    candidate,module=window(root/'native_windows/fruit.py',root,960,540)
    for kind in ('juice','ice','double','bomb'):
        fixture(candidate,5,dense=kind=='juice',special=None if kind=='juice' else kind)
        render(candidate,cairo,960,540).write_to_png(str(out/(kind+'.png')))
    if a.visual_reference:
        accepted,_=window(Path(a.visual_reference),root,960,540)
        preserved={}
        with patch('time.monotonic',return_value=100.):
            for kind in ('juice','ice','double','bomb'):
                for app in (candidate,accepted):
                    fixture(app,5,dense=kind=='juice',special=None if kind=='juice' else kind)
                    app.trails={}
                current=render(candidate,cairo,960,540);previous=render(accepted,cairo,960,540)
                preserved[kind]=pixels(current)==pixels(previous)
        report['accepted_visuals_unchanged']=preserved
    for name,source in [('candidate',root/'native_windows/fruit.py')]+([('baseline',Path(a.baseline))] if a.baseline else []):
        app,_=window(source,root,960,540)
        for dense in (False,True):
            samples=[]
            for frame in range(a.frames):
                fixture(app,3+frame/60,dense=dense)
                target=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);tc=cairo.Context(target)
                begin=time.perf_counter();app.draw(app.area,tc);samples.append((time.perf_counter()-begin)*1000)
            ordered=sorted(samples[10:])
            report['benchmarks'][name+('-dense' if dense else '-normal')]=dict(
                mean_ms=statistics.mean(ordered),p50_ms=statistics.median(ordered),
                p95_ms=ordered[int(len(ordered)*.95)],frames=len(ordered),
                rotation_bytes=getattr(app,'rotation_bytes',None),
                effect_bytes=sum(getattr(getattr(fx,'cache',None),'bytes',0) for fx in (app.cut_fx,app.item_fx)))
    # Give the persistent backbuffer the same partial clip that GTK uses. Its
    # output inside and outside the clip must equal a fresh full paint exactly.
    fixture(candidate,8,dense=True);candidate.trails={}
    with patch('time.monotonic',return_value=100.):
        render(candidate,cairo,960,540)
        frozen=pixels(candidate.backbuffer)
        target=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);tc=cairo.Context(target)
        rect=(150,100,200,150);tc.rectangle(*rect);tc.clip();candidate.draw(candidate.area,tc)
        report['unchanged_partial_equals_full']=pixels(candidate.backbuffer)==frozen
        report['partial_damage_ratio']=candidate.damage_pixels[-1]
        # Move one fruit completely across the screen; include old and new
        # sprite bounds and verify no old pixels survive.
        candidate.game.objects=candidate.game.objects[:1]
        candidate.halves=[];candidate.cut_fx.clear();candidate.item_fx.clear()
        candidate.game.objects[0].update(x=240,y=280)
        render(candidate,cairo,960,540)
        candidate.game.objects[0].update(x=990,y=420)
        tc=cairo.Context(target)
        for x,y in ((240,280),(990,420)):
            tc.rectangle((x-95)*.75,(y-95)*.75,190*.75,190*.75)
        tc.clip();candidate.draw(candidate.area,tc);partial=pixels(candidate.backbuffer)
        render(candidate,cairo,960,540)
        report['cross_screen_damage_equals_full']=partial==pixels(candidate.backbuffer)
        # Persistent backgrounds change the entire frame even when effects
        # have expired; the pre/post screen must equal fresh full composition.
        backgrounds=[]
        for kind in ('ice','double','bomb'):
            fixture(candidate,12,special=kind);candidate.trails={}
            candidate.item_fx.items[0]['age']=0.
            surface=render(candidate,cairo,960,540);surface.write_to_png(str(out/(kind+'-first.png')))
            backgrounds.append(pixels(candidate.backbuffer))
        report['special_first_frames_distinct']=len(set(backgrounds))==3
    report['passed']=all((report['tests_passed'],report['unchanged_partial_equals_full'],
        report['cross_screen_damage_equals_full'],report['special_first_frames_distinct'],report['partial_damage_ratio']<.2))
    if a.visual_reference:report['passed']=report['passed'] and all(report['accepted_visuals_unchanged'].values())
    reference,_=window(root/'native_windows/fruit.py',root,960,540)
    reference.blitter._lib=None
    for fx in (reference.cut_fx,reference.item_fx):
        if fx.cache.blitter:fx.cache.blitter._lib=None
    with patch('time.monotonic',return_value=100.):
        fixture(candidate,16,dense=True,special='double');candidate.trails={}
        fixture(reference,16,dense=True,special='double');reference.trails={}
        accelerated=render(candidate,cairo,960,540);ordinary=render(reference,cairo,960,540)
        report['native_matches_cairo']=pixels(accelerated)==pixels(ordinary)
        report['native_active']=candidate.blitter.enabled
        report['passed']=report['passed'] and report['native_matches_cairo']
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
