"""Back is one level, not a silent exit; synthetic controls only."""
import ast
from pathlib import Path
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from gamepad.host import HostControls


def back_method(name='navigate_back'):
    tree=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf8'))
    suite=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Suite')
    method=next(n for n in suite.body if isinstance(n,ast.FunctionDef) and n.name==name)
    ns={};exec(compile(ast.Module(body=[method],type_ignores=[]),'main.py','exec'),ns)
    return ns[name]


class NavigationFlowTests(unittest.TestCase):
    def suite(self,level='control',page='entry_help'):
        return NS(health_active=False,menu_level=level,action_page=page,menu=NS(section='home'),
                  menu_open=True,child=None,switching=False,choose_control=Mock(),
                  cancel_control_choice=Mock(),open_menu=Mock(),show_section=Mock(),
                  menu_back=Mock(),leave_photo=Mock(),health_view=NS(back=Mock()),close_ai=Mock())

    def test_entry_help_back_keeps_selected_game_and_returns_one_step(self):
        s=self.suite();back_method()(s)
        s.choose_control.assert_called_once_with('intro_back')
        s.cancel_control_choice.assert_not_called();s.open_menu.assert_not_called()

    def test_control_choice_back_returns_to_its_parent(self):
        s=self.suite(page='control_choice');back_method()(s)
        s.cancel_control_choice.assert_called_once();s.choose_control.assert_not_called()

    def test_photo_and_health_use_their_own_back_contract(self):
        s=self.suite('photo');back_method()(s);s.leave_photo.assert_called_once()
        s=self.suite('health');s.health_active=True;back_method()(s)
        s.health_view.back.assert_called_once();s.open_menu.assert_not_called()

    def test_category_back_does_not_resume_background_game(self):
        s=self.suite('main');s.menu.section='creation';s.child=object();back_method()(s)
        s.show_section.assert_called_once_with('home');s.menu_back.assert_not_called()

    def test_ai_phone_back_returns_to_answer_before_main_menu(self):
        s=self.suite('ai');s.ai_view=NS(phone_open=True);s.ai_select=Mock()
        back_method()(s)
        s.ai_select.assert_called_once_with('ai_phone_close');s.open_menu.assert_not_called()
        s.ai_view.phone_open=False;back_method()(s)
        s.close_ai.assert_called_once();s.open_menu.assert_not_called()

    def test_home_back_is_inert_and_running_game_back_opens_actions(self):
        s=self.suite('main');back_method()(s);s.open_menu.assert_not_called()
        s.menu_open=False;back_method()(s);s.open_menu.assert_called_once()

    def test_home_back_never_resumes_a_preserved_runner_child(self):
        s=self.suite('main');s.child=object();s.mode='runner'
        back_method()(s)
        s.menu_back.assert_not_called();s.open_menu.assert_not_called()
        s.show_section.assert_not_called();self.assertTrue(s.menu_open)

    def test_category_back_then_home_back_keeps_background_game_paused(self):
        s=self.suite('main');s.child=object();s.menu.section='entertainment'
        s.show_section.side_effect=lambda value:setattr(s.menu,'section',value)
        back_method()(s);back_method()(s)
        s.show_section.assert_called_once_with('home')
        s.menu_back.assert_not_called();s.open_menu.assert_not_called()

    def test_direct_menu_back_cannot_cancel_main_menu_into_old_child(self):
        for section in ('home','entertainment'):
            with self.subTest(section=section):
                s=self.suite('main');s.child=object();s.pending_action=None
                s.menu.section=section;s.cancel_menu=Mock()
                back_method('menu_back')(s)
                s.cancel_menu.assert_not_called()
                if section=='home':s.show_section.assert_not_called()
                else:s.show_section.assert_called_once_with('home')

    def test_failed_launch_without_a_child_still_routes_back(self):
        for page in ('error','uncertain'):
            s=self.suite('actions',page);back_method()(s)
            s.menu_back.assert_called_once();s.open_menu.assert_not_called()

    def test_loading_cancels_before_stale_control_help_dispatch(self):
        s=self.suite('control','entry_help');s.switching=True;s.control_target=None
        back_method()(s);s.open_menu.assert_called_once()
        s.choose_control.assert_not_called();s.cancel_control_choice.assert_not_called()

    def host(self,buttons=('B',)):
        h=HostControls.__new__(HostControls);s=self.suite();s.stop=threading.Event()
        s.control_mode='body';s.navigate_back=Mock();h.suite=s
        h.reader=NS(snapshot=lambda:dict(connected=True,name='Synthetic',pressed=buttons,released=(),buttons=buttons,axes={}))
        h.pointer=NS(update=lambda *_,**__: {});h.dialog=lambda:None
        h.closed=False;h.last=time.monotonic();h.navigation_until=0.;h.connected=False
        return h,s

    def test_gamepad_back_routes_to_the_same_contract(self):
        h,s=self.host();self.assertTrue(h.tick());s.navigate_back.assert_called_once()

    def test_gamepad_b_on_home_does_not_reveal_preserved_runner(self):
        h,s=self.host();s.menu_level='main';s.child=object();s.mode='runner'
        s.navigate_back=lambda:back_method()(s)
        self.assertTrue(h.tick());s.menu_back.assert_not_called()
        s.open_menu.assert_not_called();self.assertTrue(s.menu_open)

    def test_gamepad_start_on_home_keeps_preserved_runner_paused(self):
        h,s=self.host(('START',));s.menu_level='main';s.child=object()
        s.pending_action=None;s.cancel_menu=Mock()
        s.menu_back=lambda:back_method('menu_back')(s)
        self.assertTrue(h.tick());s.cancel_menu.assert_not_called()

    def test_health_b_does_not_exit_the_course_directly(self):
        h,s=self.host();s.health_active=True;s.health_view.action=Mock()
        self.assertTrue(h.tick());s.health_view.back.assert_called_once()
        s.health_view.action.assert_not_called()

    def test_photo_right_click_does_not_also_go_back(self):
        h,s=self.host(buttons=());s.menu_level='photo';s.photo_view=NS(gamepad=Mock())
        snapshot=dict(connected=True,name='Synthetic',pressed=(),released=('BACK',),buttons=(),axes={})
        h.reader.snapshot=lambda:snapshot
        h.pointer.update=lambda *_,**__:dict(back_release_consumed=True)
        h.tick();self.assertEqual(s.photo_view.gamepad.call_args.args[0]['released'],())
        self.assertEqual(snapshot['released'],('BACK',))
        h.pointer.update=lambda *_,**__:{}
        h.tick();self.assertEqual(s.photo_view.gamepad.call_args.args[0]['released'],('BACK',))

if __name__=='__main__':unittest.main()
