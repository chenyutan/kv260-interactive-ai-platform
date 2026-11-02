"""One CPU-only, fixed-style inference. No camera, download or network access."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
from PIL import Image
from ink_finish import finish_ink, VERSION as FINISH_VERSION


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def run(folder):
    start = time.monotonic()
    def stage(name):
        atomic_json(folder / 'status.json', {'stage': name})
    settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8'))
    model = Path(settings['model']).resolve(strict=True)
    if not model.is_file():
        raise ValueError('Model must be a local file')
    long_side = int(settings.get('long_side', 640))
    if long_side != 640:
        raise ValueError('Photo inference uses a fixed 640-pixel long-side limit')
    stage('loading_model')
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    session = ort.InferenceSession(str(model), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    if len(session.get_inputs()) != 1:
        raise ValueError('Expected a fixed-style network with one image input')
    stage('preparing_image')
    rgb = np.load(str(folder / 'input.npy'), allow_pickle=False)
    height, width = rgb.shape[:2]
    ratio = min(1., long_side / max(width, height))
    size = tuple(max(8, round(d * ratio / 8) * 8) for d in (width, height))
    source = Image.fromarray(rgb).resize(size, Image.BICUBIC)
    content = np.ascontiguousarray(np.asarray(source, dtype=np.float32).transpose(2, 0, 1)[None] / 255.)
    stage('painting')
    inference_start = time.monotonic()
    result = session.run(None, {session.get_inputs()[0].name: content})[0]
    inference_s = time.monotonic() - inference_start
    if result.shape != content.shape or not np.isfinite(result).all():
        raise ValueError('Network returned invalid pixels')
    stage('finishing')
    pixels = np.rint(np.clip(result[0].transpose(1, 2, 0), 0, 1) * 255).astype(np.uint8)
    pixels = finish_ink(rgb, pixels)
    # Save native model resolution. Display enlargement is a separate operation.
    np.save(str(folder / 'output.npy'), pixels, allow_pickle=False)
    atomic_json(folder / 'result.json', {
        'renderer': settings.get('renderer', 'figure-ink-v3-onnx'),
        'finish_version': FINISH_VERSION,
        'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
        'style_sha256': settings.get('style_sha256'),
        'source_width': width, 'source_height': height,
        'inference_width': size[0], 'inference_height': size[1],
        'inference_s': inference_s, 'processing_s': time.monotonic() - start,
        'runtime': ort.__version__, 'providers': session.get_providers(),
        'machine': platform.machine(), 'cpu_threads': 2,
        'network_inference': False, 'camera_opened_by_worker': False,
    })
    stage('finished')


if __name__ == '__main__':
    run(Path(sys.argv[1]))
