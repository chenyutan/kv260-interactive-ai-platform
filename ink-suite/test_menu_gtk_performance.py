"""25 Hz synthetic menu input under real GTK; no camera, HTTP or production UI.

Run in an isolated Xvfb server. Timings include a display sync so asynchronous
X drawing is accounted for. They do not claim physical display scan-out time.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import statistics
import threading
import time

import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--module',default='menu_view.py')
    parser.add_argument('--out',required=True)
    parser.add_argument('--seconds',type=float,default=8.)
    parser.add_argument('--width',type=int,default=960)
    parser.add_argument('--height',type=int,default=540)
    parser.add_argument('--state-priority',type=int,default=0)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('menu_under_test',args.module)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    timings=[];input_lags=[];draw_times=[];inputs=[]
    original=module.MenuView.draw
    start=time.monotonic()
    def measured(self,widget,cr):
        begin=time.monotonic();result=original(self,widget,cr)
        Gdk.Display.get_default().sync()
        if begin-start>.7:
            timings.append((time.monotonic()-begin)*1000);draw_times.append(begin)
        return result
    module.MenuView.draw=measured
    win=Gtk.Window();view=module.MenuView(lambda _:None)
    win.add(view);win.resize(args.width,args.height);win.show_all()
    stop=threading.Event();lock=threading.Lock();latest=[None];pending=[False]
    def deliver():
        with lock:sample=latest[0];pending[0]=False
        if sample is None:return False
        n,created=sample
        view.update_selection(dict(cursor=(.5+.35*math.cos(n*.10),.5+.28*math.sin(n*.10)),
            hover=(n//45)%4,progress=(n%45)/45,sample_key=n))
        if created-start>.7:input_lags.append((time.monotonic()-created)*1000);inputs.append(n)
        return False
    def producer():
        n=0;due=time.monotonic()
        while not stop.is_set():
            with lock:
                latest[0]=(n,time.monotonic())
                if not pending[0]:
                    pending[0]=True;GLib.idle_add(deliver,priority=args.state_priority)
            n+=1;due+=.04;stop.wait(max(0.,due-time.monotonic()))
    threading.Thread(target=producer,daemon=True).start()
    def finish():stop.set();Gtk.main_quit();return False
    GLib.timeout_add(int(args.seconds*1000),finish,priority=0)
    Gtk.main();stop.set()
    def summary(values):
        return dict(mean=statistics.mean(values),p95=sorted(values)[min(len(values)-1,int(len(values)*.95))],max=max(values)) if values else None
    report=dict(camera_used=False,http_used=False,synthetic_input_hz=25,
        requested_size=[args.width,args.height],observed_skeleton_updates=False,
        requested_seconds=args.seconds,state_priority=args.state_priority,
        gtk_draw_ms=summary(timings),input_delivery_ms=summary(input_lags),
        frame_intervals_ms=summary([(b-a)*1000 for a,b in zip(draw_times,draw_times[1:])]),
        gtk_draw_hz=(len(draw_times)-1)/(draw_times[-1]-draw_times[0]) if len(draw_times)>1 else 0,
        draws=len(timings),input_updates=len(inputs),cache_builds=view.cache_builds,
        memory_backbuffer=hasattr(view,'backbuffer') and view.backbuffer is not None)
    Path(args.out).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report));win.destroy()


if __name__=='__main__':main()
