import time,json
from pathlib import Path
import numpy as np
from xrt_buffer import Device
P=Path(__file__).resolve().parent;root=P.parents[1] if False else Path('/home/ubuntu/bodyink-live')
rows=[];device=Device()
try:
 for cached in (False,True):
  for h,w in [(1080,1920),(720,1280),(540,960)]:
   raw=np.ascontiguousarray(np.load(root/f'resolution-compare/{h}.npz')['raw'][6]).reshape(-1);bo=device.alloc(raw.size,cached);a=bo.array();samples=[]
   try:
    for k in range(30):
     t=time.monotonic();np.copyto(a,raw);t1=time.monotonic();bo.sync(0);t2=time.monotonic();bo.sync(1);t3=time.monotonic()
     if k>=5:samples.append([(t1-t)*1000,(t2-t1)*1000,(t3-t2)*1000])
    np.testing.assert_array_equal(a,raw)
    rows.append({'height':h,'cacheable':cached,'bytes':raw.size,'copy_to_ms':float(np.mean(samples,axis=0)[0]),'sync_to_ms':float(np.mean(samples,axis=0)[1]),'sync_from_ms':float(np.mean(samples,axis=0)[2]),'address_available':bo.address!=0,'equal':True})
   finally:del a;bo.close()
finally:device.close()
(P/'buffer-benchmark.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows),flush=True)
