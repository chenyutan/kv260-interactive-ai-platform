"""Thread-confined RGB colour-ink filter; no capture or inference side effects."""
import ctypes
from pathlib import Path
import numpy as np


class InkFilter:
    def __init__(self, width, height, library=None):
        self.width, self.height = int(width), int(height)
        self.library = ctypes.CDLL(str(Path(library or Path(__file__).with_name('libink_filter.so')).resolve()))
        lib = self.library
        lib.ink_create.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.ink_create.restype = ctypes.c_void_p
        lib.ink_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        lib.ink_process.restype = ctypes.c_int
        lib.ink_destroy.argtypes = [ctypes.c_void_p]
        lib.ink_destroy.restype = None
        lib.ink_version.argtypes = []
        lib.ink_version.restype = ctypes.c_char_p
        self.version = lib.ink_version().decode('ascii')
        for name in ('ink_timing', 'ink_cpu_timing'):
            if hasattr(lib, name):
                fn = getattr(lib, name)
                fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
                fn.restype = None
        self.handle = lib.ink_create(self.width, self.height)
        if not self.handle:
            raise ValueError('Unsupported ink image dimensions')

    def render(self, rgb, points=None, out=None):
        if not self.handle:
            raise RuntimeError('Ink filter has been closed')
        rgb = np.asarray(rgb)
        if rgb.dtype != np.uint8 or rgb.shape != (self.height, self.width, 3):
            raise ValueError('Expected RGB uint8 with the filter dimensions')
        rgb = np.ascontiguousarray(rgb)
        if points is not None:
            points = np.asarray(points, dtype=np.float32)
            if points.shape != (17, 3):
                raise ValueError('Expected seventeen normalized pose points')
            points = np.ascontiguousarray(points)
        if out is None:
            out = np.empty_like(rgb)
        elif (not isinstance(out, np.ndarray) or out.dtype != np.uint8 or
              out.shape != rgb.shape or not out.flags.c_contiguous or
              not out.flags.writeable or np.shares_memory(rgb, out)):
            raise ValueError('Expected separate writable contiguous RGB uint8 output')
        result = self.library.ink_process(self.handle, rgb.ctypes.data, rgb.strides[0],
                     None if points is None else points.ctypes.data, out.ctypes.data, out.strides[0])
        if result:
            raise RuntimeError('Ink render failed: %s' % result)
        return out

    def timing(self):
        """Last native call, in milliseconds; wall time includes CPU contention."""
        if not self.handle:
            raise RuntimeError('Ink filter has been closed')
        result = {}
        for key, name in (('wall_ms', 'ink_timing'), ('thread_cpu_ms', 'ink_cpu_timing')):
            if hasattr(self.library, name):
                values = (ctypes.c_double * 4)()
                getattr(self.library, name)(self.handle, values)
                result[key] = list(values)
        return result

    def close(self):
        if getattr(self, 'handle', None):
            self.library.ink_destroy(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()
