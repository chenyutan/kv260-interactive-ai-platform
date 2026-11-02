"""Native visible button geometry; synthetic pages, no camera or service IO."""
import tempfile,time,unittest
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,GdkPixbuf
from photo_studio import PhotoStudio
from photo_navigation import PhotoFileChooser
from pose_view import PoseView
from connectivity.view import ConnectionCenter


def drain():
    until=time.monotonic()+.12
    while time.monotonic()<until:
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        time.sleep(.004)


class LargeButtonTests(unittest.TestCase):
    def assert_inside(self,control,client):
        """Compare visible content coordinates, excluding CSD frame/shadow."""
        self.assertTrue(control.get_mapped())
        point=control.translate_coordinates(client,0,0)
        self.assertIsNotNone(point)
        x,y=point
        width,height=control.get_allocated_width(),control.get_allocated_height()
        self.assertGreater(width,0);self.assertGreater(height,0)
        self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
        self.assertLessEqual(x+width,client.get_allocated_width())
        self.assertLessEqual(y+height,client.get_allocated_height())

    def show(self,view,width=960,height=540):
        window=Gtk.Window();window.set_decorated(False);window.resize(width,height)
        window.add(view);window.show_all();drain()
        self.addCleanup(window.destroy)
        self.assertEqual((window.get_allocated_width(),window.get_allocated_height()),(width,height))
        return window

    def test_pose_hand_target_matches_the_real_button_after_resize(self):
        view=PoseView('http://invalid.example',lambda:None,enabled=False)
        window=self.show(view)
        for width,height in ((960,540),(1280,720)):
            window.resize(width,height);drain()
            button=view.return_button
            x,y=button.translate_coordinates(view,0,0)
            bw,bh=button.get_allocated_width(),button.get_allocated_height()
            vw,vh=view.get_allocated_width(),view.get_allocated_height()
            self.assertGreaterEqual(bh,48)
            self.assertGreaterEqual(bw,200)
            self.assertEqual(view.rectangles,((x/vw,y/vh,(x+bw)/vw,(y+bh)/vh),))
            self.assertIsNone(view.stop_event,'Layout checks must not open preview')

    def test_photo_controls_fit_540_with_actual_qr_and_long_status(self):
        view=PhotoStudio(lambda:None)
        self.addCleanup(view.close)
        view.network.set_text('手机与板卡连接同一个 Wi-Fi。\n扫码后在“照片水墨”上传。')
        qr=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,112,112)
        qr.fill(0x203f39ff);view.qr.set_from_pixbuf(qr)
        view.status.set_text('照片已选择，作品正在制作；可以返回创作，稍后再来查看。'*2)
        self.show(view)
        for control in view.actions:
            x,y=control.translate_coordinates(view,0,0)
            width,height=control.get_allocated_width(),control.get_allocated_height()
            self.assertGreaterEqual(height,48)
            self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+width,960);self.assertLessEqual(y+height,540)
        self.assertGreater(view.canvas.get_allocated_height(),180)

    def test_file_chooser_large_controls_keep_scroll_and_confirm_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            view=PhotoFileChooser(initial=folder)
            self.addCleanup(view.destroy);drain()
            # The production dialog is already mapped by its constructor.
            # Removing decorations after map makes GTK reinterpret resize()
            # against the old 26px-per-side CSD frame on some desktops.
            width,height=view.get_size()
            self.assertLessEqual(width,760);self.assertLessEqual(height,440)
            client=view.get_child()
            self.assertGreater(client.get_allocated_width(),0)
            self.assertGreater(client.get_allocated_height(),0)
            self.assertLessEqual(client.get_allocated_width(),760)
            self.assertLessEqual(client.get_allocated_height(),440)
            for control in (view.up,view.location,view.cancel,view.accept):
                self.assertGreaterEqual(control.get_allocated_height(),48)
                self.assert_inside(control,client)
            scroll=next(w for w in view.get_content_area().get_children()
                        if isinstance(w,Gtk.ScrolledWindow))
            self.assertGreaterEqual(scroll.get_allocated_height(),100)
            self.assert_inside(scroll,client)

    def test_connection_primary_buttons_remain_large_with_scrollable_keyboard(self):
        parent=Gtk.Window();parent.resize(960,540);parent.show_all();self.addCleanup(parent.destroy)
        view=ConnectionCenter(parent,lambda *_args,**_kwargs:None,lambda *_:None,lambda:None)
        self.addCleanup(view.destroy);view.open();drain()
        for button in view.buttons:
            if button.get_mapped() and button not in view.key_buttons:
                self.assertGreaterEqual(button.get_allocated_height(),48)
        view.tabs.set_current_page(1);view.toggle_keyboard();drain()
        # Gtk.Window widget allocation can include client-side shadow (52px
        # on Adwaita), whereas get_size() is the requested client dimensions.
        width,height=view.get_size()
        self.assertLessEqual(width,940);self.assertLessEqual(height,510)
        client=view.get_child()
        self.assertLessEqual(client.get_allocated_width(),940)
        self.assertLessEqual(client.get_allocated_height(),510)
        self.assert_inside(view.back,client)
        self.assertIsInstance(view.tabs.get_nth_page(1),Gtk.ScrolledWindow)
        viewport=view.tabs.get_nth_page(1).get_child()
        self.assert_inside(viewport,client)
        for row in view.keyboard.get_children():
            for button in row.get_children():
                self.assert_inside(button,viewport)


if __name__=='__main__':unittest.main()
