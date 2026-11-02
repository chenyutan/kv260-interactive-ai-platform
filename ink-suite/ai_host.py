"""Native AI page coordination. Network and database work stay off GTK."""
import json
import threading
import time
import urllib.request
from gi.repository import GLib
from ai_assistant.contracts import AIError


class AIHost:
    def open_ai(self, kind=None,project=None,_origin=None):
        if self.switching or self.pending_action or self.visitor_busy:
            return
        if kind is None:kind='guide'
        if project is None:
            project={'health':'baduanjin','entertainment':'fruit'}.get(getattr(self.menu,'section',''),'ink')
        self.open_menu(main=True)
        self.ai_origin=_origin
        self.menu_level = 'ai'
        if getattr(self.ai_view, 'phone_open', False):
            self.ai_view.select('ai_phone_close')
        self.ai_view.set_context(kind=kind,project=project,share_url=None,qr=None,health_summary=None,
            return_label='返回项目' if _origin else '返回主菜单')
        self.ai_view.set_submitting(False)
        self.ai_view.set_jobs([])
        self.ai_view.set_notice('正在读取本次游玩的 AI 建议…')
        self.ai_session = None
        self._ai_choices = None
        self.ai_sync_choices()
        self.stack.set_visible_child_name('ai')
        self.sync_menu_music()
        self.ai_refresh()

    def open_project_ai(self,project):
        """Pause the originating project; guidance never starts or plays it."""
        if self.switching or self.pending_action or self.visitor_busy:return
        origin=None
        if getattr(self,'health_active',False):
            self.health_view.pause()
            origin=dict(type='health',session=self.health_view.session,project=project)
            self.health_active=False;self.health_choices=None
            if getattr(self,'health_voice',None):self.health_voice.set_active(False)
        elif self.menu_level=='photo':
            self.photo_view.leave();origin=dict(type='photo')
        elif self.menu_level=='control':
            origin=dict(type='control',project=project,main=getattr(self,'control_return_main',True),
                section=getattr(self,'control_return_section',None))
        elif self.menu_level=='actions':
            origin=dict(type='actions',project=project,child=self.child,page=self.action_page)
        self.open_ai('guide',project,_origin=origin)

    def ai_abandon_context(self):
        origin=getattr(self,'ai_origin',None);self.ai_origin=None
        # Home/Guide exits a suspended course explicitly, saving its record.
        if origin and origin['type']=='health':
            self.health_view.action('health_home')
            if getattr(self,'health_previous_control',None) in ('body','gamepad'):
                self.control_mode=self.health_previous_control
            self.health_previous_control=None

    def close_ai(self):
        origin=getattr(self,'ai_origin',None);self.ai_origin=None
        if not origin:self.open_menu(main=True);return
        self.navigation_token+=1;self.ai_view.set_submitting(False)
        if origin['type']=='health' and self.health_view.session is origin['session']:
            self.health_active=True;self.health_choices=None;self.menu_open=False;self.menu_level='health'
            self.selector.reset();self.stack.set_visible_child_name('health')
            if getattr(self,'health_voice',None):self.health_voice.set_active(True)
            self.sync_menu_music();return
        if origin['type']=='photo':
            self.menu_level='photo';self.menu_open=True;self.selector.reset()
            self.stack.set_visible_child_name('photo');self.photo_view.return_from_ai();self.sync_menu_music();return
        if origin['type']=='control':
            self.open_control_choice(origin['project'])
            self.control_return_main=origin['main'];self.control_return_section=origin['section'];return
        if origin['type']=='actions' and self.child is origin['child'] and self.mode==origin['project']:
            self.show_action_page(origin['page']);return
        self.open_menu(main=True)

    def ai_sync_choices(self):
        if self.menu_level != 'ai':
            return
        signature = (tuple(self.ai_view.choices), tuple(self.ai_view.rectangles))
        if signature != getattr(self, '_ai_choices', None):
            self._ai_choices = signature
            self.selector.set_choices(*signature, hit_test=self.ai_view.hit_index)
            self.selector.open(None, time.monotonic(), (self.latest or {}).get('points17'))

    def ai_tick(self):
        if self.stop.is_set():
            return False
        if self.menu_level == 'ai':
            self.ai_sync_choices()
            self.ai_refresh()
        return True

    def ai_refresh(self):
        if getattr(self, '_ai_reading', False) or getattr(self,'_ai_clearing',False) or self.menu_level != 'ai':
            return
        if not self.ai_service or not self.visitor_store:
            self.ai_view.set_notice('AI 服务尚未启动，本地体验仍可正常使用。')
            return
        self._ai_reading = True
        navigation = self.navigation_token
        epoch=getattr(self,'_ai_epoch',0)
        health_course=getattr(self.ai_view,'project','baduanjin')
        if health_course not in ('baduanjin','taichi'):health_course='baduanjin'
        def work():
            try:
                session = self.visitor_store.current()
                capabilities = self.ai_service.capabilities()
                jobs = self.ai_service.list_jobs(session['token'])
                health_summary=(self.ai_service.health_snapshot(session['token'],health_course)
                                if hasattr(self.ai_service,'health_snapshot') else None)
                # Recompute LAN address only when entering the page. Job polling
                # never triggers an inference, QR upload or network enumeration.
                share = self.visitor_portal.share() if self.ai_session is None else None
                error = None
            except Exception:
                session = capabilities = jobs = share = health_summary = None
                error = '读取 AI 建议失败，可稍后刷新。'
            def done():
                self._ai_reading = False
                if self.stop.is_set() or navigation != self.navigation_token or self.menu_level != 'ai' or epoch!=getattr(self,'_ai_epoch',0):
                    return False
                if error:
                    self.ai_view.set_notice(error)
                    return False
                changed = self.ai_session is None or self.ai_session['id'] != session['id']
                self.ai_session = session
                context = dict(capabilities=capabilities)
                if (getattr(self.ai_view,'kind',None)=='health' and
                        getattr(self.ai_view,'project',None)==health_course):
                    context['health_summary']=health_summary
                if share:
                    context.update(share_url=share.get('url'), qr=share.get('qr'))
                self.ai_view.set_context(**context)
                self.ai_view.set_jobs(jobs)
                if changed:
                    self.ai_view.set_notice('选择快捷建议，或扫码在手机上补充想法。')
                self.ai_sync_choices()
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True, name='ai-page-read').start()

    def ai_select(self, choice):
        navigation = self.navigation_token
        self.ai_view.select(choice)
        self.ai_sync_choices()
        if self.menu_level == 'ai' and navigation == self.navigation_token:
            # A dwell is one-shot. Rearm from the current wrist position so
            # another choice works, but stationary holding cannot resubmit.
            self.selector.open(None, time.monotonic(), (self.latest or {}).get('points17'))

    def ai_action(self, action):
        if self.stop.is_set() or self.menu_level != 'ai' or self.visitor_busy:
            return
        if action == 'ai_back':
            self.close_ai()
            return
        if action == 'ai_refresh':
            self.ai_refresh()
            return
        if action=='ai_clear' and getattr(self,'_ai_clearing',False):
            return
        if getattr(self, '_ai_working', False) and action!='ai_clear':
            return
        if not self.ai_service or not getattr(self, 'ai_session', None):
            self.ai_view.set_notice('请等待本次游玩信息载入。')
            return
        if action!='ai_clear' and not action.startswith(('ai_preset:', 'ai_review:', 'ai_apply:','ai_guide:','ai_health:')):
            return
        navigation = self.navigation_token
        session = dict(self.ai_session)
        generation = self.generation
        project=getattr(self.ai_view,'project','ink')
        if not hasattr(self,'_ai_mutation_lock'):self._ai_mutation_lock=threading.RLock()
        self._ai_action_serial=getattr(self,'_ai_action_serial',0)+1;serial=self._ai_action_serial
        if action=='ai_clear':
            self._ai_epoch=getattr(self,'_ai_epoch',0)+1;self._ai_clearing=True
            self.ai_view.set_clearing(True)
        self._ai_working = True
        self.ai_view.set_submitting(action.startswith(('ai_preset:','ai_review:','ai_guide:','ai_health:')))
        self.ai_view.set_notice('正在清空全部 AI 建议…' if action=='ai_clear' else '正在提交…' if not action.startswith('ai_apply:') else '正在设置创作主题…')
        def work():
            adopted = None
            try:
                current = self.visitor_store.current()
                if current['id'] != session['id'] or current['status'] != 'active':
                    raise AIError('stale', '本次游玩已结束，请重新打开 AI 助手。', 409)
                if action.startswith('ai_apply:'):
                    parts = action.split(':')
                    job = self.ai_service.get(session['token'], parts[1])
                    index = int(parts[2]) if len(parts) == 3 else 0
                    if job['kind'] != 'creation' or job['status'] != 'ready' or not 0 <= index < len(job['result']['steps']):
                        raise AIError('invalid_plan', '这条创作方案暂不可用。')
                    step = job['result']['steps'][index]
                    if step['theme'] not in ('landscape', 'bamboo', 'plum', 'mixed'):
                        raise AIError('invalid_plan', '方案包含不支持的主题。')
                    # Only the selected theme may change. AI can never issue a
                    # stroke, erase work, start a game, or modify scores.
                    with self.backend_lock:
                        if (self.stop.is_set() or navigation != self.navigation_token or
                                generation != self.generation or self.menu_level != 'ai' or serial!=self._ai_action_serial):
                            raise AIError('stale', '已取消过期的创作选择。')
                        request = urllib.request.Request(self.args.base + '/api/command',
                            data=json.dumps(dict(command='theme', theme=step['theme'])).encode(),
                            headers={'Content-Type':'application/json'})
                        with self.opener.open(request, timeout=4) as response:
                            reply = json.load(response)
                        if not reply.get('accepted'):
                            raise AIError('backend', '主题设置未完成，请重试。', 503)
                    adopted = dict(job_id=job['id'], step=index, title=job['result']['title'], **step)
                    message = '主题已设置。进入人体水墨后，按方案中的动作亲自创作。'
                else:
                    with self._ai_mutation_lock:
                        if serial!=self._ai_action_serial or self.stop.is_set():
                            raise AIError('stale','已取消过期请求。',409)
                        if action=='ai_clear':
                            if not hasattr(self.ai_service,'clear_all'):
                                raise AIError('unavailable','全终端清空尚未就绪，请更新服务后重试。',503)
                            cleared=self.ai_service.clear_all()
                            if cleared.get('remaining')!=0:
                                raise AIError('clear_incomplete','建议尚未全部清空，请稍后重试。',503)
                            message='所有账户及旧匿名 AI 建议已清空。固定说明、作品和成绩仍保留。'
                        else:
                            kind='health' if action.startswith('ai_health:') else 'guide' if action.startswith('ai_guide:') else 'creation' if action.startswith('ai_preset:') else 'review'
                            extra=dict(project=project) if kind in ('guide','health') else {}
                            job=self.ai_service.submit(session['token'],kind,action.split(':',1)[1],**extra)
                            message='已读取已有建议。' if job.get('cache_hit') else '已提交，正在等待回答；未联网时可先看本地说明。'
            except AIError as exc:
                message = exc.message
            except Exception:
                message = '操作暂未完成，请刷新后重试。'
            def done():
                if serial!=self._ai_action_serial:return False
                self._ai_working = False
                if action=='ai_clear':
                    self._ai_clearing=False;self._ai_epoch=getattr(self,'_ai_epoch',0)+1
                    self.ai_view.set_clearing(False)
                if self.stop.is_set() or navigation != self.navigation_token or self.menu_level != 'ai':
                    return False
                self.ai_view.set_notice(message)
                self.ai_view.set_submitting(False)
                if action=='ai_clear' and message.startswith('所有账户及旧匿名'):
                    self.ai_view.clear_finished();self.ai_adopted=None;self.ai_sync_choices()
                if adopted:
                    self.ai_adopted = dict(session_id=session['id'], **adopted)
                    self.open_control_choice('ink')
                else:
                    self.ai_refresh()
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True, name='ai-page-action').start()

