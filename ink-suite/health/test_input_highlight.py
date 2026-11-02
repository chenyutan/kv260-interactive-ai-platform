"""Reproduce pad-to-hand transitions on the real GTK practice view."""
import unittest
from unittest.mock import patch
import cairo
from .view import HealthView


class InputHighlightTests(unittest.TestCase):
    def setUp(self):
        self.view=HealthView(lambda:None,save_reports=False,video_enabled=False)
        self.view.enter()
        self.addCleanup(self.view.destroy)

    def gesture(self,index):
        if index is None:point=(.02,.02)
        else:
            a,b,c,d=self.view.rectangles[index];point=((a+c)/2,(b+d)/2)
        self.view.update_selection(dict(cursor=point,hover=index,progress=.5))

    def test_hand_takeover_only_highlights_hand_preserving_pad_memory(self):
        self.view.set_gamepad_focus(4)
        self.gesture(1)
        self.assertEqual(self.view.highlighted_index,1)
        self.assertEqual(self.view.focus,4)
        # Render the actual buttons: the old pad card must be light, new hand dark.
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        self.view._buttons(cairo.Context(surface));surface.flush()
        def pixel(index):
            a,b,c,d=self.view.buttons[index][3]
            offset=(int(b)+7)*surface.get_stride()+(int(a)+18)*4
            return tuple(bytes(surface.get_data())[offset:offset+3])
        self.assertTrue(max(pixel(1))<110,pixel(1))
        self.assertTrue(min(pixel(4))>210,pixel(4))

    def test_blank_and_lost_hand_never_restore_old_pad_highlight(self):
        self.view.set_gamepad_focus(4);self.gesture(1)
        self.gesture(None)
        self.assertIsNone(self.view.highlighted_index)
        self.view.update_selection({})
        self.assertIsNone(self.view.highlighted_index)
        self.assertEqual(self.view.focus,4)

    def test_hand_expiry_clears_highlight(self):
        with patch('health.view.time.monotonic',return_value=10):self.gesture(1)
        with patch.object(self.view,'get_mapped',return_value=True),patch('health.view.time.monotonic',return_value=10.4):
            self.view.tick()
        self.assertIsNone(self.view.highlighted_index)

    def test_pad_reclaims_single_highlight_and_cancels_dwell(self):
        self.gesture(1);self.view.set_gamepad_focus(4)
        self.assertEqual(self.view.highlighted_index,4)
        self.assertIsNone(self.view.pointer)
        self.assertIsNone(self.view.hover)
        self.assertEqual(self.view.progress,0)

    def test_discrete_navigation_recovers_after_hand_loss(self):
        self.view.set_gamepad_focus(3);self.gesture(None)
        self.view.navigate('down')
        self.assertEqual(self.view.highlighted_index,4)
        self.assertEqual(self.view.focus,4)

if __name__=='__main__':unittest.main()
