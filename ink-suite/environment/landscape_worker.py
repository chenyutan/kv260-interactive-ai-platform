"""Single imported-photo job; CPU only, no camera/network/cloud operations.

Usage: python3 -B landscape_worker.py /path/to/job
The caller places input.npy (RGB uint8, long side <=1920) and settings.json.
"""
import hashlib
import gc
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

# Respect the two-core CPU budget even when imported NumPy uses a BLAS runtime.
for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
              'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_name] = '2'

import numpy as np
from PIL import Image

try:
    from .landscape_finish import finish_landscape, VERSION
except ImportError:
    from landscape_finish import finish_landscape, VERSION

RESAMPLE = getattr(Image, 'Resampling', Image)
MODEL_SHA256 = '7ca0cfd577415b034bc358f775ad9e2d317de344c07f4482945d2be85562d35b'
INFERENCE_LONG_SIDE = 960
INFERENCE_PIXEL_BUDGET = 524288
QUALITY_PROFILE = 'clarity-960-area524288'


def inference_size(width, height):
    """Bound actual neural work for both landscape and square photographs.

    Flooring to the model's eight-pixel grid never enlarges a small source or
    exceeds the area budget. The finishing pass restores source aspect ratio.
    """
    if (not isinstance(width, int) or not isinstance(height, int)
            or isinstance(width, bool) or isinstance(height, bool)
            or min(width, height) < 16 or max(width, height) > 1920):
        raise ValueError('Photo dimensions must be integers in 16..1920')
    ratio = min(1., INFERENCE_LONG_SIDE / max(width, height),
                math.sqrt(INFERENCE_PIXEL_BUDGET / (width * height)))
    size = tuple(int(d * ratio / 8) * 8 for d in (width, height))
    if min(size) < 16:
        raise ValueError('Photo is too narrow; choose a less extreme panorama')
    return size


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def load_input(path):
    if path.stat().st_size > 1920*1920*3 + 65536:
        raise ValueError('Photo exceeds the 1920-pixel input budget')
    # Inspect the small NPY header before allowing NumPy to allocate its array.
    with path.open('rb') as stream:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
        else:
            raise ValueError('Unsupported NPY input version')
    if (len(shape) != 3 or shape[2] != 3 or dtype != np.dtype('uint8')
            or min(shape[:2]) < 16 or max(shape[:2]) > 1920 or fortran):
        raise ValueError('Photo must be RGB uint8, 16..1920 pixels per side')
    return np.load(str(path), allow_pickle=False)


def run(folder):
    folder = Path(folder).resolve(strict=True)
    start = time.monotonic()
    # Retried jobs must never publish a previous successful image on failure.
    for name in ('output.npy', 'output.npy.tmp', 'result.json'):
        (folder/name).unlink(missing_ok=True)
    def stage(name):
        atomic_json(folder/'status.json', {'stage': name,
                    'elapsed_s': round(time.monotonic()-start, 3)})
    settings_path = folder/'settings.json'
    if settings_path.stat().st_size > 16384:
        raise ValueError('Photo settings are too large')
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    if not isinstance(settings, dict):
        raise ValueError('Photo settings must be an object')
    style = settings.get('style', 'color')
    if style not in ('color', 'ink'):
        raise ValueError('Style must be color or ink')
    # Only the bundled, identified model may be executed by the imported-photo UI.
    model = Path(__file__).resolve().parent/'models'/'figure-ink-v3.onnx'
    model_sha = hashlib.sha256(model.read_bytes()).hexdigest()
    if model_sha != MODEL_SHA256:
        raise ValueError('The bundled ink model failed its integrity check')
    stage('preparing_image')
    rgb = load_input(folder/'input.npy')
    height, width = rgb.shape[:2]
    size = inference_size(width, height)
    source = Image.fromarray(rgb).resize(size, RESAMPLE.BICUBIC)
    content = np.ascontiguousarray(np.asarray(source, dtype=np.float32)
                                  .transpose(2, 0, 1)[None]/255.)
    stage('loading_model')
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    options.add_session_config_entry('session.inter_op.allow_spinning', '0')
    session = ort.InferenceSession(str(model), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    if len(session.get_inputs()) != 1:
        raise ValueError('Expected one fixed-style image input')
    stage('painting')
    inference_start = time.monotonic()
    result = session.run(None, {session.get_inputs()[0].name: content})[0]
    inference_s = time.monotonic()-inference_start
    if result.shape != content.shape or not np.isfinite(result).all():
        raise ValueError('Ink network returned invalid image pixels')
    stage('finishing')
    pixels = np.rint(np.clip(result[0].transpose(1, 2, 0), 0, 1)*255).astype(np.uint8)
    providers = session.get_providers()
    # Release the inference arena before allocating full-size finishing arrays.
    # The result is materialized in uint8; no runtime object is needed below.
    del session, result, content, source
    gc.collect()
    output = finish_landscape(rgb, pixels, style=style)
    with (folder/'output.npy.tmp').open('wb') as stream:
        np.save(stream, output, allow_pickle=False)
    (folder/'output.npy.tmp').replace(folder/'output.npy')
    atomic_json(folder/'result.json', {
        'renderer': 'landscape-ink-v2', 'finish_version': VERSION, 'style': style,
        'quality_profile': QUALITY_PROFILE,
        'inference_pixel_budget': INFERENCE_PIXEL_BUDGET,
        'model_sha256': model_sha, 'source_width': width, 'source_height': height,
        'inference_width': size[0], 'inference_height': size[1],
        'output_width': output.shape[1], 'output_height': output.shape[0],
        'output_resampling': 'bicubic_neural_plus_mineral_wash',
        'neural_super_resolution': False,
        'inference_s': inference_s, 'processing_s': time.monotonic()-start,
        'runtime': ort.__version__, 'providers': providers,
        'machine': platform.machine(), 'cpu_threads': 2,
        'neural_inference': True, 'external_network_used': False,
        'network_inference': False, 'camera_opened_by_worker': False,
    })
    stage('finished')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: landscape_worker.py JOB_FOLDER')
    try:
        run(Path(sys.argv[1]))
    except Exception as error:
        try:
            atomic_json(Path(sys.argv[1])/'status.json',
                        {'stage': 'failed', 'error': str(error)[:300]})
        except Exception:
            pass
        raise
