"""Freeze known-working DPU results before changing the hardware platform."""
from pathlib import Path
import sys
import numpy as np
ROOT=Path('/home/ubuntu/bodyink-live')
sys.path.insert(0,str(ROOT))
from hourglass import Hourglass
from hourglass_select import rois
from movenet import YuyvPreprocessor
net=Hourglass(ROOT/'models/hourglass-pe_mpii.xmodel')
cpu=YuyvPreprocessor()
values={}
for h,w in [(1080,1920),(720,1280),(540,960)]:
    frames=np.load(ROOT/f'resolution-compare/{h}.npz')['raw']
    for index in np.linspace(0,len(frames)-1,6,dtype=int):
        raw=np.ascontiguousarray(frames[index]).reshape(-1)
        for r,roi in enumerate(rois(w,h)):
            net.predict(cpu.resize(raw,w,h,w*2,(256,256),roi=roi))
            key=f'{h}_{index}_{r}'
            values[key+'_input']=net.input.copy()
            values[key+'_output']=net.output.copy()
np.savez_compressed(ROOT/'pl-deploy-0914/old-dpu-reference.npz',**values)
print(f'PASS: froze {len(values)//2} old-firmware DPU references',flush=True)
