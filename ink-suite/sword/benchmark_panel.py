#!/usr/bin/env python3
"""Measure the real GTK SwordPanel against an isolated loopback pose server.

Examples (run from an InkSuite checkout on the board):
  DISPLAY=:98 python3 sword/benchmark_panel.py --seconds 20 --output /tmp/panel.json
  DISPLAY=:98 python3 sword/benchmark_panel.py --embed --suite-root /tmp/old-suite \
      --seconds 20 --output /tmp/old-panel.json
  DISPLAY=:98 python3 sword/benchmark_panel.py --recording /tmp/state.jsonl \
      --seconds 30 --invalid-seconds 0 --skip-controls --output /tmp/replay.json

The same external callback instrumentation works with old SwordPanel versions.
Only a random-port 127.0.0.1 fake API is permitted. No camera, production API,
screen capture, or game implementation is modified. Reported draw cadence is
GTK callback cadence, NOT physical display scanout or camera-to-photon latency.
"""
import argparse
import bisect
from collections import Counter
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from types import SimpleNamespace
import urllib.request
from unittest.mock import patch


def quantiles(values):
    values = sorted(values)
    if not values:
        return dict(count=0)
    return dict(count=len(values), mean=statistics.mean(values), minimum=values[0],
                p50=values[(len(values)-1)//2], p95=values[int((len(values)-1)*.95)],
                maximum=values[-1])


def synthetic_pose(stamp, sequence, x=.45, y=.48):
    """COCO17 input, inverse of the production right-shoulder hand mapping."""
    aspect = 16/9
    width = .18*aspect
    wx = .59+(.5-x)*width*1.6/aspect
    wy = .36+(y-.35)*width*2.6
    points = [[.5, .5, 0.] for _ in range(17)]
    values = {0:(.5,.20), 5:(.41,.36), 6:(.59,.36),
              7:(.31,.52), 8:((.59+wx)/2,(.36+wy)/2),
              9:(.30,.67), 10:(wx,wy), 11:(.44,.66), 12:(.56,.66),
              13:(.44,.80), 14:(.56,.80), 15:(.43,.96), 16:(.57,.96)}
    for index, position in values.items():
        points[index] = [*position, .98]
    return dict(pid='benchmark-synthetic-only', sequence=sequence,
                timestamp_seconds=stamp, points17=points, latency_ms=0.,
                suite=dict(active=True, mode='sword', menu=False, control_active=False))


class PoseSource:
    """Free-running source clock; repeated HTTP polls cannot make a frame new."""
    def __init__(self, rate=30., recording=None):
        self.rate = rate
        self.origin = time.monotonic()
        self.invalid = False
        self.rows = []
        self.times = []
        self.recording = recording
        if recording:
            for line in Path(recording).read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                if isinstance(row, dict) and 'test_error' not in row and isinstance(row.get('test_elapsed'), (int,float)):
                    self.rows.append(row)
            self.rows.sort(key=lambda row: row['test_elapsed'])
            if not self.rows:
                raise ValueError('Recording contains no timestamped state rows')
            first = self.rows[0]['test_elapsed']
            self.times = [row['test_elapsed']-first for row in self.rows]

    def state(self, now=None):
        now = time.monotonic() if now is None else now
        elapsed = max(0., now-self.origin)
        if self.rows:
            index = max(0, bisect.bisect_right(self.times, elapsed)-1)
            result = deepcopy(self.rows[index])
            result.update(pid='benchmark-recorded-only', sequence=index,
                          timestamp_seconds=self.origin+self.times[index])
            # Run the game alone; preserve original menu-candidate freezes.
            suite = result.get('suite') or {}
            result['suite'] = dict(active=True, mode='sword', menu=False,
                                 control_active=bool(suite.get('control_active')))
        else:
            index = int(elapsed*self.rate)
            stamp = self.origin+index/self.rate
            x = .45+.17*math.sin(index/self.rate*.63)
            y = .48+.055*math.sin(index/self.rate*.91)
            result = synthetic_pose(stamp, index, x, y)
        if self.invalid:
            points = result.get('points17')
            if points:
                for index in (5,6,10):
                    points[index][2] = 0.
            result['benchmark_injected_invalid'] = True
        return result


class FakePoseServer:
    def __init__(self, source):
        self.source = source
        self.requests = 0
        self.bytes = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/api/state':
                    self.send_error(404)
                    return
                payload = json.dumps(owner.source.state(), separators=(',',':')).encode('utf-8')
                owner.requests += 1
                owner.bytes += len(payload)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        self.httpd = HTTPServer(('127.0.0.1', 0), Handler)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name='benchmark-fake-api', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(1.)


def pause_reason(panel, now):
    """Use equivalent conditions on versions without performance_snapshot."""
    if not panel.active:
        return 'host_pause'
    control = panel.control
    valid = (control.get('valid') and control.get('status') == 'tracking'
             and panel.last_fresh is not None and now-panel.last_fresh <= .24)
    if not valid:
        return 'tracking'
    if panel.recovery_needed:
        return 'recovery'
    if now < panel.entry_until:
        return 'entry'
    suite = (panel.current_state or {}).get('suite') or {}
    return 'menu_gesture' if suite.get('control_active') else ''


def summarize_period(samples, draws, period):
    ticks = [row for row in samples if row['period'] == period]
    frames = [row for row in draws if row['period'] == period]
    intervals = [frames[i]['start']-frames[i-1]['start'] for i in range(1,len(frames))]
    reasons = Counter()
    statuses = Counter(row['status'] for row in ticks)
    for before, after in zip(ticks, ticks[1:]):
        reasons[before['pause_reason']] += max(0., after['at']-before['at'])
    elapsed = ticks[-1]['at']-ticks[0]['at'] if len(ticks)>1 else 0.
    game_delta = ticks[-1]['game_time']-ticks[0]['game_time'] if len(ticks)>1 else 0.
    return dict(samples=len(ticks), full_draw_callbacks=len(frames),
        draw_callback_cadence_fps=(len(frames)-1)/(frames[-1]['start']-frames[0]['start']) if len(frames)>1 else 0.,
        full_draw_callback_ms=quantiles([row['seconds']*1000 for row in frames]),
        compose_and_present_ms=quantiles([(row['seconds']+row.get('prepared_compose_seconds',0.))*1000 for row in frames]),
        draw_start_interval_ms=quantiles([value*1000 for value in intervals]),
        advance_callback_ms=quantiles([row['callback_seconds']*1000 for row in ticks]),
        tick_interval_ms=quantiles([(ticks[i]['at']-ticks[i-1]['at'])*1000 for i in range(1,len(ticks))]),
        source_age_ms=quantiles([row['source_age_ms'] for row in ticks if row['source_age_ms'] is not None]),
        fresh_observation_age_ms=quantiles([row['fresh_age_ms'] for row in ticks if row['fresh_age_ms'] is not None]),
        observation_status_samples=dict(statuses), pause_reason_seconds=dict(reasons),
        measured_wall_seconds=elapsed, game_time_delta=game_delta,
        game_clock_per_wall_second=game_delta/elapsed if elapsed else 0.,
        first_game_time=ticks[0]['game_time'] if ticks else None,
        last_game_time=ticks[-1]['game_time'] if ticks else None,
        phases=dict(Counter(row['phase'] for row in ticks)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=20., help='Continuous baseline duration, minimum 15 s')
    parser.add_argument('--rate', type=float, default=30., help='Synthetic pose publication Hz')
    parser.add_argument('--startup-timeout', type=float, default=45.,
                        help='Maximum time for the first 3 s game countdown, including slow old versions')
    parser.add_argument('--recording', type=Path, help='Previously recorded state.jsonl; no camera access')
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--embed', action='store_true', help='Use a real Gtk.Socket/Gtk.Plug pair')
    parser.add_argument('--fullscreen', action='store_true',
                        help='Fullscreen the benchmark window on the selected DISPLAY')
    parser.add_argument('--damage', action='store_true',help='Exercise prepared-frame exact dirty-region presentation')
    parser.add_argument('--skip-controls', action='store_true', help='Skip host-pause and restart-button exercises')
    parser.add_argument('--invalid-seconds', type=float, default=.8,
                        help='Inject fresh invalid joints after baseline/controls, 0 disables')
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds < 15 or args.seconds > 300:
        parser.error('--seconds must be between 15 and 300')
    if not math.isfinite(args.rate) or not 1 <= args.rate <= 120:
        parser.error('--rate must be between 1 and 120')
    if not math.isfinite(args.startup_timeout) or not 5 <= args.startup_timeout <= 120:
        parser.error('--startup-timeout must be between 5 and 120')
    if not math.isfinite(args.invalid_seconds) or not 0 <= args.invalid_seconds <= 10:
        parser.error('--invalid-seconds must be between 0 and 10')
    if args.width < 640 or args.height < 400:
        parser.error('Window must be at least 640x400')
    args.suite_root = args.suite_root.resolve()
    if args.damage:
        os.environ['INK_SWORD_DAMAGE']='1'
    sys.path.insert(0, str(args.suite_root))
    module = importlib.import_module('sword.main')
    if Path(module.__file__).resolve().parent.parent != args.suite_root:
        raise RuntimeError('Loaded an unintended suite root')
    Gtk, Gdk, GLib = module.Gtk, module.Gdk, module.GLib
    source = PoseSource(args.rate, args.recording)
    server = FakePoseServer(source)
    server.start()
    samples, draws, events, violations, failures = [], [], [], [], []
    state = dict(period='startup', pending=None, done=False)
    first_draw = threading.Event()
    entered_play = threading.Event()
    settled = threading.Event()
    action_done = threading.Event()
    real_open = urllib.request.OpenerDirector.open

    def guarded_open(opener, url, *pos, **kw):
        address = url.full_url if isinstance(url, urllib.request.Request) else str(url)
        if address != server.base+'/api/state':
            violations.append(address)
            raise AssertionError('Benchmark forbids external/production endpoint: '+address)
        return real_open(opener, url, *pos, **kw)

    class MeasuredPanel(module.SwordPanel):
        def draw(self, widget, context):
            started = time.monotonic()
            period = state['period']
            prepared = getattr(self,'pending_compose_seconds',0.) if getattr(self,'render_pending',False) else 0.
            value = super().draw(widget, context)
            draws.append(dict(start=started, seconds=time.monotonic()-started, period=period,
                prepared_compose_seconds=prepared))
            first_draw.set()
            return value

        def advance(self, now):
            started = time.monotonic()
            super().advance(now)
            ended = time.monotonic()
            snapshot = self.game.snapshot()
            source_stamp = (self.current_state or {}).get('timestamp_seconds')
            row = dict(at=now, period=state['period'], callback_seconds=ended-started,
                game_time=snapshot['time'], phase=snapshot['phase'], paused=snapshot['paused'],
                pause_reason=pause_reason(self, now), status=self.control.get('status'),
                source_age_ms=max(0.,now-source_stamp)*1000 if isinstance(source_stamp,(int,float)) else None,
                fresh_age_ms=max(0.,now-self.last_fresh)*1000 if self.last_fresh is not None else None,
                recovery_needed=self.recovery_needed)
            samples.append(row)
            if snapshot['phase'] == 'playing':
                entered_play.set()
            if not row['pause_reason'] and row['status'] == 'tracking':
                settled.set()

        def restart(self):
            result = super().restart()
            if result and state['pending'] and state['pending']['op'] in ('start_click','restart_click'):
                finish_action(self)
            return result

        def operation(self, op):
            result = super().operation(op)
            if state['pending'] and state['pending']['op'] == op:
                finish_action(self)
            return result

    def finish_action(panel):
        pending = state['pending']
        if pending is None:
            return
        state['pending'] = None
        event = dict(pending, applied=time.monotonic(), phase=panel.game.snapshot()['phase'],
                     game_time=panel.game.snapshot()['time'], active=panel.active,
                     paused=panel.game.snapshot()['paused'])
        event['response_ms'] = (event['applied']-event['issued'])*1000
        events.append(event)
        action_done.set()

    window = plug = panel = driver = None
    final_metrics = None
    final_window_size = final_canvas_size = None
    try:
        with patch('urllib.request.OpenerDirector.open', guarded_open):
            window = Gtk.Window(title='御剑离线 GTK 性能验证 · 非真人测试')
            window.set_default_size(args.width,args.height)
            if args.embed:
                socket = Gtk.Socket()
                window.add(socket)
                window.show_all()
                socket.realize()
                plug = Gtk.Plug.new(socket.get_id())
            panel = MeasuredPanel(SimpleNamespace(embed_socket=socket.get_id() if args.embed else None,
                offline_preview=False, base=server.base, entry_delay=0., fullscreen=False, metrics=None))
            (plug or window).add(panel)
            (plug or window).show_all()
            window.show_all()
            if args.fullscreen:
                window.fullscreen()
            window.connect('destroy', lambda *_: panel.operation('shutdown'))
            if args.embed:
                panel.operation('resume')

            def send_action(op):
                action_done.clear()
                state['pending'] = dict(op=op, issued=time.monotonic(), period=state['period'])
                if op in ('pause','resume'):
                    # Same worker-thread -> GLib command path as read_commands.
                    panel.submit_operation(op)
                else:
                    def dispatch():
                        if op == 'restart_click':
                            panel.restart_button.emit('clicked')
                        else:
                            event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
                            event.button = 1
                            event.window = panel.canvas.get_window()
                            event.x = panel.canvas.get_allocated_width()*.5
                            event.y = panel.canvas.get_allocated_height()*.67
                            event.time = Gdk.CURRENT_TIME
                            panel.canvas.emit('button-press-event',event)
                        return False
                    GLib.idle_add(dispatch, priority=GLib.PRIORITY_DEFAULT)
                if not action_done.wait(12.):
                    raise RuntimeError('GTK did not acknowledge '+op+' within 12 s')

            def wait_for(event, timeout, label):
                if not event.wait(timeout):
                    raise RuntimeError('Timeout while waiting for '+label)

            def run_driver():
                try:
                    wait_for(first_draw,20.,'first full draw')
                    time.sleep(3.2 if args.embed else .8)
                    entered_play.clear()
                    send_action('start_click')
                    wait_for(entered_play,args.startup_timeout,'initial countdown and valid input')
                    state['period']='baseline'
                    events.append(dict(op='baseline_begin',at=time.monotonic()))
                    time.sleep(args.seconds)
                    events.append(dict(op='baseline_end',at=time.monotonic()))
                    if not args.skip_controls:
                        state['period']='controls'
                        send_action('pause')
                        time.sleep(.65)
                        settled.clear()
                        send_action('resume')
                        wait_for(settled,12.,'resume countdown')
                        entered_play.clear()
                        send_action('restart_click')
                        wait_for(entered_play,15.,'restart countdown')
                    if args.invalid_seconds:
                        state['period']='invalid_injection'
                        source.invalid=True
                        events.append(dict(op='invalid_begin',at=time.monotonic()))
                        time.sleep(args.invalid_seconds)
                        source.invalid=False
                        settled.clear()
                        events.append(dict(op='invalid_end',at=time.monotonic()))
                        wait_for(settled,12.,'valid-pose recovery')
                        time.sleep(.25)
                except Exception as error:
                    failures.append(str(error))
                finally:
                    state['done']=True
                    GLib.idle_add(panel.operation,'shutdown',priority=GLib.PRIORITY_HIGH)

            driver = threading.Thread(target=run_driver,name='benchmark-actions',daemon=True)
            driver.start()
            Gtk.main()
            final_window_size = list(window.get_size())
            final_canvas_size = [panel.canvas.get_allocated_width(),panel.canvas.get_allocated_height()]
            if hasattr(panel,'performance_snapshot'):
                final_metrics = panel.performance_snapshot()
    finally:
        if panel:
            panel.operation('shutdown')
            if panel.poll_thread:
                panel.poll_thread.join(1.)
        if plug:
            plug.destroy()
        if window:
            window.destroy()
        server.close()
    periods = {period:summarize_period(samples,draws,period)
               for period in ('startup','baseline','controls','invalid_injection')}
    if not state['done']:
        failures.append('GTK loop exited before the benchmark driver completed')
    baseline = periods['baseline']
    pauses = baseline['pause_reason_seconds']
    baseline_checks = dict(
        measured_at_least_requested_minus_one_tick=baseline['measured_wall_seconds'] >= args.seconds-.3,
        no_unexpected_tracking_freeze=pauses.get('tracking',0.) <= .15,
        no_unexpected_recovery=pauses.get('recovery',0.) <= .15,
        clock_advances_in_real_time=baseline['game_clock_per_wall_second'] >= .90)
    if args.recording:
        baseline_checks = dict(applicable=False, reason='Recorded gaps/invalid poses may intentionally pause the game')
    host_pause = next((event for event in events if event.get('op') == 'pause'),None)
    host_resume = next((event for event in events if event.get('op') == 'resume'),None)
    control_checks = dict(applicable=not args.skip_controls,
        host_pause_game_clock_delta=host_resume['game_time']-host_pause['game_time']
            if host_pause and host_resume else None,
        start_click_acknowledged=any(event.get('op') == 'start_click' for event in events),
        restart_button_acknowledged=any(event.get('op') == 'restart_click' for event in events))
    invalid_rows = [row for row in samples if row['period']=='invalid_injection']
    invalid_checks = dict(applicable=bool(args.invalid_seconds),
        invalid_observed=any(row['pause_reason']=='tracking' for row in invalid_rows),
        recovery_observed=any(row['pause_reason']=='recovery' for row in invalid_rows),
        recovery_expected=args.invalid_seconds >= .75,
        returned_to_tracking=bool(invalid_rows and invalid_rows[-1]['status']=='tracking'
                                  and not invalid_rows[-1]['pause_reason']))
    if not args.recording:
        failures.extend('Synthetic baseline check failed: '+name
                        for name, passed in baseline_checks.items() if not passed)
    if not control_checks['start_click_acknowledged']:
        failures.append('Start click was not acknowledged')
    if not args.skip_controls:
        if not host_pause or not host_resume:
            failures.append('Host pause/resume was not acknowledged')
        elif abs(control_checks['host_pause_game_clock_delta']) > 1e-6:
            failures.append('Game clock advanced during host pause')
        if not control_checks['restart_button_acknowledged']:
            failures.append('Restart button was not acknowledged')
    if args.invalid_seconds:
        if not invalid_checks['invalid_observed']:
            failures.append('Injected invalid input was not observed by the real panel loop')
        if invalid_checks['recovery_expected'] and not invalid_checks['recovery_observed']:
            failures.append('Sustained invalid input did not enter recovery')
        if not invalid_checks['returned_to_tracking']:
            failures.append('Panel did not resume tracking after invalid-input injection')
    report = dict(schema='sword-native-panel-benchmark-v1',platform=platform.platform(),
        machine=platform.machine(),suite_root=str(args.suite_root),
        source_sha256={str(path.relative_to(args.suite_root)):hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (args.suite_root/'sword/main.py',args.suite_root/'sword/renderer.py',
                                    args.suite_root/'sword/input.py',args.suite_root/'sword/engine.py')},
        window=dict(requested_width=args.width,requested_height=args.height,
                    actual_size=final_window_size,canvas_size=final_canvas_size,
                    xembed=args.embed,fullscreen=args.fullscreen),
        source=dict(type='recorded_timing_preserved' if args.recording else 'fresh_synthetic_coco17',
                    path=str(args.recording) if args.recording else None,
                    rate=args.rate if not args.recording else None,
                    api_requests=server.requests,payload_bytes=server.bytes,
                    production_api_accessed=False,camera_accessed=False,forbidden_requests=violations),
        measurement='Real GTK full draw callbacks including native blit; real poll thread and loopback HTTP; excludes physical scanout',
        physical_scanout_measured=False,hardware_input_latency_measured=False,
        prepared_damage_mode=args.damage,
        control_latency_measurement='Synthetic GTK event / worker command enqueue to application callback, not physical mouse or host IPC',
        periods=periods,events=events,baseline_checks=baseline_checks,
        control_checks=control_checks,invalid_checks=invalid_checks,
        fatal_error=panel.fatal_error if panel else 'Panel failed to initialize',failures=failures,
        optional_native_metrics=final_metrics)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    trace_path = args.output.with_suffix('.trace.jsonl')
    with trace_path.open('w',encoding='utf-8') as trace:
        for row in samples:
            trace.write(json.dumps(dict(kind='advance',**row))+'\n')
        for row in draws:
            trace.write(json.dumps(dict(kind='draw',**row))+'\n')
    print(json.dumps(dict(output=str(args.output),trace=str(trace_path),baseline=baseline,
        baseline_checks=baseline_checks,failures=failures,fatal_error=report['fatal_error']),ensure_ascii=False))
    return 1 if failures or report['fatal_error'] or violations else 0


if __name__ == '__main__':
    raise SystemExit(main())
