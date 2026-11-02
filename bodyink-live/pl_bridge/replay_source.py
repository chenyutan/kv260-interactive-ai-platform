"""Finite lossless prerecorded sources, independent of inference/model code."""
import ctypes as c,hashlib,time
from pathlib import Path
import numpy as np
class NpzSource:
 def __init__(self,folder,manifest):
  self.folder=Path(folder);self.m=manifest;self.load_seconds=[];self.preload_seconds=0.;self.decompress_seconds=[]
 def __iter__(self):
  index=0
  for chunk in self.m['chunks']:
   before=time.monotonic();path=self.folder/chunk['file']
   if hashlib.sha256(path.read_bytes()).hexdigest()!=chunk['sha256']:raise ValueError('Input chunk checksum failed')
   with np.load(path,allow_pickle=False) as data:raw=data['raw'];times=data['times']
   self.load_seconds.append(time.monotonic()-before)
   assert raw.shape==(chunk['len'],self.m['height'],self.m['stride']) and raw.dtype==np.uint8
   assert len(times)==len(raw) and chunk['first_index']==index
   for pixels,pts in zip(raw,times):
    yield index,pixels,float(pts);index+=1
class Lz4Source:
 def __init__(self,folder,manifest,verify_pixels=False):
  start=time.monotonic();self.m=manifest;self.verify=verify_pixels;self.load_seconds=[];self.decompress_seconds=[]
  item=manifest['lz4'];path=Path(folder)/item['file']
  available=next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
  if path.stat().st_size>available*.70:raise MemoryError('Replay preload would consume reserved runtime memory')
  self.data=path.read_bytes()
  if len(self.data)!=item['bytes'] or hashlib.sha256(self.data).hexdigest()!=item['sha256']:raise ValueError('LZ4 pack checksum failed')
  self.entries=item['frames'];assert len(self.entries)==manifest['frame_count']
  self.origin=c.cast(c.c_char_p(self.data),c.c_void_p).value
  self.lib=c.CDLL('liblz4.so.1');self.fn=self.lib.LZ4_decompress_safe
  self.fn.argtypes=[c.c_void_p,c.c_void_p,c.c_int,c.c_int];self.fn.restype=c.c_int
  self.preload_seconds=time.monotonic()-start
 def __iter__(self):
  size=self.m['height']*self.m['stride']
  for index,entry in enumerate(self.entries):
   offset,length=entry['offset'],entry['size']
   if offset<0 or length<=0 or offset+length>len(self.data) or max(size,length)>2147483647:raise ValueError('Invalid compressed frame bounds')
   start=time.monotonic();pixels=np.empty((self.m['height'],self.m['stride']),np.uint8)
   if self.fn(self.origin+offset,pixels.ctypes.data,length,size)!=size:raise ValueError('Frame decompression failed')
   if self.verify and hashlib.sha256(pixels).hexdigest()!=entry['raw_sha256']:raise ValueError('Decoded pixel checksum mismatch')
   self.decompress_seconds.append(time.monotonic()-start)
   yield index,pixels,entry['pts']
if __name__=='__main__':
 import argparse,json
 p=argparse.ArgumentParser();p.add_argument('pack',type=Path);a=p.parse_args()
 m=json.loads((a.pack/'manifest-lz4.json').read_text());source=Lz4Source(a.pack,m,verify_pixels=True)
 count=sum(1 for _ in source)
 assert count==m['frame_count']
 print(json.dumps(dict(all_board_decoded_pixels_match_host_sha256=True,frames=count,preload_seconds=source.preload_seconds,decompress_verify_seconds=sum(source.decompress_seconds))))
