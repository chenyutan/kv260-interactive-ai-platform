"""Real HTTP/GTK startup and recovery test, using only synthetic frame metadata.

The default invocation starts an isolated Xvfb display; it never connects to the
camera, the board, or an existing desktop. Run with --out result.json on a Linux
host with the app's GTK dependencies and xvfb-run. The two PNGs beside the JSON
are screenshots of this synthetic menu, not photographs.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys


def isolated(args):
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    launcher = shutil.which('xvfb-run')
    if launcher is None:
        report = dict(passed=False, camera_used=False, production_used=False,
                      errors=['xvfb-run is required; no existing display was used'])
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
        return 2
    env = dict(os.environ, INK_STARTUP_ISOLATED_XVFB='1')
    # GTK is imported only in the child, after xvfb-run replaces DISPLAY.
    command = [launcher, '-a', '-s', '-screen 0 1024x600x24 -nolisten tcp',
               sys.executable, str(Path(__file__).resolve()), '--out', str(out),
               '--isolated-child']
    process = subprocess.Popen(command, env=env, start_new_session=True)
    try:
        return process.wait(timeout=19.0)
    except subprocess.TimeoutExpired:
        # Terminate our isolated display and test together, never a desktop or
        # service outside this process group.
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=.5)
        report = dict(passed=False, camera_used=False, production_used=False,
                      errors=['Isolated GTK test exceeded its 19-second limit'])
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
        return 2


def run(args):
    if os.environ.get('INK_STARTUP_ISOLATED_XVFB') != '1':
        raise RuntimeError('Use the default invocation to create isolated Xvfb')
    import threading
    import time
    import traceback
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk, GLib
    from main import Suite

    out = Path(args.out).resolve()
    lock = threading.Lock()
    started = [time.monotonic()]
    transport = dict(available=False)
    state = dict(pid=7654321, sequence=0, timestamp_seconds=0., pose_count=0,
                 phase='unknown', points17=None, camera_link=dict(present=True),
                 suite=dict(active=True, mode='ink', menu=True,
                            control_active=False, open_serial=0, hand=None))
    requests, commands, selections, records, errors, screenshots = [], [], [], [], [], []
    checked = set()
    observed = dict(initial_ready=None, countdown_started=None, countdown_interrupted=False,
                    first_pointer=None, disconnect_dwell=False,
                    frozen_dwell=False, restart_dwell=False)
    draws = [0]

    def pose(cursor):
        points = [[.5, .5, 1.] for _ in range(17)]
        points[5], points[6] = [.6, .4, 1.], [.4, .4, 1.]
        scale = .2 * 16 / 9
        points[10] = [.5 - (cursor[0] - .5) * scale * 3.2 / (16 / 9),
                      .4 + (cursor[1] - .35) * scale * 2.6, 1.]
        points[9] = [-1., -1., 0.]
        return points

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value):
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            with lock:
                requests.append(self.path)
                available = transport['available']
                snapshot = dict(state, suite=dict(state['suite']))
            if self.path != '/api/state':
                self.send_error(404)
            elif not available:
                self.send_error(503)
            else:
                self.reply(snapshot)

        def do_POST(self):
            command = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                commands.append(dict(at=time.monotonic()-started[0], data=dict(command),
                                     accepted=transport['available']))
                available = transport['available']
                valid = self.path == '/api/command' and command.pop('command', None) == 'suite'
                if valid and available:
                    state['suite'].update(command)
            if not valid:
                self.send_error(404)
            elif not available:
                self.send_error(503)
            else:
                self.reply(dict(accepted=True))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True).start()

    class TestedSuite(Suite):
        # Keep the actual select gate and asynchronous HTTP command. Only replace
        # child creation, so no experience executable or production path is used.
        def spawn_mode(self, mode, generation):
            if generation != self.generation or self.stop.is_set():
                return False
            selections.append(dict(mode=mode, at=time.monotonic()-started[0]))
            self.mode = mode
            self.switching = False
            self.menu_open = True
            self.error_mode = None
            self.selector.reset()
            self.stack.set_visible_child_name('menu')
            self.home.set_sensitive(True)
            return False

    app = TestedSuite(SimpleNamespace(base='http://127.0.0.1:'+str(server.server_port),
        fullscreen=False, offline_preview=True, entry_delay=.6, sword_metrics=None))
    app.resize(1024, 600)
    started[0] = time.monotonic()
    app.menu.connect('draw', lambda *_: draws.__setitem__(0, draws[0]+1))

    def snapshot(label):
        item = dict(label=label, at=time.monotonic()-started[0],
                    backend_ready=app.backend_ready, pipeline_ready=app.pipeline.ready,
                    reason=app.pipeline.reason, selection_enabled=app.menu.selection_enabled,
                    pointer=app.menu.pointer, hover=app.menu.hover,
                    progress=app.menu.progress, selector_since=app.selector.since,
                    entry_pending=app.entry_pending, entry_deadline=app.entry_deadline,
                    selections=len(selections), status=app.status.get_text())
        records.append(item)
        return item

    def require(condition, message):
        if not condition:
            errors.append(message)

    def screenshot(label):
        Gdk.Display.get_default().sync()
        width, height = app.get_size()
        pixels = Gdk.pixbuf_get_from_window(app.get_window(), 0, 0, width, height)
        if pixels is None:
            errors.append('Cannot capture isolated menu: '+label)
            return
        path = out.with_name(out.stem+'-'+label+'.png')
        pixels.savev(str(path), 'png', [], [])
        screenshots.append(str(path))

    def cleared(label):
        snapshot(label)
        require(not app.pipeline.ready and not app.menu.selection_enabled,
                label+': stale/unavailable frames left selection enabled')
        require(app.menu.pointer is None and app.menu.hover is None and app.menu.progress == 0,
                label+': cursor or dwell graphics survived invalid input')
        require(app.selector.since is None, label+': dwell start survived invalid input')

    def once(label, when, elapsed, callback):
        if elapsed >= when and label not in checked:
            checked.add(label)
            callback()

    def disabled_start(label):
        cleared(label)
        require(app.entry_pending == .6 and app.entry_deadline == 0.,
                label+': entry countdown was consumed before usable frames')
        require(not selections, label+': selected an experience before frames existed')
        # Invoke the public handler directly too: its own gate must protect
        # keyboard/programmatic selection, not merely the menu's click handler.
        app.select('ink')
        require(not app.switching, label+': direct select bypassed readiness')

    def ready_empty():
        snapshot('empty-frame-ready')
        require(app.pipeline.ready and app.interaction_ready() and app.menu.selection_enabled,
                'Continuous frames without a person did not become ready')
        require(app.entry_pending == 0 and app.entry_deadline == 0,
                'Entry countdown did not complete after pipeline readiness')
        require(app.menu.pointer is None and not selections,
                'No-person frames manufactured a pointer or selection')
        screenshot('ready-no-person')

    def check_count(count, label):
        snapshot(label)
        require(len(selections) == count, label+': expected %d selections, got %d' % (count, len(selections)))

    def interrupted(flag):
        observed[flag] = app.selector.since is not None and app.menu.progress > 0
        require(observed[flag], flag+': test did not establish a dwell before interruption')

    def interrupted_countdown():
        disabled_start('countdown-interrupted')
        observed['countdown_interrupted'] = app.entry_pending == .6 and app.entry_deadline == 0
        require(observed['countdown_started'] is not None,
                'Test did not establish an entry countdown before interruption')

    def tick():
        now = time.monotonic()
        elapsed = now-started[0]
        try:
            # Sampling and UI run at distinct cadences: the real HTTP polling
            # worker receives actual independent replies and GTK dispatches them.
            # The 250 ms startup interruption exercises restart of the full
            # entry allowance; later gesture phases retain a 400 ms margin.
            flow = elapsed-.4
            unavailable = elapsed < .6 or 2.0 <= elapsed < 2.25 or 7.0 <= flow < 7.45
            no_frames = elapsed < 1.6
            frozen = 8.65 <= flow < 9.4
            empty = flow < 3.0 or 9.4 <= flow < 10.0
            centered = (3.0 <= flow < 3.5 or 5.85 <= flow < 6.35 or
                        7.45 <= flow < 7.95 or 10.0 <= flow < 10.55 or
                        11.55 <= flow < 12.1)
            with lock:
                transport['available'] = not unavailable
                if flow >= 11.15 and state['pid'] == 7654321:
                    state.update(pid=7654322, sequence=0, pose_count=0)
                    state['suite'].update(active=False, mode='ink', menu=False)
                if not no_frames and not frozen and not unavailable:
                    state.update(sequence=state['sequence']+1, pose_count=state['pose_count']+1,
                                 timestamp_seconds=now,
                                 points17=None if empty else pose((.5, .5) if centered else (.25, .35)))
            if app.pipeline.ready and observed['initial_ready'] is None:
                observed['initial_ready'] = elapsed
            if app.entry_deadline and observed['countdown_started'] is None:
                observed['countdown_started'] = elapsed
            if app.menu.pointer is not None and observed['first_pointer'] is None:
                observed['first_pointer'] = elapsed
            once('service-down', .40, elapsed, lambda: disabled_start('service-down'))
            once('accepted-no-frame', 1.35, elapsed, lambda: disabled_start('accepted-no-frame'))
            once('waiting-shot', 1.40, elapsed, lambda: screenshot('waiting'))
            once('countdown-interrupted', 2.18, elapsed, interrupted_countdown)
            once('empty-ready', 3.20, elapsed, ready_empty)
            once('first-choice', 5.80, flow, lambda: check_count(1, 'first-choice'))
            once('disconnect-dwell', 6.90, flow, lambda: interrupted('disconnect_dwell'))
            once('disconnect-clear', 7.35, flow, lambda: cleared('disconnect-clear'))
            once('disconnect-no-leak', 8.55, flow, lambda: check_count(1, 'disconnect-no-leak'))
            once('frozen-dwell', 8.60, flow, lambda: interrupted('frozen_dwell'))
            once('frozen-clear', 9.30, flow, lambda: cleared('frozen-clear'))
            once('frozen-recover', 9.95, flow,
                 lambda: require(app.interaction_ready(), 'Frozen stream did not recover automatically'))
            once('restart-dwell', 11.05, flow, lambda: interrupted('restart_dwell'))
            once('restart-no-leak', 11.50, flow, lambda: check_count(1, 'restart-no-leak'))
            once('restarted', 11.52, flow,
                 lambda: require(app.backend_pid == 7654322 and app.interaction_ready(),
                                 'New backend PID did not synchronize and recover'))
            if flow < 14.55:
                return True
            check_count(2, 'final-choice')
            require(all(item['mode'] == 'ink' for item in selections), 'Right-hand dwell chose the wrong card')
            require(not selections or selections[0]['at'] >= 5.85, 'Initial dwell completed before two seconds')
            require(len(selections) < 2 or selections[1]['at'] >= 14.45,
                    'Disconnected/frozen/restarted time leaked into the final dwell')
            require(observed['initial_ready'] is not None and observed['initial_ready'] >= 1.6,
                    'Pipeline became ready before advancing source frames')
            require(observed['countdown_started'] is not None and
                    observed['countdown_started'] >= observed['initial_ready'],
                    'Countdown started before readiness')
            require(any(c['accepted'] and .6 <= c['at'] < 1.6 for c in commands),
                    'Test never exercised accepted mode commands without camera frames')
            require(draws[0] > 10, 'GTK did not actually render the menu')
            require(set(requests) == {'/api/state'}, 'Unexpected route used, possibly camera imagery')
            app.destroy()
            return False
        except Exception:
            errors.append(traceback.format_exc())
            app.destroy()
            return False

    GLib.timeout_add(20, tick, priority=GLib.PRIORITY_DEFAULT)
    try:
        Gtk.main()
    finally:
        if not app.stop.is_set():
            app.destroy()
        server.shutdown()
        server.server_close()
    require(not state['suite']['active'], 'Closing the test failed to send mock backend shutdown')
    report = dict(passed=not errors, camera_used=False, production_used=False,
                  isolated_display=os.environ.get('DISPLAY'), real_http=True, real_gtk=True,
                  experience_processes_started=0, elapsed_seconds=time.monotonic()-started[0],
                  selections=selections, observations=observed, checkpoints=records,
                  screenshots=screenshots, gtk_draws=draws[0], http_gets=len(requests),
                  commands=commands, errors=errors)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--isolated-child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    return run(args) if args.isolated_child else isolated(args)


if __name__ == '__main__':
    raise SystemExit(main())
