"""Verify auxiliary-button size without starting camera, audio or live host."""
import ast
from pathlib import Path
import unittest
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk

tree=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf8'))
node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='large_native_control')
namespace={'Gtk':Gtk}
exec(compile(ast.Module(body=[node],type_ignores=[]),'main-control-helper','exec'),namespace)
large=namespace['large_native_control']


class NativeControlsTests(unittest.TestCase):
    def test_toolbar_fits_960_without_squeezing_buttons(self):
        bar=Gtk.Box(spacing=10);bar.set_border_width(7)
        self.addCleanup(bar.destroy)
        labels=('☰  体验菜单','体感 / 手柄','设备 / Wi-Fi','全屏','显示','退出')
        controls=[]
        for label in labels:
            button=large(Gtk.Button(label=label));bar.pack_start(button,False,False,0);controls.append(button)
        status=Gtk.Label(label='娱乐 · 健康 · 创作');status.set_ellipsize(3)
        bar.pack_start(status,True,True,0);bar.show_all()
        for button in controls:
            self.assertGreaterEqual(button.get_preferred_height()[0],48)
        self.assertLessEqual(bar.get_preferred_width()[0],960)

    def test_error_buttons_are_readable_targets(self):
        for label in ('重试','返回主菜单'):
            button=large(Gtk.Button(label=label),200);self.addCleanup(button.destroy);button.show()
            self.assertGreaterEqual(button.get_preferred_width()[0],200)
            self.assertGreaterEqual(button.get_preferred_height()[0],48)

if __name__=='__main__':unittest.main()
