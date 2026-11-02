"""XRT BO ownership and bounded cache synchronization. Does not load an xclbin."""
import ctypes as c
import weakref
import numpy as np
P=c.c_void_p
class Device:
 def __init__(self):
  self.lib=c.CDLL('libxrt_coreutil.so',use_errno=True)
  for name,result,args in [('xrtDeviceOpen',P,[c.c_uint]),('xrtDeviceClose',c.c_int,[P]),('xrtBOAlloc',P,[P,c.c_size_t,c.c_uint64,c.c_uint32]),('xrtBOFree',c.c_int,[P]),('xrtBOMap',P,[P]),('xrtBOAddress',c.c_uint64,[P]),('xrtBOSync',c.c_int,[P,c.c_int,c.c_size_t,c.c_size_t])]:
   f=getattr(self.lib,name);f.restype=result;f.argtypes=args
  self.handle=self.lib.xrtDeviceOpen(0)
  if not self.handle:raise RuntimeError('xrtDeviceOpen failed')
  self.buffers=set()
 def alloc(self,size,cacheable=True):return Buffer(self,size,cacheable)
 def close(self):
  if self.buffers:raise RuntimeError('Close BOs before device')
  if self.handle:
   rc=self.lib.xrtDeviceClose(self.handle);self.handle=None
   if rc:raise RuntimeError('xrtDeviceClose: '+str(rc))
class Buffer:
 def __init__(self,device,size,cacheable):
  if not device.handle or size<=0:raise ValueError('Invalid device/size')
  self.device=device;self.size=size;self.handle=device.lib.xrtBOAlloc(device.handle,size,(1<<24) if cacheable else 0,0)
  if not self.handle:raise RuntimeError('xrtBOAlloc failed')
  device.buffers.add(self)
  self.leases=[]
  self.address=int(device.lib.xrtBOAddress(self.handle));self.ptr=device.lib.xrtBOMap(self.handle)
  if not self.ptr or self.address==0xffffffffffffffff:self.close();raise RuntimeError('XRT BO mapping/address unavailable')
 def array(self):
  if not self.handle:raise RuntimeError('Closed BO')
  # NumPy's base memoryview keeps this storage and its BO owner alive.
  # Device writes must still be externally synchronized with CPU access.
  storage=(c.c_uint8*self.size).from_address(self.ptr);storage._bo_owner=self
  self.leases=[r for r in self.leases if r() is not None]
  self.leases.append(weakref.ref(storage))
  return np.ctypeslib.as_array(storage)
 def sync(self,direction,size=None,offset=0):
  n=self.size-offset if size is None else size
  if not self.handle or direction not in (0,1) or offset<0 or n<0 or offset+n>self.size:raise ValueError('Invalid BO synchronization range')
  rc=self.device.lib.xrtBOSync(self.handle,direction,n,offset)
  if rc:raise RuntimeError('xrtBOSync failed: '+str(rc))
 def close(self):
  if self.handle:
   if any(r() is not None for r in self.leases):raise RuntimeError('Release all NumPy views before freeing BO')
   rc=self.device.lib.xrtBOFree(self.handle);self.handle=None;self.ptr=None;self.device.buffers.remove(self)
   if rc:raise RuntimeError('xrtBOFree failed: '+str(rc))
