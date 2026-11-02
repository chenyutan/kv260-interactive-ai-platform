"""Lossless decoded YUYV chunks for finite, all-frame board replay."""
import argparse, hashlib, json, time
from pathlib import Path
import av
import numpy as np

p=argparse.ArgumentParser();p.add_argument('video',type=Path);p.add_argument('output',type=Path);p.add_argument('--chunk-frames',type=int,default=32);a=p.parse_args()
if not 1<=a.chunk_frames<=128: p.error('chunk size must be 1..128')
a.output.mkdir(parents=True,exist_ok=True)
if (a.output/'manifest.json').exists():raise RuntimeError('Existing replay pack; choose a new output')
start=time.monotonic();parts=[];buffer=[];pts=[];all_times=[];count=0
container=av.open(str(a.video));stream=container.streams.video[0]
w,h=stream.width,stream.height
declared_frames=stream.frames
if w%2:raise ValueError('YUYV requires even width')
def flush():
 global buffer,pts,count
 if not buffer:return
 name='frames-%04d.npz'%len(parts);path=a.output/name
 with path.with_suffix('.tmp').open('wb') as f:np.savez_compressed(f,raw=np.stack(buffer),times=np.asarray(pts,np.float64))
 path.with_suffix('.tmp').replace(path)
 parts.append(dict(file=name,first_index=count,len=len(buffer),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
 count+=len(buffer);buffer=[];pts=[]
for frame in container.decode(stream):
 if frame.pts is None:raise ValueError('Missing source timestamp')
 t=float(frame.pts*frame.time_base)
 if all_times and t<=all_times[-1]:raise ValueError('Non-monotonic PTS')
 image=frame.reformat(width=w,height=h,format='yuyv422');plane=image.planes[0]
 raw=np.frombuffer(plane,np.uint8).reshape(h,plane.line_size)[:,:w*2].copy()
 buffer.append(raw);pts.append(t);all_times.append(t)
 if len(buffer)==a.chunk_frames:flush()
flush();container.close()
manifest=dict(schema='bodyink.full_yuyv_replay.v1',source=str(a.video.resolve()),source_sha256=hashlib.sha256(a.video.read_bytes()).hexdigest(),width=w,height=h,stride=w*2,frame_count=count,container_reported_frames=declared_frames,declared_count_matches_decoded=(declared_frames==count),first_pts=all_times[0],last_pts=all_times[-1],chunks=parts,pack_seconds=time.monotonic()-start,decode='PyAV decoded every frame, swscale YUYV422 conversion, no spatial resize or temporal subsampling',av_version=av.__version__)
(a.output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');print(json.dumps(manifest,indent=2))
