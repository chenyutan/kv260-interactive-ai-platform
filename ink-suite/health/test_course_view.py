"""Multi-course native flow; synthetic state and local report fixtures only."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from .view import HealthView


class CourseViewTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.widget=HealthView(lambda:None,report_directory=self.folder.name,video_enabled=False)
        self.addCleanup(self.widget.destroy)

    def test_taichi_entry_has_own_course_and_snapshot(self):
        self.widget.enter('taichi');self.widget.start('free')
        self.assertEqual(self.widget.course_id,'taichi')
        self.assertEqual(self.widget.current['course_id'],'taichi')
        self.assertEqual(self.widget.session.report()['kind'],'taichi_practice')
        self.assertEqual(self.widget.video_library.directory.name,'taichi-video')
        self.assertTrue(self.widget.current['reference_pose'])

    def test_history_filters_two_courses_in_same_report_folder(self):
        for course in ('baduanjin','taichi'):
            self.widget.enter(course);self.widget.start('free');self.widget.finish()
            self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(len(list(Path(self.folder.name).glob('*.json'))),2)
        for course in ('baduanjin','taichi'):
            self.widget.enter(course)
            self.assertEqual(len(self.widget._load_history()),1)

    def test_failed_old_report_is_saved_before_new_course_replaces_it(self):
        self.widget.enter('baduanjin');self.widget.start('free')
        with patch.object(self.widget.session,'save_report',side_effect=OSError('fixture storage unavailable')):
            self.widget.finish();self.assertTrue(self.widget.wait_for_saves());self.widget.enter('taichi')
        self.assertEqual(self.widget.course_id,'baduanjin')
        self.assertEqual(self.widget.pending_course_id,'taichi')
        self.assertTrue(self.widget.pending_recovery)
        self.widget.action('health_retry_save')
        self.assertTrue(self.widget.wait_for_saves())
        self.assertEqual(self.widget.course_id,'taichi')
        self.assertIsNone(self.widget.session)
        self.assertFalse(self.widget.pending_recovery)
        records=[json.loads(p.read_text(encoding='utf8')) for p in Path(self.folder.name).glob('*.json')]
        self.assertEqual(len(records),1);self.assertEqual(records[0]['kind'],'baduanjin_practice')

    def test_taichi_completion_does_not_display_zero_as_failed_repetitions(self):
        import cairo
        self.widget.enter('taichi');self.widget.start('free');self.widget.finish()
        text=[]
        with patch.object(self.widget,'_text',side_effect=lambda cr,value,*a,**k:text.append(str(value))):
            self.widget._completed(cairo.Context(cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)))
        self.assertIn('已记录',text);self.assertNotIn('0 次',text)

    def test_unknown_course_rejected_before_replacing_active_course(self):
        self.widget.enter('baduanjin')
        with self.assertRaises(ValueError):self.widget.enter('made-up-course')
        self.assertEqual(self.widget.course_id,'baduanjin')

    def test_many_reports_from_another_course_cannot_hide_own_history(self):
        for own,other in (('baduanjin','taichi'),('taichi','baduanjin')):
            self.widget.enter(own);self.widget.start('free');self.widget.finish()
            self.assertTrue(self.widget.wait_for_saves())
            report=json.loads(Path(self.widget.report_path).read_text(encoding='utf8'))
            other_prefix='taichi-' if other=='taichi' else ''
            for i in range(30):
                path=Path(self.folder.name)/(other_prefix+'newer-'+str(i)+'.json')
                data=dict(report,kind=other+'_practice',course_id=other)
                path.write_text(json.dumps(data),encoding='utf8')
                os.utime(path,(2000000000+i,2000000000+i))
            self.assertEqual(len(self.widget._load_history()),1)
            for path in Path(self.folder.name).glob('*.json'):path.unlink()


if __name__=='__main__':unittest.main()
