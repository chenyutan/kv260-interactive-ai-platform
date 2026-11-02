#!/usr/bin/env python3
"""Native PS platform game. One latest pose slot, no camera or model ownership."""
import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.request
# Keep the GTK backing surface on the X11 server. An image-backed buffer can
# upload cached labels in separate requests, exposing a partially drawn title
# or instruction line even with widget double buffering enabled.
os.environ['GDK_RENDERING']='similar'
import cairo
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from action_dispatch import ActionDispatcher
from gamepad.adapters import create_gamepad
from platformer.engine import Game
from platformer.controls import PoseControls
from platformer.render import Renderer
from platformer.audio import PlatformerSound
from sword.metrics import LatestMetricsWriter

def emit(event,**fields):
    packet=dict(event=event,mode='platformer')
    packet.update(fields)
    try: print(json.dumps(packet,ensure_ascii=False),flush=True)
    except (OSError,ValueError): pass

def confirmation_context(game, ready):
    """Downward presses own only explicit interaction states, never free play."""
    if ready:return 'title'
    if game.state in ('over','won'):return 'terminal'
    if game.can_enter_pipe(require_stopped=True):return 'pipe'
    return False

def title_notice(entry_remaining,control,*,offline=False):
    """Finite title messages avoid continually rebuilding changing glyphs."""
    if entry_remaining>0:
        return f'{math.ceil(entry_remaining)}秒后可开始，请准备入镜'
    if offline:return '按回车开始 · 方向键移动 · 空格跳跃'
    reason=control.get('reason','waiting')
    if reason=='missing_right_arm':return '右手臂未识别，请完整入镜'
    if not control.get('valid'):
        if reason in ('source_expired','pose_timeout','out_of_order_pose','invalid_source'):
            return '骨架更新中，请稍候再操作'
        if reason=='menu_gesture':return '菜单操作中，稍候再操作'
        return '请让肩部和右手臂完整入镜'
    if control.get('calibration') == 'frozen':
        if not control.get('confirm_ready') and not control.get('confirm_pending'):
            return '右手放在舒适位置，稍停后下压开始'
    if control.get('confirm_pending'):return '右手已下压，正在确认'
    if control.get('confirm_ready'):return '右手向下压即可开始'
    return '右手放在舒适位置，稍停后下压开始'

def configure_game_priority():
    """Best-effort nice -5 for the current Linux GTK main thread only.

    Call after this game's worker threads have started, before showing its
    window. Existing higher priorities are preserved; denied/noninteractive
    sudo or missing platform support never prevents the game from opening.
    """
    pid=os.getpid()
    state=dict(schema=1,pid=pid,target_nice=-5,before=None,after=None,
               attempted=False,success=False,changed=False,reason='non-linux')
    if sys.platform!='linux':return state
    if threading.current_thread() is not threading.main_thread():
        state['reason']='not-main-thread';return state
    try:
        state['before']=os.getpriority(os.PRIO_PROCESS,pid)
    except (AttributeError,OSError) as error:
        state.update(reason='priority-read-unavailable',error_type=type(error).__name__)
        return state
    state['after']=state['before']
    if state['before']<=state['target_nice']:
        state.update(success=True,reason='existing-priority-preserved')
    else:
        state['attempted']=True
        try:
            result=subprocess.run(['sudo','-n','renice','-n','-5','-p',str(pid)],
                                  capture_output=True,text=True,timeout=1.,check=False,shell=False)
            state['returncode']=result.returncode
            if result.returncode:
                state.update(reason='renice-rejected',detail=(result.stderr or result.stdout or '')[:240])
            else:
                state['reason']='priority-verification-pending'
        except FileNotFoundError as error:
            state.update(reason='renice-unavailable',error_type=type(error).__name__)
        except subprocess.TimeoutExpired as error:
            state.update(reason='renice-timeout',error_type=type(error).__name__)
        except OSError as error:
            state.update(reason='renice-error',error_type=type(error).__name__)
    try:
        state['after']=os.getpriority(os.PRIO_PROCESS,pid)
    except (AttributeError,OSError) as error:
        state.update(after=None,success=False,changed=None,reason='priority-verification-unavailable',
                     error_type=type(error).__name__)
        return state
    if state['attempted']:
        state['success']=state.get('returncode')==0 and state['after']<=state['target_nice']
        if state['success']:state['reason']='priority-configured'
        elif state['reason']=='priority-verification-pending':state['reason']='priority-not-applied'
        if not state['success'] and state['after']!=state['before']:
            # A timed-out tool can have applied its change before exiting.
            # Restore only our main thread to its original nice value.
            state['rollback_attempted']=True
            try:
                os.setpriority(os.PRIO_PROCESS,pid,state['before'])
                state['after']=os.getpriority(os.PRIO_PROCESS,pid)
            except (AttributeError,OSError) as error:
                state['after']=None
                state['rollback_error_type']=type(error).__name__
    state['changed']=state['after']!=state['before'] if state['after'] is not None else None
    return state

def configure_native_canvas(canvas, window):
    """Present the world and pixel text together through GTK double buffering.

    The returned dictionary describes actual GI objects and widget flags, and
    is updated on realize/unrealize. Copy it when writing a metrics report.
    The renderer paints the world before its cached text. Even an opaque
    16:9 frame must stay buffered so scanout never observes the text-free
    intermediate state. Unsupported backends retain the same safe buffering.
    """
    state = dict(schema=2, enabled=False, realized=False, realize_count=0,
                 display_type=None, canvas_window_type=None,
                 x11_display=False, x11_window=False, owned_window=False,
                 ensure_native=None, has_native=False, xid=None,
                 double_buffered=True, allocation=[0, 0], full_opaque_output=False,
                 canvas_app_paintable=False,
                 window_app_paintable=False, reason='awaiting-realize',
                 presentation='gtk-complete-frame', multipass_text=True,
                 gdk_rendering=os.environ.get('GDK_RENDERING'))

    def type_name(value):
        gtype = getattr(value, '__gtype__', None)
        return gtype.name if gtype is not None else type(value).__name__

    def flags():
        buffered_flag = canvas.get_double_buffered()
        state['double_buffered'] = (buffered_flag if buffered_flag is True or
                                    buffered_flag is False else None)
        state['canvas_app_paintable'] = canvas.get_app_paintable() is True
        state['window_app_paintable'] = window.get_app_paintable() is True

    def buffered(reason):
        state.update(enabled=False, reason=reason)
        if canvas.get_double_buffered() is not True:
            canvas.set_double_buffered(True)
        flags()

    def evaluate(widget, *unused):
        state.pop('error_type', None)
        state.update(enabled=False, display_type=None, canvas_window_type=None,
                     x11_display=False, x11_window=False, owned_window=False,
                     ensure_native=None, has_native=False, xid=None)
        try:
            width, height = widget.get_allocated_width(), widget.get_allocated_height()
            state['allocation'] = [width, height]
            state['full_opaque_output'] = (width > 0 and height > 0 and
                                           width*180 == height*320)
            display = widget.get_display()
            native = widget.get_window()
            state['display_type'] = type_name(display)
            state['canvas_window_type'] = type_name(native)
            state['owned_window'] = widget.get_has_window() is True
            try:
                gi.require_version('GdkX11', '3.0')
                from gi.repository import GdkX11
            except (ImportError, ValueError):
                return buffered('gdk-x11-unavailable')
            state['x11_display'] = isinstance(display, GdkX11.X11Display)
            state['x11_window'] = isinstance(native, GdkX11.X11Window)
            if not state['x11_display']:
                return buffered('non-x11-display')
            if not state['owned_window']:
                return buffered('shared-widget-window')
            if not state['x11_window']:
                return buffered('non-x11-canvas-window')
            state['ensure_native'] = native.ensure_native() is True
            state['has_native'] = native.has_native() is True
            if not state['ensure_native'] or not state['has_native']:
                return buffered('native-window-unavailable')
            state['xid'] = int(native.get_xid())
            if state['xid'] <= 0:
                return buffered('invalid-x11-window-id')
            # Every output paints the scene and text separately. Keep both
            # inside GTK's back buffer, including exact 16:9 outputs.
            if not state['full_opaque_output']:
                return buffered('letterbox-output')
            if widget.get_double_buffered() is not True:
                widget.set_double_buffered(True)
            flags()
            state['enabled'] = state['double_buffered'] is True
            state['reason'] = 'native-x11-complete-frame' if state['enabled'] else 'buffer-enable-rejected'
        except Exception as error:
            # GI exceptions must not prevent a game from opening. A failure
            # after the setter also restores double buffering before paint.
            state['error_type'] = type(error).__name__
            try:
                buffered('native-configuration-error')
            except Exception:
                state.update(enabled=False, reason='buffer-fallback-error')

    def realize(widget):
        state.update(realized=True, realize_count=state['realize_count']+1)
        evaluate(widget)

    def size_allocate(widget, *unused):
        if widget.get_realized() is True:
            evaluate(widget)

    def unrealize(widget):
        state.update(realized=False, enabled=False, has_native=False, xid=None,
                     reason='unrealized')
        try:
            buffered('unrealized')
        except Exception as error:
            state['error_type'] = type(error).__name__

    try:
        window.set_app_paintable(True)
        canvas.set_app_paintable(True)
        flags()
        canvas.connect('realize', realize)
        canvas.connect('unrealize', unrealize)
        canvas.connect('size-allocate', size_allocate)
        if canvas.get_realized() is True:
            realize(canvas)
    except Exception as error:
        state.update(reason='widget-configuration-unavailable',
                     error_type=type(error).__name__)
    return state

def start_canvas_ticks(canvas,tick):
    """Drive updates in GTK's pre-paint phase, with a legacy timer fallback.

    The tick owns its real monotonic dt and queue_draw request. Registering it
    with the canvas avoids a separate 16 ms timer drifting against GTK paint.
    Simple headless widgets and GTK builds without tick callbacks use GLib.
    """
    driver=dict(kind='timeout',canvas=canvas,identifier=None,active=True)
    def dispatch(*unused):
        if not driver['active']:return False
        keep=bool(tick())
        if not keep:driver['active']=False;driver['identifier']=None
        return keep and driver['active']
    register=getattr(canvas,'add_tick_callback',None)
    remove=getattr(canvas,'remove_tick_callback',None)
    if callable(register) and callable(remove):
        try:identifier=register(dispatch)
        except (AttributeError,NotImplementedError,TypeError):identifier=None
        # A genuine GTK callback ID is a positive guint. Generic test mocks
        # return a Mock instead, and must not accidentally disable their timer.
        if isinstance(identifier,int) and not isinstance(identifier,bool) and identifier>0:
            driver.update(kind='frame-clock',identifier=identifier)
            return driver
    driver['identifier']=GLib.timeout_add(16,dispatch)
    return driver

def stop_canvas_ticks(driver):
    """Remove a live registration once, including shutdown from its own tick."""
    if not driver or not driver['active']:return
    driver['active']=False
    identifier=driver['identifier'];driver['identifier']=None
    if identifier is None:return
    if driver['kind']=='frame-clock':driver['canvas'].remove_tick_callback(identifier)
    else:GLib.source_remove(identifier)

class PlatformerPanel(Gtk.Box):
    def __init__(self,args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,spacing=0)
        self.args=args
        self.stop=threading.Event()
        self.lock=threading.Lock()
        self.active=not bool(args.embed_socket)
        self.generation=0
        self.latest=None
        self.tracker=PoseControls()
        self.gamepad=create_gamepad('platformer',disabled=args.offline_preview)
        self.game=Game()
        self.game.pause()
        self.ready=True
        self.sound=PlatformerSound(enabled=not args.offline_preview)
        self.renderer=Renderer()
        self.control=dict(move=0,jump=False,confirm=False,valid=False,reason='waiting')
        self.keys=set()
        self.last_tick=time.monotonic()
        self.entry_until=self.last_tick+args.entry_delay if self.active else 0.
        self.frames_drawn=0
        self.draw_stamps=deque(maxlen=600)
        self.draw_ms=deque(maxlen=600)
        self.tick_ms=deque(maxlen=600)
        self.cpu_started=time.process_time()
        self.started=self.last_tick
        self.next_metric=0.
        self.metrics_writer=LatestMetricsWriter(args.metrics) if args.metrics else None
        self.snapshot=None
        self.poll_thread=None
        self.canvas=Gtk.DrawingArea()
        self.native_presentation=dict(enabled=False,reason='unconfigured')
        self.game_priority=dict(attempted=False,success=False,reason='unconfigured')
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.set_size_request(640,360)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.canvas.connect('button-press-event',self.click)
        self.canvas.connect('draw',self.draw)
        self.canvas.connect('unrealize',lambda *_:self.renderer.release_native_output())
        self.pack_start(self.canvas,True,True,0)
        self.connect('destroy',lambda *_:self.operation('shutdown'))
        self.dispatcher=ActionDispatcher('platformer',lambda:not self.active,self.execute_action,emit)
        if not args.offline_preview and self.gamepad is None:
            self.poll_thread=threading.Thread(target=self.poll,name='platformer-latest-pose',daemon=True)
            self.poll_thread.start()
        if self.active:self.sound.resume(self.game)
        self.timer=start_canvas_ticks(self.canvas,self.tick)
        self.reporter=None
        if not args.offline_preview:
            from visitor.reporter import attach
            self.reporter=attach(self,'platformer',GLib)
        if args.benchmark:
            self.ready=False
            self.game.start(0)
            if not self.active:self.game.pause()
            GLib.timeout_add_seconds(args.benchmark,self.finish_benchmark)

    def clear_input(self):
        with self.lock:self.latest=None;self.generation+=1
        self.tracker.reset()
        if getattr(self,'gamepad',None):self.gamepad.reset()
        self.keys.clear()
        self.control=dict(move=0,jump=False,confirm=False,valid=False,reason='waiting')

    def poll(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.stop.is_set():
            with self.lock: active,generation=self.active,self.generation
            if not active:
                self.stop.wait(.08)
                continue
            value=None
            try:
                with opener.open(self.args.base+'/api/state',timeout=.45) as response:
                    raw=response.read(256*1024+1)
                if len(raw)<=256*1024:
                    value=json.loads(raw)
                    if not isinstance(value,dict):value=None
            except (OSError,ValueError):pass
            # No queue, no GTK callbacks from transport; a pause epoch rejects
            # old HTTP responses even if they completed after resuming.
            with self.lock:
                if self.active and generation==self.generation:self.latest=value
            self.stop.wait(.02)

    def operation(self,op):
        if op=='shutdown':
            if self.stop.is_set():return False
            with self.lock:self.active=False;self.generation+=1;self.stop.set();self.latest=None
            self.game.pause()
            self.sound.close()
            if self.gamepad:self.gamepad.close()
            if self.metrics_writer:self.metrics_writer.close()
            if self.timer:stop_canvas_ticks(self.timer);self.timer=None
            if Gtk.main_level():Gtk.main_quit()
            return False
        if self.stop.is_set() or op not in ('pause','resume'):return False
        active=op=='resume'
        if active==self.active:return False
        with self.lock:self.active=active;self.generation+=1
        self.clear_input()
        self.last_tick=time.monotonic()
        if active:
            self.entry_until=self.last_tick+max(0.,self.args.entry_delay)
            if not self.ready:self.game.resume()
            self.sound.resume(self.game)
        else:
            self.game.pause()
            self.sound.pause(self.game)
        emit('resumed' if active else 'paused',game_state=self.game.state,game_clock=self.game.elapsed)
        self.canvas.queue_draw()
        return False

    def restart(self,*,while_paused=False):
        if self.stop.is_set() or not self.active and not while_paused:return False
        self.game.restart(countdown=1.2)
        self.ready=False
        self.clear_input()
        self.sound.restart(self.game)
        if not self.active:self.game.pause()
        self.last_tick=time.monotonic()
        self.canvas.queue_draw()
        return True

    def handle_action(self,request):return self.dispatcher.dispatch(request)

    def execute_action(self,action,done):
        if action.startswith('audio_'):
            self.sound.handle(action)
            done(True,audio=self.sound.director.status())
            return
        if action in ('restart','start'):
            if not self.restart(while_paused=True):done(False,'restart_unavailable');return
        elif action not in ('pause','continue'):done(False,'unsupported_action');return
        done(True,game_state=self.game.state)

    def request_menu(self):
        if not self.args.embed_socket and not self.active:self.operation('resume');return
        self.operation('pause')
        emit('menu_requested')

    def key(self,_,event,pressed=True):
        if event.keyval in (Gdk.KEY_Escape,Gdk.KEY_Menu,Gdk.KEY_F1):
            if pressed:self.request_menu()
            return True
        if not self.args.offline_preview:return False
        if pressed:self.keys.add(event.keyval)
        else:self.keys.discard(event.keyval)
        return True

    def click(self,_,event):
        # Mouse is a diagnostic fallback; normal play uses a contextual press.
        if event.button==1 and self.args.offline_preview and self.active:
            if self.ready or self.game.state in ('over','won'):self.restart()
        return True

    def tick(self):
        if self.stop.is_set():return False
        begin=time.monotonic()
        dt=max(0.,begin-self.last_tick)
        self.last_tick=begin
        if self.active and begin>=self.entry_until:
            with self.lock:state=self.latest
            if self.gamepad:
                self.control=self.gamepad.platformer(begin)
            elif self.args.offline_preview:
                move=int(Gdk.KEY_Right in self.keys)-int(Gdk.KEY_Left in self.keys)
                jump=Gdk.KEY_space in self.keys
                confirm=Gdk.KEY_Return in self.keys
                if self.args.benchmark:
                    move=1;jump=int((begin-self.started)*2)%2==0;confirm=False
                self.control=dict(move=move,jump=jump,confirm=confirm,valid=True,reason='offline',
                    calibration='ready',calibration_progress=1.,joystick_dx=move*.32,
                    joystick_dy=0.,jump_ready=not jump,confirm_pending=False,confirm_ready=False)
            else:
                self.control=self.tracker.update(state,begin,
                    confirm_context=confirmation_context(self.game,self.ready))
                suite=(state or {}).get('suite') or {}
                if not isinstance(suite,dict):suite={}
                if suite.get('menu') or suite.get('control_active'):
                    self.control=dict(move=0,jump=False,confirm=False,valid=False,reason='menu_gesture')
                    self.tracker.reset()
            if self.ready or self.game.state in ('over','won'):
                if self.control.get('confirm'):
                    self.restart()
            else:
                # Missing observations stop input; brief packet gaps never
                # pause clocks/music or create a restart countdown.
                if self.gamepad:
                    if self.control.get('valid') and self.game.state=='paused':self.game.resume()
                    elif not self.control.get('valid') and self.game.state!='paused':self.game.pause()
                self.game.advance(dt,self.control,snapshot=False)
            self.sound.tick(self.game)
        snapshot=self.game.snapshot()
        entry_remaining=max(0.,self.entry_until-begin)
        notice=(title_notice(entry_remaining,self.control,offline=self.args.offline_preview) if self.ready else
                '等待手臂入镜' if not self.args.offline_preview and not self.control.get('valid') else '')
        if self.gamepad:
            notice=(self.control.get('reason','手柄未连接') if not self.control.get('valid') else
                    '按 X 开始')
        snapshot['ui']=dict(ready=self.ready,tracking=self.control.get('valid',False),
            notice=notice,entry_remaining=entry_remaining,control=dict(self.control),
            control_mode='gamepad' if self.gamepad else 'body',
            confirm_context=confirmation_context(self.game,self.ready))
        self.snapshot=snapshot
        if self.active:self.canvas.queue_draw()
        self.tick_ms.append((time.monotonic()-begin)*1000)
        if self.metrics_writer and begin>=self.next_metric:
            self.metrics_writer.submit(self.performance())
            self.next_metric=begin+1.
        return True

    def draw(self,widget,cr):
        begin=time.monotonic()
        snapshot=self.snapshot or self.game.snapshot()
        self.renderer.render(cr,snapshot,max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height()))
        end=time.monotonic()
        self.frames_drawn+=1
        self.draw_stamps.append(end)
        self.draw_ms.append((end-begin)*1000)
        return False

    def performance(self):
        def pct(values,p):
            values=sorted(values)
            return values[max(0,math.ceil(len(values)*p)-1)] if values else None
        stamps=list(self.draw_stamps)
        elapsed=time.monotonic()-self.started
        return dict(mode='platformer',frames_drawn=self.frames_drawn,
            draw_fps=(len(stamps)-1)/(stamps[-1]-stamps[0]) if len(stamps)>1 else 0,
            draw_ms_p50=pct(self.draw_ms,.5),draw_ms_p95=pct(self.draw_ms,.95),
            tick_ms_p95=pct(self.tick_ms,.95),cpu_single_core_percent=(time.process_time()-self.cpu_started)/max(elapsed,.001)*100,
            game_state=self.game.state,elapsed=self.game.elapsed,logical_render_size=[320,180],
            output_size=[self.canvas.get_allocated_width(),self.canvas.get_allocated_height()],
            native_gui=True,physical_scanout_measured=False,camera_used=not self.args.offline_preview,
            tick_driver=self.timer['kind'] if self.timer else 'none',
            native_presentation=dict(self.native_presentation),
            game_priority=dict(self.game_priority),
            audio=self.sound.director.status())

    def finish_benchmark(self):
        if self.args.metrics:
            # Benchmark-only final durable write occurs after the timed window.
            self.args.metrics.write_text(json.dumps(self.performance(),indent=2),encoding='utf-8')
        self.operation('shutdown')
        return False

def read_commands(panel):
    try:
        for line in sys.stdin:
            try:value=json.loads(line)
            except ValueError:continue
            if not isinstance(value,dict):continue
            if value.get('op')=='action':GLib.idle_add(panel.handle_action,value,priority=GLib.PRIORITY_DEFAULT)
            elif value.get('op') in ('pause','resume','shutdown'):
                GLib.idle_add(panel.operation,value['op'],priority=GLib.PRIORITY_DEFAULT)
                if value['op']=='shutdown':return
    except OSError:pass
    GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_DEFAULT)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--embed-socket',type=int)
    parser.add_argument('--base-url',dest='base',default='http://127.0.0.1:8088')
    parser.add_argument('--offline-preview',action='store_true')
    parser.add_argument('--fullscreen',action='store_true')
    parser.add_argument('--entry-delay',type=float,default=3.)
    parser.add_argument('--metrics',type=Path)
    parser.add_argument('--benchmark',type=int,default=0)
    args=parser.parse_args(argv)
    if not math.isfinite(args.entry_delay) or not 0<=args.entry_delay<=30:parser.error('invalid entry delay')
    panel=PlatformerPanel(args)
    window=Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='超级玛丽')
    panel.native_presentation=configure_native_canvas(panel.canvas,window)
    window.set_default_size(960,540)
    window.add(panel)
    window.connect('key-press-event',panel.key)
    window.connect('key-release-event',lambda w,e:panel.key(w,e,False))
    window.connect('destroy',lambda *_:panel.operation('shutdown'))
    if args.embed_socket:threading.Thread(target=read_commands,args=(panel,),daemon=True).start()
    panel.game_priority=configure_game_priority()
    window.show_all()
    if args.fullscreen and not args.embed_socket:window.fullscreen()
    signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(panel.operation,'shutdown'))
    signal.signal(signal.SIGINT,lambda *_:GLib.idle_add(panel.operation,'shutdown'))
    GLib.idle_add(lambda:(emit('ready'),False)[1])
    try:Gtk.main()
    finally:
        panel.operation('shutdown')
        if panel.poll_thread:panel.poll_thread.join(.8)
    return 0

if __name__=='__main__':raise SystemExit(main())
