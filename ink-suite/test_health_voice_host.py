"""Real host narration method with inert controls; no GTK, camera or audio."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


def host():
    source=ast.parse((Path(__file__).parent/'main.py').read_text(encoding='utf-8'))
    suite=next(row for row in source.body if isinstance(row,ast.ClassDef) and row.name=='Suite')
    method=next(row for row in suite.body if isinstance(row,ast.FunctionDef) and row.name=='sync_health_voice')
    namespace={};exec(compile(ast.Module(body=[method],type_ignores=[]),'main.py','exec'),namespace)
    value=NS(health_voice=Mock(),health_voice_marker=None)
    value.health_voice.status.return_value={'enabled':True,'message':'讲解已就绪'}
    value.health_view=NS(course_id='taichi',session=NS(session_id='new-session'),
        current={'phase':'practice','status':'running','lesson_id':'prepare','elapsed':0},set_voice_status=Mock())
    value.sync=lambda:namespace['sync_health_voice'](value)
    return value


class HealthVoiceHostTests(unittest.TestCase):
    def test_taichi_passes_explicit_course_without_disabling_narration(self):
        value=host();value.sync()
        value.health_voice.set_active.assert_called_once_with(True)
        shot=value.health_voice.sync.call_args.args[0]
        self.assertEqual(shot['course_id'],'taichi');self.assertEqual(shot['session_id'],'new-session')

    def test_full_demonstration_keeps_original_audio_exclusive(self):
        for course in ('baduanjin','taichi'):
            value=host();value.health_view.course_id=course;value.health_view.current['phase']='video'
            value.sync();value.health_voice.set_active.assert_called_once_with(False)
            value.health_voice.sync.assert_not_called()

    def test_pause_resume_does_not_toggle_off_and_repeat_old_sentence(self):
        value=host();value.sync()
        value.health_view.current.update(phase='paused',status='paused',elapsed=3);value.sync()
        value.health_view.current.update(phase='practice',status='running');value.sync()
        self.assertTrue(all(call.args==(True,) for call in value.health_voice.set_active.call_args_list))

    def test_baduanjin_rewind_keeps_existing_explicit_replay_semantics(self):
        value=host();value.health_view.course_id='baduanjin'
        value.health_view.current['elapsed']=5;value.sync()
        value.health_voice.set_active.reset_mock();value.health_view.current['elapsed']=0;value.sync()
        self.assertEqual([call.args for call in value.health_voice.set_active.call_args_list],[(True,),(False,),(True,)])


if __name__=='__main__':unittest.main()
