"""Timing and ownership guards; no GTK, photo processing or network required."""
import unittest
from photo_wait import PhotoWaitState

def job(status='processing',stage='painting',identifier='one',elapsed=27):
    return dict(id=identifier,status=status,stage=stage,elapsed_s=elapsed)

class WaitStateTests(unittest.TestCase):
    def test_stage_is_worker_reported_even_when_elapsed_is_large(self):
        state=PhotoWaitState();state.update(job(stage='loading_model',elapsed=170),now=1)
        self.assertEqual(state.stage,'loading_model');self.assertEqual(state.elapsed,170)
        self.assertTrue(state.animated(1000))

    def test_fresh_completed_history_does_not_celebrate(self):
        state=PhotoWaitState();state.update(job('completed'),preview_ready=True,now=1)
        self.assertFalse(state.animated(1));self.assertIsNone(state.reveal_at)

    def test_completed_result_displays_without_delay_and_reveal_is_bounded(self):
        state=PhotoWaitState();state.update(job(),now=1)
        state.update(job('completed'),preview_ready=True,now=4)
        self.assertFalse(state.waiting);self.assertTrue(state.animated(4))
        self.assertFalse(state.animated(5.61))
        state.update(job('completed'),preview_ready=True,now=7)
        self.assertEqual(state.reveal_at,4)

    def test_completion_waits_for_preview_not_over_old_source(self):
        state=PhotoWaitState();state.update(job(),now=1)
        state.update(job('completed'),preview_ready=False,now=4)
        self.assertFalse(state.animated(4));self.assertTrue(state.completed_pending)
        state.update(job('completed'),preview_ready=True,now=9)
        self.assertEqual(state.reveal_at,9)

    def test_import_cannot_celebrate_previous_completed_work(self):
        state=PhotoWaitState();state.update(job('completed'),busy=True,preview_ready=True,now=1)
        state.update(job('completed'),preview_ready=True,now=2)
        self.assertFalse(state.animated(2))

    def test_failure_and_removed_job_stop_motion_immediately(self):
        for end in (job('failed'),None):
            state=PhotoWaitState();state.update(job(),now=1);state.update(end,now=2)
            self.assertFalse(state.animated(2));self.assertIsNone(state.reveal_at)

    def test_changed_identity_does_not_inherit_reveal(self):
        state=PhotoWaitState();state.update(job(),now=1)
        state.update(job('completed',identifier='another'),preview_ready=True,now=2)
        self.assertFalse(state.animated(2))

    def test_elapsed_is_reported_not_interpolated(self):
        state=PhotoWaitState();state.update(job(elapsed=15),now=1)
        self.assertTrue(state.animated(80));self.assertEqual(state.elapsed,15)
        state.update(job(elapsed=-1),now=81);self.assertEqual(state.elapsed,0)

if __name__=='__main__':unittest.main(verbosity=2)
