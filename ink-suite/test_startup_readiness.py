import copy
import unittest

from startup_readiness import PipelineReadiness


def frame(sequence=1, stamp=100., pose_count=None, **changes):
    state = dict(pid=42, phase='idle', sequence=sequence,
                 timestamp_seconds=stamp, pose_count=sequence if pose_count is None else pose_count,
                 camera_link={'present': True}, points17=None)
    state.update(changes)
    return state


class ReadinessTests(unittest.TestCase):
    def test_missing_process_identity_cannot_prove_a_live_stream(self):
        for pid in (None, '', 0, True, [], {}):
            gate = PipelineReadiness()
            for i in range(5):
                self.assertFalse(gate.update(frame(i+1,100.+i*.04,pid=pid),100.+i*.04))
    def prime(self, gate=None, base=100., **changes):
        gate = gate or PipelineReadiness()
        self.assertFalse(gate.update(frame(1, base, **changes), base))
        self.assertFalse(gate.update(frame(2, base + .04, **changes), base + .04))
        self.assertTrue(gate.update(frame(3, base + .08, **changes), base + .08))
        return gate

    def test_http_success_is_not_capture_or_inference_readiness(self):
        gate = PipelineReadiness()
        for state in ({}, {'pid': 42, 'phase': 'warming_up'}, frame(pose_count=0)):
            self.assertFalse(gate.update(state, 100.))
            self.assertEqual(gate.reason, 'warming_up')

    def test_requires_three_advanced_observations_and_elapsed_capture_span(self):
        gate = PipelineReadiness()
        for sequence, stamp in enumerate((100., 100.02, 100.04, 100.06), 1):
            self.assertFalse(gate.update(frame(sequence, stamp), stamp))
        self.assertTrue(gate.update(frame(5, 100.08), 100.08))
        self.assertEqual(gate.reason, 'ready')

    def test_rapid_replay_of_cached_frames_does_not_complete_warmup(self):
        gate = PipelineReadiness()
        for i in range(5):
            self.assertFalse(gate.update(frame(i+1, 100.+i*.02), 100.))

    def test_repeated_http_cache_never_opens_gate(self):
        gate = PipelineReadiness()
        for delta in (0., .04, .08, .2, .41, .6):
            self.assertFalse(gate.update(frame(), 100.+delta))
        self.assertEqual(gate.reason, 'stream_stalled')

    def test_duplicates_do_not_count_but_short_poll_repeats_are_allowed(self):
        gate = PipelineReadiness()
        for now in (100., 100.01, 100.02, 100.03):
            self.assertFalse(gate.update(frame(), now))
        self.assertFalse(gate.update(frame(2, 100.04), 100.04))
        self.assertTrue(gate.update(frame(3, 100.08), 100.08))
        self.assertTrue(gate.update(frame(3, 100.08), 100.3))
        self.assertFalse(gate.update(frame(3, 100.08), 100.49))
        self.assertEqual(gate.reason, 'stream_stalled')
        for i in (4, 5):
            self.assertFalse(gate.update(frame(i, 100.5 + (i-4)*.04), 100.5 + (i-4)*.04))
        self.assertTrue(gate.update(frame(6, 100.58), 100.58))

    def test_fresh_timestamps_and_sequences_cannot_mask_frozen_inference(self):
        gate = PipelineReadiness()
        for i in range(15):
            self.assertFalse(gate.update(frame(i+1, 100.+i*.04, pose_count=1), 100.+i*.04))
        self.assertEqual(gate.reason, 'stream_stalled')

    def test_fresh_inference_count_cannot_mask_frozen_capture_identity(self):
        gate = PipelineReadiness()
        for i in range(15):
            self.assertFalse(gate.update(frame(1, 100., pose_count=i+1), 100.+i*.04))
        self.assertEqual(gate.reason, 'stream_stalled')

    def test_timestamp_only_activity_cannot_keep_ready_stream_open(self):
        gate = self.prime()
        for i in range(1, 10):
            self.assertTrue(gate.update(frame(3, 100.08+i*.04, pose_count=3), 100.08+i*.04))
        self.assertFalse(gate.update(frame(3, 100.50, pose_count=3), 100.50))
        self.assertEqual(gate.reason, 'stream_stalled')

    def test_no_person_and_low_confidence_do_not_block_healthy_pipeline(self):
        for phase in ('idle', 'unknown', 'not_ready', 'no_person'):
            with self.subTest(phase=phase):
                self.prime(phase=phase, points17=[[0., 0., 0.]]*17,
                           quality='关键关节不完整', quality_detail={'valid': False})

    def test_service_failure_and_camera_disconnect_require_new_warmup(self):
        for bad, reason in ((None, 'service_unavailable'),
                            (frame(4, 100.12, camera_link={'present': False}), 'camera_missing')):
            with self.subTest(reason=reason):
                gate = self.prime()
                self.assertFalse(gate.update(bad, 101. if bad is None else 100.12))
                self.assertEqual(gate.reason, reason)
                self.prime(gate, 101.)

    def test_brief_transport_miss_does_not_rearm_running_experience(self):
        gate=self.prime()
        for now in (100.12,100.30,100.65):
            self.assertTrue(gate.update(None,now))
            self.assertEqual(gate.reason,'transport_retry')
        self.assertTrue(gate.update(frame(20,100.70),100.70))
        self.assertEqual(gate.reason,'ready')

    def test_transport_grace_never_proves_startup_or_masks_long_loss(self):
        gate=PipelineReadiness()
        self.assertFalse(gate.update(None,100.))
        gate=self.prime()
        self.assertTrue(gate.update(None,100.5))
        self.assertFalse(gate.update(None,100.9))
        self.assertFalse(gate.update(frame(20,100.94),100.94))
        self.assertFalse(gate.update(frame(21,100.98),100.98))
        self.assertTrue(gate.update(frame(22,101.02),101.02))

    def test_cached_response_after_transport_miss_cannot_hide_frozen_pipeline(self):
        gate=self.prime()
        self.assertTrue(gate.update(None,100.60))
        self.assertFalse(gate.update(frame(3,100.08),100.62))
        self.assertEqual(gate.reason,'stream_stalled')

    def test_explicit_capture_loss_and_process_changes_during_grace_still_block(self):
        for changes in ({'camera_link':{'present':False}},{'phase':'camera_unavailable'},
                        {'pid':43},{'sequence':1},{'pose_count':1}):
            with self.subTest(changes=changes):
                gate=self.prime();self.assertTrue(gate.update(None,100.30))
                state=frame(20,100.60);state.update(changes)
                self.assertFalse(gate.update(state,100.60))

    def test_runtime_failure_phases_clear_even_if_old_points_and_counters_exist(self):
        for phase in ('lost', 'camera_link_slow', 'error', 'failed', 'warming_up'):
            with self.subTest(phase=phase):
                gate = self.prime()
                self.assertFalse(gate.update(frame(4, 100.12, phase=phase), 100.12))
                self.prime(gate, 101.)

    def test_stale_or_far_future_capture_timestamp_is_rejected(self):
        for stamp in (99.24, 100.101):
            gate = self.prime()
            self.assertFalse(gate.update(frame(4, stamp), 100.))
            self.assertEqual(gate.reason, 'stale_frame')

    def test_frame_age_limit_is_inclusive_and_stall_limit_is_exclusive(self):
        gate = PipelineReadiness()
        self.assertFalse(gate.update(frame(1, 99.25), 100.))
        self.assertEqual(gate.reason, 'warming_up')
        gate = self.prime()
        self.assertTrue(gate.update(frame(3, 100.08), 100.479))
        self.assertFalse(gate.update(frame(3, 100.08), 100.481))
        self.assertEqual(gate.reason, 'stream_stalled')

    def test_pid_change_and_counter_regressions_restart_confirmation(self):
        cases = (dict(pid=43), dict(sequence=1), dict(timestamp_seconds=100.01), dict(pose_count=1))
        for changes in cases:
            with self.subTest(changes=changes):
                gate = self.prime()
                state = frame(4, 100.12)
                state.update(changes)
                self.assertFalse(gate.update(state, 100.12))
                self.assertEqual(gate.reason, 'warming_up')
                pid = state['pid']
                self.assertFalse(gate.update(frame(5, 100.16, pid=pid), 100.16))
                self.assertTrue(gate.update(frame(6, 100.20, pid=pid), 100.20))

    def test_monotonic_clock_reversal_restarts_warmup(self):
        gate = self.prime()
        self.assertFalse(gate.update(frame(4, 99.), 99.))
        self.assertFalse(gate.update(frame(5, 99.04), 99.04))
        self.assertTrue(gate.update(frame(6, 99.08), 99.08))

    def test_slow_poll_after_stall_requires_reconfirmation_even_with_new_frame(self):
        gate = self.prime()
        self.assertFalse(gate.update(frame(50, 101.), 101.))
        self.assertFalse(gate.update(frame(51, 101.04), 101.04))
        self.assertTrue(gate.update(frame(52, 101.08), 101.08))

    def test_legacy_synthetic_without_pose_count_camera_or_phase(self):
        gate = PipelineReadiness()
        for i in range(3):
            state = dict(pid='offline', sequence=i, timestamp_seconds=100.+i*.04)
            self.assertEqual(gate.update(state, 100.+i*.04), i == 2)

    def test_disappearing_inference_counter_cannot_inherit_confirmation(self):
        gate = self.prime()
        for i in range(3):
            state = frame(4+i, 100.12+i*.04)
            del state['pose_count']
            self.assertEqual(gate.update(state, 100.12+i*.04), i == 2)

    def test_reset_reason_and_configuration_are_explicit(self):
        for settings in (dict(observations=2), dict(observations=True),
                         dict(warmup_seconds=0), dict(stall_seconds=0),
                         dict(max_frame_age=float('inf'))):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    PipelineReadiness(**settings)

    def test_invalid_numeric_fields_cannot_activate_or_keep_gate_open(self):
        invalid = (None, float('nan'), float('inf'), True, '100', -1)
        for key in ('sequence', 'pose_count', 'timestamp_seconds'):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    gate = self.prime()
                    state = frame(4, 100.12)
                    state[key] = value
                    self.assertFalse(gate.update(state, 100.12))
        self.assertFalse(self.prime().update(frame(4, 100.12), float('nan')))

    def test_reset_and_update_do_not_mutate_input(self):
        gate = self.prime()
        state = frame(4, 100.12)
        original = copy.deepcopy(state)
        self.assertTrue(gate.update(state, 100.12))
        self.assertEqual(state, original)
        gate.reset('service_unavailable')
        self.assertFalse(gate.ready)
        self.assertEqual(gate.reason, 'service_unavailable')
        self.assertFalse(gate.update(state, 100.12))


if __name__ == '__main__':
    unittest.main()
