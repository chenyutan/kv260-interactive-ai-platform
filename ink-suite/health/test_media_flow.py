"""Independent media-gate regressions; only fake player and synthetic metadata."""
import copy
import json
import tempfile
from pathlib import Path
import unittest

from .catalog import COURSE
from .engine import LessonSession
from .media_flow import LessonVideoFlow, LessonVideos


class FakeClock:
    def __init__(self):
        self.now = 100.
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class FakePlayer:
    def __init__(self):
        self.state = 'idle'
        self.operations = []
        self.path = None
    def play(self, path):
        self.state, self.path = 'playing', path
        self.operations.append(('play', path))
    def stop(self):
        self.state = 'stopped'
        self.operations.append(('stop',))
    def pause(self):
        self.state = 'paused'
        self.operations.append(('pause',))
    def resume(self):
        self.state = 'playing'
        self.operations.append(('resume',))
    def snapshot(self):
        return dict(status=self.state, state=self.state, position_seconds=0.,
                    duration_seconds=10., muted=True)


class FakeLibrary:
    def lookup(self, identifier):
        return dict(id=identifier, local_path='/local/{}.webm'.format(identifier))


class MediaFlowTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.session = LessonSession('learn', clock=self.clock)
        self.session.start()
        self.player = FakePlayer()
        self.library = FakeLibrary()
        self.flow = LessonVideoFlow(self.session, self.player, self.library)

    def test_video_gates_every_mode_before_practice(self):
        for mode in ('learn', 'follow', 'free'):
            session = LessonSession(mode, clock=self.clock)
            session.start()
            flow = LessonVideoFlow(session, self.player, self.library)
            shot = flow.snapshot()
            self.assertEqual(shot['phase'], 'video')
            self.assertEqual(session.status, 'paused')
            self.assertEqual(list(flow.opened), ['prepare'])

    def test_video_never_advances_practice_clock_or_observations(self):
        self.flow.snapshot()
        before = self.session.report()
        for _ in range(20):
            self.clock.advance(5)
            shot = self.flow.update(dict(points17=[[.5,.5,.9]]*17))
            self.assertEqual(shot['phase'], 'video')
            self.assertEqual(shot['pose_points'], [None]*17)
        after = self.session.report()
        for key in ('active_seconds','observed_seconds','observed_repetitions'):
            self.assertEqual(before[key], after[key])

    def test_eos_enters_original_diagram_before_learn_practice(self):
        self.flow.snapshot()
        self.clock.advance(10)
        self.player.state = 'ended'
        shot = self.flow.snapshot()
        self.assertEqual(shot['phase'], 'demo')
        self.assertEqual(shot['elapsed'], 0)
        self.assertEqual(list(self.flow.finished), ['prepare'])
        self.assertEqual(list(self.flow.skipped), [])
        self.clock.advance(COURSE[0]['demo_seconds'])
        self.assertEqual(self.flow.snapshot()['phase'], 'practice')

    def test_skip_is_explicit_not_recorded_as_watched(self):
        self.flow.snapshot()
        shot = self.flow.action('video_skip')
        self.assertEqual(shot['phase'], 'demo')
        self.assertEqual(list(self.flow.skipped), ['prepare'])
        self.assertEqual(list(self.flow.finished), [])

    def test_repeated_polling_does_not_restart_same_video(self):
        for _ in range(20):
            self.clock.advance(.05)
            self.flow.snapshot()
        self.assertEqual(len([x for x in self.player.operations if x[0]=='play']),1)

    def test_video_pause_resume_controls_only_player(self):
        self.flow.snapshot()
        self.assertEqual(self.flow.action('pause')['status'],'paused')
        self.assertEqual(self.player.state,'paused')
        self.clock.advance(60)
        self.assertEqual(self.flow.action('resume')['status'],'running')
        self.assertEqual(self.player.state,'playing')
        self.assertEqual(self.session.status,'paused')
        self.assertEqual(self.session.session_elapsed,0)

    def test_next_during_video_gates_new_lesson_without_counting_time(self):
        self.flow.snapshot()
        self.clock.advance(60)
        shot = self.flow.action('next')
        self.assertEqual(shot['lesson_id'],'raise')
        self.assertEqual(shot['phase'],'video')
        self.assertEqual(list(self.flow.opened),['prepare','raise'])
        self.assertEqual(self.session.session_elapsed,0)

    def test_repeat_and_previous_replay_matching_video(self):
        self.flow.snapshot()
        self.flow.action('next')
        self.flow.action('repeat')
        shot = self.flow.action('previous')
        self.assertEqual(shot['lesson_id'],'prepare')
        self.assertEqual(list(self.flow.opened),['prepare','raise','raise','prepare'])
        self.assertEqual(self.player.path,'/local/prepare.webm')

    def test_rewatch_freezes_and_restores_current_practice_position(self):
        self.flow.snapshot()
        self.flow.action('video_skip')
        self.clock.advance(3)
        self.flow.snapshot()
        self.assertEqual(self.session.phase_elapsed,3)
        shot = self.flow.action('video_rewatch')
        self.assertEqual(shot['phase'],'video')
        self.clock.advance(300)
        self.flow.snapshot()
        self.assertEqual(self.session.phase_elapsed,3)
        shot = self.flow.action('video_skip')
        self.assertEqual(shot['phase'],'demo')
        self.assertEqual(shot['elapsed'],3)
        self.assertEqual(self.session.session_elapsed,3)

    def test_automatic_lesson_transition_gates_before_next_lesson_advances(self):
        self.flow.snapshot()
        self.flow.action('video_skip')
        self.clock.advance(COURSE[0]['demo_seconds']+COURSE[0]['practice_seconds'])
        shot = self.flow.snapshot()
        self.assertEqual(shot['phase'],'video')
        self.assertEqual(shot['lesson_id'],'raise')
        self.assertEqual(self.session.phase_elapsed,0)
        self.clock.advance(120)
        self.flow.snapshot()
        self.assertEqual(self.session.phase_elapsed,0)

    def test_long_event_loop_gap_cannot_skip_intermediate_teaching_videos(self):
        self.flow.snapshot()
        self.flow.action('video_skip')
        self.clock.advance(200)
        shot = self.flow.snapshot()
        self.assertEqual(shot['lesson_id'],'raise')
        self.assertEqual(shot['phase'],'video')
        self.assertEqual(list(self.flow.opened),['prepare','raise'])
        expected=COURSE[0]['demo_seconds']+COURSE[0]['practice_seconds']
        self.assertEqual(self.session.session_elapsed,expected)
        self.assertEqual(self.session.phase_elapsed,0)

    def test_missing_video_never_silently_skips_or_advances(self):
        self.library.lookup = lambda identifier:None
        shot = self.flow.snapshot()
        self.assertEqual(shot['phase'],'video')
        self.assertEqual(shot['status'],'paused')
        self.assertIn('暂不可用',shot['video']['error_message'])
        self.clock.advance(120)
        self.flow.snapshot()
        self.assertEqual(self.session.session_elapsed,0)
        self.assertEqual(self.flow.action('video_skip')['phase'],'demo')

    def test_playback_error_waits_for_retry_or_skip(self):
        self.flow.snapshot()
        self.player.state = 'error'
        shot = self.flow.snapshot()
        self.assertEqual(shot['status'],'paused')
        self.assertIn('播放失败',shot['video']['error_message'])
        self.clock.advance(120)
        self.assertEqual(self.flow.action('resume')['status'],'paused')
        shot = self.flow.action('video_retry')
        self.assertEqual(shot['status'],'running')
        self.assertIsNone(shot['video']['error_message'])
        self.assertEqual(self.session.session_elapsed,0)

    def test_manual_finish_stops_video_and_never_reopens_completed_course(self):
        self.flow.snapshot()
        shot = self.flow.action('finish')
        self.assertEqual(shot['status'],'completed')
        self.assertEqual(self.player.state,'stopped')
        self.assertEqual(self.flow.action('video_rewatch')['status'],'completed')
        self.assertEqual(list(self.flow.opened),['prepare'])

    def test_report_metadata_contains_no_pose_or_media_bytes(self):
        self.flow.snapshot()
        self.flow.action('video_skip')
        report = self.flow.report_metadata()
        self.assertFalse(report['watching_included_in_practice_seconds'])
        self.assertEqual(report['opened'],['prepare'])
        self.assertEqual(report['skipped'],['prepare'])
        self.assertNotIn('points17',report)
        report['opened'].append('untrusted')
        self.assertEqual(list(self.flow.opened),['prepare'])


class MediaLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory/'prepare.webm').write_bytes(b'QA only; not teaching video')

    def manifest(self, data):
        (self.directory/'manifest.json').write_text(json.dumps(data),encoding='utf8')
        return LessonVideos(self.directory)

    def test_known_local_manifest_maps_only_present_file(self):
        library = self.manifest({'clips':[{'id':'prepare','path':'prepare.webm'}]})
        self.assertTrue(Path(library.lookup('prepare')['local_path']).is_file())
        self.assertIsNone(library.lookup('raise'))

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        for path in ('../outside.webm',str(self.directory/'prepare.webm')):
            library=self.manifest({'clips':[{'id':'prepare','path':path}]})
            self.assertIsNone(library.lookup('prepare'))

    def test_malformed_manifest_becomes_missing_media(self):
        for data in ([],None,'bad',{}, {'clips':None}, {'clips':1}, {'clips':['bad',None]}):
            library = self.manifest(data)
            self.assertIsNone(library.lookup('prepare'))

    def test_malformed_path_becomes_missing_media(self):
        for path in (None,1,'bad\x00video.webm'):
            library = self.manifest({'clips':[{'id':'prepare','path':path}]})
            self.assertIsNone(library.lookup('prepare'))

    def test_absent_or_invalid_json_is_missing_media(self):
        self.assertIsNone(LessonVideos(self.directory).lookup('prepare'))
        (self.directory/'manifest.json').write_text('broken json',encoding='utf8')
        self.assertIsNone(LessonVideos(self.directory).lookup('prepare'))


if __name__=='__main__':unittest.main()
