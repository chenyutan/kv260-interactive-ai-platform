"""Health ownership/navigation integration using the real Suite method AST."""
import contextlib,json,threading,unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import test_action_host as host_fixture
from gesture_menu import DwellSelector
pose=host_fixture.pose

class ImmediateThread:
    def __init__(self,target,args=(),**_):self.target=target;self.args=args
    def start(self):self.target(*self.args)

class Health:
    def __init__(self):
        self.choices=('health_pause','health_home');self.rectangles=((.1,.8,.4,.95),(.6,.8,.9,.95))
        self.gesture_navigation=False;self.session=object();self.current={}
        for name in ('enter','receive','action','pause','set_voice_status'):setattr(self,name,Mock())
        self.update_selection=Mock();self.hit_index=lambda x,y:0

class HealthHostTests(unittest.TestCase):
    def setUp(self):
        fixture=host_fixture.ActionHostTests();fixture.setUp();self.s=fixture.s;s=self.s
        s.health_active=False;s.health_choices=None;s.health_previous_control=None;s.health_view=Health()
        s.visitor_store=None;s.visitor_busy=False;s.control_mode='gamepad';s.gamepad_host=None;s.pose_view=NS(stop_preview=Mock())
        s.menu.section='home';s.menu.set_section=Mock();s.health_report=Mock()
        s.__test_globals__['threading']=NS(Thread=ImmediateThread)
        self.clock=fixture.clock

    def test_section_routes_before_any_pose_gate(self):
        s=self.s;s.show_section=Mock();s.enter_health=Mock();s.interaction_ready=Mock(return_value=False)
        actual=type(s).select
        actual(s,'section_health');actual(s,'baduanjin')
        s.show_section.assert_called_once_with('health');s.enter_health.assert_called_once()
        s.interaction_ready.assert_not_called()

    def test_section_change_rearms_dwell_and_clears_prior_selection(self):
        s=self.s;s.menu_open=True;s.menu_level='main';s.selector.done=True
        s.show_section('entertainment')
        self.assertFalse(s.selector.done);self.assertIsNone(s.selector.hover)
        s.menu.set_section.assert_called_once_with('entertainment')
        self.assertEqual(s.stack.visible,'menu')

    def test_health_entry_cancels_old_child_without_restarting_services(self):
        s=self.s;old=s.child;generation=s.generation
        s.enter_health()
        self.assertTrue(s.health_active);self.assertEqual(s.generation,generation+1)
        self.assertIsNone(s.child);self.assertEqual(s.control_mode,'body')
        self.assertEqual(s.health_previous_control,'gamepad');s.stop_process.assert_called_once_with(old)
        self.assertEqual(s.commands[-1][0]['menu'],True)
        self.assertEqual(s.stack.visible,'health');s.health_view.enter.assert_called_once()

    def test_health_command_keeps_paused_backend_and_measured_body_input(self):
        s=self.s;s.health_active=True;requests=[]
        response=NS(read=lambda:b'{"accepted": true}')
        class Reply:
            def __enter__(self):return response
            def __exit__(self,*_):pass
        s.opener=NS(open=lambda request,timeout:requests.append(json.loads(request.data)) or Reply())
        s.command(dict(active=True,mode='fight',menu=False,preview=True))
        self.assertTrue(requests[-1]['menu']);self.assertFalse(requests[-1]['preview'])
        self.assertEqual(requests[-1]['input_mode'],'body')
        s.command(dict(active=False,mode='ink',menu=False))
        self.assertFalse(requests[-1]['menu']);self.assertFalse(requests[-1]['active'])

    def test_running_health_never_runs_dwell_or_processes_circle_serial(self):
        s=self.s;s.health_active=True;s.child=None;s.menu_open=False;s.menu_level='health'
        s.latest=dict(pid=12,sequence=3,timestamp_seconds=self.clock.now,points17=pose(),
            suite=dict(active=True,menu=True,mode='fight',input_mode='body',open_serial=999))
        s.lock=threading.Lock();s.pipeline.update=Mock();s.open_menu=Mock();s.sync_gamepad_backend=Mock()
        s.deliver_state()
        s.open_menu.assert_not_called();s.sync_gamepad_backend.assert_not_called()
        self.assertIsNone(s.selector.opened);s.health_view.receive.assert_called_once_with(s.latest)

    def test_device_dialog_blocks_background_health_selection(self):
        s=self.s;s.device_dialog=object();s.selector.done=True
        s.deliver_health_state(dict(points17=pose()),self.clock.now)
        s.health_view.pause.assert_called_once();s.health_view.action.assert_not_called()
        self.assertIsNone(s.selector.opened)

    def test_connection_center_retains_its_hand_route_and_pauses_health(self):
        s=self.s;s.device_view=NS(get_visible=lambda:True,update_hand=Mock())
        state=dict(pid=2,sequence=1,timestamp_seconds=self.clock.now,points17=pose())
        s.deliver_health_state(state,self.clock.now)
        s.health_view.pause.assert_called_once();s.device_view.update_hand.assert_called_once()
        s.health_view.action.assert_not_called()

    def test_exit_restores_previous_input_and_health_category(self):
        s=self.s;s.health_active=True;s.health_previous_control='gamepad';s.control_mode='body'
        s.open_menu=Mock();s.show_section=Mock();s.leave_health()
        self.assertFalse(s.health_active);self.assertEqual(s.control_mode,'gamepad')
        s.open_menu.assert_called_once_with(main=True);s.show_section.assert_called_once_with('health')

    def test_menu_key_pauses_exercise_without_triggering_game_menu(self):
        s=self.s;s.health_active=True;s.__test_globals__['Gdk']=NS(KEY_Escape=1,KEY_Menu=2,KEY_F1=3)
        self.assertTrue(s.key(None,NS(keyval=2)))
        s.health_view.action.assert_called_once_with('health_pause')

    def test_escape_from_welcome_can_return_without_a_camera(self):
        s=self.s;s.health_active=True;s.health_view.session=None;s.__test_globals__['Gdk']=NS(KEY_Escape=1,KEY_Menu=2,KEY_F1=3)
        self.assertTrue(s.key(None,NS(keyval=1)))
        s.health_view.action.assert_called_once_with('health_home')

if __name__=='__main__':unittest.main()
