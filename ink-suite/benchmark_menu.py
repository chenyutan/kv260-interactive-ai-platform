"""Synthetic Cairo render benchmark; never opens a camera or production UI."""
import argparse, importlib.util, json, math, statistics, time
from pathlib import Path
from types import SimpleNamespace
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--module',default='menu_view.py')
    parser.add_argument('--out',required=True)
    parser.add_argument('--frames',type=int,default=180)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('measured_menu',args.module)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    win=Gtk.Window();view=module.MenuView(lambda mode:None);win.add(view);win.resize(1024,550);win.show_all()
    while Gtk.events_pending():Gtk.main_iteration_do(False)
    clock=[1000.]
    if hasattr(module,'time'):module.time=SimpleNamespace(monotonic=lambda:clock[0])
    canvas=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,550);cr=cairo.Context(canvas)
    layouts=[0];original_text=module.text
    def counted_text(*args,**kwargs):layouts[0]+=1;return original_text(*args,**kwargs)
    module.text=counted_text
    for n in range(8):view.draw(view,cr)
    layouts[0]=0;wall=[];cpu=[];process=[]
    for n in range(args.frames):
        clock[0]+=1/30;now=clock[0]
        pointer=(.5+.35*math.cos(n*.10),.5+.28*math.sin(n*.10))
        view.update_selection(dict(cursor=pointer,hover=(n//45)%4,progress=(n%45)/45,sample_key=n))
        if hasattr(view,'animate'):view.animate(now)
        t=time.perf_counter();c=time.thread_time();p=time.process_time()
        view.draw(view,cr);canvas.flush()
        wall.append((time.perf_counter()-t)*1000);cpu.append((time.thread_time()-c)*1000);process.append((time.process_time()-p)*1000)
    def values(items):
        return dict(mean_ms=statistics.mean(items),p50_ms=statistics.median(items),p95_ms=sorted(items)[int(len(items)*.95)-1],max_ms=max(items))
    result=dict(camera_used=False,frames=args.frames,synthetic_hz=30,size=[1024,550],thread_cpu=values(cpu),process_cpu=values(process),wall=values(wall),pango_text_calls=layouts[0],static_cache_builds=getattr(view,'cache_builds',None))
    Path(args.out).write_text(json.dumps(result,indent=2));canvas.write_to_png(str(Path(args.out).with_suffix('.png')))
    print(json.dumps(result));win.destroy()


if __name__=='__main__':main()
