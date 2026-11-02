"""Finite no-drop replay through the production application (including display).

The input is lossless decoded YUYV. Source video PTS drive action recognition;
wall-clock times measure throughput. No labels enter the inference boundary.
"""
import argparse,hashlib,json,queue,sys,threading,time
from pathlib import Path
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live')
sys.path.insert(0,str(ROOT))
import app
from replay_source import NpzSource,Lz4Source
p=argparse.ArgumentParser();p.add_argument('pack',type=Path);p.add_argument('--name',default='full-replay');p.add_argument('--port',type=int,default=8088);p.add_argument('--lz4',action='store_true');p.add_argument('--screen-after',action='store_true');a=p.parse_args()
m=json.loads((a.pack/('manifest-lz4.json' if a.lz4 else 'manifest.json')).read_text());expected=m['frame_count']
assert m['schema']=='bodyink.full_yuyv_replay.v1' and expected>0
assert sum(c['len'] for c in m['chunks'])==expected
class NoControls:
 def __init__(self,*args):pass
 def restore_saved(self):return {'ok':True,'source':'finite prerecorded YUYV'}
 def read(self):return self.restore_saved()
app.CameraSettings=NoControls
app.read_camera_link=lambda:{'present':True,'ready_for_1080p60_yuyv':True,'message':'Offline finite prerecorded YUYV; no camera read','source':'finite prerecorded YUYV'}
class Replay(app.LiveApp):
 def __init__(self,args):
  super().__init__(args)
  self.source=Lz4Source(a.pack,m) if a.lz4 else NpzSource(a.pack,m)
  self.records=[];self.replay_points=[];self.seen=[];self.mapping={};self.loads=[];self.eof=False;self.begin=None;self.end=None;self.feed_wait=0.;self.preview_deliveries=0
  self.pending_timings=[]
  predict=self.net.predict
  def timed_predict(rgb):
   points,parts=predict(rgb);self.pending_timings.append(parts);return points,parts
  self.net.predict=timed_predict
  original=self.engine.update
  def update(points,stamp,w,h):
   index,pts=self.mapping.pop(stamp)
   if index!=len(self.seen):raise AssertionError('Dropped, duplicated or reordered source frame')
   result=original(points,pts,w,h)
   self.seen.append(index);self.replay_points.append(points.copy())
   event=result.get('event')
   self.records.append(dict(index=index,pts=pts,phase=result['phase'],reason=result.get('reason'),event=event,wall_start=stamp,wall_end=time.monotonic(),roi_timing=self.pending_timings,roi_selection=getattr(self,'last_selection',{})))
   self.pending_timings=[]
   return result
  self.engine.update=update
 def refresh_camera_status(self,now):pass
 def recognition_timestamp(self,metadata):
  return self.mapping[metadata['timestamp_seconds']][1]
 def replace_latest(self,q,value):
  if q is self.latest or q is self.preprocess_queue:
   start=time.monotonic()
   while not self.stop.is_set():
    try:q.put(value,timeout=.1);self.feed_wait+=time.monotonic()-start;return False
    except queue.Full:pass
   return False
  return super().replace_latest(q,value)
 def get_preview_frame(self,after):
  result=super().get_preview_frame(after)
  if result:self.preview_deliveries+=1
  return result
 def get_preview_rgb_frame(self,after):
  result=super().get_preview_rgb_frame(after)
  if result:self.preview_deliveries+=1
  return result
 def capture(self):
  self.begin=time.monotonic();index=0;previous_pts=None
  for actual,pixels,pts in self.source:
   if self.stop.is_set():return
   if actual!=index:raise AssertionError('Source order mismatch')
   if previous_pts is not None and pts<=previous_pts:raise ValueError('Non-monotonic PTS')
   previous_pts=float(pts);stamp=time.monotonic();self.mapping[stamp]=(index,float(pts))
   metadata=dict(width=m['width'],height=m['height'],stride=m['stride'],sequence=index,timestamp_seconds=stamp,buffer_mode='recorded-owned-array',_trace={'request':stamp,'received':stamp})
   self.replace_latest(self.preprocess_queue,(metadata,pixels.reshape(-1)))
   index+=1
  assert index==expected
  self.eof=True
 def monitor(self):
  deadline=time.monotonic()+1800
  while not self.stop.wait(.01):
   if self.eof and self.pose_count==expected:
    self.end=time.monotonic()
    # Render every accepted event before ending; queued preview may replace older
    # preview frames as in production. It never replaces inference input here.
    drain_deadline=time.monotonic()+30
    while (not self.paint_queue.empty()) and time.monotonic()<drain_deadline:time.sleep(.01)
    with self.paint_lock:pass
    if not self.paint_queue.empty():raise RuntimeError('Artwork queue failed to drain')
    if a.screen_after:
     import subprocess
     time.sleep(.7)
     subprocess.run(['/usr/bin/python3',str(ROOT/'pl-deploy-0914/capture_replay_screen.py'),str(ROOT/'pl-deploy-0914'/(a.name+'-screen.png'))],check=True,timeout=10)
    self.stop.set();return
   if time.monotonic()>deadline:raise TimeoutError('Finite replay did not complete')

args=argparse.Namespace(host='127.0.0.1',port=a.port,backend='hourglass',enabled_actions=['A01','A02','A03','A04','A06'],target_fps=60,preview_fps=30,preview_width=384,preprocess_mode='prefetch',preprocess_backend='pl',snapshot_buffer='mmap',templates=ROOT/'dataset/templates.npz',calibration=ROOT/'dataset/speed_calibration.json',output=ROOT/'pl-deploy-0914/full-replay-works')
r=Replay(args);monitor=threading.Thread(target=lambda:r.worker(r.monitor));monitor.start()
r.run();monitor.join()
assert r.failure is None and r.pose_count==expected and r.seen==list(range(expected))
assert not r.mapping and r.latest_dropped==0
if a.port==8088:assert r.preview_deliveries>0, "No local display consumed preview frames"
elapsed=r.end-r.begin
intervals=[]
for v in r.records:
 start,end=v['wall_start'],v['wall_end']
 if intervals and start<=intervals[-1][1]:intervals[-1][1]=max(intervals[-1][1],end)
 else:intervals.append([start,end])
active=sum(end-start for start,end in intervals)
report=dict(schema='bodyink.finite_full_pipeline_replay.v1',source_manifest=m,model=r.net.info,engine_config=r.engine.config,enabled_actions=args.enabled_actions,expected_frames=expected,processed_frames=r.pose_count,dropped_inference_frames=r.latest_dropped,chronological_unique_frames=True,wall_seconds_including_chunk_loading=elapsed,wall_fps=expected/elapsed,diagnostic_pipeline_active_seconds=active,diagnostic_pipeline_active_fps=expected/active,diagnostic_note='Active interval union from each frame delivery to action completion; excludes input starvation, is not wall FPS or live camera/display FPS',chunk_load_seconds_sum=sum(r.source.load_seconds),chunk_load_seconds=r.source.load_seconds,loaded_chunks=len(r.source.load_seconds),source_preload_seconds=r.source.preload_seconds,source_decompress_seconds_sum=sum(r.source.decompress_seconds),source_mode="preloaded-lz4" if a.lz4 else "npz-chunks",cold_wall_fps=expected/(elapsed+r.source.preload_seconds),preview_frames=r.preview_version,preview_http_deliveries=r.preview_deliveries,artwork_events=len(r.renderer.events),capture_failures=r.capture_failures,display_endpoint_exposed=a.port==8088,live_camera_read=False,source_video_decode_included=False,timing_scope='Full PL/DPU/pose selection/action/preview pipeline, includes lossless chunk I/O/decompression; excludes original video decode and pack/upload',records=r.records)
def convert(v):
 if isinstance(v,np.ndarray):return v.tolist()
 if isinstance(v,np.generic):return v.item()
 raise TypeError(type(v).__name__)
out=ROOT/'pl-deploy-0914'
(out/(a.name+'.json')).write_text(json.dumps(report,indent=2,default=convert))
np.savez_compressed(out/(a.name+'-points.npz'),points=np.stack(r.replay_points),times=np.asarray([v['pts'] for v in r.records]))
print(json.dumps({k:v for k,v in report.items() if k not in ('records','source_manifest','model','chunk_load_seconds')},indent=2))
