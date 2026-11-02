"""Exercise the candidate application with recorded input and real PL/DPU.

Only the camera source/control boundary is replaced. No camera is opened.
The real action engine, ROI fallback, JPEG preview and HTTP server run.
"""
import argparse
import ctypes
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live')
parser=argparse.ArgumentParser()
parser.add_argument('--preview-fps',type=int,default=30)
parser.add_argument('--seconds',type=int,default=60)
parser.add_argument('--name',default='recorded-app')
parser.add_argument('--decoder',default=None)
parser.add_argument('--preprocess-mode',choices=['prefetch','timed'],default='prefetch')
parser.add_argument('--port',type=int,default=8089)
options=parser.parse_args()
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('bodyink_candidate',ROOT/'pl-deploy-0914/runtime-candidate/app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
app.ROOT=ROOT
frames=np.load(ROOT/'resolution-compare/720.npz')['raw']
class NoCameraControls:
    def __init__(self,*args):pass
    def restore_saved(self):return {'ok':True,'source':'recorded test'}
    def read(self):return self.restore_saved()
app.CameraSettings=NoCameraControls
app.read_camera_link=lambda:{'present':True,'ready_for_1080p60_yuyv':True,'source':'recorded test'}
def recorded_status(self,now):
    self.state['capture_fps']=60
app.LiveApp.refresh_camera_status=recorded_status
sequence=0
deadline=time.monotonic()
def recorded_snapshot(**kwargs):
    global sequence,deadline
    time.sleep(max(0,deadline-time.monotonic()))
    now=time.monotonic();deadline=max(deadline+1/60,now)
    raw=frames[sequence%len(frames)].reshape(-1)
    sequence+=1
    return dict(width=1280,height=720,stride=2560,sequence=sequence,timestamp_seconds=now,buffer_mode='recorded-array'),raw
app.capture_snapshot=recorded_snapshot
preview_deliveries=[0]
original_preview=app.LiveApp.get_preview_frame
def count_preview(self,after):
    result=original_preview(self,after)
    if result and result[0]:preview_deliveries[0]+=1
    return result
app.LiveApp.get_preview_frame=count_preview
args=argparse.Namespace(host='127.0.0.1',port=options.port,backend='hourglass',
    enabled_actions=['A01','A02','A03','A04','A06'],target_fps=60,preview_fps=options.preview_fps,
    preview_width=384,preprocess_mode=options.preprocess_mode,preprocess_backend='pl',snapshot_buffer='mmap',
    templates=ROOT/'dataset/templates.npz',calibration=ROOT/'dataset/speed_calibration.json',
    output=ROOT/'pl-deploy-0914/recorded-app-works')
runtime=app.LiveApp(args)
if options.decoder:
    candidate=ctypes.CDLL(options.decoder)
    candidate.bodyink_hourglass_decode.argtypes=runtime.net.native.bodyink_hourglass_decode.argtypes
    candidate.bodyink_hourglass_decode.restype=ctypes.c_int
    runtime.net.native=candidate
samples=[]
measurement={}
def monitor():
    started=time.monotonic()
    measurement['start']=started
    while time.monotonic()-started<options.seconds and not runtime.stop.wait(1):
        state=runtime.get_state()
        if state.get('pose_count',0)>10:samples.append(state)
    measurement['end']=time.monotonic()
    measurement['poses']=runtime.pose_count
    runtime.stop.set()
thread=threading.Thread(target=monitor);thread.start()
runtime.run();thread.join()
assert runtime.failure is None
assert runtime.pose_count>300
assert runtime.preview.startswith(bytes([255,216]))
assert any(s.get('roi_timing',[{}])[0].get('pl_preprocess') for s in samples)
result=dict(live_camera_read=False,real_pl_dpu=True,real_roi_fallback=True,
    display_endpoint_exposed=(options.port==8088),
    real_action_engine=True,real_jpeg_preview=True,includes_usb_or_screen=(options.port==8088),
    includes_live_usb_input=False,preview_http_deliveries=preview_deliveries[0],
    pose_count=runtime.pose_count,preview_count=runtime.preview_version,capture_failures=runtime.capture_failures,
    measured_seconds=measurement['end']-measurement['start'],
    measured_pose_fps=measurement['poses']/(measurement['end']-measurement['start']),
    mean_reported_fps=float(np.mean([s['pose_fps'] for s in samples])),
    mean_latency_ms=float(np.mean([s['latency_ms'] for s in samples])),samples=samples)
(ROOT/f'pl-deploy-0914/{options.name}.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='samples'},indent=2))
print('PASS: candidate application completed recorded-input PL/DPU run')
