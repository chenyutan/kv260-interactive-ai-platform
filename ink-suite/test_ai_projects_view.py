"""Mapped native project guides, clear confirmation and waiting feedback.

No board, API client, visitor database or camera is accessed.
"""
import time,unittest
from unittest.mock import patch
import cairo
import gi
gi.require_version('Gtk','3.0')
gi.require_version('PangoCairo','1.0')
from gi.repository import Gtk,PangoCairo
from ai_view import AIView
from ai_assistant.projects import PROJECT_IDS,project_catalog,project_help
from gamepad.navigation import Navigation


def drain(seconds=.03):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        for _ in range(12):
            if not Gtk.events_pending():break
            Gtk.main_iteration_do(False)
        time.sleep(.002)


def job(ident='a'*32,project='fruit',status='ready'):
    return dict(id=ident,kind='guide',project=project,status=status,preset='rules',
        error='网络连接暂时不可用，请稍后重试。' if status=='error' else '',
        result=dict(title='水果忍者 · 操作与建议',summary='切开水果获得得分，避开炸弹。',
                    tips=['让刀光经过水果。','先熟悉操作，再尝试连击。']))


class AIProjectViewTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.view=AIView(self.calls.append)
        self.window=Gtk.Window();self.window.set_decorated(False);self.window.resize(960,540)
        self.window.add(self.view);self.window.show_all();drain()
        self.view.set_context(kind='guide',project='fruit',capabilities={'configured':True})
    def tearDown(self):self.window.destroy();drain()
    def paint(self):
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540)
        self.view.paint(cairo.Context(surface),(960,540));return bytes(surface.get_data())

    def test_all_twelve_projects_have_complete_scrollable_offline_help(self):
        self.assertEqual({p['id'] for p in self.view.projects},set(PROJECT_IDS))
        self.view.set_context(capabilities={'configured':False})
        for project in PROJECT_IDS:
            self.view.select('ai_projects');self.assertTrue(self.view.project_picker)
            self.assertTrue(self.view.select('ai_project:'+project));self.assertFalse(self.view.project_picker)
            help=project_help(project)
            self.assertTrue(self.view.showing_local);self.assertFalse(self.view.reveal.active)
            for value in [help['title'],help['summary'],*help['tips']]:self.assertIn(value,self.view.full_text)
            self.assertEqual(''.join(self.view.full_text[l.start:l.end] for l in self.view.text_lines),self.view.full_text)
            self.view.scroll_by(100000);self.assertEqual(self.view.scroll.target,self.view.scroll.limit)
        self.assertEqual(self.calls,[])

    def test_project_selection_and_jobs_never_mix_answers(self):
        jobs=[job('a'*32,'fruit'),job('b'*32,'fight'),job('c'*32,'fruit'),job('d'*32,'not-a-project')]
        self.view.set_jobs(jobs)
        self.assertEqual([j['id'] for j in self.view.current_jobs],['a'*32,'c'*32])
        self.view.select('ai_job:next');self.assertEqual(self.view.current_job['id'],'c'*32)
        self.view.set_context(project='fight');self.assertEqual(self.view.current_job['id'],'b'*32)
        self.assertFalse(self.view.select('ai_project:not-a-project'))
        with self.assertRaises(ValueError):self.view.set_context(project='not-a-project')

    def test_other_project_pending_blocks_duplicate_without_false_waiting(self):
        self.view.set_jobs([job(project='fight',status='running')])
        self.assertTrue(self.view.busy);self.assertFalse(self.view.waiting);self.assertFalse(self.view.showing_local)
        self.assertFalse(any(c.startswith('ai_guide:') for c in self.view.choices))
        self.assertTrue(self.view.select('ai_projects'))
        self.view.select('ai_project:fight');self.assertTrue(self.view.waiting)

    def test_clear_is_single_activation_and_waits_for_service_success(self):
        self.view.set_jobs([job()]);self.view.select('ai_clear')
        self.assertEqual(self.calls,['ai_clear']);self.assertEqual(len(self.view.jobs),1)
        self.assertFalse(self.view.overlay_open)
        self.view.set_clearing(True)
        self.assertNotIn('ai_clear',self.view.choices)
        self.assertFalse(self.view.select('ai_clear'));self.assertEqual(self.calls,['ai_clear'])
        self.view.clear_finished();self.assertFalse(self.view.clear_confirm)
        self.assertFalse(self.view.jobs);self.assertFalse(self.view.waiting)
        self.assertFalse(self.view.showing_local);self.assertEqual(self.view.full_text,'')
        self.assertTrue(self.view.history_cleared);self.assertIn('ai_clear',self.view.choices)

    def test_clear_is_available_during_waiting_and_without_configuration(self):
        self.view.set_context(capabilities={'configured':False})
        self.view.set_jobs([job(status='running')]);self.assertIn('ai_clear',self.view.choices)
        self.view.select('ai_clear');self.assertEqual(self.calls,['ai_clear'])
        self.assertFalse(self.view.clear_confirm)
        self.view.clear_finished();self.assertFalse(self.view.submitting)

    def test_project_overview_entry_is_offline_and_has_specific_guide_questions(self):
        self.view.set_context(capabilities={'configured':False})
        self.assertTrue(self.view.select('ai_system'))
        self.assertEqual((self.view.kind,self.view.project),('guide','system'))
        self.assertTrue(self.view.showing_local)
        self.assertIn('官方 Hourglass',self.view.full_text)
        self.assertIn('应用场景',self.view.full_text)
        self.assertFalse(self.calls)
        self.assertIn('ai_clear',self.view.choices)  # clears older owned sessions too
        self.view.set_context(capabilities={'configured':True})
        labels={item['id']:item['title'] for item in self.view._items}
        self.assertEqual([labels['ai_guide:'+p] for p in ('rules','controls','tips')],
                         ['项目概览','技术原理','应用场景'])

    def test_submitting_gradient_changes_pixels_without_text_layout_or_static_rebuild(self):
        self.view.set_submitting(True);self.assertTrue(self.view.waiting)
        self.paint();builds=self.view.cache_builds;signature=(self.view.choices,self.view.rectangles)
        with patch('ai_view.PangoCairo.create_layout',side_effect=AssertionError('waiting must reuse text mask')):
            self.view.animate(1.);one=self.paint()
            self.view.animate(1.8);two=self.paint()
        self.assertNotEqual(one,two);self.assertEqual(self.view.cache_builds,builds)
        self.assertEqual((self.view.choices,self.view.rectangles),signature)
        self.assertEqual(len(self.view._thinking_masks),1)
        self.view.set_submitting(False);self.view.update_selection({})
        self.assertFalse(self.view.animate(time.monotonic()+1))
        self.assertFalse(self.view.waiting);self.assertFalse(self.view.showing_local)

    def test_waiting_repaints_small_region_only(self):
        self.view.set_submitting(True);self.view.animate(1.)
        with patch.object(self.view,'invalidate') as invalid:
            self.view.animate(1.2)
        self.assertEqual(len(invalid.call_args_list),1)
        x,y,w,h=invalid.call_args.args[0]
        self.assertEqual((round(x),round(y),round(w),round(h)),(278,214,640,68))

    def test_ready_error_and_clear_stop_thinking(self):
        self.view.set_jobs([job(status='queued')]);self.assertTrue(self.view.waiting)
        self.view.set_jobs([job(status='ready')]);self.assertFalse(self.view.waiting)
        self.assertTrue(self.view.reveal.active);self.view.select('ai_reveal')
        self.view.set_jobs([job('b'*32,status='running')]);self.assertTrue(self.view.waiting)
        self.view.set_jobs([job('b'*32,status='error')]);self.assertFalse(self.view.waiting)
        self.view.update_selection({});self.assertFalse(self.view.animate(time.monotonic()+1))
        self.view.clear_finished();self.assertFalse(self.view.waiting)

    def test_hidden_and_overlay_suspend_waiting_animation(self):
        self.view.set_jobs([job(status='running')]);self.assertTrue(self.view.animate())
        for choice,close in [('ai_projects','ai_project_close'),('ai_phone','ai_phone_close')]:
            self.view.select(choice);self.assertTrue(self.view.overlay_open)
            self.view.update_selection({});self.assertFalse(self.view.animate(time.monotonic()+1))
            self.assertFalse(self.view.controller_scroll({'buttons':['RB']},.04))
            self.view.select(close);self.assertFalse(self.view.overlay_open);self.assertTrue(self.view.animate())
        self.window.hide();drain();self.assertIsNone(self.view.timer)
        self.window.show_all();drain();self.assertTrue(self.view.waiting)

    def test_local_help_can_be_read_while_cloud_waits_and_never_claims_cloud_output(self):
        self.view.set_jobs([job(status='running')]);self.view.select('ai_local_help')
        self.assertTrue(self.view.showing_local);self.assertFalse(self.view.waiting);self.assertTrue(self.view.busy)
        self.view.scroll_by(84);position=self.view.scroll.target
        self.view.set_jobs([job(status='ready')])
        self.assertTrue(self.view.showing_local);self.assertEqual(position,self.view.scroll.target)
        self.assertEqual(self.view._caption_state()[-1],True)
        self.view.select('ai_local_help');self.assertFalse(self.view.showing_local)
        self.assertIn('切开水果获得得分',self.view.full_text)

    def test_guides_emit_context_specific_requests_without_starting_experience(self):
        for project in PROJECT_IDS:
            self.view.set_context(project=project)
            for preset in ('rules','controls','tips'):
                self.assertTrue(self.view.select('ai_guide:'+preset));self.assertEqual(self.view.project,project)
        self.assertEqual(self.calls,['ai_guide:'+p for _ in PROJECT_IDS for p in ('rules','controls','tips')])

    def test_all_guide_overlay_targets_are_large_disjoint_and_reachable(self):
        self.view.set_jobs([job()]);states=['normal','projects','phone']
        for state in states:
            if state=='projects':self.view.select('ai_projects')
            elif state=='phone':self.view.select('ai_project_close');self.view.select('ai_phone')
            choices,rects=self.view.choices,self.view.rectangles
            for i,(a,b,c,d) in enumerate(rects):
                self.assertGreaterEqual((d-b)*540+1e-6,48)
                self.assertTrue(0<=a<c<=1 and 0<=b<d<=1)
                for e,f,g,h in rects[i+1:]:self.assertFalse(a<g and e<c and b<h and f<d)
            seen={0};pending=[0]
            while pending:
                for direction in ('UP','DOWN','LEFT','RIGHT'):
                    nav=Navigation();nav.page=state;nav.index=pending[-1]
                    target=nav.update({'buttons':(direction,)},choices,rects,state,1.)
                    if target not in seen:seen.add(target);pending.insert(0,target)
                pending.pop()
            self.assertEqual(seen,set(range(len(choices))),state)

    def test_return_label_and_poll_preserve_current_context(self):
        self.view.set_context(return_label='返回八段锦');self.assertEqual(self.view.return_label,'返回八段锦')
        self.view.select('ai_projects');self.view.select('ai_project:taichi')
        self.view.scroll_by(84);position=self.view.scroll.target
        self.assertFalse(self.view.set_context(capabilities={'configured':True}))
        self.assertEqual(self.view.project,'taichi');self.assertEqual(self.view.scroll.target,position)
        self.assertTrue(self.view.set_return_label('返回主菜单'))
        self.view.select('ai_back');self.assertEqual(self.calls,['ai_back'])

    def test_entry_reset_closes_overlays_and_stops_submission(self):
        self.view.set_submitting(True);self.view.select('ai_clear')
        self.assertEqual(self.calls,['ai_clear']);self.assertFalse(self.view.clear_confirm)
        self.view.set_context(share_url=None,qr=None)
        self.assertFalse(self.view.overlay_open);self.assertFalse(self.view.submitting)
        self.assertIn('ai_projects',self.view.choices)


if __name__=='__main__':unittest.main()
