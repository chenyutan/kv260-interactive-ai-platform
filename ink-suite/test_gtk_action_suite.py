"""Real five-child GTK action navigation, isolated Xvfb + loopback fake API.

Run on the board's Python/GTK environment, never the visible desktop::

    timeout 245s xvfb-run -a -s '-screen 0 1280x800x24' python3 \
      test_gtk_action_suite.py --output test-evidence/gtk-actions.json

All real children receive --offline-preview; rhythm receives fakesink. The
gesture test injects only synthetic joints into a random-port loopback server.
No camera, real artwork API, or live game instance is accessed.
"""
import argparse
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='test-evidence/gtk-action-suite-result.json')
    p.add_argument('--timeout', type=float, default=230.)
    p.add_argument('--ink-source', default='/home/ubuntu/bodyink-live/native_display.py')
    p.add_argument('--fight-source', default='/home/ubuntu/ink-fight/native/main_v2.py')
    p.add_argument('--fruit-source', default='/home/ubuntu/fruit-slice/native/main.py')
    p.add_argument('--modes', default='ink,fight,fruit,sword,taiko,platformer')
    a = p.parse_args()
    if not 30 <= a.timeout <= 240:
        p.error('--timeout must be between 30 and 240 seconds')
    return a


class FakeBackend:
    def __init__(self):
        from suite_control import SuiteControl
        self.validator = SuiteControl
        self.lock = threading.Lock()
        self.paths = set()
        self.posts = []
        self.errors = []
        self.state = dict(pid=999991, sequence=0, timestamp_seconds=0., points17=None,
            suite=dict(active=True, mode='ink', menu=True, control_active=False,
                       open_serial=0, hand=None))
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with owner.lock:
                    owner.paths.add(self.path)
                    if self.path != '/api/state':
                        owner.errors.append('Unexpected GET ' + self.path)
                        self.send_error(404)
                        return
                    owner.state['sequence'] += 1
                    owner.state['timestamp_seconds'] = time.monotonic()
                    payload = json.loads(json.dumps(owner.state))
                self.reply(payload)

            def do_POST(self):
                try:
                    data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.path != '/api/command':
                        raise ValueError('Unexpected POST ' + self.path)
                    with owner.lock:
                        owner.posts.append(dict(time=time.monotonic(), data=dict(data)))
                        command = data.pop('command')
                        if command == 'suite':
                            validated=owner.validator().configure(data)
                            owner.state['suite'].update(data)
                            owner.state['suite']['preview']=validated['preview']
                            result = dict(accepted=True)
                        elif command in ('save', 'clear', 'undo'):
                            # Only the explicit in-process receipt probe below
                            # may make these requests. No filesystem work.
                            result = dict(accepted=True, reason='fake_receipt',
                                          path='mock-artwork.png' if command == 'save' else '')
                        else:
                            raise ValueError('Unexpected command ' + str(command))
                    self.reply(result)
                except Exception:
                    owner.errors.append(traceback.format_exc())
                    self.send_error(400)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def points(self, value):
        with self.lock:
            self.state['points17'] = value

    def circle(self):
        """Inject the recognizer's completed-circle signal, not fake host UI."""
        with self.lock:
            suite = self.state['suite']
            suite.update(menu=True, hand='right', control_active=False)
            suite['open_serial'] += 1
            return suite['open_serial']


def synthetic_pose(cursor=(.5, .10)):
    """Inverse of the production shoulder-normalized right-wrist mapping."""
    width = .1 * (16 / 9)
    points = [[.5, .5, .9] for _ in range(17)]
    points[5], points[6] = [.45, .35, .9], [.55, .35, .9]
    points[10] = [.5 + (.5 - cursor[0]) * width * 3.2 / (16 / 9),
                  .35 + (cursor[1] - .35) * width * 2.6, .9]
    return points


def run(args):
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    from main import Suite
    from gesture_menu import MODES, ZONES
    from menu_view import VISITOR_CHOICES, VISITOR_RECTS
    from action_catalog import page

    modes = tuple(args.modes.split(','))
    assert modes and len(set(modes)) == len(modes) and set(modes) <= set(MODES), modes
    backend = FakeBackend()
    started = time.monotonic()
    deadline = started + args.timeout
    evidence = dict(passed=False, camera_used=False, fake_base=backend.base,
        modes=list(modes), records=[], events=[], ipc=[], navigation=[],
        mock_ink_receipts=[], errors=[], coverage=dict())
    processes = []
    context = dict(mode=None, stage='initialize')
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    class LoggedInput:
        def __init__(self, stream, child, mode):
            self.stream, self.child, self.mode = stream, child, mode

        def write(self, data):
            for line in data.splitlines():
                try:
                    packet = json.loads(line)
                except ValueError:
                    packet = dict(raw=line)
                evidence['ipc'].append(dict(time=time.monotonic(), pid=self.child.pid,
                                             mode=self.mode, packet=packet))
            return self.stream.write(data)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    class TestedSuite(Suite):
        def spawn_mode(self, mode, generation):
            result = super().spawn_mode(mode, generation)
            if self.child and self.child not in processes:
                processes.append(self.child)
                self.child.stdin = LoggedInput(self.child.stdin, self.child, mode)
            return result

        def child_event(self, child, mode, generation, event):
            evidence['events'].append(dict(time=time.monotonic(), pid=child.pid,
                mode=mode, generation=generation, event=dict(event)))
            return super().child_event(child, mode, generation, event)

        def choose_action(self, action):
            evidence['navigation'].append(dict(time=time.monotonic(), mode=self.mode,
                page=self.action_page, action=action, via_dwell=bool(self.selector.done),
                choices=list(self.selector.choices)))
            return super().choose_action(action)

    app = TestedSuite(SimpleNamespace(base=backend.base, fullscreen=False,
        offline_preview=True, entry_delay=0., ink_source=args.ink_source,
        fight_source=args.fight_source, fruit_source=args.fruit_source))
    app.resize(1024, 600)

    def wait(predicate, label, timeout=12.):
        context['stage'] = label
        until = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= until:
                raise TimeoutError(label)
            yield

    def delay(seconds, label):
        until = time.monotonic() + seconds
        yield from wait(lambda: time.monotonic() >= until, label, seconds + 2)

    def events(kind, after=0.):
        pid = app.child.pid if app.child else None
        return [e for e in evidence['events'] if e['pid'] == pid and
                e['time'] >= after and e['event'].get('event') == kind]

    def sent_actions():
        pid = app.child.pid if app.child else None
        return [e for e in evidence['ipc'] if e['pid'] == pid and
                e['packet'].get('op') == 'action']

    def only_main_visible():
        assert app.stack.get_visible_child_name() == 'menu'
        assert app.menu.timer is not None and app.actions.timer is None
        assert VISITOR_CHOICES == ('visitor_download', 'visitor_finish')
        assert len(MODES) == 6 and len(VISITOR_RECTS) == 2
        from radial_layout import rectangles
        assert app.menu.mode_choices == MODES + ('pose_view',) + VISITOR_CHOICES
        assert app.menu.rectangles == rectangles(app.menu.get_allocated_width(),app.menu.get_allocated_height(),True)
        assert app.selector.hit_test == app.menu.hit_index
        assert app.selector.choices == app.menu.mode_choices
        assert app.selector.zones == app.menu.rectangles

    def open_actions():
        backend.points(None)
        mark = time.monotonic()
        serial = backend.circle()
        yield from wait(lambda: app.menu_open and app.menu_level == 'actions' and
                        app.action_page == 'root' and app.serial == serial and
                        app.interaction_ready() and
                        bool(events('paused', mark)), 'circle opens actions')
        assert app.stack.get_visible_child_name() == 'actions'
        assert app.actions.timer is not None and app.menu.timer is None
        assert app.child_paused
        return events('paused', mark)[-1]

    def choose(action):
        assert app.interaction_ready(), app.pipeline.reason
        assert action in [x['id'] for x in page(app.mode, app.action_page)['items']], (app.mode, app.action_page, action)
        app.choose_action(action)

    def dwell(action, destination):
        index = app.selector.choices.index(action)
        zone = ZONES[index]
        center = ((zone[0] + zone[2]) / 2, (zone[1] + zone[3]) / 2)
        backend.points(synthetic_pose())
        yield from delay(.65, 'dwell neutral arm')
        # The real selector must observe movement, fresh sequences and its full
        # two-second dwell. Neither its clock nor seconds is replaced.
        backend.points(synthetic_pose(center))
        yield from wait(lambda: app.action_page == destination,
                        'observed-pose dwell ' + action, 7.)
        backend.points(None)
        entries = [e for e in evidence['navigation'] if e['mode'] == app.mode and
                   e['action'] == action and e['via_dwell']]
        assert entries, ('Selection bypassed actual DwellSelector', action)

    def confirm_action(action, success=True, options=False):
        if options:
            choose('options')
            assert app.action_page == 'options'
            if app.mode == 'taiko':
                if action.startswith(('difficulty_', 'judgment_')):
                    choose('taiko_difficulty')
                    assert app.action_page == 'taiko_difficulty'
                elif action.startswith('practice_'):
                    choose('taiko_practice')
                    assert app.action_page == 'taiko_practice'
        before = len(sent_actions())
        choose(action)
        assert app.action_page == 'confirm:' + action
        assert app.pending_action is None and len(sent_actions()) == before
        yield from delay(.7, 'confirmation awaits deliberate choice: ' + action)
        assert app.pending_action is None and len(sent_actions()) == before
        mark = time.monotonic()
        choose('confirm:' + action)
        assert len(sent_actions()) == before + 1
        identifier = sent_actions()[-1]['packet']['command_id']
        yield from wait(lambda: any(e['event'].get('command_id') == identifier
                                   for e in events('action_result', mark)),
                        'acknowledge ' + action)
        receipts = [e for e in events('action_result', mark)
                    if e['event'].get('command_id') == identifier]
        assert len(receipts) == 1 and receipts[0]['event'].get('ok') is success, receipts
        assert len(sent_actions()) == before + 1, 'Duplicate command after ACK'
        if success:
            yield from wait(lambda: not app.menu_open and app.child_paused is False and
                            bool(events('resumed', mark)), 'resume after ' + action)
            assert app.stack.get_visible_child_name() == 'experience'
            assert app.actions.timer is None and app.menu.timer is None
        else:
            yield from wait(lambda: app.action_page == 'root' and app.pending_action is None,
                            'rejected action stays on menu')
            assert app.menu_open and app.child_paused
        evidence['records'][-1]['actions'].append(dict(action=action,
            command_id=identifier, receipt=receipts[0]['event'], single_send=True,
            explicit_confirmation=True, resumed=success))
        return receipts[0]['event']

    def ink_no_write():
        before = len(sent_actions())
        mark = time.monotonic()
        choose('save')
        yield from wait(lambda: bool(events('action_result', mark)), 'offline ink rejects save')
        receipt = events('action_result', mark)[-1]['event']
        assert receipt['action'] == 'save' and receipt['ok'] is False, receipt
        assert receipt['reason'] == 'offline_preview_no_artwork_changes', receipt
        assert len(sent_actions()) == before + 1
        assert app.menu_open and app.child_paused
        evidence['records'][-1]['actions'].append(dict(action='save', receipt=receipt,
                                                      expected_offline_rejection=True))
        yield from confirm_action('new_scroll', success=False)

    def mock_artwork_receipts():
        # Real Controller.ink_action transport and real ActionDispatcher ACK;
        # only its endpoint and visual shell are fake. Separate from five actual
        # children, which never receive permission to mutate artwork.
        from embedded_runner import Controller
        from action_dispatch import ActionDispatcher
        controller = Controller.__new__(Controller)
        controller.args = SimpleNamespace(pose_url=backend.base + '/api/state', offline_preview=False)
        controller.closed = False
        controller._share_epoch = 0
        controller.visitor_ink = None  # match __init__; this isolated probe has no visitor store
        controller.app = SimpleNamespace(publish=lambda *a: None)
        probe_events = []
        dispatcher = ActionDispatcher('ink', lambda: True, controller.ink_action,
            lambda event, **data: probe_events.append(dict(event=event, **data)))
        for action, command in [('save', 'save'), ('new_scroll', 'clear')]:
            identifier = 'mock-ink-' + action
            request = dict(op='action', command_id=identifier, action=action)
            before = len([p for p in backend.posts if p['data'].get('command') == command])
            dispatcher.dispatch(request)
            yield from wait(lambda: any(e['command_id'] == identifier for e in probe_events),
                            'mock artwork receipt ' + action)
            receipt = [e for e in probe_events if e['command_id'] == identifier][-1]
            assert receipt['ok'] is True and receipt['reason'] == 'fake_receipt', receipt
            dispatcher.dispatch(request)  # cached ACK, not a repeated save
            yield from delay(.15, 'mock duplicate receipt is idempotent')
            assert len([p for p in backend.posts if p['data'].get('command') == command]) == before + 1
            evidence['mock_ink_receipts'].append(dict(receipt=receipt, backend_writes=1,
                                                     duplicate_replayed=True))

    def scenario():
        yield from wait(app.interaction_ready, 'initial synthetic stream readiness')
        # Offline construction skips visitor setup; enter the normal main-menu
        # path so the same five modes and two footer controls are checked at
        # startup and after every child returns home.
        app.open_menu(main=True)
        yield from wait(app.interaction_ready, 'initial main menu readiness')
        only_main_visible()
        # Central tool is not a sixth game: no child starts and ink stays paused.
        app.select('pose_view')
        yield from wait(lambda:app.interaction_ready() and app.menu_level=='pose','central camera tool ownership')
        assert app.stack.get_visible_child_name()=='pose' and app.child is None
        assert backend.state['suite'].get('preview') is True and backend.state['suite']['menu'] is True
        app.open_menu(main=True)
        yield from wait(app.interaction_ready,'return from central camera tool')
        # The real backend clears preview on every ordinary suite command.
        assert not app.pose_view.stop_event
        assert backend.state['suite'].get('preview') is False
        only_main_visible()
        evidence['coverage']['central_camera_tool']=True
        for mode in modes:
            context['mode'] = mode
            mode_start = time.monotonic()
            app.select(mode)
            yield from wait(lambda: not app.switching and app.child is not None and
                            app.mode == mode and not app.menu_open and
                            bool(events('resumed', mode_start)), 'launch ' + mode, 24.)
            assert app.child.poll() is None and app.socket.get_plug_window() is not None
            command = Path('/proc', str(app.child.pid), 'cmdline').read_bytes().split(b'\0')
            assert b'--offline-preview' in command, command
            if mode == 'taiko':
                assert b'--audio-sink' in command and b'fakesink' in command, command
            record = dict(mode=mode, pid=app.child.pid, actions=[], embedded=True,
                          command=[p.decode(errors='replace') for p in command if p])
            evidence['records'].append(record)
            yield from open_actions()
            if mode == 'ink':
                yield from ink_no_write()
            else:
                yield from confirm_action('restart')
                # Leave enough time for real countdowns and audio startup. The
                # following frozen-clock probe must run in an advanced round.
                # Sword preserve both the three-second re-entry delay and
                # the three-second game countdown. Do not mistake time=0 in
                # their countdown for a successful playing-clock freeze.
                yield from delay(7.3 if mode == 'sword' else 4.7,
                                 'advance restarted round ' + mode)
                yield from open_actions()

            paused = events('paused')[-1]
            if mode in ('ink', 'taiko'):
                choose('options')
                assert app.action_page == 'options'
            if mode == 'fight':
                yield from dwell('help', 'help')
                yield from dwell('back', 'root')
                record['help_via_real_dwell'] = True
            else:
                choose('help')
                assert app.action_page == 'help'
                yield from delay(.8, 'help page holds ' + mode)
                choose('back')
            assert app.action_page == 'root'
            before = len(sent_actions())
            if mode != 'ink':
                choose('restart')
                assert app.action_page == 'confirm:restart'
                yield from delay(.8, 'cancelled restart confirmation ' + mode)
                assert app.pending_action is None and len(sent_actions()) == before
                choose('back')
            assert len(sent_actions()) == before, 'Help/cancel sent a game mutation'
            mark = time.monotonic()
            choose('resume')
            yield from wait(lambda: not app.menu_open and bool(events('resumed', mark)),
                            'resume unchanged help and cancelled-confirm round')
            resumed = events('resumed', mark)[-1]
            if mode != 'ink':
                start_clock, end_clock = paused['event']['game_clock'], resumed['event']['game_clock']
                assert start_clock > 0., ('Round never advanced before freeze probe', paused)
                assert abs(end_clock - start_clock) < .05, (paused, resumed)
                record['clock_freeze'] = dict(paused=paused['event'], resumed=resumed['event'],
                    wall_seconds=resumed['time'] - paused['time'], tolerance_seconds=.05)
            # The resume card deliberately uses a child 'continue' ACK so a
            # game's own help/pause state is also cleared before host resume.
            assert len(sent_actions()) == before + 1
            resume_request = sent_actions()[-1]['packet']
            assert resume_request['action'] == 'continue', resume_request
            resume_receipts = [e['event'] for e in events('action_result', mark)
                               if e['event'].get('command_id') == resume_request['command_id']]
            assert len(resume_receipts) == 1 and resume_receipts[0]['ok'] is True, resume_receipts
            assert app.actions.timer is None and app.menu.timer is None
            record['continue_receipt'] = resume_receipts[0]
            record['help_and_cancel_do_not_send_action'] = True

            if mode == 'taiko':
                yield from open_actions()
                choose('options')
                yield from dwell('taiko_practice', 'taiko_practice')
                before = len(sent_actions())
                yield from dwell('practice_alternate', 'confirm:practice_alternate')
                assert len(sent_actions()) == before and app.pending_action is None
                choose('back')
                choose('resume')
                yield from wait(lambda: not app.menu_open, 'cancel practice confirmation and resume')
                record['practice_via_real_dwell'] = True
                for action in ('silent', 'difficulty_normal', 'difficulty_hard', 'practice_alternate', 'practice_full'):
                    yield from open_actions()
                    receipt = yield from confirm_action(action, options=True)
                    if action.startswith('difficulty_'):
                        assert receipt['result']['difficulty'] == action.removeprefix('difficulty_'), receipt
                    if action.startswith('practice_'):
                        assert receipt['result']['practice'] == action.removeprefix('practice_'), receipt
                    assert receipt['result']['game_state'] == 'paused', receipt
            yield from open_actions()
            choose('home')
            yield from delay(.25, 'return five-experience menu ' + mode)
            only_main_visible()
            assert app.menu_open and app.menu_level == 'main' and app.child_paused
            record['home_and_hidden_timer_verified'] = True
            record['elapsed_seconds'] = time.monotonic() - mode_start
            print(json.dumps(dict(mode=mode, passed=True, elapsed=record['elapsed_seconds'])), flush=True)

        assert not [p for p in backend.posts if p['data'].get('command') != 'suite'], 'A real offline child mutated artwork'
        yield from mock_artwork_receipts()
        assert not backend.errors, backend.errors
        assert backend.paths == {'/api/state'}, backend.paths
        if 'fight' in modes:
            assert evidence['records'][modes.index('fight')]['help_via_real_dwell']
        identifiers = [e['packet']['command_id'] for e in evidence['ipc'] if e['packet'].get('op') == 'action']
        assert len(identifiers) == len(set(identifiers)), identifiers
        evidence['coverage'] = dict(completed_circle_signal=True, real_dwell_help_back='fight' in modes,
            restart_confirmation=True, cancelled_confirmation_no_command=True,
            clocks_frozen_across_help_and_cancel=True, five_menu_return=True,
            hidden_menu_timers_stop=True, ink_mutations_only_fake_backend=True,
            note='Completed-circle backend signal injected; circle recognition itself is not tested.')
        evidence['passed'] = True

    procedure = scenario()

    def tick():
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError('Absolute integration deadline reached: ' + str(context))
            next(procedure)
            return True
        except StopIteration:
            app.destroy()
            return False
        except Exception:
            evidence['errors'].append(traceback.format_exc())
            evidence['failure_context'] = dict(context, pending_action=str(app.pending_action),
                page=app.action_page, level=app.menu_level, pipeline=app.pipeline.reason,
                menu_open=app.menu_open, child_paused=app.child_paused)
            app.destroy()
            return False

    GLib.timeout_add(50, tick)
    try:
        Gtk.main()
    finally:
        if not app.stop.is_set():
            app.destroy()
        # Own subprocesses only. This never enumerates/kills the running app.
        for child in processes:
            if child.poll() is None:
                Suite.stop_process(child)
        backend.server.shutdown()
        backend.server.server_close()
        evidence['orphan_pids'] = [p.pid for p in processes if p.poll() is None]
        evidence['backend_active_after_close'] = backend.state['suite']['active']
        if evidence['orphan_pids'] or evidence['backend_active_after_close']:
            evidence['errors'].append('Child or fake backend lifecycle did not stop')
        evidence['passed'] = evidence['passed'] and not evidence['errors']
        evidence['elapsed_seconds'] = time.monotonic() - started
        evidence['backend_posts'] = backend.posts
        evidence['request_paths'] = sorted(backend.paths)
        evidence['backend_errors'] = backend.errors
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=evidence['passed'], output=str(output),
                          elapsed=evidence['elapsed_seconds'])), flush=True)
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(run(arguments()))
