"""All XRandR/CVT commands are fake; these tests never touch a display."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from display_modes import (inspect, baseline_output, trial, confirm, watch,
                           restore_transaction, atomic_json, parse_query)
from display_modes import apply_confirmed, complete_confirmed_startup


class FakeXRandR:
    def __init__(self, available=False):
        self.current = '1280x720';self.modes = ['1280x720'] + (['960x540'] if available else [])
        self.calls = [];self.fail_switch = False;self.transform = '1 0 0\n                0 1 0\n                0 0 1'
        self.edid = '00' * 16;self.second = False

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[0] == 'cvt':
            return 'Modeline "960x540_60.00" 40.75 960 992 1088 1216 540 543 548 562 -hsync +vsync\n'
        if argv[1] == '--query':
            size = '960x540' if self.current.startswith('InkSuite_') else self.current
            lines = ['Screen 0: minimum 8 x 8, current ' + size.replace('x', ' x ') + ', maximum 8192 x 8192',
                     'DP-1 connected primary ' + size + '+0+0 (normal left inverted right x axis y axis)']
            lines += ['   ' + name + ' 60.00' + ('*+' if name == self.current else '') for name in self.modes]
            if self.second: lines += ['HDMI-1 connected 1280x720+1280+0 (normal left inverted right x axis y axis)', '   1280x720 60.00*+']
            return '\n'.join(lines)
        if argv[1] == '--verbose':
            return ('DP-1 connected primary 1280x720+0+0 (0x41) normal\n'
                    '        Transform: ' + self.transform + '\n'
                    '        EDID:\n                ' + self.edid + '\n'
                    'HDMI-1 disconnected\n')
        if argv[1] == '--addmode':
            self.modes.append(argv[3]);return ''
        if argv[1] == '--newmode': return ''
        if argv[1] == '--output':
            mode = argv[argv.index('--mode') + 1]
            if self.fail_switch and mode != '1280x720': raise RuntimeError('display rejected mode')
            self.current = mode;return ''
        raise AssertionError(argv)


class DisplayModeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory();self.directory = Path(self.temporary.name)
        self.runner = FakeXRandR();self.now = 100.;self.guard_paths = []
    def tearDown(self): self.temporary.cleanup()
    def clock(self): return self.now
    def guardian(self, path):
        self.guard_paths.append(path)
        saved = json.loads(path.read_text())
        self.assertEqual(saved['baseline']['outputs'][0]['current_mode'], '1280x720')
        self.assertFalse(any(call[1] == '--output' for call in self.runner.calls))
        atomic_json(path.with_suffix('.ready.json'), dict(ready=True, pid=98765))
        return 98765
    def make_trial(self):
        return trial('960x540', self.directory, self.runner, self.guardian, self.clock)

    def test_capture_single_output_identity_edid_and_reject_transform(self):
        snapshot = inspect(self.runner)
        self.assertEqual(baseline_output(snapshot)['current_mode'], '1280x720')
        self.assertEqual(len(snapshot['outputs'][0]['edid_sha256']), 64)
        self.runner.transform = '0.75 0 0\n                0 0.75 0\n                0 0 1'
        with self.assertRaises(RuntimeError): baseline_output(inspect(self.runner))
        self.runner.transform = '1 0 0\n                0 1 0\n                0 0 1';self.runner.second = True
        with self.assertRaises(RuntimeError): baseline_output(inspect(self.runner))

    def test_trial_records_baseline_arms_before_switch_and_does_not_auto_confirm(self):
        result = self.make_trial()
        self.assertEqual(result['rollback_seconds'], 15.)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.runner.current, 'InkSuite_960x540_60')
        self.assertFalse((self.directory / 'confirmed.json').exists())
        saved = json.loads(Path(result['transaction']).read_text())
        self.assertEqual(saved['deadline_monotonic'], 115.)
        self.assertEqual(saved['watchdog_pid'], 98765)
        self.assertEqual([call[1] for call in self.runner.calls if call[0] == 'xrandr'][-3:],
                         ['--newmode', '--addmode', '--output'])

    def test_offered_mode_skips_cvt_creation(self):
        self.runner = FakeXRandR(available=True);self.make_trial()
        self.assertEqual(self.runner.current, '960x540')
        self.assertFalse(any(call[0] == 'cvt' for call in self.runner.calls))

    def test_confirmation_requires_current_token_mode_identity_and_live_deadline(self):
        result = self.make_trial()
        with self.assertRaises(ValueError): confirm('invalid', self.directory, self.runner, self.clock)
        self.runner.edid = '11' * 16
        with self.assertRaises(RuntimeError): confirm(result['token'], self.directory, self.runner, self.clock)
        self.runner.edid = '00' * 16
        preferred = confirm(result['token'], self.directory, self.runner, self.clock)
        self.assertTrue(preferred['human_visibility_confirmation'])
        self.assertTrue((self.directory / 'confirmed.json').exists())
        self.now = 120.
        self.assertEqual(watch(result['transaction'], self.runner, self.clock)['status'], 'confirmed')
        self.assertEqual(self.runner.current, 'InkSuite_960x540_60')

    def test_watchdog_restores_after_controller_disappears_without_confirm(self):
        result = self.make_trial();self.now = 116.
        data = watch(result['transaction'], self.runner, self.clock)
        self.assertEqual(data['status'], 'rolled_back')
        self.assertEqual(self.runner.current, '1280x720')
        restore = self.runner.calls[-1]
        self.assertIn('--transform', restore);self.assertIn('60.00', restore)
        with self.assertRaises(RuntimeError): confirm(result['token'], self.directory, self.runner, self.clock)

    def test_rejected_mode_rolls_back_and_a_repeat_reuses_attached_custom_mode(self):
        self.runner.fail_switch = True
        with self.assertRaises(RuntimeError): self.make_trial()
        saved = json.loads(self.guard_paths[-1].read_text())
        self.assertEqual(saved['status'], 'rolled_back')
        self.assertEqual(self.runner.current, '1280x720')
        self.runner.fail_switch = False;self.runner.calls.clear()
        self.make_trial()
        self.assertFalse(any(call[1] in ('--newmode', '--addmode') for call in self.runner.calls))

    def test_guardian_launch_failure_never_switches_or_leaves_pending_trial(self):
        def broken(_): raise OSError('spawn failed')
        with self.assertRaises(OSError): trial('960x540', self.directory, self.runner, broken, self.clock)
        self.assertFalse(any(call[1] == '--output' for call in self.runner.calls))
        saved = json.loads(next(self.directory.glob('trial-*.json')).read_text())
        self.assertEqual(saved['status'], 'watchdog_failed')

    def test_expired_confirmation_and_second_pending_trial_are_rejected(self):
        result = self.make_trial()
        with self.assertRaises(RuntimeError): self.make_trial()
        self.now = 116.
        with self.assertRaises(RuntimeError): confirm(result['token'], self.directory, self.runner, self.clock)
        self.assertFalse((self.directory / 'confirmed.json').exists())
        self.assertEqual(restore_transaction(result['transaction'], self.runner)['status'], 'rolled_back')

    def test_query_parsing_handles_disconnected_and_negative_offsets(self):
        outputs = parse_query('DP-1 connected 1280x720-1280+0 left (normal left inverted right)\n   1280x720 59.94*+\nHDMI-1 disconnected (normal left inverted right)')
        self.assertEqual(outputs[0]['position'], [-1280, 0])
        self.assertEqual(outputs[0]['rotation'], 'left')
        self.assertFalse(outputs[1]['connected'])

    def test_real_independent_watchdog_survives_controller_process_exit(self):
        path = self.directory / ('trial-' + 'a' * 16 + '.json')
        restored = self.directory / 'fake-restore.json'
        atomic_json(path, dict(status='pending', baseline=inspect(self.runner),
                              deadline_monotonic=time.monotonic() + .8))
        # Both processes are real, while XRandR is a file-writing fake. The
        # controller exits before its independently detached guardian restores.
        watcher = ('from pathlib import Path; import json; from display_modes import watch; '
                   'watch(Path(' + repr(str(path)) + '), runner=lambda argv: '
                   'Path(' + repr(str(restored)) + ').write_text(json.dumps(argv)))')
        controller = ('import subprocess,sys; p=subprocess.Popen([sys.executable,"-c",'
                      + repr(watcher) + '],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,'
                      'stderr=subprocess.DEVNULL,start_new_session=True);print(p.pid)')
        process = subprocess.run([sys.executable, '-c', controller], capture_output=True,
                                 text=True, timeout=3)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(process.stdout.strip().isdigit())
        deadline = time.monotonic() + 3.
        while time.monotonic() < deadline:
            data = json.loads(path.read_text())
            if data['status'] == 'rolled_back':break
            time.sleep(.02)
        self.assertEqual(data['status'], 'rolled_back')
        command = json.loads(restored.read_text())
        self.assertEqual(command[:5], ['xrandr', '--output', 'DP-1', '--mode', '1280x720'])

    def test_startup_without_saved_preference_performs_no_io_or_directory_creation(self):
        absent = self.directory / 'not-created'
        result = apply_confirmed(absent, self.runner, self.guardian, self.clock)
        self.assertEqual(result['reason'], 'no_saved_preference')
        self.assertFalse(absent.exists());self.assertEqual(self.runner.calls, [])

    def test_saved_startup_matches_edid_and_preserves_original_preference(self):
        result = self.make_trial();confirm(result['token'], self.directory, self.runner, self.clock)
        preference = (self.directory / 'confirmed.json').read_bytes()
        self.runner.current = '1280x720';self.runner.calls.clear()
        applied = apply_confirmed(self.directory, self.runner, self.guardian, self.clock)
        self.assertTrue(applied['applied']);self.assertTrue(applied['needs_first_frame_completion'])
        complete = complete_confirmed_startup(applied['token'], self.directory, self.runner, self.clock)
        self.assertTrue(complete['completed'])
        self.assertEqual((self.directory / 'confirmed.json').read_bytes(), preference)
        self.now = 120.
        self.assertEqual(watch(applied['transaction'], self.runner, self.clock)['status'], 'confirmed_startup')
        self.assertEqual(self.runner.current, 'InkSuite_960x540_60')

    def test_changed_screen_saved_startup_is_nonmutating_and_new_trial_cannot_auto_confirm(self):
        result = self.make_trial()
        with self.assertRaises(RuntimeError):
            complete_confirmed_startup(result['token'], self.directory, self.runner, self.clock)
        confirm(result['token'], self.directory, self.runner, self.clock)
        self.runner.current = '1280x720';self.runner.edid = '11' * 16;self.runner.calls.clear()
        result = apply_confirmed(self.directory, self.runner, self.guardian, self.clock)
        self.assertFalse(result['applied']);self.assertEqual(result['reason'], 'monitor_identity_changed')
        self.assertFalse(any(call[1] == '--output' for call in self.runner.calls))

    def test_saved_startup_without_first_frame_ack_rolls_back_original_mode(self):
        result = self.make_trial();confirm(result['token'], self.directory, self.runner, self.clock)
        self.runner.current = '1280x720';self.runner.calls.clear()
        applied = apply_confirmed(self.directory, self.runner, self.guardian, self.clock)
        self.now = 116.
        self.assertEqual(watch(applied['transaction'], self.runner, self.clock)['status'], 'rolled_back')
        self.assertEqual(self.runner.current, '1280x720')


if __name__ == '__main__': unittest.main()
