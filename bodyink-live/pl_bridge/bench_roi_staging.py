"""Saved frames only; verify sparse BO staging against production C sampler."""
import sys,time,json,gc
from pathlib import Path
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live');sys.path.insert(0,str(ROOT))
from movenet import YuyvPreprocessor
from hourglass_select import rois
from xrt_buffer import Device
from roi_staging import RoiStaging
P=Path(__file__).resolve().parent
device=Device();pre=YuyvPreprocessor();results=[]
try:
 for height,width in [(1080,1920),(720,1280),(540,960)]:
  frames=np.load(ROOT/f'resolution-compare/{height}.npz')['raw']
  bo=device.alloc(width*height*2,False);view=bo.array();raw=np.ascontiguousarray(frames[6]).reshape(-1)
  try:
   for roi in rois(width,height)+[(1,1,31,29),(width-1,height-1,1,1)]:
    plan=RoiStaging(width,height,width*2,roi);samples=[];full=[]
    for k in range(36):
     raw=np.ascontiguousarray(frames[k]).reshape(-1)
     # Poison all non-copied bytes during correctness checks to expose omissions.
     view.fill(0xcd if k%2 else 0x39)
     t=time.monotonic();plan.copy(raw,view);t1=time.monotonic();bo.sync(0);t2=time.monotonic()
     expected=pre.resize(raw,width,height,width*2,(256,256),roi=roi)
     actual=pre.resize(view,width,height,width*2,(256,256),roi=roi)
     np.testing.assert_array_equal(actual,expected)
     t3=time.monotonic();np.copyto(view,raw);bo.sync(0);t4=time.monotonic()
     if k>=6:samples.append([(t1-t)*1000,(t2-t1)*1000]);full.append((t4-t3)*1000)
    result={'height':height,'roi':roi,'frames_bit_exact':36,'source_bytes':raw.size,'copied_bytes':plan.bytes,'copied_rows':len(plan.rows),'sparse_copy_ms':float(np.mean(samples,axis=0)[0]),'sync_ms':float(np.mean(samples,axis=0)[1]),'full_copy_and_sync_ms':float(np.mean(full))}
    results.append(result);print(json.dumps(result),flush=True)
  finally:del view;gc.collect();bo.close()
finally:device.close()
(P/'roi-staging-result.json').write_text(json.dumps({'live_camera_read':False,'actual_pl_dma_tested':False,'rows':results},indent=2))
