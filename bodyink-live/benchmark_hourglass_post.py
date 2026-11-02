import ctypes,json,time
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parent
inputs=[np.fromfile(p,np.int8) for p in sorted((root/'validation/hourglass-raw').glob('*.i8'))][:8]
results={};all_outputs=[]
for name in ['reference','candidate']:
    lib=ctypes.CDLL(str(root/f'libhourglass-{name}.so'));f=lib.bodyink_hourglass_decode
    f.argtypes=[np.ctypeslib.ndpointer(dtype=np.int8,flags='C_CONTIGUOUS'),ctypes.c_int,np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')];f.restype=ctypes.c_int
    outputs=[];times=[]
    for raw in inputs:
        out=np.empty((16,3),np.float32)
        for _ in range(12):
            start=time.monotonic();assert f(raw,7,out)==0;times.append((time.monotonic()-start)*1000)
        outputs.append(out.copy())
    results[name]={'mean_ms':float(np.mean(times)),'p95_ms':float(np.percentile(times,95)),'max_ms':float(max(times))}
    all_outputs.append(np.stack(outputs))
assert np.array_equal(*all_outputs)
results['bitwise_equal']=True;results['samples']=len(inputs)
(root/'validation/hourglass-post-benchmark.json').write_text(json.dumps(results,indent=2));print(json.dumps(results))
