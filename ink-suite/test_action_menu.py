"""Real GTK action menu checks; run with an isolated Xvfb display on KV260."""
import math
import unittest
from types import SimpleNamespace

try:
    import cairo
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk
    import action_menu
    import menu_view
    from gesture_menu import ZONES
except ModuleNotFoundError as exc:
    if exc.name not in ('cairo','gi'):raise
    action_menu=None


@unittest.skipIf(action_menu is None,'Requires real GTK and pycairo; run under Xvfb')
class ActionMenuTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.;self.original_time=menu_view.time
        menu_view.time=SimpleNamespace(monotonic=lambda:self.clock)
        self.chosen=[];self.window=Gtk.Window()
        self.menu=action_menu.ActionMenu(self.chosen.append)
        self.window.add(self.menu);self.window.resize(1024,550);self.window.show_all()
        while Gtk.events_pending():Gtk.main_iteration_do(False)
        self.menu.set_actions('墨武 · 操作菜单',self.items())
        self.surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,550)
        self.cr=cairo.Context(self.surface)

    def tearDown(self):
        self.window.destroy();menu_view.time=self.original_time

    @staticmethod
    def items():
        return [dict(id='resume',title='继续对局',subtitle='回到刚才的战斗'),
                dict(id='restart',title='重新开始',subtitle='本局进度将重置',danger=True),
                dict(id='help',title='招式说明',subtitle='查看出拳、腿法与防御')]

    def point(self,index):
        a,b,c,d=ZONES[index]
        return ((a+c)/2,(b+d)/2)

    def click(self,index):
        x,y=self.point(index)
        return self.menu.click(self.menu,SimpleNamespace(button=1,
            x=x*self.menu.get_allocated_width(),y=y*self.menu.get_allocated_height()))

    def send(self,key,point=None,hover=0,progress=.25):
        self.menu.update_selection(dict(cursor=self.point(0) if point is None else point,
            hover=hover,progress=progress,sample_key=key))

    def advance(self,seconds=.03):
        self.clock+=seconds;self.menu.animate(self.clock)

    def test_click_uses_dynamic_ids_and_shared_dwell_geometry(self):
        self.assertEqual(action_menu.RECTS,ZONES)
        for i,item in enumerate(self.items()):
            self.assertEqual(self.menu.hit_test(self.point(i)),item['id'])
            self.assertTrue(self.click(i));self.assertEqual(self.chosen[-1],item['id'])
        self.assertEqual(self.menu.choices,('resume','restart','help'))

    def test_unused_slots_never_select_or_show_dwell(self):
        for i in (3,4,5):
            self.assertFalse(self.click(i));self.assertIsNone(self.menu.hit_test(self.point(i)))
            self.send(i,point=self.point(i),hover=i,progress=1.)
            self.assertIsNone(self.menu.hover);self.assertEqual(self.menu.progress,0.)
        self.assertEqual(self.chosen,[])

    def test_six_actions_fit_all_slots(self):
        items=[dict(id=f'choice-{i}',title=f'操作 {i}',subtitle='') for i in range(6)]
        self.menu.set_actions('操作',items)
        for i in range(6):self.click(i)
        self.assertEqual(self.chosen,[x['id'] for x in items])

    def test_invalid_items_do_not_partly_replace_current_page(self):
        original=(self.menu.title,self.menu.choices)
        for items in ([dict(id='x',title='一')]*2,[dict(id='',title='空')],
                      [dict(id=str(i),title='太多') for i in range(7)]):
            with self.assertRaises(ValueError):self.menu.set_actions('非法页',items)
            self.assertEqual((self.menu.title,self.menu.choices),original)

    def test_setter_copies_item_records(self):
        items=self.items();self.menu.set_actions('操作',items)
        items[0]['id']='changed';items[0]['title']='changed';items.clear()
        self.assertEqual(self.menu.action_at(0),'resume')
        self.assertEqual(self.menu.actions[0]['title'],'继续对局')

    def test_confirmation_change_clears_previous_cursor_and_progress(self):
        self.send(1,hover=1,progress=.99);self.advance()
        self.menu.draw(self.menu,self.cr);built=self.menu.cache_builds
        self.menu.set_actions('确认重新开始？',[
            dict(id='confirm',title='确认重新开始',subtitle='本局进度将重置',danger=True),
            dict(id='back',title='取消',subtitle='保留当前对局')])
        self.assertIsNone(self.menu.pointer);self.assertIsNone(self.menu.hover)
        self.assertEqual(self.menu.progress,0.)
        self.send(1,hover=0,progress=1.)
        self.assertIsNone(self.menu.pointer)
        self.menu.draw(self.menu,self.cr)
        self.assertEqual(self.menu.cache_builds,built+1)
        self.assertTrue(self.click(1));self.assertEqual(self.chosen,['back'])

    def test_unavailable_page_discards_input_and_mouse_selection(self):
        self.send(1);self.advance()
        self.menu.set_availability(False,'正在恢复识别…')
        self.send(2,progress=1.);self.advance()
        self.assertIsNone(self.menu.pointer);self.assertIsNone(self.menu.hover)
        self.assertFalse(self.click(0));self.assertEqual(self.chosen,[])
        self.menu.set_availability(True,'识别已就绪')
        self.assertTrue(self.click(0));self.assertEqual(self.chosen,['resume'])

    def test_help_has_one_back_choice_and_inert_complete_text_cards(self):
        lines=['左右移动：身体向对应方向侧移，回到中间后停下。',
               '跳跃：双手自然放下，轻轻下沉后起身。',
               '踢腿：抬起左膝或右膝，落脚后再做下一次。',
               '防御：举起双臂。再轻轻下沉，可做蹲防。',
               '大招：真气满后双手举起。结束动画播放后显示胜负。']
        self.menu.set_help('墨武 · 招式说明',lines)
        self.assertEqual(self.menu.choices,('back',))
        self.assertEqual(self.menu.help_lines,tuple(lines))
        for i in range(1,6):
            self.assertFalse(self.click(i));self.assertIsNone(self.menu.action_at(i))
        self.menu.draw(self.menu,self.cr)
        self.assertTrue(self.click(0));self.assertEqual(self.chosen,['back'])
        self.menu.set_actions('操作',self.items())
        self.assertFalse(self.menu.help_lines)

    def test_visual_animation_never_confirms_an_action_by_itself(self):
        for i in range(90):
            self.send(i,progress=1.);self.advance()
        self.assertEqual(self.chosen,[])
        self.assertEqual(self.menu.progress,1.)

    def test_entry_has_explicit_start_back_and_inert_complete_instructions(self):
        from entry_help import briefing
        for mode in ('ink','fight','fruit','sword','taiko','platformer','runner','pal'):
            for control in ('body','gamepad'):
                info=briefing(mode,control)
                self.menu.set_briefing(info['title'],info['lines'],info['message'])
                self.assertEqual(self.menu.choices,('intro_start','intro_back'))
                self.assertEqual(len(self.menu.help_lines),4)
                for i in range(2,6):
                    self.assertIsNone(self.menu.hit_test(self.point(i)))
                    self.assertFalse(self.click(i))
                self.menu.draw(self.menu,self.cr)
        self.assertFalse(self.chosen)

    def test_duplicate_observation_expires_without_phantom_hand(self):
        self.send(8);self.advance()
        for _ in range(10):self.send(8);self.advance()
        self.assertIsNone(self.menu.pointer);self.assertFalse(self.menu.trail)
        self.assertIsNone(self.menu.hover);self.assertEqual(self.menu.progress,0.)

    def test_hide_stops_timer_and_clears_stale_highlight(self):
        self.send(1,hover=1);self.advance();self.menu.hide()
        self.assertIsNone(self.menu.timer);self.assertIsNone(self.menu.hover)
        self.assertEqual(self.menu.progress,0.)
        invalidations=[];self.menu.invalidate=invalidations.append
        self.send(2);self.advance();self.assertEqual(invalidations,[])
        self.menu.show();self.assertIsNotNone(self.menu.timer)

    def test_pointer_redraw_keeps_static_cache_and_opaque_backbuffer(self):
        self.menu.draw(self.menu,self.cr);built=self.menu.cache_builds
        buffer=self.menu.backbuffer
        for key in range(4):
            self.send(key,point=(.15+key*.025,.36));self.advance()
            self.menu.draw(self.menu,self.cr)
        self.assertEqual(self.menu.cache_builds,built)
        self.assertIs(self.menu.backbuffer,buffer)
        self.assertEqual(buffer.get_format(),cairo.FORMAT_RGB24)

    def test_partial_repaint_matches_full_image(self):
        self.send(1);self.advance();self.menu.draw(self.menu,self.cr)
        for key,point in ((2,(.27,.39)),(3,(.50,.39))):
            rectangles=[];self.menu.invalidate=rectangles.append
            self.send(key,point=point,hover=0 if key==2 else 1);self.advance()
            self.cr.save()
            for x,y,w,h in rectangles:
                self.cr.rectangle(math.floor(x),math.floor(y),math.ceil(w)+2,math.ceil(h)+2)
            self.cr.clip();self.menu.draw(self.menu,self.cr);self.cr.restore()
            bounded_frame=bytes(self.menu.backbuffer.get_data())
            reference=cairo.ImageSurface(cairo.FORMAT_RGB24,1024,550)
            self.menu.draw(self.menu,cairo.Context(reference))
            self.assertEqual(bounded_frame,bytes(self.menu.backbuffer.get_data()))
            from test_menu_view import assert_damage_presentation
            assert_damage_presentation(self,self.surface,reference,rectangles)

    def test_bad_progress_or_pointer_does_not_crash_or_select(self):
        for n,value in enumerate((None,float('nan'),float('inf'),'bad')):
            self.menu.update_hand((.2,.3),0,value,sample_key=n)
            self.assertEqual(self.menu.progress,0.)
        self.menu.update_hand((float('nan'),.2),0,1.,sample_key=10)
        self.assertIsNone(self.menu.pointer);self.assertEqual(self.chosen,[])


if __name__=='__main__':unittest.main()
