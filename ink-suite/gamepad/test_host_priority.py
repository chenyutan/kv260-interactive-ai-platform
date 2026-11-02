"""Host arbitration against real reader/host code, without a GTK display.

The host module is loaded with inert GTK imports. PoseView's actual class body
is compiled without its Gtk.Box base so its real selection interface can be
exercised without constructing a camera preview or a window.
"""
import ast
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from gamepad.device import GamepadReader, EV_ABS, EV_KEY, EV_SYN, SYN_REPORT
from gamepad.navigation import Navigation


ROOT = Path(__file__).resolve().parents[1]


def load_host():
    spec = importlib.util.spec_from_file_location(
        'gamepad._priority_host_contract', ROOT / 'gamepad' / 'host.py')
    module = importlib.util.module_from_spec(spec)
    repository = NS(Gtk=NS(), GLib=NS())
    with patch.dict('sys.modules', {
            'gi': NS(repository=repository), 'gi.repository': repository,
            'gamepad.dialog_navigation': NS(DialogNavigation=Mock)}):
        spec.loader.exec_module(module)
    return module


def actual_pose_view_class():
    path = ROOT / 'pose_view.py'
    tree = ast.parse(path.read_text(encoding='utf8'))
    constant = next(node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'BACK_ZONE'
                            for target in node.targets))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == 'PoseView')
    klass.bases = []
    namespace = {}
    source = ast.fix_missing_locations(ast.Module(body=[constant, klass], type_ignores=[]))
    exec(compile(source, str(path), 'exec'), namespace)
    return namespace['PoseView']


class HostPriorityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_host()
        self.now = 100.0
        self.module.time = NS(monotonic=lambda: self.now)
        self.reader = GamepadReader(start=False)
        axes = {code: (0, -32768, 32767, 0, 3000, 0) for code in (0, 1, 3, 4)}
        axes.update({code: (0, 0, 255, 0, 0, 0) for code in (2, 5)})
        self.reader.attach('Synthetic controller', axes)
        self.reader.snapshot()
        view = NS(mode_choices=('one', 'two'), choices=('one', 'two'),
                  rectangles=((0., 0., .4, 1.), (.6, 0., 1., 1.)),
                  section='home', set_availability=Mock(), update_selection=Mock())
        self.suite = NS(stop=threading.Event(), control_mode='body', health_active=False,
                        menu_open=True, menu_level='main', menu=view, actions=view,
                        switching=False, pending_action=None, visitor_busy=False,
                        entry_pending=0., entry_deadline=0., navigation_token=1,
                        action_page='root', child=None, select=Mock(), open_menu=Mock(),
                        navigate_back=Mock(), set_status=Mock())
        self.host = self.module.HostControls.__new__(self.module.HostControls)
        self.host.suite = self.suite
        self.host.reader = self.reader
        self.host.pointer = NS(update=Mock(return_value={}))
        self.host.navigation = Navigation()
        self.host.dialog = lambda: None
        self.host.closed = False
        self.host.connected = False
        self.host.last = self.now - .016
        self.host.navigation_until = 0.
        self.host.sequence = 0

    def tearDown(self):
        self.reader.close()

    def report(self, *events):
        for event in events:
            self.reader.feed(*event)
        self.reader.feed(EV_SYN, SYN_REPORT, 0)

    def test_idle_connection_does_not_own_body_cursor(self):
        self.assertFalse(self.host.gestures_blocked(self.now))
        self.assertTrue(self.host.tick())
        self.assertFalse(self.host.navigation_active(self.now))
        self.suite.menu.update_selection.assert_not_called()

    def test_explicit_gamepad_keeps_gestures_blocked_after_disconnect(self):
        self.suite.control_mode = 'gamepad'
        self.reader.disconnect()
        self.assertTrue(self.host.gestures_blocked(self.now))
        self.assertFalse(self.host.navigation_active(self.now))

    def test_priority_peeks_preserve_a_press_for_exactly_one_dispatch(self):
        self.report((EV_KEY, 304, 1))
        with patch.object(self.reader, 'snapshot', wraps=self.reader.snapshot) as snapshot:
            self.assertTrue(self.host.gestures_blocked(self.now))
            self.assertTrue(self.host.gestures_blocked(self.now))
            self.assertEqual(snapshot.call_count, 2)
            self.assertTrue(all(call.kwargs == {'consume_edges': False}
                                for call in snapshot.call_args_list))
        self.assertIn('A', self.reader.snapshot(consume_edges=False)['pressed'])
        self.assertTrue(self.host.tick())
        self.assertTrue(self.host.tick())
        self.suite.select.assert_called_once_with('one')
        self.assertFalse(self.reader.snapshot(consume_edges=False)['pressed'])

    def test_fast_tap_survives_gesture_peek_and_is_not_replayed(self):
        self.report((EV_KEY, 304, 1))
        self.report((EV_KEY, 304, 0))
        self.assertTrue(self.host.gestures_blocked(self.now))
        self.assertTrue(self.host.tick())
        self.now += .016
        self.assertTrue(self.host.tick())
        self.suite.select.assert_called_once_with('one')

    def test_gesture_gate_runs_before_pointer_dispatch(self):
        trace = []
        self.report((EV_ABS, 3, 32767))
        def suppress(now):
            trace.append(('gate', self.host.gestures_blocked(now)))
        self.suite.suppress_gesture_navigation = suppress
        self.host.pointer.update = lambda *_args, **_kwargs: trace.append(('pointer', True)) or {}
        self.assertTrue(self.host.tick())
        self.assertEqual(trace, [('gate', True), ('pointer', True)])

    def test_right_stick_owns_gestures_without_keyboard_selection(self):
        for mode in ('body', 'gamepad'):
            with self.subTest(mode=mode):
                self.suite.control_mode = mode
                self.report((EV_ABS, 3, 20000))
                self.assertTrue(self.host.gestures_blocked(self.now))
                self.assertTrue(self.host.tick())
                self.now += .016
                self.assertTrue(self.host.tick())
                self.assertFalse(self.host.navigation_active(self.now))
                self.suite.menu.update_selection.assert_not_called()

    def test_right_stick_cancels_previous_navigation_focus(self):
        self.report((EV_KEY, 547, 1))
        self.assertTrue(self.host.tick())
        self.assertEqual(self.suite.menu.update_selection.call_count, 1)
        self.report((EV_KEY, 547, 0))
        self.assertTrue(self.host.tick())
        self.suite.menu.update_selection.reset_mock()
        self.suite.control_mode = 'gamepad'
        self.report((EV_ABS, 3, 32767))
        self.assertTrue(self.host.tick())
        self.report((EV_ABS, 3, 0))
        self.now += .016
        self.assertTrue(self.host.tick())
        self.assertTrue(self.host.gestures_blocked(self.now))
        self.suite.menu.update_selection.assert_not_called()
        self.report((EV_KEY, 546, 1))
        self.assertTrue(self.host.tick())
        self.suite.menu.update_selection.assert_called_once()

    def test_actual_pose_view_without_availability_accepts_confirm(self):
        klass = actual_pose_view_class()
        self.assertFalse(hasattr(klass, 'set_availability'))
        view = klass.__new__(klass)
        # The AST harness strips Gtk.Box; model an as-yet unmapped view.
        view.get_mapped = lambda: False
        view.return_button = NS(get_label=lambda: '返回主菜单 · 95%', set_label=Mock(),
                                grab_focus=Mock())
        self.suite.pose_view = view
        self.suite.menu_level = 'pose'
        self.report((EV_KEY, 304, 1))
        self.assertTrue(self.host.tick())
        self.assertEqual(self.host.last_error, '')
        view.return_button.set_label.assert_called_once_with('返回主菜单')
        self.suite.open_menu.assert_called_once_with(main=True)
        self.assertTrue(self.host.tick())
        self.suite.open_menu.assert_called_once_with(main=True)
        self.suite.menu_level = 'main'
        self.report((EV_KEY, 304, 0), (EV_KEY, 547, 1))
        self.assertTrue(self.host.tick())
        self.assertEqual(self.host.last_error, '')
        self.assertEqual(self.host.navigation.index, 1)
        self.suite.menu.update_selection.assert_called_once()

    def test_failed_tick_logs_diagnostic_and_next_tick_recovers(self):
        self.host.pointer.update.side_effect = [RuntimeError('synthetic pointer failure'), {}]
        with self.assertLogs('gamepad.host', level='ERROR') as captured:
            self.assertTrue(self.host.tick())
        self.assertIn('synthetic pointer failure', '\n'.join(captured.output))
        self.assertIn('RuntimeError', self.host.last_error)
        self.assertEqual(self.host.error_count, 1)
        self.now += .016
        self.assertTrue(self.host.tick())
        self.assertEqual(self.host.last_error, '')
        self.assertEqual(self.host.error_count, 1)
        self.assertEqual(self.host.last_success, self.now)
        self.assertEqual(self.host.tick_count, 2)

    def test_failed_action_edge_is_not_replayed_after_recovery(self):
        self.report((EV_KEY, 304, 1))
        self.suite.select.side_effect = RuntimeError('synthetic action failure')
        with self.assertLogs('gamepad.host', level='ERROR'):
            self.assertTrue(self.host.tick())
        self.suite.select.side_effect = None
        self.assertTrue(self.host.tick())
        self.assertEqual(self.suite.select.call_count, 1)
        self.report((EV_KEY, 304, 0))
        self.assertTrue(self.host.tick())
        self.report((EV_KEY, 304, 1))
        self.assertTrue(self.host.tick())
        self.assertEqual(self.suite.select.call_count, 2)

    def test_close_and_stop_end_timer_normally(self):
        self.host.closed = True
        self.assertFalse(self.host.tick())
        self.host.closed = False
        self.suite.stop.set()
        self.assertFalse(self.host.tick())
        self.host.pointer.update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
