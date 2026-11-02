"""Offline GTK screenshots and full renderer timings in an isolated Xvfb.

Run ``DISPLAY=:98 python3 -m sword.verify_ui --output /path/to/evidence``.
Every snapshot is a declared synthetic scenario. No camera or API is used.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
import statistics
import time
from types import SimpleNamespace
from unittest.mock import patch

import cairo
from .main import SwordPanel, ROOT, Gtk, Gdk, GLib, START_RECT
from .engine import Game


def fixtures():
    game = Game(seed=260, duration=75)
    ready = game.snapshot()
    game.start(countdown=3)
    countdown = game.snapshot()
    game.start(countdown=0)
    for _ in range(120):
        game.update(1/30, dict(x=.48,y=.45,valid=True,status='tracking',special=False))
    playing = game.snapshot()
    playing.update(score=345, combo=7, energy=.72)
    special = deepcopy(playing)
    special.update(special_time=1.8, energy=0.)
    special['effects'] = [dict(kind='special',x=.34,y=.48,age=.35,ttl=2.3,seed=260)]
    boss = deepcopy(playing)
    boss.update(time=68., progress=68/75., scene=2, boss_phase=3,
                boss_name='玄墨魁首',wave_name='终阵 · 环形墨雨',
                message='闪避墨雨 · 击破玄墨魁首',power=4,kills=23,graze=15)
    boss['entities'] = [dict(id=260,kind='boss',x=.50,y=.26,r=.085,
        health=220,max_health=700,style='ring',warning=0.,age=23.,hit=False)]
    boss['bullets'] = [dict(id=300+i,x=.07+(i%15)*.06,y=.35+(i//15)*.12,
        r=.007,style=i%3,hit=False) for i in range(60)]
    boss['shots'] = [dict(id=500+i,x=.44+(i%3)*.03,y=.39+(i//3)*.12,
        r=.005,style=0,hit=False) for i in range(9)]
    won = deepcopy(boss)
    won.update(phase='won', score=1865, combo=21, progress=1., message='一剑开天 · 山河与你共鸣')
    celebrating = deepcopy(won)
    celebrating.update(special_time=2.3, effects=[dict(kind='special',x=.555,y=.48,age=0.,ttl=2.3,seed=260)])
    lost = deepcopy(playing)
    lost.update(phase='lost', lives=0, message='剑意暂歇 · 再试一次，山河仍在等你')
    return dict(ready=ready,countdown=countdown,playing=playing,special=special,
                boss=boss,celebrating=celebrating,won=won,lost=lost)


def pump(seconds=.13):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        for _ in range(8):
            if not Gtk.events_pending() or time.monotonic() >= deadline:
                break
            Gtk.main_iteration_do(False)
        time.sleep(.002)


def audit_hud_alignment(panel, image, snapshot):
    """Compare native-window HUD pixels with an identity-origin reference.

    GTK passes a Cairo transform containing the toolbar's vertical offset.
    Allocation-only checks cannot detect a cached HUD accidentally drawn at
    device (0, 0), so verify all three top panels at their actual window origin.
    """
    width = panel.canvas.get_allocated_width()
    height = panel.canvas.get_allocated_height()
    origin = panel.canvas.translate_coordinates(panel.get_toplevel(), 0, 0)
    assert origin is not None, 'Canvas could not translate to the window'
    ox, oy = origin
    assert oy >= 32, ('Native toolbar offset missing', origin)
    assert oy+height <= image.get_height(), ('Canvas exceeds screenshot', origin, height)
    reference = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    panel.renderer.draw(cairo.Context(reference), width, height, snapshot, panel.ui_state())
    reference.flush()
    expected = memoryview(reference.get_data()).cast('B')
    actual = memoryview(image.get_pixels()).cast('B')
    rs, stride, channels = reference.get_stride(), image.get_rowstride(), image.get_n_channels()
    difference = samples = large_errors = 0
    # Sample complete HUD panels, including their gold top borders and text.
    for x0,y0,x1,y1 in ((25,23,301,100),(481,23,799,88),(981,23,1255,100)):
        for y in range(round(y0*height/720), round(y1*height/720), 2):
            for x in range(round(x0*width/1280), round(x1*width/1280), 2):
                a = (y+oy)*stride+(x+ox)*channels
                b = y*rs+x*4
                errors = (abs(actual[a]-expected[b+2]), abs(actual[a+1]-expected[b+1]),
                          abs(actual[a+2]-expected[b]))
                difference += sum(errors)
                large_errors += max(errors)>32
                samples += 1
    mean_error = difference/max(1,samples*3)
    error_fraction = large_errors/max(1,samples)
    assert mean_error < 7. and error_fraction < .20, (
        'HUD is shifted, clipped or covered by native toolbar', origin, mean_error, error_fraction)
    return dict(canvas_origin=[ox,oy],canvas_size=[width,height],samples=samples,
                mean_rgb_error=mean_error,large_error_fraction=error_fraction)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'sword/qa-board')
    parser.add_argument('--samples', type=int, default=45)
    args = parser.parse_args(argv)
    if args.samples < 3:
        parser.error('--samples must be at least 3')
    args.output.mkdir(parents=True,exist_ok=True)
    # A guard makes accidental future networking fail the acceptance run.
    with patch('urllib.request.OpenerDirector.open',
            side_effect=AssertionError('Offline visual QA attempted network access')) as network:
        panel = SwordPanel(SimpleNamespace(embed_socket=1, offline_preview=True,
            base='http://127.0.0.1:1', entry_delay=3., fullscreen=False))
        GLib.source_remove(panel.timer)
        panel.timer = None
        window = Gtk.Window()
        window.resize(1024,600)
        window.add(panel)
        window.show_all()
        panel.notice = '离线演示 · 合成场景 / 鼠标操控'
        scenes = fixtures()
        screenshots = []
        times = []
        try:
            for size in ((1024,600),(800,480)):
                window.resize(*size)
                for name in scenes if size == (1024,600) else ('ready','playing','won'):
                    if name == 'won':
                        panel.end_started = time.monotonic()-3.
                        panel.end_paused_at = panel.end_started+3.
                    elif name == 'celebrating':
                        panel.end_started = panel.end_paused_at = None
                    with patch.object(panel.game, 'snapshot', side_effect=lambda:deepcopy(scenes[name])):
                        panel.canvas.queue_draw()
                        pump(.20)
                        width,height = window.get_size()
                        assert (width,height) == size, (width,height,size)
                        image = Gdk.pixbuf_get_from_window(window.get_window(),0,0,width,height)
                        assert image is not None, 'Xvfb capture unavailable'
                        path = args.output/('ui-%s-%dx%d.png' % (name,width,height))
                        image.savev(str(path),'png',[],[])
                        alignment = audit_hud_alignment(panel,image,
                            panel.presentation_snapshot(time.monotonic()))
                        screenshots.append(dict(name=name,width=width,height=height,path=path.name,
                                                hud_alignment=alignment))
                        # Hit area and renderer are both normalized to the full
                        # canvas; confirm the native chrome leaves usable space.
                        allocation = panel.canvas.get_allocation()
                        assert allocation.height >= height-70, allocation.height
                        assert allocation.width == width, allocation.width
                        assert 0 <= START_RECT[0] < START_RECT[2] <= 1
                        assert 0 <= START_RECT[1] < START_RECT[3] <= 1
            for width,height in ((1024,550),(1280,720)):
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
                context = cairo.Context(surface)
                panel.renderer.draw(context,width,height,scenes['playing'],panel.ui_state())
                elapsed, cpu = [], []
                for n in range(args.samples):
                    state = deepcopy(scenes['special' if n%3 == 0 else 'playing'])
                    state['time'] += n/30.
                    started, cpu_started = time.perf_counter(), time.thread_time()
                    panel.renderer.draw(context,width,height,state,panel.ui_state())
                    surface.flush()
                    cpu.append((time.thread_time()-cpu_started)*1000)
                    elapsed.append((time.perf_counter()-started)*1000)
                times.append(dict(width=width,height=height,samples=args.samples,
                    mean_ms=statistics.mean(elapsed),p95_ms=sorted(elapsed)[int(args.samples*.95)-1],
                    max_ms=max(elapsed),cpu_mean_ms=statistics.mean(cpu)))
            assert panel.fatal_error is None, panel.fatal_error
            assert panel.poll_thread is None
            network.assert_not_called()
        finally:
            panel.operation('shutdown')
            window.destroy()
            pump(.03)
        report = dict(platform=platform.platform(),machine=platform.machine(),
            input='offline synthetic scenario snapshots; no camera, person, API, or network',
            measures='Cairo full game composite; excludes inference and physical display scanout',
            screenshots=screenshots,timings=times)
        (args.output/'ui-and-render-report.json').write_text(
            json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
