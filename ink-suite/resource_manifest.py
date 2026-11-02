"""Pinned offline legacy photo resources and reviewed game art assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import time
import zipfile

MODEL_PATH = 'environment/models/figure-ink-v3.onnx'
MODEL_SHA256 = '7ca0cfd577415b034bc358f775ad9e2d317de344c07f4482945d2be85562d35b'
RUNTIME_PATH = 'environment/runtime'
MANIFEST_NAME = 'resource-manifest.json'
ORT_VERSION = '1.19.2'
ASSET_PATH = 'avatar/assets'
SWORD_ASSET_PATH = 'sword/assets'
REQUIRED_SWORD_ASSETS = (SWORD_ASSET_PATH+'/landscape.png', SWORD_ASSET_PATH+'/manifest.json',
                         SWORD_ASSET_PATH+'/gameplay-atlas.png')
OPTIONAL_SWORD_ASSETS = ()
# Retired code and art never enter a new release. Historical backups/reports
# remain separate and are never covered by this top-level removal list.
REMOVE_PATHS = ('koi',)
REQUIRED_TAIKO_ASSETS = tuple('taiko/assets/'+name for name in
                            ('landscape.png', 'drum.png', 'manifest.json'))
REQUIRED_TAIKO_AUDIO = ('taiko/audio/qinglan-guyin.wav', 'taiko/audio/manifest.json')
REQUIRED_SUITE_AUDIO = ('audio/manifest.json',)+tuple('audio/assets/'+name+'.wav' for name in (
    'bgm_ink','bgm_fruit','bgm_fight','bgm_taiko','bgm_sword',
    'ui_open','ui_select','ui_confirm','ui_back','ink_stroke','ink_save','ink_clear',
    'fruit_slice','fruit_combo','fruit_double','fruit_freeze','fruit_bomb','fruit_miss',
    'fight_swing','fight_hit','fight_kick','fight_block','fight_special','fight_win','fight_lose',
    'taiko_left','taiko_right','taiko_both','taiko_miss','taiko_finish',
    'sword_shot','sword_hit','sword_kill','sword_hurt','sword_collect','sword_special','sword_boss','sword_win','sword_lose'))
REQUIRED_PLATFORMER_AUDIO = ('platformer/audio_assets/bgm_platformer.wav', 'platformer/audio_assets/manifest.json', 'platformer/audio_assets/platformer_checkpoint.wav', 'platformer/audio_assets/platformer_clear.wav', 'platformer/audio_assets/platformer_coin.wav', 'platformer/audio_assets/platformer_die.wav', 'platformer/audio_assets/platformer_hurt.wav', 'platformer/audio_assets/platformer_jump.wav', 'platformer/audio_assets/platformer_pipe.wav', 'platformer/audio_assets/platformer_power.wav', 'platformer/audio_assets/platformer_stomp.wav', 'platformer/audio_assets/platformer_win.wav')
REQUIRED_PLATFORMER_ASSETS = ('platformer/assets/sprite_sheet.png',)
REQUIRED_RUNNER_AUDIO = ('runner/audio_assets/manifest.json', 'runner/audio_assets/bgm_runner.wav', 'runner/audio_assets/sfx_runner_jump.wav', 'runner/audio_assets/sfx_runner_slide.wav', 'runner/audio_assets/sfx_runner_lane.wav', 'runner/audio_assets/sfx_runner_coin.wav', 'runner/audio_assets/sfx_runner_power.wav', 'runner/audio_assets/sfx_runner_hit.wav', 'runner/audio_assets/sfx_runner_over.wav')
PRIVATE_PIXMAN_RESOURCES = ('runtime/pixman-a53/manifest.json', 'runtime/pixman-a53/COPYING', 'runtime/pixman-a53/lib/libpixman-1.so.0')
PAL_RESOURCES = ('pal/libink-pal-control.so',)
REQUIRED_RUNNER_NATIVE = ('runner/native/librunner3d.so',)
REQUIRED_KART_NATIVE = ('kart/native/libracer3d.so',)
REQUIRED_RUNNER_ART = ('runner/assets/material_atlas.png', 'runner/assets/material_atlas.pkm', 'runner/assets/manifest.json')
REQUIRED_GAME_RESOURCES = (REQUIRED_RUNNER_ART + REQUIRED_RUNNER_NATIVE + REQUIRED_KART_NATIVE + PAL_RESOURCES + REQUIRED_RUNNER_AUDIO + PRIVATE_PIXMAN_RESOURCES + REQUIRED_PLATFORMER_AUDIO + REQUIRED_PLATFORMER_ASSETS + REQUIRED_SWORD_ASSETS +
                           REQUIRED_TAIKO_ASSETS + REQUIRED_TAIKO_AUDIO + REQUIRED_SUITE_AUDIO)
ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.svg', '.json'}
WHEELS_MANIFEST_SHA256 = 'ee009433847920b5ca0ecc2d4150940d24e332eabb14671613c8897700203f74'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_path(root, name):
    relative = PurePosixPath(name)
    if (not name or '\\' in name or relative.is_absolute() or
            '..' in relative.parts or ':' in name or relative.as_posix() != name):
        raise RuntimeError('Unsafe resource path: ' + name)
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    if any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)) or not path.resolve().is_relative_to(root):
        raise RuntimeError('Resource symlink escapes or replaces a pinned file: ' + name)
    return path


def is_arm64_elf(path):
    with Path(path).open('rb') as stream:
        header = stream.read(20)
    return header[:6] == b'\x7fELF\x02\x01' and int.from_bytes(header[18:20], 'little') == 183


def load_manifest(root):
    manifest = json.loads((Path(root) / MANIFEST_NAME).read_text(encoding='utf-8'))
    files = manifest.get('files', {})
    if (not isinstance(files, dict) or manifest.get('schema') != 1 or manifest.get('architecture') != 'aarch64' or
            manifest.get('python_abi') != 'cp310' or manifest.get('onnxruntime_version') != ORT_VERSION or
            files.get(MODEL_PATH) != MODEL_SHA256):
        raise RuntimeError('Unexpected resource manifest/model/runtime identity')
    if not any(n.startswith(RUNTIME_PATH + '/onnxruntime/capi/') for n in files):
        raise RuntimeError('Missing isolated ONNX runtime')
    for name, digest in files.items():
        safe_path(root, name)
        if not resource_allowed(name):
            raise RuntimeError('Resource outside explicit whitelist: ' + name)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise RuntimeError('Invalid SHA256: ' + name)
    return manifest


def resource_allowed(name):
    return (name == MODEL_PATH or name.startswith(RUNTIME_PATH + '/') or
            (name.startswith(ASSET_PATH + '/') and PurePosixPath(name).suffix.lower() in ASSET_SUFFIXES) or
            name in REQUIRED_GAME_RESOURCES + OPTIONAL_SWORD_ASSETS)


def resource_files(root):
    """Return the closed deployable resource tree, rejecting links and loose files."""
    root = Path(root).resolve()
    for retired in REMOVE_PATHS:
        if safe_path(root,retired).exists():
            raise RuntimeError('Retired experience remains in release: '+retired)
    result = {}
    for relative in ('environment/models', RUNTIME_PATH, ASSET_PATH, SWORD_ASSET_PATH,
                     'taiko/assets', 'taiko/audio','audio/assets', 'platformer/audio_assets', 'platformer/assets', 'runner/audio_assets', 'runner/assets', 'runtime/pixman-a53'):
        folder = safe_path(root, relative)
        for path in sorted(folder.rglob('*')):
            name = path.relative_to(root).as_posix()
            safe_path(root, name)
            if '__pycache__' in path.parts or path.suffix == '.pyc' or not path.is_file():
                continue
            if not resource_allowed(name):
                raise RuntimeError('Resource outside explicit whitelist: ' + name)
            result[name] = sha(path)
    audio_manifest=safe_path(root,'audio/manifest.json')
    if audio_manifest.is_file():result['audio/manifest.json']=sha(audio_manifest)
    for name in PAL_RESOURCES + REQUIRED_RUNNER_NATIVE + REQUIRED_KART_NATIVE:
        path=safe_path(root,name)
        if path.is_file():result[name]=sha(path)
    return result


def write_manifest(root, provenance=None):
    root = Path(root)
    manifest = dict(schema=1, architecture='aarch64', python_abi='cp310',
                    onnxruntime_version=ORT_VERSION, files=resource_files(root))
    if provenance is not None:
        manifest['provenance'] = provenance
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return verify_resources(root)


def prepare_resources(source_root, destination):
    """Unpack hash-pinned ARM wheels offline; never install into host Python."""
    source_root, destination = Path(source_root), Path(destination)
    if destination.exists():
        raise RuntimeError('Resource destination must be new: ' + str(destination))
    model = source_root / 'environment/qa-style-finetune-v5/runs/figure-v3-balanced-300/fixed-style.onnx'
    wheels = source_root / 'environment/qa-neural-v4/arm-wheels'
    wheel_manifest = wheels / 'manifest.json'
    if sha(model) != MODEL_SHA256 or sha(wheel_manifest) != WHEELS_MANIFEST_SHA256:
        raise RuntimeError('Model or wheel provenance hash mismatch')
    packages = json.loads(wheel_manifest.read_text(encoding='utf-8'))
    for package in packages:
        path = safe_path(wheels, package['file'])
        if path.stat().st_size != package['bytes'] or sha(path) != package['sha256']:
            raise RuntimeError('Wheel hash mismatch: ' + package['file'])
    destination.mkdir(parents=True)
    model_target = safe_path(destination, MODEL_PATH)
    model_target.parent.mkdir(parents=True)
    shutil.copy2(model, model_target)
    for package in packages:
        with zipfile.ZipFile(wheels / package['file']) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError('Wheel contains a symlink: ' + entry.filename)
                target = safe_path(destination, RUNTIME_PATH + '/' + entry.filename)
                if target.exists():
                    raise RuntimeError('Duplicate wheel resource: ' + entry.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as incoming, target.open('wb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
    return write_manifest(destination, dict(model_source=str(model), wheels=packages,
        wheels_manifest_sha256=WHEELS_MANIFEST_SHA256, global_python_modified=False))


def verify_resources(root):
    root = Path(root)
    manifest = load_manifest(root)
    files = manifest['files']
    for name, digest in files.items():
        path = safe_path(root, name)
        if not path.is_file() or sha(path) != digest:
            raise RuntimeError('Resource hash mismatch: ' + name)
        if '.so' in path.name and not is_arm64_elf(path):
            raise RuntimeError('Non-AArch64 shared library: ' + name)
    # Runtime is a closed, pinned tree. Python cache files are generated locally,
    # not deployable resources, and do not change package code/identity.
    if set(resource_files(root)) != set(files):
        raise RuntimeError('Resource tree differs from pinned file whitelist')
    return manifest


def smoke_runtime(root):
    """Import real ARM runtime and run a tiny tensor, not a 50-second photo."""
    if platform.machine() != 'aarch64' or sys.version_info[:2] != (3, 10):
        raise RuntimeError('Runtime smoke requires target AArch64 Python 3.10')
    root = Path(root).resolve()
    runtime = root / RUNTIME_PATH
    sys.path.insert(0, str(runtime))
    started = time.monotonic()
    import numpy as np
    import onnxruntime as ort
    if ort.__version__ != ORT_VERSION or not Path(ort.__file__).resolve().is_relative_to(runtime):
        raise RuntimeError('ONNX runtime resolved outside isolated pinned directory')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    options.add_session_config_entry('session.inter_op.allow_spinning', '0')
    session = ort.InferenceSession(str(root / MODEL_PATH), options, providers=['CPUExecutionProvider'])
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].type != 'tensor(float)':
        raise RuntimeError('Unexpected model input contract')
    output = session.run(None, {inputs[0].name: np.full((1, 3, 32, 32), .5, np.float32)})
    if len(output) != 1 or output[0].shape != (1, 3, 32, 32) or not np.isfinite(output[0]).all():
        raise RuntimeError('Model tiny-tensor execution failed')
    return dict(onnxruntime=ort.__version__, runtime_module=ort.__file__,
                numpy=np.__version__, architecture=platform.machine(),
                model_sha256=sha(root / MODEL_PATH), input_shape=[1, 3, 32, 32],
                output_shape=list(output[0].shape), elapsed_seconds=time.monotonic() - started,
                camera_used=False, full_photo_inference=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    manifest = verify_resources(args.root)
    result = dict(resources_verified=True, files=len(manifest['files']), camera_used=False)
    if args.smoke:
        result['runtime_smoke'] = smoke_runtime(args.root)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
