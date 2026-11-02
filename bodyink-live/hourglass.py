"""Official Vitis AI 2.5 Hourglass MPII pretrained DPU pose backend."""
import ctypes,hashlib,json,subprocess,sys,time,os
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
import numpy as np
from movenet import EXPECTED_FINGERPRINT,NativeDecoder

@dataclass(frozen=True)
class QuantizedRGB:
    """Owned, already-quantized Hourglass RGB tensor from verified PL."""
    data: np.ndarray

def body_rois(width,height):
    side=min(width,height)
    return [((width-side)//2,0,side,side)]

class Hourglass:
    input_size=(256,256)
    color_order='RGB'
    def __init__(self,model,*,allow_coarse=False):
        self.direct_pl_input=os.environ.get("BODYINK_DIRECT_PL_INPUT")=="1"
        self._inflight_input=None
        import xir,vart
        self.model=Path(model);self.graph=xir.Graph.deserialize(str(model))
        dpu=[s for s in self.graph.get_root_subgraph().toposort_child_subgraph() if s.has_attr('device') and s.get_attr('device').upper()=='DPU']
        if len(dpu)!=1:raise ValueError('Expected one Hourglass DPU partition')
        actual=int(dpu[0].get_attr('dpu_fingerprint'))
        if actual!=EXPECTED_FINGERPRINT:raise ValueError('Hourglass model fingerprint mismatch')
        query=subprocess.run([sys.executable,'-m','xdputil','query'],capture_output=True,text=True,timeout=15,check=True)
        hardware=json.loads(query.stdout).get('kernels',[])
        if not hardware or any(int(str(k.get('fingerprint','0')),0)!=actual for k in hardware):raise ValueError('Loaded DPU fingerprint mismatch')
        self.runner=vart.Runner.create_runner(dpu[0],'run')
        inputs=self.runner.get_input_tensors();outputs=self.runner.get_output_tensors()
        if len(inputs)!=1 or list(inputs[0].dims)!=[1,256,256,3] or len(outputs)!=1 or list(outputs[0].dims)!=[1,64,64,16]:raise ValueError('Unexpected Hourglass tensors')
        self.input_fix=int(inputs[0].get_attr('fix_point'));self.output_fix=int(outputs[0].get_attr('fix_point'))
        self.input=np.empty((1,256,256,3),np.int8);self.output=np.empty((1,64,64,16),np.int8)
        # Official setMeanScaleBGR stores BGR mean; setInputImageRGB subtracts
        # each BGR source channel's mean before writing the reversed RGB order.
        self.quant=NativeDecoder(SimpleNamespace(mean=np.array([110.3385,113.22,112.302],np.float32),scale=np.array([.00392157]*3,np.float32)),self.input_fix)
        lib=Path(__file__).with_name('libbodyink_hourglass.so');self.native=None
        if lib.exists():
            self.native=ctypes.CDLL(str(lib))
            self.native.bodyink_hourglass_decode.argtypes=[np.ctypeslib.ndpointer(dtype=np.int8,flags='C_CONTIGUOUS'),ctypes.c_int,np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')]
            self.native.bodyink_hourglass_decode.restype=ctypes.c_int
        elif not allow_coarse:raise RuntimeError('Native Hourglass decoder missing')
        self.info={'name':'AMD Hourglass MPII','fingerprint':hex(actual),'model_sha256':hashlib.sha256(self.model.read_bytes()).hexdigest(),
            'input_shape':list(self.input.shape),'input_fix_point':self.input_fix,'output_fix_point':self.output_fix,
            'score_semantics':'native_heatmap_peak','preprocess_version':'rgb-bgrmean-reversed-v2',
            'decoder':'bicubic-v1' if self.native else 'coarse-probe-only'}
    def predict(self,rgb):
        if self._inflight_input is not None:raise RuntimeError("Previous DPU input still owned; runner recovery required")
        start=time.monotonic();actual_input=self.input
        if isinstance(rgb,QuantizedRGB):
            if rgb.data.shape!=self.input.shape or rgb.data.dtype!=np.int8 or not rgb.data.flags.c_contiguous:
                raise ValueError('Expected contiguous quantized Hourglass tensor')
            if self.direct_pl_input and rgb.data.flags.owndata and rgb.data.flags.writeable:
                actual_input=rgb.data;input_stage='pl_tensor_bind_ms'
            else:
                np.copyto(self.input,rgb.data);input_stage='pl_tensor_copy_ms'
        else:
            if rgb.shape!=(256,256,3) or rgb.dtype!=np.uint8:raise ValueError('Expected RGB uint8 256x256')
            self.quant.quantize(np.ascontiguousarray(rgb),self.input)
            input_stage='quantize_ms'
        before=time.monotonic()
        # Retain the owned tensor through synchronous completion, and on any
        # ambiguous execute/wait failure. No alias of a reusable PL BO is bound.
        self._inflight_input=actual_input
        job=self.runner.execute_async([actual_input],[self.output]);status=self.runner.wait(job)
        if status not in (0,None):raise RuntimeError('Hourglass DPU job failed; input retained')
        self._inflight_input=None
        done=time.monotonic();mpii=np.empty((16,3),np.float32)
        if self.native:
            if self.native.bodyink_hourglass_decode(self.output,self.output_fix,mpii):raise RuntimeError('Hourglass native decode failed')
        else:
            maps=self.output[0].transpose(1,0,2).reshape(-1,16)
            peak=maps.argmax(axis=0);score=maps[peak,np.arange(16)].astype(np.float32)*(2.0**(-self.output_fix))
            mpii[:,:2]=np.stack((peak//64,peak%64),axis=1)/64.;mpii[:,2]=score
            mpii[score<=.001,:2]=-1
        points=np.zeros((17,3),np.float32);points[:,:2]=-1
        points[5:17]=mpii[[13,12,14,11,15,10,3,2,4,1,5,0]]
        self.last_mpii=mpii
        return points,{input_stage:(before-start)*1000,'dpu_ms':(done-before)*1000,'postprocess_ms':(time.monotonic()-done)*1000}
