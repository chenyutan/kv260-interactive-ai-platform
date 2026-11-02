"""Real host input and shaped label geometry; no devices, account or API IO."""
import time,unittest
import cairo
import gi
gi.require_version('Gtk','3.0')
from ai_view import AIView,block
from gamepad.device import EV_KEY
from gamepad.navigation import Navigation


class AIHostFocusTests(unittest.TestCase):
    def setUp(self):
        from test_ai_host import AIHostControllerTests
        self.harness=AIHostControllerTests('test_idle_pad_preserves_hand_selection_on_ai_page')
        self.harness.setUp();self.fixture=self.harness.fixture
        self.view=self.harness.view;self.host=self.fixture.host
        self.fixture.suite.control_mode='gamepad'
    def tearDown(self):self.harness.tearDown()
    def tick(self):
        self.fixture.now+=.016;self.assertTrue(self.host.tick());self.assertEqual(self.host.last_error,'')
    def focus(self,choice):
        index=self.view.choices.index(choice);self.host.navigation.index=index
        self.view.set_gamepad_focus(index)
    def confirm(self,choice):
        self.focus(choice);self.fixture.report((EV_KEY,304,1));self.fixture.report((EV_KEY,304,0));self.tick();self.tick()
    def selected(self):return self.view.choices[self.host.navigation.index]

    def test_repeated_content_confirm_stays_on_pressed_category_not_back(self):
        for choice in ('ai_kind:review','ai_kind:health','ai_kind:creation','ai_kind:guide','ai_system'):
            with self.subTest(choice=choice):
                self.confirm(choice)
                self.assertEqual(self.selected(),choice)
                self.assertEqual(self.view.choices[self.view.hover],choice)
        self.assertEqual(self.harness.calls,[])

    def test_fixed_help_enables_reading_actions_without_moving_its_focus(self):
        self.confirm('ai_kind:guide');self.confirm('ai_local_help')
        self.assertTrue(self.view.showing_local)
        self.assertEqual(self.selected(),'ai_local_help')
        self.assertEqual(self.view.choices[self.view.hover],'ai_local_help')

    def test_busy_roundtrip_recovers_same_preset_without_return_focus(self):
        self.focus('ai_preset:bamboo')
        self.view.set_submitting(True);self.tick()
        self.assertEqual(self.selected(),'ai_kind:creation')
        self.view.set_submitting(False);self.tick()
        self.assertEqual(self.selected(),'ai_preset:bamboo')
        self.assertEqual(self.view.choices[self.view.hover],'ai_preset:bamboo')

    def test_explicit_navigation_during_busy_prevents_late_focus_recall(self):
        self.focus('ai_preset:bamboo');self.view.set_submitting(True);self.tick()
        self.fixture.report((EV_KEY,547,1));self.fixture.report((EV_KEY,547,0));self.tick()
        selected=self.selected();self.view.set_submitting(False);self.tick()
        self.assertEqual(self.selected(),selected)
        self.assertNotEqual(self.selected(),'ai_preset:bamboo')

    def test_overlay_returns_to_its_opening_control_and_new_visit_resets(self):
        self.confirm('ai_phone')
        self.assertTrue(self.view.phone_open);self.assertEqual(self.selected(),'ai_phone_close')
        self.confirm('ai_phone_close')
        self.assertFalse(self.view.phone_open);self.assertEqual(self.selected(),'ai_phone')
        self.fixture.suite.navigation_token+=1;self.tick()
        self.assertEqual(self.selected(),'ai_kind:creation')


class AIViewFocusAndTextTests(unittest.TestCase):
    def setUp(self):self.view=AIView(lambda _:None);self.view.set_context(capabilities={'configured':True})
    def tearDown(self):self.view.destroy()
    def test_poll_redraw_preserves_observed_pointer_progress_and_age(self):
        self.view.set_context(kind='health',project='baduanjin')
        index=self.view.choices.index('ai_kind:health');r=self.view.rectangles[index]
        self.view.update_selection(dict(cursor=((r[0]+r[2])/2,(r[1]+r[3])/2),hover=index,progress=.43,sample_key=('body',1)))
        before=(self.view.hover,self.view.progress,self.view.sample_received,self.view.sample_key,self.view.target)
        self.view.set_context(health_summary=dict(course_id='baduanjin',summary='记录已保存。'))
        self.assertEqual((self.view.hover,self.view.progress,self.view.sample_received,self.view.sample_key,self.view.target),before)
    def test_button_ink_center_for_chinese_latin_multiline_and_ellipsis(self):
        import numpy as np
        from ai_view import _centered_origin
        for value,width,height,size in (('返回主菜单',100,50,15),('AI 助手',168,50,18),
                ('DeepSeek',120,19,12),('健康训练\n训练建议',196,56,16),('很长的按钮文字需要省略号',104,50,17)):
            with self.subTest(value=value):
                image=cairo.ImageSurface(cairo.FORMAT_RGB24,300,120);cr=cairo.Context(image)
                layout=block(cr,value,20,20,width,height,size,(1,1,1),True,centered=True)
                x,y=_centered_origin(layout,20,20,width,height);ink,_=layout.get_pixel_extents()
                self.assertAlmostEqual(x+ink.x+ink.width/2,20+width/2)
                self.assertAlmostEqual(y+ink.y+ink.height/2,20+height/2)
                image.flush();pixels=np.frombuffer(image.get_data(),dtype=np.uint8).reshape(120,image.get_stride())[:,:1200].reshape(120,300,4)
                rows,cols=np.where(pixels[:,:,:3].max(axis=2)>96)
                self.assertGreater(len(cols),0)
                self.assertLessEqual(abs((cols.min()+cols.max()+1)/2-(20+width/2)),1.5)
                self.assertLessEqual(abs((rows.min()+rows.max()+1)/2-(20+height/2)),1.5)


class AIIdentityNavigationTests(unittest.TestCase):
    def test_reordered_actions_keep_id_only_in_opted_in_page(self):
        rects=((0,0,.2,.2),(.4,0,.6,.2),(.8,0,1,.2));nav=Navigation()
        nav.update({},('back','preset','local'),rects,'ai',0,preserve_identity=True)
        nav.index=2
        index=nav.update({},('back','local','preset'),rects,'ai',1,preserve_identity=True)
        self.assertEqual(index,1)
        self.assertEqual(nav.update({},('local','back'),rects[:2],'new-visit',2,preserve_identity=True),0)
    def test_reenabled_action_does_not_steal_simultaneous_confirm(self):
        rects=((0,0,.2,.2),(.4,0,.6,.2),(.8,0,1,.2));nav=Navigation()
        nav.update({},('back','kind','preset'),rects,'ai',0,preserve_identity=True);nav.index=2
        self.assertEqual(nav.update({},('back','kind'),rects[:2],'ai',1,preserve_identity=True,default_choice='kind'),1)
        index=nav.update({'pressed':['A']},('back','kind','preset'),rects,'ai',2,preserve_identity=True,default_choice='kind')
        self.assertEqual(index,1);self.assertIsNone(nav.missing_choice)

if __name__=='__main__':unittest.main()
