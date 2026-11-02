"""Project entry/return, clear races and request context, without real devices."""
import ast,threading,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from ai_host import AIHost
from action_catalog import page
from section_catalog import CONTENT


class AIProjectHostTests(unittest.TestCase):
    def setUp(self):
        from test_ai_host import AIHostTests
        self.base=AIHostTests('test_poll_preserves_qr_and_never_resubmits');self.base.setUp()
        self.h=self.base.host
        def clear_all():
            count=len(self.h.jobs);self.h.jobs.clear()
            return dict(cleared=count,remaining=0,scope='terminal_all',jobs=[])
        self.h.ai_service.clear_all=Mock(side_effect=clear_all)
    def tearDown(self):self.base.tearDown()
    def enter(self,project='fruit'):
        self.h.open_ai('guide',project);self.base.worker();self.base.done()
    def action_done(self):self.base.worker();self.base.done()

    def test_default_page_and_project_request_are_explicit(self):
        self.h.menu.section='health';self.h.open_ai();self.base.worker();self.base.done()
        self.assertEqual((self.h.ai_view.kind,self.h.ai_view.project),('guide','baduanjin'))
        self.h.ai_action('ai_guide:controls');self.action_done()
        self.h.ai_service.submit.assert_called_once_with('token-a','guide','controls',project='baduanjin')

    def test_clear_then_delayed_old_read_cannot_restore_history(self):
        self.enter();self.h.ai_refresh();self.base.worker()
        self.h.ai_action('ai_clear')
        self.base.done();self.action_done()
        self.assertEqual(self.h.ai_view.jobs,());self.h.ai_service.clear_all.assert_called_once_with()
        self.assertFalse(self.h.requests)

    def test_queued_host_submission_is_canceled_by_clear(self):
        self.enter();self.h.ai_action('ai_guide:rules');self.h.ai_action('ai_clear')
        self.action_done();self.action_done()
        self.h.ai_service.submit.assert_not_called();self.h.ai_service.clear_all.assert_called_once()
        self.assertFalse(self.h._ai_working);self.assertEqual(self.h.ai_view.jobs,())

    def test_periodic_refresh_cannot_read_old_jobs_during_clear(self):
        self.enter();self.h.ai_action('ai_clear');epoch=self.h._ai_epoch
        self.assertTrue(self.h._ai_clearing)
        pending=len(self.base.work)
        for _ in range(5):self.h.ai_refresh()
        self.assertEqual(len(self.base.work),pending)
        self.action_done()
        self.assertFalse(self.h._ai_clearing);self.assertGreater(self.h._ai_epoch,epoch)
        self.assertEqual(self.h.ai_view.jobs,())

    def test_repeated_clear_during_same_request_is_ignored(self):
        self.enter();self.h.ai_action('ai_clear');pending=len(self.base.work)
        self.h.ai_action('ai_clear');self.assertEqual(len(self.base.work),pending)
        self.assertTrue(self.h.ai_view.clearing)
        self.action_done();self.h.ai_service.clear_all.assert_called_once()
        self.assertFalse(self.h.ai_view.clearing)
        self.base.worker();self.base.done()
        self.assertEqual(self.h.ai_view.jobs,())

    def test_clear_rejects_switched_visitor_before_mutation(self):
        self.enter();self.h.ai_action('ai_clear')
        self.h.session=dict(id='visitor-b',token='token-b',status='active');self.action_done()
        self.h.ai_service.clear_all.assert_not_called()

    def test_open_health_clears_previous_visitor_summary_before_poll(self):
        self.h.ai_view.set_context(health_summary=dict(course_id='baduanjin',report_count=9,summary='old'))
        self.h.open_ai('health','baduanjin')
        self.assertEqual(self.h.ai_view.health_summary,{})

    def test_health_request_captures_course(self):
        self.h.ai_service.health_snapshot=Mock(return_value=dict(course_id='taichi',report_count=0))
        self.h.open_ai('health','taichi');self.action_done()
        self.h.ai_action('ai_health:next');self.h.ai_view.set_context(project='baduanjin');self.action_done()
        self.h.ai_service.submit.assert_called_once_with('token-a','health','next',project='taichi')

    def test_clear_partial_backend_does_not_claim_success(self):
        self.enter();self.h.ai_service.clear_all.return_value=dict(cleared=1,remaining=1)
        self.h.ai_service.clear_all.side_effect=None
        self.h.ai_action('ai_clear');self.action_done()
        self.assertIn('尚未全部清空',self.h.ai_view.notice)

    def test_submission_does_not_run_twice_and_snapshots_project(self):
        self.enter('pal');self.h.ai_action('ai_guide:tips');self.h.ai_action('ai_guide:tips')
        self.h.ai_view.set_context(project='runner');self.action_done()
        self.h.ai_service.submit.assert_called_once_with('token-a','guide','tips',project='pal')

    def test_return_to_game_actions_keeps_same_child_paused(self):
        self.h.menu_level='actions';self.h.mode='fruit';self.h.child=object();self.h.action_page='options'
        self.h.show_action_page=Mock();self.h.open_project_ai('fruit');self.h.close_ai()
        self.h.show_action_page.assert_called_once_with('options');self.assertFalse(self.h.requests)

    def test_return_to_control_choice_preserves_parent(self):
        self.h.menu_level='control';self.h.control_return_main=True;self.h.control_return_section='entertainment'
        self.h.open_project_ai('runner');self.h.close_ai()
        self.h.open_control_choice.assert_called_once_with('runner')
        self.assertEqual(self.h.control_return_section,'entertainment')

    def test_health_guidance_keeps_session_and_returns_paused(self):
        session=object();self.h.health_active=True;self.h.menu_level='health'
        self.h.health_view=NS(session=session,pause=Mock(),action=Mock());self.h.health_voice=NS(set_active=Mock())
        self.h.open_project_ai('taichi');self.assertFalse(self.h.health_active)
        self.h.health_view.pause.assert_called_once();self.h.health_view.action.assert_not_called()
        self.h.close_ai();self.assertTrue(self.h.health_active);self.assertEqual(self.h.menu_level,'health')
        self.assertIs(self.h.health_view.session,session);self.h.health_view.action.assert_not_called()

    def test_home_abandon_from_ai_finishes_suspended_course_once(self):
        self.h.health_active=True;self.h.menu_level='health';self.h.control_mode='body';self.h.health_previous_control='gamepad'
        self.h.health_view=NS(session=object(),pause=Mock(),action=Mock())
        self.h.open_project_ai('baduanjin');self.h.ai_abandon_context();self.h.ai_abandon_context()
        self.h.health_view.action.assert_called_once_with('health_home');self.assertEqual(self.h.control_mode,'gamepad')

    def test_photo_round_trip_does_not_resubmit_or_reset_selected_file(self):
        self.h.menu_level='photo';self.h.photo_view=NS(leave=Mock(),return_from_ai=Mock(),selected='synthetic.png')
        self.h.open_project_ai('photo_ink');self.h.close_ai()
        self.assertEqual(self.h.photo_view.selected,'synthetic.png');self.assertEqual(self.h.menu_level,'photo')
        self.h.photo_view.leave.assert_called_once();self.h.photo_view.return_from_ai.assert_called_once()
        self.h.ai_service.submit.assert_not_called()

    def test_every_game_has_ai_entry_in_options_without_extra_slots(self):
        for project in CONTENT['entertainment']+('ink',):
            entries=page(project,'options')['items']
            self.assertIn('ai_assistant',[r['id'] for r in entries]);self.assertLessEqual(len(entries),6)


if __name__=='__main__':unittest.main()
