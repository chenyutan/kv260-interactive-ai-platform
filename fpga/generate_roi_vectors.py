"""Full-frame fixtures; C production sampler is the pixel oracle."""
from pathlib import Path
import numpy as np,subprocess,json
P=Path(__file__).resolve().parent;root=P.parent/'bodyink-live';out=P/'roi-vectors';out.mkdir(exist_ok=True)
def lin(p):return '/mnt/'+p.drive[0].lower()+str(p.resolve())[2:].replace('\\','/')
def run(a):subprocess.run(['wsl','-e',*map(str,a)],check=True,capture_output=True)
run(['gcc','-O2',lin(P/'sim/reference_driver.c'),lin(root/'preprocess.c'),'-o',lin(P/'sim/reference_driver')])
lut=np.stack([np.array([int(x,16) for x in (P/f'vectors/quant_{c}.hex').read_text().split()],np.uint8) for c in 'rgb'],axis=1)
rng=np.random.default_rng(2602);cases=[]
for height,width,idx,roi,pad in [(1080,1920,6,(420,0,1080,1080),0),(720,1280,17,(280,0,720,720),0),(540,960,28,(264,0,432,432),0),(720,1280,6,(279,11,577,601),16)]:
 raw=np.ascontiguousarray(np.random.default_rng(idx).integers(0,256,(height,width,2),dtype=np.uint8)).reshape(height,width*2);cases.append((raw,width,height,roi,pad,'synthetic'))
for width,height,roi,pad in [(32,24,(3,5,17,13),12),(32,24,(31,23,1,1),4),(2,1,(0,0,2,1),0),(4096,3,(4093,1,3,2),16)]:
 cases.append((rng.integers(0,256,(height,width*2),dtype=np.uint8),width,height,roi,pad,'synthetic'))
report=[]
for idx,(raw,width,height,roi,pad,source) in enumerate(cases):
 stride=width*2+pad;raw.tofile(out/'source.raw')
 run([lin(P/'sim/reference_driver'),lin(out/'source.raw'),lin(out/'reference.rgb'),width,height,*roi])
 rgb=np.fromfile(out/'reference.rgb',np.uint8).reshape(-1,3);q=lut[rgb,np.arange(3)]
 with (out/f'expected{idx}.hex').open('w') as f:
  for r,t in zip(rgb,q):f.write(f'{int.from_bytes(r.tobytes()+t.tobytes(),"little"):012x}\n')
 mem=np.full((height,stride),0xcd,np.uint8);mem[:,:width*2]=raw
 with (out/f'frame{idx}.hex').open('w') as f:
  for word in mem.reshape(-1).view('<u4'):f.write(f'{int(word):08x}\n')
 (out/f'config{idx}.txt').write_text(' '.join(map(str,[width,height,stride,*roi]))+'\n')
 report.append({'case':idx,'source':source,'size':[width,height],'stride':stride,'roi':roi,'source_words':height*stride//4,'output_pixels':65536})
(out/'cases.txt').write_text(str(len(cases))+'\n');(P/'roi-fixtures.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
