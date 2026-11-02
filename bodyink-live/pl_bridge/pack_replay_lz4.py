"""Repack every decoded pixel losslessly; no new video conversion."""
import argparse,hashlib,json,time
from pathlib import Path
import numpy as np
import lz4.block
p=argparse.ArgumentParser();p.add_argument('pack',type=Path);a=p.parse_args()
m=json.loads((a.pack/'manifest.json').read_text());out=a.pack/'frames.lz4blocks';entries=[];start=time.monotonic()
with out.with_suffix('.tmp').open('wb') as f:
 for chunk in m['chunks']:
  with np.load(a.pack/chunk['file'],allow_pickle=False) as data:
   for raw,pts in zip(data['raw'],data['times']):
    pixels=raw.tobytes();compressed=lz4.block.compress(pixels,store_size=False)
    if lz4.block.decompress(compressed,uncompressed_size=len(pixels))!=pixels:raise AssertionError('LZ4 roundtrip changed pixels')
    entries.append(dict(offset=f.tell(),size=len(compressed),pts=float(pts),raw_sha256=hashlib.sha256(pixels).hexdigest()));f.write(compressed)
out.with_suffix('.tmp').replace(out)
assert len(entries)==m['frame_count']
m['lz4']=dict(file=out.name,bytes=out.stat().st_size,sha256=hashlib.sha256(out.read_bytes()).hexdigest(),frames=entries,host_all_decoded_pixels_equal=True,repack_seconds=time.monotonic()-start)
(a.pack/'manifest-lz4.json').write_text(json.dumps(m,indent=2));print('frames',len(entries),'bytes',out.stat().st_size,'seconds',time.monotonic()-start)
