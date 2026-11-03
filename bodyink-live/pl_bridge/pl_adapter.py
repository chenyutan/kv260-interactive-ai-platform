"""Blocking PL producer; returns owned tensors so latest-frame queues stay safe."""
import gc
from collections import OrderedDict
import time
import numpy as np
from pl_preprocess import Preprocessor
from xrt_buffer import Device
from roi_staging import RoiStaging
from hourglass import QuantizedRGB

class PLAdapter:
    def __init__(self):
        self.pp = Preprocessor()
        self.device = Device()
        self.source = self.output = None
        self.source_view = self.output_view = None
        self.geometry = None
        self.plans = OrderedDict()
        self.plan_limit = 64
        self.plan_hits = self.plan_misses = self.plan_evictions = 0
        self.tag = 0
        self.last_timing = {}

    def _get_plan(self, key, width, height, stride, roi):
        # A plan owns only reusable addressing data, never source/output BOs.
        # Construct before eviction: invalid geometry must leave the cache intact.
        try:
            plan = self.plans.pop(key)
        except KeyError:
            plan = RoiStaging(width, height, stride, roi)
            self.plan_misses += 1
        else:
            self.plan_hits += 1
        self.plans[key] = plan
        if len(self.plans) > self.plan_limit:
            self.plans.popitem(last=False)
            self.plan_evictions += 1
        return plan

    def _release_buffers(self):
        if self.pp.active_buffers is not None:
            raise RuntimeError('PL job still owns buffers')
        self.source_view = self.output_view = None
        gc.collect()
        if self.source is not None: self.source.close()
        if self.output is not None: self.output.close()
        self.source = self.output = None

    def resize(self, raw, width, height, stride, size=(256,256), roi=None):
        if size != (256,256) or roi is None:
            raise ValueError('PL adapter requires an explicit 256x256 ROI')
        raw = np.frombuffer(raw, dtype=np.uint8)
        if raw.size != height*stride:
            raise ValueError('YUYV frame size mismatch')
        key = (width,height,stride,tuple(roi))
        plan = self._get_plan(key, width, height, stride, roi)
        if self.geometry != (width,height,stride):
            self._release_buffers()
            self.source = self.device.alloc(height*stride,True)
            self.output = self.device.alloc(self.pp.OUTPUT_BYTES,True)
            self.source_view = self.source.array()
            self.output_view = self.output.array()
            self.geometry = (width,height,stride)
        begin = time.monotonic()
        if height >= 720:
            plan.copy(raw,self.source_view)
        else:
            np.copyto(self.source_view,raw)
        self.source.sync(0)
        staged = time.monotonic()
        self.tag = (self.tag+1) & 0xffffffff
        self.pp.submit(self.source,self.output,width,height,stride,roi,self.tag)
        status = self.pp.wait()
        done = time.monotonic()
        self.output.sync(1)
        # This copy detaches queue ownership from the reusable device buffer.
        result = self.output_view.view(np.int8).reshape(1,256,256,3).copy()
        end = time.monotonic()
        self.last_timing = dict(staging_ms=(staged-begin)*1000,
                                hardware_ms=status['hardware_ms'],
                                pl_wait_ms=(done-staged)*1000,
                                handoff_ms=(end-done)*1000)
        return QuantizedRGB(result)

    def close(self):
        if self.pp.active_buffers is not None:
            self.pp.drain_before_exit()
        self._release_buffers()
        self.plans.clear()
        self.pp.close()
        self.device.close()
