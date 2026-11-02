"""Actual host methods with fake transports; no camera/provider/application IO."""
import ast,io,json,threading,time,types,unittest,urllib.request
from pathlib import Path
from unittest.mock import Mock
from player_context import input_context

tree=ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf8'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Suite')
nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('command','deliver_state')]
ns=dict(json=json,time=time,urllib=__import__('urllib'),input_context=input_context)
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'actual-suite-methods','exec'),ns)

class Opener:
    def __init__(self,on_open=None):self.requests=[];self.on_open=on_open
    def open(self,request,timeout):
        self.requests.append(json.loads(request.data))
        if self.on_open:self.on_open()
        return io.BytesIO(b'{"accepted":true}')

class HostPlayerTests(unittest.TestCase):
    def host(self):
        return types.SimpleNamespace(args=types.SimpleNamespace(base='http://fake-only'),stop=threading.Event(),
            generation=1,navigation_token=5,backend_lock=threading.Lock(),opener=Opener(),control_mode='body',
            health_active=False,backend_mode=lambda mode:mode)
    def call(self,host,data,**kw):return ns['command'](host,data,**kw)
    def test_same_visitor_across_modes_never_sends_lease_commands(self):
        host=self.host();host.visitor_store=types.SimpleNamespace(current=lambda:dict(id='a'*32))
        for mode in ('runner','fruit','taiko'):self.call(host,dict(active=True,mode=mode,menu=False))
        self.assertTrue(all('player_session' not in row and 'player_action' not in row for row in host.opener.requests))
    def test_visitor_change_does_not_release_or_acquire_backend_player(self):
        host=self.host();current=dict(id='a'*32);host.visitor_store=types.SimpleNamespace(current=lambda:dict(current))
        self.call(host,dict(active=True,mode='ink',menu=True));current['id']='b'*32
        self.call(host,dict(active=True,mode='ink',menu=True))
        self.assertTrue(all('player_session' not in row and 'player_action' not in row for row in host.opener.requests))
        self.assertEqual(host.visitor_source_session,'b'*32)
    def test_legacy_caller_fields_are_explicitly_removed(self):
        host=self.host()
        for action in ('release','acquire'):self.call(host,dict(mode='runner',player_session='old-session',player_action=action))
        self.assertTrue(all(not {'player_session','player_action','token','name'}.intersection(row) for row in host.opener.requests))
    def test_controller_mode_stays_gamepad(self):
        host=self.host();host.control_mode='gamepad';self.call(host,dict(active=True,mode='runner'))
        self.assertEqual(host.opener.requests[0]['input_mode'],'gamepad')
    def test_stale_generation_is_cancelled_before_transport(self):
        host=self.host()
        with self.assertRaises(RuntimeError):self.call(host,dict(mode='runner'),generation=0)
        self.assertFalse(host.opener.requests)
    def test_stale_navigation_is_cancelled_before_transport(self):
        host=self.host()
        with self.assertRaises(RuntimeError):self.call(host,dict(mode='runner'),navigation=4)
        self.assertFalse(host.opener.requests)
    def test_old_response_cannot_complete_after_generation_or_navigation_changes(self):
        for attribute in ('generation','navigation_token'):
            with self.subTest(attribute=attribute):
                host=self.host();host.opener=Opener(lambda:setattr(host,attribute,getattr(host,attribute)+1))
                with self.assertRaises(RuntimeError):self.call(host,dict(mode='runner'),generation=1,navigation=5)
                self.assertEqual(len(host.opener.requests),1)
    def test_only_pid_or_local_visitor_resets_dwell_and_never_controller_focus(self):
        host=self.host();host.lock=threading.Lock();host.pending_state=True;host.health_active=True
        host.latest=dict(pid=12,sequence=1,points17=[[.5,.5,.9]]*17,available=True,
            player_tracking=dict(identity='actor',epoch=1,input_allowed=True))
        host.visitor_source_session='a'*32
        current=Mock(side_effect=AssertionError('deliver_state must not read the visitor database'))
        host.visitor_store=types.SimpleNamespace(current=current)
        resets=[];seen=[];focus=object();host.gamepad_focus=focus
        host.selector=types.SimpleNamespace(reset=lambda:resets.append(1))
        host.pipeline=types.SimpleNamespace(ready=True,update=lambda s,now:None)
        host.suppress_gesture_navigation=lambda now:False;host.deliver_health_state=lambda s,now:seen.append(s);host.serial=None
        ns['deliver_state'](host);ns['deliver_state'](host)
        self.assertEqual(len(resets),1);self.assertEqual(seen[-1]['visitor_session'],'a'*32)
        host.latest['player_tracking']=dict(identity='other',epoch=9,input_allowed=False)
        host.latest.update(person_id='foreign',track_id='unreliable');ns['deliver_state'](host)
        self.assertEqual(len(resets),1);self.assertTrue(seen[-1]['available']);self.assertTrue(all(p[2]==.9 for p in seen[-1]['points17']))
        host.visitor_source_session='b'*32;ns['deliver_state'](host)
        self.assertEqual(len(resets),2);self.assertEqual(seen[-1]['visitor_session'],'b'*32)
        host.latest['pid']=13;ns['deliver_state'](host)
        self.assertEqual(len(resets),3);self.assertIs(host.gamepad_focus,focus)
        current.assert_not_called()

if __name__=='__main__':unittest.main()
