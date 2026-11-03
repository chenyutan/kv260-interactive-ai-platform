"""Compare the deployed staging path with CPU reference tensors at ROI boundaries."""
import json,sys
from pathlib import Path
import numpy as np
root=Path('/home/ubuntu/bodyink-live');sys.path.insert(0,str(root))
from hourglass import Hourglass
from movenet import YuyvPreprocessor
from pl_adapter import PLAdapter
from pl_preprocess import defer_dma_signals
rng=np.random.default_rng(35033);count=0
cpu=YuyvPreprocessor()
net=Hourglass(root/'models/hourglass-pe_mpii.xmodel')
adapter=PLAdapter()
with defer_dma_signals():
    try:
        for height,width in [(540,960),(720,1280),(1080,1920)]:
            raw=np.load(root/f'resolution-compare/{height}.npz')['raw'][0].reshape(-1)
            rois=[(0,0,width,height),(1,1,width-1,height-1),(width-33,height-35,33,35)]
            for i in range(13):
                x=int(rng.integers(width-32));y=int(rng.integers(height-32))
                rois.append((x,y,int(rng.integers(32,width-x+1)),int(rng.integers(32,height-y+1))))
            for roi in rois:
                rgb=cpu.resize(raw,width,height,width*2,(256,256),roi=roi)
                net.predict(rgb)
                packed=adapter.resize(raw,width,height,width*2,roi=roi)
                np.testing.assert_array_equal(net.input,packed.data)
                count+=1
    finally:
        adapter.close()
result={'random_and_edge_roi_cases':count,'all_input_tensor_bytes_equal':True}
(root/'pl-deploy-0914/edge-roi-board-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
