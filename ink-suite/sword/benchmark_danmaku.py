#!/usr/bin/env python3
"""Bounded, camera-free benchmark of the actual GTK SwordPanel and game.

Default display is a newly created private Xvfb, never the user's DISPLAY.
Examples, from the InkSuite directory on KV260 (no deployment is performed):
  python3 -m sword.benchmark_danmaku --scene stress --seconds 30 --output /tmp/stress.json
  python3 -m sword.benchmark_danmaku --scene stress --stress-collisions --seconds 30 --output /tmp/collision-stress.json
  python3 -m sword.benchmark_danmaku --scene level --seconds 85 --output /tmp/level.json
Only after the operator has coordinated use of the physical screen:
  python3 -m sword.benchmark_danmaku --physical-display :0 --fullscreen \
      --scene stress --seconds 30 --output /tmp/stress-panel.json

The level scene preserves production rules and uses deterministic synthetic
poses. A short run or early death is explicitly NOT a full-level validation.
Stress is an artificial near-budget population, NOT attainable gameplay. Both
run the real poll thread, input tracker, game update and complete GTK renderer.
No camera, production API, screenshots or physical scanout measurements.
"""
import argparse
from collections import Counter, deque
from contextlib import contextmanager
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import select
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request
from unittest.mock import patch

from .benchmark_panel import FakePoseServer, PoseSource, quantiles as _quantiles, synthetic_pose


SAMPLE_LIMIT = 75000  # At most 300 seconds, bounded even if timer behavior breaks.


# This host must be a separate process: a same-process Socket/Plug pair takes
# GTK's same_app child-widget shortcut instead of production's foreign XEmbed.
EMBED_HOST = r'''
import json,os,sys,threading
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,GLib
ok=Gtk.init_check([])
if not (ok[0] if isinstance(ok,tuple) else ok):
    raise RuntimeError('Private XEmbed host could not initialize selected X11 display')
width,height,fullscreen,lifetime=int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3]),float(sys.argv[4])
window=Gtk.Window(title='御剑弹幕验证宿主 · 合成输入')
window.set_default_size(width,height)
window.set_size_request(640,480)
box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);window.add(box)
# Match Suite.__init__ geometry without running its camera/API/menu services.
bar=Gtk.Box(spacing=10);bar.set_border_width(7);box.pack_start(bar,False,False,0)
bar.pack_start(Gtk.Button(label='☰  四境菜单'),False,False,0)
label=Gtk.Label(label='御剑弹幕验证 · 合成输入');label.set_xalign(0);label.set_ellipsize(3)
bar.pack_start(label,True,True,0)
bar.pack_start(Gtk.Button(label='全屏'),False,False,0)
bar.pack_start(Gtk.Button(label='退出'),False,False,0)
stack=Gtk.Stack();stack.set_transition_type(Gtk.StackTransitionType.NONE);box.pack_start(stack,True,True,0)
socket=Gtk.Socket();socket.set_hexpand(True);socket.set_vexpand(True)
socket.connect('plug-removed',lambda *_:True)
stack.add_named(socket,'experience');stack.set_visible_child_name('experience')
window.connect('destroy',lambda *_:Gtk.main_quit())
window.show_all()
if fullscreen:window.fullscreen()
def ready():
    if not socket.get_realized() or not socket.get_mapped():return True
    print(json.dumps(dict(socket_id=socket.get_id(),host_pid=os.getpid(),
        host_window=list(window.get_size()),socket_allocation=[socket.get_allocated_width(),socket.get_allocated_height()])),flush=True)
    return False
def close():
    window.destroy()
    return False
def commands():
    for line in sys.stdin:
        if line.strip()=='quit':break
    GLib.idle_add(close,priority=GLib.PRIORITY_DEFAULT)
threading.Thread(target=commands,daemon=True).start()
GLib.timeout_add(50,ready)
GLib.timeout_add(int(lifetime*1000),close)
Gtk.main()
'''


def read_host_ready(read_fd, timeout=10.):
    """Bounded private stdout protocol; never reads a production process pipe."""
    deadline,data=time.monotonic()+timeout,bytearray()
    while b'\n' not in data:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([read_fd],[],[],remaining)[0]:
            raise RuntimeError('Private XEmbed host readiness timed out')
        chunk=os.read(read_fd,4096-len(data))
        if not chunk:
            raise RuntimeError('Private XEmbed host exited before readiness')
        data.extend(chunk)
        if len(data)>=4096 and b'\n' not in data:
            raise RuntimeError('Private XEmbed host readiness exceeded 4096 bytes')
    info=json.loads(bytes(data).split(b'\n',1)[0].decode('utf-8'))
    if not all(isinstance(info.get(k),int) and not isinstance(info[k],bool) and info[k]>0
               for k in ('socket_id','host_pid')):
        raise RuntimeError('Private XEmbed host returned invalid identifiers')
    return info


class NativeEmbedHost:
    def __init__(self,args):
        self.process=self.log=None
        self.info=None
        self.log_path=args.output.with_suffix('.embed-host.log')
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        try:
            self.log=self.log_path.open('wb')
            self.process=subprocess.Popen([sys.executable,'-u','-c',EMBED_HOST,
                str(args.width),str(args.height),str(int(args.fullscreen)),
                str(args.seconds+args.warmup+50)],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=self.log,bufsize=0)
            self.info=read_host_ready(self.process.stdout.fileno())
            if self.info['host_pid']!=self.process.pid:
                raise RuntimeError('Private XEmbed host PID did not match launched process')
        except Exception as error:
            self.close()
            raise RuntimeError('%s; private host stderr saved to %s'%(error,self.log_path)) from error

    def close(self):
        process=self.process
        if process is not None:
            if process.poll() is None:
                try:process.stdin.write(b'quit\n');process.stdin.flush()
                except (OSError,ValueError):pass
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait(timeout=3)
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()
            self.process=None
        if self.log is not None:
            self.log.close();self.log=None


def embed_is_ready(window,panel):
    return bool(window.get_embedded() and window.get_mapped() and panel.canvas.get_mapped()
                and panel.canvas.get_allocated_width()>1 and panel.canvas.get_allocated_height()>1)


def diagnostic_embedded_frame_sync(window, requested=False, *, x11=None):
    """A/B only: native Plug is not directly managed by the window manager.

    GTK's documented XEmbed exception:
    https://docs.gtk.org/gdk3-x11/method.X11Window.set_frame_sync_enabled.html
    The default does not load GdkX11, query the window, or change any setting.
    """
    report = dict(requested=bool(requested), applied=False,
                  scope='Diagnostic foreign Gtk.Plug only; production main unchanged')
    if not requested:
        return report
    native = window.get_window() if window.get_realized() else None
    if native is None:
        raise RuntimeError('Frame-sync diagnostic requires a realized Gtk.Plug native window')
    if x11 is None:
        import gi
        gi.require_version('GdkX11', '3.0')
        from gi.repository import GdkX11
        x11 = GdkX11
    x11.X11Window.set_frame_sync_enabled(native, False)
    report.update(applied=True, native_xid=x11.X11Window.get_xid(native), frame_sync_enabled=False)
    return report


def finite_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def quantiles(values):
    # Invalid measurements must be reported as invalid, never emitted as NaN
    # percentages/FPS that downstream result readers might accidentally accept.
    values = list(values)
    valid = [v for v in values if finite_number(v)]
    result = _quantiles(valid)
    result['invalid_count'] = len(values)-len(valid)
    return result


def validate_measurement(draws, ticks, logic, started, seconds):
    """Validity gate, independent of an eventual performance/FPS target.

    A completed timer does not prove that GTK ever painted. Require enough
    real draws, every full second covered, and an advancing active game clock.
    Terminal screens legitimately stop the game clock, but still must paint.
    """
    draws,ticks,logic = list(draws),list(ticks),list(logic)
    failures, invalid = [],[]
    for name,rows,fields in (
        ('draw',draws,('at','seconds','cpu_seconds')),
        ('tick',ticks,('at','seconds','interval','game_time')),
        ('logic',logic,('at','seconds','fixture_seconds'))):
        for index,row in enumerate(rows):
            bad = [field for field in fields if not finite_number(row.get(field)) or row[field] < 0]
            if name == 'tick':
                bad.extend('counts.'+key for key,value in row.get('counts',{}).items()
                           if not finite_number(value) or value < 0)
            if bad:
                if len(invalid) < 10:
                    invalid.append(dict(collection=name,index=index,fields=bad))
                failures.append('Non-finite or negative %s timing/object measurement' % name)
                break
    if len(draws) < 5:
        failures.append('Fewer than 5 actual GTK draw callbacks; no valid rendering benchmark')
    if len(ticks) < 5 or len(logic) < 5:
        failures.append('Too few real tick/logic samples to validate active processing')
    if not finite_number(started) or not finite_number(seconds) or seconds < 1:
        failures.append('Missing or invalid measurement window')
        return dict(passed=False,actual_draws=len(draws),invalid_samples=invalid,seconds=[]),failures
    buckets = [dict(second=index,draws=0,ticks=0,active_ticks=0,game_times=[])
               for index in range(int(seconds))]
    for row in draws:
        if finite_number(row.get('at')):
            bucket = int(math.floor(row['at']-started))
            if 0 <= bucket < len(buckets):
                buckets[bucket]['draws'] += 1
    for row in ticks:
        if finite_number(row.get('at')):
            bucket = int(math.floor(row['at']-started))
            if 0 <= bucket < len(buckets):
                buckets[bucket]['ticks'] += 1
                if row.get('phase') == 'playing' and not row.get('pause_reason'):
                    buckets[bucket]['active_ticks'] += 1
                    if finite_number(row.get('game_time')):
                        buckets[bucket]['game_times'].append(row['game_time'])
    starvation = []
    stalled = []
    for bucket in buckets:
        times = bucket.pop('game_times')
        bucket['active_game_time_delta'] = max(times)-min(times) if len(times)>1 else 0.
        if bucket['draws'] == 0 or bucket['ticks'] == 0:
            starvation.append(bucket['second'])
        if bucket['active_ticks'] >= 2 and bucket['active_game_time_delta'] <= 1e-9:
            stalled.append(bucket['second'])
    if starvation:
        failures.append('Rendering/tick starvation in full measurement seconds: '+str(starvation))
    if stalled:
        failures.append('Active game clock did not advance in seconds: '+str(stalled))
    return dict(passed=not failures,actual_draws=len(draws),invalid_samples=invalid,
                seconds=buckets),failures


class BoundedSamples:
    def __init__(self, limit=SAMPLE_LIMIT):
        self.rows = deque(maxlen=limit)
        self.dropped = 0

    def append(self, value):
        if len(self.rows) == self.rows.maxlen:
            self.dropped += 1
        self.rows.append(value)


class DiagnosticFrameClock:
    """Opt-in benchmark scheduling only; baseline retains production behavior."""
    def __init__(self, panel, glib, mode=None):
        self.panel,self.mode = panel,mode
        self.callback_id = None
        self.callbacks = self.draw_requests = self.driven_ticks = 0
        if mode not in (None,'request','drive'):
            raise ValueError('Unknown diagnostic frame clock mode')
        if mode == 'drive' and panel.timer is not None:
            source = glib.MainContext.default().find_source_by_id(panel.timer)
            if source is not None:
                source.destroy()
            panel.timer = None
        if mode is not None:
            self.callback_id = panel.canvas.add_tick_callback(self.dispatch)

    def dispatch(self, widget, frame_clock):
        self.callbacks += 1
        if self.panel.stop.is_set():
            self.callback_id = None
            return False
        if self.panel.active:
            if self.mode == 'request':
                # Input/game timer is untouched; only declare steady animation
                # at the GDK update phase instead of relying on idle requests.
                widget.queue_draw()
                self.draw_requests += 1
            elif self.mode == 'drive':
                self.driven_ticks += 1
                if not self.panel.tick():
                    self.callback_id = None
                    return False
        return True

    def close(self):
        if self.callback_id is not None:
            # GTK already removes callbacks when the widget is destroyed.
            if self.panel.canvas.get_realized():
                self.panel.canvas.remove_tick_callback(self.callback_id)
            self.callback_id = None

    def report(self):
        return dict(enabled=self.mode is not None,mode=self.mode,
                    production_timer_retained=self.mode!='drive',
                    callback_count=self.callbacks,draw_requests=self.draw_requests,
                    frame_clock_driven_ticks=self.driven_ticks,
                    production_code_modified=False)


class DanmakuPoseSource(PoseSource):
    def __init__(self, rate=30., strategy='sweep'):
        super().__init__(rate=rate)
        self.strategy = strategy
        self.observation = (.5,.76,False)
        self.cached_index = None
        self.cached_state = None
        self.target_x = .5
        self.cast_until = -1
        self.last_cast = -1000
        self.cast_requests = 0

    def observe(self, game):
        """One latest tiny view from this test's game, never a camera/API read."""
        boss = next((e for e in game.entities if e['kind']=='boss' and not e['hit']),None)
        drops = [e for e in game.entities if e['kind']=='power' and e['y']>.3 and not e['hit']]
        enemies = [e for e in game.entities if e['kind'] in ('enemy','elite')
                   and .03 < e['y'] < .55 and not e['hit']]
        if boss:
            # Deterministic synthetic aiming lead, preserving all game rules.
            x = boss['x']+.19*.54*math.cos(boss['age']*.54)*.34
        elif drops:
            x = max(drops,key=lambda e:e['y'])['x']
        elif enemies:
            x = min(enemies,key=lambda e:abs(e['x']-game.player['x']))['x']
        else:
            x = .5
        self.observation = (max(.07,min(.93,x)), .76,
                            game.energy >= 1.-1e-8 and game.phase=='playing')

    def state(self, now=None):
        now = time.monotonic() if now is None else now
        index = max(0, int((now-self.origin)*self.rate))
        if index == self.cached_index:
            return self.cached_state
        t = index/self.rate
        # Visible smooth traversals of the lower play field, not teleportation.
        x, y = .5+.31*math.sin(t*.47), .84+.055*math.sin(t*.73)
        if self.strategy == 'level':
            target_x, target_y, can_cast = self.observation
            requested = max(0.,min(1.,(target_x-.07)/.86))
            delta = max(1,index-self.cached_index) if self.cached_index is not None else 1
            step = min(.12,delta*.04)
            self.target_x += max(-step,min(step,requested-self.target_x))
            x,y = self.target_x,(target_y-.22)/.62
            if can_cast and index-self.last_cast > self.rate:
                self.last_cast,self.cast_until = index,index+10
                self.cast_requests += 1
        frame = synthetic_pose(self.origin+index/self.rate, index, x, y)
        if self.strategy == 'level' and index <= self.cast_until:
            # A real neutral -> spread -> neutral pose sequence, including
            # the same temporary right-pointer movement as a human spell.
            blend = min(1.,max(0.,(index-self.last_cast+1)/3.))
            for joint, target in ((9,(.20,.36)),(10,(.80,.36))):
                point = frame['points17'][joint]
                point[0] += (target[0]-point[0])*blend
                point[1] += (target[1]-point[1])*blend
        self.cached_index,self.cached_state = index,frame
        return frame


def read_display_number(read_fd, timeout=10.):
    """Read the entire -displayfd protocol line before closing its reader.

    Xvfb may write the digits and newline separately. Closing after the digits
    makes the server's newline write fail with EPIPE and kills the server.
    """
    deadline = time.monotonic()+timeout
    data = bytearray()
    while b'\n' not in data:
        remaining = deadline-time.monotonic()
        if remaining <= 0 or not select.select([read_fd],[],[],remaining)[0]:
            raise RuntimeError('Private Xvfb did not finish its display-number line within %.1f seconds' % timeout)
        chunk = os.read(read_fd,64-len(data))
        if not chunk:
            raise RuntimeError('Private Xvfb closed -displayfd before its terminating newline')
        data.extend(chunk)
        if len(data) >= 64 and b'\n' not in data:
            raise RuntimeError('Private Xvfb returned an overlong display-number line')
    token = bytes(data).split(b'\n',1)[0].decode('ascii').strip()
    if not token.isdecimal():
        raise RuntimeError('Private Xvfb did not provide a valid display number')
    return ':'+token


@contextmanager
def isolated_display(args):
    """Only an explicit option can put a window on an existing real display."""
    previous = os.environ.get('DISPLAY')
    previous_backend = os.environ.get('GDK_BACKEND')
    process = None
    log = None
    log_path = None
    if args.physical_display:
        display = args.physical_display
        if not display.startswith(':') or '/' in display:
            raise ValueError('--physical-display must be a local X11 display such as :0')
        kind = 'operator-selected-existing-display'
    else:
        executable = shutil.which('Xvfb')
        if not executable:
            raise RuntimeError('Xvfb is required for the default isolated run; no physical-display fallback')
        log_path = args.output.with_suffix('.xvfb.log')
        log_path.parent.mkdir(parents=True,exist_ok=True)
        log = log_path.open('wb')
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen([executable, '-displayfd', str(write_fd), '-screen', '0',
                '%dx%dx%d' % (args.width, args.height, args.depth), '-nolisten', 'tcp', '-noreset'],
                pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=log)
            os.close(write_fd)
            write_fd = None
            display, kind = read_display_number(read_fd), 'private-xvfb'
        except Exception as error:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.close()
            raise RuntimeError('%s; private Xvfb stderr saved to %s' % (error,log_path)) from error
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
    os.environ['DISPLAY'] = display
    # An inherited Wayland session must not bypass the isolated X11 display.
    os.environ['GDK_BACKEND'] = 'x11'
    try:
        yield dict(name=display, kind=kind, requested_depth=args.depth,
                   xvfb_stderr_path=str(log_path) if log_path else None,
                   xvfb_pid=process.pid if process is not None else None,
                   physical_display_requested=bool(args.physical_display))
    except Exception as error:
        if process is None:
            raise
        status = process.poll()
        raise RuntimeError('%s; private Xvfb DISPLAY=%s exit_code=%s; stderr saved to %s'
                           % (error,display,status,log_path)) from error
    finally:
        if previous is None:
            os.environ.pop('DISPLAY', None)
        else:
            os.environ['DISPLAY'] = previous
        if previous_backend is None:
            os.environ.pop('GDK_BACKEND', None)
        else:
            os.environ['GDK_BACKEND'] = previous_backend
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log is not None:
            log.close()


def counts(game):
    entities = getattr(game, 'entities', ())
    return dict(bullets=len(getattr(game, 'bullets', ())),
                shots=len(getattr(game, 'shots', ())), entities=len(entities),
                enemies=sum(e.get('kind') in ('enemy', 'elite', 'boss') and not e.get('hit') for e in entities),
                bosses=sum(e.get('kind') == 'boss' and not e.get('hit') for e in entities),
                effects=len(getattr(game, 'effects', ())))


def summarize(draw_rows, tick_rows, logic_rows):
    draws, ticks, logic = list(draw_rows), list(tick_rows), list(logic_rows)
    frames = [row['at'] for row in draws if finite_number(row.get('at'))]
    elapsed = max(0.,frames[-1]-frames[0]) if len(frames) > 1 else 0.
    intervals = [(b-a)*1000 for a, b in zip(frames, frames[1:])]
    return dict(
        actual_draw_callbacks=len(draws), measured_draw_fps=(len(draws)-1)/elapsed if elapsed else 0.,
        measured_draw_span_seconds=elapsed,
        draw_ms=quantiles([r['seconds']*1000 for r in draws]),
        draw_cpu_ms=quantiles([r['cpu_seconds']*1000 for r in draws]),
        draw_interval_ms=quantiles(intervals),
        tick_callback_ms=quantiles([r['seconds']*1000 for r in ticks]),
        tick_interval_ms=quantiles([r['interval']*1000 for r in ticks]),
        logic_ms=quantiles([r['seconds']*1000 for r in logic]),
        stress_fixture_ms=quantiles([r['fixture_seconds']*1000 for r in logic]),
        peak_objects={key:max((r['counts'][key] for r in ticks if finite_number(r['counts'][key])), default=0)
                      for key in ('bullets','shots','entities','enemies','bosses','effects')},
        object_counts={key:quantiles([r['counts'][key] for r in ticks])
                       for key in ('bullets','shots','entities','enemies','bosses','effects')},
        drawn_object_counts={key:quantiles([r['counts'][key] for r in draws if 'counts' in r])
                             for key in ('bullets','shots','entities','enemies','bosses','effects')},
        phases=dict(Counter(r['phase'] for r in ticks)),
        pause_reason_samples=dict(Counter(r['pause_reason'] for r in ticks)),
        long_draws_over_80ms=sum(r['seconds'] > .08 for r in draws),
        long_ticks_over_80ms=sum(r['interval'] > .08 for r in ticks))


def run(args, display_info):
    # GTK imports happen only after a safe display has been selected.
    module = importlib.import_module('sword.main')
    engine_module = importlib.import_module('sword.engine')
    if not hasattr(engine_module,'MAX_BULLETS'):
        raise RuntimeError('This benchmark requires the danmaku engine, not the old flight game')
    Gtk, GLib = module.Gtk, module.GLib
    initialization = Gtk.init_check([])
    initialized = initialization[0] if isinstance(initialization,tuple) else bool(initialization)
    if not initialized:
        raise RuntimeError('Gtk.init_check failed for selected DISPLAY=%s; no fallback to the physical screen'
                           % display_info['name'])
    if args.scene == 'replay':
        from .replay import ReplayServer
        source = server = ReplayServer(args.recording, args.recording_start)
        if args.seconds > server.description['seconds_available']:
            server.close()
            raise ValueError('Requested replay exceeds remaining recorded duration; EOF is not fresh input')
    else:
        source = DanmakuPoseSource(rate=30.,strategy=args.scene)
        server = FakePoseServer(source)
    draw_rows, tick_rows, logic_rows = BoundedSamples(), BoundedSamples(), BoundedSamples()
    failures, violations, events = [], [], []
    state = dict(started=False, first_play=None, measure_start=None, finish=False, last_tick=None)
    real_game = module.Game
    fixture = StressPopulation(engine_module,collisions=args.stress_collisions) if args.scene == 'stress' else None
    real_open = urllib.request.OpenerDirector.open

    def measured():
        return state['measure_start'] is not None and not state['finish']

    def guarded_open(opener, url, *pos, **kw):
        address = url.full_url if isinstance(url, urllib.request.Request) else str(url)
        if address != server.base+'/api/state':
            violations.append(address)
            raise AssertionError('Benchmark forbids production/external API: '+address)
        return real_open(opener, url, *pos, **kw)

    class MeasuredGame(real_game):
        def _damage(self, entity, amount):
            before = entity.get('health',0.)
            result = super()._damage(entity,amount)
            if fixture is not None and before > entity.get('health',before):
                fixture.damage_events += 1
                fixture.damage_total += before-entity['health']
            return result

        def update(self, dt, control):
            before = time.monotonic()
            if fixture is not None and self.phase == 'playing' and dt > 0:
                fixture.apply(self)
            prepared = time.monotonic()
            result = super().update(dt, control)
            ended = time.monotonic()
            if measured():
                logic_rows.append(dict(at=before, seconds=ended-prepared, fixture_seconds=prepared-before,
                                       phase=self.phase))
            return result

    class MeasuredPanel(module.SwordPanel):
        def draw(self, widget, context):
            before, cpu = time.monotonic(), time.thread_time()
            draw_counts = counts(self.game)
            result = super().draw(widget, context)
            if measured():
                draw_rows.append(dict(at=before, seconds=time.monotonic()-before,
                    cpu_seconds=time.thread_time()-cpu, phase=self.game.phase,counts=draw_counts))
            return result

        def advance(self, now):
            before = time.monotonic()
            if (args.scene == 'replay' and source.origin is None and self.active
                    and now >= self.entry_until):
                source.begin(now)
                state['measure_start'] = now
                events.append(dict(event='recorded_replay_started', at=now,
                                   recording_start=args.recording_start))
            super().advance(now)
            ended = time.monotonic()
            if args.scene == 'level':
                source.observe(self.game)
            if not state['started'] and self.control.get('status') == 'tracking':
                state['started'] = True
                if args.scene == 'replay':
                    # The saved poses are authoritative; don't waste their
                    # first three seconds in a new synthetic countdown. This
                    # starts the unchanged game, not its original saved RNG or
                    # exact live lifecycle. Report that distinction explicitly.
                    self.game.start(countdown=0)
                    self.game.pause(bool(self.pause_reason))
                else:
                    self.restart()
            if self.game.phase == 'playing' and state['first_play'] is None:
                state['first_play'] = now
            if (state['first_play'] is not None and state['measure_start'] is None
                    and now-state['first_play'] >= args.warmup):
                state['measure_start'] = now
                events.append(dict(event='measurement_started', at=now, game_time=self.game.time))
            if measured():
                tick_rows.append(dict(at=now, seconds=ended-before,
                    interval=now-state['last_tick'] if state['last_tick'] is not None else 0.,
                    phase=self.game.phase, game_time=self.game.time,
                    pause_reason=self.pause_reason, counts=counts(self.game)))
            state['last_tick'] = now
            if state['measure_start'] is not None and now-state['measure_start'] >= args.seconds:
                state['finish'] = True
                GLib.idle_add(self.operation, 'shutdown', priority=GLib.PRIORITY_DEFAULT)

    server.start()
    window = panel = scheduler = host = None
    started = time.monotonic()
    actual_size = canvas_size = final_snapshot = performance = cache_metrics = None
    frame_sync = diagnostic_embedded_frame_sync(None)
    timeout_source = mapping_source = None
    embedding = dict(enabled=bool(args.embed),mode='foreign-process-xembed' if args.embed else 'standalone',
        plug_pid=os.getpid() if args.embed else None,host_pid=None,verified_mapped=False,
        host_services_included=False,host_layout='Suite top bar + transition-free Gtk.Stack' if args.embed else None)
    try:
        with patch.object(module, 'Game', MeasuredGame), patch('urllib.request.OpenerDirector.open', guarded_open):
            if args.embed:
                host = NativeEmbedHost(args)
                embedding.update(host.info)
                embedding['host_stderr_path']=str(host.log_path)
                window = Gtk.Plug.new(host.info['socket_id'])
            else:
                window = Gtk.Window(title='御剑弹幕压测 · 合成输入 · 无摄像头')
                window.set_default_size(args.width, args.height)
                window.set_resizable(False)
            panel = MeasuredPanel(SimpleNamespace(embed_socket=host.info['socket_id'] if host else None, offline_preview=False,
                base=server.base, entry_delay=0., fullscreen=False,
                metrics=args.native_metrics, timeline=args.timeline))
            scheduler = DiagnosticFrameClock(panel,GLib,
                'request' if args.frame_clock_request else 'drive' if args.frame_clock_drive else None)
            window.add(panel)
            window.connect('destroy', lambda *_: panel.operation('shutdown'))
            window.show_all()
            frame_sync = diagnostic_embedded_frame_sync(window, args.disable_embedded_frame_sync)
            if args.fullscreen and not args.embed:
                window.fullscreen()

            def resume_when_mapped():
                nonlocal mapping_source
                if panel.stop.is_set():
                    mapping_source=None
                    return False
                if embed_is_ready(window,panel):
                    # The clock and HTTP poller remain paused until the foreign
                    # plug and the actual canvas both have mapped allocations.
                    embedding['verified_mapped']=True
                    embedding['mapped_canvas']=[panel.canvas.get_allocated_width(),panel.canvas.get_allocated_height()]
                    events.append(dict(event='foreign_plug_mapped',at=time.monotonic(),
                                       canvas=embedding['mapped_canvas']))
                    panel.operation('resume')
                    mapping_source=None
                    return False
                return True
            if args.embed:
                mapping_source=GLib.timeout_add(50,resume_when_mapped,priority=GLib.PRIORITY_DEFAULT)

            def watchdog():
                nonlocal timeout_source
                if state['finish']:
                    timeout_source = None
                    return False
                if host is not None and host.process.poll() is not None:
                    timeout_source=None
                    failures.append('Private XEmbed host exited before benchmark completed')
                    panel.operation('shutdown')
                    return False
                if time.monotonic()-started > args.seconds+args.warmup+35:
                    timeout_source = None
                    failures.append('Benchmark did not finish within its bounded wall-clock allowance')
                    panel.operation('shutdown')
                    return False
                return True
            timeout_source = GLib.timeout_add(1000, watchdog, priority=GLib.PRIORITY_DEFAULT)
            Gtk.main()
            actual_size = list(window.get_size())
            canvas_size = [panel.canvas.get_allocated_width(), panel.canvas.get_allocated_height()]
            final_snapshot = panel.game.snapshot()
            performance = panel.performance_snapshot()
            renderer = panel.renderer
            cache_metrics = dict(projectile_tile_allocations=getattr(renderer,'projectile_tile_allocations',None),
                projectile_tiles=len(getattr(renderer,'projectile_cache',())),
                enemy_tiles=len(getattr(renderer,'enemy_cache',())),
                viewport_surface_allocations=getattr(renderer,'viewport_surface_allocations',None),
                hud_surface_allocations=getattr(renderer,'hud_surface_allocations',None))
    finally:
        for source_id in (timeout_source,mapping_source):
            if source_id is not None:
                source_handle = GLib.MainContext.default().find_source_by_id(source_id)
                if source_handle is not None:
                    source_handle.destroy()
        timeout_source=mapping_source=None
        if panel:
            panel.operation('shutdown')
            if panel.poll_thread:
                panel.poll_thread.join(1.)
            if panel.timeline:
                panel.timeline.finished.wait(2.)
        if scheduler:
            scheduler.close()
        if window:
            window.destroy()
        if host:
            host.close()
        server.close()
    if not state['finish']:
        failures.append('Measurement did not complete')
    if args.embed and not embedding['verified_mapped']:
        failures.append('Foreign XEmbed plug/canvas never verified mapped')
    if any(item.dropped for item in (draw_rows, tick_rows, logic_rows)):
        failures.append('Bounded trace capacity exceeded; reported percentiles contain retained samples only')
    if panel and panel.fatal_error:
        failures.append(panel.fatal_error)
    timeline = panel.timeline.summary() if panel and panel.timeline else None
    if timeline and (timeline['error'] or not timeline['finished'] or timeline['overwritten'] or timeline['contended']):
        failures.append('Detailed timeline is incomplete; inspect timeline error/loss counters')
    validity, validity_failures = validate_measurement(draw_rows.rows,tick_rows.rows,logic_rows.rows,
                                                      state['measure_start'],args.seconds)
    failures.extend(validity_failures)
    summary = summarize(draw_rows.rows, tick_rows.rows, logic_rows.rows)
    full_level = bool(args.scene == 'level' and final_snapshot and
        (final_snapshot['phase'] == 'won' or final_snapshot['time'] >= final_snapshot['duration']-.1))
    if args.require_full_level and not full_level:
        failures.append('Full production level not observed; a short segment or early death cannot pass this requirement')
    playing_draws = [r for r in draw_rows.rows if r['phase'] == 'playing']
    playing_ticks = [r for r in tick_rows.rows if r['phase'] == 'playing']
    root = Path(__file__).resolve().parent
    report = dict(schema='sword-danmaku-native-panel-benchmark-v1',
        scene=args.scene, synthetic=args.scene != 'replay', synthetic_stress=args.scene == 'stress',
        input=('Recorded COCO17 sampled arrivals through separate-process loopback HTTP'
               if args.scene == 'replay' else 'deterministic COCO17 loopback HTTP at 30 Hz'),
        input_strategy=('Bounded-speed synthetic target aiming and power pickup, '
                        'full-energy neutral/spread COCO17 gesture through production InputTracker'
                        if args.scene=='level' else 'Uninterpolated recorded poses; affine timestamp rebase'
                        if args.scene=='replay' else 'Smooth lower-field sine trajectory'),
        recorded_replay=server.description if args.scene == 'replay' else None,
        replay_game_note=('New seed-260 game starts without countdown at first tracked replay pose; '
                          'source pose/arrival timeline retained, original lifecycle/system load not reconstructed'
                          if args.scene == 'replay' else None),
        synthetic_gesture_requests=getattr(source, 'cast_requests', None),
        measurement='Actual SwordPanel GTK draw callbacks, polling, input and Game.update; not optical FPS',
        physical_scanout_measured=False, camera_accessed=False, production_api_accessed=False,
        platform=platform.platform(), machine=platform.machine(), display=display_info,
        window=dict(requested=[args.width,args.height], actual=actual_size, canvas=canvas_size,
                    fullscreen=args.fullscreen,xembed=args.embed),
        embedding=embedding,
        seconds_requested=args.seconds, warmup_seconds=args.warmup,
        diagnostic_scheduler=scheduler.report() if scheduler else dict(enabled=False,mode=None),
        diagnostic_embedded_frame_sync=frame_sync,
        whole_run=summary, measurement_validity=validity,
        playing_only=summarize(playing_draws, playing_ticks,
                              [r for r in logic_rows.rows if r['phase'] == 'playing']),
        full_level_observed=full_level,
        full_level_note='Only terminal win or reaching the unchanged production level duration counts; early loss/short segment does not',
        stress_fixture=fixture.description() if fixture else None,
        bounded_sample_capacity=SAMPLE_LIMIT,
        dropped_samples=dict(draw=draw_rows.dropped, tick=tick_rows.dropped, logic=logic_rows.dropped),
        source_sha256={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                       for name in ('benchmark_danmaku.py','main.py','engine.py','renderer.py','input.py')},
        final_game={key:final_snapshot.get(key) for key in ('phase','time','duration','score','lives','kills','power','boss_phase','projectile_budget')}
                    if final_snapshot else None,
        native_metrics=performance, renderer_cache=cache_metrics,
        detailed_timeline=timeline,
        events=events, failures=failures, forbidden_requests=violations,
        api_requests=server.requests, api_payload_bytes=server.bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.trace:
        with args.output.with_suffix('.trace.jsonl').open('w', encoding='utf-8') as output:
            for kind, records in (('draw',draw_rows), ('tick',tick_rows), ('logic',logic_rows)):
                for row in records.rows:
                    output.write(json.dumps(dict(kind=kind, **row))+'\n')
    print(json.dumps(dict(output=str(args.output), measured=summary, full_level_observed=full_level,
                         failures=failures), ensure_ascii=False), flush=True)
    return int(bool(failures or violations))


class StressPopulation:
    """Engine-specific population is deliberately isolated from production."""
    def __init__(self, engine_module,collisions=False):
        names = ('MAX_BULLETS','MAX_SHOTS','MAX_ENEMIES','MAX_ENTITIES','MAX_EFFECTS')
        if any(not hasattr(engine_module, name) for name in names):
            raise RuntimeError('Stress scene requires the danmaku engine with explicit object budgets')
        self.budget = {name:int(getattr(engine_module,name)) for name in names}
        self.applications = 0
        self.collisions = bool(collisions)
        self.damage_events = 0
        self.damage_total = 0.

    def apply(self, game):
        self.applications += 1
        t = game.time
        # Synthetic-only: keep the workload alive and the player's visible
        # collision point stable. Production level scene never touches these.
        game.duration = max(10000., game.duration)
        game.player['invulnerable'] = 0.
        game.lives = 5
        if not any(e['kind']=='boss' and not e.get('hit') for e in game.entities):
            game._spawn_boss()
        boss = next(e for e in game.entities if e['kind']=='boss' and not e.get('hit'))
        boss['health'] = boss['max_health']*(.93,.61,.29)[int(t/4)%3]
        boss['warning'] = 0.
        while sum(e['kind'] in ('enemy','elite','boss') and not e.get('hit')
                  for e in game.entities) < self.budget['MAX_ENEMIES']:
            i = len(game.entities)
            if game._entity('elite' if i%5 == 0 else 'enemy',
                    .08+(i%10)*.047, .19+(i//10)*.14,
                    .052 if i%5 == 0 else .032, style=i%4, vx=0.,vy=.015) is None:
                break
        while len(game.entities) < self.budget['MAX_ENTITIES']:
            i = len(game.entities)
            if game._entity('power' if i%3 == 0 else 'orb', .08+(i%10)*.047,
                            .48+(i%3)*.03, .022 if i%3 == 0 else .016) is None:
                break
        for i, entity in enumerate(game.entities):
            if entity['kind'] == 'boss':
                entity['x'],entity['y'] = .50, .16
            else:
                entity['x'] = .08+(i%10)*.047+.006*math.sin(t+i)
                entity['y'] = .22+(i//10)*.09
                if self.collisions and entity['kind'] in ('enemy','elite'):
                    # Synthetic durability keeps targets alive; real shot
                    # damage/contact calculations and hit removal still run.
                    entity['health'] = entity['max_health'] = 1000000.
        while len(game.bullets) < self.budget['MAX_BULLETS']:
            i = len(game.bullets)
            before = len(game.bullets)
            game._bullet(.05,.25,math.pi/2,.025,
                         style=('ink','red','gold','indigo')[i%4],radius=.010+(i%3)*.002)
            if len(game.bullets) == before:
                break
        for i, bullet in enumerate(game.bullets):
            # Separated upper-field lattice: real engine movement/collision
            # tests still execute, while contacts don't collapse the workload.
            bullet['x'] = .035+(i%20)*.047+.006*math.sin(t*.8+i)
            bullet['y'] = .23+(i//20)*.038+.005*math.cos(t+i)
        while len(game.shots) < self.budget['MAX_SHOTS']:
            before = len(game.shots)
            game._shot(.85,.8)
            if len(game.shots) == before:
                break
        for i, shot in enumerate(game.shots):
            if self.collisions:
                # Span and cross the actual enemy band, including aligned and
                # near-miss columns; global AABB rejection cannot skip it.
                shot['x'] = .075+(i%12)*.047
                shot['y'] = .14+(i//12)*.092+.05*math.sin(t*1.5)
            else:
                shot['x'] = .72+(i%12)*.020
                shot['y'] = .69+(i//12)*.046
        while len(game.effects) < self.budget['MAX_EFFECTS']:
            i = len(game.effects)
            game._effect(('hit','collect','power','kill')[i%4],
                         .08+(i%10)*.090,.27+(i//10)*.12,.9)
        for i, effect in enumerate(game.effects):
            # Leave >= .35 s lifetime even at the engine's 250 ms dt cap, so
            # all 40 survive update and are available to the real renderer.
            effect['age'] = (t*.7+i*.047)%.55
        game.power = 3
        game.kills = int(t*7)
        game.score = int(t*130)
        game.energy = (t*.075)%1.
        game.special_time = max(0.,1.1-t%7)  # Includes the illustrated sword fan.

    def description(self):
        return dict(synthetic=True, production_rules=False, budget=self.budget,
            collisions=self.collisions,
            actual_damage_events=self.damage_events,actual_damage_total=self.damage_total,
            applications=self.applications,
            changes='Populations replenished to bounded budgets; moving lattices; '
                    'boss health phases, HUD values and hit effects injected; level duration extended',
            timing='Fixture maintenance included in tick time, separately reported from unmodified Game.update',
            collision_loop=('Real collision/update loops; shots overlap/cross enemy band; non-boss health '
                            'set to 1000000 solely in this synthetic stress; hit shots removed by engine '
                            'and replenished next tick; rendered shot count is measured, not forced to 72'
                            if self.collisions else
                            'Renderer-load scenario: separated shots/enemies permit real broad-phase rejection; '
                            'not a dense collision workload'))


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', choices=('level','stress','replay'), default='stress')
    parser.add_argument('--seconds', type=float, default=30.)
    parser.add_argument('--warmup', type=float, default=None)
    parser.add_argument('--recording', type=Path, help='State JSONL with original test_elapsed arrival timing')
    parser.add_argument('--recording-start', type=float, default=0., help='Seconds after first recorded arrival')
    parser.add_argument('--timeline', type=Path, help='Opt-in detailed tick/draw/GC JSONL with bounded async writer')
    parser.add_argument('--native-metrics', type=Path, help='Enable the same optional one-second metrics writer as production')
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--depth', type=int, choices=(16,24), default=16)
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--embed', action='store_true',
                        help='Private separate-process Gtk.Socket host and foreign Gtk.Plug, with production Suite layout')
    parser.add_argument('--disable-embedded-frame-sync', action='store_true',
                        help='Diagnostic A/B only: disable native Gtk.Plug frame synchronization after realization; requires --embed')
    parser.add_argument('--physical-display', help='Explicitly use existing local X11 display; default starts private Xvfb')
    parser.add_argument('--trace', action='store_true', help='Write bounded raw timing trace after the timed run')
    parser.add_argument('--require-full-level', action='store_true',
                        help='Exit nonzero unless the unchanged production level completes or reaches its duration')
    parser.add_argument('--stress-collisions', action='store_true',
                        help='Stress actual overlapping bullet/enemy collisions, with synthetic durable enemies')
    scheduler = parser.add_mutually_exclusive_group()
    scheduler.add_argument('--frame-clock-request',action='store_true',
        help='Diagnostic only: retain production timer and request active redraws from GDK frame clock')
    scheduler.add_argument('--frame-clock-drive',action='store_true',
        help='Diagnostic only: replace production timer with GDK frame clock calling the real tick method')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.warmup is None:
        args.warmup = 0. if args.scene == 'replay' else 2.
    if not math.isfinite(args.seconds) or not 15 <= args.seconds <= 300:
        parser.error('--seconds must be between 15 and 300')
    if not math.isfinite(args.warmup) or not 0 <= args.warmup <= 10:
        parser.error('--warmup must be between 0 and 10')
    if not 640 <= args.width <= 4096 or not 400 <= args.height <= 2160:
        parser.error('Window dimensions must be in 640..4096 x 400..2160')
    if args.require_full_level and args.scene != 'level':
        parser.error('--require-full-level applies only to the production level scene')
    if args.stress_collisions and args.scene != 'stress':
        parser.error('--stress-collisions applies only to the synthetic stress scene')
    if (args.scene == 'replay') != bool(args.recording):
        parser.error('--scene replay and --recording must be supplied together')
    if not math.isfinite(args.recording_start) or args.recording_start < 0:
        parser.error('--recording-start must be finite and nonnegative')
    if args.scene != 'replay' and args.recording_start:
        parser.error('--recording-start requires --scene replay')
    if args.scene == 'replay' and args.warmup:
        parser.error('Recorded replay uses --warmup 0 to retain the complete selected input interval')
    if args.disable_embedded_frame_sync and not args.embed:
        parser.error('--disable-embedded-frame-sync requires --embed')
    return args


def main(argv=None):
    args = arguments(argv)
    with isolated_display(args) as display_info:
        return run(args, display_info)


if __name__ == '__main__':
    raise SystemExit(main())
