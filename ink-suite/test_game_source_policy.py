"""Source continuity is separate from body/control validity."""
import math
import unittest
from game_source_policy import SourceGapPolicy


def packet(t,n=None,pid=1,**fields):
    return dict(pid=pid,sequence=round(t*100) if n is None else n,
                timestamp_seconds=t,**fields)


class SourceGapTests(unittest.TestCase):
    def test_all_joints_missing_fresh_source_never_pauses(self):
        gate=SourceGapPolicy()
        for n in range(201):
            now=10+n*.05
            self.assertFalse(gate.update(packet(now,points17=None,available=False),now))
        self.assertEqual(gate.reason,'source_live')

    def test_two_seconds_without_any_packet_are_not_a_pause(self):
        gate=SourceGapPolicy();gate.update(packet(10),10)
        for now in (10.1,10.6,11.,11.99,12.):self.assertFalse(gate.update(None,now))
        self.assertTrue(gate.update(None,12.501))

    def test_cached_packet_cannot_renew_grace(self):
        gate=SourceGapPolicy();cached=packet(10)
        for now in (10.,10.5,11.5,12.5):self.assertFalse(gate.update(cached,now))
        self.assertTrue(gate.update(cached,12.501))

    def test_new_capture_only_does_not_hide_stalled_inference(self):
        gate=SourceGapPolicy();gate.update(packet(10,pose_count=1),10)
        for n in range(1,51):gate.update(packet(10+n*.05,pose_count=1),10+n*.05)
        self.assertTrue(gate.update(packet(12.6,pose_count=1),12.6))

    def test_camera_sequence_restart_with_new_inference_is_live(self):
        gate=SourceGapPolicy();gate.update(packet(10,n=800,pose_count=10),10)
        self.assertFalse(gate.update(packet(10.1,n=1,pose_count=11),10.1))
        self.assertEqual(gate.last_source,10.1)

    def test_retimestamping_equal_sequence_does_not_refresh(self):
        gate=SourceGapPolicy();gate.update(packet(10,n=1),10)
        gate.update(packet(11,n=1),11)
        self.assertEqual(gate.last_source,10.)

    def test_future_nan_invalid_identity_and_counters_do_not_poison(self):
        gate=SourceGapPolicy();gate.update(packet(10),10)
        for bad in (packet(99),packet(math.nan,n=1),packet(10.1,pid=False),packet(10.1,n=True),packet(10.1,pose_count=True)):
            gate.update(bad,10.2)
            self.assertEqual(gate.last_source,10.)
        gate.update(packet(10.3),10.3);self.assertEqual(gate.last_source,10.3)

    def test_backend_restart_and_retired_backend_do_not_pause_or_replay(self):
        gate=SourceGapPolicy();gate.update(packet(10),10)
        self.assertFalse(gate.update(packet(10.1,pid=2),10.1))
        gate.update(packet(10.2,pid=1),10.2)
        self.assertEqual(gate.last_source,10.1)

    def test_recovery_requires_actual_fresh_source_not_joint_validity(self):
        gate=SourceGapPolicy();gate.update(packet(10),10);self.assertTrue(gate.update(None,13))
        self.assertFalse(gate.update(packet(13.1,points17=[]),13.1))

    def test_offline_needs_no_source_and_invalid_time_is_safe(self):
        gate=SourceGapPolicy()
        self.assertFalse(gate.update(None,10,offline=True))
        self.assertTrue(gate.update(None,math.nan))


if __name__=='__main__':unittest.main()
