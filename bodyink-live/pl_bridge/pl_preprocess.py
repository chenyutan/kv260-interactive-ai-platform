"""Single-owner MMIO driver for the verified BodyInk 0x42495001 register ABI.

Use only after loading matching firmware. A failed cancellation leaves BOs
owned by this object: the caller must reset/unload PL before releasing them.
"""
import ctypes as c
import fcntl
import mmap
import os
import time
from pathlib import Path

class Preprocessor:
    BASE = 0x81000000
    OUTPUT_BYTES = 256 * 256 * 3
    CLOCK_HZ = 99999000

    def __init__(self, base=BASE):
        if base != self.BASE:
            raise ValueError('Unverified MMIO address')
        self.active_buffers = None
        self.lock = open('/home/ubuntu/bodyink-live/pl-deploy-0914/owner.lock', 'a+')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        matches = []
        for item in Path('/sys/class/uio').glob('uio*'):
            addr = item/'maps/map0/addr'
            if addr.exists() and int(addr.read_text().strip(), 0) == base:
                if int((item/'maps/map0/size').read_text().strip(),0) != 4096:
                    raise RuntimeError('Unexpected UIO register window size')
                matches.append('/dev/'+item.name)
        if len(matches) != 1:
            raise RuntimeError('Matching PL UIO device absent; load verified firmware first')
        fd = os.open(matches[0], os.O_RDWR | os.O_SYNC)
        try:
            self.mm = mmap.mmap(fd, 4096, mmap.MAP_SHARED,
                                mmap.PROT_READ | mmap.PROT_WRITE, offset=0)
        finally:
            os.close(fd)
        self.storage = (c.c_uint8 * 4096).from_buffer(self.mm)
        self.ptr = c.addressof(self.storage)
        self.lib = c.CDLL(os.environ.get('BODYINK_PL_WAIT_LIBRARY',str(Path(__file__).with_name('libpl_mmio.so'))))
        self.lib.bodyink_read32.argtypes = [c.c_void_p, c.c_size_t]
        self.lib.bodyink_read32.restype = c.c_uint32
        self.lib.bodyink_write32.argtypes = [c.c_void_p, c.c_size_t, c.c_uint32]
        self.lib.bodyink_write32.restype = None
        if hasattr(self.lib,'bodyink_wait_done'):
            self.lib.bodyink_wait_done.argtypes = [c.c_void_p,c.c_uint64,c.POINTER(c.c_uint32)]
            self.lib.bodyink_wait_done.restype = c.c_int
        if self.read(0) != 0x42495001:
            raise RuntimeError('Preprocessor ID mismatch; no commands issued')
        if self.read(8) & 1:
            raise RuntimeError('Preprocessor already active; refusing ownership')
        self.write(0x0c, 0)  # Poll completion; no unclaimed IRQs.

    def read(self, offset):
        if offset % 4 or not 0 <= offset <= 0x3c:
            raise ValueError('Invalid register')
        return int(self.lib.bodyink_read32(self.ptr, offset))

    def write(self, offset, value):
        if offset % 4 or not 0 <= offset <= 0x3c or not 0 <= value <= 0xffffffff:
            raise ValueError('Invalid register/value')
        self.lib.bodyink_write32(self.ptr, offset, value)

    def submit(self, source, output, width, height, stride, roi, tag):
        x, y, w, h = map(int, roi)
        if self.active_buffers or self.read(8) & 1:
            raise RuntimeError('Previous DMA job not drained')
        if not (2 <= width <= 4096 and width % 2 == 0 and
                1 <= height <= 4096 and width * 2 <= stride <= 65535 and stride % 4 == 0 and
                0 <= x < width and 0 <= y < height and w > 0 and h > 0 and
                x + w <= width and y + h <= height and 0 <= tag <= 0xffffffff):
            raise ValueError('Invalid frame/ROI/tag')
        for bo, size in ((source, height * stride), (output, self.OUTPUT_BYTES)):
            if not bo.handle or bo.size < size or bo.address % 4 or bo.address + size > 0x7ff00000:
                raise ValueError('BO must cover job within mapped low DDR')
        if source.address < output.address + output.size and output.address < source.address + source.size:
            raise ValueError('Source and output BOs overlap')
        for offset, value in ((0x10, source.address), (0x14, 0),
                              (0x18, output.address), (0x1c, 0),
                              (0x20, width | height << 16), (0x24, stride),
                              (0x28, x | y << 16), (0x2c, w | h << 16), (0x30, tag)):
            self.write(offset, value)
        self.active_buffers = (source, output)
        self.tag = tag
        self.write(4, 1)

    def wait(self, timeout=1.0):
        if self.active_buffers is None:
            raise RuntimeError('No submitted job')
        deadline = time.monotonic() + timeout
        completed_status = None
        if hasattr(self.lib,'bodyink_wait_done'):
            status_out=c.c_uint32()
            result=self.lib.bodyink_wait_done(self.ptr,int(max(0,min(timeout,10.0))*1e9),c.byref(status_out))
            if result<0:raise RuntimeError('Native MMIO wait failed; DMA buffers retained')
            if result==0:completed_status=status_out.value
        while True:
            status = self.read(8) if completed_status is None else completed_status
            completed_status = None
            if status & 2 and not status & 1:
                tag, cycles = self.read(0x34), self.read(0x38)
                self.active_buffers = None
                if tag != self.tag:
                    raise RuntimeError('Completion tag mismatch')
                if status & 12:
                    raise RuntimeError(f'DMA error/cancellation: {status:#x}')
                return {'status': status, 'tag': tag, 'cycles': cycles,
                        'hardware_ms': cycles * 1000 / self.CLOCK_HZ}
            if time.monotonic() >= deadline:
                self.write(4, 2)
                drain_deadline = time.monotonic() + 1.0
                while time.monotonic() < drain_deadline:
                    status = self.read(8)
                    if status & 2 and not status & 1:
                        self.active_buffers = None
                        raise TimeoutError('DMA timed out; cancellation drained successfully')
                    time.sleep(.001)
                raise RuntimeError('FATAL: DMA not drained; reset PL before freeing BOs or exiting')
            time.sleep(.0001)

    def close(self):
        if self.active_buffers is not None:
            raise RuntimeError('Cannot close while DMA owns BOs')
        del self.storage
        self.mm.close()
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()
