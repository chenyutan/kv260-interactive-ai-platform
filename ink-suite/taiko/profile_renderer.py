"""40-frame renderer microprofile; no GTK, input, audio or camera."""
import cProfile
import io
import json
from pathlib import Path
import pstats
import sys
import time
import cairo
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from taiko.renderer import Renderer
from taiko.engine import Game


def main():
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
    renderer=Renderer();game=Game('normal');game.pause(False);game.advance(25.)
    surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,560);cr=cairo.Context(surface)
    ui=dict(notice='合成渲染分析',audio_notice='无音频输出')
    for _ in range(5):renderer.draw(cr,1024,560,game.snapshot(),ui)
    samples=[];profile=cProfile.Profile();profile.enable()
    for _ in range(40):
        game.advance(game.time+1/60);start=time.perf_counter();renderer.draw(cr,1024,560,game.snapshot(),ui)
        samples.append((time.perf_counter()-start)*1000)
    profile.disable();s=io.StringIO();pstats.Stats(profile,stream=s).sort_stats('cumulative').print_stats(22)
    (output/'renderer-profile.txt').write_text(s.getvalue(),encoding='utf-8')
    result=dict(scope='RAM Cairo renderer only, NOT application FPS',frames=40,mean_ms=sum(samples)/len(samples),
                p95_ms=sorted(samples)[37],camera_images_read=False)
    (output/'renderer-profile.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
