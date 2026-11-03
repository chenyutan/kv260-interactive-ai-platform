"""Portable tests of actual child adapters, with inert GTK/network boundaries."""
import ast
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
import urllib.request

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from action_dispatch import ActionDispatcher, SUPPORTED_ACTIONS
from sword.engine import Game as SwordGame
from taiko.session import Session as TaikoSession
from taiko.audio_clock import SilentClock


def req(action,ident='test-1',**extra):
    return dict(op='action',command_id=ident,action=action,**extra)


def load_class(path,name,**extra):
    """Run production method bodies unchanged; omit native imports and widgets."""
    node=next(x for x in ast.parse(path.read_text(encoding='utf-8')).body
              if isinstance(x,ast.ClassDef) and x.name==name)
    node.bases=[]
    namespace=dict(time=time,json=json,os=os,sys=sys,threading=threading,
        SimpleNamespace=NS,ActionDispatcher=ActionDispatcher,emit=Mock(),
        SilentClock=SilentClock,GLib=NS(idle_add=lambda f,*a,**kw:f(*a),PRIORITY_DEFAULT=0),urllib=urllib)
    namespace.update(extra)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(path),'exec'),namespace)
    return namespace[name],namespace


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.sent=[];self.calls=[];self.paused=True;self.pending=[]
        def execute(action,done):self.calls.append(action);self.pending.append(done)
        self.dispatch=ActionDispatcher('ink',lambda:self.paused,execute,
            lambda event,**fields:self.sent.append(dict(event=event,**fields)))

    def test_unpaused_and_arbitrary_payload_never_execute(self):
        self.paused=False;self.dispatch.dispatch(req('save'))
        self.assertEqual(self.sent[-1]['reason'],'experience_not_paused')
        self.paused=True
        for i,value in enumerate((req('save','extra',payload={'command':'clear'}),
                                  req('__dict__','method'),req('restart','wrong-mode'),
                                  req('save','../../unsafe'))):
            self.dispatch.dispatch(value)
        self.assertFalse(self.calls)
        self.assertTrue(all(not packet['ok'] for packet in self.sent))

    def test_async_idempotence_busy_conflict_and_double_done(self):
        self.dispatch.dispatch(req('save'));self.dispatch.dispatch(req('save'))
        self.assertEqual(self.calls,['save']);self.assertFalse(self.sent)
        self.dispatch.dispatch(req('undo'))
        self.assertEqual(self.sent[-1]['reason'],'command_id_conflict')
        self.dispatch.dispatch(req('undo','other'))
        self.assertEqual(self.sent[-1]['reason'],'action_busy')
        self.pending[0](True,'saved',path='painting.png');self.pending[0](False,'late')
        first=self.sent[-1];self.dispatch.dispatch(req('save'))
        self.assertEqual(self.sent[-1],first);self.assertEqual(self.calls,['save'])
        self.assertEqual(first['command_id'],'test-1')

    def test_exception_is_explicit_failure_and_cache_is_bounded(self):
        self.dispatch.execute=lambda *_:(_ for _ in ()).throw(ValueError('broken'))
        self.dispatch.dispatch(req('save'))
        self.assertIn('broken',self.sent[-1]['reason']);self.assertIsNone(self.dispatch.pending)
        self.dispatch.execute=lambda action,done:done(True)
        for i in range(200):self.dispatch.dispatch(req('undo',str(i)))
        self.assertEqual(len(self.dispatch.completed),128)

    def test_each_catalog_action_roundtrips_matching_id(self):
        for mode,actions in SUPPORTED_ACTIONS.items():
            packets=[]
            d=ActionDispatcher(mode,lambda:True,lambda action,done:done(True),
                lambda event,**kw:packets.append(kw))
            for action in actions:d.dispatch(req(action,mode+'-'+action))
            self.assertEqual(len(packets),len(actions))
            self.assertTrue(all(p['ok'] and p['command_id']==mode+'-'+p['action'] for p in packets))


class EmbeddedTests(unittest.TestCase):
    def controller(self,mode='fight',**globals):
        cls,ns=load_class(ROOT/'embedded_runner.py','Controller',**globals)
        c=cls.__new__(cls);c.args=NS(mode=mode,offline_preview=False,pose_url='http://localhost:8088/api/state')
        c.closed=False;c.paused=True;c.before='playing';c._share_epoch=1
        c.visitor_ink=None;c.visitor_reporter=None
        c.app=NS(game=NS(state='paused',paths=[]),area=NS(queue_draw=Mock()),
            gesture=NS(reset=Mock()),cancel_input=Mock(),reader=None,help_open=False,
            pose=NS(reset=Mock()),trails=[],publish=Mock(),animation=NS(cancel=Mock()),
            show_share=Mock(),share_dialog=None)
        return c,ns

    def test_restart_retains_countdown_behind_host_pause(self):
        c,_=self.controller();done=Mock()
        c.app.start=lambda:setattr(c.app.game,'state','countdown')
        c.execute_action('restart',done)
        self.assertTrue(c.paused);self.assertEqual(c.app.game.state,'paused')
        self.assertEqual(c.before,'countdown');done.assert_called_once()
        c.resume();self.assertEqual(c.app.game.state,'countdown')

    def test_continue_manual_pause_and_finishing(self):
        for phase in ('playing','finishing'):
            c,_=self.controller();c.before='paused';c.app.before_pause=phase
            c.execute_action('continue',Mock())
            self.assertTrue(c.paused);self.assertEqual(c.app.game.state,'paused')
            c.resume();self.assertEqual(c.app.game.state,phase)

    def test_continue_closes_existing_native_help_without_resuming_early(self):
        c,_=self.controller();c.app.help_open=True
        def close_help():c.app.help_open=False;c.app.game.state='finishing'
        c.app.help=close_help;c.execute_action('continue',Mock())
        self.assertFalse(c.app.help_open);self.assertTrue(c.paused)
        self.assertEqual(c.app.game.state,'paused');self.assertEqual(c.before,'finishing')
        c.resume();self.assertEqual(c.app.game.state,'finishing')

    def test_fruit_rules_and_mirror_use_fixed_native_controls(self):
        c,_=self.controller('fruit');c.app.mode_choice=NS(set_active_id=Mock())
        c.app.game.pause=lambda:setattr(c.app.game,'state','paused')
        c.app.start=lambda:setattr(c.app.game,'state','countdown')
        c.app.mirror=lambda w:setattr(c.app,'mirrored',w.get_active())
        c.execute_action('rules_classic',Mock())
        self.assertEqual(c.app.game.mode,'classic');self.assertTrue(c.paused)
        self.assertEqual(c.app.game.state,'paused');self.assertEqual(c.before,'countdown')
        c.execute_action('mirror_off',Mock());self.assertFalse(c.app.mirrored)
        c.execute_action('mirror_on',Mock());self.assertTrue(c.app.mirrored)

    def ink_request(self,result):
        jobs=[]
        class Thread:
            def __init__(self,target,**_):self.target=target
            def start(self):jobs.append(self.target)
        c,ns=self.controller('ink',threading=NS(Thread=Thread))
        requests=[]
        class Response(io.BytesIO):pass
        def open_request(request,**_):
            requests.append(json.loads(request.data));return Response(json.dumps(result).encode())
        fake=NS(request=NS(Request=urllib.request.Request,ProxyHandler=urllib.request.ProxyHandler,
            build_opener=lambda *_:NS(open=open_request)))
        ns['urllib']=fake;c._ink_module=NS(fetch_share=Mock(return_value={'qr':'ready'}))
        return c,jobs,requests

    def test_save_ack_only_after_server_accepts_and_qr_after_resume(self):
        c,jobs,requests=self.ink_request({'accepted':True,'path':'/paintings/art.png'})
        done=Mock();c.ink_action('save',done);done.assert_not_called()
        jobs.pop(0)();self.assertEqual(requests,[{'command':'save'}])
        self.assertTrue(done.call_args.args[0]);c.app.show_share.assert_not_called()
        c.resume();jobs.pop(0)();c.app.show_share.assert_called_once_with({'qr':'ready'})

    def test_ink_action_archives_the_returned_file_for_launch_session(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from visitor.store import VisitorStore
        import base64
        sys.path.insert(0,str(ROOT.parent/'bodyink-live'))
        with TemporaryDirectory() as temp:
            store=VisitorStore(Path(temp)/'visitor-data');session=store.current()
            path=Path(temp)/'ink-20261103-171000-000001.png'
            pixels=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1kAAAAASUVORK5CYII=')
            path.write_bytes(pixels)
            c,jobs,_=self.ink_request({'accepted':True,'path':str(path),'event_count':13})
            c.visitor_ink=Mock()
            with patch.dict(os.environ,{'INK_VISITOR_DATABASE':str(store.path),'INKSUITE_VISITOR_SESSION':session['id']}):
                done=Mock();c.ink_action('save',done);jobs.pop(0)()
            self.assertTrue(done.call_args.args[0]);c.visitor_ink.snapshot.assert_not_called()
            art,=store.view(session['token'])['artworks']
            self.assertEqual(art['marks'],13)
            self.assertEqual(store.artwork(session['token'],art['id'])[1],pixels)

    def test_save_rejection_and_offline_do_not_show_qr(self):
        c,jobs,_=self.ink_request({'accepted':False,'reason':'empty painting'})
        done=Mock();c.ink_action('save',done);jobs.pop(0)()
        self.assertFalse(done.call_args.args[0]);self.assertEqual(done.call_args.args[1],'empty painting')
        self.assertFalse(getattr(c,'_pending_share',None))
        c.args.offline_preview=True;c.ink_action('save',done)
        self.assertFalse(jobs);self.assertEqual(done.call_args.args[1],'offline_preview_no_artwork_changes')

    def test_new_scroll_and_theme_use_exact_api_payload(self):
        c,jobs,requests=self.ink_request({'accepted':True})
        c.ink_action('new_scroll',Mock());jobs.pop(0)()
        c.ink_action('theme_plum',Mock());jobs.pop(0)()
        self.assertEqual(requests,[{'command':'clear'},{'command':'theme','theme':'plum'}])
        self.assertEqual(c.app.animation.cancel.call_count,2)

    def test_server_disconnect_and_invalid_json_fail_explicitly(self):
        for payload in (b'not-json',b'[]',b'x'*262145):
            c,jobs,_=self.ink_request({})
            c.ink_action.__globals__['urllib'].request.build_opener=lambda *_:NS(open=lambda *a,**kw:io.BytesIO(payload))
            done=Mock();c.ink_action('save',done);jobs.pop(0)()
            self.assertFalse(done.call_args.args[0]);self.assertIn('artwork_request_failed',done.call_args.args[1])
        c,jobs,_=self.ink_request({})
        def disconnected(*a,**kw):raise OSError('offline')
        c.ink_action.__globals__['urllib'].request.build_opener=lambda *_:NS(open=disconnected)
        done=Mock();c.ink_action('save',done);jobs.pop(0)()
        self.assertIn('offline',done.call_args.args[1])

    def test_deferred_and_old_qr_cannot_cover_next_menu(self):
        c,_=self.controller('ink');c.deliver_share(1,{'id':'pending'})
        c.app.show_share.assert_not_called();c.resume()
        c.app.show_share.assert_called_once_with({'id':'pending'})
        dialog=Mock();c.app.share_dialog=dialog;c.pause();dialog.destroy.assert_called_once()
        c.deliver_share(1,{'id':'stale'});c.resume()
        self.assertEqual(c.app.show_share.call_count,1)

    def test_late_save_completion_cannot_queue_qr_for_new_menu(self):
        c,jobs,_=self.ink_request({'accepted':True,'path':'/paintings/old.png'})
        done=Mock();c.ink_action('save',done)
        c.resume();c.pause();jobs.pop(0)()
        self.assertTrue(done.call_args.args[0]);self.assertIsNone(c._pending_share)
        c.resume();self.assertFalse(jobs);c.app.show_share.assert_not_called()

    def test_paused_gallery_home_link_is_deferred_too(self):
        c,jobs,_=self.ink_request({})
        c.request_share();self.assertFalse(jobs)
        c.resume();jobs.pop(0)();c._ink_module.fetch_share.assert_called_once_with(None)


class SeparateChildrenTests(unittest.TestCase):
    def test_child_pipe_routes_actions_and_keeps_shutdown_final(self):
        for mode in ('sword','taiko'):
            source=ast.parse((ROOT/mode/'main.py').read_text(encoding='utf-8'))
            fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='read_commands')
            pending=[];ops=[];actions=[]
            ns=dict(json=json,GLib=NS(idle_add=lambda f,*a,**kw:pending.append((f,a)),PRIORITY_DEFAULT=0))
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-reader','exec'),ns)
            panel=NS(submit_operation=ops.append,handle_action=actions.append)
            value=req('restart',mode+'-restart')
            ns['read_commands'](panel,io.StringIO(json.dumps(value)+'\n{"op":"shutdown"}\n'+json.dumps(req('start'))+'\n'))
            self.assertEqual(ops,['shutdown']);self.assertFalse(actions)
            self.assertEqual(len(pending),1)
            pending[0][0](*pending[0][1]);self.assertEqual(actions,[value])

    def test_sword_restart_real_engine_stays_frozen(self):
        cls,_=load_class(ROOT/'sword/main.py','SwordPanel')
        p=cls.__new__(cls);p.active=False;p.stop=threading.Event();p.game=SwordGame()
        p.dwell=NS(reset=Mock());p.canvas=NS(queue_draw=Mock());p.clear_input=Mock()
        p.execute_action('restart',Mock())
        self.assertFalse(p.active);self.assertTrue(p.game.paused)
        before=p.game.snapshot();p.game.update(.5,{'x':.5,'y':.5,'valid':True})
        self.assertEqual(p.game.snapshot()['time'],before['time'])
        p.clear_input.assert_called_once()

    def taiko(self):
        cls,_=load_class(ROOT/'taiko/main.py','TaikoPanel')
        p=cls.__new__(cls);p.active=False;p.stop=threading.Event()
        p.audio=SilentClock();p.session=TaikoSession(p.audio);p.canvas=NS(queue_draw=Mock())
        p.clear_input=Mock();p.synthetic_seen=set();p.args=NS(audio_sink='autoaudiosink')
        p.difficulty=NS(set_active_id=lambda value:p.change_difficulty(NS(get_active_id=lambda:value)))
        return p

    def test_taiko_difficulty_rebuilds_once_and_audio_stays_paused(self):
        p=self.taiko();original=p.session.restart;p.session.restart=Mock(wraps=original)
        p.execute_action('difficulty_normal',Mock())
        self.assertEqual(p.session.difficulty,'normal');p.session.restart.assert_called_once()
        self.assertFalse(p.active);self.assertFalse(p.session.running);self.assertFalse(p.audio.playing)
        p.session.tick(time.monotonic()+20,[]);self.assertEqual(p.session.game.time,0.)

    def test_taiko_audio_failure_negative_ack_then_explicit_silent_recovers(self):
        p=self.taiko();bad=SilentClock();bad.error='device missing';p.make_audio=lambda _:bad
        done=Mock();p.execute_action('retry_audio',done)
        self.assertFalse(done.call_args.args[0]);self.assertEqual(done.call_args.args[1],'device missing')
        p.execute_action('silent',done)
        self.assertTrue(done.call_args.args[0]);self.assertIsNone(p.session.error)
        self.assertFalse(p.active);self.assertFalse(p.audio.playing)


if __name__=='__main__':unittest.main()
