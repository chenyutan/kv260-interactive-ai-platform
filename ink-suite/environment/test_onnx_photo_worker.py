"""Offline worker contract: fake ORT pixels, real geometry and ink finishing."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from ink_finish import finish_ink, VERSION
from onnx_photo_worker import run


class WorkerContractTests(unittest.TestCase):
    def test_fixed_cpu_inference_finishes_and_records_native_resolution(self):
        captured = {}

        class Options:
            def add_session_config_entry(self, name, value):
                captured[name] = value

        class Session:
            def __init__(self, model, sess_options, providers):
                captured.update(options=sess_options, providers=providers)

            def get_inputs(self):
                return [SimpleNamespace(name='content')]

            def get_providers(self):
                return captured['providers']

            def run(self, _, inputs):
                captured['shape'] = inputs['content'].shape
                return [np.full_like(inputs['content'], .75)]

        fake_ort = SimpleNamespace(SessionOptions=Options, InferenceSession=Session,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'), __version__='test-ort')
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            model = folder / 'model.onnx'; model.write_bytes(b'protocol fixture')
            settings = {'model': str(model), 'long_side': 640}
            (folder / 'settings.json').write_text(json.dumps(settings))
            source = np.full((1080, 1920, 3), (90, 170, 100), np.uint8)
            np.save(str(folder / 'input.npy'), source, allow_pickle=False)
            with patch.dict('sys.modules', {'onnxruntime': fake_ort}):
                run(folder)
            output = np.load(str(folder / 'output.npy'), allow_pickle=False)
            expected = finish_ink(source, np.full((360, 640, 3), 191, np.uint8))
            np.testing.assert_array_equal(output, expected)
            record = json.loads((folder / 'result.json').read_text())
            self.assertEqual(record['finish_version'], VERSION)
            self.assertEqual(record['model_sha256'], hashlib.sha256(model.read_bytes()).hexdigest())
            self.assertEqual((record['source_width'], record['source_height']), (1920, 1080))
            self.assertEqual((record['inference_width'], record['inference_height']), (640, 360))
            self.assertEqual(captured['shape'], (1, 3, 360, 640))
            self.assertEqual(captured['options'].intra_op_num_threads, 2)
            self.assertEqual(captured['options'].inter_op_num_threads, 1)
            self.assertEqual(record['providers'], ['CPUExecutionProvider'])
            self.assertFalse(record['network_inference'])
            self.assertFalse(record['camera_opened_by_worker'])
            self.assertEqual(json.loads((folder / 'status.json').read_text())['stage'], 'finished')

    def test_other_resolution_is_rejected_before_runtime_load(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); model = folder / 'model.onnx'; model.write_bytes(b'fixture')
            (folder / 'settings.json').write_text(json.dumps({'model': str(model), 'long_side': 512}))
            with self.assertRaisesRegex(ValueError, '640'):
                run(folder)


if __name__ == '__main__':
    unittest.main()
