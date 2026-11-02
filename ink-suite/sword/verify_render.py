"""Deterministic offline gameplay, native artwork previews and Cairo timing.

No backend connection, webcam image, DPU or real desktop is used. Render
latency is explicitly not camera-to-display latency.
"""
import argparse
import json
import math
from pathlib import Path
import platform
import statistics
import time
import cairo
from .engine import Game
from .renderer import Renderer


def script_control(game, tick):
    # This is deterministic non-human preview input, not an accuracy test.
    t=tick/120
    if hasattr(game,'bullets'):
        targets=[e for e in game.entities if not e.get('hit') and e['kind'] in ('enemy','elite','boss')]
        target=min(targets,key=lambda e:abs(e['x']-game.player['x'])) if targets else None
        x=target['x'] if target else .5+.28*math.sin(t*.52)
        y=.78+.08*math.sin(t*.61)
        return dict(x=(x-.07)/.86,y=(y-.22)/.68,valid=True,status='tracking',
                    special=bool(game.energy>=.999 and tick%100==0),source_key=tick)
    future=[e for e in game.entities if not e.get('hit') and e['kind']=='gate' and e['x']>game.player['x']-.01]
    target=min(future,key=lambda e:e['x']) if future else None
    boss=next((e for e in game.entities if e['kind']=='boss' and not e.get('hit')),None)
    x=.29 if boss is None else min(.60,boss['x'])
    y=boss['y'] if boss else target['y'] if target else .5
    return dict(x=(x-.08)/.52,y=(y-.18)/.67,valid=True,status='tracking',
                special=bool(game.energy>=.999 and tick%100==0),source_key=tick)


def stress_scene(frame=0):
    """Illustrative maximum-load layout, never passed off as played gameplay."""
    t=frame/30
    bullets=[]
    for i in range(288):
        ring=i//36;angle=(i%36)*math.tau/36+ring*.14+t*.09
        radial=.055+ring*.055
        bullets.append(dict(id=i,x=.5+math.cos(angle)*radial/1.7778,
                            y=.32+math.sin(angle)*radial,r=.010,
                            style=('cinnabar','indigo','ink')[ring%3]))
    shots=[dict(id=i,x=.37+(i%5)*.066,y=(.92-(i//5)*.058-t*.4)%.94,r=.008)
           for i in range(80)]
    entities=[dict(id=1000,kind='boss',x=.5,y=.28,r=.073,health=142,max_health=200,warning=0.)]
    entities.extend(dict(id=1001+i,kind='enemy',x=.1+i*.16,y=.20+(i%2)*.13,r=.032,health=3,max_health=3) for i in range(6))
    entities.extend((dict(id=1100,kind='elite',x=.18,y=.49,r=.052,health=11,max_health=18),
                     dict(id=1101,kind='power',x=.81,y=.59,r=.022),
                     dict(id=1102,kind='orb',x=.75,y=.72,r=.016)))
    return dict(phase='playing',time=84.+t,duration=120.,score=18620+frame//5*20,
                combo=36,energy=.76,lives=4,power=4,kills=75,graze=142+frame//7,
                scene=2,progress=.7,player=dict(x=.52+.08*math.sin(t),y=.82,r=.009,angle=-math.pi/2),
                entities=entities,bullets=bullets,shots=shots,effects=[],special_time=0.,tracking=True,
                wave_name='天门决战',boss_name='玄墨魁首',boss_phase=2,
                projectile_budget=dict(enemy=288,friendly=80))


def sequences():
    game=Game()
    records={'ready':game.snapshot()}
    game.start(0)
    sampled=[]
    for i in range(int((game.duration+12)*120)):
        game.update(1/120,script_control(game,i))
        if i%4==0:sampled.append(game.snapshot())
        snap=game.snapshot()
        if snap['time']>10 and 'bamboo' not in records:records['bamboo']=snap
        if snap['time']>game.duration*.4 and 'clouds' not in records:records['clouds']=snap
        if snap['special_time']>.5 and 'special' not in records:records['special']=snap
        if any(e['kind']=='boss' for e in snap['entities']) and 'seal' not in records:records['seal']=snap
        if game.phase in ('won','lost'):
            records['ending']=snap
            break
    records['illustrative-bullet-storm']=stress_scene()
    special=stress_scene(15);special['special_time']=1.05
    special['effects']=[dict(kind='special',x=.52,y=.82,age=.15,ttl=1.25)]
    records['illustrative-clear-spell']=special
    return records,sampled


def stats(values):
    return dict(mean_ms=statistics.mean(values),p95_ms=sorted(values)[max(0,int(len(values)*.95)-1)],max_ms=max(values))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=Path(__file__).parent/'qa-board')
    parser.add_argument('--frames',type=int,default=90)
    parser.add_argument('--skip-benchmark',action='store_true')
    parser.add_argument('--stress',action='store_true',help='benchmark 288 hostile + 80 friendly projectiles')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    samples,trajectory=sequences()
    renderer=Renderer()
    for name,snap in samples.items():
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1280,720)
        renderer.draw(cairo.Context(surface),1280,720,snap,{'offline':True})
        surface.write_to_png(str(args.out/(name+'.png')))
    reports=[]
    if not args.skip_benchmark:
        for w,h in ((1024,552),(1280,720),(1920,1080)):
            renderer=Renderer()
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
            context=cairo.Context(surface)
            for sample in trajectory[:8]:renderer.draw(context,w,h,sample)
            wall=[];cpu=[]
            # Consecutive frames within three actual game windows preserve
            # normal HUD changes and cache use. Jumping ~one second every
            # frame would artificially force every static HUD to rebuild.
            per=max(1,args.frames//3)
            test_frames=[]
            for second in (14.,35.,63.):
                start=next((i for i,s in enumerate(trajectory) if s['time']>=second),max(0,len(trajectory)-per))
                test_frames.extend(trajectory[start:start+per])
            if args.stress:
                test_frames=[stress_scene(index) for index in range(max(1,args.frames))]
                for sample in test_frames[:8]:renderer.draw(context,w,h,sample)
            for sample in test_frames[:max(1,args.frames)]:
                t=time.perf_counter();p=time.thread_time()
                renderer.draw(context,w,h,sample,{'offline':True})
                surface.flush()
                wall.append((time.perf_counter()-t)*1000)
                cpu.append((time.thread_time()-p)*1000)
            reports.append(dict(size=[w,h],frames=len(wall),wall=stats(wall),cpu=stats(cpu),
                                sprite_cache=len(renderer.cache),text_cache=len(renderer.letters),
                                projectile_tiles=len(renderer.projectile_cache),enemy_tiles=len(renderer.enemy_cache),
                                projectile_tile_allocations=renderer.projectile_tile_allocations,
                                stress=bool(args.stress)))
    report=dict(platform=platform.platform(),architecture=platform.machine(),
                input='deterministic scripted gameplay, no camera or recorded person',
                camera_used=False,backend_used=False,display_scanout_measured=False,
                stress=bool(args.stress),illustrative_layouts=['illustrative-bullet-storm','illustrative-clear-spell'],
                simulation_frames=len(trajectory),sample_windows_seconds=[14,35,63],ending=samples.get('ending'),
                previews=list(samples),timings=reports)
    (args.out/'render-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='ending'},ensure_ascii=False))


if __name__=='__main__':main()
