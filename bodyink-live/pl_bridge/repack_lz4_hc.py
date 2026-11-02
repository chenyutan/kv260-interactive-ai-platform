"""Reduce offline pack size without changing a single decoded input byte."""
import argparse,hashlib,json,time
from pathlib import Path
import lz4.block
p=argparse.ArgumentParser();p.add_argument('pack',type=Path);a=p.parse_args()
m=json.loads((a.pack/'manifest-lz4.json').read_text(encoding='utf-8'))
source=m['lz4'];target=a.pack/'frames-hc.lz4blocks';digest=hashlib.sha256();entries=[];start=time.monotonic()
with (a.pack/source['file']).open('rb') as inp,target.open('xb') as out:
 for e in source['frames']:
  inp.seek(e['offset']);raw=lz4.block.decompress(inp.read(e['size']),uncompressed_size=m['height']*m['stride'])
  assert hashlib.sha256(raw).hexdigest()==e['raw_sha256']
  block=lz4.block.compress(raw,mode='high_compression',compression=9,store_size=False)
  assert lz4.block.decompress(block,uncompressed_size=len(raw))==raw
  entries.append(dict(e,offset=out.tell(),size=len(block)));out.write(block);digest.update(block)
m['lz4']=dict(source,file=target.name,bytes=target.stat().st_size,sha256=digest.hexdigest(),frames=entries,
              lossless_repack_from_sha256=source['sha256'],high_compression=True,repack_seconds=time.monotonic()-start)
(a.pack/'manifest-hc.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
print(json.dumps(dict(frames=len(entries),before=source['bytes'],after=target.stat().st_size,seconds=time.monotonic()-start)))
