"""One native window, shared pose backend, one active XEmbed experience."""
import argparse,json,os,queue,signal,subprocess,sys,threading,time,urllib.request
from pathlib import Path
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib
from menu_view import MenuView,MODES,TITLES
from section_menu import SectionMenuView
from health.view import HealthView
from health.voice import HealthVoice
from gesture_menu import DwellSelector
from startup_readiness import PipelineReadiness
from action_menu import ActionMenu
from action_catalog import page as action_page,CONFIRM
from connectivity.controller import ConnectivityController
from connectivity.component_policy import ComponentPolicy
from connectivity.view import ConnectionCenter
from audio.menu_music import MenuMusic
from toolbar_autohide import MouseToolbar
from cursor_autohide import MouseCursor
from display_settings import DisplaySettings
from visitor.store import VisitorStore
from visitor.portal import Portal
from visitor.coordinator import FinishCoordinator
from visitor.view import VisitorView
from visitor.photo_service import PhotoService
from photo_studio import PhotoStudio
from pose_view import PoseView
from experience_names import CONTROL_NAMES, GAMEPAD_HELP, COMMON_GAMEPAD_HELP
from entry_help import briefing
from gamepad.host import HostControls
from ai_assistant.service import AIService
from ai_view import AIView
from ai_host import AIHost
from player_context import input_context

ROOT=Path(__file__).resolve().parent


def large_native_control(button, width=72):
    """Give auxiliary native buttons the same readable 48px target as cards."""
    button.set_size_request(width,48)
    provider=Gtk.CssProvider()
    provider.load_from_data(b'button {font-size:16px;min-height:42px;padding:2px 10px;}')
    button.get_style_context().add_provider(provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return button


class Suite(AIHost, Gtk.Window):
    control_mode='body'
    gamepad_host=None
    def __init__(self,args):
        super().__init__(title='墨境 · 国风体感工坊')
        self.args=args;self.stop=threading.Event();self.child=None;self.child_paused=None;self.socket=None
        self.source_input_key=None;self.visitor_source_session=None
        self.lock=threading.Lock();self.backend_lock=threading.Lock();self.latest=None;self.pending_state=False;self.serial=None
        self.mode='ink';self.menu_open=True;self.switching=False;self.generation=0;self.error_mode=None
        self.health_active=False;self.health_previous_control=None;self.health_choices=None
        self.health_pending_reports={}
        self.menu_level='main';self.action_page='root';self.pending_action=None;self.action_counter=0;self.ready_child=None
        self.control_mode='body';self.control_target=None;self.control_choice=None;self.control_previous_mode=None;self.gamepad_host=None
        self.deferred_audio_retry=None
        self.music_resume_pending=None
        self.action_notice=None;self.notice_until=0.
        self.backend_pid=None;self.backend_ready=False;self.resyncing=False
        self.pipeline=PipelineReadiness();self.resync_token=0;self.resync_after=0.;self.menu_hand=None;self.navigation_token=0
        self.selector=DwellSelector();self.selector.open(None,time.monotonic())
        self.entry_pending=max(0.,getattr(args,'entry_delay',0.));self.entry_deadline=0.
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.devices=None;self.device_snapshot={};self.device_policy=ComponentPolicy()
        self.menu_music=MenuMusic(enabled=not args.offline_preview)
        self.health_voice=HealthVoice(enabled=not args.offline_preview)
        self.device_alerts=[];self.device_dialog=None;self.device_timer=None
        self.display_dialog=None;self.saved_display_trial=None
        self.visitor_store=None;self.visitor_portal=None;self.visitor_coordinator=None
        self.photo_service=None;self.ai_service=None;self.ai_session=None;self.ai_timer=None
        self.visitor_busy=False;self.visitor_error=None;self.visitor_timer=None;self.visitor_finish_id=None
        self.set_default_size(1024,600);self.set_size_request(640,480)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);self.add(box)
        bar=Gtk.Box(spacing=10);bar.set_border_width(7)
        self.home=Gtk.Button(label='☰  体验菜单');self.home.connect('clicked',lambda *_:self.open_menu());bar.pack_start(self.home,False,False,0)
        self.status=Gtk.Label(label='娱乐 · 健康 · 创作');self.status.set_xalign(0);self.status.set_ellipsize(3);bar.pack_start(self.status,True,True,0)
        controls=Gtk.Button(label='体感 / 手柄');controls.connect('clicked',lambda *_:self.open_control_choice(self.mode));bar.pack_start(controls,False,False,0)
        devices=Gtk.Button(label='设备 / Wi‑Fi');devices.connect('clicked',lambda *_:self.open_connections());bar.pack_start(devices,False,False,0)
        full=Gtk.Button(label='全屏');full.connect('clicked',lambda *_:self.toggle_fullscreen());bar.pack_start(full,False,False,0)
        display=Gtk.Button(label='显示');display.connect('clicked',lambda *_:self.open_display_settings());bar.pack_start(display,False,False,0)
        close=Gtk.Button(label='退出');close.connect('clicked',lambda *_:self.destroy());bar.pack_start(close,False,False,0)
        for control in (self.home,controls,devices,full,display,close):large_native_control(control)
        self.mouse_toolbar=MouseToolbar(self,bar,self.toolbar_blocked)
        self.mouse_cursor=MouseCursor(self)
        self.stack=Gtk.Stack();self.stack.set_transition_type(Gtk.StackTransitionType.NONE);box.pack_start(self.stack,True,True,0)
        self.menu=SectionMenuView(self.select,visitor_controls=True);self.stack.add_named(self.menu,'menu')
        self.ai_view=AIView(self.ai_action);self.stack.add_named(self.ai_view,'ai')
        self.health_view=HealthView(self.leave_health,self.health_report,
            report_directory=ROOT/'data/health_reports',save_reports=not args.offline_preview,
            on_ai=lambda:self.open_project_ai(self.health_view.course_id),on_start=self.health_started)
        self.stack.add_named(self.health_view,'health')
        self.photo_view=PhotoStudio(self.leave_photo,on_ai=lambda:self.open_project_ai('photo_ink'))
        self.stack.add_named(self.photo_view,'photo')
        self.pose_view=PoseView(args.base,lambda:self.open_menu(main=True),enabled=not args.offline_preview)
        self.stack.add_named(self.pose_view,'pose')
        self.selector.set_choices(self.menu.mode_choices,self.menu.rectangles,hit_test=self.menu.hit_index)
        self.actions=ActionMenu(self.choose_action);self.stack.add_named(self.actions,'actions')
        self.visitor_view=VisitorView(self.visitor_choose);self.stack.add_named(self.visitor_view,'visitor')
        self.menu.set_availability(False,'正在连接摄像头与识别服务…')
        self.loading=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=20);self.loading.set_valign(Gtk.Align.CENTER);self.loading.set_halign(Gtk.Align.CENTER)
        self.loading_label=Gtk.Label(label='正在加载…');self.loading.pack_start(self.loading_label,False,False,0)
        self.retry=Gtk.Button(label='重试');self.retry.connect('clicked',lambda *_:self.select(self.error_mode) if self.error_mode else self.open_menu());self.loading.pack_start(self.retry,False,False,0)
        back=Gtk.Button(label='返回主菜单');back.connect('clicked',lambda *_:self.open_menu(main=True));self.loading.pack_start(back,False,False,0)
        for control in (self.retry,back):large_native_control(control,200)
        self.stack.add_named(self.loading,'loading');self.stack.set_visible_child_name('menu')
        self.connect('key-press-event',self.key);self.connect('destroy',self.close)
        self.device_view=ConnectionCenter(self,self.device_operation,self.ignore_device,self.connections_closed)
        self.show_all();self.retry.hide()
        if args.fullscreen:self.fullscreen()
        if not args.offline_preview and not getattr(args,'preserve_display',False):self.apply_saved_display()
        self.sync_menu_music()
        self.setup_visitors()
        self.ai_timer=GLib.timeout_add_seconds(1,self.ai_tick)
        self.gamepad_host=HostControls(self)
        threading.Thread(target=self.poll_state,daemon=True).start()
        self.command_async(dict(active=True,mode='ink',menu=True),self.initialized)
        if not args.offline_preview:
            self.devices=ConnectivityController(self.devices_updated,self.device_result,GLib.idle_add)
            self.devices.start();self.device_timer=GLib.timeout_add(500,self.check_devices)
        signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(self.destroy,priority=GLib.PRIORITY_DEFAULT))
        signal.signal(signal.SIGINT,lambda *_:GLib.idle_add(self.destroy,priority=GLib.PRIORITY_DEFAULT))
    @staticmethod
    def backend_mode(mode):
        # Platformer and runner use the existing nonpainting pose/circle profile. Its
        # children, reports, menu and scores retain distinct platformer/runner modes.
        # This avoids restarting the proven camera/DPU owner for a PS game.
        return 'sword' if mode in ('platformer','runner','pal','kart') else mode
    def command(self,data,generation=None,navigation=None):
        data=dict(data)
        # Visitor ownership stays local to its records and callback generations.
        # Do not forward obsolete single-player lease commands from old callers.
        data.pop('player_session',None);data.pop('player_action',None)
        owner=getattr(self,'visitor_store',None)
        if owner is not None:self.visitor_source_session=owner.current()['id']
        data['input_mode']=getattr(self,'control_mode','body')
        local_game=(data['input_mode']=='gamepad' and not getattr(self,'health_active',False)
            and data.get('active',True) and data.get('mode') in
            ('sword','fight','fruit','taiko','platformer','runner','pal','kart'))
        if getattr(self,'health_active',False) and data.get('active',True):
            # Keep inference running but suspend painting and circle detection.
            # Health navigation is handled locally, only on safe UI pages.
            data.update(menu=True,preview=False,input_mode='body')
        if 'mode' in data:data['mode']=self.backend_mode(data['mode'])
        request=urllib.request.Request(self.args.base+'/api/command',data=json.dumps(dict(command='suite',**data)).encode(),headers={'Content-Type':'application/json'})
        with self.backend_lock:
            if generation is not None and (generation!=self.generation or self.stop.is_set()):
                raise RuntimeError('已取消过期的模式切换')
            if navigation is not None and navigation!=self.navigation_token:
                raise RuntimeError('已取消过期的菜单操作')
            try:
                with self.opener.open(request,timeout=4) as response:result=json.load(response)
            except (urllib.error.URLError,TimeoutError,OSError) as exc:
                # Local controller games do not need pose data. Still try to
                # synchronize the shared service, but a transport outage must
                # not trap a controller user on Start/Resume. Explicit server
                # rejection and stale navigation are never treated as success.
                if (not local_game or isinstance(exc,urllib.error.HTTPError)
                    or self.stop.is_set() or (generation is not None and generation!=self.generation)
                    or (navigation is not None and navigation!=self.navigation_token)):
                    raise
                print(json.dumps(dict(event='recognition_transport_unavailable',
                    input_mode='gamepad',local_game_continues=True)),flush=True)
                return dict(accepted=True,recognition_unavailable=True)
            if self.stop.is_set() or (generation is not None and generation!=self.generation):
                raise RuntimeError('已取消过期的模式切换')
            if navigation is not None and navigation!=self.navigation_token:
                raise RuntimeError('已取消过期的菜单操作')
        if not result.get('accepted'):raise RuntimeError(result.get('message') or result.get('reason') or '识别服务未接受模式切换')
        return result
    def command_async(self,data,callback=None):
        generation=self.generation;navigation=self.navigation_token
        def work():
            try:result=self.command(data,generation,navigation);error=None
            except Exception as exc:result=None;error=str(exc)
            if callback and generation==self.generation and not self.stop.is_set():GLib.idle_add(callback,result,error,priority=GLib.PRIORITY_DEFAULT)
        threading.Thread(target=work,daemon=True).start()
    def initialized(self,result,error):
        if error:self.set_status('正在连接识别服务，将自动重试…')
        elif self.visitor_store and self.visitor_store.current()['status']=='ending':
            self.show_visitor_finish();self.finish_visitors(recovery=True)
        return False
    def setup_visitors(self):
        if self.args.offline_preview:return
        photos=None;portal=None;ai=None
        try:
            store=VisitorStore(getattr(self.args,'visitor_data',None) or ROOT/'data/visitors')
            store.replay_pending();store.recover_rounds()
            try:photos=PhotoService(store)
            except Exception as exc:
                # Photo processing is optional; existing works/reports remain
                # available even when its private queue cannot be opened.
                print('照片处理服务未启动：'+str(exc),file=sys.stderr,flush=True)
            try:ai=AIService(store,ROOT/'data/ai_assistant')
            except Exception:print('AI 服务未启动，本地体验仍可使用。',file=sys.stderr,flush=True)
            portal=Portal(store,port=getattr(self.args,'visitor_port',8091),photos=photos,ai=ai).start()
            self.ai_service=ai
            self.visitor_store=store;self.visitor_portal=portal
            self.visitor_source_session=store.current()['id']
            from health.ownership import PracticeOwnership
            self.health_record_owner=PracticeOwnership(store)
            self.photo_service=photos;self.photo_view.bind(photos,store,portal)
            self.visitor_coordinator=FinishCoordinator(store,self.args.base)
            self.selector.set_choices(self.menu.mode_choices,self.menu.rectangles,hit_test=self.menu.hit_index)
            self.visitor_timer=GLib.timeout_add_seconds(5,self.visitor_refresh_tick)
        except Exception as exc:
            if ai:ai.close()
            if portal:portal.close()
            elif photos:photos.close()
            self.visitor_error='游客下载服务未启动：'+str(exc)
    def visitor_refresh_tick(self):
        if self.stop.is_set():return False
        if getattr(self,'health_record_owner',None):self.health_record_owner.kick()
        if self.menu_level=='visitor' and not self.visitor_busy:self.refresh_visitors(capture=False)
        return True
    def open_pose_view(self):
        if self.switching or self.pending_action or self.visitor_busy or not self.interaction_ready():return
        self.open_menu(main=True)
        self.navigation_token+=1;self.menu_level='pose';self.backend_ready=False;self.resyncing=False
        self.selector.set_choices(self.pose_view.choices,self.pose_view.rectangles)
        self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
        self.stack.set_visible_child_name('pose');self.sync_menu_music()
        self.command_async(dict(active=True,mode=self.mode,menu=True,preview=True))
    def open_visitors(self):
        if self.switching or self.pending_action or self.visitor_busy:return
        self.open_menu(main=True);self.menu_level='visitor'
        self.selector.set_choices(self.visitor_view.choices);self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
        self.visitor_view.set_availability(True,'扫码带走本次画卷与成绩 · 右手停留 2 秒或鼠标选择')
        self.stack.set_visible_child_name('visitor');self.refresh_visitors()
    def refresh_visitors(self,capture=True):
        if self.visitor_busy:return
        if not self.visitor_portal:
            self.visitor_view.set_info(dict(message=self.visitor_error or '离线预览不建立游客会话。'))
            return
        navigation=self.navigation_token
        def work():
            try:
                if capture:self.visitor_coordinator.snapshot()
                info=self.visitor_portal.share()
            except Exception as exc:info=dict(message='读取未完成，请刷新重试：'+str(exc))
            def done():
                if navigation==self.navigation_token and self.menu_level=='visitor' and not self.stop.is_set():self.visitor_view.set_info(info)
                return False
            GLib.idle_add(done)
        threading.Thread(target=work,daemon=True,name='visitor-share').start()
    def show_visitor_finish(self):
        if self.switching or self.pending_action or self.visitor_busy:return
        self.open_menu(main=True);self.menu_level='visitor_confirm'
        self.visitor_finish_id=self.visitor_store.current()['id'] if self.visitor_store else None
        self.actions.set_actions('结束本次游玩？',[
            dict(id='visitor_cancel',title='继续游玩',subtitle='返回主菜单，保留本次画卷与成绩'),
            dict(id='visitor_confirm',title='归档并结束',subtitle='先保存画卷与成绩，再清空当前画卷；登录已绑定账户可继续下载',danger=True)],
            '只有选择“归档并结束”才清理当前画卷。')
        self.actions.set_availability(True,'右手停留 2 秒或鼠标选择；归档失败会保留当前画卷。')
        self.selector.set_choices(self.actions.choices);self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
        self.stack.set_visible_child_name('actions')
    def visitor_choose(self,action):
        if self.visitor_busy:return
        if action in ('visitor_back','visitor_cancel'):self.open_menu(main=True)
        elif action=='visitor_download':self.open_visitors()
        elif action=='visitor_refresh':self.refresh_visitors()
        elif action=='visitor_finish':self.show_visitor_finish()
        elif action=='visitor_confirm':self.finish_visitors()
    def finish_visitors(self,recovery=False):
        if self.visitor_busy:return
        if not recovery and getattr(self,'photo_service',None) and self.visitor_store:
            try:
                jobs=self.photo_service.list_jobs(self.visitor_store.current()['token'])
                if any(job.get('status') in ('queued','processing') for job in jobs):
                    self.actions.set_availability(True,'照片正在制作，请完成后再结束游玩。');return
            except Exception:
                self.actions.set_availability(True,'照片状态暂时无法确认，请稍后再结束游玩。');return
        if not self.visitor_coordinator:
            self.actions.set_availability(True,self.visitor_error or '游客归档服务不可用，请先恢复服务。');return
        self.visitor_busy=True;self.switching=True;self.generation+=1;self.navigation_token+=1
        generation=self.generation;identifier=self.visitor_finish_id;child=self.child;self.child=None;self.child_paused=None
        self.actions.set_availability(False,'正在归档画卷与报告，请稍候…')
        def work():
            try:
                self.command(dict(active=True,mode=self.mode,menu=True),generation)
                self.stop_process(child)
                self.visitor_coordinator.finish(identifier)
                self.command(dict(active=True,mode='ink',menu=True),generation);error=None
            except Exception as exc:error=str(exc)
            def done():
                if generation!=self.generation or self.stop.is_set():return False
                self.visitor_busy=False;self.switching=False
                if error:
                    self.menu_level='visitor_confirm';self.stack.set_visible_child_name('actions')
                    self.actions.set_availability(True,'结束尚未完成，画卷与成绩已保留。可再次确认重试：'+error[:90])
                    self.selector.set_choices(self.actions.choices);self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
                else:
                    self.mode='ink';self.open_menu(main=True);self.set_status('本次作品与报告已归档，欢迎下一位游客。')
                return False
            GLib.idle_add(done)
        threading.Thread(target=work,daemon=True,name='visitor-finish').start()
    def open_connections(self,wifi=False):
        if self.pending_action:return
        self.hide_mouse_toolbar()
        self.open_menu();self.device_view.update_snapshot(self.device_snapshot);self.device_view.open(wifi)
    def connections_closed(self):
        self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
    def device_operation(self,op,**data):
        return self.devices.request(op,**data) if self.devices else None
    def device_result(self,token,op,result):
        if self.stop.is_set():return False
        self.device_view.complete(token,op,result)
        if op=='audio_retry' and result.get('ok'):
            self.retry_child_audio()
            music=getattr(self,'menu_music',None)
            if music:music.retry()
        return False
    def sync_menu_music(self):
        music=getattr(self,'menu_music',None)
        if music:
            music.set_menu_open(self.menu_open and self.menu_level=='main' and
                                not self.switching and not self.stop.is_set())
            state=music.status();now=time.monotonic()
            if isinstance(state,dict) and state.get('enabled') is True and now>=getattr(self,'music_log_due',0.):
                self.music_log_due=now+3.
                fields=('state','desired','position_seconds','error','output_active','target_sink','menu_open')
                print(json.dumps(dict(event='menu_audio',monotonic=now,
                      **{key:state.get(key) for key in fields}),ensure_ascii=False),file=sys.stderr,flush=True)
    def retry_child_audio(self):
        child=self.child
        if (self.stop.is_set() or self.switching or self.mode not in MODES or
                not child or child.poll() is not None):return
        pending=self.pending_action
        if (pending and pending.get('action')=='audio_retry' and pending.get('child') is child
                and pending.get('generation')==self.generation):return
        # Recovery is an edge, so retain it while another command owns its ACK
        # or gameplay is active. It must never cross a child/mode replacement.
        self.deferred_audio_retry=(child,self.generation,self.mode)
        self.flush_child_audio_retry()
    def flush_child_audio_retry(self):
        request=getattr(self,'deferred_audio_retry',None)
        if request is None:return
        child,generation,mode=request
        if (self.stop.is_set() or child is not self.child or generation!=self.generation
                or mode!=self.mode or self.mode not in MODES or child.poll() is not None):
            self.deferred_audio_retry=None;return
        if self.pending_action or not self.menu_open or self.switching or self.resyncing:return
        # Consume before writing. Neither a timeout nor an uncertain write may
        # replay the retry, and its ACK retains the paused menu as before.
        self.deferred_audio_retry=None
        self.send_child('pause');self.action_counter+=1
        identifier='device-%d-%d-%d'%(self.generation,self.navigation_token,self.action_counter)
        self.pending_action=dict(command_id=identifier,action='audio_retry',child=child,
                                 generation=self.generation,navigation=self.navigation_token,stay_paused=True)
        try:
            child.stdin.write(json.dumps(dict(op='action',command_id=identifier,action='audio_retry'))+'\n');child.stdin.flush()
        except (OSError,BrokenPipeError,ValueError):
            self.pending_action=None;self.device_view.message.set_text('体验声音重连失败，请从菜单重试。');return
        GLib.timeout_add_seconds(22,self.action_timeout,identifier)
    def devices_updated(self,snapshot):
        if self.stop.is_set():return False
        old_audio=self.device_snapshot.get('audio',{}).get('connected')
        self.device_snapshot=snapshot;self.device_view.update_snapshot(snapshot)
        music=getattr(self,'menu_music',None)
        if music:music.update_inventory(snapshot)
        voice=getattr(self,'health_voice',None)
        if voice:voice.update_inventory(snapshot)
        health=getattr(self,'health_view',None)
        if health and hasattr(health,'set_audio_inventory'):health.set_audio_inventory(snapshot)
        self.sync_menu_music()
        if old_audio is False and snapshot.get('audio',{}).get('connected') is True:self.retry_child_audio()
        return False
    def ignore_device(self,key):
        self.device_policy.dismiss(key)
        self.device_alerts=[a for a in self.device_alerts if a.key!=key]
    def check_devices(self):
        if self.stop.is_set():return False
        self.flush_child_audio_retry()
        now=time.monotonic()
        self.device_alerts.extend(self.device_policy.update(self.device_snapshot,now))
        if self.device_dialog:
            key=self.device_dialog.component_key
            if self.device_policy.ready(key):self.device_dialog.response(100)
            return True
        if self.pending_action or self.switching or self.device_view.get_visible():return True
        while self.device_alerts:
            missing=self.device_alerts.pop(0)
            if missing.key=='camera' and (self.control_mode=='gamepad' or self.menu_level=='photo'):continue
            if self.device_policy.ready(missing.key):continue
            self.open_menu()
            dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,text=missing.message)
            dialog.format_secondary_text(missing.detail+'\n可以重连，也可以保持未连接。当前内容会保留。')
            dialog.add_button('保持未连接',Gtk.ResponseType.CANCEL);dialog.add_button('重连',Gtk.ResponseType.OK)
            dialog.component_key=missing.key;self.device_dialog=dialog
            dialog.hand_target=None;dialog.hand_since=0.;dialog.hand_key=None;dialog.hand_time=None
            dialog.hand_hint=Gtk.Label(label='右手移动，停留 2 秒选择；也可以直接点击。')
            dialog.get_content_area().pack_start(dialog.hand_hint,False,False,0)
            def response(d,r,key=missing.key):
                if d is not self.device_dialog:return
                self.device_dialog=None;d.destroy();self.ignore_device(key)
                self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
                if r==Gtk.ResponseType.OK:
                    self.open_connections(wifi=key=='wifi')
                    if key!='wifi':self.device_view.act(key+'_retry')
            dialog.connect('response',response);dialog.show_all();break
        return True
    def device_dialog_hand(self,result,key,now):
        d=self.device_dialog;cursor=result.get('cursor')
        if cursor is None:
            d.hand_target=None;d.hand_since=now;d.hand_time=None;return
        if key==d.hand_key:return
        if d.hand_time is not None and now-d.hand_time>.22:d.hand_target=None;d.hand_since=now
        d.hand_key=key;d.hand_time=now;x,y=cursor;w,h=d.get_size();target=None
        for b in d.get_action_area().get_children():
            origin=b.translate_coordinates(d,0,0);a=b.get_allocation()
            if origin and origin[0]<=x*w<=origin[0]+a.width and origin[1]<=y*h<=origin[1]+a.height:target=b;break
        if target is not d.hand_target:d.hand_target=target;d.hand_since=now
        if target is None:d.hand_hint.set_text('右手移动到下方选项，停留 2 秒选择。')
        else:
            progress=min(1.,(now-d.hand_since)/2.);d.hand_hint.set_text('%s · %d%%'%(target.get_label(),int(progress*100)))
            if progress>=1.:d.hand_target=None;target.clicked()
    def waiting_message(self):
        return {
            'service_unavailable':'正在连接摄像头与识别服务…',
            'camera_missing':'摄像头未连接 · 接好后会自动恢复',
            'warming_up':'正在等待摄像头与识别数据…',
            'stale_frame':'画面数据暂未更新 · 正在等待恢复…',
            'stream_stalled':'画面数据暂未更新 · 正在等待恢复…',
        }.get(self.pipeline.reason,'正在准备体感识别…')
    def wait_for_input(self,message=None):
        self.selector.reset();self.menu.set_availability(False,message or self.waiting_message())
        self.actions.set_availability(False,message or self.waiting_message())
        if self.menu_level=='visitor_confirm' and not self.visitor_busy:self.actions.set_availability(True,'体感暂不可用，仍可用鼠标继续游玩或确认归档。')
        if self.menu_level=='actions' and self.action_page in ('error','uncertain'):
            self.actions.set_availability(True,'识别暂不可用，仍可重试或返回主菜单。')
        # A short source hiccup only removes controls. It must not freeze an
        # already running game or restart its recovery countdown/music clock.
        if not self.gameplay_can_continue():self.send_child('pause')
        self.set_status(message or self.waiting_message())
    def interaction_ready(self):
        pad=getattr(self,'gamepad_host',None)
        if pad and pad.connected and (self.menu_open or self.control_mode=='gamepad'):return True
        return self.backend_ready and self.pipeline.ready
    def update_game_source(self,state,now):
        policy=getattr(self,'game_source_policy',None)
        if policy is None:
            from game_source_policy import SourceGapPolicy
            policy=SourceGapPolicy();self.game_source_policy=policy
        self.game_source_blocked=policy.update(state,now,
            offline=bool(getattr(self.args,'offline_preview',False)))
    def gameplay_can_continue(self):
        """Source grace belongs only to this already released game instance.

        It cannot start a game, dismiss a menu, undo an explicit pause, or
        transfer to a replacement child. Children discard invalid controls.
        """
        child=self.child
        if (self.mode not in ('fight','fruit','sword','taiko','runner','platformer','kart','pal')
                or not child or child.poll() is not None or self.menu_open or self.switching
                or getattr(self,'health_active',False) or self.menu_level=='photo'
                or self.pending_action or self.child_paused is not False
                or getattr(self,'gameplay_source_key',None)!=(child,self.generation)
                or getattr(self,'game_source_blocked',True)
                or getattr(self,'device_dialog',None) or getattr(self,'display_dialog',None)):
            return False
        device=getattr(self,'device_view',None)
        if device and device.get_visible():return False
        if self.control_mode=='gamepad':
            pad=getattr(self,'gamepad_host',None)
            return bool(pad and pad.connected)
        if self.backend_ready and self.pipeline.ready:
            self.gameplay_unready_since=None
            return True
        now=time.monotonic();since=getattr(self,'gameplay_unready_since',None)
        if since is None:
            since=now;self.gameplay_unready_since=now
        return 0<=now-since<=self.game_source_policy.timeout
    def set_status(self,value):
        if value!=getattr(self,'status_text',None):
            self.status_text=value;self.status.set_text(value)
    def show_section(self,section='home'):
        if self.switching or self.pending_action or self.visitor_busy:return
        if getattr(self,'health_active',False):
            self.health_view.action('health_home')
            if self.health_active:return
        if not self.menu_open or self.menu_level!='main':self.open_menu(main=True)
        self.navigation_token+=1
        self.menu.set_section(section)
        self.selector.set_choices(self.menu.mode_choices,self.menu.rectangles,hit_test=self.menu.hit_index)
        self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
        self.menu.update_selection({});self.stack.set_visible_child_name('menu')
        self.sync_menu_music()
    def enter_photo(self):
        if self.switching or self.pending_action or self.visitor_busy or self.stop.is_set():return
        if self.visitor_store and self.visitor_store.current()['status']=='ending':
            self.show_visitor_finish();return
        self.open_menu(main=True)
        if getattr(self,'health_active',False):return
        self.generation+=1;self.navigation_token+=1
        old=self.child;self.child=None;self.child_paused=None;self.ready_child=None
        if old:threading.Thread(target=self.stop_process,args=(old,),daemon=True).start()
        self.menu_level='photo';self.menu_open=True;self.selector.reset()
        self.stack.set_visible_child_name('photo');self.photo_view.enter()
        self.sync_menu_music();self.set_status('照片水墨 · 手机上传或从板卡选择照片')
        self.command_async(dict(active=True,mode=self.mode,menu=True,preview=False))
    def leave_photo(self):
        self.photo_view.leave();self.open_menu(main=True);self.show_section('creation')
    def enter_health(self,course_id='baduanjin'):
        if course_id not in ('baduanjin','taichi'):raise ValueError('Unknown health course')
        if self.switching or self.pending_action or self.visitor_busy or self.stop.is_set():return
        visitor=self.visitor_store.current() if self.visitor_store else None
        if visitor and visitor['status']=='ending':
            self.show_visitor_finish();return
        self.health_entry_visitor_id=visitor['id'] if visitor else None
        if getattr(self,'pose_view',None):self.pose_view.stop_preview()
        self.hide_mouse_toolbar();self.send_child('pause')
        self.generation+=1;self.navigation_token+=1
        old=self.child;self.child=None;self.child_paused=None;self.ready_child=None
        if old:threading.Thread(target=self.stop_process,args=(old,),daemon=True).start()
        self.health_previous_control=self.control_mode;self.control_mode='body'
        self.health_active=True;self.health_choices=None;self.menu_open=False;self.menu_level='health'
        self.backend_ready=False;self.resyncing=False;self.pending_action=None
        self.selector.reset();self.health_view.enter(course_id=course_id);self.stack.set_visible_child_name('health')
        if getattr(self,'health_voice',None):self.health_voice.set_active(True)
        self.sync_menu_music();self.home.set_sensitive(True)
        self.command_async(dict(active=True,mode=self.mode,menu=True))
        self.set_status(('太极' if course_id=='taichi' else '八段锦')+' · 选择学习方式；练习中用 Menu 键或屏幕按钮暂停')
    def leave_health(self):
        if not getattr(self,'health_active',False):return
        self.health_active=False;self.health_choices=None
        if getattr(self,'health_voice',None):self.health_voice.set_active(False)
        if self.health_previous_control in ('body','gamepad'):self.control_mode=self.health_previous_control
        self.health_previous_control=None
        self.open_menu(main=True);self.show_section('health')
    def health_started(self,practice_id):
        recorder=getattr(self,'health_record_owner',None)
        if recorder:recorder.bind(practice_id,getattr(self,'health_entry_visitor_id',None))
    def health_report(self,report):
        # The lesson engine writes an atomic, bounded statistics-only record.
        # Do not place a medical/health score on the game leaderboard.
        identifier=report.get('session_id')
        recorder=getattr(self,'health_record_owner',None)
        if recorder:recorder.saved(report)
        if not hasattr(self,'health_pending_reports'):self.health_pending_reports={}
        if report.get('report_path') and not report.get('report_save_error'):
            self.health_pending_reports.pop(identifier,None)
        elif identifier:self.health_pending_reports[identifier]=report
        print(json.dumps(dict(event='health_practice_saved' if report.get('report_path') else 'health_practice_pending',report_path=report.get('report_path'),
            course=report.get('course_id','baduanjin'),mode=report.get('mode'),
            course_timer_finished=report.get('course_timer_finished',False)),ensure_ascii=False),file=sys.stderr,flush=True)
    def sync_health_voice(self):
        voice=getattr(self,'health_voice',None)
        if voice:
            snapshot=dict(self.health_view.current)
            snapshot['course_id']=getattr(self.health_view,'course_id','baduanjin')
            if snapshot.get('phase')=='video':
                voice.set_active(False)
                self.health_voice_marker=None
                self.health_view.set_voice_status(False,'教学视频使用原声讲解')
                return
            voice.set_active(True)
            snapshot['session_id']=getattr(self.health_view.session,'session_id',None)
            marker=(snapshot['session_id'],snapshot.get('lesson_id'),snapshot.get('phase'),snapshot.get('elapsed',0))
            previous=getattr(self,'health_voice_marker',None)
            if previous and (previous[0]!=marker[0] or
                    previous[:3]==marker[:3] and marker[3]<previous[3]-.1):
                # A new course or an explicit rewind may replay its opening
                # cue; a normal pause/resume must not repeat an old sentence.
                voice.set_active(False);voice.set_active(True)
            self.health_voice_marker=marker
            voice.sync(snapshot)
            status=voice.status()
            self.health_view.set_voice_status(bool(status.get('enabled')),status.get('message',status.get('voice_status','文字指导')))
    def suppress_gesture_navigation(self,now):
        """Controller ownership cancels dwell, without hiding pose feedback."""
        pad=getattr(self,'gamepad_host',None)
        gate=getattr(pad,'gestures_blocked',None)
        blocked=bool(gate(now)) if callable(gate) else False
        previous=getattr(self,'gesture_navigation_suppressed',False)
        self.gesture_navigation_suppressed=blocked
        if not blocked:return False
        self.selector.reset()
        self.health_choices=None
        device=getattr(self,'device_view',None)
        if device and (not previous or getattr(device,'hand_target',None) is not None):
            device.reset_hand()
        dialog=getattr(self,'device_dialog',None)
        if dialog:
            dialog.hand_target=None;dialog.hand_since=0.;dialog.hand_key=None;dialog.hand_time=None
        if not previous:
            # Clear only the old body cursor once, before the pad paints focus.
            # Repeating this every pose callback would erase controller focus.
            if getattr(self,'health_active',False):view=self.health_view
            elif self.menu_level=='main':view=self.menu
            elif self.menu_level in ('actions','control','visitor_confirm'):view=self.actions
            elif self.menu_level=='visitor':view=self.visitor_view
            elif self.menu_level=='pose':view=self.pose_view
            elif self.menu_level=='ai':view=self.ai_view
            else:view=None
            if view is not None:view.update_selection({})
        return True
    def deliver_health_state(self,state,now):
        gestures_blocked=self.suppress_gesture_navigation(now)
        device=getattr(self,'device_view',None)
        modal=bool(getattr(self,'device_dialog',None) or getattr(self,'display_dialog',None))
        device_open=bool(device and device.get_visible())
        if modal or device_open:
            self.health_view.pause();self.health_view.receive(state)
            self.sync_health_voice()
            self.health_choices=None;self.health_view.update_selection({})
            if device_open and not modal and not gestures_blocked:
                if self.selector.opened is None:self.selector.open(None,now,(state or {}).get('points17') if isinstance(state,dict) else None)
                result=self.selector.update(state,now)
                sample=(state.get('pid'),state.get('sequence'),state.get('timestamp_seconds')) if isinstance(state,dict) else None
                device.update_hand(result,sample,now)
                if self.selector.done:self.selector.done=False
            else:self.selector.reset()
            return False
        self.health_view.receive(state)
        self.sync_health_voice()
        if isinstance(state,dict):
            suite=state.get('suite') or {}
            mismatch=(not suite.get('active') or not suite.get('menu') or
                suite.get('mode')!=self.backend_mode(self.mode) or suite.get('input_mode','body')!='body')
            if mismatch and not self.resyncing and now>=self.resync_after:
                self.resyncing=True;self.resync_token+=1;token=self.resync_token
                navigation=self.navigation_token
                def restored(_,error):
                    if token!=self.resync_token:return False
                    self.resyncing=False
                    if navigation!=self.navigation_token or not self.health_active or self.stop.is_set():return False
                    self.backend_ready=not bool(error);self.resync_after=time.monotonic()+1.
                    return False
                self.command_async(dict(active=True,mode=self.mode,menu=True),restored)
        choices=tuple(self.health_view.choices)
        if gestures_blocked or not self.health_view.gesture_navigation:
            self.selector.reset();self.health_choices=None
            return False
        if choices!=self.health_choices or self.selector.opened is None:
            self.selector.set_choices(choices,self.health_view.rectangles,hit_test=self.health_view.hit_index)
            self.selector.open(None,now,(state or {}).get('points17') if isinstance(state,dict) else None)
            self.health_choices=choices
        result=self.selector.update(state,now)
        self.health_view.update_selection(dict(result,sample_key=(state or {}).get('sequence') if isinstance(state,dict) else None))
        if result.get('selected'):
            self.health_view.action(result['selected']);self.health_choices=None
        return False
    def navigate_back(self):
        """One-level Back for keyboard and controller; Home remains explicit."""
        if self.switching:
            self.open_menu();return
        if getattr(self,'health_active',False):
            self.health_view.back();return
        if self.menu_level=='photo':self.leave_photo();return
        if self.menu_level=='control':
            if self.action_page=='entry_help':self.choose_control('intro_back')
            else:self.cancel_control_choice()
            return
        if self.menu_level=='ai':
            if getattr(self.ai_view,'clear_confirm',False):self.ai_select('ai_clear_cancel')
            elif getattr(self.ai_view,'project_picker',False):self.ai_select('ai_project_close')
            elif getattr(self.ai_view,'phone_open',False):self.ai_select('ai_phone_close')
            else:self.close_ai()
            return
        if self.menu_level in ('visitor','visitor_confirm','pose','ai'):
            self.open_menu(main=True);return
        if self.menu_open and self.menu_level=='main':
            # A paused game may stay embedded behind Home for a later explicit
            # selection. Back belongs to the visible menu, not that old child.
            if getattr(self.menu,'section','home')!='home':self.show_section('home')
            return
        if self.menu_open and self.menu_level=='actions':self.menu_back()
        elif not self.menu_open or self.switching:self.open_menu()

    def key(self,_,event):
        if getattr(self,'device_dialog',None) or (getattr(self,'device_view',None) and self.device_view.get_visible()):return False
        if event.keyval in (Gdk.KEY_Escape,Gdk.KEY_Menu,Gdk.KEY_F1):
            if event.keyval!=Gdk.KEY_Escape and getattr(self,'health_active',False) and self.health_view.session is not None:
                self.health_view.action('health_pause')
            else:self.navigate_back()
            return True
        return False
    def toggle_fullscreen(self):
        self.hide_mouse_toolbar()
        if self.get_window().get_state() & Gdk.WindowState.FULLSCREEN:self.unfullscreen()
        else:self.fullscreen()
    def toolbar_blocked(self):
        view=getattr(self,'device_view',None)
        return bool(self.stop.is_set() or self.device_dialog or getattr(self,'display_dialog',None) or
                    (view and view.get_visible()) or self.action_page.startswith('confirm:'))
    def open_display_settings(self):
        self.hide_mouse_toolbar()
        if self.display_dialog:self.display_dialog.present();return
        if not self.menu_open:self.open_menu()
        def closed():self.display_dialog=None
        self.display_dialog=DisplaySettings(self,closed=closed)
    def apply_saved_display(self):
        # A saved preference is tied to a previously human-confirmed EDID.
        # Its separate watchdog also restores the old mode if this GUI fails.
        from display_modes import apply_confirmed
        def work():
            result=apply_confirmed()
            def ready():
                if not self.stop.is_set() and result.get('applied'):
                    self.saved_display_trial=result
                    self.connect_after('draw',self.complete_saved_display)
                    self.queue_draw()
                return False
            GLib.idle_add(ready)
        threading.Thread(target=work,daemon=True,name='display-preference').start()
    def complete_saved_display(self,*_):
        trial=self.saved_display_trial
        if not trial or not self.get_mapped():return False
        wanted=tuple(map(int,trial['size'].split('x')))
        window=self.get_window()
        if window and window.get_state() & Gdk.WindowState.FULLSCREEN and self.get_size()!=wanted:return False
        self.saved_display_trial=None
        def finish():
            from display_modes import complete_confirmed_startup
            try:complete_confirmed_startup(trial['token'])
            except Exception as error:print('Display preference fallback: '+str(error),file=sys.stderr,flush=True)
        threading.Thread(target=finish,daemon=True,name='display-first-frame').start()
        return False
    def hide_mouse_toolbar(self):
        toolbar=getattr(self,'mouse_toolbar',None)
        if toolbar:toolbar.suppress()
    def poll_state(self):
        while not self.stop.is_set():
            try:
                with self.opener.open(self.args.base+'/api/state',timeout=.6) as response:s=json.load(response)
            except Exception:s=None
            with self.lock:
                self.latest=s
                if not self.pending_state:self.pending_state=True;GLib.idle_add(self.deliver_state,priority=GLib.PRIORITY_DEFAULT)
            # Latest-only delivery avoids queued input. Menu rendering has its own
            # 30 ms clock; the host needs fewer state requests inside experiences.
            self.stop.wait(.040 if self.menu_open or getattr(self,'health_active',False) else .100)
    def deliver_state(self):
        with self.lock:s=self.latest;self.pending_state=False
        if self.stop.is_set():return False
        visitor_session=getattr(self,'visitor_source_session',None)
        s,source_key,source_changed=input_context(s,getattr(self,'source_input_key',None),visitor_session)
        self.source_input_key=source_key
        if source_changed:
            self.selector.reset();self.menu_hand=None
            if isinstance(s,dict):self.serial=(s.get('suite') or {}).get('open_serial',self.serial)
        now=time.monotonic();was_ready=self.pipeline.ready
        self.pipeline.update(s,now)
        gestures_blocked=self.suppress_gesture_navigation(now)
        if gestures_blocked and isinstance(s,dict):
            # Consume, but do not execute, circle-menu requests while the pad
            # owns navigation. They must not fire later after the idle grace.
            self.serial=(s.get('suite') or {}).get('open_serial',self.serial)
        if getattr(self,'health_active',False):return self.deliver_health_state(s,now)
        if self.menu_level=='photo':
            # Imported photographs do not depend on camera/pose availability.
            self.sync_gamepad_backend(s,now)
            return False
        self.update_game_source(s,now)
        pad=getattr(self,'gamepad_host',None)
        pad_menu=gestures_blocked and (self.menu_open or getattr(self,'device_dialog',None) or
            (getattr(self,'device_view',None) and self.device_view.get_visible()))
        if self.control_mode=='gamepad' or pad_menu:
            # A controller round is independent of camera/pose availability.
            # Do not let a stale pose response take ownership of its pause clock.
            if self.menu_level=='pose':self.pose_view.update_state(s)
            self.sync_gamepad_backend(s,now)
            if pad and pad.connected and not self.menu_open and not self.switching:
                self.send_child('resume')
            elif self.menu_open or not (pad and pad.connected):self.send_child('pause')
            return False
        if self.menu_level=='pose':self.pose_view.update_state(s)
        if not self.pipeline.ready and self.entry_deadline:
            # A reconnect must not consume the user's advertised entry time.
            self.entry_pending=max(0.,getattr(self.args,'entry_delay',0.));self.entry_deadline=0.
        if not isinstance(s,dict):
            if self.pipeline.ready:
                # A short missed HTTP response is not a lost camera or new
                # backend epoch. Keep the current IPC owner and game clock;
                # consumers receive no invented pose/collision input. Menus
                # discard dwell immediately so missing frames cannot select.
                if self.menu_open:
                    self.selector.reset()
                    message='正在等待新的识别数据…'
                    self.menu.set_availability(False,message)
                    self.actions.set_availability(self.menu_level=='actions' and self.action_page in ('error','uncertain'),message)
                    self.set_status(message)
                return False
            self.backend_ready=False
            self.resync_token+=1;self.resyncing=False
            self.wait_for_input()
            return False
        suite=s.get('suite') or {};serial=suite.get('open_serial');pid=s.get('pid')
        restarted=self.backend_pid is not None and self.backend_pid!=pid
        self.backend_pid=pid
        if restarted:
            self.serial=None;self.resync_token+=1;self.resyncing=False;self.resync_after=0.
        if restarted or not suite.get('active') or (not self.switching and suite.get('mode')!=self.backend_mode(self.mode)):self.backend_ready=False
        if not self.backend_ready and not self.switching:
            self.wait_for_input('正在恢复体感模式…' if self.pipeline.ready else None)
            if not self.resyncing and now>=self.resync_after:
                self.resyncing=True;self.resync_token+=1;token=self.resync_token
                generation=self.generation;navigation=self.navigation_token;wanted_mode=self.mode;wanted_menu=self.menu_open;wanted_preview=self.menu_level=='pose'
                def synced(_,error):
                    if token!=self.resync_token:return False
                    self.resyncing=False
                    if generation!=self.generation or navigation!=self.navigation_token or pid!=self.backend_pid or self.stop.is_set():return False
                    if error:
                        self.resync_after=time.monotonic()+.75
                        self.set_status('正在连接识别服务，将自动重试…');return False
                    self.backend_ready=True
                    return False
                self.command_async(dict(active=True,mode=wanted_mode,menu=wanted_menu,preview=wanted_preview),synced)
            return False
        if serial is not None and serial!=self.serial:
            previous=self.serial;self.serial=serial
            if previous is not None and suite.get('menu') and not self.switching:
                self.open_menu(suite.get('hand'),s.get('points17'),from_backend=True)
        if self.switching:return False
        if not self.pipeline.ready:
            self.wait_for_input();return False
        if self.entry_pending:
            self.entry_deadline=now+self.entry_pending;self.entry_pending=0.
        if self.entry_deadline:
            remaining=self.entry_deadline-now
            if remaining>0:
                self.wait_for_input('入场倒计时 %d 秒 · 结束后移动右手选择体验'%int(remaining+1))
                return False
            self.entry_deadline=0.;self.selector.open(None,now)
        # Device dialogs never permit background dwell to launch a game. The
        # connection center can reuse the right-hand cursor when pose is ready.
        if getattr(self,'device_dialog',None):
            self.send_child('pause')
            if self.selector.opened is None:self.selector.open(None,now,s.get('points17'))
            result=self.selector.update(s,now)
            self.device_dialog_hand(result,(s.get('pid'),s.get('sequence'),s.get('timestamp_seconds')),now)
            if self.selector.done:self.selector.done=False
            return False
        if getattr(self,'device_view',None) and self.device_view.get_visible():
            self.send_child('pause')
            if self.selector.opened is None:self.selector.open(None,now,s.get('points17'))
            result=self.selector.update(s,now)
            self.device_view.update_hand(result,(s.get('pid'),s.get('sequence'),s.get('timestamp_seconds')),now)
            if self.selector.done:self.selector.done=False
            self.set_status('设备与联网 · 体验已暂停');return False
        # Sword freezes candidate gestures from its own higher-rate state feed;
        # a host pause/resume would unnecessarily rearm its recovery countdown.
        # Fruit, sword and taiko gate candidate-gesture input locally. A
        # candidate is not a confirmed menu and must not pause their clocks.
        blocked=self.menu_open
        self.send_child('pause' if blocked else 'resume')
        if self.menu_level=='pose':
            if not was_ready or self.selector.opened is None:self.selector.open(None,now,s.get('points17'))
            result=self.selector.update(s,now);self.pose_view.update_selection(result)
            if result.get('selected')=='pose_back':self.open_menu(main=True)
            self.set_status('摄像头 + 人体骨架 · 游戏与落墨已暂停')
            return False
        if self.menu_open:
            if self.pending_action:
                self.actions.set_availability(False,'正在执行操作，请稍候…')
                return False
            if not was_ready or self.selector.opened is None:self.selector.open(self.menu_hand,now,s.get('points17'))
            if self.menu_level=='ai':self.ai_sync_choices()
            result=self.selector.update(s,now)
            view=self.ai_view if self.menu_level=='ai' else self.visitor_view if self.menu_level=='visitor' else self.actions if self.menu_level in ('actions','control','visitor_confirm') else self.menu
            view.update_selection(dict(result,sample_key=(s.get('pid'),s.get('sequence'),s.get('timestamp_seconds'))))
            message=(('体验已暂停 · ' if self.menu_level=='actions' else '主菜单 · ')+'右手移动，停留 2 秒确认' if result.get('cursor') is not None else '识别已就绪 · 请让肩膀和右手入镜')
            if self.menu_level=='control' and self.action_page=='entry_help':
                message=briefing(self.control_target,self.control_choice)['message']
            if self.menu_level=='actions' and self.action_notice and now<self.notice_until:message=self.action_notice
            view.set_availability(True,message)
            if result.get('cancel') and self.child:self.menu_back()
            elif result.get('selected'):
                if self.menu_level=='ai':self.ai_select(result['selected'])
                elif self.menu_level=='control':self.choose_control(result['selected'])
                elif self.menu_level in ('visitor','visitor_confirm'):self.visitor_choose(result['selected'])
                elif self.menu_level=='actions':self.choose_action(result['selected'])
                elif result['selected'].startswith('visitor_'):self.visitor_choose(result['selected'])
                elif result['selected']=='pose_view':self.open_pose_view()
                else:self.select(result['selected'])
        self.set_status((message if self.menu_open else TITLES[MODES.index(self.mode)])+
            (' · 正在识别画圈' if suite.get('control_active') and not self.menu_open else ''))
        return False
    def sync_gamepad_backend(self,state,now):
        """Restore only suite ownership after service recovery, never pose gates."""
        if not isinstance(state,dict) or self.switching:return
        suite=state.get('suite') or {};pid=state.get('pid')
        mismatch=(pid!=self.backend_pid or not suite.get('active') or
            suite.get('mode')!=self.backend_mode(self.mode) or
            bool(suite.get('menu'))!=self.menu_open or suite.get('input_mode','body')!=self.control_mode)
        self.backend_pid=pid
        if not mismatch or self.resyncing or now<self.resync_after:return
        self.resyncing=True;self.resync_token+=1;token=self.resync_token
        generation=self.generation;navigation=self.navigation_token
        def synced(result,error):
            if token!=self.resync_token:return False
            self.resyncing=False
            if generation!=self.generation or navigation!=self.navigation_token or self.stop.is_set():return False
            self.backend_ready=not bool(error) and not (result or {}).get('recognition_unavailable',False)
            if not self.backend_ready:self.resync_after=time.monotonic()+1.
            return False
        self.command_async(dict(active=True,mode=self.mode,menu=self.menu_open,preview=self.menu_level=='pose'),synced)
    def open_menu(self,hand=None,points=None,from_backend=False,main=False):
        if getattr(self,'menu_level',None)=='ai':self.ai_abandon_context()
        if self.stop.is_set():return
        if self.menu_level=='photo':self.photo_view.leave()
        if getattr(self,'health_active',False):
            self.health_view.action('health_home' if main else 'health_pause');return
        if self.pending_action and self.child and self.child.poll() is None:return
        if self.menu_level=='control' and getattr(self,'control_previous_mode',None) in ('body','gamepad'):
            self.control_mode=self.control_previous_mode
            self.control_previous_mode=None;self.control_target=None;self.control_choice=None
        if getattr(self,'pose_view',None):self.pose_view.stop_preview()
        self.hide_mouse_toolbar()
        self.navigation_token+=1
        # Cancel an in-flight launch; worker completion checks this generation.
        if self.switching:
            self.generation+=1;self.switching=False;self.resyncing=False
            # A spawned child may still be awaiting ready/ownership ACK. Its
            # mode is not self.mode yet, so never expose the old mode's actions
            # against it when the user cancels a launch.
            interrupted=self.child;self.child=None;self.child_paused=None;self.ready_child=None
            if interrupted:threading.Thread(target=self.stop_process,args=(interrupted,),daemon=True).start()
        self.pending_action=None
        self.menu_open=True;self.menu_hand=hand
        self.resync_token+=1;self.resyncing=False
        if not from_backend:self.backend_ready=False
        self.menu_level='actions' if self.child and not main else 'main'
        if self.menu_level=='actions':self.show_action_page('root',points=points)
        else:
            if main and hasattr(self.menu,'set_section'):self.menu.set_section('home')
            self.selector.set_choices(getattr(self.menu,'mode_choices',MODES),getattr(self.menu,'rectangles',None),hit_test=self.menu.hit_index);self.selector.open(hand,time.monotonic(),points)
            self.menu.update_selection({});self.stack.set_visible_child_name('menu')
        if not self.interaction_ready():self.wait_for_input()
        self.home.set_sensitive(True)
        self.send_child('pause')
        self.sync_menu_music()
        if not from_backend:
            self.resyncing=True;token=self.resync_token;navigation=self.navigation_token;pid=self.backend_pid
            def opened(result,error):
                if token!=self.resync_token:return False
                self.resyncing=False
                if navigation!=self.navigation_token or pid!=self.backend_pid or self.stop.is_set():return False
                if error:
                    self.resync_after=time.monotonic()+.75
                    self.set_status('正在恢复菜单控制，请稍候…');return False
                self.backend_ready=not (result or {}).get('recognition_unavailable',False)
                return False
            self.command_async(dict(active=True,mode=self.mode,menu=True),opened)
    def show_action_page(self,name='root',points=None,message=None):
        self.hide_mouse_toolbar()
        self.navigation_token+=1;self.menu_level='actions';self.action_page=name
        self.sync_menu_music()
        self.action_notice=message;self.notice_until=time.monotonic()+8 if message else 0.
        data=action_page(self.mode,name,self.control_mode)
        if 'help' in data:self.actions.set_help(data['title'],data['help'])
        else:self.actions.set_actions(data['title'],data['items'])
        self.selector.set_choices([x['id'] for x in data['items']])
        if points is None:points=(self.latest or {}).get('points17')
        self.selector.open(self.menu_hand,time.monotonic(),points)
        self.actions.update_selection({});self.stack.set_visible_child_name('actions')
        self.actions.set_availability(name in ('error','uncertain') or self.interaction_ready(),message or '体验已暂停 · 右手移动，停留 2 秒确认')
    def menu_back(self):
        if self.pending_action:return
        # Menu/START and gesture-cancel also enter here directly. Never turn a
        # top-level Back into cancel_menu(), which resumes the preserved game.
        if self.menu_level=='main':
            if getattr(self.menu,'section','home')!='home':self.show_section('home')
            return
        if self.menu_level=='control':self.cancel_control_choice();return
        if self.menu_level=='actions' and self.action_page in ('uncertain','error'):
            self.open_menu(main=True);return
        if self.menu_level=='actions' and self.action_page!='root':self.show_action_page('root')
        elif self.menu_level=='actions':self.choose_action('resume')
        else:self.cancel_menu()
    def choose_action(self,action):
        if self.menu_level=='control':
            if not self.menu_open or self.pending_action or self.switching or self.stop.is_set():return
            if action in self.actions.choices:return self.choose_control(action)
            return
        if action.startswith('visitor_'):self.visitor_choose(action);return
        if (not self.menu_open or self.menu_level!='actions' or self.pending_action or
                self.switching or self.stop.is_set()):return
        allowed={x['id'] for x in action_page(self.mode,self.action_page,self.control_mode)['items']}
        if action not in allowed:return
        if action not in ('home','retry','back','connections','ai_assistant') and not self.interaction_ready():return
        if action=='ai_assistant':self.open_project_ai(self.mode);return
        if action=='control_mode':self.open_control_choice(self.mode);return
        if action=='connections':self.open_connections();return
        if action=='resume':action='continue'
        if action=='back':self.show_action_page('root');return
        if action=='home':self.open_menu(main=True);return
        if action=='retry':
            if self.error_mode:self.select(self.error_mode)
            return
        if action in ('help','options','system','themes','audio','audio_options',
                      'taiko_difficulty','taiko_practice','taiko_timing'):self.show_action_page(action);return
        if action=='fullscreen':
            self.toggle_fullscreen();self.show_action_page(self.action_page);return
        if action in CONFIRM:
            self.show_action_page('confirm:'+action);return
        if action.startswith('confirm:'):
            action=action.split(':',1)[1]
            if action=='quit':self.destroy();return
        child=self.child
        if not child or child.poll() is not None:self.open_menu(main=True);return
        self.action_counter+=1
        identifier='%d-%d-%d'%(self.generation,self.navigation_token,self.action_counter)
        self.pending_action=dict(command_id=identifier,action=action,child=child,
                                 generation=self.generation,navigation=self.navigation_token)
        self.selector.reset();self.actions.set_availability(False,'正在执行操作，请稍候…')
        self.send_child('pause')
        try:
            child.stdin.write(json.dumps(dict(op='action',command_id=identifier,action=action))+'\n');child.stdin.flush()
        except (OSError,BrokenPipeError,ValueError):
            self.pending_action=None;self.show_action_page('uncertain');return
        GLib.timeout_add_seconds(22,self.action_timeout,identifier)
    def action_timeout(self,identifier):
        if self.pending_action and self.pending_action['command_id']==identifier:
            if self.pending_action.get('stay_paused'):
                self.pending_action=None;self.device_view.message.set_text('未收到体验声音重连回执，请稍后重试。');return False
            self.pending_action=None;self.show_action_page('uncertain')
        return False
    def action_result(self,child,event):
        pending=self.pending_action
        if (not pending or child is not pending['child'] or event.get('command_id')!=pending['command_id']
                or event.get('action')!=pending['action'] or self.generation!=pending['generation']
                or self.navigation_token!=pending['navigation']):return False
        self.pending_action=None
        if pending.get('stay_paused'):
            self.device_view.message.set_text('体验声音已重新准备，请返回菜单继续。' if event.get('ok') is True
                else '体验声音重连未完成：'+str(event.get('reason') or '请重试')[:100])
            self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
            return False
        if event.get('ok') is True:
            self.set_status(event.get('reason') or '操作完成');self.cancel_menu()
        else:self.show_action_page('root',message='操作未完成：'+str(event.get('reason') or '请重试')[:100])
        return False
    def cancel_menu(self):
        if not self.child or self.pending_action:return
        self.hide_mouse_toolbar()
        child=self.child;generation=self.generation;self.navigation_token+=1;navigation=self.navigation_token
        self.backend_ready=False;self.resyncing=True;self.resync_token+=1;token=self.resync_token
        self.actions.set_availability(False,'正在恢复体验…');self.menu.set_availability(False,'正在恢复体验…')
        def done(result,error):
            if token!=self.resync_token:return False
            self.resyncing=False
            if generation!=self.generation or child is not self.child or navigation!=self.navigation_token or self.stop.is_set():return False
            if error:
                self.show_action_page('root',message='返回体验失败：'+error)
                self.set_status('正在恢复菜单控制，请稍候…');return False
            self.hide_mouse_toolbar()
            self.menu_open=False;self.backend_ready=not (result or {}).get('recognition_unavailable',False);self.selector.reset();self.sync_menu_music();self.stack.set_visible_child_name('experience');self.send_child('resume')
            return False
        self.command_async(dict(active=True,mode=self.mode,menu=False),done)
    def send_child(self,op):
        if op=='resume' and not self.interaction_ready() and not self.gameplay_can_continue():op='pause'
        child=self.child
        if not child or child.poll() is not None:return
        if op=='pause':self.gameplay_source_key=None
        music=getattr(self,'menu_music',None)
        if op=='resume' and music:
            music.set_menu_open(False)
            if not music.status().get('silenced',True):
                request=(child,self.generation,self.navigation_token)
                if getattr(self,'music_resume_pending',None)!=request:
                    self.music_resume_pending=request
                    GLib.timeout_add(20,self.resume_after_music,request,time.monotonic()+2.)
                return
        if op in ('pause','resume'):
            paused=op=='pause'
            if self.child_paused==paused:return
            self.child_paused=paused
        try:
            child.stdin.write(json.dumps({'op':op})+'\n');child.stdin.flush()
            if op=='resume':
                self.gameplay_source_key=(child,self.generation)
                self.gameplay_unready_since=None
        except (OSError,BrokenPipeError,ValueError):pass
    def resume_after_music(self,request,deadline):
        if getattr(self,'music_resume_pending',None)!=request:return False
        child,generation,navigation=request
        if (self.stop.is_set() or child is not self.child or generation!=self.generation or
                navigation!=self.navigation_token or self.menu_open or
                not (self.interaction_ready() or self.gameplay_can_continue())):
            self.music_resume_pending=None;return False
        music=getattr(self,'menu_music',None)
        if music and not music.status().get('silenced',True):
            if time.monotonic()<deadline:return True
            music.close()  # A failed menu output must not delay or overlap a game.
            self.menu_music=None
        self.music_resume_pending=None;self.send_child('resume');return False
    @staticmethod
    def stop_process(child):
        if not child:return
        try:
            if child.poll() is None:
                child.stdin.write('{"op":"shutdown"}\n');child.stdin.flush();child.stdin.close()
                child.wait(timeout=3)
        except (OSError,ValueError,subprocess.TimeoutExpired):
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2)
        finally:
            for stream in (child.stdin,child.stdout):
                try:stream.close()
                except (OSError,ValueError,AttributeError):pass
    def open_control_choice(self,mode):
        if getattr(self,'health_active',False) or self.menu_level=='photo':
            self.set_status('当前页面可直接使用手柄，无需切换游戏控制方式。')
            return
        if mode not in MODES or self.switching or self.pending_action or self.visitor_busy:return
        self.control_return_section=getattr(self.menu,'section',None)
        self.control_return_main=self.menu_level=='main' or not bool(self.child)
        previous=getattr(self,'control_previous_mode',None) or self.control_mode
        self.open_menu(main=not bool(self.child))
        self.control_previous_mode=previous
        self.navigation_token+=1;self.menu_level='control';self.action_page='control_choice';self.control_target=mode;self.control_choice=None
        self.actions.set_actions(TITLES[MODES.index(mode)]+' · 选择控制方式',[
            dict(id='control_body',title='体感游戏',subtitle='使用摄像头，下一页查看动作说明'),
            dict(id='control_gamepad',title='游戏手柄',subtitle='使用手柄，下一页查看按键说明'),
            dict(id='control_ai',title='AI 讲解与建议',subtitle='先了解本项目；断网可看本地说明'),
            dict(id='control_back',title='返回',subtitle='保留当前内容')],
            '右手停留选择，或方向键选择后按 A；右摇杆 / R3 也可点击。')
        self.actions.set_availability(True,self.actions.message)
        self.selector.set_choices(self.actions.choices);self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
        self.stack.set_visible_child_name('actions')
    def cancel_control_choice(self):
        previous=getattr(self,'control_previous_mode',None)
        if previous in ('body','gamepad'):self.control_mode=previous
        self.control_target=None;self.control_choice=None;self.control_previous_mode=None
        if self.child and not getattr(self,'control_return_main',False):self.show_action_page('root')
        else:
            section=getattr(self,'control_return_section',None)
            self.open_menu(main=True)
            if section in ('entertainment','health','creation'):self.show_section(section)
    def choose_control(self,choice):
        if self.menu_level!='control' or not self.control_target:return
        if choice=='control_back':self.cancel_control_choice();return
        if choice=='control_ai' and self.action_page=='control_choice':self.open_project_ai(self.control_target);return
        if self.action_page=='entry_help':
            if choice=='intro_back':
                previous=getattr(self,'control_previous_mode',None)
                if previous in ('body','gamepad'):self.control_mode=previous
                target=self.control_target;return_main=getattr(self,'control_return_main',False);section=getattr(self,'control_return_section',None)
                self.open_control_choice(target);self.control_return_main=return_main;self.control_return_section=section;return
            if choice!='intro_start':return
            wanted=self.control_choice
        else:
            if choice.startswith('intro_'):return
            wanted={'control_body':'body','control_gamepad':'gamepad'}.get(choice)
        if wanted is None:return
        if wanted=='gamepad' and not (self.gamepad_host and self.gamepad_host.connected):
            self.actions.set_availability(True,'手柄未连接 · 请开启手柄，接收器重连后再按 A；也可选择体感游戏。');return
        if wanted=='body' and not (self.pipeline.ready or self.args.offline_preview):
            self.actions.set_availability(True,'体感识别尚未就绪 · 请连接摄像头；也可选择游戏手柄。');return
        if self.action_page!='entry_help':
            self.control_choice=wanted;self.control_mode=wanted
            self.navigation_token+=1;self.action_page='entry_help'
            info=briefing(self.control_target,wanted)
            self.actions.set_briefing(info['title'],info['lines'],info['message'])
            self.actions.set_availability(True,info['message'])
            self.selector.set_choices(self.actions.choices)
            self.selector.open(None,time.monotonic(),(self.latest or {}).get('points17'))
            self.actions.update_selection({});return
        mode=self.control_target
        self.control_mode=wanted
        if self.launch_selected(mode):
            self.control_target=None;self.control_choice=None;self.control_previous_mode=None
    def select(self,mode):
        if mode.startswith('section_'):
            section={'section_home':'home','section_entertainment':'entertainment',
                'section_health':'health','section_creation':'creation'}.get(mode)
            if section is not None:self.show_section(section)
            return
        if mode in ('baduanjin','taichi'):self.enter_health(mode);return
        if mode=='ai_assistant':self.open_ai();return
        if mode=='photo_ink':self.enter_photo();return
        if mode=='pose_view':self.open_pose_view();return
        if mode.startswith('visitor_'):self.visitor_choose(mode);return
        if mode in MODES:self.open_control_choice(mode)
    def launch_selected(self,mode):
        if getattr(self,'health_active',False):return
        if mode=='pose_view':self.open_pose_view();return
        if mode.startswith('visitor_'):self.visitor_choose(mode);return
        if getattr(self,'visitor_busy',False):return
        if getattr(self,'visitor_store',None) and self.visitor_store.current()['status']=='ending':
            self.show_visitor_finish();return
        if self.entry_pending or (self.entry_deadline and time.monotonic()<self.entry_deadline):return
        if not self.interaction_ready():self.wait_for_input();return
        if mode not in MODES or self.switching or self.stop.is_set():return
        self.hide_mouse_toolbar()
        self.navigation_token+=1
        self.pending_action=None;self.ready_child=None;self.deferred_audio_retry=None
        self.generation+=1;generation=self.generation;self.switching=True;self.menu_open=True;self.error_mode=mode;self.resyncing=False
        self.sync_menu_music()
        self.selector.reset();self.send_child('pause');self.stack.set_visible_child_name('loading')
        self.loading_label.set_text('正在打开「'+TITLES[MODES.index(mode)]+'」…');self.retry.hide();self.home.set_sensitive(False)
        old=self.child;self.child=None
        def switch():
            try:
                self.command(dict(active=True,mode=mode,menu=True),generation);self.stop_process(old)
                GLib.idle_add(self.spawn_mode,mode,generation,priority=GLib.PRIORITY_DEFAULT)
            except Exception as exc:
                self.stop_process(old);GLib.idle_add(self.switch_error,mode,generation,str(exc),priority=GLib.PRIORITY_DEFAULT)
        threading.Thread(target=switch,daemon=True).start()
        return True
    def spawn_mode(self,mode,generation):
        if generation!=self.generation or self.stop.is_set():return False
        if self.socket:
            self.stack.remove(self.socket);self.socket.destroy()
        self.socket=Gtk.Socket();self.socket.set_hexpand(True);self.socket.set_vexpand(True)
        self.socket.connect('plug-removed',lambda *_:True)
        self.stack.add_named(self.socket,'experience');self.socket.show();self.socket.realize()
        xid=self.socket.get_id();common=['--embed-socket',str(xid)]
        if mode in ('platformer','runner','pal','kart'):
            if mode=='platformer':
                from thextech.selection import entry
                script=entry(ROOT)
            else:script=ROOT/mode/'main.py'
            argv=[sys.executable,str(script)]+common
            argv+=['--base-url',self.args.base]
        elif mode=='sword':
            argv=[sys.executable,str(ROOT/'sword/main.py')]+common
            argv+=['--base-url',self.args.base]
            if getattr(self.args,'sword_metrics',None):argv+=['--metrics',str(self.args.sword_metrics)]
        elif mode=='taiko':
            argv=[sys.executable,str(ROOT/mode/'main.py')]+common
            argv+=['--pose-url',self.args.base+'/api/state']
            metrics=getattr(self.args,mode+'_metrics',None)
            if metrics:argv+=['--metrics',str(metrics)]
        else:
            sources={'ink':self.args.ink_source,'fight':self.args.fight_source,'fruit':self.args.fruit_source}
            argv=[sys.executable,str(ROOT/'embedded_runner.py'),'--mode',mode,'--source',sources[mode],'--pose-url',self.args.base+'/api/state']+common
        if self.args.offline_preview:
            argv.append('--offline-preview')
            if mode=='taiko':argv+=['--audio-sink','fakesink']
        try:
            logdir=ROOT/'logs';logdir.mkdir(exist_ok=True)
            child_env=dict(os.environ)
            child_env['INK_CONTROL_MODE']=self.control_mode
            if getattr(self,'visitor_store',None) and not self.args.offline_preview:
                child_env['INKSUITE_VISITOR_DATA']=str(self.visitor_store.root)
                child_env['INKSUITE_VISITOR_SESSION']=self.visitor_store.current()['id']
            else:
                child_env.pop('INKSUITE_VISITOR_DATA',None);child_env.pop('INKSUITE_VISITOR_SESSION',None)
            # GTK also paints widget/CSS backgrounds before the game's opaque
            # image. Recording surfaces replay those intermediate clears onto
            # the visible X11 window, causing full-frame black/white flashes.
            # Keep a real backing surface for every embedded experience. Do
            # not let an inherited diagnostic "recording" override reintroduce
            # flicker; image remains an explicitly supported buffered fallback.
            child_env['GDK_RENDERING']='image' if child_env.get('GDK_RENDERING')=='image' else 'similar'
            with (logdir/(mode+'.log')).open('a',encoding='utf-8') as log:
                if mode=='fruit':
                    try:
                        from fruit_pixman_runtime import build_child_environment
                        child_env,pixman_runtime=build_child_environment(ROOT,mode,child_env)
                    except Exception as exc:
                        pixman_runtime=dict(enabled=False,reason='runtime-helper-error',detail=type(exc).__name__)
                    log.write('[fruit-pixman] '+json.dumps(pixman_runtime,ensure_ascii=False)+'\n');log.flush()
                child=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1,env=child_env)
            self.child=child;self.child_paused=None
            self.send_child('pause')
            threading.Thread(target=self.child_events,args=(child,mode,generation),daemon=True).start()
            GLib.timeout_add_seconds(45 if mode=='platformer' and script.parent.name=='thextech' else 20,
                                     self.launch_timeout,child,mode,generation)
        except Exception as exc:self.switch_error(mode,generation,str(exc))
        return False
    def child_events(self,child,mode,generation):
        try:
            for line in child.stdout:
                try:event=json.loads(line)
                except ValueError:continue
                GLib.idle_add(self.child_event,child,mode,generation,event,priority=GLib.PRIORITY_DEFAULT)
        except (OSError,ValueError):pass
        result=child.wait();GLib.idle_add(self.child_ended,child,mode,generation,result,priority=GLib.PRIORITY_DEFAULT)
    def child_event(self,child,mode,generation,event):
        if generation!=self.generation or self.child is not child or self.stop.is_set():return False
        kind=event.get('event')
        if kind=='ready':
            if self.ready_child is child or not self.switching:return False
            self.ready_child=child;navigation=self.navigation_token
            def done(result,error):
                if generation!=self.generation or self.child is not child or navigation!=self.navigation_token or self.stop.is_set():return False
                if error:return self.switch_error(mode,generation,error)
                self.mode=mode;self.menu_open=False;self.switching=False;self.error_mode=None;self.backend_ready=not (result or {}).get('recognition_unavailable',False);self.resyncing=False
                self.hide_mouse_toolbar()
                self.sync_menu_music()
                self.stack.set_visible_child_name('experience');self.home.set_sensitive(True);self.send_child('resume')
                return False
            self.command_async(dict(active=True,mode=mode,menu=False),done)
        elif kind=='menu_requested':
            if not self.menu_open:self.open_menu()
        elif kind=='action_result':self.action_result(child,event)
        elif kind in ('paused','resumed'):
            # Keep acknowledgements observable separately from the last
            # command sent. A restart may prepare a countdown while still
            # owned by this paused host; only the following resumed receipt
            # proves that the new native round was released.
            self.child_receipt=dict(child=child,generation=generation,
                paused=kind=='paused',state=event.get('game_state'),clock=event.get('game_clock'))
            print(json.dumps(dict(event='child_state',mode=mode,generation=generation,
                paused=kind=='paused',game_state=event.get('game_state'),game_clock=event.get('game_clock'))),flush=True)
        elif kind=='audio_diagnostic' and mode in ('fruit','fight'):
            # Cached worker state only: retain route/fault evidence without
            # starting output, touching focus or changing pause ownership.
            print(json.dumps(dict(event,mode=mode,generation=generation,child_pid=child.pid)),flush=True)
        elif kind=='fruit_render_diagnostic' and mode=='fruit':
            # Optional child-side sampling uses a real clock. Forward metadata
            # only, without changing menu state, focus, timing or ownership.
            print(json.dumps(dict(event,mode=mode,generation=generation,child_pid=child.pid)),flush=True)
        elif kind=='fullscreen_requested':self.toggle_fullscreen()
        return False
    def launch_timeout(self,child,mode,generation):
        if generation==self.generation and self.switching and self.child is child:
            self.switch_error(mode,generation,'体验窗口未完成加载，请重试。')
        return False
    def switch_error(self,mode,generation,error):
        if generation!=self.generation or self.stop.is_set():return False
        self.hide_mouse_toolbar()
        self.switching=False;self.error_mode=mode;self.menu_open=True;self.home.set_sensitive(True)
        self.loading_label.set_text('「'+TITLES[MODES.index(mode)]+'」暂未打开\n'+str(error)[:180]);self.retry.show();self.stack.set_visible_child_name('loading')
        child=self.child;self.child=None
        self.pending_action=None;self.show_action_page('error',message=str(error)[:110])
        threading.Thread(target=self.stop_process,args=(child,),daemon=True).start()
        self.command_async(dict(active=True,mode=self.mode,menu=True))
        return False
    def child_ended(self,child,mode,generation,result):
        if generation!=self.generation or self.child is not child or self.stop.is_set():return False
        self.child=None;self.child_paused=None;self.deferred_audio_retry=None
        if self.switching:return self.switch_error(mode,generation,'体验进程退出（'+str(result)+'），请重试。')
        self.open_menu(main=True);return False
    def close(self,*_):
        if self.stop.is_set():return
        if getattr(self,'health_voice',None):self.health_voice.close()
        if getattr(self,'health_view',None):self.health_view.close()
        recorder=getattr(self,'health_record_owner',None)
        if recorder and not recorder.flush(timeout=1.5):
            print('health_record_pending: 已保留原用户归属，下次启动继续关联。',file=sys.stderr,flush=True)
        if getattr(self,'photo_view',None):self.photo_view.close()
        if getattr(self,'gamepad_host',None):self.gamepad_host.close();self.gamepad_host=None
        cursor=getattr(self,'mouse_cursor',None)
        if cursor:cursor.close()
        toolbar=getattr(self,'mouse_toolbar',None)
        if toolbar:toolbar.close()
        if getattr(self,'display_dialog',None):self.display_dialog.close();self.display_dialog=None
        self.stop.set();self.generation+=1;child=self.child;self.child=None;self.deferred_audio_retry=None
        if getattr(self,'menu_music',None):self.menu_music.close()
        if getattr(self,'pose_view',None):self.pose_view.stop_preview()
        if getattr(self,'device_timer',None):GLib.source_remove(self.device_timer);self.device_timer=None
        if getattr(self,'devices',None):self.devices.close()
        if getattr(self,'device_policy',None):self.device_policy.close()
        if getattr(self,'device_dialog',None):self.device_dialog.destroy();self.device_dialog=None
        if getattr(self,'device_view',None):self.device_view.destroy()
        self.stop_process(child)
        if getattr(self,'ai_timer',None):GLib.source_remove(self.ai_timer);self.ai_timer=None
        if getattr(self,'ai_service',None):self.ai_service.close();self.ai_service=None
        if getattr(self,'visitor_timer',None):GLib.source_remove(self.visitor_timer);self.visitor_timer=None
        if getattr(self,'visitor_portal',None):self.visitor_portal.close();self.visitor_portal=None
        # launcher also restores this after abnormal host termination.
        try:self.command(dict(active=False,mode='ink',menu=False))
        except Exception:pass
        Gtk.main_quit()


def arguments():
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8088');p.add_argument('--fullscreen',action='store_true');p.add_argument('--offline-preview',action='store_true')
    p.add_argument('--ink-source',default='/home/ubuntu/bodyink-live/native_display.py')
    p.add_argument('--fight-source',default='/home/ubuntu/ink-fight/native/main_v2.py')
    p.add_argument('--fruit-source',default='/home/ubuntu/fruit-slice/native/main.py')
    p.add_argument('--entry-delay',type=float,default=0.,help='Seconds before accepting the first menu selection')
    p.add_argument('--preserve-display',action='store_true',help='Keep the selected monitor mode during this launch')
    p.add_argument('--sword-metrics',help='Optional native sword rendering diagnostics file')
    p.add_argument('--taiko-metrics',help='Optional native rhythm rendering diagnostics file')
    p.add_argument('--visitor-data',type=Path,help='Persistent visitor artwork/results directory')
    p.add_argument('--visitor-port',type=int,default=8091,help='Phone download portal on the same Wi-Fi')
    return p.parse_args()

if __name__=='__main__':Suite(arguments());Gtk.main()
