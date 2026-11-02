import json,time
from pathlib import Path
from PIL import Image
import numpy as np
from movenet import YuyvPreprocessor
from snapshot import capture_snapshot
root=Path(__file__).resolve().parent
pre=YuyvPreprocessor();metadata,raw=capture_snapshot()
rgb=pre.resize(raw,size=(640,360));Image.fromarray(rgb).save(root/'validation/camera-preview.jpg')
measure={}
for name,size,roi in [('body256',(256,256),(420,0,1080,1080)),('preview640',(640,360),None)]:
    times=[]
    for _ in range(40):
        start=time.monotonic();pre.resize(raw,size=size,roi=roi);times.append((time.monotonic()-start)*1000)
    measure[name]={'mean_ms':float(np.mean(times)),'p95_ms':float(np.percentile(times,95))}
luma=np.frombuffer(raw,dtype=np.uint8)[::2]
report={'metadata':metadata,'native':measure,'luma':{'minimum':int(luma.min()),'maximum':int(luma.max()),'mean':float(luma.mean()),'percentiles':np.percentile(luma,[1,50,99]).tolist()},'rgb_mean':rgb.mean(axis=(0,1)).tolist()};(root/'validation/camera-preview.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
