"""Creation feedback on real adapter methods, with inert HTTP/GTK boundaries."""
import ast
import io
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
import urllib.request

from ink_feedback import BRUSH_NAMES,brush_name,ink_message,menu_message

ROOT=Path(__file__).resolve().parent


def load_class(path,name,**extra):
    node=next(n for n in ast.parse(path.read_text(encoding='utf8')).body
              if isinstance(n,ast.ClassDef) and n.name==name)
    node.bases=[]
    namespace=dict(time=NS(monotonic=lambda:100.),json=json,os=os,
                   threading=threading,urllib=urllib,ink_message=ink_message,
                   brush_name=brush_name,menu_message=menu_message,
                   GLib=NS(idle_add=lambda f,*a,**kw:f(*a),PRIORITY_DEFAULT=0))
    namespace.update(extra)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                 str(path),'exec'),namespace)
    return namespace[name],namespace


class CreationFeedbackTests(unittest.TestCase):
    def controller(self):
        cls,ns=load_class(ROOT/'embedded_runner.py','Controller')
        c=cls.__new__(cls);c.closed=False;c.paused=False;c._gamepad_ink_busy=False
        c.sound=NS(ink_state=Mock());c._ink_original_publish=Mock()
        c.app=NS(status=Mock(),step=Mock(),metrics=Mock(),current_theme='mixed')
        return c,ns

    def test_live_progress_results_and_errors_survive_body_and_gamepad_updates(self):
        messages=('动作已识别，正在落墨。','已入队，前面还有2笔。',
                  '请先放下双手，回到自然站立。','请保持手臂完整入镜。',
                  '请先画竹干或梅枝，再添叶添花','作品同步失败，请稍后重试')
        c,_=self.controller();c._ink_pad_text='当前画笔：竹';c._ink_pad_valid=True
        for message in messages:
            with self.subTest(message=message):
                state=dict(message=message,phase='unchanged',event_count=4)
                before=dict(state);c.ink_publish('state',state)
                published=c._ink_original_publish.call_args.args[1]
                self.assertEqual(published['message'],message)
                self.assertEqual(state,before)
                self.assertEqual(published['event_count'],4)
                c.ink_gamepad_labels();c.app.status.set_text.assert_called_with(message)
        self.assertEqual(c.sound.ink_state.call_count,len(messages))

    def test_generic_or_old_packets_fall_back_without_inventing_progress(self):
        c,_=self.controller();c._ink_pad_text='当前画笔：主竹干';c._ink_pad_valid=True
        for state in ({},{'message':None},{'message':'体感控制'},
                      {'message':'手柄模式：LB/RB选择画法，按A落墨。'}):
            c.ink_publish('state',state);c.ink_gamepad_labels()
            c.app.status.set_text.assert_called_with('当前画笔：主竹干')
            self.assertEqual(c._ink_original_publish.call_args.args[1]['message'],'体感控制')

    def test_disconnect_is_visible_instead_of_old_creation_progress(self):
        c,_=self.controller();c._ink_state_message='动作已识别，正在落墨。'
        c._ink_pad_text='手柄未连接';c._ink_pad_valid=False
        c.ink_gamepad_labels();c.app.status.set_text.assert_called_with('手柄未连接')

    def test_notice_packets_keep_their_existing_delivery_channel(self):
        c,_=self.controller();c.ink_publish('notice','作品已保存')
        c._ink_original_publish.assert_called_once_with('notice','作品已保存')
        c.sound.ink_state.assert_not_called()

    def test_theme_names_match_backend_for_all_six_action_ids(self):
        tree=ast.parse((ROOT/'runtime/gamepad_stroke.py').read_text(encoding='utf8'))
        assignment=next(n for n in tree.body if isinstance(n,ast.Assign)
                        and any(isinstance(t,ast.Name) and t.id=='MAPPING' for t in n.targets))
        backend=ast.literal_eval(assignment.value)
        self.assertEqual({k:BRUSH_NAMES[k] for k in backend},backend)
        c,_=self.controller()
        for theme,names in backend.items():
            c.app.current_theme=theme
            for i,name in enumerate(names,1):
                with self.subTest(theme=theme,action=i):
                    c.gamepad=NS(ink=lambda now,a='A%02d'%i:
                        dict(valid=True,reason='gamepad',action=a,draw=False))
                    c.ink_gamepad_tick()
                    c.app.status.set_text.assert_called_with('当前画笔：'+name)

    def test_missing_theme_and_unknown_labels_use_compatible_fallback(self):
        c,_=self.controller();del c.app.current_theme
        c.gamepad=NS(ink=lambda now:dict(valid=True,reason='gamepad',action='A01',draw=False))
        c.ink_gamepad_tick();c.app.status.set_text.assert_called_with('当前画笔：山/飞鸟')
        self.assertEqual(brush_name('future-theme','A06'),'添叶/花')
        self.assertEqual(brush_name('mixed','A99'),'A99')

    def test_long_feedback_is_one_short_line(self):
        message=ink_message({'message':'正在绘制\n'+'山 '*100})
        self.assertLessEqual(len(message),96)
        self.assertNotIn('\n',message)
        self.assertTrue(message.startswith('正在绘制 '))


class NativeSaveFeedbackTests(unittest.TestCase):
    def request(self,result):
        jobs=[];trace=[];requests=[]
        class QueuedThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):jobs.append(self.target)
        def open_request(request,**kwargs):
            payload=json.loads(request.data);requests.append(payload)
            trace.append(payload['command'])
            return io.BytesIO(json.dumps(result).encode())
        fake=NS(request=NS(Request=urllib.request.Request,
            ProxyHandler=urllib.request.ProxyHandler,build_opener=lambda *_:NS(open=open_request)))
        cls,_=load_class(ROOT/'embedded_runner.py','Controller',
                         threading=NS(Thread=QueuedThread),urllib=fake)
        c=cls.__new__(cls);c.closed=False;c.paused=True;c._share_epoch=4
        c.args=NS(offline_preview=False,pose_url='http://unused.invalid/api/state')
        c.app=NS(publish=Mock(),animation=NS(cancel=Mock()))
        c.visitor_ink=NS(snapshot=Mock(side_effect=lambda:trace.append('snapshot')))
        c._ink_module=NS(fetch_share=Mock())
        return c,jobs,requests,trace

    def test_success_message_waits_for_acceptance_and_preserves_snapshot_and_payload(self):
        c,jobs,requests,trace=self.request(dict(accepted=True,path='/works/art.png',
            message='作品已保存在板卡，并下载到浏览器'))
        done=Mock();c.ink_action('save',done)
        done.assert_not_called();c.app.publish.assert_not_called()
        jobs.pop(0)()
        self.assertEqual(requests,[dict(command='save')])
        self.assertEqual(trace,['save','snapshot'])
        self.assertEqual(c._pending_share,('art.png',))
        done.assert_called_once_with(True,'作品已保存，正在准备下载码',path='/works/art.png')
        c.app.publish.assert_called_once_with('notice','作品已保存，正在准备下载码')
        c._ink_module.fetch_share.assert_not_called()

    def test_rejected_or_non_boolean_acceptance_never_claims_saved(self):
        for accepted in (False,1,'true'):
            with self.subTest(accepted=accepted):
                c,jobs,_,_=self.request(dict(accepted=accepted,reason='保存失败，空间不足'))
                done=Mock();c.ink_action('save',done);jobs.pop(0)()
                done.assert_called_once_with(False,'保存失败，空间不足',path='')
                c.visitor_ink.snapshot.assert_not_called()
                self.assertFalse(getattr(c,'_pending_share',None))

    def test_late_epoch_or_missing_share_support_does_not_claim_qr_preparation(self):
        for case in ('late','missing'):
            with self.subTest(case=case):
                c,jobs,_,_=self.request(dict(accepted=True,path='/works/art.png'))
                if case=='missing':c._ink_module=NS()
                done=Mock();c.ink_action('save',done)
                if case=='late':c._share_epoch+=1
                jobs.pop(0)()
                done.assert_called_once_with(True,'作品已保存到板卡',path='/works/art.png')
                self.assertFalse(getattr(c,'_pending_share',None))

    def test_snapshot_failure_retains_existing_failure_semantics(self):
        c,jobs,_,_=self.request(dict(accepted=True,path='/works/art.png'))
        c.visitor_ink.snapshot.side_effect=RuntimeError('归档不可用')
        done=Mock();c.ink_action('save',done);jobs.pop(0)()
        self.assertFalse(done.call_args.args[0])
        self.assertIn('归档不可用',done.call_args.args[1])
        c.app.publish.assert_not_called()
        self.assertFalse(getattr(c,'_pending_share',None))

    def test_new_scroll_still_snapshots_before_clear(self):
        c,jobs,requests,trace=self.request(dict(accepted=True))
        c.ink_action('new_scroll',Mock());jobs.pop(0)()
        self.assertEqual(requests,[dict(command='clear')])
        self.assertEqual(trace,['snapshot','clear'])
        self.assertEqual(c.app.animation.cancel.call_count,2)


class GamepadMenuFeedbackTests(unittest.TestCase):
    def host(self,notice='操作未完成：保存失败',deadline=108.):
        cls,_=load_class(ROOT/'gamepad/host.py','HostControls')
        view=NS(choices=('resume',),rectangles=((0.,0.,1.,1.),),
                set_availability=Mock(),update_selection=Mock())
        suite=NS(stop=threading.Event(),control_mode='gamepad',health_active=False,
            menu_open=True,menu_level='actions',switching=False,pending_action=None,
            visitor_busy=False,entry_pending=0.,entry_deadline=0.,actions=view,
            action_page='root',navigation_token=3,action_notice=notice,
            notice_until=deadline,choose_action=Mock())
        snapshot=dict(connected=True,axes={},pressed=(),released=())
        host=cls.__new__(cls);host.suite=suite;host.closed=False;host.last=99.
        host.navigation_until=0.;host.sequence=0
        host.reader=NS(snapshot=lambda:snapshot)
        host.pointer=NS(update=lambda *a,**kw:{})
        host.navigation=NS(update=lambda *a,**kw:0)
        host.dialog=lambda:None
        return host,suite,snapshot

    def test_active_failure_remains_visible_while_navigation_and_confirm_work(self):
        host,suite,snapshot=self.host();snapshot['pressed']=('A',)
        host.tick()
        suite.actions.set_availability.assert_called_once_with(True,'操作未完成：保存失败')
        suite.actions.update_selection.assert_called_once()
        suite.choose_action.assert_called_once_with('resume')

    def test_expiry_restores_normal_gamepad_hint(self):
        for deadline in (99.,100.):
            host,suite,_=self.host(deadline=deadline);host.tick()
            self.assertIn('方向键选择',suite.actions.set_availability.call_args.args[1])

    def test_old_or_other_page_has_no_notice_leak(self):
        host,suite,_=self.host();del suite.action_notice;del suite.notice_until
        host.tick();self.assertIn('方向键选择',suite.actions.set_availability.call_args.args[1])
        suite.action_notice='旧操作失败';suite.notice_until=108.;suite.menu_level='control'
        host.tick();self.assertIn('方向键选择',suite.actions.set_availability.call_args.args[1])


if __name__=='__main__':unittest.main()
