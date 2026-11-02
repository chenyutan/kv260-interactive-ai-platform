"""Native A/B camera captures; no input devices, deployment or live game state.
Run: python3 -B -m runner.qa_roof_camera --library /isolated/librunner3d.so --output /isolated/qa
Use the same current Python tree with old/new libraries for camera-only A/B.
"""
import argparse
import ctypes as C
import hashlib
import json
import math
from pathlib import Path
import struct
import time
import zlib
from .native_render import Renderer
from .test_roof_replay import roof_replay


def png(path,pixels,w=960,h=540):
    def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    # Native GL readback starts at the bottom left.
    rows=b''.join(b'\x00'+pixels[y*w*4:(y+1)*w*4] for y in reversed(range(h)))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows,6))+chunk(b'IEND',b''))


def projected_y(z,height,eye_y,pitch,eye_z):
    dy=height-eye_y;dz=z-eye_z;c=math.cos(pitch);s=math.sin(pitch)
    return 270.-270./math.tan(math.radians(55)/2)*(c*dy+s*dz)/(-s*dy+c*dz)


def main():
    p=argparse.ArgumentParser();p.add_argument('--library',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    lib=C.CDLL(str(a.library.resolve()))
    camera=getattr(lib,'rv_camera_state',None)
    if camera:camera.argtypes=[C.c_void_p,C.POINTER(C.c_float),C.c_int];camera.restype=C.c_int
    lib.rv_finish.argtypes=[C.c_void_p];lib.rv_vertex_count.argtypes=[C.c_void_p]
    report=dict(library=str(a.library),sha256=hashlib.sha256(a.library.read_bytes()).hexdigest(),camera_hook=bool(camera),scenarios=[])
    for mode,jump,label in ((1,True,'straight'),(2,True,'transfer'),(1,False,'missed-input')):
        game,commands,samples=roof_replay(mode=mode,speed=16.,jump=jump)
        r=Renderer(library=lib);assert r.attach_window(0,960,540,offscreen=True),r.metrics()
        records=[];times=[];vertices=[];captures={3.:'ramp',13.:'roof',21.:'gap-preview',25.:'takeoff',28.:'gap-crossing',40.:'landing'};last_coins=0
        for snap in samples:
            if snap['coins']>last_coins:r.event(dict(kind='collect',item_kind='coin',lane=snap['lane']))
            last_coins=snap['coins'];r.update_effects(1/30)
            start=time.perf_counter();assert r.render(None,snap,960,540);lib.rv_finish(r._context)
            times.append((time.perf_counter()-start)*1000);vertices.append(lib.rv_vertex_count(r._context))
            if camera:
                values=(C.c_float*5)();assert camera(r._context,values,5);eye_y,pitch,eye_z,altitude,edge=map(float,values)
            else:eye_y=3.55+snap['player_y']*.57;pitch=.12;eye_z=-7.;altitude=None;edge=None
            row=dict(distance=snap['distance'],player_y=snap['player_y'],jump=snap['jump_height'],eye_y=eye_y,pitch=pitch,eye_z=eye_z,altitude=altitude,warning_edge=edge)
            records.append(row)
            for at,name in list(captures.items()):
                if snap['distance']>=at:
                    file=f'{label}-{name}.png';png(a.output/file,r.readback());row['capture']=file
                    if name=='gap-preview':
                        end=27.6-snap['distance'];ys=[projected_y(z,2.63,eye_y,pitch,eye_z) for z in (end,end+3)]
                        row['gap_edge_screen_y']=ys;row['gap_pixels']=abs(ys[0]-ys[1])
                        if camera:
                            assert pitch>.26 and eye_y>6.8,row
                            assert row['gap_pixels']>22,row
                            assert 120<min(ys)<max(ys)<480,row
                            assert edge>0,row
                    del captures[at]
        if camera and jump:
            airborne=[x for x in records if 26<x['distance']<35 and x['jump']>.2]
            assert max(x['eye_y'] for x in airborne)-min(x['eye_y'] for x in airborne)<.05
        scenario=dict(name=label,commands=commands,result=game.report(),frames=records,metrics=r.metrics(),frame_ms_p50=sorted(times)[len(times)//2],frame_ms_p95=sorted(times)[int(len(times)*.95)],max_vertices=max(vertices))
        report['scenarios'].append(scenario);r.release_native_output()
    if camera:
        r=Renderer(library=lib);assert r.attach_window(0,960,540,offscreen=True)
        def probe(entities,**fields):
            snap=dict(state='running',distance=200.,player_x=1.,player_y=2.49,entities=entities);snap.update(fields)
            for _ in range(45):r.update_effects(1/30);assert r.render(None,snap,960,540)
            data=(C.c_float*5)();assert camera(r._context,data,5);return list(data)
        def cart(z,contacted=False):return dict(kind='cart',lane=1,z=z,y=0.,contacted=contacted)
        terminal=probe([cart(-3),cart(4.2)])
        assert terminal[4]==-1,terminal
        gap=probe([cart(-3),cart(4.2),cart(14.4)])
        assert abs(gap[4]-11.4)<.01,gap
        contacted=probe([cart(-3),cart(4.2),cart(14.4,True)])
        assert contacted[4]==-1,contacted
        paused=probe([],state='paused',player_y=0.)
        assert abs(paused[0]-contacted[0])<1e-5,paused
        restarted=probe([],distance=0.,player_y=0.)
        assert abs(restarted[0]-3.55)<.01,restarted
        report['probes']=dict(terminal=terminal,gap=gap,contacted=contacted,paused=paused,restarted=restarted)
        r.release_native_output()
    (a.output/'camera-replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(output=str(a.output),library_sha256=report['sha256'],scenarios=[dict(name=s['name'],result=s['result'],frame_ms_p95=s['frame_ms_p95'],max_vertices=s['max_vertices']) for s in report['scenarios']]),ensure_ascii=False))

if __name__=='__main__':main()
