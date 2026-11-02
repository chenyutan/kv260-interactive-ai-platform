"""Offline GTK screenshots and full compositing timings; no camera access."""
import json
from pathlib import Path
import statistics
import time
from types import SimpleNamespace
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk, Gdk
from .main import AvatarPanel, ROOT, SCENES
from .rig import Rig, neutral_pose


def main():
    out = ROOT/'avatar/qa-board'
    out.mkdir(parents=True,exist_ok=True)
    args = SimpleNamespace(embed_socket=1,offline_preview=True,offline_image=None,
        base='http://127.0.0.1:1',works=out/'works',fps=4.)
    panel = AvatarPanel(args)
    window = Gtk.Window()
    window.resize(1024,600)
    window.add(panel)
    window.show_all()
    samples=json.loads((ROOT/'avatar/fixtures/recorded_poses.json').read_text())['samples']
    def pump():
        end=time.monotonic()+.15
        while time.monotonic()<end:
            while Gtk.events_pending() and time.monotonic()<end:
                Gtk.main_iteration_do(False)
            time.sleep(.003)
    def screenshot(name):
        pump()
        width,height=window.get_size()
        Gdk.pixbuf_get_from_window(window.get_window(),0,0,width,height).savev(str(out/(name+'.png')),'png',[],[])
        assert width==1024 and height==600,(width,height)
    for i,(scene,_) in enumerate(SCENES):
        panel.choose_scene(scene)
        rig=Rig()
        name=('raised','resting','crossed')[i]
        for frame in range(50):
            state=dict(samples[name]['state'],pid='recorded-offline',sequence=frame,timestamp_seconds=frame/30.)
            panel.pose=rig.update(state,frame/30.)
        screenshot('ui-'+scene+'-1024x600')
        image=cairo.ImageSurface(cairo.FORMAT_RGB24,1280,720)
        panel.paint(cairo.Context(image),1280,720)
        image.write_to_png(str(out/('art-'+scene+'-1280x720.png')))
    panel.show_page('photo')
    screenshot('ui-photo-1024x600')
    panel.show_page('avatar')
    panel.pose=neutral_pose()
    records=[]
    for width,height in ((890,500),(1280,720)):
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
        context=cairo.Context(surface)
        panel.paint(context,width,height)
        times=[]; cpu_times=[]
        for _ in range(90):
            start=time.perf_counter()
            cpu_start=time.thread_time()
            panel.paint(context,width,height)
            surface.flush()
            cpu_times.append((time.thread_time()-cpu_start)*1000)
            times.append((time.perf_counter()-start)*1000)
        records.append(dict(width=width,height=height,samples=len(times),mean_ms=statistics.mean(times),
            p95_ms=sorted(times)[int(len(times)*.95)-1],max_ms=max(times),
            cpu_mean_ms=statistics.mean(cpu_times),cpu_p95_ms=sorted(cpu_times)[int(len(cpu_times)*.95)-1]))
    panel.operation('shutdown')
    window.destroy()
    if panel.photo:panel.photo.preview_thread.join(2)
    report=dict(platform='KV260 ARM system Python native Cairo',input='saved pose fixture, no network/camera',
        measures='cached background + character full composite only, excludes pose inference/display scanout',
        windows=[1024,600],timings=records)
    (out/'ui-and-render-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':main()
