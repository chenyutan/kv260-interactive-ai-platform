"""HD photo publication tests with generated pixels and a loopback-only portal.

The renderer is a fixture; these checks prove export/storage/recovery contracts,
not neural quality or KV260 runtime performance.
"""
import errno
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from . import test_photo_service as fixtures
from .portal import Portal


class PhotoClarityTests(unittest.TestCase):
    # Reuse the established service/HTTP fixtures without inheriting and running
    # all their test methods again under a second class.
    wait_job = fixtures.ServiceCase.wait_job
    request = fixtures.HTTPCase.request

    def setUp(self):
        fixtures.ServiceCase.setUp(self)
        self.portal = Portal(self.store, host='127.0.0.1', port=0,
                             public_url='http://localhost', photos=self.service).start()
        self.base = 'http://127.0.0.1:' + str(self.portal.port)
        from .account_test_support import login_fixture
        self.cookie=login_fixture(self.portal,self.session['token'])

    def tearDown(self):
        self.portal.close()
        fixtures.ServiceCase.tearDown(self)

    def wait_released(self, job):
        done = self.wait_job(job, timeout=8)
        self.service._thread.join(2)
        self.assertFalse(self.service._thread.is_alive())
        self.assertFalse(self.service._busy)
        return done

    def seed_old_artwork(self):
        done = self.wait_released(self.service.submit_bytes(
            self.session['token'], fixtures.photograph()))
        return done, self.store.artwork(self.session['token'], done['art_id'])[1]

    def assert_old_artwork_preserved(self, old, png):
        token = self.session['token']
        self.assertEqual(self.store.artwork(token, old['art_id'])[1], png)
        self.assertEqual(self.service.read_image(token, old['id'], 'result'), png)

    def assert_can_process_again(self):
        done = self.wait_released(self.service.submit_bytes(
            self.session['token'], fixtures.photograph(color=(71, 149, 88))))
        self.assertEqual(done['status'], 'completed')

    def test_hd_png_is_identical_in_service_artwork_http_and_bundle(self):
        # Fine synthetic color variation detects downsample-and-upscale paths
        # which could otherwise keep a 1920x1080 header but lose source pixels.
        y, x = np.mgrid[:1080, :1920]
        rgb = np.stack((x % 251, y % 241, (x + 2*y) % 239), axis=-1).astype(np.uint8)
        upload = io.BytesIO()
        Image.fromarray(rgb).save(upload, format='PNG')
        token = self.session['token']
        endpoint = '/api/visitor/' + token + '/photos'
        with self.request(endpoint, upload.getvalue(),
                          {'X-Ink-Visitor': '1', 'Content-Type': 'image/png'}) as response:
            self.assertEqual(response.status, 202)
            job = json.load(response)['job']
        done = self.wait_released(job)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual((done['width'], done['height']), (1920, 1080))
        png = self.service.read_image(token, done['id'], 'result')
        expected = rgb.copy()
        expected[:, :, 0] = 203  # The established fixture renderer's only change.
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(image.mode, 'RGB')
            np.testing.assert_array_equal(np.asarray(image), expected)
        self.assertEqual(self.store.artwork(token, done['art_id'])[1], png)
        for path in (done['result_url'] + '?download=1',
                     '/v/' + token + '/art/' + done['art_id'] + '.png'):
            with self.subTest(path=path), self.request(path) as response:
                self.assertEqual(response.headers['Content-Type'], 'image/png')
                self.assertIn('attachment', response.headers['Content-Disposition'])
                self.assertEqual(int(response.headers['Content-Length']), len(png))
                self.assertEqual(response.read(), png)
        with self.request('/v/' + token + '/report.zip') as response:
            self.assertEqual(response.headers['Content-Type'], 'application/zip')
            bundle = response.read()
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('artworks/' + done['art_id'] + '.png'), png)

    def test_source_dimensions_remain_separate_from_export_dimensions(self):
        def smaller_export(rgb, settings, cancelled, progress):
            return np.full((720, 1280, 3), (121, 181, 81), np.uint8), {
                'source_width': 1920, 'source_height': 1080,
                'inference_width': 960, 'inference_height': 536,
                'output_width': 1280, 'output_height': 720,
            }
        self.service._renderer = smaller_export
        token = self.session['token']
        done = self.wait_released(self.service.submit_bytes(
            token, fixtures.photograph(size=(2560, 1440))))
        self.assertEqual(done['status'], 'completed')
        # Existing public width/height describe the normalized source, not the
        # original phone file, neural tensor, or downloaded export.
        self.assertEqual((done['width'], done['height']), (1920, 1080))
        for kind, expected in (('source', (1920, 1080)), ('result', (1280, 720))):
            with Image.open(io.BytesIO(self.service.read_image(token, done['id'], kind))) as image:
                self.assertEqual(image.size, expected)
        with self.store.connect() as db:
            row = db.execute('SELECT details FROM photo_jobs WHERE id=?', (done['id'],)).fetchone()
        details = json.loads(row['details'])
        self.assertEqual((details['inference_width'], details['inference_height']), (960, 536))
        self.assertEqual((details['output_width'], details['output_height']), (1280, 720))

    def test_1921_pixel_result_is_rejected_and_old_artwork_survives(self):
        old, png = self.seed_old_artwork()
        self.service._renderer = lambda *args: (np.zeros((1080, 1921, 3), np.uint8), {})
        failed = self.wait_released(self.service.submit_bytes(
            self.session['token'], fixtures.photograph()))
        self.assertEqual(failed['status'], 'failed')
        self.assertIsNone(failed['art_id'])
        self.assertIsNone(failed['result_url'])
        with self.assertRaises(KeyError):
            self.service.read_image(self.session['token'], failed['id'], 'result')
        self.assertEqual(len(self.store.view(self.session['token'])['artworks']), 1)
        self.assert_old_artwork_preserved(old, png)
        self.service._renderer = fixtures.render
        self.assert_can_process_again()

    def _disk_full_open(self, target):
        original_open = Path.open
        injected = []
        class DiskFullStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, data):
                self.stream.write(data[:16])
                raise OSError(errno.ENOSPC, 'No space left on device')
        def open_with_full_disk(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if target(path):
                injected.append(path)
                return DiskFullStream(stream)
            return stream
        return patch.object(Path, 'open', open_with_full_disk), injected

    def test_disk_full_during_source_write_releases_reserved_slot(self):
        old, png = self.seed_old_artwork()
        folders_before = set(self.service.root.rglob('source.png'))
        fault, injected = self._disk_full_open(lambda path: path.name == 'source.png.tmp')
        with fault, self.assertRaises(OSError) as error:
            self.service.submit_bytes(self.session['token'], fixtures.photograph())
        self.assertEqual(error.exception.errno, errno.ENOSPC)
        self.assertEqual(len(injected), 1)
        self.assertFalse(self.service._busy)
        self.assertEqual(set(self.service.root.rglob('source.png')), folders_before)
        self.assertEqual(list(self.service.root.rglob('*.tmp')), [])
        self.assert_old_artwork_preserved(old, png)
        self.assert_can_process_again()

    def test_disk_full_during_result_write_does_not_publish_partial_png(self):
        self._check_async_disk_failure(lambda path: path.name == 'result.png.tmp')

    def test_disk_full_during_artwork_copy_does_not_publish_or_replace_old_art(self):
        self._check_async_disk_failure(
            lambda path: path.parent == self.store.art_root and path.name.endswith('.png.tmp'))

    def _check_async_disk_failure(self, target):
        old, png = self.seed_old_artwork()
        fault, injected = self._disk_full_open(target)
        with fault:
            failed = self.wait_released(self.service.submit_bytes(
                self.session['token'], fixtures.photograph(color=(71, 149, 88))))
        self.assertEqual(len(injected), 1)
        self.assertEqual(failed['status'], 'failed')
        self.assertIsNone(failed['art_id'])
        self.assertIsNone(failed['result_url'])
        with self.assertRaises(KeyError):
            self.service.read_image(self.session['token'], failed['id'], 'result')
        self.assertEqual(len(self.store.view(self.session['token'])['artworks']), 1)
        self.assertEqual(list(self.store.root.rglob('*.tmp')), [])
        self.assert_old_artwork_preserved(old, png)
        self.assert_can_process_again()


if __name__ == '__main__':
    unittest.main()
