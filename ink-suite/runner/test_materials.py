"""Atlas validation and context lifetime; no camera, audio, X11 or GPU."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

from runner.native_render import Renderer, load_material_atlas
from runner.test_native_render import FakeHud, NativeFunction, NativeLibrary


def png(width=512, height=512, pixel=(255,255,255,255)):
    def chunk(tag, data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    rows=(b''.join(b'\0'+bytes(pixel(y))*width for y in range(height)) if callable(pixel)
          else (b'\0'+bytes(pixel)*width)*height)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+
            chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b''))


class MaterialTests(unittest.TestCase):
    def make(self, loader=None, library=None):
        self.library=library or NativeLibrary()
        self.fallback=SimpleNamespace(**{n:Mock() for n in (
            'release_native_output','render','configure_depth','warm','event','update_effects')},
            metrics=Mock(return_value={'presentation':'cairo'}))
        self.loader=loader or Mock(return_value=dict(pixels=bytearray([255]*4),width=1,height=1,stride=4,
                                                     sha256='white-test-fixture'))
        return Renderer(library=self.library,material_loader=self.loader,
                        fallback_factory=lambda:self.fallback,hud_factory=FakeHud)

    def test_old_library_missing_material_capability_is_rejected_before_create(self):
        lib=NativeLibrary();del lib.rv_material_api_version
        r=self.make(library=lib)
        self.assertFalse(r.attach_window(10,960,540))
        self.assertEqual(lib.rv_create.calls,[])
        self.assertIn('rv_material_api_version',r.metrics()['fallback_reason'])

    def test_wrong_material_capability_is_rejected(self):
        lib=NativeLibrary();lib.rv_material_api_version=NativeFunction(lambda:2)
        r=self.make(library=lib)
        self.assertFalse(r.attach_window(10,960,540))
        self.assertEqual(lib.rv_create.calls,[])

    def test_decode_and_upload_once_during_many_frames_and_resize(self):
        r=self.make();self.assertTrue(r.attach_window(10,960,540))
        for _ in range(60):
            self.assertTrue(r.render(None,dict(state='running'),960,540))
        self.assertTrue(r.render(None,dict(state='running'),1280,720))
        self.assertEqual(self.loader.call_count,1)
        self.assertEqual(len(self.library.rv_set_material_atlas.calls),1)
        m=r.metrics()
        self.assertTrue(m['material_loaded']);self.assertEqual(m['material_decodes'],1)
        self.assertEqual(m['material_uploads'],1);self.assertEqual(m['material_bytes_uploaded'],4)
        self.assertEqual(m['material_sha256'],'white-test-fixture')
        self.assertEqual(len(self.library.rv_set_hud.calls),1)

    def test_context_recreation_reuploads_same_cached_bytes_without_decode(self):
        r=self.make();self.assertTrue(r.attach_window(10,960,540))
        r.release_native_output();self.assertFalse(r.material_loaded)
        self.assertTrue(r.attach_window(11,960,540))
        self.assertEqual(self.loader.call_count,1)
        self.assertEqual(len(self.library.rv_set_material_atlas.calls),2)
        self.assertEqual(r.metrics()['material_uploads'],2)
        self.assertEqual(r.context_generation,2)

    def test_failed_native_upload_destroys_owned_context_and_falls_back(self):
        lib=NativeLibrary();lib.rv_set_material_atlas=NativeFunction(lambda *a:0)
        r=self.make(library=lib)
        self.assertFalse(r.attach_window(10,960,540))
        self.assertEqual(len(lib.rv_destroy.calls),1)
        self.assertIn('atlas upload failed',r.metrics()['fallback_reason'])
        self.assertFalse(r.metrics()['material_loaded'])

    def test_missing_material_file_is_explicit_failure_not_ready(self):
        r=self.make(loader=Mock(side_effect=FileNotFoundError('material_atlas.png missing')))
        self.assertFalse(r.attach_window(10,960,540))
        self.assertEqual(len(self.library.rv_destroy.calls),1)
        self.assertFalse(self.library.rv_frame.calls)
        self.assertIn('material_atlas.png missing',r.metrics()['fallback_reason'])

    def test_invalid_decoded_stride_is_rejected_before_native_upload(self):
        r=self.make(loader=Mock(return_value=dict(pixels=bytearray(4),width=512,height=512,stride=4)))
        self.assertFalse(r.attach_window(10,960,540))
        self.assertFalse(self.library.rv_set_material_atlas.calls)
        self.assertEqual(len(self.library.rv_destroy.calls),1)

    def test_invalid_png_header_and_dimensions_are_checked_before_cairo(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.png'
            for data in (b'not a png',png(64,64),png(1024,512)):
                p.write_bytes(data)
                with self.assertRaises(ValueError):load_material_atlas(p)

    @unittest.skipUnless(importlib.util.find_spec('cairo') or importlib.util.find_spec('cairocffi'),
                         'real Cairo PNG decoder unavailable on this host')
    def test_real_png_decode_retains_top_left_rows_and_rejects_transparency(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'atlas.png';p.write_bytes(png())
            atlas=load_material_atlas(p)
            self.assertEqual((atlas['width'],atlas['height'],atlas['stride']),(512,512,2048))
            self.assertEqual(atlas['pixels'],bytearray([255])*(512*512*4))
            p.write_bytes(png(pixel=lambda y:(255,255,255,255) if y<256 else (40,95,210,255)))
            atlas=load_material_atlas(p)
            self.assertEqual(atlas['pixels'][:4],bytearray([255,255,255,255]))
            self.assertEqual(atlas['pixels'][-4:],bytearray([40,95,210,255]))
            p.write_bytes(png(pixel=(253,253,253,255)))
            with self.assertRaisesRegex(ValueError,'white origin'):load_material_atlas(p)
            p.write_bytes(png(pixel=(255,255,255,128)))
            with self.assertRaisesRegex(ValueError,'opaque'):load_material_atlas(p)


if __name__=='__main__':unittest.main()
