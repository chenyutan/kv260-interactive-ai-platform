"""Pretrained AMD MoveNet DPU backend; exact fingerprint is checked before run.

Postprocessing follows Vitis-AI 2.5 movenet_imp.cpp (Apache-2.0), preserved
under vendor/. Output order is normalized x, y, heatmap confidence.
"""
import ctypes
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
import numpy as np

EXPECTED_FINGERPRINT = 0x101000016010407

class YuyvPreprocessor:
    def __init__(self, library=None):
        library=Path(library or Path(__file__).with_name('libbodyink_preprocess.so'))
        self.lib=ctypes.CDLL(str(library))
        arr=np.ctypeslib.ndpointer(dtype=np.uint8,flags='C_CONTIGUOUS')
        self.convert=self.lib.bodyink_yuyv_rgb
        self.convert.argtypes=[arr,ctypes.c_int,ctypes.c_int,ctypes.c_int,arr,ctypes.c_int,ctypes.c_int]
        self.convert.restype=ctypes.c_int
        self.convert_roi=self.lib.bodyink_yuyv_roi
        self.convert_roi.argtypes=self.convert.argtypes+[ctypes.c_int]*4
        self.convert_roi.restype=ctypes.c_int
    def resize(self, raw, width=1920,height=1080,stride=3840,size=(192,192),roi=None):
        src=np.frombuffer(raw,dtype=np.uint8)
        if stride<width*2 or len(src)!=stride*height: raise ValueError('Incomplete YUYV frame')
        ow,oh=size
        dst=np.empty((oh,ow,3),np.uint8)
        rect=roi or (0,0,width,height)
        if self.convert_roi(src,width,height,stride,dst,ow,oh,*rect): raise ValueError('Unsupported YUYV geometry')
        return dst

def body_rois(width,height):
    """Two aspect-preserving central viewports, independent of action labels."""
    side=min(width,height)
    small=int(side*.8)
    return [((width-side)//2,0,side,side),((width-small)//2,0,small,small)]

class RoiSelector:
    def __init__(self): self.previous=None
    def select(self, points_list,rois,width,height):
        ids=[5,6,7,8,9,10,11,12]
        quality=[float(np.min(p[ids,2])) if np.isfinite(p[ids]).all() else 0 for p in points_list]
        best=int(np.argmax(quality))
        if self.previous is not None and quality[self.previous]>=quality[best]-.025:
            best=self.previous
        self.previous=best
        p=points_list[best].copy(); x,y,w,h=rois[best]
        valid=(p[:,:2]>=0).all(1)
        p[valid,:2]=(p[valid,:2]*[w,h]+[x,y])/[width,height]
        return p,best,quality

class MoveNetDecoder:
    def __init__(self, prototxt):
        text=Path(prototxt).read_text()
        self.center_weights=np.array([float(x) for x in re.findall(r'center_weight:\s*([\deE.+-]+)',text)],np.float32)
        self.threshold=float(re.search(r'conf_threshold:\s*([\d.]+)',text)[1])
        self.mean=np.array([float(x) for x in re.findall(r'\bmean:\s*([\d.eE+-]+)',text)],np.float32)
        self.scale=np.array([float(x) for x in re.findall(r'\bscale:\s*([\d.eE+-]+)',text)],np.float32)
        if self.mean.shape!=(3,) or self.scale.shape!=(3,): raise ValueError('Expected three RGB mean/scale values')
    @staticmethod
    def sigmoid(x): return 1/(1+np.exp(-np.clip(x,-60,60)))
    def decode(self, outputs, fix_points):
        arrays={}
        for role in ('heatmap','center','reg','offset'):
            names=[n for n in outputs if role in n]
            if len(names)!=1: raise ValueError('Cannot identify unique '+role+' output')
            n=names[0]
            arrays[role]=np.asarray(outputs[n][0],np.float32)*(2.0**(-fix_points[n]))
        heat=self.sigmoid(arrays['heatmap']); h,w,joints=heat.shape
        if joints!=17 or h!=w or self.center_weights.size!=h*w: raise ValueError('Unsupported MoveNet output shape')
        center=self.sigmoid(arrays['center'].reshape(h,w))
        cy,cx=np.unravel_index(np.argmax(center*self.center_weights.reshape(h,w)),(h,w))
        reg=arrays['reg'][cy,cx].reshape(17,2)+np.array([cx+.5,cy+.5],np.float32)
        # The reference stores squared distances in vector<int> before sqrt.
        dx=((np.arange(w)[None,:]-reg[:,0,None])**2).astype(np.int32)
        dy=((np.arange(h)[None,:]-reg[:,1,None])**2).astype(np.int32)
        penalty=np.sqrt(dy[:,:,None]+dx[:,None,:])+1.8
        weighted=heat.transpose(2,0,1)/penalty
        idx=np.argmax(weighted.reshape(17,-1),axis=1)
        yy,xx=idx//w,idx%w
        ji=np.arange(17)
        score=heat[yy,xx,ji]
        offsets=arrays['offset'].reshape(h,w,17,2)[yy,xx,ji]
        xy=(np.stack((xx,yy),axis=1)+offsets)/[w,h]
        xy[score<self.threshold]=-1
        return np.column_stack((xy,score)).astype(np.float32)

class NativeDecoder:
    """Byte-for-byte reference-tested native hot path; NumPy remains the oracle."""
    def __init__(self, decoder, input_fix, library=None):
        self.lib=ctypes.CDLL(str(library or Path(__file__).with_name('libbodyink_postprocess.so')))
        i8=np.ctypeslib.ndpointer(dtype=np.int8,flags='C_CONTIGUOUS')
        u8=np.ctypeslib.ndpointer(dtype=np.uint8,flags='C_CONTIGUOUS')
        f32=np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')
        self.lib.bodyink_movenet_decode.argtypes=[i8]*4+[ctypes.c_int]*4+[f32,ctypes.c_float,f32]
        self.lib.bodyink_movenet_decode.restype=ctypes.c_int
        self.lib.bodyink_quantize_rgb_lut.argtypes=[u8,i8,ctypes.c_size_t,i8]
        self.lib.bodyink_quantize_rgb_lut.restype=ctypes.c_int
        self.decoder=decoder
        self.lut=np.clip(np.trunc((np.arange(256,dtype=np.float32)[:,None]-decoder.mean)*decoder.scale*(2.0**input_fix)),-128,127).astype(np.int8)
    def quantize(self,rgb,dst):
        if self.lib.bodyink_quantize_rgb_lut(rgb,dst,rgb.size//3,self.lut):
            raise RuntimeError('Native input quantization failed')
    def decode(self,outputs,fix_points):
        names=[]
        for role in ('heatmap','center','reg','offset'):
            found=[n for n in outputs if role in n]
            if len(found)!=1: raise ValueError('Cannot identify unique '+role+' output')
            names.append(found[0])
        points=np.empty((17,3),np.float32)
        if self.lib.bodyink_movenet_decode(*(outputs[n] for n in names),*(fix_points[n] for n in names),self.decoder.center_weights,self.decoder.threshold,points):
            raise RuntimeError('Native postprocessing failed')
        return points

class MoveNet:
    def __init__(self, model, prototxt):
        # The service supplies the already-verified isolated VART library fix.
        import xir
        import vart
        self.model=Path(model)
        self.graph=xir.Graph.deserialize(str(model))
        children=self.graph.get_root_subgraph().toposort_child_subgraph()
        dpu=[s for s in children if s.has_attr('device') and s.get_attr('device').upper()=='DPU']
        if len(dpu)!=1: raise ValueError('Expected exactly one DPU subgraph')
        self.subgraph=dpu[0]
        if not self.subgraph.has_attr('dpu_fingerprint'): raise ValueError('DPU graph has no fingerprint')
        actual=int(self.subgraph.get_attr('dpu_fingerprint'))
        if actual!=EXPECTED_FINGERPRINT: raise ValueError('Model fingerprint mismatch: '+hex(actual))
        query=subprocess.run([sys.executable,'-m','xdputil','query'],capture_output=True,text=True,timeout=15,check=True)
        data=json.loads(query.stdout)
        hardware=data.get('kernels',[])
        if not hardware or any(int(str(k.get('fingerprint','0')),0)!=actual for k in hardware):
            raise ValueError('Loaded hardware fingerprint mismatch')
        self.runner=vart.Runner.create_runner(self.subgraph,'run')
        tensors=self.runner.get_input_tensors()
        if len(tensors)!=1 or list(tensors[0].dims)!=[1,192,192,3]: raise ValueError('Unsupported model input')
        self.input_fix=int(tensors[0].get_attr('fix_point'))
        self.input=np.empty((1,192,192,3),np.int8)
        self.output_tensors=self.runner.get_output_tensors()
        self.outputs=[np.empty(tuple(t.dims),np.int8) for t in self.output_tensors]
        self.fix_points={t.name:int(t.get_attr('fix_point')) for t in self.output_tensors}
        self.decoder=MoveNetDecoder(prototxt)
        self.native=NativeDecoder(self.decoder,self.input_fix)
        self.info={'name':'AMD MoveNet','fingerprint':hex(actual),'model_sha256':hashlib.sha256(self.model.read_bytes()).hexdigest(),
            'input_shape':list(self.input.shape),'input_fix_point':self.input_fix,
            'outputs':[{'name':t.name,'shape':list(t.dims),'fix_point':self.fix_points[t.name]} for t in self.output_tensors]}
    def predict(self, rgb):
        if rgb.shape!=(192,192,3) or rgb.dtype!=np.uint8: raise ValueError('Expected RGB uint8 192x192')
        start=time.monotonic()
        # Vitis AI 2.5 GLOBAL_ENABLE_ROUND_SETINPUT defaults to 0:
        # conversion truncates toward zero (not NumPy ties-to-even rounding).
        self.native.quantize(np.ascontiguousarray(rgb),self.input)
        before=time.monotonic()
        job=self.runner.execute_async([self.input],self.outputs)
        status=self.runner.wait(job)
        if status not in (0,None): raise RuntimeError('VART job failed: '+str(status))
        done=time.monotonic()
        points=self.native.decode({t.name:o for t,o in zip(self.output_tensors,self.outputs)},self.fix_points)
        return points,{'quantize_ms':(before-start)*1000,'dpu_ms':(done-before)*1000,'postprocess_ms':(time.monotonic()-done)*1000}
