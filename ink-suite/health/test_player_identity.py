"""Practice history follows explicit source/visitor context, never player leases."""
import copy,unittest
from .engine import LessonSession
from .test_engine import Clock,person,raised

DEFAULT=object()
class HealthPlayerTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.sequence=0;self.session=LessonSession('free',clock=self.clock)
        self.session.action('next');self.session.start()
    def feed(self,pose,identity='owner-a',epoch=1,allowed=True,seconds=.35,
             pid=31,visitor_session='visitor-a',tracking=DEFAULT,**extra):
        for _ in range(round(seconds/.05)):
            self.clock.advance(.05);self.sequence+=1
            lease=dict(identity=identity,epoch=epoch,input_allowed=allowed) if tracking is DEFAULT else tracking
            state=dict(pid=pid,sequence=self.sequence,timestamp_seconds=self.clock(),points17=copy.deepcopy(pose),
                player_tracking=lease,visitor_session=visitor_session,**extra)
            shot=self.session.update(state)
        return shot
    def test_same_source_and_visitor_counts_normal_cycle(self):
        self.feed(person());self.feed(raised());shot=self.feed(person());self.assertEqual(shot['repetition'],1)
    def test_lease_epoch_change_does_not_break_cycle(self):
        self.feed(person());self.feed(raised());self.assertEqual(self.feed(person(),epoch=4)['repetition'],1)
    def test_person_identity_metadata_does_not_break_cycle(self):
        self.feed(person());self.feed(raised())
        self.assertEqual(self.feed(person(),identity='other',person_id='other',track_id='other')['repetition'],1)
    def test_false_tracking_gate_does_not_block_observed_time_or_count(self):
        self.feed(person());before=self.session.report()['observed_seconds']
        self.feed(raised(),allowed=False);shot=self.feed(person(),allowed=False)
        self.assertEqual(shot['repetition'],1);self.assertGreater(self.session.report()['observed_seconds'],before)
    def test_malformed_tracking_metadata_is_ignored(self):
        for index,invalid in enumerate((None,{},[],dict(input_allowed=False,epoch=True))):
            with self.subTest(invalid=invalid):
                self.feed(person(),tracking=invalid);self.feed(raised(),tracking=invalid)
                self.assertEqual(self.feed(person(),tracking=invalid)['repetition'],index+1)
    def test_local_visitor_change_clears_previous_endpoint(self):
        self.feed(person());self.feed(raised());self.assertEqual(self.feed(person(),visitor_session='visitor-b')['repetition'],0)
        self.feed(raised(),visitor_session='visitor-b');self.assertEqual(self.feed(person(),visitor_session='visitor-b')['repetition'],1)
    def test_backend_pid_change_clears_previous_endpoint(self):
        self.feed(person());self.feed(raised());self.assertEqual(self.feed(person(),pid=32)['repetition'],0)
        self.feed(raised(),pid=32);self.assertEqual(self.feed(person(),pid=32)['repetition'],1)
    def test_real_unavailable_frames_still_cannot_count(self):
        self.feed(person());before=self.session.report()['observed_seconds']
        self.feed(raised(),available=False);shot=self.feed(person(),available=False)
        self.assertEqual(shot['repetition'],0);self.assertEqual(self.session.report()['observed_seconds'],before)

if __name__=='__main__':unittest.main()