"""Recorded inputs only. CPU-emulated PL output; actual XRT BO and DPU handoff."""
import sys,time,json,gc
from pathlib import Path
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live');sys.path.insert(0,str(ROOT))
from hourglass import Hourglass
from movenet import YuyvPreprocessor
from hourglass_select import rois
from xrt_buffer import Device
P=Path(__file__).resolve().parent
device=Device();bo=device.alloc(256*256*3,False)
view=bo.array();child=view[1:];del view;gc.collect()
try:
 bo.close()
 raise AssertionError('Freed BO while slice was alive')
except RuntimeError as e:
 assert 'NumPy views' in str(e)
del child;gc.collect();bo.close()
net=Hourglass(ROOT/'models/hourglass-pe_mpii.xmodel');pre=YuyvPreprocessor();rows=[]
try:
 for cached in (False,True):
  bo=device.alloc(net.input.nbytes,cached);view=bo.array().view(np.int8).reshape(net.input.shape)
  try:
   for h,w in [(1080,1920),(720,1280),(540,960)]:
    frames=np.load(ROOT/f'resolution-compare/{h}.npz')['raw'];samples=[]
    for k in range(15):
     raw=np.ascontiguousarray(frames[[6,17,28][k%3]]).reshape(-1)
     rgb=pre.resize(raw,w,h,w*2,(256,256),roi=rois(w,h)[0])
     points,_=net.predict(rgb);expected=net.output.copy()
     # CPU fills the bytes that PL will eventually produce. No PL kernel ran.
     np.copyto(view,net.input);bo.sync(0)
     t=time.monotonic();bo.sync(1);t1=time.monotonic()
     np.copyto(net.input,view);t2=time.monotonic()
     job=net.runner.execute_async([net.input],[net.output]);status=net.runner.wait(job);t3=time.monotonic()
     assert status in (0,None)
     np.testing.assert_array_equal(net.output,expected)
     if k>=3:samples.append([(t1-t)*1000,(t2-t1)*1000,(t3-t2)*1000])
    rows.append({'height':h,'cacheable':cached,'cases':15,'heatmaps_bit_exact':True,'mean_ms':dict(zip(['sync_from','copy_to_vart_input','dpu'],np.mean(samples,axis=0).tolist()))})
    print(json.dumps(rows[-1]),flush=True)
  finally:
   del view;gc.collect();bo.close()
finally:device.close()
result={'source':'recorded frames; CPU-emulated PL output','actual_pl_dma_tested':False,'live_camera_read':False,'slice_lifetime_guard_passed':True,'rows':rows}
(P/'handoff-result.json').write_text(json.dumps(result,indent=2))
