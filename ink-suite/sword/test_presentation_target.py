"""Exercise the production target selection without importing GTK on Windows.

Native Cairo pixel and draw-integration checks live in test_main.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class ImageSurface:
    def __init__(self,fmt):self.fmt=fmt
    def get_format(self):return self.fmt


tree=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8'))
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='target_uses_rgb565')
namespace={'cairo':SimpleNamespace(ImageSurface=ImageSurface,FORMAT_RGB16_565=4)}
exec(compile(ast.Module(body=[function],type_ignores=[]),'main.py::target_uses_rgb565','exec'),namespace)
select=namespace['target_uses_rgb565']


class PresentationTargetTests(unittest.TestCase):
    def test_rgb24_and_argb32_targets_preserve_color_on_16bit_window(self):
        for fmt in (0,1):self.assertFalse(select(ImageSurface(fmt),True))

    def test_actual_565_image_wins_over_a_24bit_window_visual(self):
        self.assertTrue(select(ImageSurface(4),False))

    def test_other_image_formats_are_not_misidentified_as_565(self):
        for fmt in (2,3,5):self.assertFalse(select(ImageSurface(fmt),True))

    def test_native_nonimage_target_retains_exact_visual_rule(self):
        for target in (object(),None):
            self.assertTrue(select(target,True))
            self.assertFalse(select(target,False))


if __name__=='__main__':unittest.main()
