"""Private Xvfb diagnostic: synthetic input, fakesink only, real native panel."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from taiko.main import TaikoPanel,parse_args,Gtk,GLib
from taiko.engine import Game
from taiko.renderer import Renderer
import cairo


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=12.)
    p.add_argument('--difficulty',choices=('easy','normal','hard'),default='easy')
    p.add_argument('--practice',choices=('full','intro','alternate','finale'),default='full')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    # Hard failure on any accidental network request: no shared API/camera.
    def denied(*args,**kwargs):raise AssertionError('Network forbidden in synthetic test')
    urllib.request.OpenerDirector.open=denied
    args=parse_args(['--offline-preview','--autoplay','--audio-sink','fakesink','--entry-delay','0',
                     '--difficulty',a.difficulty,'--practice',a.practice,'--quit-after',str(a.seconds)])
    win=Gtk.Window();win.set_default_size(1024,600);panel=TaikoPanel(args);win.add(panel);win.show_all()
    checkpoints=[]
    def checkpoint(op):
        panel.operation(op)
        checkpoints.append(dict(op=op,at=time.monotonic(),game=panel.session.game.time,audio=panel.audio.position()))
        return False
    def frozen():
        checkpoints.append(dict(op='frozen',at=time.monotonic(),game=panel.session.game.time,audio=panel.audio.position()));return False
    GLib.timeout_add(3500,checkpoint,'pause');GLib.timeout_add(4300,frozen);GLib.timeout_add(4600,checkpoint,'resume')
    Gtk.main()
    result=panel.performance_snapshot();result.update(checkpoints=checkpoints,synthetic=True,camera_images_read=False,speaker_output=False,xvfb=True)
    failures=[]
    if panel.frames<5:failures.append('too_few_draws')
    if result['audio_error']:failures.append('audio_error')
    if len(checkpoints)!=3:failures.append('missing_lifecycle_checkpoints')
    elif abs(checkpoints[1]['game']-checkpoints[0]['game'])>.001:failures.append('game_advanced_while_paused')
    elif abs(checkpoints[1]['audio']-checkpoints[0]['audio'])>.08:failures.append('audio_advanced_while_paused')
    if result['counts']['Perfect']<1:failures.append('autoplay_never_judged')
    if a.seconds >= panel.session.game.duration-panel.session.game.start_time+6:
        if not panel.session.game.finished:failures.append('full_song_not_finished')
        if result['counts']['Perfect']!=result['total_notes'] or result['counts']['Miss']:
            failures.append('full_song_autoplay_score_mismatch')
    result['failures']=failures
    (a.output/'native-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    renderer=Renderer()
    for w,h in ((1024,560),(1280,720),(1920,1080)):
        for phase,t in (('playing',25.),('finale',72.)):
            game=Game(a.difficulty);game.pause(False)
            for note in game.notes:
                if note['time']<t:
                    for hand in ('L','R') if note['lane']=='B' else (note['lane'],):game.strike(hand,note['time'],(note['id'],hand))
            game.advance(t)
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
            renderer.draw(cairo.Context(surface),w,h,game.snapshot(),dict(notice='合成测试 · 无真人输入',audio_notice='fakesink 静音验收',finale_age=2.))
            surface.write_to_png(str(a.output/('%s-%dx%d.png'%(phase,w,h))))
    win.destroy();print(json.dumps(result,ensure_ascii=False));return 1 if failures else 0


if __name__=='__main__':raise SystemExit(main())
