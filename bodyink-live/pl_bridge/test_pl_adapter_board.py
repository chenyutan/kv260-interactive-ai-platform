"""Check row-staged alternating ROIs against frozen old-platform inputs."""
from pathlib import Path
import sys
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live');sys.path.insert(0,str(ROOT))
from pl_adapter import PLAdapter
from hourglass_select import rois
reference=np.load(ROOT/'pl-deploy-0914/old-dpu-reference.npz')
adapter=PLAdapter();count=0
try:
    for height,width in [(1080,1920),(720,1280),(540,960)]:
        frames=np.load(ROOT/f'resolution-compare/{height}.npz')['raw']
        previous=None
        for index in np.linspace(0,len(frames)-1,6,dtype=int):
            raw=frames[index].reshape(-1)
            for r,roi in enumerate(rois(width,height)):
                result=adapter.resize(raw,width,height,width*2,roi=roi)
                np.testing.assert_array_equal(result.data,reference[f'{height}_{index}_{r}_input'])
                if previous is not None:
                    np.testing.assert_array_equal(previous[0].data,previous[1])
                previous=(result,result.data.copy());count+=1
finally:
    if adapter.pp.active_buffers is not None:
        import time
        print('FATAL_DMA_NOT_DRAINED: preserving BOs',flush=True)
        while True:time.sleep(1)
    adapter.close()
print(f'PASS: {count} alternating ROI inputs and retained tensors match frozen reference')
