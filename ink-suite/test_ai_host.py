"""Actual AI host/view/selector integration with queued synthetic workers.

No camera, visitor disk, real network, credentials or board are accessed.
"""
import io
import json
import threading
import unittest
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import gi
gi.require_version('Gtk','3.0')
import cairo
import ai_host
from ai_host import AIHost
from ai_view import AIView
from gesture_menu import DwellSelector
from action_catalog import page,CONFIRM
from experience_names import TITLES
import section_catalog


def make_job():
    return dict(id='a'*32,kind='creation',status='ready',preset='bamboo',result=dict(
        title='竹林与远山',summary='两步完成构图',steps=[
            dict(theme='landscape',action='A01',instruction='双臂展开'),
            dict(theme='bamboo',action='A02',instruction='单臂上举')]))


class Harness(AIHost):
    def __init__(self):
        self.stop=threading.Event();self.switching=False;self.pending_action=None;self.visitor_busy=False
        self.navigation_token=1;self.generation=1;self.menu_level='main';self.menu_open=True
        self.menu=NS(section='creation');self.latest=None;self.ai_session=None
        self.selector=DwellSelector();self.stack=NS(set_visible_child_name=Mock())
        self.sync_menu_music=Mock();self.open_control_choice=Mock()
        self.ai_view=AIView(self.ai_action)
        self.session=dict(id='visitor-a',token='token-a',status='active')
        self.visitor_store=NS(current=lambda:dict(self.session))
        self.jobs=[make_job()]
        self.ai_service=NS(capabilities=lambda:{'configured':True},list_jobs=lambda token:deepcopy(self.jobs),
            get=Mock(side_effect=lambda token,ident:deepcopy(self.jobs[0])),submit=Mock(return_value={'cache_hit':False}))
        png=io.BytesIO();cairo.ImageSurface(cairo.FORMAT_RGB24,32,32).write_to_png(png)
        self.visitor_portal=NS(share=Mock(return_value={'url':'http://lan/v/token-a','qr':png.getvalue()}))
        self.backend_lock=threading.Lock();self.args=NS(base='http://fake.local')
        self.requests=[]
        def request(req,timeout):
            self.requests.append(json.loads(req.data));return io.BytesIO(b'{"accepted":true}')
        self.opener=NS(open=Mock(side_effect=request))

    def open_menu(self,main=False):
        self.navigation_token+=1;self.menu_level='main'
        self.selector.set_choices(('section_creation',),((0.,0.,1.,1.),))


class AIHostTests(unittest.TestCase):
    def setUp(self):
        self.work=[];self.callbacks=[];self.host=Harness()
        def thread(*,target,**kwargs):return NS(start=lambda:self.work.append(target))
        self.patchers=[patch.object(ai_host,'threading',NS(Thread=thread,RLock=threading.RLock)),
            patch.object(ai_host,'GLib',NS(idle_add=lambda callback:self.callbacks.append(callback)))]
        for p in self.patchers:p.start()

    def tearDown(self):
        self.host.ai_view.destroy()
        for p in reversed(self.patchers):p.stop()

    def worker(self):self.work.pop(0)()
    def done(self):self.callbacks.pop(0)()
    def refresh(self):
        self.host.ai_refresh();self.worker();self.done()
    def enter(self):
        self.host.open_ai('creation');self.worker();self.done()

    def test_poll_preserves_qr_and_never_resubmits(self):
        self.enter();qr=self.host.ai_view.qr
        self.assertIsNotNone(qr)
        self.refresh();self.refresh()
        self.assertEqual(self.host.ai_view.share_url,'http://lan/v/token-a')
        self.assertIs(self.host.ai_view.qr,qr)
        self.host.ai_service.submit.assert_not_called()
        self.host.visitor_portal.share.assert_called_once()

    def test_reentry_restores_ai_selector_and_clears_previous_visitor_qr(self):
        self.enter();self.host.open_menu(main=True);self.host.open_ai()
        self.assertEqual(self.host.selector.choices,self.host.ai_view.choices)
        self.assertNotIn('section_creation',self.host.selector.choices)
        self.assertIsNone(self.host.ai_view.share_url);self.assertIsNone(self.host.ai_view.qr)

    def test_reentry_closes_previous_phone_page(self):
        self.enter();self.host.ai_select('ai_phone')
        self.assertTrue(self.host.ai_view.phone_open)
        self.host.open_menu(main=True);self.host.open_ai()
        self.assertFalse(self.host.ai_view.phone_open)
        self.assertIn('ai_phone',self.host.ai_view.choices)
        self.assertNotIn('ai_phone_close',self.host.ai_view.choices)
        self.assertEqual(self.host.selector.choices,self.host.ai_view.choices)

    def test_refresh_completion_cannot_reopen_or_overwrite_departed_page(self):
        self.enter();self.host.ai_refresh();self.worker()
        self.host.open_menu(main=True);before=self.host.ai_view.notice
        self.done()
        self.assertEqual(self.host.menu_level,'main');self.assertEqual(self.host.ai_view.notice,before)
        self.assertFalse(self.host._ai_reading)

    def test_old_visit_callback_cannot_fill_new_visit_page(self):
        self.host.open_ai();self.worker()
        self.host.open_menu(main=True);self.host.session=dict(id='visitor-b',token='token-b',status='active')
        self.host.open_ai();self.done()
        self.assertIsNone(self.host.ai_session);self.assertFalse(self.host.ai_view.jobs)
        self.host.ai_refresh();self.worker();self.done()
        self.assertEqual(self.host.ai_session['id'],'visitor-b')

    def test_apply_selected_step_only_changes_theme_before_control_choice(self):
        self.enter();self.host.ai_select('ai_step:1');self.host.ai_select('ai_apply_selected')
        self.assertEqual(len(self.work),1)
        self.worker();self.done()
        self.assertEqual(self.host.requests,[dict(command='theme',theme='bamboo')])
        self.host.ai_service.get.assert_called_once_with('token-a','a'*32)
        self.host.open_control_choice.assert_called_once_with('ink')
        self.assertEqual(self.host.ai_adopted['step'],1);self.assertEqual(self.host.ai_adopted['session_id'],'visitor-a')

    def test_old_visitor_apply_is_rejected_before_backend(self):
        self.enter();self.host.ai_select('ai_apply_selected')
        self.host.session=dict(id='visitor-b',token='token-b',status='active')
        self.worker();self.done()
        self.assertFalse(self.host.requests);self.host.open_control_choice.assert_not_called()

    def check_canceled_apply(self,field):
        self.enter();self.host.ai_select('ai_apply_selected')
        setattr(self.host,field,getattr(self.host,field)+1)
        self.worker();self.done()
        self.assertFalse(self.host.requests);self.host.open_control_choice.assert_not_called()

    def test_pending_apply_cannot_run_after_navigation_change(self):
        self.check_canceled_apply('navigation_token')

    def test_pending_apply_cannot_run_after_generation_change(self):
        self.check_canceled_apply('generation')

    def test_late_apply_ack_cannot_launch_after_back(self):
        self.enter();self.host.ai_select('ai_apply_selected');self.worker()
        self.host.open_menu(main=True);self.done()
        self.host.open_control_choice.assert_not_called();self.assertEqual(self.host.menu_level,'main')

    def test_repeated_confirm_during_submission_runs_once(self):
        self.enter();self.host.ai_action('ai_preset:bamboo');self.host.ai_action('ai_preset:bamboo')
        self.assertEqual(len(self.work),1);self.worker();self.done()
        self.host.ai_service.submit.assert_called_once_with('token-a','creation','bamboo')

    def test_same_choices_step_and_refresh_rearm_observed_dwell(self):
        self.enter()
        points=[[.5,.5,.9] for _ in range(17)]
        points[5]=[.4,.4,.9];points[6]=[.6,.4,.9];points[10]=[.55,.5,.9]
        self.host.latest={'points17':points}
        for choice in ('ai_step:1','ai_refresh'):
            with self.subTest(choice=choice):
                self.host.selector.done=True;self.host.ai_select(choice)
                self.assertFalse(self.host.selector.done);self.assertFalse(self.host.selector.armed)
                opened=self.host.selector.opened
                self.assertIsNotNone(opened)
                # Holding still after confirmation cannot submit repeatedly.
                for seq in range(1,31):
                    now=opened+.1*seq
                    out=self.host.selector.update(dict(points17=points,pid=1,sequence=seq,timestamp_seconds=now),now)
                    self.assertIsNone(out['selected']);self.assertFalse(self.host.selector.armed)

    def test_ai_header_and_existing_section_targets_do_not_overlap(self):
        for section in section_catalog.SECTIONS:
            choices=section_catalog.choices(section,True);rects=section_catalog.rectangles(section,True)
            self.assertIn('ai_assistant',choices);self.assertEqual(len(choices),len(rects))
            for i,(a,b,c,d) in enumerate(rects):
                self.assertEqual(section_catalog.hit_index((a+c)/2,(b+d)/2,section,True),i)
                for e,f,g,h in rects[i+1:]:self.assertFalse(a<g and e<c and b<h and f<d)

    def test_secondary_action_pages_keep_six_slot_contract(self):
        pages=['root','options','system','help','error','uncertain','themes','audio','audio_options',
               'taiko_difficulty','taiko_practice','taiko_timing']+['confirm:'+action for action in CONFIRM]
        for mode in TITLES:
            for name in pages:
                try:data=page(mode,name)
                except ValueError:continue
                with self.subTest(mode=mode,page=name):
                    self.assertLessEqual(len(data['items']),6)
                    self.assertEqual(len(data['items']),len({i['id'] for i in data['items']}))


class AIHostControllerTests(unittest.TestCase):
    def setUp(self):
        from gamepad.test_host_priority import HostPriorityTests
        self.fixture=HostPriorityTests('test_idle_connection_does_not_own_body_cursor');self.fixture.setUp()
        self.calls=[];self.view=AIView(self.calls.append)
        self.view.set_context(capabilities={'configured':True});self.view.set_jobs([make_job()])
        suite=self.fixture.suite;suite.menu_level='ai';suite.ai_view=self.view
        suite.ai_select=Mock(side_effect=self.view.select)
        self.fixture.host.navigation.update({},self.view.choices,self.view.rectangles,
            (suite.navigation_token,'ai',suite.action_page,'answer'),self.fixture.now,
            memory_key=('ai',suite.navigation_token,'answer'),preserve_identity=True)

    def tearDown(self):self.view.destroy();self.fixture.tearDown()

    def test_idle_pad_preserves_hand_selection_on_ai_page(self):
        self.view.update_selection(dict(cursor=(.5,.5),hover=3,progress=.6,sample_key=('body',1)))
        self.assertTrue(self.fixture.host.tick())
        self.assertEqual(self.view.hover,3);self.assertEqual(self.view.progress,.6)

    def test_a_dispatches_internal_tab_through_view_once(self):
        from gamepad.device import EV_KEY
        host=self.fixture.host;host.navigation.index=self.view.choices.index('ai_kind:review')
        self.fixture.report((EV_KEY,304,1));self.assertTrue(host.tick());self.assertTrue(host.tick())
        self.assertEqual(self.view.kind,'review');self.assertFalse(self.calls)
        self.fixture.suite.ai_select.assert_called_once_with('ai_kind:review')
        self.fixture.suite.select.assert_not_called()

    def test_right_stick_suppresses_gestures_without_forcing_button_focus(self):
        from gamepad.device import EV_ABS
        self.fixture.report((EV_ABS,3,20000))
        self.assertTrue(self.fixture.host.gestures_blocked(self.fixture.now))
        self.assertTrue(self.fixture.host.tick());self.assertIsNone(self.view.hover)


if __name__=='__main__':unittest.main()
