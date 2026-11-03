"""Recorded 60 FPS source, latest-only queue, real DPU + native pose decoder.

Measures compute pipeline latency; excludes USB capture and display latency.
CPU and PL use identical recorded frames, model, ROI and decoder.
"""
import argparse
import json
from pathlib import Path
import queue
import signal
import sys
import threading
import time
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live');sys.path.insert(0,str(ROOT))
from hourglass import Hourglass
from movenet import YuyvPreprocessor
from hourglass_select import rois
from pl_adapter import PLAdapter

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--height',type=int,choices=(540,720,1080),default=720)
    p.add_argument('--backend',choices=('cpu','pl'),required=True)
    p.add_argument('--seconds',type=float,default=15)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();h=a.height;w=h*16//9
    frames=np.load(ROOT/f'resolution-compare/{h}.npz')['raw']
    frames=[np.ascontiguousarray(frames[i]).reshape(-1) for i in (6,17,28)]
    roi=rois(w,h)[0]
    net=Hourglass(ROOT/'models/hourglass-pe_mpii.xmodel')
    cpu=YuyvPreprocessor();expected=[]
    for raw in frames:
        net.predict(cpu.resize(raw,w,h,w*2,(256,256),roi=roi))
        expected.append(net.output.copy())
    pre=cpu if a.backend=='cpu' else PLAdapter()
    stop=threading.Event();ready=queue.Queue(maxsize=1)
    stats={'produced':0,'dropped':0};errors=[]
    def producer():
        deadline=time.monotonic();sequence=0
        try:
            while not stop.is_set():
                if stop.wait(max(0,deadline-time.monotonic())):break
                arrived=time.monotonic();index=sequence%3;sequence+=1
                image=pre.resize(frames[index],w,h,w*2,(256,256),roi=roi)
                item=(index,arrived,image,time.monotonic()-arrived)
                try:ready.put_nowait(item)
                except queue.Full:
                    try:ready.get_nowait();stats['dropped']+=1
                    except queue.Empty:pass
                    ready.put_nowait(item)
                stats['produced']+=1
                deadline+=1/60
                if deadline<time.monotonic():deadline=time.monotonic()
        except BaseException as exc:errors.append(repr(exc));stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    worker=threading.Thread(target=producer);worker.start()
    rows=[];begin=time.monotonic()
    try:
        while time.monotonic()-begin<a.seconds and not stop.is_set():
            try:index,arrived,image,pre_seconds=ready.get(timeout=1)
            except queue.Empty:continue
            started=time.monotonic();points,timing=net.predict(image);finished=time.monotonic()
            if not np.array_equal(net.output,expected[index]):
                raise AssertionError('DPU heatmap changed for the same recorded frame')
            checked=time.monotonic()
            rows.append(dict(finished=finished,latency_ms=(finished-arrived)*1000,
                             queue_age_ms=(started-arrived-pre_seconds)*1000,
                             validation_ms=(checked-finished)*1000,
                             predict_wall_ms=(finished-started)*1000,
                             preprocess_ms=pre_seconds*1000,**timing))
    finally:
        stop.set();worker.join()
        if a.backend=='pl':pre.close()
    if errors:raise RuntimeError(errors)
    measured=rows[10:]
    if len(measured)<20:raise RuntimeError('Too few measured pose frames')
    result=dict(backend=a.backend,height=h,source='saved frames paced at up to 60 FPS',
                live_camera_read=False,includes_native_pose_decode=True,
                includes_usb_or_display=False,roi_count=1,all_heatmaps_equal=True,
                pose_fps=(len(measured)-1)/(measured[-1]['finished']-measured[0]['finished']),
                frames=len(rows),**stats)
    for name in ('latency_ms','queue_age_ms','preprocess_ms','dpu_ms','postprocess_ms','validation_ms','predict_wall_ms'):
        values=[r[name] for r in measured]
        result[name]=dict(mean=float(np.mean(values)),p95=float(np.percentile(values,95)),max=max(values))
    a.output.write_text(json.dumps(dict(summary=result,rows=rows),indent=2))
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
