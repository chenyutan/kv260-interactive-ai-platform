"""Adapt validated native windows into one XEmbed slot without changing games.

One child owns one mode. JSON on stdin: pause, resume, shutdown and whitelisted
action commands. EOF terminates the child. Action acknowledgements carry the
same command_id; only the host may resume after changing a paused experience.
"""
import argparse, importlib.util, json, math, os, signal, sys, threading, time
import urllib.request
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from action_dispatch import ActionDispatcher
from audio.integration import ExperienceAudio
from audio.game_output import CubeGameAudioDirector
from toolbar_autohide import hide_embedded_toolbar
from gamepad.adapters import create_gamepad
from ink_feedback import ink_message,brush_name,creation_header,hide_embedded_ink_controls
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib

NATIVE_WINDOW_SOURCES={'fight':Path('/home/ubuntu/ink-fight/native/main_v2.py'),
                       'fruit':Path('/home/ubuntu/fruit-slice/native/main.py')}


def load_window_module(path,mode):
    path=Path(path).resolve();sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('suite_active_mode',path)
    module=importlib.util.module_from_spec(spec)
    override=Path(__file__).resolve().parent/'native_windows'/(mode+'.py')
    if path==NATIVE_WINDOW_SOURCES.get(mode) and override.is_file():
        if not path.is_file():raise RuntimeError('原体验源文件不存在：'+str(path))
        if mode=='fight':
            scene_override=override.with_name('fight_scene.py')
            scene_source=path.with_name('scene.py')
            if scene_override.is_file():
                if not scene_source.is_file():raise RuntimeError('格斗原场景源文件不存在：'+str(scene_source))
                scene_spec=importlib.util.spec_from_file_location('scene',scene_source)
                scene_module=importlib.util.module_from_spec(scene_spec)
                exec(compile(scene_override.read_bytes(),str(scene_override),'exec'),scene_module.__dict__)
                sys.modules['scene']=scene_module
        # Only the packaged code changes. The original module.__file__ retains
        # dependency, asset and round directories, and original sources remain
        # untouched. Explicit custom sources never use a production override.
        exec(compile(override.read_bytes(),str(override),'exec'),module.__dict__)
    else:spec.loader.exec_module(module)
    return module


def emit(event, **fields):
    try:print(json.dumps(dict(event=event,**fields)),flush=True)
    except (BrokenPipeError,OSError):pass


class FruitRenderDiagnostics:
    """Opt-in draw-callback accounting; never measures physical scanout.

    Samples come from completed native full_draw_times, not paint fragments or
    the rolling deque's old tail. GTK owns this collector, so it needs no locks.
    """
    def __init__(self,app,now=None,cpu=None):
        self.app=app
        self.since=time.monotonic() if now is None else now
        self.cpu=time.process_time() if cpu is None else cpu
        self.frames=app.frames
        self.native_calls=getattr(app.blitter,'native_calls',0)
        self.draws=deque(maxlen=600);self.ticks=deque(maxlen=600)
        self.dropped_draws=0;self.dropped_ticks=0
    def record_draw(self,seconds,damage=None,at=None):
        if not math.isfinite(seconds) or seconds<0:return
        if len(self.draws)==self.draws.maxlen:self.dropped_draws+=1
        self.draws.append((time.monotonic() if at is None else at,seconds,damage))
    def record_tick(self,core_seconds,sound_seconds,at=None):
        if not all(math.isfinite(v) and v>=0 for v in (core_seconds,sound_seconds)):return
        if len(self.ticks)==self.ticks.maxlen:self.dropped_ticks+=1
        self.ticks.append((time.monotonic() if at is None else at,core_seconds,sound_seconds))
    @staticmethod
    def percentiles_ms(values):
        values=sorted(values)
        if not values:return None,None
        n=len(values);mid=n//2
        median=values[mid] if n%2 else (values[mid-1]+values[mid])/2
        # Nearest-rank P95, defined even for a single completed draw.
        return median*1000,values[max(0,math.ceil(n*.95)-1)]*1000
    def sample(self,now=None,cpu=None,paused=False):
        now=time.monotonic() if now is None else now
        cpu=time.process_time() if cpu is None else cpu
        elapsed=now-self.since
        if not math.isfinite(elapsed) or elapsed<=0:return None
        draws=[row for row in self.draws if self.since<row[0]<=now]
        ticks=[row for row in self.ticks if self.since<row[0]<=now]
        # A future test-clock sample stays pending; expired records never leak
        # into the next interval. Do not reset game or cumulative draw counters.
        self.draws=deque((r for r in self.draws if r[0]>now),maxlen=600)
        self.ticks=deque((r for r in self.ticks if r[0]>now),maxlen=600)
        frames=self.app.frames;frame_delta=frames-self.frames
        cpu_delta=cpu-self.cpu if self.cpu is not None else None
        cpu_valid=cpu_delta is not None and math.isfinite(cpu_delta) and cpu_delta>=0
        draw_p50,draw_p95=self.percentiles_ms([r[1] for r in draws])
        core_p50,core_p95=self.percentiles_ms([r[1] for r in ticks])
        sound_p50,sound_p95=self.percentiles_ms([r[2] for r in ticks])
        damage=[r[2] for r in draws if r[2] is not None and math.isfinite(r[2])]
        app=self.app;cut=getattr(app,'cut_fx',None);item=getattr(app,'item_fx',None)
        blitter=app.blitter;native=getattr(blitter,'native_calls',0)
        output=dict(monotonic_seconds=now,interval_seconds=elapsed,
            frames_drawn=frames,frames_delta=frame_delta if frame_delta>=0 else None,
            draw_fps=frame_delta/elapsed if frame_delta>=0 else None,
            cpu_single_core_percent=cpu_delta/elapsed*100 if cpu_valid else None,
            counter_reset=frame_delta<0 or not cpu_valid,
            full_draw_samples=len(draws),full_draw_ms_p50=draw_p50,full_draw_ms_p95=draw_p95,
            core_tick_samples=len(ticks),core_tick_ms_p50=core_p50,core_tick_ms_p95=core_p95,
            sound_tick_ms_p50=sound_p50,sound_tick_ms_p95=sound_p95,
            dropped_draw_samples=self.dropped_draws,dropped_tick_samples=self.dropped_ticks,
            game_state=app.game.state,controller_paused=bool(paused),
            objects=len(app.game.objects),halves=len(app.halves),splashes=len(app.splashes),
            juice_groups=len(getattr(cut,'items',())),item_effect_groups=len(getattr(item,'items',())),
            trail_points={hand:len(path) for hand,path in app.trails.items()},
            damage_ratio_mean=sum(damage)/len(damage) if damage else None,
            rotation_cache_bytes=app.rotation_bytes,
            effect_cache_bytes=sum(getattr(getattr(v,'cache',None),'bytes',0) for v in (cut,item)),
            special_background_cache_bytes=getattr(item,'background_bytes',0),
            presentation_backend=(getattr(app.presenter,'backend','unknown')
                if getattr(app,'presentation_is_rgb565',False) else 'rgb24-direct'),
            native_sprite_calls=native,
            native_sprite_calls_delta=native-self.native_calls if native>=self.native_calls else None,
            native_batch_calls=getattr(blitter,'batch_native_calls',None),
            cairo_alpha_fallback_calls=getattr(blitter,'fallback_calls',None),
            render_size=list(app.cache_key) if app.cache_key else None,
            physical_scanout_measured=False)
        self.since=now;self.cpu=cpu if math.isfinite(cpu) else None
        self.frames=frames;self.native_calls=native
        self.dropped_draws=0;self.dropped_ticks=0
        return output


class Controller:
    def __init__(self,args):
        self.args=args;self.app=None;self.closed=False;self.paused=False;self.before=None
        self.gamepad=create_gamepad(args.mode,disabled=args.offline_preview)
        self.gamepad_hold=None;self.gamepad_notice='';self._gamepad_ink_busy=False
        audio_enabled=not args.offline_preview
        director=CubeGameAudioDirector(args.mode,enabled=audio_enabled) if args.mode in ('fruit','fight') else None
        self.sound=ExperienceAudio(args.mode,enabled=audio_enabled,director=director)
        self.next_audio_diagnostic=0.;self.fruit_metrics=None
        self.plug=Gtk.Plug.new(args.embed_socket);self.plug.connect('destroy',self.close)
        self.plug.connect('key-press-event',self.key)
        self.load();self.sound.bind(self.app);self.pause()
        if args.mode=='fruit' and os.environ.get('INK_FRUIT_METRICS')=='1':
            self.fruit_metrics=FruitRenderDiagnostics(self.app)
            GLib.timeout_add(2000,self.fruit_render_diagnostic)
        self.visitor_ink=None
        if args.mode=='ink' and not args.offline_preview and os.environ.get('INKSUITE_VISITOR_DATA'):
            from visitor.store import VisitorStore
            from visitor.coordinator import FinishCoordinator
            self.visitor_ink=FinishCoordinator(VisitorStore(os.environ['INKSUITE_VISITOR_DATA']),args.pose_url.removesuffix('/api/state'))
        self.visitor_reporter=None
        if args.mode in ('fight','fruit'):
            from visitor.reporter import GameReporter
            self.visitor_reporter=GameReporter.from_environment(args.mode,disabled=args.offline_preview)
            if self.visitor_reporter:GLib.timeout_add(200,self.visitor_tick)
        self.plug.show_all()
        threading.Thread(target=self.read_control,daemon=True).start()
        signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(self.close,priority=GLib.PRIORITY_DEFAULT))
        GLib.idle_add(lambda:(emit('ready',mode=args.mode),False)[1])
    def load(self):
        module=load_window_module(self.args.source,self.args.mode)
        self.game_world=(getattr(module,'W',1280),getattr(module,'H',720))
        if self.args.mode=='ink':
            self._ink_module=module
            module.BASE=self.args.pose_url.removesuffix('/api/state')
            if self.args.offline_preview:
                module.Display.fetch_preview=lambda self:None
                module.Display.fetch_state=lambda self:None
                module.Display.fetch_animation=lambda self:None
            # Plug supports Window methods, but never requests a separate WM frame.
            self.app=module.Display(window_factory=lambda:self.plug)
            self._ink_embedded_controls=hide_embedded_ink_controls(self.app)
            if hasattr(self.app,'art'):
                from native_render import bind_artwork_policy
                bind_artwork_policy(self.app.art)
            self._share_epoch=0;self._pending_share=None;self._deferred_share=None
            if hasattr(module,'fetch_share') and hasattr(self.app,'show_share'):
                # Keep the existing non-modal QR presentation, but bind every
                # request to this menu epoch. A late response cannot cover a
                # newer gesture menu or a different experience.
                self.app.share_work=self.request_share
            # The entry confirmation owns operation tutorials. Preserve only
            # short state/error text under the creative canvas. No backend
            # packets, animation data or real inference rates are modified.
            for name in ('step','metrics'):
                label=getattr(self.app,name,None)
                if label is not None:label.set_no_show_all(True);label.hide()
            self._ink_original_publish=self.app.publish
            self.app.publish=self.ink_publish
            if self.args.offline_preview:self.app.status.set_text('离线布局验证 · 未访问摄像头')
            original_update=self.app.update
            def creation_update():
                result=original_update()
                self.ink_creation_header()
                if self.gamepad:self.ink_gamepad_labels()
                return result
            self.app.update=creation_update
            self.ink_creation_header()
            GLib.timeout_add(200,self.ink_creation_header)
            if self.gamepad:
                GLib.timeout_add(32,self.ink_gamepad_tick)
            return
        if self.args.mode=='fight':
            # Production Reader hardcodes the endpoint; override only transport.
            target=self.args.pose_url
            def run(reader):
                import urllib.request
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));last=None
                while not reader.closed:
                    try:
                        with opener.open(target,timeout=.5) as response:s=json.load(response)
                        key=(s.get('pid'),s.get('sequence'),s.get('timestamp_seconds'))
                        if key!=last:last=key;reader.value=s;reader.received=time.monotonic()
                    except Exception:reader.value=None
                    time.sleep(.03)
            module.Reader.run=run
        owner=self
        class EmbeddedWindow(module.Window):
            def focus_out(self,*args):
                # The native Window constructor connects self.focus_out as
                # well as the Plug handler below. Override both surfaces:
                # XEmbed can deliver a delayed loss of focus after a restart
                # has been acknowledged and resumed. Only host IPC owns
                # pause; a focus event may discard input, never stop a round.
                return owner.focus_out(*args)
            def tick(self):
                if owner.gamepad:
                    result=owner.gamepad_tick(self,super().tick)
                    owner.sound.tick()
                    return result
                metrics=getattr(owner,'fruit_metrics',None)
                if metrics is None:
                    result=super().tick()
                    owner.sound.tick()
                else:
                    begin=time.monotonic();result=super().tick();core_end=time.monotonic()
                    owner.sound.tick();end=time.monotonic()
                    metrics.record_tick(core_end-begin,end-core_end,at=end)
                if owner.args.mode in ('fruit','fight') and time.monotonic()>=owner.next_audio_diagnostic:
                    owner.next_audio_diagnostic=time.monotonic()+3.
                    emit('audio_diagnostic',mode=owner.args.mode,game_state=self.game.state,
                         audio=owner.sound.director.status())
                return result
            def draw(self,*args):
                metrics=getattr(owner,'fruit_metrics',None)
                if metrics is None:
                    result=super().draw(*args)
                    owner.gamepad_overlay(self,args)
                    return result
                before=self.frames;begin=time.monotonic()
                result=super().draw(*args)
                if self.frames>before:
                    full=getattr(self,'full_draw_times',None)
                    damage=getattr(self,'damage_pixels',None)
                    metrics.record_draw(full[-1] if full else time.monotonic()-begin,
                        damage[-1] if damage else None)
                owner.gamepad_overlay(self,args)
                return result
            def start(self,*args):
                reporter=getattr(owner,'visitor_reporter',None)
                if reporter:reporter.observe(self.game);reporter.reset()
                result=super().start(*args)
                if owner.app is not None:owner.sound.restart()
                return result
            def show_all(self):pass
            def present(self):pass
            def fullscreen(self):emit('fullscreen_requested')
            def unfullscreen(self):emit('fullscreen_requested')
            def get_window(self):return owner.plug.get_window()
        args=SimpleNamespace(pose=not self.args.offline_preview and self.gamepad is None,pose_url=self.args.pose_url,
            benchmark=0,live_test=False,record_session=None,report='unused-suite-report.json')
        self.app=EmbeddedWindow(args)
        toolbar=getattr(self.app,'toolbar',None)
        if toolbar is not None:hide_embedded_toolbar(toolbar, True)
        if self.args.offline_preview:
            # Exercise real animation using the existing synthetic benchmark
            # branches, while excluding benchmark rounds and reports from data.
            self.app.args.benchmark=True
            if hasattr(self.app,'round_export'):self.app.round_export.enabled=False
        content=self.app.get_child();self.app.remove(content);self.plug.add(content)
        if self.args.mode=='fight':self.plug.connect('key-release-event',self.app.key_up)
        self.plug.connect('focus-out-event',self.focus_out)
    def fruit_render_diagnostic(self):
        if self.closed:return False
        if self.fruit_metrics is not None:
            fields=self.fruit_metrics.sample(paused=self.paused)
            if fields is not None:emit('fruit_render_diagnostic',mode='fruit',**fields)
        return True
    def focus_out(self,*_):
        # XEmbed emits focus-out on page unmapping. It can arrive after the host
        # has already resumed; never turn that delayed event into a new menu or
        # change simulation state. Parent pause/resume IPC owns that transition.
        if self.app and not self.closed:
            if self.args.mode=='fight':
                self.app.cancel_input();self.app.gesture.reset();self.app.manual_guard=False;self.app.keys.clear()
                self.reset_body_control()
            elif self.args.mode=='fruit':self.app.pose.reset();self.app.trails.clear();self.app.game.paths.clear();self.app.mouse=False
        return False
    def reset_body_control(self):
        # Both old and new fighting releases can be embedded. Never retain a
        # lateral neutral pose or a pending knee-rise across menu ownership.
        control=getattr(self.app,'body_control',None)
        if control is not None:control.reset()
    def diagnostic(self):
        if self.args.mode in ('fruit','fight'):
            game=self.app.game
            return dict(game_state=game.state,game_clock=getattr(game,'time',getattr(game,'elapsed',0.)))
        return {}
    def key(self,_,event):
        if event.keyval in (Gdk.KEY_Escape,Gdk.KEY_Menu,Gdk.KEY_F1):
            self.pause();emit('menu_requested');return True
        if self.app and self.args.mode in ('fruit','fight'):
            return self.app.key(self.plug,event)
        return False
    def pause(self):
        if self.closed or self.paused:return False
        self.paused=True
        if getattr(self,'gamepad',None):self.gamepad.reset()
        if getattr(self,'sound',None):self.sound.pause()
        if self.args.mode=='ink':self.clear_share()
        if self.args.mode in ('fruit','fight'):
            a=self.app;self.before=a.game.state
            if getattr(self,'gamepad_hold',None) is not None:
                self.before=self.gamepad_hold;self.gamepad_hold=None
            if self.args.mode=='fruit':
                a.game.pause();a.pose.reset();a.trails.clear();a.game.paths.clear();a.mouse=False
            else:
                if a.game.state in ('playing','countdown','finishing'):a.game.state='paused'
                a.cancel_input();a.gesture.reset();a.manual_guard=False;a.keys.clear();a.tracking_pause=False
                self.reset_body_control()
            # Stop the actual tick while the menu is open: fight otherwise consumes
            # fresh poses even while paused and builds input histories.
            if not hasattr(a,'_suite_original_tick'):
                a._suite_original_tick=a.tick
            a.last=time.monotonic()
        emit('paused',mode=self.args.mode,**self.diagnostic())
        return False
    def resume(self):
        if self.closed or not self.paused:return False
        self.paused=False
        if getattr(self,'gamepad',None):self.gamepad.reset();self.gamepad_hold=None
        if self.args.mode in ('fruit','fight'):
            a=self.app
            if self.before in ('playing','countdown','finishing'):
                a.game.state=self.before
            elif self.before=='menu':
                a.start()
                if self.args.offline_preview:a.game.countdown=0
            a.last=time.monotonic()
            if self.args.mode=='fruit':a.pose.reset();a.trails.clear();a.game.paths.clear()
            else:
                a.gesture.reset();a.cancel_input();a.lost_since=None;a.good_since=None
                self.reset_body_control()
            if a.reader:a.reader.value=None;a.reader.received=0
        elif self.args.mode=='ink':
            pending=getattr(self,'_pending_share',None);self._pending_share=None
            deferred=getattr(self,'_deferred_share',None);self._deferred_share=None
            if pending:self.request_share(pending[0])
            elif deferred:self.deliver_share(*deferred)
        if getattr(self,'sound',None):self.sound.resume()
        emit('resumed',mode=self.args.mode,**self.diagnostic())
        return False
    def clear_share(self):
        self._share_epoch=getattr(self,'_share_epoch',0)+1
        self._pending_share=None;self._deferred_share=None
        dialog=getattr(self.app,'share_dialog',None)
        if dialog:dialog.destroy();self.app.share_dialog=None
        if hasattr(self.app,'share_queued'):self.app.share_queued=None
    def request_share(self,name=None):
        if self.closed:return False
        if self.paused:self._pending_share=(name,);return False
        epoch=getattr(self,'_share_epoch',0)
        def fetch():
            try:
                share=self._ink_module.fetch_share(name)
                GLib.idle_add(self.deliver_share,epoch,share)
            except Exception as error:
                if not self.closed and epoch==getattr(self,'_share_epoch',0):
                    self.app.publish('notice','作品已保存；扫码暂不可用：'+str(error))
        threading.Thread(target=fetch,daemon=True).start()
        return False
    def deliver_share(self,epoch,share):
        if self.closed or epoch!=getattr(self,'_share_epoch',0):return False
        if self.paused:self._deferred_share=(epoch,share);return False
        self.app.show_share(share)
        return False
    def handle_action(self,request):
        if not hasattr(self,'action_dispatcher'):
            self.action_dispatcher=ActionDispatcher(self.args.mode,
                lambda:self.paused and not self.closed,self.execute_action,emit)
        return self.action_dispatcher.dispatch(request)
    def hold_game(self):
        a=self.app;self.before=a.game.state
        if self.args.mode=='fruit':a.game.pause()
        elif a.game.state in ('playing','countdown','finishing'):a.game.state='paused'
        a.last=time.monotonic()
        a.area.queue_draw()
    def execute_action(self,action,done):
        if action.startswith('audio_'):
            self.sound.handle(action);done(True,'声音设置已更新',audio=self.sound.director.status());return
        if self.args.mode=='ink':
            if action in ('pause','continue'):done(True);return
            self.ink_action(action,done);return
        a=self.app
        if action in ('start','restart'):
            self.gamepad_hold=None
            a.start();self.hold_game()
        elif action=='continue':
            if self.args.mode=='fight' and a.help_open:
                a.help();self.hold_game()
            if self.before=='paused':
                self.before=(getattr(a.game,'previous','playing') if self.args.mode=='fruit'
                             else getattr(a,'before_pause','playing'))
            if self.before=='menu':a.start();self.hold_game()
        elif action=='pause':pass
        elif action in ('rules_arcade','rules_classic'):
            rule='arcade' if action=='rules_arcade' else 'classic'
            a.game.mode=rule;a.mode_choice.set_active_id(rule)
            a.start();self.hold_game()
        elif action in ('mirror_on','mirror_off'):
            a.mirror(SimpleNamespace(get_active=lambda:action=='mirror_on'))
        else:done(False,'unsupported_action');return
        # A success receipt describes the freshly prepared native state,
        # not just whether start() returned without raising. The host remains
        # the paused owner until it releases the backend and sends resume.
        if action in ('start','restart','rules_arcade','rules_classic') and self.before not in ('countdown','playing'):
            done(False,'restart_did_not_prepare_round',game_state=self.before);return
        done(True,game_state=self.before)
    def ink_publish(self,key,value):
        if key=='state':
            self.sound.ink_state(value)
            self._ink_event_count=value.get('event_count')
            self._ink_state_message=ink_message(value)
            value=dict(value,message=self._ink_state_message or '体感控制')
        return self._ink_original_publish(key,value)

    def ink_creation_header(self):
        if self.closed:return False
        label=getattr(self.app,'title',None)
        if label is not None:
            text=creation_header(getattr(self.app,'current_theme','mixed'),
                getattr(self,'_ink_event_count',None),getattr(self.app,'animation',None))
            if label.get_text()!=text:label.set_text(text)
        return True

    def ink_action(self,action,done):
        commands={'save':('save',{}),'new_scroll':('clear',{}),'undo':('undo',{}),
            'theme_mixed':('theme',{'theme':'mixed'}),'theme_landscape':('theme',{'theme':'landscape'}),
            'theme_bamboo':('theme',{'theme':'bamboo'}),'theme_plum':('theme',{'theme':'plum'})}
        command,extra=commands[action]
        if self.args.offline_preview:done(False,'offline_preview_no_artwork_changes');return
        if command in ('clear','undo') and hasattr(self.app,'animation'):self.app.animation.cancel()
        epoch=getattr(self,'_share_epoch',0)
        def deliver(result,error=None):
            if error:done(False,'artwork_request_failed: '+str(error));return False
            accepted=result.get('accepted') is True
            reason=result.get('reason') or result.get('message') or ('操作完成' if accepted else '操作未完成')
            if accepted and command=='save':reason='作品已保存到板卡'
            if not self.closed:
                if command in ('clear','undo') and hasattr(self.app,'animation'):self.app.animation.cancel()
                if accepted and command=='save' and epoch==getattr(self,'_share_epoch',0):
                    name=os.path.basename(str(result.get('path','')))
                    if name and hasattr(self,'_ink_module') and hasattr(self._ink_module,'fetch_share'):
                        self._pending_share=(name,)
                        reason='作品已保存，正在准备下载码'
                self.app.publish('notice',reason)
                if getattr(self,'sound',None):self.sound.command(command,accepted,'ink-'+str(epoch)+'-'+str(time.monotonic_ns()))
            done(accepted,reason,path=result.get('path','') if command=='save' else '')
            return False
        def send():
            try:
                if command=='clear' and self.visitor_ink:self.visitor_ink.snapshot()
                url=self.args.pose_url.removesuffix('/api/state')+'/api/command'
                request=urllib.request.Request(url,data=json.dumps(dict(command=command,**extra)).encode(),
                    headers={'Content-Type':'application/json'})
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request,timeout=15) as response:
                    raw=response.read(262145)
                    if len(raw)>262144:raise ValueError('Oversized artwork response')
                    result=json.loads(raw)
                if not isinstance(result,dict):raise ValueError('Invalid artwork response')
                if command=='save' and result.get('accepted') is True and self.visitor_ink:self.visitor_ink.snapshot()
                GLib.idle_add(deliver,result)
            except Exception as error:GLib.idle_add(deliver,None,str(error))
        threading.Thread(target=send,daemon=True).start()
    def visitor_tick(self):
        if self.closed:return False
        try:self.visitor_reporter.observe(self.app.game)
        except Exception as exc:print('visitor observation failed: '+str(exc),file=sys.stderr)
        return True
    def close(self,*_):
        if self.closed:return False
        if getattr(self,'visitor_reporter',None):self.visitor_reporter.close(self.app.game)
        self.closed=True
        if getattr(self,'gamepad',None):self.gamepad.close()
        if getattr(self,'sound',None):self.sound.close()
        if self.app:
            if self.args.mode=='ink':self.clear_share();self.app.close()
            else:self.app.destroy()
        Gtk.main_quit();return False

    def gamepad_tick(self,a,tick):
        """Direct real game controls; no pose synthesis or preview scoring."""
        now=time.monotonic()
        control=getattr(self.gamepad,self.args.mode)(now)
        self.gamepad_notice='' if control['valid'] else control['reason']
        if not control['valid']:
            if self.gamepad_hold is None and a.game.state in ('playing','countdown','finishing'):
                self.gamepad_hold=a.game.state
                if self.args.mode=='fruit':a.game.pause()
                else:a.game.state='paused';a.cancel_input()
                self.sound.pause()
            if self.args.mode=='fruit':a.game.paths.pop('gamepad',None);a.trails.clear()
            else:a.keys.clear();a.manual_guard=False;a.manual_jump=False
            a.last=now;a.area.queue_draw()
            return True
        if self.gamepad_hold is not None:
            a.game.state=self.gamepad_hold;self.gamepad_hold=None;a.last=now
            self.sound.resume()
        if control.get('confirm') and a.game.state in ('menu','over'):a.start()
        if self.args.mode=='fruit':
            old_pointer=getattr(a,'gamepad_pointer',None)
            a.gamepad_pointer=(control['x'],control['y'],control['slicing'])
            width,height=a.area.get_allocated_width(),a.area.get_allocated_height()
            ww,hh=self.game_world;scale=min(width/ww,height/hh)
            for pointer in (old_pointer,a.gamepad_pointer):
                if pointer:
                    px=(width-ww*scale)/2+pointer[0]*ww*scale
                    py=(height-hh*scale)/2+pointer[1]*hh*scale
                    a.area.queue_draw_area(int(px-14),int(py-14),28,28)
            if control['slicing'] and a.game.state=='playing':
                # Engine coordinates are independent of display resolution.
                x=control['x']*self.game_world[0];y=control['y']*self.game_world[1]
                a.game.input('gamepad',x,y);a.trail('gamepad',x,y)
            else:a.game.paths.pop('gamepad',None)
            a.mouse=False
        else:
            a.keys.clear()
            if control['move_axis']<-.1:a.keys.add(Gdk.KEY_Left)
            elif control['move_axis']>.1:a.keys.add(Gdk.KEY_Right)
            if control['crouch']:a.keys.add(Gdk.KEY_Down)
            a.manual_guard=control['guard'];a.manual_jump=control['jump']
            for move in control['moves']:a.perform(move)
        result=tick()
        if self.args.mode=='fight':a.last_status='手柄：左摇杆进退/蹲防 · A跳 · X拳 · B腿 · Y双掌 · LB防御 · RB大招'
        return result

    def gamepad_overlay(self,a,args):
        if not getattr(self,'gamepad',None):return
        if len(args)<2:return
        cr=args[1]
        if self.args.mode=='fruit' and getattr(a,'gamepad_pointer',None):
            x,y,slicing=a.gamepad_pointer;width,height=a.area.get_allocated_width(),a.area.get_allocated_height()
            ww,hh=self.game_world;scale=min(width/ww,height/hh)
            x=(width-ww*scale)/2+x*ww*scale;y=(height-hh*scale)/2+y*hh*scale
            cr.save();cr.set_line_width(4);cr.set_source_rgb(.10,.15,.13);cr.arc(x,y,7,0,math.tau);cr.stroke()
            cr.set_line_width(2);cr.set_source_rgb(*((.95,.30,.18) if slicing else (.90,.69,.25)))
            cr.arc(x,y,7,0,math.tau);cr.stroke();cr.restore()
        if not self.gamepad_notice:return
        cr.save();cr.set_source_rgba(.05,.09,.10,.88);cr.rectangle(0,0,a.area.get_allocated_width(),44);cr.fill()
        cr.set_source_rgb(1.,.94,.76);cr.select_font_face('Sans');cr.set_font_size(20)
        cr.move_to(18,29);cr.show_text(self.gamepad_notice);cr.restore()

    def ink_gamepad_tick(self):
        if self.closed:return False
        if self.paused:return True
        control=self.gamepad.ink(time.monotonic())
        label=(control['reason'] if not control['valid'] else
               '当前画笔：%s'%brush_name(getattr(self.app,'current_theme','mixed'),control['action']))
        self._ink_pad_valid=control['valid'];self._ink_pad_text=label;self.ink_gamepad_labels()
        if not control['valid'] or not control['draw'] or self._gamepad_ink_busy:return True
        self._gamepad_ink_busy=True
        # The backend accepts an explicit artistic command, never invented joints.
        def send():
            try:
                body=dict(command='gamepad_stroke',action=control['action'],speed=control['speed'])
                request=urllib.request.Request(self.args.pose_url.removesuffix('/api/state')+'/api/command',
                    data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request,timeout=5) as response:
                    result=json.loads(response.read(262145))
                if result.get('accepted') is not True:raise RuntimeError(result.get('reason','落墨请求未接受'))
            except Exception as error:
                GLib.idle_add(self.app.publish,'notice','手柄落墨失败：'+str(error))
            finally:GLib.idle_add(self.ink_gamepad_done)
        threading.Thread(target=send,name='gamepad-ink-command',daemon=True).start()
        return True

    def ink_gamepad_done(self):
        self._gamepad_ink_busy=False
        return False

    def ink_gamepad_labels(self):
        message=getattr(self,'_ink_pad_text','手柄控制 · 正在连接')
        if getattr(self,'_ink_pad_valid',True):message=getattr(self,'_ink_state_message','') or message
        self.app.status.set_text(message)
        self.app.step.set_text('')
        self.app.metrics.set_text('')
    def read_control(self):
        try:
            for line in sys.stdin:
                if len(line)>65536:continue
                try:request=json.loads(line);op=request.get('op')
                except (ValueError,AttributeError):continue
                if op=='action':GLib.idle_add(self.handle_action,request,priority=GLib.PRIORITY_DEFAULT);continue
                fn={'pause':self.pause,'resume':self.resume,'shutdown':self.close}.get(op)
                if fn:GLib.idle_add(fn,priority=GLib.PRIORITY_DEFAULT)
        finally:GLib.idle_add(self.close,priority=GLib.PRIORITY_DEFAULT)


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['ink','fight','fruit'],required=True)
    p.add_argument('--source',required=True);p.add_argument('--embed-socket',type=int,required=True)
    p.add_argument('--pose-url',default='http://127.0.0.1:8088/api/state');p.add_argument('--offline-preview',action='store_true')
    a=p.parse_args()
    # Window.tick is registered in constructors; intercept that registration so
    # paused menus never advance simulation, recognize gestures, or grow queues.
    original_timeout=GLib.timeout_add;holder={}
    def timeout(interval,callback,*args,**kwargs):
        if getattr(callback,'__name__','')=='tick':
            def guarded():
                owner=holder.get('controller')
                if owner and owner.closed:return False
                if owner and owner.paused:
                    if owner.app and hasattr(owner.app,'last'):owner.app.last=time.monotonic()
                    return True
                return callback(*args)
            return original_timeout(interval,guarded,**kwargs)
        return original_timeout(interval,callback,*args,**kwargs)
    GLib.timeout_add=timeout
    controller=Controller(a);holder['controller']=controller
    Gtk.main()

if __name__=='__main__':main()
