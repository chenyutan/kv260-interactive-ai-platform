#!/usr/bin/env python3
"""Native closed-course kart racer; shared pose input, bounded render and async audio."""
import argparse
import ctypes
from collections import deque
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request
os.environ['GDK_RENDERING']='similar'
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from game_source_policy import SourceGapPolicy
from action_dispatch import ActionDispatcher
from gamepad.adapters import create_gamepad
from platformer.main import configure_native_canvas,start_canvas_ticks,stop_canvas_ticks,configure_game_priority
from sword.metrics import LatestMetricsWriter
from kart.engine import KartGame, TRACK, clamp
from kart.controls import PoseControls, gamepad_action
from kart.render import Renderer
from kart.audio import KartSound

def emit(event,**fields):
    fields.setdefault('mode','kart')
    try:print(json.dumps(dict(event=event,**fields),ensure_ascii=False),flush=True)
    except (OSError,ValueError):pass

def choose_native_visual(canvas):
    """Use the real X root visual, not GDK's equivalent cloned GL visual.

    This board's Mali EGL advertises its original X visual IDs. GDK may choose
    another TrueColor visual with identical masks that EGL cannot bind.
    """
    try:
        gi.require_version('GdkX11','3.0')
        from gi.repository import GdkX11
        x=ctypes.CDLL('libX11.so.6')
        x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=ctypes.c_void_p
        x.XDefaultScreen.argtypes=[ctypes.c_void_p];x.XDefaultScreen.restype=ctypes.c_int
        x.XDefaultVisual.argtypes=[ctypes.c_void_p,ctypes.c_int];x.XDefaultVisual.restype=ctypes.c_void_p
        x.XVisualIDFromVisual.argtypes=[ctypes.c_void_p];x.XVisualIDFromVisual.restype=ctypes.c_ulong
        x.XCloseDisplay.argtypes=[ctypes.c_void_p]
        display=x.XOpenDisplay(None)
        if not display:return None
        try:
            visual_id=int(x.XVisualIDFromVisual(x.XDefaultVisual(display,x.XDefaultScreen(display))))
            visual=GdkX11.X11Screen.lookup_visual(canvas.get_screen(),visual_id)
            if visual is not None:canvas.set_visual(visual);return visual_id
        finally:x.XCloseDisplay(display)
    except (OSError,ImportError,ValueError,TypeError,AttributeError):pass
    return None

class KartPanel(Gtk.Box):
    def __init__(self,args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.args=args;self.stop=threading.Event();self.lock=threading.Lock()
        self.active=not bool(args.embed_socket);self.generation=0;self.latest=None
        self.tracker=PoseControls();self.game=KartGame(seed=args.seed)
        self.gamepad=create_gamepad('kart',disabled=args.offline_preview)
        self.renderer=Renderer(on_status=emit);self.sound=KartSound(enabled=not args.offline_preview)
        self.control=dict(valid=False,reason='waiting');self.pending=deque(maxlen=16);self.down=set()
        self.started=self.last_tick=time.monotonic();self.entry_until=self.last_tick+args.entry_delay
        self.last_valid=self.last_tick;self.tracking_paused=False;self.snapshot=None
        self.draw_stamps=deque(maxlen=900);self.draw_ms=deque(maxlen=900);self.tick_ms=deque(maxlen=900)
        self.interval_ms=deque(maxlen=9000);self.frames_drawn=0;self.cpu_start=time.process_time()
        self.next_metric=0.;self.metrics_writer=LatestMetricsWriter(args.metrics) if args.metrics else None
        self.dispatcher=ActionDispatcher('kart',lambda:not self.active,self.execute_action,emit)
        self.canvas=Gtk.DrawingArea();self.native_visual_id=choose_native_visual(self.canvas)
        self.canvas.set_hexpand(True);self.canvas.set_vexpand(True)
        self.canvas.set_size_request(640,360);self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.canvas.connect('button-press-event',self.click);self.canvas.connect('draw',self.draw)
        # The common Cairo helper connects normal handlers later in main().
        # Our after-handlers can select direct EGL only after those checks.
        self.canvas.connect_after('realize',self.prepare_renderer)
        self.canvas.connect_after('size-allocate',self.resize_renderer)
        self.canvas.connect('unrealize',lambda *_:self.renderer.release_native_output())
        self.pack_start(self.canvas,True,True,0)
        self.poll_thread=None
        if not args.offline_preview and self.gamepad is None:
            self.poll_thread=threading.Thread(target=self.poll,name='kart-latest-pose',daemon=True);self.poll_thread.start()
        self.timer=start_canvas_ticks(self.canvas,self.tick)
        self.reporter=None
        if not args.offline_preview:
            from visitor.reporter import attach
            self.reporter=attach(self,'kart',GLib)
        if args.benchmark:
            self.game.start();self.entry_until=0;GLib.timeout_add_seconds(args.benchmark,self.finish_benchmark)
        if args.fixture:
            from kart.fixtures import apply_fixture
            apply_fixture(self.game,args.fixture);self.entry_until=0
        self.native_presentation={}
        self.game_priority={}

    def prepare_renderer(self,widget):
        # This runs once while the host still shows its loading view.
        self.renderer.configure_depth(widget.get_visual().get_depth())
        width,height=max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height())
        try:
            gi.require_version('GdkX11','3.0')
            from gi.repository import GdkX11
            native=widget.get_window()
            if isinstance(native,GdkX11.X11Window) and widget.get_has_window():
                native.ensure_native()
                xid=int(native.get_xid())
                # EGL opens a separate Xlib connection. Finish creating this
                # drawable on GDK's connection before EGL queries its visual.
                widget.get_display().sync()
            else:xid=0
        except (ImportError,ValueError,AttributeError,TypeError):xid=0
        self.renderer.attach_window(xid,width,height)
        self.apply_renderer_presentation(widget)
        if not self.renderer.is_native:
            context=Gdk.cairo_create(widget.get_window())
            self.renderer.warm(context,width,height)

    def apply_renderer_presentation(self,widget):
        native=self.renderer.is_native
        widget.set_double_buffered(not native)
        if native:
            widget.set_app_paintable(True)
            # GTK must not clear/paint over EGL's completed scene + HUD swap.
            try:widget.get_window().set_background_pattern(None)
            except (AttributeError,TypeError):pass
        self.native_presentation.update(double_buffered=not native,
            presentation='egl-gles3d-single-swap' if native else 'gtk-complete-frame',
            renderer_accelerated=self.renderer.metrics().get('accelerated',False),multipass_text=not native,
            reason='native-gles3d-complete-frame' if native else 'cairo-fallback')

    def resize_renderer(self,widget,*unused):
        if self.renderer.is_native:
            self.renderer.resize(max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height()))
        self.apply_renderer_presentation(widget)

    def clear_input(self):
        with self.lock:self.latest=None;self.generation+=1
        self.tracker.reset();self.pending.clear();self.down.clear()
        if getattr(self,'gamepad',None):self.gamepad.reset()
        self.control=dict(valid=False,reason='waiting');self.last_valid=time.monotonic();self.tracking_paused=False

    def poll(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.stop.is_set():
            with self.lock:active,generation=self.active,self.generation
            if not active:self.stop.wait(.08);continue
            value=None
            try:
                with opener.open(self.args.base+'/api/state',timeout=.45) as response:raw=response.read(256*1024+1)
                if len(raw)<=256*1024:
                    value=json.loads(raw)
                    if not isinstance(value,dict):value=None
            except (OSError,ValueError):pass
            with self.lock:
                if self.active and generation==self.generation:self.latest=value
            self.stop.wait(.02)

    def operation(self,op):
        if op=='shutdown':
            if self.stop.is_set():return False
            with self.lock:self.active=False;self.stop.set();self.generation+=1;self.latest=None
            self.sound.close()
            if self.gamepad:self.gamepad.close()
            if self.metrics_writer:self.metrics_writer.close()
            if self.timer:stop_canvas_ticks(self.timer);self.timer=None
            # Destroy EGL resources before GTK destroys the native X11 window.
            self.renderer.release_native_output()
            if Gtk.main_level():Gtk.main_quit()
            return False
        if self.stop.is_set() or op not in ('pause','resume'):return False
        active=op=='resume'
        if active==self.active:return False
        with self.lock:self.active=active
        self.clear_input();self.last_tick=time.monotonic()
        if active:self.game.resume()
        else:self.game.pause();self.sound.set_playing(False)
        self.canvas.queue_draw();emit('resumed' if active else 'paused',game_state=self.game.state)
        return False

    def restart(self,*,while_paused=False):
        if self.stop.is_set() or not self.active and not while_paused:return False
        self.game.restart();self.clear_input();self.sound.restart();self.last_tick=time.monotonic()
        if not self.active:self.game.pause()
        self.canvas.queue_draw();emit('kart_round_started');return True

    def execute_action(self,action,done):
        if action.startswith('audio_'):
            accepted=self.sound.handle(action);done(accepted,audio=self.sound.director.status());return
        if action in ('start','restart'):
            if not self.restart(while_paused=True):done(False,'restart_unavailable');return
        elif action not in ('pause','continue'):done(False,'unsupported_action');return
        done(True,game_state=self.game.state)

    def handle_action(self,request):return self.dispatcher.dispatch(request)
    def request_menu(self):
        if not self.args.embed_socket and not self.active:self.operation('resume');return
        self.operation('pause');emit('menu_requested')
    def key(self,_,event,pressed=True):
        if event.keyval in (Gdk.KEY_Escape,Gdk.KEY_Menu,Gdk.KEY_F1):
            if pressed:self.request_menu()
            return True
        # Keyboard remains an accessibility fallback without owning the camera.
        if pressed:
            if event.keyval not in self.down:
                fields={Gdk.KEY_space:('item',True),Gdk.KEY_x:('item',True),Gdk.KEY_Return:('confirm',True)}
                if event.keyval in fields:self.pending.append(fields[event.keyval])
            self.down.add(event.keyval)
        else:self.down.discard(event.keyval)
        return True
    def click(self,_,event):
        if event.button==1 and self.active and self.game.state in ('title','finished'):self.restart()
        return True

    def _benchmark_actions(self):
        # Deterministic preview only. No live pose is polled in this mode.
        g=self.game;r=g.player
        if g.state=='finished':g.restart()
        curve=TRACK.curvature(r.s)
        steer=clamp(-r.x*.30+curve*r.speed*r.speed*.078/(4.6+r.speed*.075),-1,1)
        return dict(valid=True,steer=steer,accelerate=1.,item=bool(r.item),
                    drift=abs(curve)>.014 and (int(g.elapsed*1.3)%5<4))

    def tick(self):
        if self.stop.is_set():return False
        begin=time.monotonic();dt=max(0.,begin-self.last_tick);self.last_tick=begin
        if self.active:
            if self.gamepad:
                action=gamepad_action(self.gamepad.sample(begin))
            elif self.args.offline_preview:
                action=self._benchmark_actions() if self.args.benchmark else dict(valid=True,reason='keyboard')
            else:
                with self.lock:state=self.latest
                action=self.tracker.update(state,begin)
                suite=(state or {}).get('suite') or {}
                if isinstance(suite,dict) and (suite.get('menu') or suite.get('control_active')):
                    action=dict(valid=False,reason='menu_gesture');self.tracker.reset()
            if self.gamepad is None:
                for key,value in self.pending:action[key]=value;action['valid']=True
                keyboard_steer=int(Gdk.KEY_Right in self.down or Gdk.KEY_d in self.down)-int(Gdk.KEY_Left in self.down or Gdk.KEY_a in self.down)
                if keyboard_steer:action['steer']=keyboard_steer;action['valid']=True
                if Gdk.KEY_Shift_L in self.down or Gdk.KEY_Shift_R in self.down:action['drift']=True;action['valid']=True
                if Gdk.KEY_Down in self.down or Gdk.KEY_s in self.down:action['brake']=True;action['valid']=True
            self.pending.clear();self.control=action
            if action.get('valid'):self.last_valid=begin
            # Only a sustained missing source freezes the run. Brief dropped
            # observations neither pause music nor open a reconnect/countdown.
            if not hasattr(self,'source_gap'):self.source_gap=SourceGapPolicy()
            self.tracking_paused=(self.source_gap.update(state if not self.args.offline_preview and self.gamepad is None else None,
                                  begin,offline=self.args.offline_preview)
                                  and self.game.state in ('running','countdown'))
            if self.gamepad:self.tracking_paused=not action.get('valid',False)
            entered=begin>=self.entry_until
            self.sound.set_playing(entered and not self.tracking_paused and self.game.state in ('countdown','running','finished'))
            if entered and not self.tracking_paused:
                if action.get('confirm') and self.game.state in ('title','finished'):self.restart()
                if not self.args.hold_frame:self.game.update(dt,action)
                events=self.game.drain_events()
                for event in events:
                    self.renderer.event(event)
                    if event.get('kind')=='finished':emit('kart_round_completed',report=self.game.report())
                self.sound.events(events)
                self.renderer.update_effects(dt)
            notice=''
            if not entered:notice=f'{math.ceil(self.entry_until-begin)}秒后开始'
            elif self.tracking_paused:notice=action.get('reason','手柄未连接') if self.gamepad else '请让肩、手臂和髋回到镜头范围'
            elif self.gamepad:notice=''
            elif self.game.state=='title' and not action.get('valid'):notice='请让肩、手臂入镜，双手高举开始'
            snap=self.game.snapshot();snap['ui']=dict(notice=notice,tracking_paused=self.tracking_paused,
                                                     control_mode='gamepad' if self.gamepad else 'keyboard' if self.args.offline_preview else 'body')
            self.snapshot=snap;self.canvas.queue_draw()
        self.tick_ms.append((time.monotonic()-begin)*1000)
        if self.metrics_writer and begin>=self.next_metric:
            self.metrics_writer.submit(self.performance());self.next_metric=begin+1.
        return True

    def draw(self,widget,cr):
        begin=time.monotonic();was_native=self.renderer.is_native
        native_frame=self.renderer.render(cr,self.snapshot or self.game.snapshot(),
            max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height()))
        if was_native and not self.renderer.is_native:self.apply_renderer_presentation(widget)
        end=time.monotonic()
        if self.draw_stamps:self.interval_ms.append((end-self.draw_stamps[-1])*1000)
        self.draw_stamps.append(end);self.draw_ms.append((end-begin)*1000);self.frames_drawn+=1
        return bool(native_frame)

    def performance(self):
        def pct(values,p):
            values=sorted(values);return values[max(0,math.ceil(len(values)*p)-1)] if values else None
        stamps=list(self.draw_stamps);intervals=sorted(self.interval_ms,reverse=True)
        worst=intervals[:max(1,math.ceil(len(intervals)*.01))]
        return dict(mode='kart',frames_drawn=self.frames_drawn,
            draw_fps=(len(stamps)-1)/(stamps[-1]-stamps[0]) if len(stamps)>1 else 0,
            one_percent_low_fps=1000/(sum(worst)/len(worst)) if worst else 0,
            draw_ms_p50=pct(self.draw_ms,.5),draw_ms_p95=pct(self.draw_ms,.95),
            frame_interval_ms_p99=pct(self.interval_ms,.99),tick_ms_p95=pct(self.tick_ms,.95),
            cpu_single_core_percent=(time.process_time()-self.cpu_start)/max(.001,time.monotonic()-self.started)*100,
            game_state=self.game.state,elapsed=self.game.elapsed,entities=len(self.game.karts)+len(self.game.projectiles),
            output_size=[self.canvas.get_allocated_width(),self.canvas.get_allocated_height()],
            camera_used=not self.args.offline_preview and self.gamepad is None,physical_scanout_measured=False,
            renderer=self.renderer.metrics(),audio=self.sound.director.status(),
            game_priority=self.game_priority,
            tick_driver=self.timer['kind'] if self.timer else None)

    def finish_benchmark(self):
        if self.args.metrics:self.args.metrics.write_text(json.dumps(self.performance(),indent=2),encoding='utf-8')
        self.operation('shutdown');return False

def commands(panel):
    try:
        for line in sys.stdin:
            try:request=json.loads(line)
            except ValueError:continue
            if not isinstance(request,dict):continue
            # A busy GPU frame clock can keep default-idle (priority 200)
            # callbacks waiting indefinitely. Host control is normal-priority
            # GUI work and must run ahead of the next paint.
            if request.get('op')=='action':GLib.idle_add(panel.handle_action,request,priority=GLib.PRIORITY_DEFAULT)
            elif request.get('op') in ('pause','resume','shutdown'):
                GLib.idle_add(panel.operation,request['op'],priority=GLib.PRIORITY_DEFAULT)
                if request['op']=='shutdown':return
    except OSError:pass
    GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_DEFAULT)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--embed-socket',type=int);p.add_argument('--base-url','--pose-url',dest='base',default='http://127.0.0.1:8088')
    p.add_argument('--offline-preview',action='store_true');p.add_argument('--fullscreen',action='store_true')
    p.add_argument('--entry-delay',type=float,default=0.);p.add_argument('--metrics',type=Path)
    p.add_argument('--benchmark',type=int,default=0);p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--fixture',choices=('starting-grid','bend','village','items'))
    p.add_argument('--hold-frame',action='store_true',help='Offline fixture capture only')
    p.add_argument('--size',default='960x540');args=p.parse_args(argv)
    if not math.isfinite(args.entry_delay) or not 0<=args.entry_delay<=30:p.error('invalid entry delay')
    if args.benchmark and not args.offline_preview:p.error('benchmark requires --offline-preview')
    if (args.fixture or args.hold_frame) and not args.offline_preview:p.error('fixtures require --offline-preview')
    try:w,h=map(int,args.size.split('x'));assert 640<=w<=1920 and 360<=h<=1080
    except (ValueError,AssertionError):p.error('invalid size')
    panel=KartPanel(args)
    win=Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='赛车')
    panel.native_presentation=configure_native_canvas(panel.canvas,win)
    panel.game_priority=configure_game_priority()
    win.set_default_size(w,h);win.add(panel);win.connect('key-press-event',panel.key)
    win.connect('key-release-event',lambda w,e:panel.key(w,e,False));win.connect('destroy',lambda *_:panel.operation('shutdown'))
    if args.embed_socket:threading.Thread(target=commands,args=(panel,),daemon=True).start()
    win.show_all()
    if args.fullscreen and not args.embed_socket:win.fullscreen()
    signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_DEFAULT))
    signal.signal(signal.SIGINT,lambda *_:GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_DEFAULT))
    GLib.idle_add(lambda:(emit('ready'),False)[1],priority=GLib.PRIORITY_DEFAULT)
    try:Gtk.main()
    finally:
        panel.operation('shutdown')
        if panel.poll_thread:panel.poll_thread.join(.8)
    return 0

if __name__=='__main__':raise SystemExit(main())
