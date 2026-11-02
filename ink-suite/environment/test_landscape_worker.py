"""Imported-photo safety and pixel contracts; real ORT smoke tests live in QA."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

try:
    from .landscape_finish import finish_landscape
    from . import landscape_worker as worker
except ImportError:
    from landscape_finish import finish_landscape
    import landscape_worker as worker


class LandscapeFinishTests(unittest.TestCase):
    def setUp(self):
        y, x = np.mgrid[:240, :360]
        self.source = np.stack((70+x*.25, 120+y*.25,
                                90+x*.18), axis=-1).astype(np.uint8)
        gray = np.clip(235-y*.55+20*np.sin(x*.035), 0, 255).astype(np.uint8)
        self.neural = np.repeat(gray[..., None], 3, axis=2)

    def test_styles_are_distinct_and_shape_preserved(self):
        color = finish_landscape(self.source, self.neural, 'color')
        ink = finish_landscape(self.source, self.neural, 'ink')
        self.assertEqual(color.shape, self.source.shape)
        self.assertEqual(color.dtype, np.uint8)
        self.assertGreater(float(np.abs(color.astype(float)-ink).mean()), 3)
        self.assertLess(float(np.abs(ink[..., 0].astype(float)-ink[..., 1]).mean()), 3)

    def test_portrait_aspect_ratio_not_replaced_by_neural_rounding(self):
        result = finish_landscape(self.source.transpose(1, 0, 2),
                                  self.neural[:160, :240].transpose(1, 0, 2))
        self.assertEqual(result.shape, (360, 240, 3))

    def test_default_output_preserves_1920_source_and_1280_remains_supported(self):
        source = np.full((1080, 1920, 3), 200, np.uint8)
        neural = np.full((536, 960, 3), 150, np.uint8)
        self.assertEqual(finish_landscape(source, neural).shape, (1080, 1920, 3))
        self.assertEqual(finish_landscape(source, neural, long_side=1280).shape,
                         (720, 1280, 3))
        self.assertEqual(finish_landscape(source, neural, long_side=640).shape,
                         (360, 640, 3))

    def test_full_resolution_portrait_and_square_export(self):
        for shape, neural_shape in (((1920, 1080, 3), (960, 536, 3)),
                                    ((1920, 1920, 3), (720, 720, 3))):
            with self.subTest(shape=shape):
                result = finish_landscape(np.full(shape, 200, np.uint8),
                                          np.full(neural_shape, 150, np.uint8))
                self.assertEqual(result.shape, shape)
                self.assertEqual(result.dtype, np.uint8)

    def test_finishing_caps_large_source_and_does_not_enlarge_small_source(self):
        neural = np.full((64, 64, 3), 150, np.uint8)
        oversized = np.full((1280, 2560, 3), 200, np.uint8)
        self.assertEqual(finish_landscape(oversized, neural).shape, (960, 1920, 3))
        small = np.full((31, 47, 3), 200, np.uint8)
        self.assertEqual(finish_landscape(small, neural).shape, small.shape)

    def test_white_sky_stays_paper_even_for_saturated_blue_source(self):
        source = np.full((100, 180, 3), (20, 130, 255), np.uint8)
        white = np.full_like(source, 255)
        result = finish_landscape(source, white)
        self.assertGreater(result[..., 0].min(), 245)
        self.assertGreater(result[..., 1].min(), 245)
        self.assertGreater(result[..., 2].min(), 238)

    def test_black_ink_stays_dark(self):
        result = finish_landscape(self.source, np.zeros_like(self.neural))
        self.assertLess(int(result.max()), 15)

    def test_is_deterministic(self):
        np.testing.assert_array_equal(finish_landscape(self.source, self.neural),
                                      finish_landscape(self.source, self.neural))

    def test_bad_styles_and_dtypes_rejected(self):
        with self.assertRaises(ValueError):
            finish_landscape(self.source, self.neural, 'anything')
        with self.assertRaises(ValueError):
            finish_landscape(self.source.astype(float), self.neural)
        with self.assertRaises(ValueError):
            finish_landscape(self.source, self.neural, long_side=4096)
        with self.assertRaises(ValueError):
            finish_landscape(self.source, self.neural, long_side=1921)


class InferenceSizeTests(unittest.TestCase):
    def test_landscape_and_portrait_tensor_sizes(self):
        for source, expected in (((1920, 1080), (960, 536)),
                                 ((1080, 1920), (536, 960)),
                                 ((1920, 1440), (832, 624)),
                                 ((1440, 1920), (624, 832))):
            with self.subTest(source=source):
                self.assertEqual(worker.inference_size(*source), expected)

    def test_square_neural_work_is_bounded_by_area_not_only_long_side(self):
        for source in ((1920, 1920), (1919, 1919), (960, 960)):
            with self.subTest(source=source):
                self.assertEqual(worker.inference_size(*source), (720, 720))
        self.assertEqual(worker.inference_size(960, 720), (832, 624))

    def test_small_and_off_grid_sources_are_not_enlarged(self):
        for source, expected in (((320, 240), (320, 240)),
                                 ((31, 48), (24, 48)),
                                 ((16, 16), (16, 16)),
                                 ((1919, 1079), (960, 536)),
                                 ((1079, 1919), (536, 960)),
                                 ((1919, 32), (960, 16))):
            with self.subTest(source=source):
                size = worker.inference_size(*source)
                self.assertEqual(size, expected)
                self.assertTrue(all(result <= original
                                    for result, original in zip(size, source)))
                self.assertTrue(all(value % 8 == 0 for value in size))

    def test_extreme_panorama_is_rejected_instead_of_enlarged_or_zero_sized(self):
        for source in ((1920, 16), (1920, 31), (31, 1920), (15, 32)):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    worker.inference_size(*source)

    def test_caps_hold_across_nonstandard_aspect_ratios(self):
        for source in ((1783, 1371), (1371, 1783), (1919, 719),
                       (901, 955), (831, 831), (832, 832), (64, 1919)):
            with self.subTest(source=source):
                width, height = worker.inference_size(*source)
                self.assertGreaterEqual(min(width, height), 16)
                self.assertLessEqual(max(width, height), 960)
                self.assertLessEqual(width * height, 524288)
                self.assertEqual(width % 8, 0)
                self.assertEqual(height % 8, 0)
                self.assertLessEqual(width, source[0])
                self.assertLessEqual(height, source[1])


class ImportedWorkerTests(unittest.TestCase):
    def test_npy_header_guard_rejects_bad_dimensions_and_dtype(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'input.npy'
            for array in (np.zeros((3, 50, 3), np.uint8),
                          np.zeros((20, 1921, 3), np.uint8),
                          np.zeros((20, 40, 4), np.uint8),
                          np.zeros((20, 40, 3), float),
                          np.asfortranarray(np.zeros((20, 40, 3), np.uint8))):
                np.save(path, array)
                with self.assertRaises(ValueError):
                    worker.load_input(path)

    def test_safe_input_uses_real_shape_and_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'input.npy'
            array = np.arange(32*48*3, dtype=np.uint8).reshape((32, 48, 3))
            np.save(path, array)
            np.testing.assert_array_equal(worker.load_input(path), array)

    def test_claimed_huge_npy_shape_rejected_without_allocating(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'input.npy'
            with path.open('wb') as stream:
                np.lib.format.write_array_header_1_0(stream,
                    {'descr': '|u1', 'fortran_order': False,
                     'shape': (100000, 100000, 3)})
            with self.assertRaises(ValueError):
                worker.load_input(path)

    def test_failed_retry_removes_stale_success(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'settings.json').write_text('{"style":"bad"}')
            np.save(folder/'output.npy', np.zeros((16, 16, 3), np.uint8))
            (folder/'output.npy.tmp').write_bytes(b'incomplete previous export')
            (folder/'result.json').write_text('{"old": true}')
            with self.assertRaises(ValueError):
                worker.run(folder)
            self.assertFalse((folder/'output.npy').exists())
            self.assertFalse((folder/'output.npy.tmp').exists())
            self.assertFalse((folder/'result.json').exists())

    def test_too_narrow_photo_fails_before_loading_runtime_or_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            np.save(folder/'input.npy', np.zeros((31, 1920, 3), np.uint8))
            (folder/'settings.json').write_text('{}')
            with patch.dict('sys.modules', {'onnxruntime': None}):
                with self.assertRaisesRegex(ValueError, 'too narrow'):
                    worker.run(folder)
            self.assertFalse((folder/'output.npy').exists())
            self.assertFalse((folder/'result.json').exists())

    def test_bad_settings_rejected_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for data in ('[]', '{"style":"other"}', ' '*16385):
                (folder/'settings.json').write_text(data)
                with patch.dict('sys.modules', {'onnxruntime': None}):
                    with self.assertRaises(ValueError):
                        worker.run(folder)

    def test_worker_cpu_contract_records_export_vs_neural_size(self):
        captured = {}
        class Options:
            def add_session_config_entry(self, key, value):
                captured[key] = value
        class Session:
            def __init__(self, model, sess_options, providers):
                captured.update(model=model, options=sess_options, providers=providers)
            def get_inputs(self):
                return [SimpleNamespace(name='content')]
            def get_providers(self):
                return captured['providers']
            def run(self, _, inputs):
                captured['shape'] = inputs['content'].shape
                return [np.full_like(inputs['content'], .72)]
        fake = SimpleNamespace(SessionOptions=Options, InferenceSession=Session,
             ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'), __version__='test')
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = np.full((1080, 1920, 3), (70, 150, 90), np.uint8)
            np.save(folder/'input.npy', source)
            (folder/'settings.json').write_text('{"style":"color"}')
            with patch.dict('sys.modules', {'onnxruntime': fake}):
                worker.run(folder)
            record = json.loads((folder/'result.json').read_text())
            self.assertEqual(captured['shape'], (1, 3, 536, 960))
            self.assertEqual(captured['options'].intra_op_num_threads, 2)
            self.assertEqual(captured['options'].inter_op_num_threads, 1)
            self.assertEqual(captured['session.intra_op.allow_spinning'], '0')
            self.assertEqual(captured['session.inter_op.allow_spinning'], '0')
            self.assertEqual(record['providers'], ['CPUExecutionProvider'])
            self.assertFalse(record['network_inference'])
            self.assertFalse(record['camera_opened_by_worker'])
            self.assertFalse(record['neural_super_resolution'])
            self.assertEqual(record['renderer'], 'landscape-ink-v2')
            self.assertEqual(record['quality_profile'], 'clarity-960-area524288')
            self.assertEqual(record['inference_pixel_budget'], 524288)
            self.assertEqual((record['source_width'], record['source_height']), (1920, 1080))
            self.assertEqual((record['output_width'], record['output_height']), (1920, 1080))
            self.assertEqual((record['inference_width'], record['inference_height']), (960, 536))
            self.assertEqual(np.load(folder/'output.npy').shape, (1080, 1920, 3))
            self.assertEqual(json.loads((folder/'status.json').read_text())['stage'], 'finished')
            self.assertFalse((folder/'output.npy.tmp').exists())

    def test_nonfinite_neural_result_is_not_published(self):
        class Options:
            def add_session_config_entry(self, *args):
                pass
        class Session:
            def __init__(self, *args, **kwargs):
                pass
            def get_inputs(self):
                return [SimpleNamespace(name='content')]
            def run(self, _, inputs):
                return [np.full_like(inputs['content'], np.nan)]
        fake = SimpleNamespace(SessionOptions=Options, InferenceSession=Session,
                               ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            np.save(folder/'input.npy', np.zeros((32, 48, 3), np.uint8))
            (folder/'settings.json').write_text('{}')
            with patch.dict('sys.modules', {'onnxruntime': fake}):
                with self.assertRaisesRegex(ValueError, 'invalid image'):
                    worker.run(folder)
            self.assertFalse((folder/'output.npy').exists())
            self.assertFalse((folder/'result.json').exists())


if __name__ == '__main__':
    unittest.main()
