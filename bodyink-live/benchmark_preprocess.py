import json,time
from pathlib import Path
import numpy as np
from movenet import YuyvPreprocessor,body_rois
from snapshot import capture_snapshot
pre=YuyvPreprocessor();times=[];stamps=[];latencies=[]
for _ in range(150):
    start=time.monotonic();metadata,raw=capture_snapshot();got=time.monotonic()
    for roi in body_rois(1920,1080):pre.resize(raw,roi=roi)
    finish=time.monotonic();stamps.append(metadata['timestamp_seconds'])
    times.append([(got-start)*1000,(finish-got)*1000]);latencies.append((finish-stamps[-1])*1000)
    time.sleep(max(0,.04-(time.monotonic()-start)))
report={'samples':len(stamps),'fps':(len(stamps)-1)/(stamps[-1]-stamps[0]),'snapshot_and_pre_ms_mean':np.mean(times,axis=0).tolist(),
    'snapshot_and_pre_ms_p95':np.percentile(times,95,axis=0).tolist(),'latency_p95_ms':float(np.percentile(latencies,95))}
Path('/home/ubuntu/bodyink-live/validation/preprocess-benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
