#!/usr/bin/env python3
"""TheXTech episode inside InkSuite; owns no camera, inference or physical SDL pad."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from thextech.controls import BodyKeys, PadKeys
from thextech.runtime import NativeChild, ReportGame, read_config, DEFAULT_CONFIG
from game_source_policy import SourceGapPolicy


def emit(event, **fields):
    fields.setdefault('mode', 'platformer')
    try: print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)
    except (OSError, ValueError): pass


def main(argv=None):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('GdkX11', '3.0')
    from gi.repository import Gtk, GLib, GdkX11
    from action_dispatch import ActionDispatcher
    from thextech.x11 import EmbedWindow

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--embed-socket', type=int)
    p.add_argument('--base-url', default='http://127.0.0.1:8088')
    p.add_argument('--fullscreen', action='store_true')
    p.add_argument('--entry-delay', type=float, default=0)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--user-root', type=Path)
    p.add_argument('--test-level', type=Path)
    p.add_argument('--no-audio', action='store_true')
    p.add_argument('--offline-preview', action='store_true')
    args = p.parse_args(argv)
    if not 0 <= args.entry_delay <= 30: p.error('invalid entry delay')

    class Panel(Gtk.Box):
        def __init__(self):
            super().__init__(orientation=Gtk.Orientation.VERTICAL)
            self.active = not bool(args.embed_socket)
            self.stopped = self.ready = False
            self.control_mode = os.environ.get('INK_CONTROL_MODE', 'body')
            self.body, self.pad, self.reader = BodyKeys(), PadKeys(), None
            self.child = self.embed = None
            self.boot = 'preparing'
            self.bootstrap_ticket = self.operation_ticket = self.action_ticket = None
            self.action_done = None
            self.action_pause_ticket = None
            self.action_result = None
            self.muted = False
            self.peer_closed_since = None
            self.loss_since = self.good_since = None
            self.tracking_paused = False
            self.lock, self.stop = threading.Lock(), threading.Event()
            self.latest = None
            self.generation = 0
            self.started = time.monotonic()
            self.entry_until = self.started + args.entry_delay
            self.last_diagnostic = 0.
            self.size = (320, 240)
            self.report_game, self.reporter = ReportGame(), None
            self.canvas = Gtk.DrawingArea()
            self.canvas.set_hexpand(True); self.canvas.set_vexpand(True)
            self.canvas.set_size_request(320, 200)
            self.canvas.set_can_focus(False)
            self.pack_start(self.canvas, True, True, 0)
            self.notice = Gtk.Label(label='正在加载开源关卡…')
            self.notice.set_margin_top(4); self.notice.set_margin_bottom(4)
            self.notice.set_no_show_all(True)
            self.pack_start(self.notice, False, False, 0)
            self.canvas.connect('realize', lambda *_: GLib.idle_add(self.prepare))
            self.canvas.connect('size-allocate', self.resize)
            self.dispatcher = ActionDispatcher('platformer', self.native_paused, self.execute, emit)
            self.timer = GLib.timeout_add(20, self.tick)
            if not args.offline_preview:
                threading.Thread(target=self.poll_pose, name='thextech-latest-pose', daemon=True).start()

        def hint(self, message=''):
            self.notice.set_text(message); self.notice.set_visible(bool(message))

        def prepare(self):
            def work():
                config, sink, error = None, None, None
                try:
                    config = read_config(args.config)
                except Exception as exc: error = str(exc)
                if config and not args.no_audio:
                    try:
                        from connectivity.audio_device import inventory
                        from audio.menu_music import _select_cube
                        route = _select_cube(inventory(timeout=1.))
                        sink = route['sink'] if route else None
                    except Exception as exc:
                        emit('audio_diagnostic', engine='thextech', reason=str(exc), available=False)
                GLib.idle_add(self.launch, config, sink, error)
            threading.Thread(target=work, name='thextech-prepare', daemon=True).start()
            return False

        def launch(self, config, sink, error):
            if self.stopped: return False
            if error:
                self.fail(error); return False
            try:
                self.embed = EmbedWindow()
                self.child = NativeChild(config, no_audio=args.no_audio or not sink, user_root=args.user_root,
                                         test_level=args.test_level)
                self.child.peer.set_size(*self.size)
                self.child.start(sink)
                self.boot = 'window'
                if not args.offline_preview:
                    from visitor.reporter import GameReporter
                    self.reporter = GameReporter.from_environment('platformer')
                emit('thextech_launch', child_pid=self.child.process.pid, engine=config.get('version'),
                     content='Adventures of Demo', audio_sink=sink, private_input=True)
            except Exception as exc: self.fail(str(exc))
            return False

        def resize(self, widget, *_):
            width, height = widget.get_allocated_width(), widget.get_allocated_height()
            # Hidden Gtk.Stack pages have no final allocation. Bootstrap the
            # exact SDL window at a valid size, then resize the same process.
            size = (max(320, min(3840, width)), max(200, min(2160, height)))
            if size == self.size: return
            self.size = size
            try:
                if self.embed: self.embed.resize(size)
                if self.child: self.child.peer.set_size(*size)
            except Exception as exc: self.fail(str(exc))

        def poll_pose(self):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while not self.stop.is_set():
                with self.lock:
                    wanted = self.active and self.control_mode == 'body'
                    generation = self.generation
                if not wanted: self.stop.wait(.1); continue
                packet = None
                try:
                    with opener.open(args.base_url + '/api/state', timeout=.35) as response:
                        raw = response.read(262145)
                    if len(raw) <= 262144:
                        value = json.loads(raw)
                        if isinstance(value, dict): packet = value
                except (OSError, ValueError): pass
                with self.lock:
                    if generation == self.generation and self.active: self.latest = packet
                self.stop.wait(.025)

        def clear(self):
            self.body.reset(); self.pad.reset()
            self.generation += 1
            self.loss_since = self.good_since = None
            with self.lock: self.latest = None
            if self.child and not self.child.peer.closed: self.child.peer.set_input(0)

        def native_paused(self):
            state = self.child.peer.state if self.child else None
            return (not self.active and self.child and self.child.peer.healthy and state and
                    state.get('paused') and state.get('audio_paused') and not state.get('held_mask'))

        def dispatch_request(self, request, deadline=None):
            if self.stopped: return False
            deadline = time.monotonic() + 2.5 if deadline is None else deadline
            if self.native_paused() or time.monotonic() >= deadline:
                return self.dispatcher.dispatch(request)
            GLib.timeout_add(20, self.dispatch_request, request, deadline)
            return False

        def mode(self, mode):
            if mode not in ('body', 'gamepad') or self.stopped: return False
            self.clear(); self.control_mode = mode
            self.tracking_paused = False
            if self.reader: self.reader.close(); self.reader = None
            self.hint()
            if self.child and self.ready:
                self.operation_ticket = self.child.peer.pause() if not self.active else self.child.peer.run(0)
            emit('control_mode_changed', control_mode=mode)
            return False

        def operation(self, op):
            if op == 'shutdown':
                if self.stopped: return False
                self.stopped = True; self.active = False; self.stop.set()
                self.clear()
                if self.action_done:
                    done, self.action_done = self.action_done, None
                    done(False, '游戏已退出')
                try:
                    if self.child: self.child.close()
                finally:
                    for cleanup in (self.embed.close if self.embed else None,
                                    self.reader.close if self.reader else None,
                                    (lambda: self.reporter.close(self.report_game)) if self.reporter else None):
                        if cleanup:
                            try: cleanup()
                            except Exception as exc: emit('cleanup_error', reason=str(exc))
                    self.reader = None
                    if Gtk.main_level(): Gtk.main_quit()
                return False
            if self.stopped or op not in ('pause', 'resume'): return False
            self.active = op == 'resume'; self.clear()
            self.tracking_paused = False
            if self.child and self.ready:
                self.child.peer.set_mute(self.muted or not self.active)
                self.operation_ticket = self.child.peer.run(0) if self.active else self.child.peer.pause()
            return False

        def execute(self, action, done):
            if action in ('pause', 'continue'): done(True); return
            if not self.child or not self.ready: done(False, '游戏尚未加载'); return
            if action in ('start', 'restart'):
                self.clear()
                self.child.peer.set_mute(True)
                self.action_ticket = self.child.peer.restart(timeout=8.)
                self.action_done = done
                return
            if action == 'audio_retry':
                done(False, '请返回主菜单后重新进入游戏，重新连接原生音频'); return
            if action == 'audio_unmute' and self.child.no_audio and not args.no_audio:
                done(False, '启动时音响未就绪，请连接后重新进入游戏'); return
            if action in ('audio_mute', 'audio_unmute'):
                self.muted = action == 'audio_mute'
                self.child.peer.set_mute(True)  # still in the host's paused menu
                done(True, audio_muted=self.muted); return
            done(False, '此引擎仅支持整体静音和恢复声音')

        def fail(self, reason):
            emit('error', engine='thextech', reason=reason)
            self.hint('开源关卡无法运行：' + reason[:180])
            self.operation('shutdown')

        def keys(self, now, state):
            # The world map activates a level through Player.Controls.Jump.
            # Only native menus/messages consume SharedControls.MenuDo.
            menu = bool(state.get('native_ui')) or state.get('phase') in (1, 4)
            if args.offline_preview: return dict(valid=True, mask=0, reason='offline')
            if self.control_mode == 'gamepad':
                if not self.reader:
                    from gamepad.device import GamepadReader
                    self.reader = GamepadReader()
                return self.pad.update(self.reader.snapshot(), now, menu=menu)
            with self.lock: packet = self.latest
            if isinstance(packet, dict) and ((packet.get('suite') or {}).get('menu') or (packet.get('suite') or {}).get('control_active')):
                return dict(valid=False, mask=0, reason='menu_gesture')
            return self.body.update(packet, now, menu=menu)

        def tick(self):
            if self.stopped: return False
            now = time.monotonic()
            child = self.child
            if child is None:
                if now - self.started > 15: self.fail('启动准备超时')
                return not self.stopped
            if child.process.poll() is not None:
                if child.process.returncode == 0 and self.ready:
                    emit('thextech_exited', returncode=0, clean=True)
                    self.operation('shutdown')
                else: self.fail('引擎已退出，代码 ' + str(child.process.returncode))
                return False
            try:
                child.peer.tick()
                state = child.peer.state or {}
                if child.peer.closed:
                    # Save-and-Quit closes the socket before the process can
                    # become waitable. Let the normal exit branch observe its
                    # status without blocking GTK or calling it a crash.
                    self.peer_closed_since = self.peer_closed_since or now
                    if now - self.peer_closed_since < .3:
                        return True
                    raise RuntimeError('原生控制连接已断开')
                if self.bootstrap_ticket and self.bootstrap_ticket.status in ('error', 'timeout'):
                    raise RuntimeError('窗口初始化确认失败')
                if self.boot == 'window' and state.get('xid'):
                    self.canvas.get_display().sync()
                    self.embed.attach(state['xid'], int(self.canvas.get_window().get_xid()),
                                      child.process.pid, self.size)
                    self.bootstrap_ticket = child.peer.run(0, timeout=25.)
                    self.boot = 'first_frame'
                if self.boot == 'first_frame' and state.get('ready'):
                    self.bootstrap_ticket = child.peer.pause()
                    self.boot = 'paused'
                if self.boot == 'paused' and self.bootstrap_ticket.status == 'applied' and state.get('paused'):
                    self.boot = 'ready'; self.ready = True; self.hint()
                    emit('ready', engine='thextech', native_sdl=True, child_xid=state['xid'])
                    if self.active: self.operation('resume')
                if not self.ready:
                    if now - child.started > 38: self.fail('原生窗口或首帧超时')
                    return not self.stopped
                if self.operation_ticket and self.operation_ticket.status == 'applied':
                    emit('resumed' if self.active else 'paused', game_state=state.get('phase'),
                         native_audio_paused=state.get('audio_paused'), engine='thextech')
                    self.operation_ticket = None
                if self.operation_ticket and self.operation_ticket.status in ('error', 'timeout'):
                    raise RuntimeError('暂停或恢复未得到引擎确认')
                if self.action_ticket and self.action_ticket.status != 'pending' and not self.action_pause_ticket:
                    ticket, done = self.action_ticket, self.action_done
                    ok = ticket.status == 'completed'
                    self.action_result = (ok, '' if ok else '当前场景不支持重开' if ticket.error == 'wrong_phase' else '关卡重开未完成')
                    self.action_pause_ticket = child.peer.pause()
                if self.action_pause_ticket and self.action_pause_ticket.done:
                    ok, reason = self.action_result
                    if not self.action_pause_ticket.success: ok, reason = False, '关卡重开后未确认暂停'
                    done = self.action_done
                    self.action_ticket = self.action_done = self.action_pause_ticket = self.action_result = None
                    if ok:
                        if self.reporter: self.reporter.reset()
                        self.report_game.reset_round(child.peer.telemetry)
                    done(ok, reason, native_restart=state.get('restarts_completed'), engine='thextech')
                if self.active and self.action_ticket is None and now >= self.entry_until:
                    keys = self.keys(now, state)
                    if self.control_mode=='gamepad':source_blocked=not keys['valid']
                    else:
                        if not hasattr(self,'source_gap'):self.source_gap=SourceGapPolicy()
                        with self.lock:source_packet=self.latest
                        source_blocked=self.source_gap.update(source_packet,now,offline=args.offline_preview)
                    if not source_blocked:
                        self.loss_since = None
                        if self.tracking_paused:
                            child.peer.run(0); self.tracking_paused = False; self.hint()
                        child.peer.set_input(keys['mask'] if keys['valid'] else 0)
                    else:
                        child.peer.set_input(0)
                        self.good_since = None
                        self.loss_since = self.loss_since or now
                        delay = .1 if self.control_mode == 'gamepad' else 0.
                        if now - self.loss_since >= delay and not self.tracking_paused:
                            self.tracking_paused = True; child.peer.pause()
                            self.hint('手柄未连接，连接后松开按键继续' if self.control_mode == 'gamepad' else
                                      '追踪暂停，请让肩部和手臂完整入镜')
                    child.peer.set_mute(self.muted or self.tracking_paused)
                if self.reporter:
                    previous_phase = self.report_game.state
                    self.report_game.update(child.peer.telemetry)
                    if previous_phase == 'playing' and self.report_game.state == 'ready':
                        self.reporter.reset()
                        self.report_game.reset_round(child.peer.telemetry)
                    self.reporter.observe(self.report_game, running=self.active and not state.get('paused') and state.get('ready'))
                if now - self.last_diagnostic >= 2:
                    self.last_diagnostic = now
                    emit('thextech_diagnostic', native=state, counters=child.peer.telemetry)
            except Exception as exc: self.fail(str(exc))
            return not self.stopped

    panel = Panel()
    win = Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='超级玛丽 · TheXTech')
    # A Gtk.Plug's default size becomes a minimum request at the host Socket.
    # Let the embedding host choose its allocation instead of pinning 960x540.
    win.set_default_size(*((320, 200) if args.embed_socket else (960, 540))); win.add(panel)
    win.connect('destroy', lambda *_: panel.operation('shutdown'))
    def commands():
        try:
            for line in sys.stdin:
                try: request = json.loads(line)
                except ValueError: continue
                if not isinstance(request, dict): continue
                if request.get('op') == 'action': GLib.idle_add(panel.dispatch_request, request)
                elif request.get('op') in ('pause', 'resume', 'shutdown'):
                    GLib.idle_add(panel.operation, request['op'])
                elif request.get('op') == 'control_mode': GLib.idle_add(panel.mode, request.get('mode'))
        except OSError: pass
        GLib.idle_add(panel.operation, 'shutdown')
    if args.embed_socket: threading.Thread(target=commands, name='thextech-host-commands', daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(panel.operation, 'shutdown'))
    signal.signal(signal.SIGINT, lambda *_: GLib.idle_add(panel.operation, 'shutdown'))
    win.show_all(); panel.notice.hide()
    if args.fullscreen and not args.embed_socket: win.fullscreen()
    try: Gtk.main()
    finally: panel.operation('shutdown')
    return 0


if __name__ == '__main__': raise SystemExit(main())
