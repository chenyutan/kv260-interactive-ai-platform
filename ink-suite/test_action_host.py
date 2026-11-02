"""Independent host-action lifecycle tests on the real Suite AST; no GTK/IO."""
import ast,io,json,threading,time,unittest,urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from action_catalog import page as action_page,CONFIRM,TITLES as TITLE_MAP
from gesture_menu import DwellSelector,MODES
from radial_layout import rectangles as radial_rectangles

ROOT=Path(__file__).resolve().parent
class Clock:
    def __init__(self):self.now=100.
    def monotonic(self):return self.now
class Widget:
    def __init__(self):
        self.available=None;self.text=None;self.visible=None;self.items=[]
        self.mode_choices=MODES;self.rectangles=radial_rectangles()
    def set_availability(self,ready,message):self.available=ready;self.text=message
    def update_selection(self,result):self.selection=result
    def set_visible_child_name(self,name):self.visible=name
    def set_sensitive(self,value):self.sensitive=value
    def set_text(self,value):self.text=value
    def set_actions(self,title,items,message=None):
        self.text=title;self.items=items
        if message is not None:self.message=message
    def set_help(self,title,help):self.text=title;self.help=help
    def show(self):pass
    def hit_index(self,x,y):return None
class Child:
    def __init__(self):self.stdin=io.StringIO();self.exit_code=None
    def poll(self):return self.exit_code
    def messages(self):return [json.loads(x) for x in self.stdin.getvalue().splitlines()]

def pose(x=.5,y=.5):
    p=[[.5,.5,1.] for _ in range(17)];p[5]=[.6,.4,1.];p[6]=[.4,.4,1.];s=.2*16/9
    p[10]=[.5-(x-.5)*s*3.2/(16/9),.4+(y-.35)*s*2.6,1.];return p

def load_host(clock,timers):
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf8'))
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Suite')
    cls.bases=[]
    ns=dict(time=clock,json=json,threading=threading,urllib=urllib,MODES=MODES,TITLES=tuple(TITLE_MAP[x] for x in MODES),
        action_page=action_page,CONFIRM=CONFIRM,
        GLib=SimpleNamespace(timeout_add_seconds=lambda secs,fn,*args:timers.append((secs,fn,args)),
            timeout_add=lambda ms,fn,*args:timers.append((ms/1000,fn,args)),PRIORITY_DEFAULT=0))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),str(ROOT/'main.py'),'exec'),ns)
    ns['Suite'].__test_globals__=ns
    return ns['Suite']

class ActionHostTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.timers=[];Suite=load_host(self.clock,self.timers);self.s=Suite.__new__(Suite);s=self.s
        s.stop=threading.Event();s.child=Child();s.child_paused=True;s.mode='fight';s.menu_open=True;s.menu_level='actions'
        s.action_page='root';s.pending_action=None;s.action_counter=0;s.ready_child=None;s.action_notice=None;s.notice_until=0
        s.navigation_token=3;s.generation=8;s.switching=False;s.error_mode=None;s.backend_ready=True;s.resyncing=False
        s.backend_pid=123;s.resync_token=0;s.resync_after=0.;s.backend_lock=threading.Lock();s.args=SimpleNamespace(base='http://unused.invalid')
        s.menu_hand='right';s.pipeline=SimpleNamespace(ready=True,reason='ready');s.entry_pending=s.entry_deadline=0
        s.latest=dict(points17=pose());s.selector=DwellSelector();s.selector.open('right',self.clock.now,pose())
        for name in ('actions','menu','stack','home','status','loading_label','retry'):setattr(s,name,Widget())
        s.commands=[]
        s.command_async=lambda data,callback=None:s.commands.append((data,callback))
        s.toggle_fullscreen=Mock();s.destroy=Mock();s.select=Mock();s.stop_process=Mock()
    def action_messages(self):return [x for x in self.s.child.messages() if x.get('op')=='action']
    def dispatch_restart(self):
        self.s.choose_action('restart');self.s.choose_action('confirm:restart');return dict(self.s.pending_action)
    def ack(self,pending,**extra):
        value=dict(event='action_result',command_id=pending['command_id'],action=pending['action'],ok=True);value.update(extra)
        self.s.child_event(pending['child'],self.s.mode,pending['generation'],value)
    def complete_last(self,error=None):
        data,cb=self.s.commands[-1];self.assertIsNotNone(cb);cb(dict(accepted=error is None),error)
    def test_restart_requires_confirmation_before_stdin_action(self):
        self.s.choose_action('restart');self.assertEqual(self.s.action_page,'confirm:restart');self.assertFalse(self.action_messages())
        self.s.choose_action('confirm:restart');self.assertEqual(len(self.action_messages()),1)
        self.assertEqual(self.action_messages()[0]['action'],'restart')
    def test_confirm_identifier_cannot_be_injected_on_root(self):
        self.s.choose_action('confirm:restart');self.assertFalse(self.action_messages());self.assertIsNone(self.s.pending_action)
    def test_pending_selection_and_duplicate_ack_execute_only_once(self):
        p=self.dispatch_restart();self.s.choose_action('confirm:restart');self.assertEqual(len(self.action_messages()),1)
        self.ack(p);self.assertEqual(len(self.s.commands),1);self.ack(p);self.assertEqual(len(self.s.commands),1)
        self.complete_last();self.assertFalse(self.s.menu_open)
        self.assertEqual(sum(x.get('op')=='resume' for x in self.s.child.messages()),1)
    def test_wrong_id_or_action_ack_does_not_resume(self):
        p=self.dispatch_restart()
        for changes in (dict(command_id='other'),dict(action='save')):
            self.ack(p,**changes);self.assertIsNotNone(self.s.pending_action);self.assertFalse(self.s.commands)
    def test_old_child_generation_and_navigation_ack_are_ignored(self):
        for mutate in ('child','generation','navigation'):
            with self.subTest(mutate=mutate):
                self.setUp();p=self.dispatch_restart()
                if mutate=='child':self.s.child=Child()
                elif mutate=='generation':self.s.generation+=1
                else:self.s.navigation_token+=1
                self.ack(p);self.assertFalse(self.s.commands);self.assertTrue(self.s.menu_open)
    def test_timeout_does_not_retry_and_late_ack_does_not_resume(self):
        p=self.dispatch_restart();self.s.action_timeout(p['command_id']);self.assertEqual(self.s.action_page,'uncertain')
        self.s.action_timeout(p['command_id']);self.ack(p);self.assertEqual(len(self.action_messages()),1)
        self.assertFalse(self.s.commands);self.assertTrue(self.s.menu_open)
    def test_old_timeout_cannot_cancel_a_new_request(self):
        p=self.dispatch_restart();self.ack(p,ok=False,reason='failed')
        second=self.dispatch_restart();self.assertNotEqual(p['command_id'],second['command_id'])
        self.s.action_timeout(p['command_id']);self.assertEqual(self.s.pending_action['command_id'],second['command_id'])
    def test_non_boolean_success_is_not_a_success_ack(self):
        p=self.dispatch_restart();self.ack(p,ok='true')
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.commands)
        self.assertEqual(self.s.action_page,'root')
    def test_not_ready_or_stopped_cannot_submit_action(self):
        for condition in ('pipeline','backend','stopped'):
            with self.subTest(condition=condition):
                self.setUp();self.s.show_action_page('confirm:restart')
                if condition=='pipeline':self.s.pipeline.ready=False
                elif condition=='backend':self.s.backend_ready=False
                else:self.s.stop.set()
                self.s.choose_action('confirm:restart');self.assertFalse(self.action_messages())
    def test_failed_action_stays_paused_and_rearms_menu(self):
        p=self.dispatch_restart();self.ack(p,ok=False,reason='failed')
        self.assertTrue(self.s.menu_open);self.assertEqual(self.s.action_page,'root');self.assertFalse(self.s.commands)
        self.assertFalse(self.s.selector.done);self.assertIn('failed',self.s.actions.text)
    def test_help_back_returns_to_actions_without_child_command(self):
        self.s.choose_action('help');self.assertEqual(self.s.action_page,'help');self.assertEqual(self.s.selector.choices,('back',))
        self.s.choose_action('back');self.assertEqual(self.s.action_page,'root');self.assertTrue(self.s.menu_open)
        self.assertFalse(self.s.child.messages())
    def test_error_or_uncertain_back_goes_to_main_not_resume(self):
        for name in ('error','uncertain'):
            self.s.show_action_page(name);self.s.menu_back();self.assertEqual(self.s.menu_level,'main')
            self.assertEqual(self.s.stack.visible,'menu');self.assertTrue(self.s.menu_open)
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))
    def test_error_retry_selects_requested_failed_mode(self):
        self.s.error_mode='taiko';self.s.show_action_page('error');self.s.choose_action('retry')
        self.s.select.assert_called_once_with('taiko')
    def test_confirm_page_requires_new_hand_movement_and_empty_slots_inert(self):
        self.s.latest=dict(points17=pose(.5,.35));self.s.choose_action('restart')
        d=self.s.selector
        for i in range(100):
            t=100.+i/30;r=d.update(dict(pid=1,sequence=i,timestamp_seconds=t,points17=pose(.5,.35)),t)
            self.assertIsNone(r['selected'])
        self.s.show_action_page('help',points=pose(.5,.5));d=self.s.selector
        for i in range(100):
            t=104.+i/30;r=d.update(dict(pid=1,sequence=i,timestamp_seconds=t,points17=pose(.8,.35)),t)
            self.assertIsNone(r['selected'])
    def test_cancel_backend_failure_rearms_gesture(self):
        self.s.selector.done=True;self.s.choose_action('resume')
        self.ack(dict(self.s.pending_action));self.complete_last('network failed')
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.selector.done)
        self.assertIsNotNone(self.s.selector.opened)
    def test_late_cancel_ack_cannot_resume_replacement_child(self):
        self.s.cancel_menu();self.s.child=Child();self.complete_last()
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.child.messages())
    def test_late_ready_callback_cannot_close_new_menu_page(self):
        self.s.switching=True;child=self.s.child;self.s.child_event(child,'fight',8,dict(event='ready'))
        self.assertEqual(len(self.s.commands),1)
        self.s.show_action_page('help');self.complete_last()
        self.assertTrue(self.s.menu_open);self.assertEqual(self.s.action_page,'help')
        self.assertFalse(any(x.get('op')=='resume' for x in child.messages()))
    def test_duplicate_ready_event_only_starts_one_backend_request(self):
        self.s.switching=True;child=self.s.child
        for _ in range(2):self.s.child_event(child,'fight',8,dict(event='ready'))
        self.assertEqual(len(self.s.commands),1)

    def transport(self):
        requests=[]
        class Opener:
            def open(self,request,timeout):
                requests.append(json.loads(request.data));return io.StringIO('{"accepted":true}')
        self.s.opener=Opener();return requests
    def test_stale_navigation_is_rejected_before_backend_write(self):
        requests=self.transport();old=self.s.navigation_token;self.s.navigation_token+=1
        with self.assertRaises(RuntimeError):self.s.command(dict(active=True,mode='fight',menu=False),self.s.generation,old)
        self.assertFalse(requests)
    def test_navigation_change_while_waiting_for_transport_lock_is_rechecked(self):
        requests=self.transport();s=self.s;old=s.navigation_token
        class WaitingLock:
            def __enter__(self):s.navigation_token+=1
            def __exit__(self,*exc):return False
        s.backend_lock=WaitingLock()
        with self.assertRaises(RuntimeError):s.command(dict(active=True,mode='fight',menu=False),s.generation,old)
        self.assertFalse(requests)
    def test_current_navigation_may_write_backend_once(self):
        requests=self.transport()
        result=self.s.command(dict(active=True,mode='fight',menu=True),self.s.generation,self.s.navigation_token)
        self.assertTrue(result['accepted']);self.assertEqual(len(requests),1);self.assertTrue(requests[0]['menu'])
    def test_async_worker_captures_navigation_before_thread_runs(self):
        requests=self.transport();workers=[];idles=[];s=self.s
        class DeferredThread:
            def __init__(self,target,**kw):self.target=target
            def start(self):workers.append(self.target)
        ns=type(s).__test_globals__;ns['threading']=SimpleNamespace(Thread=DeferredThread)
        ns['GLib'].idle_add=lambda callback,*args,**kw:idles.append((callback,args))
        s.command_async=type(s).command_async.__get__(s,type(s))
        s.command_async(dict(active=True,mode='fight',menu=False),Mock())
        self.assertEqual(len(workers),1);s.navigation_token+=1;workers[0]()
        self.assertFalse(requests)
    def test_manual_menu_waits_for_backend_ack_before_accepting_action(self):
        self.s.menu_open=False;self.s.open_menu();self.assertFalse(self.s.backend_ready)
        self.assertTrue(self.s.menu_open);self.s.choose_action('restart')
        self.assertEqual(self.s.action_page,'root');self.assertFalse(self.action_messages())
        self.complete_last();self.assertTrue(self.s.backend_ready)
    def test_continue_waits_for_backend_ack_before_resume(self):
        self.s.cancel_menu();self.assertTrue(self.s.menu_open);self.assertFalse(self.s.backend_ready)
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))
        self.complete_last();self.assertFalse(self.s.menu_open)
        self.assertTrue(any(x.get('op')=='resume' for x in self.s.child.messages()))

    def test_continue_waits_for_child_ack_before_backend_or_resume(self):
        self.s.choose_action('resume');pending=dict(self.s.pending_action)
        self.assertEqual(pending['action'],'continue');self.assertEqual(self.action_messages()[0]['action'],'continue')
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.commands)
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))
        self.ack(pending)
        self.assertEqual(len(self.s.commands),1);self.assertTrue(self.s.menu_open)
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))
        self.complete_last();self.assertFalse(self.s.menu_open)
        self.assertEqual(sum(x.get('op')=='resume' for x in self.s.child.messages()),1)
    def test_continue_child_failure_retains_menu_without_backend_resume(self):
        self.s.choose_action('resume');pending=dict(self.s.pending_action)
        self.ack(pending,ok=False,reason='native_pause_restore_failed')
        self.assertIsNone(self.s.pending_action);self.assertTrue(self.s.menu_open)
        self.assertEqual(self.s.action_page,'root');self.assertFalse(self.s.commands)
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))
        self.assertIsNotNone(self.s.selector.opened);self.assertFalse(self.s.selector.done)
    def test_action_root_back_uses_continue_ack_path(self):
        self.s.menu_back();self.assertIsNotNone(self.s.pending_action)
        self.assertEqual(self.s.pending_action['action'],'continue');self.assertFalse(self.s.commands)

    def test_device_audio_repair_ack_keeps_connection_center_paused(self):
        self.s.device_view=SimpleNamespace(message=Widget())
        self.s.retry_child_audio();pending=dict(self.s.pending_action)
        self.assertEqual(pending['action'],'audio_retry');self.assertTrue(pending['stay_paused'])
        self.ack(pending)
        self.assertIsNone(self.s.pending_action);self.assertTrue(self.s.menu_open);self.assertFalse(self.s.commands)
        self.assertFalse(any(x.get('op')=='resume' for x in self.s.child.messages()))

    def test_device_audio_repair_timeout_does_not_resend_or_switch_page(self):
        self.s.device_view=SimpleNamespace(message=Widget())
        self.s.retry_child_audio();pending=dict(self.s.pending_action);self.s.action_timeout(pending['command_id'])
        self.assertIsNone(self.s.pending_action);self.assertEqual(self.s.action_page,'root')
        self.ack(pending);self.assertEqual(len(self.action_messages()),1);self.assertFalse(self.s.commands)

    def test_device_audio_repair_deduplicates_and_skips_modes_without_audio(self):
        self.s.device_view=SimpleNamespace(message=Widget())
        self.s.mode='retired';self.s.retry_child_audio();self.assertFalse(self.action_messages())
        self.s.mode='fight';self.s.retry_child_audio();self.s.retry_child_audio();self.assertEqual(len(self.action_messages()),1)

    def device_fixture(self):
        s=self.s
        s.device_snapshot={'audio':{'connected':False}}
        s.device_view=SimpleNamespace(message=Widget(),update_snapshot=Mock(),get_visible=lambda:False)
        s.device_policy=SimpleNamespace(update=lambda snapshot,now:[])
        s.device_dialog=None;s.device_alerts=[]

    def test_audio_recovery_edge_waits_for_busy_ack_then_dispatches_once(self):
        self.device_fixture();pending=self.dispatch_restart()
        self.s.devices_updated({'audio':{'connected':True}})
        self.assertEqual(len(self.action_messages()),1)
        self.assertIsNotNone(self.s.deferred_audio_retry)
        self.ack(pending,ok=False,reason='failed')
        self.s.check_devices();repair=dict(self.s.pending_action)
        self.assertEqual([x['action'] for x in self.action_messages()],['restart','audio_retry'])
        self.ack(repair)
        self.s.devices_updated({'audio':{'connected':True}});self.s.check_devices()
        self.assertEqual(len(self.action_messages()),2)
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.commands)

    def test_audio_recovery_during_game_waits_for_paused_menu_and_resync(self):
        self.device_fixture();self.s.menu_open=False;self.s.child_paused=False
        self.s.devices_updated({'audio':{'connected':True}})
        self.s.check_devices();self.assertFalse(self.action_messages())
        self.s.open_menu();self.s.check_devices();self.assertFalse(self.action_messages())
        self.complete_last();self.s.check_devices()
        self.assertEqual([x['action'] for x in self.action_messages()],['audio_retry'])
        messages=self.s.child.messages()
        self.assertEqual(messages[0]['op'],'pause')
        self.assertEqual(messages[-1]['op'],'action')
        self.assertTrue(self.s.pending_action['stay_paused'])

    def test_deferred_audio_repair_does_not_change_existing_success_resume_ack(self):
        self.device_fixture();pending=self.dispatch_restart()
        self.s.devices_updated({'audio':{'connected':True}})
        self.ack(pending);self.s.check_devices()
        self.assertEqual(len(self.action_messages()),1)
        self.complete_last();self.s.check_devices()
        self.assertFalse(self.s.menu_open);self.assertEqual(len(self.action_messages()),1)
        self.s.open_menu();self.complete_last();self.s.check_devices()
        self.assertEqual(len(self.action_messages()),2)
        repair=dict(self.s.pending_action);self.ack(repair)
        self.assertTrue(self.s.menu_open)
        self.assertEqual(sum(x.get('op')=='resume' for x in self.s.child.messages()),1)

    def test_deferred_audio_repair_never_crosses_child_generation_mode_or_close(self):
        for change in ('child','generation','mode','exit','close'):
            with self.subTest(change=change):
                self.setUp();self.device_fixture();self.s.menu_open=False
                self.s.retry_child_audio();child=self.s.child
                if change=='child':self.s.child=Child()
                elif change=='generation':self.s.generation+=1
                elif change=='mode':self.s.mode='fruit'
                elif change=='exit':child.exit_code=0
                else:self.s.stop.set()
                self.s.menu_open=True;self.s.flush_child_audio_retry()
                self.assertIsNone(self.s.deferred_audio_retry)
                self.assertFalse(child.messages());self.assertFalse(self.action_messages())

    def test_deferred_audio_timeout_and_duplicate_requests_do_not_replay(self):
        self.device_fixture();self.s.menu_open=False
        self.s.retry_child_audio();self.s.retry_child_audio()
        self.s.menu_open=True;self.s.check_devices();pending=dict(self.s.pending_action)
        self.s.retry_child_audio();self.s.action_timeout(pending['command_id'])
        self.s.check_devices();self.ack(pending);self.s.check_devices()
        self.assertEqual(len(self.action_messages()),1)
        self.assertIsNone(self.s.deferred_audio_retry)
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.commands)

    def test_deferred_audio_retry_uses_dispatch_navigation_for_ack(self):
        self.device_fixture();self.s.menu_open=False
        self.s.retry_child_audio();self.s.navigation_token+=1
        self.s.menu_open=True;self.s.check_devices();pending=dict(self.s.pending_action)
        self.assertEqual(pending['navigation'],self.s.navigation_token)
        self.ack(pending);self.assertIsNone(self.s.pending_action)
        self.assertTrue(self.s.menu_open);self.assertFalse(self.s.commands)

    def test_deferred_audio_uncertain_write_is_not_replayed(self):
        self.device_fixture();self.s.menu_open=False
        self.s.retry_child_audio();self.s.child.stdin=Mock()
        self.s.child.stdin.write.side_effect=BrokenPipeError('closed')
        self.s.menu_open=True;self.s.check_devices();self.s.check_devices()
        self.assertEqual(self.s.child.stdin.write.call_count,1)
        self.assertIsNone(self.s.pending_action);self.assertIsNone(self.s.deferred_audio_retry)

    def test_cancel_loading_detaches_uncommitted_mode_child(self):
        self.s.mode='ink';self.s.error_mode='fruit';self.s.switching=True
        child=self.s.child;old_generation=self.s.generation;workers=[]
        class DeferredThread:
            def __init__(self,target,args=(),**kw):self.target=target;self.args=args
            def start(self):workers.append((self.target,self.args))
        type(self.s).__test_globals__['threading']=SimpleNamespace(Thread=DeferredThread)
        self.s.open_menu(main=True)
        self.assertGreater(self.s.generation,old_generation);self.assertIsNone(self.s.child)
        self.assertIsNone(self.s.child_paused);self.assertIsNone(self.s.ready_child)
        self.assertFalse(self.s.switching);self.assertEqual(self.s.menu_level,'main')
        self.assertEqual(len(workers),1);fn,args=workers[0];fn(*args)
        self.s.stop_process.assert_called_once_with(child)
        self.complete_last();before=len(self.s.commands);self.s.cancel_menu()
        self.assertEqual(len(self.s.commands),before);self.assertTrue(self.s.menu_open)
        self.assertFalse(any(x.get('op')=='resume' for x in child.messages()))
    def test_ready_from_cancelled_launch_cannot_reclaim_host(self):
        self.s.switching=True;child=self.s.child;generation=self.s.generation
        self.s.child_event(child,'fruit',generation,dict(event='ready'));old_done=self.s.commands[-1][1]
        self.s.open_menu(main=True);new_mode=self.s.mode
        old_done(dict(accepted=True),None)
        self.s.child_event(child,'fruit',generation,dict(event='ready'))
        self.assertTrue(self.s.menu_open);self.assertEqual(self.s.mode,new_mode)
        self.assertIsNone(self.s.child);self.assertEqual(self.s.menu_level,'main')
        self.assertFalse(any(x.get('op')=='resume' for x in child.messages()))

    def test_menu_music_only_plays_on_main_menu(self):
        self.s.menu_music=Mock()
        for menu_open,level,switching,expected in (
                (True,'main',False,True),(True,'actions',False,False),
                (False,'main',False,False),(True,'main',True,False)):
            self.s.menu_open=menu_open;self.s.menu_level=level;self.s.switching=switching
            self.s.sync_menu_music()
            self.s.menu_music.set_menu_open.assert_called_with(expected)
        self.s.stop.set();self.s.switching=False;self.s.menu_open=True
        self.s.sync_menu_music();self.s.menu_music.set_menu_open.assert_called_with(False)

    def test_main_menu_music_pauses_before_experience_resume(self):
        self.s.menu_music=Mock();self.s.menu_level='main'
        self.s.cancel_menu();self.complete_last()
        self.s.menu_music.set_menu_open.assert_called_with(False)
        self.assertFalse(self.s.menu_open)
        self.assertEqual(self.s.child.messages()[-1]['op'],'resume')
        self.s.open_menu(main=True)
        self.s.menu_music.set_menu_open.assert_called_with(True)
        self.s.show_action_page('root')
        self.s.menu_music.set_menu_open.assert_called_with(False)

    def test_menu_music_reconnect_receives_current_inventory(self):
        self.device_fixture();self.s.menu_music=Mock();self.s.menu_level='main'
        snapshot={'audio':{'connected':True,'inventory':{'devices':[]}}}
        self.s.devices_updated(snapshot)
        self.s.menu_music.update_inventory.assert_called_once_with(snapshot)
        self.s.menu_music.set_menu_open.assert_called_with(True)

    def test_child_waits_for_pcm_pause_ack_without_blocking(self):
        self.s.menu_music=Mock();self.s.menu_music.status.return_value={'silenced':False}
        self.s.menu_open=False;self.s.send_child('resume');self.s.send_child('resume')
        self.assertFalse(self.s.child.messages());self.assertEqual(len(self.timers),1)
        _,fn,args=self.timers[0];self.assertTrue(fn(*args));self.assertFalse(self.s.child.messages())
        self.s.menu_music.status.return_value={'silenced':True}
        self.assertFalse(fn(*args));self.assertEqual(self.s.child.messages(),[{'op':'resume'}])

    def test_cancelled_menu_music_wait_never_resumes_a_new_navigation(self):
        self.s.menu_music=Mock();self.s.menu_music.status.return_value={'silenced':False}
        self.s.menu_open=False;self.s.send_child('resume');_,fn,args=self.timers[0]
        self.s.navigation_token+=1;self.s.menu_music.status.return_value={'silenced':True}
        self.assertFalse(fn(*args));self.assertFalse(self.s.child.messages())

    def test_menu_music_pause_timeout_does_not_loop_or_block_game(self):
        music=Mock();music.status.return_value={'silenced':False};self.s.menu_music=music
        self.s.menu_open=False;self.s.send_child('resume');_,fn,args=self.timers[0]
        self.clock.now+=3.;self.assertFalse(fn(*args))
        music.close.assert_called_once();self.assertIsNone(self.s.menu_music)
        self.assertEqual(self.s.child.messages(),[{'op':'resume'}]);self.assertEqual(len(self.timers),1)

if __name__=='__main__':unittest.main(verbosity=2)
