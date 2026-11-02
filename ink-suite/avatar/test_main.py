"""Native scene/page regression tests; synthetic poses and photos only.

Run in the isolated board staging root with DISPLAY=:98 python3 -m unittest
avatar.test_main -v. Never contacts the pose API, camera, or artwork website.
"""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

try:
    import cairo
    from avatar import main as ui
    GTK_AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in ('cairo', 'gi'):
        raise
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'Native Cairo/GTK tests run on the board under Xvfb')
class AvatarUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.photo_calls = []
        self.render_entered = threading.Event()
        self.render_release = threading.Event(); self.render_release.set()
        self.photo_module = ui.photo_module()
        owner = self

        class Jobs:
            def __init__(self, **kwargs):
                pass

            def validate_resources(self, settings):
                pass

            def render(self, rgb, settings, cancelled, progress=None):
                owner.photo_calls.append(rgb.shape)
                owner.render_entered.set()
                if progress:
                    progress({'stage': 'painting'})
                while not owner.render_release.wait(.01):
                    if cancelled.is_set():
                        raise owner.photo_module.RenderCancelled()
                return 255-rgb, dict(renderer='offline-test-worker', finish_version='test')

        self.jobs_patch = patch.object(self.photo_module, 'PhotoJobs', Jobs); self.jobs_patch.start()
        self.emit_patch = patch.object(ui, 'emit'); self.events = self.emit_patch.start()
        self.photo_emit_patch = patch.object(self.photo_module, 'emit'); self.photo_events = self.photo_emit_patch.start()
        self.network_patch = patch('urllib.request.OpenerDirector.open',
            side_effect=AssertionError('Offline UI test must not open the network'))
        self.network = self.network_patch.start()
        args = SimpleNamespace(embed_socket=None, offline_preview=True, offline_image=None,
            base='http://127.0.0.1:1', works=Path(self.directory.name), fps=4.)
        self.panel = ui.AvatarPanel(args)
        self.window = ui.Gtk.Window(); self.window.resize(1024, 600)
        self.window.add(self.panel); self.window.show_all()

    def tearDown(self):
        self.render_release.set()
        with patch.object(ui.Gtk, 'main_quit'):
            self.panel.operation('shutdown')
        self.window.destroy()
        for worker in self.panel.save_threads:
            worker.join(5)
        if self.panel.photo:
            self.panel.photo.preview_thread.join(2)
            for worker in self.panel.photo.photo_threads+self.panel.photo.save_threads:
                worker.join(5)
        self.pump(.04)
        self.network.assert_not_called()
        self.network_patch.stop(); self.photo_emit_patch.stop(); self.emit_patch.stop(); self.jobs_patch.stop()
        self.directory.cleanup()

    def pump(self, seconds=.05, until=None):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            # Animation timers keep enqueueing draws under Xvfb; bounded
            # dispatch must yield so the test's timeout can still expire.
            dispatched = 0
            while dispatched<8 and time.monotonic()<deadline and ui.Gtk.events_pending():
                ui.Gtk.main_iteration_do(False)
                dispatched += 1
            if until and until():
                return
            time.sleep(.005)
        if until:
            self.assertTrue(until())

    def finish_countdown(self):
        photo = self.panel.photo
        photo.cancel_countdown_timer()
        with photo.lock:
            photo.session.deadline = time.monotonic()-.01
        photo.countdown_tick()

    def shoot(self):
        self.assertTrue(self.panel.photo.begin_capture())
        self.finish_countdown()
        self.pump(3, lambda:self.panel.photo.session.phase=='result')

    def test_avatar_starts_without_photo_creation_or_capture(self):
        self.pump(.15)
        self.assertEqual(self.panel.page, 'avatar')
        self.assertTrue(self.panel.active)
        self.assertIsNone(self.panel.photo)
        self.assertGreater(self.panel.frames_drawn, 0)
        self.assertIsNone(self.panel.poll_thread)
        self.assertEqual(self.photo_calls, [])

    def test_embedded_entry_stays_inactive_until_parent_resume(self):
        args = SimpleNamespace(**vars(self.panel.args)); args.embed_socket = 123
        embedded = ui.AvatarPanel(args)
        try:
            self.assertFalse(embedded.active)
            pose = embedded.pose
            embedded.tick(); embedded.save()
            self.assertIs(embedded.pose, pose)
            self.assertEqual(embedded.save_threads, [])
            self.assertIsNone(embedded.photo)
            embedded.operation('resume')
            self.assertTrue(embedded.active)
            embedded.tick()
            self.assertIsNot(embedded.pose, pose)
        finally:
            with patch.object(ui.Gtk, 'main_quit'):
                embedded.operation('shutdown')
            embedded.destroy()

    def test_scene_button_clicks_are_exclusive_and_do_not_reenter(self):
        for scene in ('mountains', 'plum', 'bamboo', 'bamboo'):
            self.panel.scene_buttons[scene].clicked()
            self.assertEqual(self.panel.scene, scene)
            self.assertEqual([name for name,button in self.panel.scene_buttons.items() if button.get_active()], [scene])
        self.assertEqual(self.panel.character, 'scholar')

    def test_pause_resets_tracking_and_resume_preserves_selected_art(self):
        self.panel.choose_scene('plum'); self.pump(.1)
        old_generation = self.panel.generation
        self.panel.operation('pause')
        paused_pose = self.panel.pose
        self.pump(.15)
        self.assertFalse(self.panel.active)
        self.assertIs(self.panel.pose, paused_pose)
        self.assertIsNone(self.panel.latest)
        self.assertGreater(self.panel.generation, old_generation)
        self.panel.operation('resume'); self.pump(.1)
        self.assertTrue(self.panel.active)
        self.assertEqual(self.panel.scene, 'plum')
        self.assertEqual(self.panel.character, 'scholar')
        self.assertEqual(self.photo_calls, [])

    def test_pose_response_cannot_publish_after_generation_changes_at_lock(self):
        owner = self.panel
        class PageChangesBeforeCommit:
            def __enter__(self):
                # Deterministically model a main-thread pause at the response
                # commit boundary; polling must recheck under this same lock.
                owner.generation += 1
                owner.active = False
                owner.latest = None
            def __exit__(self, *_):
                pass
        opener = SimpleNamespace(open=lambda *args, **kwargs:io.StringIO(json.dumps(ui.demo_state(10))))
        with patch.object(ui.urllib.request, 'build_opener', return_value=opener), \
                patch.object(owner, 'lock', PageChangesBeforeCommit()), \
                patch.object(owner.stop, 'wait', side_effect=lambda _:owner.stop.set()):
            try:
                owner.poll()
                self.assertIsNone(owner.latest)
            finally:
                owner.stop.clear()

    def test_page_buttons_and_photo_back_keep_one_panel_and_finished_result(self):
        self.panel.photo_button.clicked()
        photo = self.panel.photo
        self.assertEqual(self.panel.page, 'photo')
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'photo')
        self.assertTrue(self.panel.photo_button.get_active())
        self.assertFalse(self.panel.avatar_button.get_active())
        self.assertTrue(photo.session.active)
        self.assertEqual(photo.back_button.get_label(), '返回国画分身')
        self.pump(.1); self.assertEqual(self.photo_calls, [])
        self.shoot(); art = photo.current
        photo.request_menu()
        self.assertEqual(self.panel.page, 'avatar')
        self.assertFalse(photo.session.active)
        self.events.assert_not_called()
        self.panel.photo_button.clicked()
        self.assertIs(self.panel.photo, photo)
        self.assertIs(photo.current, art)
        self.assertEqual(photo.session.phase, 'result')
        self.panel.operation('pause'); self.panel.operation('resume')
        self.assertIs(photo.current, art)
        self.assertEqual(len(self.photo_calls), 1)

    def test_switching_pages_cancels_countdown_without_taking_photo(self):
        self.panel.show_page('photo'); photo = self.panel.photo
        self.assertTrue(photo.begin_capture())
        self.panel.show_page('avatar'); self.pump(.1)
        self.assertFalse(photo.session.active)
        self.assertIsNone(photo.countdown_source)
        self.assertEqual(photo.session.phase, 'viewfinder')
        self.assertEqual(self.photo_calls, [])
        self.panel.show_page('photo'); self.pump(.1)
        self.assertTrue(photo.session.active)
        self.assertEqual(self.photo_calls, [])

    def test_switching_pages_cancels_processing_then_next_photo_succeeds(self):
        self.render_release.clear()
        self.panel.show_page('photo'); photo = self.panel.photo
        photo.begin_capture(); self.finish_countdown(); self.pump(2, self.render_entered.is_set)
        self.panel.show_page('avatar')
        self.assertTrue(photo.photo_cancel.is_set())
        photo.photo_threads[-1].join(2)
        self.assertFalse(photo.photo_threads[-1].is_alive())
        self.assertIsNone(photo.current)
        self.panel.show_page('photo'); self.render_release.set(); self.shoot()
        self.assertEqual(len(self.photo_calls), 2)

    def test_avatar_save_is_exact_snapshot_and_repeated_click_is_ignored(self):
        self.panel.pose = ui.neutral_pose()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 720)
        self.panel.paint(cairo.Context(surface), 1280, 720); surface.flush()
        expected = np.asarray(Image.frombuffer('RGBA', (1280,720), bytes(surface.get_data()), 'raw', 'BGRA', 0, 1).convert('RGB'))
        entered = threading.Event(); release = threading.Event()
        real_save = self.photo_module.save_art
        def delayed_save(*args):
            entered.set(); release.wait(3)
            return real_save(*args)
        with patch.object(self.photo_module, 'save_art', delayed_save):
            self.panel.save(); self.pump(1, entered.is_set)
            self.assertFalse(self.panel.save_button.get_sensitive())
            self.panel.save(); self.assertEqual(len(self.panel.save_threads), 1)
            self.panel.choose_scene('plum'); release.set()
            self.pump(4, lambda:not self.panel.save_busy)
        paths = list(Path(self.directory.name).glob('*.png'))
        self.assertEqual(len(paths), 1)
        np.testing.assert_array_equal(np.asarray(Image.open(paths[0])), expected)
        metadata = json.loads(paths[0].with_suffix('.json').read_text())
        self.assertEqual(metadata['scene'], 'bamboo')
        self.assertEqual(metadata['character'], 'scholar')
        self.assertEqual(metadata['source_kind'], 'offline-demo')
        self.assertEqual(metadata['renderer'], 'fixed-ink-avatar-v1')
        self.assertIsNone(self.panel.share_dialog)
        self.assertTrue(self.panel.save_button.get_sensitive())

    def test_save_resolves_photo_module_on_the_ui_thread(self):
        calls = []
        def resolve():
            calls.append(threading.get_ident())
            return self.photo_module
        with patch.object(ui, 'photo_module', side_effect=resolve):
            self.panel.save()
            self.pump(4, lambda:not self.panel.save_busy)
        self.assertEqual(calls, [threading.get_ident()])

    def test_stale_share_cannot_reopen_after_pause_or_page_change(self):
        generation = self.panel.generation
        self.panel.operation('pause'); self.panel.operation('resume')
        self.panel.show_share(dict(png=b'not-a-png', url='http://example.invalid'), generation)
        self.assertIsNone(self.panel.share_dialog)
        generation = self.panel.generation
        self.panel.show_page('photo')
        self.panel.show_share(dict(png=b'not-a-png', url='http://example.invalid'), generation)
        self.assertIsNone(self.panel.share_dialog)

    def test_requested_avatar_save_finishes_on_shutdown(self):
        entered = threading.Event(); release = threading.Event(); real_save = self.photo_module.save_art
        def delayed_save(*args):
            entered.set(); release.wait(3)
            return real_save(*args)
        with patch.object(self.photo_module, 'save_art', delayed_save):
            self.panel.save(); self.pump(1, entered.is_set)
            self.assertFalse(self.panel.save_threads[-1].daemon)
            with patch.object(ui.Gtk, 'main_quit'):
                self.panel.operation('shutdown')
            release.set(); self.panel.save_threads[-1].join(4)
        self.assertFalse(self.panel.save_threads[-1].is_alive())
        self.assertEqual(len(list(Path(self.directory.name).glob('*.png'))), 1)
        self.assertEqual(len(list(Path(self.directory.name).glob('*.json'))), 1)

    def test_menu_pauses_active_photo_and_shutdown_closes_both_pages(self):
        self.panel.show_page('photo'); photo = self.panel.photo; photo.begin_capture()
        self.panel.request_menu()
        self.assertFalse(self.panel.active); self.assertFalse(photo.session.active)
        self.events.assert_called_once_with('menu_requested')
        self.assertIsNone(photo.countdown_source)
        self.panel.operation('resume')
        self.assertTrue(photo.session.active)
        with patch.object(ui.Gtk, 'main_quit'):
            self.panel.operation('shutdown')
        self.assertTrue(self.panel.stop.is_set()); self.assertTrue(photo.stop.is_set())
        self.assertIsNone(self.panel.timer)
        self.assertEqual(photo.session.phase, 'closed')

    def test_photo_space_shortcut_is_forwarded_to_shutter(self):
        self.panel.show_page('photo')
        handled = self.panel.key(None, SimpleNamespace(keyval=ui.Gdk.KEY_space))
        self.assertTrue(handled)
        self.assertEqual(self.panel.photo.session.phase, 'countdown')


if __name__ == '__main__':
    unittest.main()
