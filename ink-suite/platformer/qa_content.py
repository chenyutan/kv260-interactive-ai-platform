"""Host-only content previews and comparable 960x540 renderer measurements.

Run this script with --project pointing to either the frozen baseline or the
working ink-suite. Outputs must be explicitly placed outside the source tree.
This measures an offscreen Cairo host workload, not KV260 or human acceptance.
"""
import argparse
import importlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--previews', action='store_true')
    parser.add_argument('--frames', type=int, default=600)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.project).resolve()))
    from platformer.engine import Game
    from platformer.levels import LEVELS
    from platformer import render as r
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'WSL host offscreen Cairo only; no KV260 or camera measurement',
              'resolution': [960,540], 'source': str(Path(args.project).resolve()),
              'scenarios': []}
    for index, level in enumerate(LEVELS):
        game = Game((level,)); game.start(0)
        scene = game.snapshot(); scene['level_index'] = index
        scene['ui'] = {'tracking': True, 'control_mode': 'body',
                       'control': {'valid': True, 'calibration': 'ready', 'move': 1,
                                   'jump_ready': True, 'joystick_dx': .3, 'joystick_dy': 0}}
        renderer = r.Renderer()
        surface = r.cairo.ImageSurface(r.cairo.FORMAT_RGB24, 960,540)
        cr = r.cairo.Context(surface)
        timings = []
        for frame in range(args.frames+120):
            fraction = ((frame-120) % args.frames) / max(1,args.frames-1)
            camera = int((level.width-320)*fraction)
            scene['camera_x'] = camera; scene['elapsed'] = frame / 30
            scene['player'].update(x=camera+120, y=138, grounded=True, vx=118)
            begin = time.perf_counter()
            renderer.render(cr,scene,960,540)
            if frame >= 120:timings.append((time.perf_counter()-begin)*1000)
        ordered=sorted(timings); worst=ordered[-max(1,math.ceil(len(ordered)*.01)):]
        report['scenarios'].append({'stage':index+1, 'frames':len(timings),
            'mean_ms':statistics.mean(timings), 'median_ms':statistics.median(timings),
            'p99_ms':ordered[math.ceil(len(ordered)*.99)-1],
            'one_percent_low_render_fps':1000/statistics.mean(worst),
            'max_ms':max(timings), 'stats':renderer.stats()})
    if args.previews:
        from PIL import Image
        from platformer.test_content import full_replay
        scenarios = [('01_grass',0,0,4,138), ('02_bonus_balcony',0,30,36,58),
                     ('03_underground',1,12,19,138), ('04_castle_outside',2,72,79,106),
                     ('05_castle_hall',2,100,108,138)]
        images=[]
        for name,index,camera_col,player_col,y in scenarios:
            game=Game();game.start(0);game._load_level(index);game.state='playing'
            game.camera_x=camera_col*16
            game.player.update(x=player_col*16,y=y,grounded=True)
            game.elapsed=2.
            scene=game.snapshot()
            surface=r.cairo.ImageSurface(r.cairo.FORMAT_RGB24,960,540)
            r.Renderer().render(r.cairo.Context(surface),scene,960,540)
            path=out/(name+'.png');surface.write_to_png(str(path));images.append(path)
        game, controls=full_replay()
        assert game.state == 'won' and game.rescued
        surface=r.cairo.ImageSurface(r.cairo.FORMAT_RGB24,960,540)
        r.Renderer().render(r.cairo.Context(surface),game.snapshot(),960,540)
        path=out/'06_rescued_actual_replay.png';surface.write_to_png(str(path));images.append(path)
        # RLE input recording can be replayed without inspecting world state.
        runs=[]
        for control in controls:
            if runs and runs[-1]['input'] == control:runs[-1]['steps']+=1
            else:runs.append({'steps':1,'input':control})
        (out/'complete_input_replay.json').write_text(json.dumps({'step_hz':120,
            'initial':'Game(); start(0)', 'runs':runs,'result':game.report()},ensure_ascii=False,indent=2),encoding='utf-8')
        sheet=Image.new('RGB',(1920,1620),'#17212e')
        for i,path in enumerate(images):
            with Image.open(path) as image:sheet.paste(image,(i%2*960,i//2*540))
        sheet.save(out/'contact_sheet.png')
        report['replay']=game.report();report['previews']=[p.name for p in images]
    (out/'host_render_measurement.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'scenarios':[dict(stage=s['stage'],mean_ms=s['mean_ms'],
       p99_ms=s['p99_ms'],one_percent_low_render_fps=s['one_percent_low_render_fps']) for s in report['scenarios']]},ensure_ascii=False))


if __name__ == '__main__':main()
