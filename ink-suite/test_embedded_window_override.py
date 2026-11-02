"""Execute the real canonical UI loader without importing GTK or a camera."""
import ast
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


class WindowOverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.suite=self.root/'suite';self.suite.mkdir();(self.suite/'native_windows').mkdir()
        self.original=self.root/'original'/'main.py';self.original.parent.mkdir()
        self.original.write_text('MARKER="original"\n',encoding='utf8')
        self.override=self.suite/'native_windows'/'fight.py'
        self.override.write_text('from pathlib import Path\nMARKER="updated"\nASSET_ROOT=Path(__file__).parent\n',encoding='utf8')
        tree=ast.parse(Path(__file__).with_name('embedded_runner.py').read_text(encoding='utf8'))
        fn=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='load_window_module')
        self.ns=dict(importlib=importlib,Path=Path,sys=sys,
                     __file__=str(self.suite/'embedded_runner.py'),
                     NATIVE_WINDOW_SOURCES={'fight':self.original.resolve()})
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),
                     'embedded_runner.py','exec'),self.ns)
        self.load=self.ns['load_window_module'];self.path_before=list(sys.path)

    def tearDown(self):
        sys.path[:]=self.path_before;self.temp.cleanup()

    def test_canonical_override_preserves_original_file_and_asset_directory(self):
        before=self.original.read_bytes();module=self.load(self.original,'fight')
        self.assertEqual(module.MARKER,'updated')
        self.assertEqual(Path(module.__file__),self.original.resolve())
        self.assertEqual(module.ASSET_ROOT,self.original.parent.resolve())
        self.assertEqual(self.original.read_bytes(),before)
        self.assertEqual(sys.path[0],str(self.original.parent.resolve()))

    def test_explicit_custom_source_is_not_replaced(self):
        custom=self.root/'custom.py';custom.write_text('MARKER="custom"\n',encoding='utf8')
        self.assertEqual(self.load(custom,'fight').MARKER,'custom')

    def test_without_packaged_override_original_source_is_used(self):
        self.override.unlink()
        self.assertEqual(self.load(self.original,'fight').MARKER,'original')

    def test_missing_original_does_not_authorize_packaged_override(self):
        self.original.unlink()
        with self.assertRaisesRegex(RuntimeError,'原体验源文件不存在'):
            self.load(self.original,'fight')

    def test_other_mode_does_not_use_matching_named_override(self):
        (self.suite/'native_windows'/'ink.py').write_text('MARKER="wrong"\n',encoding='utf8')
        self.assertEqual(self.load(self.original,'ink').MARKER,'original')


if __name__=='__main__':unittest.main()
