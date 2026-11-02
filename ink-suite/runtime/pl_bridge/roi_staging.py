"""Copy only the row spans read by the current fixed 256x256 RTL frontend.

Keep original frame addressing and stride; untouched BO bytes are invalid.
One plan covers one ROI. Do not reuse the buffer until its DMA job completes.
"""
import numpy as np
import ctypes
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=32)
def _row_indices(height,y,h):
 # Horizontal crop motion cannot change sampled source rows.
 fy=np.clip(y*512+(2*np.arange(256,dtype=np.int64)+1)*h-256,y*512,(y+h-1)*512)
 top=fy//512;bottom=np.minimum(top+1,height-1)
 rows=np.unique(np.concatenate((top,bottom))).astype(np.uint32)
 rows.setflags(write=False)
 return rows

@lru_cache(maxsize=1)
def _copy_library():
 native=ctypes.CDLL(str(Path(__file__).with_name('libroi_copy.so')))
 native.bodyink_copy_rows.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_size_t]
 native.bodyink_copy_rows.restype=ctypes.c_int
 return native

class RoiStaging:
 def __init__(self,width,height,stride,roi):
  x,y,w,h=map(int,roi)
  if not (2<=width<=4096 and width%2==0 and 1<=height<=4096 and stride>=width*2 and stride%4==0 and 0<=x<width and 0<=y<height and w>0 and h>0 and x+w<=width and y+h<=height):
   raise ValueError('Invalid RTL frame/ROI geometry')
  # Exact integer Q9 half-pixel positions used by bodyink_roi_frontend.
  self.rows=_row_indices(height,y,h)
  first=x//2;last=min(x+w,width-1)//2
  self.begin=first*4;self.end=(last+1)*4
  self.width=width;self.height=height;self.stride=stride
  self.bytes=len(self.rows)*(self.end-self.begin)
  self.native=_copy_library()
 def copy(self,source,destination):
  if source.dtype!=np.uint8 or destination.dtype!=np.uint8 or not source.flags.c_contiguous or not destination.flags.c_contiguous or source.size!=self.height*self.stride or destination.size!=source.size:
   raise ValueError('Expected contiguous full-stride uint8 frame buffers')
  if np.shares_memory(source,destination):raise ValueError('Source and destination must not overlap')
  rc=self.native.bodyink_copy_rows(destination.ctypes.data,source.ctypes.data,source.size,self.stride,self.rows.ctypes.data,len(self.rows),self.begin,self.end)
  if rc:raise RuntimeError('Invalid native row copy: '+str(rc))
