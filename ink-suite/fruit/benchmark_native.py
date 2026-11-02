"""Fixed synthetic load on an isolated GTK display; never a camera or audio.

Run baseline/candidate with the same display, viewport and scene. The original
Game implementation is untouched: only its visual fixture is replenished so
both painters see 16 whole fruit + 8 halves + 8 hit requests in a dense scene.
This is a rendering benchmark, not a scoring/input/human acceptance test.
"""
import argparse
import cProfile
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch


class WarmDrawSamples:
    """Measure completed callbacks after startup and a real warmup window.

    Timestamps are supplied by the callback, keeping this sampler independent
    of GTK and allowing portable tests for a several-second initial rebuild.
    """
    def __init__(self,warmup=2.):
        self.warmup=warmup;self.first_begin=None;self.first_end=None
        self.samples=[];self.gaps=[];self.completed_gaps=[]
        self.first_sample_begin=None;self.first_sample_end=None
        self.last_sample_begin=None;self.last_sample_end=None

    def is_warm(self,begin):
        return self.first_end is not None and begin>=self.first_end+self.warmup

    def record(self,begin,end):
        if not math.isfinite(begin) or not math.isfinite(end) or end<begin:
            raise ValueError('Draw timestamps must be finite and ordered')
        if self.first_end is None:
            self.first_begin,self.first_end=begin,end
            return False
        if not self.is_warm(begin):
            # Never mix a pre-warm predecessor into a steady-state interval.
            self.last_sample_begin=self.last_sample_end=None
            return False
        self.samples.append((end-begin)*1000)
        if self.last_sample_begin is not None:
            self.gaps.append(begin-self.last_sample_begin)
            self.completed_gaps.append(end-self.last_sample_end)
        if self.first_sample_begin is None:
            self.first_sample_begin,self.first_sample_end=begin,end
        self.last_sample_begin,self.last_sample_end=begin,end
        return True

    def summary(self):
        gaps=sorted(gap*1000 for gap in self.gaps)
        return dict(
            startup_draw_ms=(self.first_end-self.first_begin)*1000 if self.first_end is not None else None,
            warm_seconds_measured=(self.last_sample_end-self.first_sample_begin) if self.samples else 0.,
            actual_draw_callbacks=len(self.samples),
            actual_draw_fps=1/statistics.mean(self.gaps) if self.gaps else 0.,
            completed_sample_fps=1/statistics.mean(self.completed_gaps) if self.completed_gaps else 0.,
            gap_ms_p50=statistics.median(gaps) if gaps else None,
            gap_ms_p95=gaps[int(len(gaps)*.95)] if gaps else None,
            gap_ms_max=max(gaps) if gaps else None)


def choose_suite_root(source,helper_root,explicit=None):
    """Use the source's own packages even when one helper drives both builds."""
    if explicit:return Path(explicit).resolve()
    source=Path(source).resolve()
    for root in (source.parent.parent,source.parent):
        if (root/'fruit').is_dir() and (root/'sword').is_dir():return root
    return Path(helper_root).resolve()


def source_with_alpha_wrapper(source,wrapper=None):
    if wrapper is None:return source
    original='from sword.alpha_blit import AlphaBlitter'
    if source.count(original)!=1:
        raise ValueError('An alpha override requires exactly one known renderer import')
    replacement=(
        'import importlib.util as _fruit_alpha_importlib\n'
        f'_fruit_alpha_spec=_fruit_alpha_importlib.spec_from_file_location("fruit_benchmark_alpha_override",{str(Path(wrapper).resolve())!r})\n'
        '_fruit_alpha_module=_fruit_alpha_importlib.module_from_spec(_fruit_alpha_spec)\n'
        '_fruit_alpha_spec.loader.exec_module(_fruit_alpha_module)\n'
        'AlphaBlitter=_fruit_alpha_module.AlphaBlitter')
    return source.replace(original,replacement)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    p.add_argument('--scene',choices=('normal','dense'),default='dense')
    p.add_argument('--seconds',type=int,default=15)
    p.add_argument('--width',type=int,default=960);p.add_argument('--height',type=int,default=540)
    p.add_argument('--fruit-main',default='/home/ubuntu/fruit-slice/native/main.py')
    p.add_argument('--suite-root',help='Override source suite dependency root')
    p.add_argument('--alpha-wrapper',help='Opt-in isolated alpha wrapper; renderer files are not changed')
    p.add_argument('--profile',help='Optional full draw and tick callback cProfile output')
    p.add_argument('--profile-frames',type=int,default=20)
    a=p.parse_args();helper_root=Path(__file__).resolve().parents[1]
    root=choose_suite_root(a.source,helper_root,a.suite_root)
    sys.path.insert(0,str(Path(a.fruit_main).parent));sys.path.insert(0,str(root))
    import cairo
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk,GLib
    # The fixture and timing helper are identical for both builds. Runtime
    # fruit/sword/render_policy imports still resolve to the source root above.
    fixture_spec=importlib.util.spec_from_file_location('uniform_fruit_fixture',helper_root/'fruit/verify_render_fx.py')
    fixture_module=importlib.util.module_from_spec(fixture_spec);fixture_spec.loader.exec_module(fixture_module)
    fixture=fixture_module.fixture
    spec=importlib.util.spec_from_file_location('fruit_render_benchmark',a.fruit_main)
    module=importlib.util.module_from_spec(spec)
    source=source_with_alpha_wrapper(Path(a.source).read_text(encoding='utf-8'),a.alpha_wrapper)
    exec(compile(source,a.source,'exec'),module.__dict__)
    timing=WarmDrawSamples();raw_ticks=[];fixtures=[];network=[];app=None
    steady_started=None;steady_cpu_start=None
    raw_draw=module.Window.draw;raw_tick=module.Window.tick
    started=time.monotonic();cpu_start=time.process_time()
    profiler=cProfile.Profile() if a.profile else None;profiled=0;profiled_ticks=0

    def denied(*args,**kw):
        network.append('blocked network request')
        raise AssertionError('Native benchmark must not request camera/backend')

    def draw(self,*args):
        nonlocal profiled,steady_started,steady_cpu_start
        begin=time.monotonic()
        warmed=timing.is_warm(begin)
        if warmed and steady_started is None:
            steady_started,steady_cpu_start=begin,time.process_time()
        sample=profiler is not None and warmed and profiled<a.profile_frames
        if sample:profiler.enable()
        try:result=raw_draw(self,*args)
        finally:
            if sample:profiler.disable();profiled+=1
        timing.record(begin,time.monotonic())
        return result

    def tick(self):
        nonlocal profiled_ticks
        begin=time.monotonic()
        warmed=timing.is_warm(begin)
        sample=profiler is not None and warmed and profiled_ticks<a.profile_frames
        if sample:profiler.enable()
        try:
            result=raw_tick(self);split=time.monotonic()
            fixture(self,self.game.elapsed,dense=a.scene=='dense')
        finally:
            if sample:profiler.disable();profiled_ticks+=1
        if warmed:
            raw_ticks.append((split-begin)*1000)
            fixtures.append((time.monotonic()-split)*1000)
        return result

    def finish(self):
        ordered=sorted(timing.samples);now=time.monotonic()
        visual=self.area.get_visual()
        report=dict(camera_used=False,backend_used=False,audio_used=False,
            visitor_data_used=False,synthetic=True,production_rules_changed=False,
            source=str(a.source),suite_root=str(root),fixture_source=str(fixture_spec.origin),
            runtime_packages={name:getattr(sys.modules.get(name),'__file__',None) for name in ('fruit.render_regions','fruit.juice_effects','render_policy','sword.alpha_blit')},
            alpha_wrapper=str(Path(a.alpha_wrapper).resolve()) if a.alpha_wrapper else None,
            alpha_native_backend=getattr(getattr(self,'blitter',None),'backend',None),
            scene=a.scene,display=os.environ.get('DISPLAY'),
            render_cap=os.environ.get('INK_SUITE_RENDER_CAP','default'),
            output_size=[self.area.get_allocated_width(),self.area.get_allocated_height()],
            render_size=list(self.cache_key),visual_depth=visual.get_depth() if visual else None,
            seconds=now-started,warmup_seconds=timing.warmup,
            full_draw_ms_mean=statistics.mean(ordered) if ordered else None,
            full_draw_ms_p50=statistics.median(ordered) if ordered else None,
            full_draw_ms_p95=ordered[int(len(ordered)*.95)] if ordered else None,
            full_draw_ms_max=max(ordered) if ordered else None,
            raw_tick_ms_mean=statistics.mean(raw_ticks) if raw_ticks else None,
            fixture_ms_mean=statistics.mean(fixtures) if fixtures else None,
            process_cpu_percent_one_core=100*(time.process_time()-cpu_start)/(now-started),
            steady_cpu_percent_one_core=100*(time.process_time()-steady_cpu_start)/(now-steady_started) if steady_started is not None else None,
            playing_state=self.game.state,fruit_count=len(self.game.objects),
            half_count=len(self.halves),cut_effect_count=len(self.cut_fx.items),
            effect_request_count=8 if a.scene=='dense' else 2,
            damage_ratio_mean=(sum(self.damage_pixels)/len(self.damage_pixels) if getattr(self,'damage_pixels',None) else None),
            rotation_bytes=getattr(self,'rotation_bytes',None),
            effect_bytes=sum(getattr(getattr(fx,'cache',None),'bytes',0) for fx in (self.cut_fx,self.item_fx)),
            special_background_bytes=getattr(self.item_fx,'background_bytes',0),
            presentation_backend=(self.presenter.backend if getattr(self,'presentation_is_rgb565',False) else 'rgb24-direct'),
            native_sprite_calls=getattr(getattr(self,'blitter',None),'native_calls',0),
            native_present_calls=getattr(getattr(self,'presenter',None),'calls',0),
            profiled_draws=profiled,profiled_ticks=profiled_ticks,
            network_attempts=network,physical_scanout_measured=False)
        report.update(timing.summary())
        report['passed']=bool(timing.samples) and not network and self.game.state=='playing'
        output=Path(a.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if profiler is not None:
            profile_path=Path(a.profile);profile_path.parent.mkdir(parents=True,exist_ok=True)
            profiler.dump_stats(str(profile_path))
        if hasattr(self,'backbuffer'):self.backbuffer.write_to_png(str(output.with_suffix('.png')))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        self.destroy();return False

    def finish_after_warm_samples():
        if timing.first_end is not None and time.monotonic()>=timing.first_end+timing.warmup+a.seconds:
            return finish(app)
        return True

    with patch.object(module.Window,'draw',draw),patch.object(module.Window,'tick',tick),\
         patch.object(module.Window,'finish_benchmark',finish),\
         patch.object(module.Window,'fullscreen',lambda self:None),\
         patch.object(module.Window,'focus_out',lambda *args:False),\
         patch('urllib.request.urlopen',side_effect=denied):
        parameters=SimpleNamespace(pose_url='http://127.0.0.1:1',pose=False,
            live_test=False,benchmark=0,report=a.output)
        app=module.Window(parameters)
        app.args.benchmark=False # no synthetic collision input; visuals only
        app.toolbar.set_no_show_all(True);app.toolbar.hide()
        app.resize(a.width,a.height)
        app.game.start(7)
        app.game.state='playing';app.game.countdown=0
        fixture(app,0.,dense=a.scene=='dense')
        GLib.timeout_add(100,finish_after_warm_samples)
        Gtk.main()
    result=json.loads(Path(a.output).read_text(encoding='utf-8'))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
