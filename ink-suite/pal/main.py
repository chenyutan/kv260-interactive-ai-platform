#!/usr/bin/env python3
"""Native InkSuite PAL adapter. Existing commercial data stays outside InkSuite."""
import argparse
from collections import deque
import json
import mmap
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pal.controls import BodyKeys, PadKeys, BODY_HINT, PAD_HINT, KEYS

DEFAULT_PAL_ROOT=Path('/home/ubuntu/games/pal-kv260/releases/20261015-dos-b9626ce0d0c6')
MAX_VIDEO_SIZE=(3840,2160)


def video_size(width,height):
    """Match the native PAL port limits; never silently draw a smaller surface."""
    if not all(isinstance(n,int) and not isinstance(n,bool) for n in (width,height)):
        raise ValueError('invalid PAL video dimensions')
    if not 320<=width<=MAX_VIDEO_SIZE[0] or not 200<=height<=MAX_VIDEO_SIZE[1]:
        raise ValueError('invalid PAL video dimensions')
    return width,height

def emit(event, **fields):
    fields.setdefault('mode','pal')
    try: print(json.dumps(dict(event=event,**fields),ensure_ascii=False),flush=True)
    except (OSError,ValueError): pass


def launch_spec(pal_root, save_dir, xid, control_file, bridge, size, no_audio=False):
    """Explicit child-only environment; no shell, game data copy or system changes."""
    pal_root, save_dir, bridge = Path(pal_root), Path(save_dir), Path(bridge)
    width,height=video_size(*size)
    if not isinstance(xid,int) or xid<=0:
        raise ValueError('invalid PAL target')
    env=dict(os.environ,SDL_WINDOWID=str(xid),SDL_VIDEODRIVER='x11',
             INK_PAL_CONTROL=str(control_file),LD_PRELOAD=str(bridge),
             SDL_VIDEO_X11_DGAMOUSE='0',SDL_NOMOUSE='1')
    env['LD_LIBRARY_PATH']=str(pal_root/'runtime/lib')+(':'+os.environ['LD_LIBRARY_PATH'] if os.environ.get('LD_LIBRARY_PATH') else '')
    # All input is normalized by InkSuite. The shipped PAL build does not init
    # SDL_INIT_JOYSTICK; no SDL joystick or global XTest input is used here.
    command=[str(pal_root/'engine/bin/sdlpal'),'--data-dir',str(pal_root/'data'),
             '--save-dir',str(save_dir),'--windowed','--size',f'{width}x{height}']
    if no_audio: command.append('--no-audio');env['SDL_AUDIODRIVER']='dummy'
    return command,env


class DirectedKeys:
    """Write only fixed PAL key bits to this child's private SDL bridge."""
    ORDER=('Up','Down','Left','Right','Return','Escape','space','Page_Up','Page_Down')
    def __init__(self,control):
        self.control=control;self.held=set();self.expires={};self.mask=0

    def send(self,key,pressed):
        if key not in self.ORDER:return
        bit=1<<self.ORDER.index(key)
        self.mask=self.mask|bit if pressed else self.mask&~bit
        struct.pack_into('=I',self.control,20,self.mask)

    def update(self,held,pulses=(),now=None):
        now=time.monotonic() if now is None else now
        for key in pulses:
            if key in KEYS:self.expires[key]=now+.11
        self.expires={key:until for key,until in self.expires.items() if until>now}
        target=(set(held)|set(self.expires))&KEYS
        for key in self.held-target:self.send(key,False)
        for key in target-self.held:self.send(key,True)
        self.held=target

    def release(self):
        self.expires.clear();self.update(set())

    def close(self):
        if self.control is not None:self.release();self.control=None


class PalChild:
    """Owned process group and private pause/audio state; never SIGSTOP."""
    def __init__(self,args,xid,size):
        self.args=args;self.xid=xid;self.size=size;self.process=None;self.keys=None
        self.temp=None;self.control=None;self.fd=None;self.log=None
        self.muted=False;self.started=0.;self.closing=False

    def start(self,active):
        if self.process is not None:return
        bridge=Path(__file__).with_name('libink-pal-control.so')
        pal_root=self.args.pal_root
        for file in (bridge,pal_root/'engine/bin/sdlpal',pal_root/'runtime/lib/libSDL-1.2.so.0'):
            if not file.is_file():raise RuntimeError('Missing PAL dependency: '+str(file))
        self.args.save_dir.mkdir(parents=True,exist_ok=True)
        runtime=os.environ.get('XDG_RUNTIME_DIR')
        self.temp=tempfile.TemporaryDirectory(prefix='ink-pal-',dir=runtime if runtime and os.access(runtime,os.W_OK) else None)
        ctl=Path(self.temp.name)/'control';self.fd=os.open(ctl,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        os.ftruncate(self.fd,32);self.control=mmap.mmap(self.fd,32)
        self.control[:]=bytes(32);self.write_word(0,0 if active else 1);self.write_word(2,int(self.muted))
        log_path=ROOT/'logs/pal-child.log';log_path.parent.mkdir(parents=True,exist_ok=True)
        # A bounded new log replaces only this adapter's diagnostic log.
        self.log=log_path.open('wb')
        command,env=launch_spec(pal_root,self.args.save_dir,self.xid,ctl,bridge,self.size,self.args.no_audio)
        self.keys=DirectedKeys(self.control)
        try:
            self.process=subprocess.Popen(command,cwd=str(pal_root/'engine'),env=env,
                stdin=subprocess.DEVNULL,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
        except Exception:
            self.close();raise
        self.started=time.monotonic();self.closing=False

    def word(self,index):
        return struct.unpack_from('=I',self.control,index*4)[0] if self.control is not None else 0

    def write_word(self,index,value):
        if self.control is not None:struct.pack_into('=I',self.control,index*4,value)

    def pause(self,paused):
        if self.keys:self.keys.release()
        self.write_word(0,1 if paused else 0)

    def mute(self,muted):
        self.muted=bool(muted);self.write_word(2,int(self.muted))

    def resize(self,width,height):
        size=video_size(width,height)
        if size==self.size:return False
        self.size=size
        # Word 3 was reserved in the original 32-byte private protocol. The SDL
        # thread consumes its latest resize request, including after a pause.
        self.write_word(3,(height<<16)|width)
        return True

    def close(self):
        self.closing=True
        if self.keys:self.keys.close();self.keys=None
        self.write_word(0,2)
        if self.process:
            try:self.process.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid,signal.SIGTERM)
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait(timeout=1)
            self.process=None
        if self.log:self.log.close();self.log=None
        if self.control is not None:self.control.close();self.control=None
        if self.fd is not None:os.close(self.fd);self.fd=None
        if self.temp:self.temp.cleanup();self.temp=None


def main(argv=None):
    import gi
    gi.require_version('Gtk','3.0');gi.require_version('GdkX11','3.0')
    from gi.repository import Gtk,GLib,GdkX11
    from action_dispatch import ActionDispatcher
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--embed-socket',type=int);p.add_argument('--base-url',default='http://127.0.0.1:8088')
    p.add_argument('--fullscreen',action='store_true');p.add_argument('--entry-delay',type=float,default=0)
    p.add_argument('--pal-root',type=Path,default=Path(os.environ.get('INK_PAL_ROOT',str(DEFAULT_PAL_ROOT))))
    p.add_argument('--save-dir',type=Path);p.add_argument('--no-audio',action='store_true')
    p.add_argument('--offline-preview',action='store_true');args=p.parse_args(argv)
    args.save_dir=args.save_dir or args.pal_root/'saves'
    if not 0<=args.entry_delay<=30:p.error('entry delay must be 0..30')

    class Panel(Gtk.Box):
        def __init__(self):
            super().__init__(orientation=Gtk.Orientation.VERTICAL)
            self.active=not bool(args.embed_socket);self.stopped=False;self.ready=False
            self.control_mode=os.environ.get('INK_CONTROL_MODE','body');self.generation=0
            self.gamepad_paused=None
            self.reader=None;self.body=BodyKeys();self.pad=PadKeys();self.latest=None
            self.lock=threading.Lock();self.stop=threading.Event();self.child=None;self.resize_timer=0
            self.entry_until=time.monotonic()+args.entry_delay
            self.canvas=Gtk.DrawingArea();self.canvas.set_hexpand(True);self.canvas.set_vexpand(True)
            self.canvas.set_size_request(320,240)
            # DOS PAL uses non-square 320x200 source pixels, presented at 4:3.
            # Allocate the native X11 surface itself at the fitted size so SDL
            # fills it; a later GTK allocation cannot scale an SDL surface.
            self.viewport=Gtk.AspectFrame.new(None,.5,.5,4/3,False)
            self.viewport.set_shadow_type(Gtk.ShadowType.NONE)
            self.viewport.set_hexpand(True);self.viewport.set_vexpand(True)
            self.viewport.add(self.canvas);self.pack_start(self.viewport,True,True,0)
            css=Gtk.CssProvider();css.load_from_data(b'.pal-viewport { background-color: #080b0b; }')
            self.viewport.get_style_context().add_class('pal-viewport')
            self.viewport.get_style_context().add_provider(css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.hint=Gtk.Label();self.hint.set_margin_top(4);self.hint.set_margin_bottom(4)
            self.hint.set_no_show_all(True)
            self.pack_start(self.hint,False,False,0);self.hint.hide()
            self.canvas.connect('realize',self.realize)
            self.canvas.connect('size-allocate',self.queue_viewport)
            self.dispatcher=ActionDispatcher('pal',lambda:not self.active,self.execute,emit)
            self.timer=GLib.timeout_add(20,self.tick)
            if not args.offline_preview:
                threading.Thread(target=self.poll,name='pal-pose-latest',daemon=True).start()

        def set_hint(self,message=''):
            # Healthy play has no instruction footer. Only actual errors or a
            # disconnected controller need a visible message; GTK allocation
            # changes still use the existing deferred native resize path.
            self.hint.set_text(message);self.hint.set_visible(bool(message))

        def realize(self,widget):
            # realize runs before the parent has its final allocation (formerly
            # launching 320x200 into a later 960x509 X window). Settle first.
            self.queue_viewport(widget)

        def queue_viewport(self,widget,*_):
            if self.stopped:return
            if self.resize_timer:GLib.source_remove(self.resize_timer)
            self.resize_timer=GLib.timeout_add(80,self.sync_viewport)

        def sync_viewport(self):
            self.resize_timer=0
            if self.stopped or not self.canvas.get_realized():return False
            try:
                width,height=self.canvas.get_allocated_width(),self.canvas.get_allocated_height()
                if width<320 or height<200:
                    # The host keeps this Stack page hidden until our ready
                    # acknowledgement. Bootstrap paused at a valid native size
                    # to break that dependency; its first visible allocation
                    # then resizes the same process to fill the fitted viewport.
                    if self.child:return False
                    size=(320,240)
                else:size=video_size(width,height)
                # Flush the GTK resize before the separate SDL process inspects
                # or changes this X11 window. Runtime resize never restarts PAL.
                self.canvas.get_display().sync()
                if self.child:
                    if self.child.resize(*size):emit('pal_viewport',width=size[0],height=size[1],resized=True)
                else:
                    child=PalChild(args,self.canvas.get_window().get_xid(),size)
                    initial_active=self.active and self.control_mode!='gamepad'
                    try:child.start(initial_active)
                    except Exception:
                        child.close();raise
                    self.child=child
                    self.gamepad_paused=not initial_active if self.control_mode=='gamepad' else None
                    emit('pal_viewport',width=size[0],height=size[1],resized=False)
            except Exception as error:
                emit('error',reason=str(error));self.set_hint('仙剑奇侠传无法启动：'+str(error))
            return False

        def poll(self):
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while not self.stop.is_set():
                with self.lock:active=self.active and self.control_mode=='body';generation=self.generation
                if not active:self.stop.wait(.1);continue
                value=None
                try:
                    with opener.open(args.base_url+'/api/state',timeout=.35) as r:raw=r.read(262145)
                    if len(raw)<=262144:value=json.loads(raw)
                    if not isinstance(value,dict):value=None
                except (OSError,ValueError):pass
                with self.lock:
                    if generation==self.generation and self.active:self.latest=value
                self.stop.wait(.025)

        def clear(self):
            self.body.reset();self.pad.reset();self.generation+=1
            with self.lock:self.latest=None
            if self.child and self.child.keys:self.child.keys.release()

        def mode(self,mode):
            if mode not in ('body','gamepad'):return False
            self.clear();self.control_mode=mode
            self.set_hint()
            if mode=='gamepad':self.pause_gamepad(True)
            elif self.child:self.child.pause(not self.active);self.gamepad_paused=None
            if mode!='gamepad' and self.reader:self.reader.close();self.reader=None
            emit('control_mode_changed',control_mode=mode);return False

        def operation(self,op):
            if op=='shutdown':
                if self.stopped:return False
                self.stopped=True;self.stop.set();self.active=False
                if self.resize_timer:GLib.source_remove(self.resize_timer);self.resize_timer=0
                self.clear()
                if self.reader:self.reader.close();self.reader=None
                if self.child:self.child.close()
                if Gtk.main_level():Gtk.main_quit()
                return False
            if self.stopped or op not in ('pause','resume'):return False
            self.active=op=='resume';self.clear()
            if self.control_mode=='gamepad':self.pause_gamepad(True)
            elif self.child:self.child.pause(not self.active)
            emit('resumed' if self.active else 'paused');return False

        def execute(self,action,done):
            if action in ('pause','continue'):done(True);return
            if action in ('start','restart'):
                # PAL progress is persisted by PAL itself, never by game reset.
                if not self.child:done(False,'pal_unavailable');return
                self.child.close();self.child.start(False);self.clear()
                self.gamepad_paused=True if self.control_mode=='gamepad' else None
                done(True);return
            if action in ('audio_mute','audio_music_off','audio_effects_off','audio_low'):
                if self.child:self.child.mute(True)
                done(True,audio_muted=True);return
            if action in ('audio_unmute','audio_music_on','audio_effects_on','audio_normal','audio_retry'):
                if self.child:self.child.mute(False)
                done(True,audio_muted=False);return
            done(False,'unsupported_action')

        def pause_gamepad(self,paused):
            if self.child and self.gamepad_paused!=paused:self.child.pause(paused)
            self.gamepad_paused=paused

        def tick(self):
            if self.stopped:return False
            now=time.monotonic();child=self.child
            if not child or not child.process:return True
            rc=child.process.poll()
            if rc is not None:
                emit('pal_exited',returncode=rc);self.operation('shutdown');return False
            expected_paused=not self.active or (self.control_mode=='gamepad' and self.gamepad_paused)
            if not self.ready and now-child.started>.35 and child.word(1)==int(bool(expected_paused)):
                self.ready=True;emit('ready',control_mode=self.control_mode,native_sdl=True)
            if not self.active or now<self.entry_until:return True
            if self.control_mode=='gamepad':
                if not self.reader:
                    from gamepad.device import GamepadReader
                    self.reader=GamepadReader()
                keys=self.pad.update(self.reader.snapshot(),now)
                self.pause_gamepad(not keys['valid'])
                self.set_hint('' if keys['valid'] else
                    '手柄未连接 · 本局已暂停，连接后松开按键和摇杆' if keys.get('reason')=='controller_disconnected' else
                    '手柄已连接 · 松开按键和摇杆后继续' if keys.get('reason')=='controller_release_required' else
                    '手柄数据待恢复 · 本局已暂停')
            elif args.offline_preview:
                keys=dict(valid=False,held=set(),pulses=set())
            else:
                with self.lock:packet=self.latest
                keys=self.body.update(packet,now)
                if isinstance(packet,dict) and ((packet.get('suite') or {}).get('menu') or (packet.get('suite') or {}).get('control_active')):
                    keys=dict(valid=False,held=set(),pulses=set());self.body.reset()
            child.keys.update(keys['held'] if keys['valid'] else set(),keys['pulses'] if keys['valid'] else set(),now)
            return True

    panel=Panel();win=Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='仙剑奇侠传')
    win.set_default_size(960,540);win.add(panel);win.connect('destroy',lambda *_:panel.operation('shutdown'))
    def commands():
        try:
            for line in sys.stdin:
                try:request=json.loads(line)
                except ValueError:continue
                if not isinstance(request,dict):continue
                if request.get('op')=='action':GLib.idle_add(panel.dispatcher.dispatch,request)
                elif request.get('op') in ('pause','resume','shutdown'):GLib.idle_add(panel.operation,request['op'])
                elif request.get('op')=='control_mode':GLib.idle_add(panel.mode,request.get('mode'))
        except OSError:pass
        GLib.idle_add(panel.operation,'shutdown')
    if args.embed_socket:threading.Thread(target=commands,name='pal-host-commands',daemon=True).start()
    signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(panel.operation,'shutdown'))
    signal.signal(signal.SIGINT,lambda *_:GLib.idle_add(panel.operation,'shutdown'))
    win.show_all()
    if args.fullscreen and not args.embed_socket:win.fullscreen()
    try:Gtk.main()
    finally:panel.operation('shutdown')
    return 0

if __name__=='__main__':raise SystemExit(main())
