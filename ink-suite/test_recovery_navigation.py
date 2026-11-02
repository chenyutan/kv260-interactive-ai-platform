"""Actual Suite recovery/launch regressions with bounded synthetic transports.

Suite methods come from main.py AST; only URL responses, thread scheduling and
process creation are injected. No GTK, service, camera or real network opens.
"""
import contextlib
import io
import json
import threading
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock
import test_action_host as host_fixture


class InlineThread:
    def __init__(self,target,args=(),kwargs=None,**_):
        self.target,self.args,self.kwargs=target,args,kwargs or {}
    def start(self):self.target(*self.args,**self.kwargs)


class Opener:
    def __init__(self,error=None,result=None,while_waiting=None):
        self.error=error;self.result=result if result is not None else dict(accepted=True)
        self.while_waiting=while_waiting;self.calls=[]
    def open(self,request,timeout=None):
        self.calls.append(json.loads(request.data))
        if self.while_waiting:self.while_waiting()
        if self.error is not None:raise self.error
        return io.BytesIO(json.dumps(self.result).encode())


class RecoveryNavigationTests(unittest.TestCase):
    def setUp(self):
        fixture=host_fixture.ActionHostTests('test_restart_requires_confirmation_before_stdin_action');fixture.setUp()
        self.s=fixture.s;self.fixture=fixture;s=self.s
        s.control_mode='body';s.gamepad_host=None;s.backend_ready=False;s.pipeline.ready=False
        s.health_active=False;s.visitor_busy=False;s.entry_pending=s.entry_deadline=0.
        s.args.offline_preview=False;s.retry.hide=Mock();s.resyncing=False;s.backend_pid=123
        s.opener=Opener();self.output=io.StringIO()
    def failed_page(self,name='error'):
        s=self.s;s.child=None;s.error_mode='runner';s.show_action_page(name,message='synthetic backend unavailable')
    def enable_inline_async(self):
        s=self.s;ns=type(s).__test_globals__
        ns['threading']=SimpleNamespace(Thread=InlineThread)
        ns['GLib'].idle_add=lambda fn,*a,**kw:fn(*a)
        s.command_async=type(s).command_async.__get__(s,type(s))
    def controller(self):
        self.s.control_mode='gamepad';self.s.gamepad_host=SimpleNamespace(connected=True)
    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines() if line.startswith('{')]
    def test_error_home_works_without_pose_or_controller(self):
        self.failed_page();self.assertTrue(self.s.actions.available)
        self.s.choose_action('home')
        self.assertEqual(self.s.stack.visible,'menu');self.assertEqual(self.s.menu_level,'main')
    def test_error_back_works_without_pose_or_controller(self):
        self.failed_page();self.s.menu_back();self.assertEqual(self.s.menu_level,'main');self.assertEqual(self.s.stack.visible,'menu')
    def test_error_retry_works_without_pose_or_controller(self):
        self.failed_page();self.s.choose_action('retry');self.s.select.assert_called_once_with('runner')
    def test_wait_for_input_preserves_error_and_uncertain_safe_navigation(self):
        for name in ('error','uncertain'):
            with self.subTest(name=name):
                self.failed_page(name);self.s.wait_for_input();self.assertTrue(self.s.actions.available)
                self.s.choose_action('home');self.assertEqual(self.s.menu_level,'main')
    def test_controller_transport_outage_returns_diagnostic_offline_receipt(self):
        self.controller();self.s.opener=Opener(error=urllib.error.URLError('synthetic offline'))
        with contextlib.redirect_stdout(self.output):result=self.s.command(dict(active=True,mode='runner',menu=True),self.s.generation,self.s.navigation_token)
        self.assertEqual(result,dict(accepted=True,recognition_unavailable=True))
        self.assertEqual(self.events(),[dict(event='recognition_transport_unavailable',input_mode='gamepad',local_game_continues=True)])
        self.assertEqual(self.s.opener.calls[0]['mode'],'sword')
    def test_body_ink_health_and_inactive_never_bypass_transport_failure(self):
        cases=[('body','runner',False,True),('gamepad','ink',False,True),('gamepad','runner',True,True),('gamepad','runner',False,False)]
        for control,mode,health,active in cases:
            with self.subTest(control=control,mode=mode,health=health,active=active):
                self.s.control_mode=control;self.s.health_active=health
                self.s.opener=Opener(error=urllib.error.URLError('synthetic offline'))
                with contextlib.redirect_stdout(self.output),self.assertRaises(urllib.error.URLError):
                    self.s.command(dict(active=active,mode=mode,menu=True),self.s.generation,self.s.navigation_token)
        self.assertFalse(self.events())
    def test_HTTP_errors_and_explicit_service_rejections_never_bypass(self):
        self.controller()
        for code in (401,403,503):
            with self.subTest(code=code):
                self.s.opener=Opener(error=urllib.error.HTTPError('http://unused.invalid',code,'synthetic',None,None))
                with contextlib.redirect_stdout(self.output),self.assertRaises(urllib.error.HTTPError):
                    self.s.command(dict(active=True,mode='runner',menu=True))
        self.s.opener=Opener(result=dict(accepted=False,reason='synthetic reject'))
        with contextlib.redirect_stdout(self.output),self.assertRaisesRegex(RuntimeError,'synthetic reject'):
            self.s.command(dict(active=True,mode='runner',menu=True))
        self.assertFalse(self.events())
    def test_cancel_during_failed_network_wait_never_becomes_offline_success(self):
        self.controller()
        for field in ('generation','navigation_token','stop'):
            with self.subTest(field=field):
                generation,navigation=self.s.generation,self.s.navigation_token
                def cancel(field=field):
                    if field=='stop':self.s.stop.set()
                    else:setattr(self.s,field,getattr(self.s,field)+1)
                self.s.opener=Opener(error=urllib.error.URLError('synthetic offline'),while_waiting=cancel)
                with contextlib.redirect_stdout(self.output),self.assertRaises((urllib.error.URLError,RuntimeError)):
                    self.s.command(dict(active=True,mode='runner',menu=True),generation,navigation)
                self.s.stop.clear()
        self.assertFalse(self.events())
    def test_cancel_during_successful_network_wait_is_rejected_as_stale(self):
        self.controller()
        for field in ('generation','navigation_token','stop'):
            with self.subTest(field=field):
                generation,navigation=self.s.generation,self.s.navigation_token
                def cancel(field=field):
                    if field=='stop':self.s.stop.set()
                    else:setattr(self.s,field,getattr(self.s,field)+1)
                self.s.opener=Opener(while_waiting=cancel)
                with self.assertRaises(RuntimeError):
                    self.s.command(dict(active=True,mode='runner',menu=True),generation,navigation)
                self.s.stop.clear()
    def test_local_game_launch_ready_pause_and_resume_survive_transport_outage(self):
        s=self.s;self.controller();self.enable_inline_async()
        s.opener=Opener(error=urllib.error.URLError('synthetic offline'))
        s.child=None;s.mode='runner';s.menu_level='control';s.control_previous_mode='body'
        s.action_page='entry_help';s.control_target='runner';s.control_choice='gamepad'
        child=host_fixture.Child();child.pid=777
        def spawn(mode,generation):
            self.assertEqual(mode,'runner');self.assertEqual(generation,s.generation)
            s.child=child;s.child_paused=True
        s.spawn_mode=spawn
        with contextlib.redirect_stdout(self.output):
            s.choose_control('intro_start');self.assertIs(s.child,child);self.assertTrue(s.switching);self.assertIsNone(s.control_previous_mode)
            s.child_event(child,'runner',s.generation,dict(event='ready'))
            self.assertFalse(s.switching);self.assertFalse(s.menu_open);self.assertEqual(s.stack.visible,'experience')
            self.assertFalse(s.backend_ready);self.assertTrue(s.interaction_ready())
            self.assertIn(dict(op='resume'),child.messages())
            s.open_menu()
            self.assertTrue(s.menu_open);self.assertEqual(s.menu_level,'actions');self.assertFalse(s.backend_ready)
            s.cancel_menu()
        self.assertFalse(s.menu_open);self.assertEqual(s.stack.visible,'experience');self.assertFalse(s.backend_ready)
        self.assertGreaterEqual(sum(x==dict(op='resume') for x in child.messages()),2)
        self.assertGreaterEqual(len(self.events()),4)
        self.assertTrue(all(e['event']=='recognition_transport_unavailable' and e['local_game_continues'] for e in self.events()))
    def test_gamepad_backend_resync_does_not_claim_recovered_recognition(self):
        self.controller();self.enable_inline_async();s=self.s;s.mode='runner';s.menu_open=True
        s.opener=Opener(error=urllib.error.URLError('synthetic offline'))
        with contextlib.redirect_stdout(self.output):s.sync_gamepad_backend(dict(pid=123,suite=dict(active=False)),100.)
        self.assertFalse(s.resyncing);self.assertFalse(s.backend_ready)


if __name__=='__main__':unittest.main(verbosity=2)
