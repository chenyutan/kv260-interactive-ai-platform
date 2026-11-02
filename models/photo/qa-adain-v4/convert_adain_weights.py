"""Strictly decode pinned AdaIN legacy state_dicts into non-pickle NPZ.

Never imports torch or resolves arbitrary pickle globals. This only implements
the FloatStorage / contiguous tensor subset. Adapted from the isolated
MicroAST converter, with bounded limits for VGG and legacy tensor-v1 support.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import pickle
import struct
import numpy as np


class FloatStorage:
    pass


@dataclass
class StorageSpec:
    key: str
    count: int


@dataclass
class TensorSpec:
    storage: StorageSpec
    offset: int
    shape: tuple
    stride: tuple


def tensor(storage, offset, shape, stride, requires_grad=False, hooks=None):
    if not isinstance(storage, StorageSpec) or requires_grad or hooks:
        raise ValueError('Unexpected tensor schema')
    if not isinstance(offset, int) or offset < 0:
        raise ValueError('Invalid storage offset')
    if not isinstance(shape, tuple) or not 1 <= len(shape) <= 4:
        raise ValueError('Invalid shape')
    if not isinstance(stride, tuple) or len(stride) != len(shape):
        raise ValueError('Invalid stride')
    expected = 1
    for n, s in zip(reversed(shape), reversed(stride)):
        if not isinstance(n, int) or not 1 <= n <= 8192 or s != expected:
            raise ValueError('Only bounded contiguous tensors supported')
        expected *= n
    if offset + expected > storage.count:
        raise ValueError('Tensor exceeds storage')
    return TensorSpec(storage, offset, shape, stride)


class RestrictedReader(pickle.Unpickler):
    def __init__(self, stream, storages):
        super().__init__(stream)
        self.storages = storages

    def find_class(self, module, name):
        allowed = {
            ('collections', 'OrderedDict'): OrderedDict,
            ('torch', 'FloatStorage'): FloatStorage,
            ('torch._utils', '_rebuild_tensor'): tensor,
            ('torch._utils', '_rebuild_tensor_v2'): tensor,
        }
        if (module, name) not in allowed:
            raise pickle.UnpicklingError('Disallowed global: ' + module + '.' + name)
        return allowed[(module, name)]

    def persistent_load(self, value):
        if not isinstance(value, tuple) or len(value) != 6:
            raise ValueError('Unexpected persistent id')
        kind, dtype, key, location, count, view = value
        if kind != 'storage' or dtype is not FloatStorage or view is not None:
            raise ValueError('Unexpected storage type')
        if not isinstance(key, str) or not key.isdigit():
            raise ValueError('Unexpected storage key')
        if not isinstance(count, int) or not 1 <= count <= 3_000_000:
            raise ValueError('Storage out of bounds')
        spec = StorageSpec(key, count)
        if key in self.storages and self.storages[key] != spec:
            raise ValueError('Conflicting storage')
        self.storages[key] = spec
        return spec


def convert(source, target):
    data = source.read_bytes()
    if len(data) > 90_000_000:
        raise ValueError('File too large')
    stream = io.BytesIO(data)
    storages = {}
    def load():
        return RestrictedReader(stream, storages).load()
    if load() != 119547037146038801333356 or load() != 1001:
        raise ValueError('Not expected torch legacy format')
    system = load()
    if not system.get('little_endian'):
        raise ValueError('Only little endian supported')
    state = load()
    keys = load()
    if not isinstance(state, OrderedDict) or set(keys) != set(storages):
        raise ValueError('Unexpected state dict')
    arrays = {}
    for key in keys:
        count = struct.unpack('<q', stream.read(8))[0]
        if count != storages[key].count:
            raise ValueError('Storage size mismatch')
        raw = stream.read(count * 4)
        if len(raw) != count * 4:
            raise ValueError('Truncated storage')
        arrays[key] = np.frombuffer(raw, dtype='<f4')
    if stream.read(1):
        raise ValueError('Unexpected trailing data')
    tensors = {}
    for name, spec in state.items():
        if not isinstance(name, str) or not isinstance(spec, TensorSpec):
            raise ValueError('Unexpected state item')
        size = int(np.prod(spec.shape))
        tensors[name] = arrays[spec.storage.key][spec.offset:spec.offset + size].reshape(spec.shape)
        if not np.isfinite(tensors[name]).all():
            raise ValueError('Nonfinite weights')
    np.savez(target, **tensors)
    return {'source':source.name, 'target':target.name,
            'source_sha256':hashlib.sha256(data).hexdigest(),
            'target_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'tensors':len(tensors), 'parameters':sum(a.size for a in tensors.values()),
            'tensor_shapes':{k:list(a.shape) for k,a in tensors.items()}}


if __name__ == '__main__':
    root = Path(__file__).resolve().parent / 'upstream' / 'models'
    expected_sizes = {'decoder.pth': 14023458, 'vgg_normalised.pth': 80102481}
    for name, size in expected_sizes.items():
        if (root / name).stat().st_size != size:
            raise ValueError('Release asset size mismatch: ' + name)
    rows = [convert(root / name, (root / name).with_suffix('.npz'))
            for name in expected_sizes]
    (root / 'npz-manifest.json').write_text(json.dumps(rows, indent=2))
    print(json.dumps([{k:v for k,v in x.items() if k != 'tensor_shapes'} for x in rows], indent=2))
