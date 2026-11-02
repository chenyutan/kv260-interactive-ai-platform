"""Photo page state transitions under real GTK, with bounded synthetic IO."""
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path

import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
from PIL import Image

from photo_studio import PhotoStudio
from test_photo_controls import drain, press, ready


def png(color):
    stream=io.BytesIO();Image.new('RGB',(64,48),color).save(stream,format='PNG')
    return stream.getvalue()


def job(identifier='first',status='completed'):
    return dict(id=identifier,status=status,stage='painting' if status=='processing' else 'finished',
                style='color',created='2026-10-21T17:00:01+08:00',elapsed_s=5)


class Store:
    token='visitor-one'
    def current(self):return dict(token=self.token)


class Service:
    def __init__(self):
        self.rows={'visitor-one':[job()]};self.images={'first':png('red'),'second':png('green')}
        self.reads=[];self.submissions=[];self.fail_reads=0;self.read_gate=None;self.submit_gate=None;self.submit_error=None
    def list_jobs(self,token):return [dict(row) for row in self.rows.get(token,[])]
    def read_image(self,token,identifier,kind):
        self.reads.append((token,identifier,kind))
        if self.read_gate:self.read_gate.wait(2)
        if self.fail_reads:
            self.fail_reads-=1;raise OSError('temporary preview IO')
        return self.images.get(identifier,png('blue'))
    def submit_path(self,token,path,style):
        self.submissions.append((token,path,style))
        if self.submit_gate:self.submit_gate.wait(2)
        if self.submit_error:raise OSError(self.submit_error)
        row=job('new','processing');self.rows.setdefault(token,[]).insert(0,row)
        return row


class PhotoUXTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'landscape.png';self.source.write_bytes(png('blue'))
        self.service=Service();self.store=Store();self.exits=[]
        self.window=Gtk.Window();self.window.set_decorated(False);self.window.resize(960,540)
        self.panel=PhotoStudio(lambda:self.exits.append(True),self.service,self.store)
        self.window.add(self.panel);self.window.show_all();self.panel.enter()
        self.wait(lambda:self.panel.pixbuf is not None)
    def tearDown(self):
        for gate in (self.service.submit_gate,self.service.read_gate):
            if gate:gate.set()
        self.panel.close();self.window.destroy();drain(.04);self.temp.cleanup()
    def wait(self,predicate,timeout=3):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.panel.tick();drain(.025)
            if predicate():return
        self.fail('Photo async condition did not complete')
    def select(self):
        self.panel.selected=self.source
        self.panel.filename.set_text(self.source.name)
        self.panel.tick();drain(.06)

    def test_switching_history_clears_old_image_until_new_preview_arrives(self):
        self.service.rows['visitor-one'].append(job('second'))
        self.wait(lambda:self.panel.history.get_model() is not None and len(self.panel.history.get_model())==2)
        self.service.read_gate=threading.Event()
        self.panel.history.set_active_id('second')
        self.wait(lambda:self.panel.image_key is not None and self.panel.image_key[1]=='second')
        self.assertIsNone(self.panel.pixbuf,'Old photo must not appear under the newly selected title')
        self.service.read_gate.set()
        self.wait(lambda:self.panel.pixbuf is not None)
        self.assertEqual(bytes(self.panel.pixbuf.get_pixels())[:3],bytes((0,128,0)))

    def test_transient_preview_error_retries_without_user_toggling(self):
        self.service.fail_reads=1
        self.panel.image_key=None;self.panel.pixbuf=None
        before=len(self.service.reads)
        self.wait(lambda:len(self.service.reads)>before and not self.panel.pending)
        self.wait(lambda:self.panel.pixbuf is not None,3)
        self.assertGreaterEqual(len(self.service.reads),before+2)

    def test_selecting_another_file_does_not_unlock_generate_during_processing(self):
        self.service.rows['visitor-one']=[job('first','processing')]
        self.wait(lambda:self.panel.job['status']=='processing')
        self.panel.choose_file();self.panel.dialog.set_filename(self.source)
        ready(lambda:self.panel.dialog.get_filename()==str(self.source))
        self.panel.dialog.response(Gtk.ResponseType.OK);drain()
        self.assertFalse(self.panel.generate.is_sensitive())
        self.panel.submit();drain(.05)
        self.assertEqual(self.service.submissions,[])

    def test_leave_reenter_during_import_preserves_submission_lock(self):
        self.select();self.service.submit_gate=threading.Event()
        self.panel.submit();self.wait(lambda:len(self.service.submissions)==1)
        self.panel.leave();self.panel.enter();self.wait(lambda:self.panel.session_token=='visitor-one')
        self.assertTrue(self.panel.busy)
        self.assertFalse(self.panel.generate.is_sensitive())
        self.panel.submit();self.assertEqual(len(self.service.submissions),1)
        self.service.submit_gate.set()
        self.wait(lambda:not self.panel.busy and self.panel.job['id']=='new')

    def test_submission_error_remains_visible_across_status_poll(self):
        self.select();self.service.submit_error='U盘读取失败，请重新连接后再试。'
        self.panel.submit();self.wait(lambda:not self.panel.busy)
        for _ in range(3):self.panel.tick();drain(.05)
        self.assertIn('U盘读取失败',self.panel.status.get_text())
        self.assertTrue(self.panel.generate.is_sensitive())

    def test_new_visitor_clears_original_toggle_and_empty_state(self):
        self.panel.original.set_active(True);self.select()
        self.store.token='visitor-two'
        self.wait(lambda:self.panel.session_token=='visitor-two')
        self.assertFalse(self.panel.original.is_sensitive())
        self.assertFalse(self.panel.original.get_active())
        self.assertIsNone(self.panel.pixbuf)
        self.assertIsNone(self.panel.selected)
        self.assertFalse(self.panel.history.is_sensitive())
        self.assertNotIn('作品已保存',self.panel.status.get_text())

    def test_removed_jobs_clear_old_preview_and_buttons(self):
        self.service.rows['visitor-one']=[]
        self.wait(lambda:self.panel.job is None)
        self.assertIsNone(self.panel.pixbuf)
        self.assertFalse(self.panel.original.is_sensitive())
        self.assertFalse(self.panel.history.is_sensitive())

    def test_long_import_error_does_not_resize_540p_page(self):
        self.select();self.service.submit_error='无法读取照片，请检查U盘。'*100
        self.panel.submit();self.wait(lambda:not self.panel.busy)
        drain(.2)
        self.assertEqual(tuple(self.window.get_size()),(960,540))
        self.assertLessEqual(self.panel.status.get_allocated_height(),54)

    def test_destroyed_picker_can_be_opened_again(self):
        self.panel.choose_file();first=self.panel.dialog;first.destroy();drain()
        self.assertIsNone(self.panel.dialog)
        self.panel.choose_file();self.assertIsNotNone(self.panel.dialog)

    def test_phone_upload_refreshes_newest_but_keeps_explicit_history_selection(self):
        self.service.rows['visitor-one'].insert(0,job('second'))
        self.wait(lambda:self.panel.image_key is not None and self.panel.image_key[1]=='second' and self.panel.pixbuf is not None)
        self.assertEqual(self.panel.history.get_active_id(),'second')
        self.panel.history.set_active_id('first')
        self.wait(lambda:self.panel.job['id']=='first' and self.panel.pixbuf is not None)
        for _ in range(3):self.panel.tick();drain(.04)
        self.assertEqual(self.panel.history.get_active_id(),'first')

    def test_old_preview_completion_cannot_replace_new_history_selection(self):
        gate=threading.Event();self.service.read_gate=gate
        self.service.rows['visitor-one'].append(job('second'))
        self.wait(lambda:len(self.panel.history.get_model())==2)
        original=self.service.read_image
        def read(token,identifier,kind):
            if identifier=='first':gate.wait(2)
            return self.service.images[identifier]
        self.service.read_image=read
        self.panel.image_key=None;self.panel.load_image();drain(.04)
        self.panel.history.set_active_id('second')
        self.wait(lambda:self.panel.image_key is not None and self.panel.image_key[1]=='second' and self.panel.pixbuf is not None)
        gate.set();drain(.1)
        self.assertEqual(bytes(self.panel.pixbuf.get_pixels())[:3],bytes((0,128,0)))

    def test_picker_cancel_preserves_selected_file(self):
        self.select();self.panel.choose_file()
        press(self.panel.dialog,'B')
        self.assertIsNone(self.panel.dialog)
        self.assertEqual(self.panel.selected,self.source)

    def test_import_error_recovers_on_successful_retry(self):
        self.select();self.service.submit_error='照片读取失败'
        self.panel.submit();self.wait(lambda:not self.panel.busy)
        self.assertIn('照片读取失败',self.panel.status.get_text())
        self.service.submit_error=None;self.panel.submit()
        self.wait(lambda:self.panel.job['id']=='new' and not self.panel.busy)
        self.assertNotIn('照片读取失败',self.panel.status.get_text())
        self.assertFalse(self.panel.generate.is_sensitive())

    def test_style_selection_is_used_for_submission(self):
        self.select();self.panel.pad_controls.focus(self.panel.style)
        press(self.panel,'RIGHT');self.assertEqual(self.panel.style.get_active_id(),'ink')
        self.panel.submit();self.wait(lambda:bool(self.service.submissions))
        self.assertEqual(self.service.submissions[0][2],'ink')

    def test_returns_during_processing_do_not_resubmit_or_cancel(self):
        self.service.rows['visitor-one']=[job('first','processing')]
        self.wait(lambda:self.panel.job['status']=='processing')
        press(self.panel,'B');self.panel.leave()
        self.assertEqual(self.exits,[True]);self.assertEqual(self.service.submissions,[])
        self.panel.enter();self.wait(lambda:self.panel.job is not None)
        self.assertEqual(self.panel.job['status'],'processing')
        self.assertIn('不会中断',self.panel.status.get_text())

    def test_new_visitor_cancels_old_picker(self):
        self.panel.choose_file();old=self.panel.dialog
        self.store.token='visitor-two'
        self.wait(lambda:self.panel.session_token=='visitor-two')
        self.assertIsNone(self.panel.dialog)
        self.assertTrue(old.destroyed)


if __name__=='__main__':unittest.main(verbosity=2)
