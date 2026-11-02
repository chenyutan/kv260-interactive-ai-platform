import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connectivity.component_policy import ComponentPolicy


class ComponentPolicyTests(unittest.TestCase):
    def test_startup_delay_no_spam_and_camera_not_faked_ready(self):
        policy = ComponentPolicy()
        self.assertEqual(policy.update({'camera': False}, 0), [])
        self.assertEqual(policy.update({'camera': False}, 7.99), [])
        alerts = policy.update({'camera': False}, 8)
        self.assertEqual([a.message for a in alerts], ['摄像头未连接'])
        policy.dismiss('camera')
        self.assertFalse(policy.ready('camera'))
        for now in (9, 60, 600):
            self.assertEqual(policy.update({'camera': False}, now), [])

    def test_recovery_then_new_loss_debounced_and_new_episode(self):
        policy = ComponentPolicy(initial_delay=0)
        policy.update({'camera': False}, 0)
        first = policy.update({}, 2)[0]
        policy.dismiss('camera')
        policy.update({'camera': True}, 3)
        self.assertTrue(policy.ready('camera'))
        self.assertEqual(policy.update({'camera': False}, 4), [])
        self.assertEqual(policy.update({}, 5.99), [])
        second = policy.update({}, 6)[0]
        self.assertGreater(second.episode, first.episode)

    def test_transient_dropout_does_not_alert(self):
        policy = ComponentPolicy(initial_delay=0)
        policy.update({'audio': True}, 0)
        self.assertEqual(policy.update({'audio': False}, 1), [])
        self.assertEqual(policy.update({'audio': True}, 2), [])
        self.assertEqual(policy.update({}, 10), [])

    def test_optional_components_do_not_change_camera_status(self):
        policy = ComponentPolicy(initial_delay=0, loss_debounce=0)
        alerts = policy.update({'camera': True, 'wifi': False, 'audio': False}, 0)
        self.assertEqual([a.key for a in alerts], ['wifi', 'audio'])
        policy.dismiss('wifi')
        policy.dismiss('audio')
        self.assertTrue(policy.ready('camera'))
        self.assertFalse(policy.ready('wifi'))
        self.assertFalse(policy.ready('audio'))

    def test_retry_failure_can_alert_once_again(self):
        policy = ComponentPolicy(initial_delay=0, loss_debounce=0)
        policy.update({'wifi': False}, 0)
        token = policy.retry('wifi')
        self.assertEqual(policy.update({}, 1), [])
        self.assertTrue(policy.apply_result('wifi', False, token, 2, '无无线网卡'))
        alerts = policy.update({}, 2)
        self.assertEqual(alerts[0].detail, '无无线网卡')
        self.assertEqual(policy.update({}, 3), [])
        self.assertFalse(policy.apply_result('wifi', True, token, 4))

    def test_stale_retry_cannot_override_newer_recovery_or_dismissal(self):
        policy = ComponentPolicy(initial_delay=0, loss_debounce=0)
        policy.update({'camera': False}, 0)
        old = policy.retry('camera')
        new = policy.retry('camera')
        self.assertFalse(policy.apply_result('camera', True, old, 1))
        policy.update({'camera': True}, 2)
        self.assertFalse(policy.apply_result('camera', False, new, 3))
        self.assertTrue(policy.ready('camera'))
        token = policy.retry('audio')
        policy.dismiss('audio')
        self.assertFalse(policy.apply_result('audio', True, token, 4))

    def test_close_ignores_late_updates_and_results(self):
        policy = ComponentPolicy(initial_delay=0, loss_debounce=0)
        policy.update({'camera': False}, 0)
        token = policy.retry('camera')
        policy.close()
        self.assertFalse(policy.apply_result('camera', True, token, 1))
        self.assertEqual(policy.update({'camera': False}, 20), [])
        self.assertFalse(policy.ready('camera'))

    def test_new_launch_does_not_persist_dismissals(self):
        old = ComponentPolicy(initial_delay=0, loss_debounce=0)
        old.update({'audio': False}, 0)
        old.dismiss('audio')
        new = ComponentPolicy(initial_delay=0, loss_debounce=0)
        self.assertEqual(len(new.update({'audio': False}, 0)), 1)

    def test_partial_unknown_and_invalid_data_are_fail_closed(self):
        policy = ComponentPolicy(initial_delay=0, loss_debounce=0)
        self.assertEqual(policy.update({}, 0), [])
        self.assertFalse(policy.ready('camera'))
        alerts = policy.update({'camera': {'connected': 'yes', 'detail': 'missing'},
                                'random': False}, 1)
        self.assertEqual(alerts[0].detail, 'missing')
        self.assertFalse(policy.ready('camera'))
        policy.update({'wifi': True}, 2)
        self.assertFalse(policy.ready('camera'))
        self.assertTrue(policy.ready('wifi'))

    def test_regressed_batches_do_not_replace_recovery(self):
        policy = ComponentPolicy(initial_delay=0, loss_debounce=0)
        policy.update({'camera': True}, 10)
        self.assertEqual(policy.update({'camera': False}, 9), [])
        self.assertTrue(policy.ready('camera'))

    def test_invalid_delays_and_times_rejected(self):
        for value in (-1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                ComponentPolicy(initial_delay=value)
        policy = ComponentPolicy()
        with self.assertRaises(ValueError):
            policy.update({}, float('nan'))


if __name__ == '__main__':
    unittest.main()
