"""Cancellable offline renderer subprocess used by the native photo panel.

The UI's worker thread calls render(); pause/retake sets its cancellation event.
Only one render can exist per manager, and cancelled output is never delivered.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np


class RenderCancelled(Exception):
    pass


class PhotoJobs:
    def __init__(self, worker=None, python=None, runtime=None, scratch=None):
        self.worker = Path(worker or Path(__file__).with_name('onnx_photo_worker.py'))
        self.python = str(python or sys.executable)
        self.runtime = runtime
        self.scratch = scratch
        self._slot = threading.Lock()

    def validate_resources(self, settings):
        """Fail before taking a photo; never substitute the legacy renderer."""
        if not self.worker.is_file():
            raise FileNotFoundError('照片入墨工作程序缺失：' + str(self.worker))
        model = Path(settings['model'])
        if not model.is_file():
            raise FileNotFoundError('照片入墨模型缺失：' + str(model))
        if self.runtime is not None:
            runtime = Path(self.runtime)
            if not (runtime / 'onnxruntime' / '__init__.py').is_file():
                raise FileNotFoundError('照片入墨独立运行库缺失：' + str(runtime))

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()

    def render(self, rgb, settings, cancelled, progress=None, timeout=120):
        if not self._slot.acquire(blocking=False):
            raise RuntimeError('A photo is already being rendered')
        process = None
        started = time.monotonic()
        try:
            if cancelled.is_set():
                raise RenderCancelled()
            if (rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3
                    or min(rgb.shape[:2]) < 16 or max(rgb.shape[:2]) > 4096):
                raise ValueError('Expected a bounded RGB uint8 photograph')
            with tempfile.TemporaryDirectory(prefix='ink-photo-', dir=self.scratch) as folder:
                job = Path(folder)
                np.save(str(job / 'input.npy'), rgb, allow_pickle=False)
                (job / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
                environment = os.environ.copy()
                if self.runtime:
                    environment['PYTHONPATH'] = str(self.runtime)
                environment['PYTHONNOUSERSITE'] = '1'
                environment['OMP_NUM_THREADS'] = '2'
                environment['OPENBLAS_NUM_THREADS'] = '2'
                # The child owns all inference threads; killing it also releases ORT.
                with (job / 'worker.log').open('wb') as log:
                    process = subprocess.Popen(
                        [self.python, str(self.worker), str(job)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        env=environment,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    try:
                        last_status = None
                        while process.poll() is None:
                            if cancelled.wait(.05):
                                raise RenderCancelled()
                            if time.monotonic() - started > timeout:
                                raise TimeoutError('Photo rendering exceeded the allowed time')
                            status_path = job / 'status.json'
                            if progress and status_path.is_file():
                                try:
                                    status = json.loads(status_path.read_text(encoding='utf-8'))
                                except (ValueError, FileNotFoundError):
                                    continue
                                if status != last_status:
                                    progress(status)
                                    last_status = status
                        if cancelled.is_set():
                            raise RenderCancelled()
                    finally:
                        # Reap before closing logs/removing the temporary directory.
                        self._stop(process)
                    if process.returncode:
                        detail = (job / 'worker.log').read_text(encoding='utf-8', errors='replace')[-2000:]
                        raise RuntimeError('Photo worker failed: ' + detail)
                    output = np.load(str(job / 'output.npy'), allow_pickle=False)
                    metadata = json.loads((job / 'result.json').read_text(encoding='utf-8'))
                    if output.dtype != np.uint8 or output.ndim != 3 or output.shape[2] != 3:
                        raise ValueError('Invalid rendered photograph')
                    if cancelled.is_set():
                        raise RenderCancelled()
                    metadata['worker_wall_s'] = time.monotonic() - started
                    output.setflags(write=False)
                    return output, metadata
                # Temporary source/result files are removed on success and failure.
        finally:
            if process is not None:
                self._stop(process)
            self._slot.release()
