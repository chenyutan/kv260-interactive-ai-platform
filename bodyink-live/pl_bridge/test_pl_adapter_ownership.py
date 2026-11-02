"""The queue must never retain a view into the next DMA job's output buffer."""
import sys
import types
from unittest.mock import patch
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'live-runtime'))
with patch.dict(sys.modules,{'pl_preprocess':types.SimpleNamespace(Preprocessor=object)}):
    import pl_adapter as module

class Buffer:
    def __init__(self,size):self.data=np.zeros(size,np.uint8);self.handle=True
    def array(self):return self.data
    def sync(self,direction):pass
    def close(self):self.handle=False

class Device:
    def alloc(self,size,cacheable):return Buffer(size)
    def close(self):pass

class PP:
    OUTPUT_BYTES=256*256*3
    active_buffers=None
    def submit(self,source,output,*args):output.data[:]=source.data[0]
    def wait(self):return {'hardware_ms':1.0}
    def close(self):pass

def test_queued_tensor_survives_next_dma_and_shutdown(monkeypatch):
    monkeypatch.setattr(module,'Preprocessor',PP)
    monkeypatch.setattr(module,'Device',Device)
    monkeypatch.setattr(module,'RoiStaging',lambda *a:None)
    adapter=module.PLAdapter()
    first=adapter.resize(np.full(16,17,np.uint8),4,2,8,roi=(0,0,4,2))
    second=adapter.resize(np.full(16,29,np.uint8),4,2,8,roi=(0,0,4,2))
    assert not np.shares_memory(first.data,adapter.output_view)
    assert not np.shares_memory(second.data,adapter.output_view)
    assert not np.shares_memory(first.data,second.data)
    adapter.close()
    np.testing.assert_array_equal(first.data,17)
    np.testing.assert_array_equal(second.data,29)
