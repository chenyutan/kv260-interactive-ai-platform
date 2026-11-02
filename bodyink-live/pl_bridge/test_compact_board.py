import json,sys
from pathlib import Path
import numpy as np
root=Path('/home/ubuntu/bodyink-live');sys.path.insert(0,str(root))
from pl_adapter import PLAdapter
rng=np.random.default_rng(35033);adapter=PLAdapter();count=0;reductions=[]
try:
    for height,width in [(540,960),(720,1280),(1080,1920)]:
        raw=np.load(root/f'resolution-compare/{height}.npz')['raw'][0].reshape(-1)
        rois=[(0,0,width,height),(1,1,width-1,height-1),(width-33,height-35,33,35)]
        for i in range(13):
            x=int(rng.integers(width-32));y=int(rng.integers(height-32))
            rois.append((x,y,int(rng.integers(32,width-x+1)),int(rng.integers(32,height-y+1))))
        for roi in rois:
            adapter.compact=False;original=adapter.resize(raw,width,height,width*2,roi=roi)
            adapter.compact=True;packed=adapter.resize(raw,width,height,width*2,roi=roi)
            np.testing.assert_array_equal(original.data,packed.data)
            reductions.append(adapter.last_timing['source_sync_bytes']/(width*height*2));count+=1
finally:
    if adapter.pp.active_buffers is not None:
        import time
        print('FATAL_DMA_NOT_DRAINED: preserving BOs',flush=True)
        while True:time.sleep(1)
    adapter.close()
result={'random_and_edge_roi_cases':count,'all_input_tensor_bytes_equal':True,'source_sync_ratio_mean':float(np.mean(reductions))}
(root/'pl-deploy-0914/compact-board-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
