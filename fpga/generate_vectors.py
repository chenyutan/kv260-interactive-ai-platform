"""Independent vectors + exact comparison with the production C sampler."""
import json,subprocess,hashlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve().parent;V=HERE/'vectors'
def linux(p):return '/mnt/'+p.drive[0].lower()+str(p.resolve())[2:].replace('\\','/')
def run(args):subprocess.run(['wsl','-e',*map(str,args)],check=True,capture_output=True)
run(['gcc','-O2',linux(HERE/'sim/reference_driver.c'),linux(ROOT/'bodyink-live/preprocess.c'),'-o',linux(HERE/'sim/reference_driver')])
run(['gcc','-O2',linux(HERE/'sim/quant_reference.c'),'-o',linux(HERE/'sim/quant_reference')])
mean=np.array([110.3385,113.22,112.302],np.float32)
lut=np.clip(np.trunc((np.arange(256,dtype=np.float32)[:,None]-mean)*np.float32(.00392157)*np.float32(128)),-128,127).astype(np.int8)
np.repeat(np.arange(256,dtype=np.uint8),3).tofile(V/'quant_input.bin')
run([linux(HERE/'sim/quant_reference'),linux(V/'quant_input.bin'),linux(V/'quant_expected.bin')])
np.testing.assert_array_equal(np.fromfile(V/'quant_expected.bin',np.int8).reshape(256,3),lut)
for c,name in enumerate('rgb'):(V/('quant_'+name+'.hex')).write_text(''.join('%02x\n'%int(v) for v in lut[:,c].view(np.uint8)))
def model(p,a,b):
 p=p.astype(np.int64);y=np.maximum(p[:,:,0]-16,0)*1220542+524288;u=p[:,:,1]-128;v=p[:,:,2]-128
 rgb=np.clip(np.stack(((y+v*1673527)>>20,(y-u*409993-v*852492)>>20,(y+u*2116026)>>20),axis=-1),0,255)
 a=a[:,None];b=b[:,None];top=rgb[:,0]*(2048-a)+rgb[:,1]*a;bottom=rgb[:,2]*(2048-a)+rgb[:,3]*a
 return ((top*(2048-b)+bottom*b+2097152)>>22).astype(np.uint8)
def coordinates(offset,length,limit):
 f=np.clip(offset+(np.arange(256)+.5)*length/256-.5,offset,offset+length-1);lo=f.astype(int);hi=np.minimum(lo+1,limit-1);a=((f-lo)*2048+.5).astype(int)
 carry=a==2048;lo=np.where(carry,hi,lo);a=np.where(carry,0,a);return lo,hi,a
records=[];total=0
with (V/'input.hex').open('w') as ins,(V/'expected.hex').open('w') as outs:
 def emit(p,a,b,rgb,last):
  global total
  q=lut[rgb,np.arange(3)].view(np.uint8)
  for i in range(len(p)):
   word=int.from_bytes(p[i].tobytes(),'little')|(int(a[i])<<96)|(int(b[i])<<107)|(int(last[i])<<118)
   expected=int.from_bytes(rgb[i].tobytes()+q[i].tobytes(),'little')
   ins.write(f'{word:030x}\n');outs.write(f'{expected:012x}\n')
  total+=len(p)
 for height,width,index in [(1080,1920,6),(720,1280,17),(540,960,28)]:
  raw=np.ascontiguousarray(np.random.default_rng(index).integers(0,256,(height,width,2),dtype=np.uint8));raw.tofile(V/'source.yuyv')
  for size in (height,int(height*.8)):
   x=(width-size)//2;y=0;x0,x1,a=coordinates(x,size,width);y0,y1,b=coordinates(y,size,height)
   p=[];flat=raw.reshape(height,width*2)
   for xx,yy in [(x0,y0),(x1,y0),(x0,y1),(x1,y1)]:
    base=(xx&~1)*2
    p.append(np.stack([flat[yy[:,None],(xx*2)[None,:]],flat[yy[:,None],(base+1)[None,:]],flat[yy[:,None],(base+3)[None,:]]],axis=-1).reshape(-1,3))
   p=np.stack(p,axis=1);aa=np.tile(a,256);bb=np.repeat(b,256);rgb=model(p,aa,bb)
   run([linux(HERE/'sim/reference_driver'),linux(V/'source.yuyv'),linux(V/'reference.rgb'),width,height,x,y,size,size])
   reference=np.fromfile(V/'reference.rgb',np.uint8).reshape(-1,3);np.testing.assert_array_equal(rgb,reference)
   last=np.zeros(len(p),int);last[-1]=1;emit(p,aa,bb,rgb,last)
   records.append({'source':[width,height],'synthetic_seed':index,'roi':[x,y,size,size],'pixels':len(p),'rgb_max_error':0,'rgb_sha256':hashlib.sha256(rgb.tobytes()).hexdigest()})
 rng=np.random.default_rng(260)
 p=rng.integers(0,256,(32768,4,3),dtype=np.uint8);a=rng.integers(0,2048,len(p));b=rng.integers(0,2048,len(p));a[::8]=0;b[::9]=0;a[1::8]=2047;b[1::9]=2047
 rgb=model(p,a,b);last=np.zeros(len(p),int);last[-1]=1;emit(p,a,b,rgb,last)
 # Exact chroma/luma boundaries, including below-black and saturated values.
 p=np.array([[y,u,v] for y in [0,15,16,17,128,235,254,255] for u in [0,1,16,127,128,129,240,254,255] for v in [0,1,16,127,128,129,240,254,255]],np.uint8)
 p=np.repeat(p[:,None,:],4,axis=1);a=np.zeros(len(p),int);b=a.copy();rgb=model(p,a,b);last=np.zeros(len(p),int);last[-1]=1;emit(p,a,b,rgb,last)
(V/'count.txt').write_text(str(total)+'\n')
report={'cases':records,'vectors':total,'quant_lut_entries_verified':768,'input_shape':[1,256,256,3],'input_fix_point':7,'rounding':'truncate toward zero','sampler_sha256':hashlib.sha256((ROOT/'bodyink-live/preprocess.c').read_bytes()).hexdigest()}
(HERE/'vectors-result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

