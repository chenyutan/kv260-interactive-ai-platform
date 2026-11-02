"""Native hierarchy, clear feedback and per-course guidance; no devices/API."""
import time,unittest
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk
from ai_view import AIView
from gamepad.navigation import Navigation
from section_menu import SectionMenuView

def drain():
    until=time.monotonic()+.03
    while time.monotonic()<until:
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        time.sleep(.002)

class AICoachViewTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.view=AIView(self.calls.append)
        self.window=Gtk.Window();self.window.set_decorated(False)
        self.window.add(self.view);self.window.resize(960,540);self.window.show_all();drain()
        self.view.set_context(capabilities={'configured':True})
    def tearDown(self):self.window.destroy();drain()

    def test_main_sections_and_function_tools_have_separate_regions(self):
        for kind in ('guide','creation','review','health'):
            self.view.set_context(kind=kind)
            sections=[i for i in self.view._items if i['role']=='section']
            tools=[i for i in self.view._items if i['role']=='tool']
            self.assertEqual([i['title'] for i in sections],['项目指南','创作方案','成绩复盘','健康训练','项目介绍'])
            self.assertEqual({i['id'] for i in tools},{'ai_back','ai_refresh','ai_phone','ai_clear'})
            self.assertLess(max(i['rect'][1]+i['rect'][3] for i in tools),min(i['rect'][1] for i in sections))
            for item in sections+tools:
                x,y,w,h=item['rect'];self.assertGreaterEqual(h,48)
                self.assertEqual(self.view.choices[self.view.hit_index((x+w/2)/960,(y+h/2)/540)],item['id'])

    def test_clear_result_is_empty_history_and_not_reopened_fixed_help(self):
        self.view.select('ai_system');self.assertTrue(self.view.showing_local)
        self.assertIn('技术原理',self.view.full_text)
        self.view.clear_finished()
        self.assertTrue(self.view.history_cleared);self.assertFalse(self.view.showing_local)
        self.assertEqual(self.view.full_text,'');self.assertEqual(self.view.text_lines,())
        self.view.set_context(kind='guide',project='fruit')
        self.assertFalse(self.view.showing_local);self.assertEqual(self.view.full_text,'')
        self.assertTrue(self.view.select('ai_local_help'));self.assertTrue(self.view.showing_local)
        self.assertIn('水果',self.view.full_text)

    def test_empty_history_after_entry_never_looks_like_restored_ai_results(self):
        self.view.set_context(kind='guide',project='fruit')
        self.view.set_jobs([])
        self.assertFalse(self.view.showing_local);self.assertEqual(self.view.full_text,'')
        self.view.select('ai_projects');self.view.select('ai_project:fruit')
        self.assertTrue(self.view.showing_local)
        self.assertIn('固定说明',next(item['title'] for item in self.view._items if item['id']=='ai_local_help'))

    def test_health_course_picker_never_offers_games_or_system(self):
        self.view.select('ai_kind:health')
        self.assertEqual((self.view.kind,self.view.project),('health','baduanjin'))
        self.view.select('ai_projects')
        self.assertEqual({choice for choice in self.view.choices if choice.startswith('ai_project:')},
                         {'ai_project:baduanjin','ai_project:taichi'})
        self.view.select('ai_project:taichi')
        self.assertEqual((self.view.kind,self.view.project),('health','taichi'))
        for preset in ('next','review','demo'):
            self.assertTrue(self.view.select('ai_health:'+preset))
        self.assertEqual(self.calls,['ai_health:next','ai_health:review','ai_health:demo'])

    def test_health_jobs_and_record_summary_do_not_cross_courses(self):
        self.view.set_context(kind='health',project='baduanjin',health_summary=dict(
            course_id='baduanjin',report_count=2,summary='本人八段锦记录两次。',
            local_help=dict(title='记录摘要',summary='本人八段锦记录两次。',tips=['先复习已记录动作。'])))
        jobs=[dict(id=c*32,kind='health',project=course,status='ready',result=dict(
            title=course,summary=('记录摘要。\n'*30),tips=['先看示范。']))
            for c,course in (('a','baduanjin'),('b','taichi'))]
        self.view.set_jobs(jobs)
        self.assertEqual(self.view.current_job['project'],'baduanjin')
        self.view.select('ai_reveal');self.view.scroll_by(100000)
        self.assertEqual(self.view.scroll.target,self.view.scroll.limit)
        self.view.set_context(project='taichi')
        self.assertEqual(self.view.current_job['project'],'taichi');self.assertEqual(self.view.course_summary,{})
        self.view.set_jobs([]);self.view.select('ai_local_help')
        self.assertNotIn('本人八段锦记录两次',self.view.full_text)
        self.assertIn('暂无本人的有效训练记录',self.view.full_text)

    def test_all_navigation_and_tool_targets_remain_reachable(self):
        self.view.set_context(kind='health')
        choices,rects=self.view.choices,self.view.rectangles
        reached={0};todo=[0]
        while todo:
            start=todo.pop()
            for direction in ('UP','DOWN','LEFT','RIGHT'):
                nav=Navigation();nav.page='ai';nav.index=start
                target=nav.update({'buttons':[direction]},choices,rects,'ai',1.)
                if target not in reached:reached.add(target);todo.append(target)
        self.assertEqual(reached,set(range(len(choices))))
        for i,(a,b,c,d) in enumerate(rects):
            for e,f,g,h in rects[i+1:]:self.assertFalse(a<g and e<c and b<h and f<d)

    def test_official_whale_is_bundled_and_static_decode_is_reused(self):
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,96,54);cr=cairo.Context(surface)
        self.assertTrue(SectionMenuView.deepseek_mark(cr,5,5,80,40))
        original=SectionMenuView._deepseek_image
        self.assertEqual((original.get_width(),original.get_height()),(256,187))
        for _ in range(8):SectionMenuView.deepseek_mark(cr,5,5,80,40)
        self.assertIs(SectionMenuView._deepseek_image,original)

if __name__=='__main__':unittest.main()
