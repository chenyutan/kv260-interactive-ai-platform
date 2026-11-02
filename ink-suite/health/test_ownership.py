"""Late reports stay with the participant captured when practice began."""
import unittest
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from .ownership import PracticeOwnership

class Store:
    def __init__(self,root=None):self.rows=[];self.fail=False;self.root=root
    def record_health(self,visitor,report):
        if self.fail:raise OSError('temporary')
        self.rows.append((visitor,report['session_id']))

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();self.owner=PracticeOwnership(self.store)
        self.owner.kick=lambda:True  # explicitly drain at controlled boundaries
    def report(self,ident,**extra):
        return dict(session_id=ident,report_path='/private/report.json',course_id='baduanjin',kind='baduanjin_practice',
                    mode='follow',active_seconds=4,lessons=[],**extra)
    def test_late_first_participant_never_uses_second_owner(self):
        self.owner.bind('1'*32,'a'*32);self.owner.bind('2'*32,'b'*32)
        self.owner.saved(self.report('2'*32));self.owner.saved(self.report('1'*32));self.owner.drain()
        self.assertEqual(self.store.rows,[('b'*32,'2'*32),('a'*32,'1'*32)])
    def test_missing_owner_and_unsaved_report_never_become_current_user(self):
        self.assertFalse(self.owner.saved(self.report('1'*32)))
        self.owner.bind('1'*32,'a'*32)
        self.assertFalse(self.owner.saved(dict(session_id='1'*32)))
        self.assertFalse(self.owner.saved(self.report('1'*32,report_save_error='busy')))
        self.assertEqual(self.store.rows,[])
    def test_owner_cannot_be_reassigned_after_restart(self):
        self.assertTrue(self.owner.bind('1'*32,'a'*32))
        self.assertFalse(self.owner.bind('1'*32,'b'*32))
        self.owner.saved(self.report('1'*32));self.owner.drain()
        self.assertEqual(self.store.rows,[('a'*32,'1'*32)])
    def test_retry_retains_captured_owner_and_report(self):
        self.owner.bind('1'*32,'a'*32);report=self.report('1'*32)
        self.store.fail=True;self.owner.saved(report);report['session_id']='2'*32
        self.assertFalse(self.owner.drain());self.assertEqual(len(self.owner.pending),1)
        self.store.fail=False;self.assertTrue(self.owner.drain())
        self.assertEqual(self.store.rows,[('a'*32,'1'*32)]);self.assertFalse(self.owner.pending)

    def test_restart_retries_fixed_owner_without_current_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));store.fail=True
            owner=PracticeOwnership(store,autostart=False);owner.kick=lambda:True
            owner.bind('1'*32,'a'*32);owner.saved(self.report('1'*32));self.assertFalse(owner.drain())
            self.assertTrue((store.root/'health-pending.json').is_file())
            restored=PracticeOwnership(store,autostart=False)
            self.assertFalse(restored.bind('1'*32,'b'*32));store.fail=False
            self.assertTrue(restored.drain());self.assertEqual(store.rows,[('a'*32,'1'*32)])
            self.assertEqual(json.loads((store.root/'health-pending.json').read_text())['pending'],[])

    def test_queue_excludes_raw_private_fields_and_does_not_import_legacy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));owner=PracticeOwnership(store,autostart=False);owner.kick=lambda:True
            (store.root/'old-course-report.json').write_text(json.dumps(self.report('2'*32)))
            owner.bind('1'*32,'a'*32);owner.saved(self.report('1'*32,name='PRIVATE NAME',raw_pose=[[1,2]],sources=['PRIVATE SOURCE']))
            raw=(store.root/'health-pending.json').read_text()
            for secret in ('PRIVATE NAME','raw_pose','PRIVATE SOURCE','/private/'):self.assertNotIn(secret,raw)
            restored=PracticeOwnership(store,autostart=False)
            self.assertEqual(set(restored.pending),{'1'*32});self.assertNotIn('2'*32,restored.owners)

    def test_flush_timeout_keeps_durable_queue_then_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));entered=threading.Event();release=threading.Event()
            original=store.record_health
            def held(*args):entered.set();release.wait(3);original(*args)
            store.record_health=held
            owner=PracticeOwnership(store,autostart=False);owner.bind('1'*32,'a'*32)
            try:
                owner.saved(self.report('1'*32));self.assertTrue(entered.wait(1))
                before=time.monotonic();self.assertFalse(owner.flush(timeout=.02))
                self.assertLess(time.monotonic()-before,.5)
                self.assertEqual(len(json.loads((store.root/'health-pending.json').read_text())['pending']),1)
            finally:
                release.set()
                if owner._worker:owner._worker.join(2)
            self.assertTrue(owner.flush(timeout=.2));self.assertEqual(store.rows,[('a'*32,'1'*32)])

    def test_committed_but_uncleaned_queue_is_safe_to_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            from visitor.store import VisitorStore
            from health.engine import LessonSession
            store=VisitorStore(Path(directory)/'visitors');visitor=store.current()
            lesson=LessonSession(mode='follow');lesson.start();raw=lesson.report();raw['report_path']='/private/report.json'
            owner=PracticeOwnership(store,autostart=False);owner.kick=lambda:True
            owner.bind(lesson.session_id,visitor['id']);owner.saved(raw)
            with patch.object(owner,'_persist_locked',side_effect=OSError('disk busy')):self.assertFalse(owner.drain())
            self.assertEqual(store.health_summary(visitor['token'])['report_count'],1)
            restored=PracticeOwnership(store,autostart=False);self.assertTrue(restored.drain())
            self.assertEqual(store.health_summary(visitor['token'])['report_count'],1)

    def test_invalid_persisted_owner_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'health-pending.json'
            path.write_text(json.dumps(dict(schema=1,pending=[dict(owner='current',practice_id='1'*32,report=self.report('1'*32))])))
            owner=PracticeOwnership(Store(Path(directory)),autostart=False)
            self.assertFalse(owner.pending);self.assertFalse(owner.owners);self.assertIsNotNone(owner.last_error)

if __name__=='__main__':unittest.main()
