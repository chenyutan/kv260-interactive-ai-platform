"""Six real native pages under Xvfb, fake backend and synthetic images only."""
import ctypes,json,os,threading,time,traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import gi
gi.require_version('Gtk','3.0')
gi.require_version('GdkX11','3.0')
from gi.repository import Gtk,Gdk,GdkX11,GLib
from main import Suite
from suite_control import SuiteControl

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'test-evidence';OUT.mkdir(exist_ok=True)
state={'pid':999999,'sequence':0,'timestamp_seconds':0,'points17':None,
       'suite':{'active':True,'mode':'ink','menu':True,'control_active':False,'open_serial':0,'hand':None}}
requests=[]
unavailable=False


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        requests.append(self.path)
        if unavailable:self.send_error(503);return
        if self.path!='/api/state':self.send_error(404);return
        state['sequence']+=1;state['timestamp_seconds']=time.monotonic()
        data=json.dumps(state).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.path=='/api/command' and data.pop('command')=='suite'
        # Use the real validator so an unknown mode cannot pass mock GTK tests.
        SuiteControl().configure(data)
        state['suite'].update(data)
        result=b'{"accepted":true}';self.send_response(200);self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result)

server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
args=SimpleNamespace(base='http://127.0.0.1:'+str(server.server_port),fullscreen=False,offline_preview=True,
    ink_source='/home/ubuntu/bodyink-live/native_display.py',fight_source='/home/ubuntu/ink-fight/native/main_v2.py',fruit_source='/home/ubuntu/fruit-slice/native/main.py')
events=[];commands=[];candidate_restarts=[];lifecycles=[];captures=[]


class TestedSuite(Suite):
    def child_event(self,child,mode,generation,event):
        events.append(dict(event=event,time=time.monotonic(),mode=mode,pid=child.pid,generation=generation))
        return super().child_event(child,mode,generation,event)
    def send_child(self,op):
        # Record the host decision at its source, not only delayed child stdout.
        # A readiness pause after a costly screenshot is legitimate and must
        # not be mistaken for a pause caused by a circle candidate.
        child=self.child;ready=self.interaction_ready()
        effective='pause' if op=='resume' and not ready else op
        available=child is not None and child.poll() is None
        send=bool(available and (effective not in ('pause','resume') or
                  self.child_paused!=(effective=='pause')))
        latest_suite=(self.latest or {}).get('suite') or {}
        commands.append(dict(time=time.monotonic(),requested=op,effective=effective,
            sent=send,mode=self.mode,pid=child.pid if child else None,generation=self.generation,
            interaction_ready=ready,pipeline_ready=self.pipeline.ready,pipeline_reason=self.pipeline.reason,
            backend_ready=self.backend_ready,menu_open=self.menu_open,control_active=latest_suite.get('control_active'),
            child_paused_before=self.child_paused))
        return super().send_child(op)

app=TestedSuite(args);app.resize(1024,600)
# Simulate a stale diagnostic environment after this host's GTK initialized.
# Each actual child must still receive a real backing surface.
os.environ['GDK_RENDERING']='recording'
sequence=['ink','sword','fight','fruit','taiko'];records=[];errors=[];stage='disconnect';current=None;due=time.monotonic()+.7;deadline=due+240;old_pids=[]
saved_fight_source=args.fight_source
recovery_deadline=0.
candidate_pause_events=0
candidate_command_start=0
candidate_retries=0
candidate_stable_since=None
pause_lifecycle=None


def baseline_resume_ack(mode,last,resumed,paused,command_history):
    """Observe child state, including Sword's intentional command coalescing.

    Sword emits acknowledgements only on actual active-state changes. A short
    readiness pause followed by resume can collapse to a no-op resume before
    its GTK callback runs, so there will be no newer event. Never accept an old
    resumed event after an observed paused ACK or after a real menu pause.
    """
    if not last or last['effective']!='resume' or not resumed:return False
    previous=resumed[-1]['time']
    if paused and paused[-1]['time']>previous:return False
    if previous>=last['time']:return True
    if mode!='sword':return False
    transitions=[c for c in command_history if c.get('pid')==last['pid'] and
                 c.get('generation')==last['generation'] and c.get('sent') and
                 c['effective'] in ('pause','resume') and previous<c['time']<=last['time']]
    pauses=[c for c in transitions if c['effective']=='pause']
    return bool(pauses and transitions[-1]['effective']=='resume' and
                all(not c['interaction_ready'] and not c['menu_open'] for c in pauses))


def check_resume_ack_contract():
    """Regression: a coalesced no-op is allowed; a still-paused child is not."""
    old=[dict(time=1.)]
    pause=dict(time=2.,effective='pause',pid=7,generation=1,sent=True,
               interaction_ready=False,menu_open=False)
    resume=dict(pause,time=2.15,effective='resume',interaction_ready=True)
    assert baseline_resume_ack('sword',resume,old,[],[pause,resume])
    assert not baseline_resume_ack('sword',resume,old,[dict(time=2.1)],[pause,resume])
    assert not baseline_resume_ack('sword',resume,old,[],[dict(pause,menu_open=True),resume])
    assert not baseline_resume_ack('sword',resume,old,[],[dict(pause,interaction_ready=True),resume])
    assert not baseline_resume_ack('taiko',resume,old,[],[pause,resume])
    assert not baseline_resume_ack('sword',resume,old,[],[resume])
    assert baseline_resume_ack('sword',resume,[dict(time=2.2)],[dict(time=2.1)],[pause,resume])


check_resume_ack_contract()


def transition_ack(kind,pid,generation,command_history,event_history):
    """Require a causal ACK for this child's last state-transition command."""
    op='pause' if kind=='paused' else 'resume'
    sent=[c for c in command_history if c.get('pid')==pid and
          c.get('generation')==generation and c.get('sent') and c['effective'] in ('pause','resume')]
    if not sent or sent[-1]['effective']!=op:return None
    command=sent[-1]
    matching=[e for e in event_history if e.get('pid')==pid and e.get('generation')==generation and
              e['event'].get('event')==kind and e['time']>=command['time']]
    return dict(command=command,ack=matching[-1]) if matching else None


def check_transition_ack_contract():
    pause=dict(time=2.,effective='pause',pid=7,generation=3,sent=True)
    resume=dict(pause,time=3.,effective='resume')
    ack=lambda kind,when,pid=7,generation=3:dict(time=when,pid=pid,generation=generation,event={'event':kind})
    assert transition_ack('paused',7,3,[pause],[ack('paused',2.1)])
    assert not transition_ack('paused',7,3,[pause],[ack('paused',1.)])
    assert not transition_ack('paused',7,3,[pause],[ack('paused',2.1,pid=8)])
    assert not transition_ack('paused',7,3,[pause],[ack('paused',2.1,generation=2)])
    assert not transition_ack('resumed',7,3,[pause,resume],[ack('resumed',1.)])
    assert transition_ack('resumed',7,3,[pause,resume],[ack('resumed',3.1)])
    assert not transition_ack('paused',7,3,[pause,resume],[ack('paused',2.1)])


check_transition_ack_contract()


def begin_pause_lifecycle():
    global stage,due,recovery_deadline,pause_lifecycle
    pause_lifecycle=None
    app.open_menu();stage='paused';due=time.monotonic()+.05;recovery_deadline=due+8.


def current_events(kind):
    return [e for e in events if e['mode']==current and app.child is not None and
            e.get('pid')==app.child.pid and e['event'].get('event')==kind]


def check_candidate_window(now):
    """Fail real candidate pause; bounded retry if service readiness changed."""
    global stage,due,recovery_deadline,candidate_retries,candidate_stable_since
    window=commands[candidate_command_start:]
    wrong=[c for c in window if c['mode'] in ('sword','taiko','fruit') and c['sent'] and c['effective']=='pause'
           and c['interaction_ready'] and not c['menu_open']]
    assert not wrong,('Sword candidate incorrectly sent host pause',wrong)
    interrupted=not app.interaction_ready() or any(not c['interaction_ready'] for c in window)
    if not interrupted:return False
    candidate_retries+=1
    candidate_restarts.append(dict(mode=current,stage=stage,attempt=candidate_retries,
        time=now,pipeline_reason=app.pipeline.reason,commands=window))
    assert candidate_retries<=2,('Candidate test never obtained a stable live stream',candidate_restarts)
    state['suite']['control_active']=False
    stage='candidate_ready';candidate_stable_since=None;due=now+.1;recovery_deadline=now+8
    return True


class XImagePrefix(ctypes.Structure):
    # Xlib XImage header through the native visual masks. The function table
    # follows these fields; XDestroyImage owns the entire Xlib allocation.
    _fields_=[('width',ctypes.c_int),('height',ctypes.c_int),('xoffset',ctypes.c_int),
        ('format',ctypes.c_int),('data',ctypes.c_void_p),('byte_order',ctypes.c_int),
        ('bitmap_unit',ctypes.c_int),('bitmap_bit_order',ctypes.c_int),('bitmap_pad',ctypes.c_int),
        ('depth',ctypes.c_int),('bytes_per_line',ctypes.c_int),('bits_per_pixel',ctypes.c_int),
        ('red_mask',ctypes.c_ulong),('green_mask',ctypes.c_ulong),('blue_mask',ctypes.c_ulong)]


def ximage_rgb(info,raw):
    """Detached RGB bytes, respecting the X server's masks, order and stride."""
    import numpy as np
    width,height=info.width,info.height;bits=info.bits_per_pixel;stride=info.bytes_per_line
    if (width<=0 or height<=0 or bits not in (16,24,32) or
            stride<width*(bits//8) or len(raw)!=stride*height):
        raise ValueError('Unsupported/truncated XImage capture')
    rows=np.frombuffer(raw,dtype=np.uint8).reshape(height,stride)
    if bits==24:
        b=rows[:,:width*3].reshape(height,width,3).astype(np.uint32)
        pixels=(b[:,:,0]|(b[:,:,1]<<8)|(b[:,:,2]<<16) if info.byte_order==0 else
                (b[:,:,0]<<16)|(b[:,:,1]<<8)|b[:,:,2])
    else:
        dtype=np.dtype(('<' if info.byte_order==0 else '>')+'u'+str(bits//8))
        pixels=rows[:,:width*(bits//8)].copy().view(dtype).reshape(height,width).astype(np.uint32)
    masks=(info.red_mask,info.green_mask,info.blue_mask)
    if any(not m or m>=1<<bits for m in masks) or any(masks[i]&masks[j] for i,j in ((0,1),(0,2),(1,2))):
        raise ValueError('Unsupported XImage visual masks')
    channels=[]
    for mask in masks:
        shift=(mask & -mask).bit_length()-1;maximum=mask>>shift
        if maximum & (maximum+1):raise ValueError('Noncontiguous XImage mask')
        channels.append((((pixels&mask)>>shift)*255//maximum).astype(np.uint8))
    return np.stack(channels,axis=-1).tobytes()


def check_ximage_rgb_contract():
    info=SimpleNamespace(width=3,height=1,bits_per_pixel=16,bytes_per_line=8,byte_order=0,
        red_mask=0xf800,green_mask=0x07e0,blue_mask=0x001f)
    assert ximage_rgb(info,b'\x00\xf8\xe0\x07\x1f\x00\xee\xee')==bytes((255,0,0,0,255,0,0,0,255))
    info=SimpleNamespace(width=2,height=1,bits_per_pixel=32,bytes_per_line=8,byte_order=1,
        red_mask=0xff0000,green_mask=0xff00,blue_mask=0xff)
    assert ximage_rgb(info,b'\x00\xff\x00\x00\x00\x00\xff\x00')==bytes((255,0,0,0,255,0))
    info.bits_per_pixel=24;info.bytes_per_line=8
    assert ximage_rgb(info,b'\xff\x00\x00\x00\xff\x00\xee\xee')==bytes((255,0,0,0,255,0))


check_ximage_rgb_contract()


def capture_x11_window(window):
    """Read only the visible X11 drawable, including same-depth XEmbed children.

    Cairo 1.16/GDK's pixbuf_get_from_window can mark an image carrying MIME
    data dirty and abort the process. A real SIGABRT core identified that exact
    screenshot-only path. XGetImage avoids touching Cairo's cached surfaces;
    raw pixels are copied before releasing their Xlib storage.
    """
    library=ctypes.CDLL('libX11.so.6')
    library.XOpenDisplay.argtypes=[ctypes.c_char_p];library.XOpenDisplay.restype=ctypes.c_void_p
    library.XCloseDisplay.argtypes=[ctypes.c_void_p];library.XCloseDisplay.restype=ctypes.c_int
    library.XGetImage.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_int,
        ctypes.c_uint,ctypes.c_uint,ctypes.c_ulong,ctypes.c_int]
    library.XGetImage.restype=ctypes.POINTER(XImagePrefix)
    library.XDestroyImage.argtypes=[ctypes.POINTER(XImagePrefix)];library.XDestroyImage.restype=ctypes.c_int
    display=library.XOpenDisplay(None)
    if not display:raise RuntimeError('Private X11 capture display unavailable')
    image=None
    try:
        # Clip in window coordinates: XGetImage rejects off-screen extents.
        root_x,root_y=window.get_origin()[-2:];screen=window.get_screen()
        left,top=max(0,-root_x),max(0,-root_y)
        right=min(window.get_width(),screen.get_width()-root_x)
        bottom=min(window.get_height(),screen.get_height()-root_y)
        width,height=right-left,bottom-top
        if width<=0 or height<=0:raise RuntimeError('Window is outside the private display')
        image=library.XGetImage(display,window.get_xid(),left,top,width,height,
            ctypes.c_ulong(-1).value,2) # AllPlanes, ZPixmap
        if not image:raise RuntimeError('XGetImage did not return private window pixels')
        info=image.contents
        if (info.width,info.height)!=(width,height) or info.depth!=window.get_visual().get_depth():
            raise RuntimeError('XImage dimensions/depth do not match the host window')
        raw=ctypes.string_at(info.data,info.bytes_per_line*info.height)
        return width,height,ximage_rgb(info,raw),dict(clip=[left,top,width,height],depth=info.depth,
            bits_per_pixel=info.bits_per_pixel,byte_order=info.byte_order,
            masks=[info.red_mask,info.green_mask,info.blue_mask],backend='XGetImage')
    finally:
        if image:library.XDestroyImage(image)
        library.XCloseDisplay(display)


def screenshot(name):
    from PIL import Image
    w,h=app.get_size();assert w<=1024 and h<=600,(name,w,h)
    app.hide_mouse_toolbar();window=app.get_window()
    plug=app.socket.get_plug_window() if app.socket else None
    if plug is not None:
        assert plug.get_visual().get_depth()==window.get_visual().get_depth(),'Mixed-depth XEmbed capture is undefined'
    window.get_display().sync()
    width,height,pixels,metadata=capture_x11_window(window)
    Image.frombytes('RGB',(width,height),pixels).save(OUT/(name+'.png'))
    captures.append(dict(name=name,window_size=[w,h],**metadata))
    return w,h


def tick():
    global stage,current,due,unavailable,recovery_deadline,candidate_pause_events
    global candidate_command_start,candidate_retries,candidate_stable_since
    global pause_lifecycle
    try:
        now=time.monotonic()
        if now>deadline:raise TimeoutError('UI test deadline; '+stage)
        if now<due:return True
        if stage=='disconnect':
            # This fixture tests sustained service loss, not the permitted
            # short HTTP miss. Stay beyond the gate's 0.75-second transport
            # grace with margin before asserting that selection was revoked.
            unavailable=True;stage='reconnect'
            due=now+max(1.,app.pipeline.transport_grace_seconds+.25)
        elif stage=='reconnect':
            assert app.menu_open and app.selector.opened is None,'Disconnect retained an armed selector'
            assert not app.pipeline.ready and not app.menu.selection_enabled,'Disconnect left menu selection enabled'
            unavailable=False;stage='menu';due=now+.5
        elif stage=='menu':
            if not app.interaction_ready() or app.resyncing:return True
            screenshot('menu');current=sequence.pop(0);app.select(current);stage='opening';due=now+.2
        elif stage=='opening':
            if app.switching:return True
            assert app.mode==current and app.child and app.child.poll() is None,(current,app.loading_label.get_text())
            assert app.socket.get_plug_window() is not None,'Child failed XEmbed'
            stage='active';due=now+1.2
        elif stage=='active':
            size=screenshot(current);old_pids.append(app.child.pid)
            env=dict(item.split(b'=',1) for item in Path('/proc',str(app.child.pid),'environ').read_bytes().split(b'\0') if b'=' in item)
            assert env.get(b'GDK_RENDERING')==b'similar',(current,'unbuffered child rendering')
            records.append(dict(mode=current,pid=app.child.pid,embedded=True,size=size,rendering='similar'))
            # Screenshot copying can block this GTK loop long enough to force
            # the real readiness gate to reconfirm its live observations.
            # Establish a stable resumed ACK before attributing later pauses.
            state['suite']['control_active']=False
            candidate_retries=0;candidate_stable_since=None
            stage='candidate_ready';due=time.monotonic()+.1;recovery_deadline=due+8
        elif stage=='candidate_ready':
            resumed=current_events('resumed')
            last_commands=[c for c in commands if app.child is not None and c['pid']==app.child.pid
                           and c['sent'] and c['effective'] in ('pause','resume')]
            last=last_commands[-1] if last_commands else None
            acknowledged=baseline_resume_ack(current,last,resumed,current_events('paused'),commands)
            latest_suite=(app.latest or {}).get('suite') or {}
            stable=(app.interaction_ready() and not app.child_paused and not app.menu_open and
                    not app.resyncing and acknowledged and not latest_suite.get('control_active'))
            if not stable:candidate_stable_since=None
            elif candidate_stable_since is None:candidate_stable_since=now
            if candidate_stable_since is None or now-candidate_stable_since<.35:
                assert now<recovery_deadline,('Candidate baseline not ready',current,last,resumed,app.pipeline.reason)
                return True
            candidate_pause_events=len(current_events('paused'))
            candidate_command_start=len(commands)
            state['suite']['control_active']=True;stage='candidate';due=now+.4;recovery_deadline=due+4
        elif stage=='candidate':
            if check_candidate_window(now):return True
            if (app.child_paused!=(current=='fight') or '正在识别画圈' not in app.status.get_text()) and now<recovery_deadline:return True
            assert app.child_paused==(current=='fight'),(current,app.child_paused)
            if current in ('sword','taiko','fruit'):assert len(current_events('paused'))==candidate_pause_events,'Native game stable candidate incorrectly sent host pause'
            assert '正在识别画圈' in app.status.get_text(),'Candidate was not delivered'
            state['suite']['control_active']=False;stage='candidate_clear';due=now+.4;recovery_deadline=due+4
        elif stage=='candidate_clear':
            if check_candidate_window(now):return True
            if (app.child_paused or '正在识别画圈' in app.status.get_text()) and now<recovery_deadline:return True
            assert not app.child_paused,(current,'Circle candidate did not resume')
            assert '正在识别画圈' not in app.status.get_text(),'Candidate clear was not delivered'
            if current in ('sword','taiko','fruit'):assert len(current_events('paused'))==candidate_pause_events,'Native game candidate clear rearmed host recovery'
            if current in ('sword','fight','taiko'):
                state['pid']+=1;state['suite'].update(active=False,mode='ink',menu=False)
                stage='backend_restart';due=now+.2;recovery_deadline=now+6
            else:begin_pause_lifecycle()
        elif stage=='backend_restart':
            if (not state['suite']['active'] or state['suite']['mode']!=current or not app.backend_ready) and now<recovery_deadline:return True
            assert state['suite']['active'] and state['suite']['mode']==current,state['suite']
            assert app.backend_ready and app.child.pid==old_pids[-1],'Backend restart lost active game'
            begin_pause_lifecycle()
        elif stage=='paused':
            assert app.menu_open and app.stack.get_visible_child_name()=='actions'
            assert app.actions.timer is not None,'Visible action menu must animate'
            pause_lifecycle=transition_ack('paused',app.child.pid,app.generation,commands,events)
            if pause_lifecycle is None:
                assert now<recovery_deadline,('Current child never acknowledged menu pause',current,app.child.pid,commands[-12:],current_events('paused'))
                return True
            # Keep the menu open until this specific child ACKs its actual
            # pause. A slow native draw may delay commands; a fixed 0.5-second
            # wait or any historical mode ACK proves neither pause nor clock.
            app.cancel_menu();stage='resumed';due=time.monotonic()+.05;recovery_deadline=due+8.
        elif stage=='resumed':
            resumed_lifecycle=transition_ack('resumed',app.child.pid,app.generation,commands,events)
            host_resumed=(not app.menu_open and app.stack.get_visible_child_name()=='experience' and
                          not app.resyncing and app.backend_ready and not app.child_paused)
            if not host_resumed or resumed_lifecycle is None:
                assert now<recovery_deadline,('Menu/backend/current child resume not completed',current,app.child.pid,
                    host_resumed,commands[-12:],current_events('resumed'))
                return True
            assert app.menu.timer is None,'Hidden menu kept its frame timer'
            assert app.actions.timer is None,'Hidden action menu kept its frame timer'
            if current in ('sword','fruit','fight','taiko'):
                paused=pause_lifecycle['ack']['event'];resumed=resumed_lifecycle['ack']['event']
                allowed=(('paused','playing','finished') if current=='taiko' else
                         ('ready','playing','countdown','over','won','lost','transforming') if current in ('sword')
                         else ('playing','countdown'))
                assert resumed['game_state'] in allowed,resumed
                if current in ('sword','taiko'):assert resumed['game_state']==paused['game_state'],(paused,resumed)
                assert abs(resumed['game_clock']-paused['game_clock'])<.05,(paused,resumed)
            lifecycles.append(dict(mode=current,pid=app.child.pid,generation=app.generation,
                                   paused=pause_lifecycle,resumed=resumed_lifecycle))
            # EOF must close the child and return to menu without killing host.
            app.child.stdin.close();stage='eof';due=now+.5
        elif stage=='eof':
            if app.child is not None:return True
            assert app.menu_open
            # Returning from EOF now confirms menu ownership before enabling
            # selection, just as a user must wait for the ready cards.
            if not app.interaction_ready() or app.resyncing:return True
            if sequence:
                current=sequence.pop(0);app.select(current);stage='opening';due=now+.2
            else:
                args.fight_source='/home/ubuntu/ink-suite-staging/missing-game-for-error-test.py'
                app.select('fight');stage='failed_start';due=now+.2
        elif stage=='failed_start':
            if app.switching:return True
            assert app.stack.get_visible_child_name()=='actions' and app.action_page=='error','Missing child failed without gesture retry page'
            assert app.menu_open and app.child is None
            stage='retry_ready';due=now+.1;recovery_deadline=now+8
        elif stage=='retry_ready':
            # Failed mode launch restores the old backend mode asynchronously.
            # select() deliberately ignores input during that readiness gap.
            if not app.interaction_ready() or app.resyncing:
                assert now<recovery_deadline,'Failed launch never restored readiness'
                return True
            os.environ['GDK_RENDERING']='image'
            args.fight_source=saved_fight_source;app.select('fight');stage='retry';due=now+.3
            assert app.switching,'Ready retry request was not accepted'
        elif stage=='retry':
            if app.switching:return True
            assert app.child and app.child.poll() is None and app.mode=='fight'
            env=dict(item.split(b'=',1) for item in Path('/proc',str(app.child.pid),'environ').read_bytes().split(b'\0') if b'=' in item)
            assert env.get(b'GDK_RENDERING')==b'image','Buffered image fallback was lost'
            screenshot('retry-fight');old_pids.append(app.child.pid)
            stage='finished';due=now+.3
        elif stage=='finished':
                assert not any('preview' in x or 'environment' in x for x in requests),requests
                (OUT/'gtk-suite-result.json').write_text(json.dumps(dict(passed=True,camera_used=False,records=records,events=events,commands=commands,candidate_restarts=candidate_restarts,lifecycles=lifecycles,captures=captures,request_paths=sorted(set(requests)),eof_exits=len(records),disconnect_reconnect=True,backend_restart_resync=True,failed_start_retry=True,circle_candidate_host_pauses_fight_only=True,native_game_candidate_never_sends_host_pause=True,hidden_menu_timer_stops=True),indent=2))
                app.destroy();return False
        return True
    except Exception:
        errors.append(traceback.format_exc());(OUT/'gtk-suite-error.txt').write_text(errors[-1]);
        (OUT/'gtk-suite-error-context.json').write_text(json.dumps(dict(stage=stage,current=current,events=events,commands=commands,candidate_restarts=candidate_restarts,records=records,backend_pid=app.backend_pid,backend_ready=app.backend_ready,pipeline_reason=app.pipeline.reason,resyncing=app.resyncing,latest=app.latest,pending_state=app.pending_state,requests=len(requests)),indent=2));app.destroy();return False

GLib.timeout_add(100,tick);Gtk.main();server.shutdown()
if errors:raise RuntimeError(errors[-1])
assert state['suite']['active'] is False,'Exit failed to restore backend'
assert all(not Path('/proc',str(pid)).exists() for pid in old_pids),'Orphan child after exit'
print(json.dumps(dict(passed=True,camera_used=False,modes=[r['mode'] for r in records],eof_exits=len(records))))
