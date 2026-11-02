#!/usr/bin/env python3
"""Native sword game: one shared COCO17 API, never a camera or image model.

The GLib thread owns input, game and rendering. A single polling worker replaces
one latest-state slot; it cannot queue old poses or publish across a pause.
--offline-preview uses explicit synthetic poses and optionally a mouse.
"""
import argparse
from collections import deque
import json
import math
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.request
import cairo

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from action_dispatch import ActionDispatcher
from gamepad.adapters import create_gamepad
from toolbar_autohide import hide_embedded_toolbar
from audio.integration import ExperienceAudio
from audio.sword_output import SwordAudioDirector
from sword.engine import Game, MAX_DT
from sword.input import InputTracker
from sword.renderer import Renderer
from sword.rgb565 import RGB565Presenter
from sword.metrics import LatestMetricsWriter
from sword.timeline import Timeline
from game_source_policy import SourceGapPolicy

START_RECT = (.35, .60, .65, .74)
DWELL_SECONDS = 1.2
MAX_SAMPLE_GAP = .24
RECOVER_AFTER = .60
RESUME_SECONDS = 3.
CELEBRATION_SECONDS = 2.2


def emit(event, **fields):
    try:
        print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)
    except (BrokenPipeError, OSError):
        pass


def in_start_region(x, y):
    left, top, right, bottom = START_RECT
    return left <= x <= right and top <= y <= bottom


def uses_rgb565(visual):
    """Match the actual window visual, never assume all future screens are 16-bit."""
    return bool(visual is not None and visual.get_depth()==16 and
        tuple(visual.get_red_pixel_details())==(0xf800,11,5) and
        tuple(visual.get_green_pixel_details())==(0x07e0,5,6) and
        tuple(visual.get_blue_pixel_details())==(0x001f,0,5))


def target_uses_rgb565(target, window_is_rgb565):
    """Respect GTK's actual paint buffer before considering its X11 visual.

    GDK_RENDERING=image can give a 16-bit window a 32-bit RAM paint buffer.
    Reducing our RGB24 frame there would need a second conversion back to
    RGB24 before GTK ultimately presents it to X11, losing color needlessly.
    """
    if isinstance(target, cairo.ImageSurface):
        return target.get_format() == cairo.FORMAT_RGB16_565
    return bool(window_is_rgb565)


def demo_state(now, pointer=None):
    """Synthetic skeleton, including inverse body-relative hand mapping."""
    x, y = pointer or (.50 + .29*math.sin(now*.55), .39 + .14*math.sin(now*.83))
    shoulder = .18 * (16/9)
    wrist_x = .59 + (.5-x)*shoulder*1.6/(16/9)
    wrist_y = .36 + (y-.35)*shoulder*2.6
    points = [[.5, .5, 0.] for _ in range(17)]
    values = {0:(.5,.20), 5:(.41,.36), 6:(.59,.36),
              7:(.31,.52), 8:((.59+wrist_x)/2,(.36+wrist_y)/2),
              9:(.30,.67), 10:(wrist_x,wrist_y),
              11:(.44,.66), 12:(.56,.66), 13:(.44,.80),
              14:(.56,.80), 15:(.43,.96), 16:(.57,.96)}
    for index, value in values.items():
        points[index] = [*value, .98]
    return dict(pid='sword-offline-synthetic', sequence=int(now*1000),
                timestamp_seconds=now, points17=points)


class FreshDwell:
    """Only new valid observations contribute; a replay cannot confirm a menu."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.key = None
        self.last_at = None
        self.elapsed = 0.

    @property
    def progress(self):
        return min(1., self.elapsed / DWELL_SECONDS)

    def update(self, control, now, enabled=True):
        if not enabled or control.get('continuity_reset') or not control.get('valid') or control.get('status') != 'tracking' or not in_start_region(
                control.get('x', -1), control.get('y', -1)):
            self.reset()
            return False
        key = control.get('source_key')
        if key is None:
            self.reset()
            return False
        if key == self.key:
            if self.last_at is not None and now-self.last_at > MAX_SAMPLE_GAP:
                self.elapsed = 0.
            return False
        if self.last_at is not None and 0 <= now-self.last_at <= MAX_SAMPLE_GAP:
            self.elapsed += now-self.last_at
        else:
            self.elapsed = 0.
        self.key, self.last_at = key, now
        if self.elapsed >= DWELL_SECONDS:
            self.reset()
            return True
        return False


class SwordPanel(Gtk.Box):
    def __init__(self, args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.args = args
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.command_lock = threading.Lock()
        self.command_pending = False
        self.command_op = None
        self.active = not bool(args.embed_socket)
        self.generation = 0
        self.latest = None
        self.current_state = None
        self.poll_thread = None
        self.tracker = InputTracker()
        self.gamepad = create_gamepad('sword', disabled=args.offline_preview)
        self.game = Game(seed=260, duration=75)
        self.game.pause(True)
        audio_enabled = not args.offline_preview
        self.sound = ExperienceAudio('sword', enabled=audio_enabled,
            director=SwordAudioDirector(enabled=audio_enabled))
        self.sound.bind(self)
        if self.active:self.sound.resume()
        self.renderer = Renderer()
        self.presenter = RGB565Presenter()
        self.presentation_visual = None
        self.presentation_visual_is_rgb565 = False
        self.presentation_is_rgb565 = False
        self.presentation_target_format = None
        self.dwell = FreshDwell()
        self.control = dict(x=.5, y=.5, valid=False, special=False,
                            source_key=None, status='waiting')
        now = time.monotonic()
        self.last_tick = now
        self.last_fresh = None
        self.last_key = None
        self.invalid_since = None
        self.recovery_needed = False
        self.recovery_until = 0.
        self.end_started = None
        self.end_paused_at = None
        self.entry_until = now + max(0., args.entry_delay) if self.active else 0.
        self.mouse = None
        self.notice = ''
        self.fatal_error = None
        self.frames_drawn = 0
        self.backbuffer = None
        self.draw_samples = deque(maxlen=600)
        self.draw_stamps = deque(maxlen=600)
        self.tick_samples = deque(maxlen=600)
        self.logic_samples = deque(maxlen=600)
        self.pause_reason = 'ready'
        self.discarded_tick_seconds = 0.
        self.metrics_path = getattr(args, 'metrics', None)
        self.metrics_writer = None
        self.next_metrics = 0.
        if self.metrics_path:
            self.metrics_path = Path(self.metrics_path)
            self.metrics_writer = LatestMetricsWriter(self.metrics_path)
        self.last_draw_seconds = 0.
        self.last_frame_timing = None
        self.long_frames = deque(maxlen=32)
        self.long_ticks = deque(maxlen=32)
        self.recovery_events = deque(maxlen=64)
        self.timeline = (Timeline(args.timeline) if getattr(args, 'timeline', None) else None)
        self.next_tick_due = 0.
        self.last_frame_at = None
        self.fps = 0.
        self.status_text = None
        self.get_style_context().add_class('sword-shell')
        css = Gtk.CssProvider()
        css.load_from_data(b'.sword-shell {background:#ebe4d2;color:#243d37}'
            b'.sword-bar {background:#e8dfc9;padding:4px 7px;}'
            b'.sword-bar button {padding:5px 12px;background:#f5edda;'
            b'color:#284138;border-radius:4px;border-color:#cabd9b;}'
            b'.sword-bar label {background:transparent;}')
        self.get_style_context().add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        bar = Gtk.Box(spacing=8)
        self.toolbar = bar
        bar.get_style_context().add_class('sword-bar')
        self.home_button = Gtk.Button(label='返回菜单')
        self.home_button.connect('clicked', lambda *_: self.request_menu())
        bar.pack_start(self.home_button, False, False, 0)
        self.title = Gtk.Label(label='御剑山河' + (' · 离线演示' if args.offline_preview else ''))
        self.title.set_xalign(0)
        bar.pack_start(self.title, True, True, 0)
        self.restart_button = Gtk.Button(label='重新开始')
        self.restart_button.connect('clicked', lambda *_: self.restart())
        bar.pack_start(self.restart_button, False, False, 0)
        self.full_button = Gtk.Button(label='全屏')
        self.full_button.connect('clicked', lambda *_: self.request_fullscreen())
        bar.pack_start(self.full_button, False, False, 0)
        self.exit_button = Gtk.Button(label='退出')
        self.exit_button.connect('clicked', lambda *_: self.operation('shutdown'))
        bar.pack_start(self.exit_button, False, False, 0)
        self.pack_start(bar, False, False, 0)
        hide_embedded_toolbar(self.toolbar, bool(getattr(args, 'embed_socket', None)))
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.set_can_focus(True)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.canvas.connect('draw', self.draw)
        self.canvas.connect('button-press-event', self.click)
        self.canvas.connect('motion-notify-event', self.motion)
        self.canvas.connect('leave-notify-event', self.leave)
        self.pack_start(self.canvas, True, True, 0)
        # Keep input at normal priority, but schedule the next timer only
        # after this callback returns. Repeating timers can stay continuously
        # ready under a dense collision load and starve GTK's redraw source.
        self.timer = GLib.timeout_add(16, self.scheduled_tick, priority=GLib.PRIORITY_DEFAULT)
        if self.timeline:
            self.next_tick_due = time.monotonic()+.016
            self.timeline.mark('timer_set', self.next_tick_due, 16)
        if not args.offline_preview and self.gamepad is None:
            self.poll_thread = threading.Thread(target=self.poll,
                name='sword-latest-pose', daemon=True)
            self.poll_thread.start()

    def clear_input(self):
        with self.lock:
            self.latest = None
        self.current_state = None
        self.tracker.reset()
        if getattr(self,'gamepad',None):self.gamepad.reset()
        self.dwell.reset()
        self.control = dict(x=.5, y=.5, valid=False, special=False,
                            source_key=None, status='waiting')
        self.last_key = self.last_fresh = self.invalid_since = None
        self.mouse = None

    def operation(self, op):
        if self.timeline:
            self.timeline.mark('operation_begin', op)
        if op == 'shutdown':
            if self.stop.is_set():
                return False
            with self.lock:
                self.stop.set()
                self.active = False
                self.generation += 1
            self.clear_input()
            self.game.pause(True)
            if getattr(self,'sound',None):self.sound.close()
            if getattr(self,'gamepad',None):self.gamepad.close()
            if self.metrics_writer is not None:
                self.metrics_writer.close()
            if self.timeline:
                self.timeline.close()
            if self.timer is not None:
                GLib.source_remove(self.timer)
                self.timer = None
            if Gtk.main_level():
                Gtk.main_quit()
            return False
        if self.stop.is_set() or op not in ('pause', 'resume'):
            return False
        active = op == 'resume'
        if active == self.active:
            return False
        now = time.monotonic()
        if self.end_started is not None:
            if not active:
                self.end_paused_at = now
            elif self.end_paused_at is not None:
                self.end_started += max(0., now-self.end_paused_at)
                self.end_paused_at = None
        with self.lock:
            self.active = active
            self.generation += 1
        self.clear_input()
        self.game.pause(True)
        if getattr(self,'sound',None):
            self.sound.resume() if active else self.sound.pause()
        self.recovery_until = 0.
        self.recovery_needed = active and self.game.snapshot()['phase'] in ('playing', 'countdown')
        self.entry_until = time.monotonic()+max(RESUME_SECONDS, self.args.entry_delay) if active else 0.
        self.last_tick = time.monotonic()
        if not self.args.embed_socket:
            self.home_button.set_label('返回菜单' if active else '继续御剑')
        self.canvas.queue_draw()
        emit('resumed' if active else 'paused', mode='sword',
             game_state=self.game.snapshot()['phase'], game_clock=self.game.snapshot()['time'])
        return False

    def submit_operation(self, op):
        """Coalesce parent commands; EOF/shutdown wins over later input."""
        if self.timeline:
            self.timeline.mark('operation_queued', op)
        with self.command_lock:
            if self.command_op == 'shutdown' or self.stop.is_set():
                return
            self.command_op = op
            if self.command_pending:
                return
            self.command_pending = True
        GLib.idle_add(self.deliver_operation, priority=GLib.PRIORITY_DEFAULT)

    def deliver_operation(self):
        with self.command_lock:
            op = self.command_op
            self.command_op = None
            self.command_pending = False
        return self.operation(op)

    def pause_game(self, value):
        # Game.pause also clears pending simulation input; call only on an
        # actual transition so ordinary ticks preserve fractional physics time.
        if bool(self.game.paused) != bool(value):
            self.game.pause(value)

    def request_menu(self):
        if not self.args.embed_socket and not self.active:
            self.operation('resume')
            return
        self.operation('pause')
        emit('menu_requested')

    def request_fullscreen(self):
        if self.args.embed_socket:
            emit('fullscreen_requested')
        else:
            window = self.get_toplevel()
            if window.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                window.unfullscreen()
            else:
                window.fullscreen()

    def key(self, _, event):
        if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_Menu, Gdk.KEY_F1):
            self.request_menu()
            return True
        if event.keyval == Gdk.KEY_F11:
            self.request_fullscreen()
            return True
        return False

    def handle_action(self,request):
        if not hasattr(self,'action_dispatcher'):
            self.action_dispatcher=ActionDispatcher('sword',
                lambda:not self.active and not self.stop.is_set(),self.execute_action,emit)
        return self.action_dispatcher.dispatch(request)

    def execute_action(self,action,done):
        if action.startswith('audio_'):
            self.sound.handle(action)
            done(True,'声音设置已更新',audio=self.sound.director.status());return
        if action in ('start','restart'):
            if not self.restart(while_paused=True):done(False,'restart_unavailable');return
            self.clear_input()
        elif action not in ('pause','continue'):done(False,'unsupported_action');return
        done(True,game_state=self.game.snapshot()['phase'])

    def restart(self,*,while_paused=False):
        if self.stop.is_set() or (not self.active and not while_paused):
            return False
        self.game.start(countdown=3)
        self.game.pause(True)
        if getattr(self,'sound',None):self.sound.restart()
        self.dwell.reset()
        self.recovery_until = 0.
        self.recovery_needed = False
        self.end_started = self.end_paused_at = None
        self.last_tick = time.monotonic()
        self.canvas.queue_draw()
        return True

    def click(self, widget, event):
        if event.button != 1:
            return False
        widget.grab_focus()
        x = event.x/max(1, widget.get_allocated_width())
        y = event.y/max(1, widget.get_allocated_height())
        if in_start_region(x, y) and self.active and time.monotonic() >= self.entry_until:
            if self.presentation_snapshot(time.monotonic())['phase'] in ('ready', 'won', 'lost'):
                self.restart()
        return True

    def motion(self, widget, event):
        if self.args.offline_preview and self.active:
            self.mouse = (max(0., min(1., event.x/max(1, widget.get_allocated_width()))),
                          max(0., min(1., event.y/max(1, widget.get_allocated_height()))))
        return False

    def leave(self, *_):
        self.mouse = None
        return False

    def poll(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not self.stop.is_set():
            with self.lock:
                active, generation = self.active, self.generation
            if not active:
                self.stop.wait(.06)
                continue
            trace = self.timeline
            if trace:
                trace.mark('poll_begin', generation)
            try:
                with opener.open(self.args.base.rstrip('/')+'/api/state', timeout=.45) as response:
                    if trace:
                        trace.mark('poll_headers')
                    state = json.load(response)
                if not isinstance(state, dict):
                    state = None
                if trace:
                    sequence = state.get('sequence') if state else None
                    trace.mark('poll_decoded', sequence if isinstance(sequence, (int, str)) else None)
            except Exception:
                state = None
            published = False
            with self.lock:
                if self.active and not self.stop.is_set() and generation == self.generation:
                    self.latest = (state, time.monotonic())
                    published = True
            if trace:
                trace.mark('poll_end', published, state is not None)
            self.stop.wait(.026 if state is not None else .12)

    def scheduled_tick(self):
        self.timer = None
        started = time.monotonic()
        keep = self.tick()
        if keep and not self.stop.is_set():
            delay = max(1, math.ceil(16-(time.monotonic()-started)*1000))
            self.timer = GLib.timeout_add(delay, self.scheduled_tick,
                                         priority=GLib.PRIORITY_DEFAULT)
            if self.timeline:
                self.next_tick_due = time.monotonic()+delay/1000.
                self.timeline.mark('timer_set', self.next_tick_due, delay)
        return False

    def advance(self, now):
        """One bounded UI tick; also used by deterministic lifecycle tests."""
        elapsed = max(0., now-self.last_tick)
        if self.metrics_path and elapsed > .080:
            self.long_ticks.append(dict(at=now, interval_ms=elapsed*1000,
                previous_draw_ms=self.last_draw_seconds*1000,
                phase=self.game.phase, game_time=self.game.time,
                previous_pause_reason=self.pause_reason,
                previous_frame=dict(self.last_frame_timing) if self.last_frame_timing else None))
        dt = min(MAX_DT, elapsed)
        self.tick_samples.append(elapsed)
        self.discarded_tick_seconds += max(0., elapsed-MAX_DT)
        self.last_tick = now
        if not self.active:
            self.pause_reason = 'host_pause'
            self.game.pause(True)
            self.dwell.reset()
            self.notice = '已暂停 · 从菜单返回后继续' if self.args.embed_socket else '已暂停 · 点击“继续御剑”返回'
            return
        if getattr(self,'gamepad',None):
            self.advance_gamepad(now,dt)
            return
        failed = False
        if self.args.offline_preview:
            self.current_state = demo_state(now, self.mouse)
        else:
            with self.lock:
                pending, self.latest = self.latest, None
            if pending is not None:
                self.current_state = pending[0] if now-pending[1] <= MAX_SAMPLE_GAP else None
                failed = self.current_state is None
        if self.timeline:
            self.timeline.mark('input_begin')
        self.control = self.tracker.update(self.current_state, now)
        if self.timeline:
            sequence = (self.current_state or {}).get('sequence')
            self.timeline.mark('input_end', self.control.get('status'),
                self.control.get('source_key') != self.last_key,
                sequence if isinstance(sequence, (int, str)) else None)
        key = self.control.get('source_key')
        tracking = self.control.get('status') == 'tracking'
        fresh = bool(self.control.get('valid') and tracking and key is not None and key != self.last_key)
        if fresh:
            self.last_key, self.last_fresh = key, now
        valid = bool(not failed and self.control.get('valid') and tracking and self.last_fresh is not None and
                     now-self.last_fresh <= MAX_SAMPLE_GAP)
        phase = self.game.phase
        in_game = phase in ('playing', 'countdown')
        previous_recovery = (self.recovery_needed, self.recovery_until)
        discontinuity = bool(self.control.get('continuity_reset'))
        if not hasattr(self,'source_gap'):self.source_gap=SourceGapPolicy()
        source_blocked=self.source_gap.update(self.current_state,now,offline=self.args.offline_preview)
        if not valid:
            self.dwell.reset()
            if self.invalid_since is None:
                self.invalid_since = now
        else:
            self.invalid_since = None
        # Only a sustained source outage protects the world clock. Body/joint
        # quality and ROI changes discard controls, not time or music.
        self.recovery_needed=source_blocked
        self.recovery_until=0.
        if self.metrics_path and (failed or discontinuity or
                self.control.get('continuous_roi') or
                previous_recovery != (self.recovery_needed, self.recovery_until)):
            self.recovery_events.append(dict(at=now, phase=phase,
                game_time=self.game.time, source_key=key, failed_poll=failed,
                observation_missing=bool(self.control.get('observation_missing')),
                continuity_reset=discontinuity,
                continuity_reason=self.control.get('continuity_reason'),
                continuous_roi=bool(self.control.get('continuous_roi')),
                tracking_status=self.control.get('status'), fresh=fresh,
                recovery_needed=self.recovery_needed, recovery_until=self.recovery_until))
        suite = (self.current_state or {}).get('suite') or {}
        menu_gesture = bool(suite.get('control_active')) if isinstance(suite, dict) else False
        hold = source_blocked or now < self.entry_until
        self.pause_reason = ('source_timeout' if source_blocked else
            'entry' if now < self.entry_until else '')
        self.pause_game(hold)
        if self.timeline:
            self.timeline.mark('game_begin', 0. if hold else dt, hold)
        logic_started = time.perf_counter()
        game_control=self.control if valid and not menu_gesture and not discontinuity else dict(valid=False,special=False,status='waiting')
        self.game.update(0. if hold else dt, game_control)
        self.logic_samples.append(time.perf_counter()-logic_started)
        if self.timeline:
            self.timeline.mark('game_end')
        # Deep-copying every entity/effect solely to read phase repeated work
        # three times per input tick. Detached snapshots belong to rendering;
        # only a victory needs the presentation-only celebration override.
        presentation_phase = (self.presentation_snapshot(now)['phase']
            if self.game.phase == 'won' else self.game.phase)
        if self.dwell.update(self.control, now,
                enabled=not hold and presentation_phase in ('ready', 'won', 'lost')):
            self.restart()
        if now < self.entry_until:
            self.notice = '准备入场 · %d' % math.ceil(self.entry_until-now)
        elif not valid:
            self.notice = '骨架暂失 · 等待右手与双肩'
        elif self.recovery_needed:
            self.notice = '准备继续 · %d' % max(1, math.ceil(self.recovery_until-now))
        elif menu_gesture:
            self.notice = '正在识别菜单手势 · 御剑已暂停'
        elif self.args.offline_preview:
            self.notice = '离线演示 · 合成骨架 / 鼠标操控'
        else:
            self.notice = ''

    def advance_gamepad(self,now,dt):
        self.control=self.gamepad.sword(now)
        valid=self.control['valid']
        entering=now<self.entry_until
        hold=not valid or entering
        self.pause_reason='gamepad' if not valid else 'entry' if entering else ''
        self.recovery_needed=False;self.recovery_until=0.
        self.pause_game(hold)
        if not hold and self.control.get('confirm') and self.game.phase in ('ready','won','lost'):
            self.restart()
            self.pause_game(False)
        self.game.update(0. if hold else dt,self.control)
        self.notice=(self.control['reason'] if not valid else
                     '准备入场 · %d'%math.ceil(self.entry_until-now) if entering else
                     '')

    def tick(self):
        if self.stop.is_set():
            return False
        trace = self.timeline
        now = time.monotonic()
        if trace:
            trace.mark('tick_begin', max(0., now-self.last_tick),
                max(0., now-self.next_tick_due) if self.next_tick_due else None,
                self.game.phase, self.game.time, at=now)
        try:
            old_notice = self.notice
            if trace:
                trace.mark('advance_begin')
            self.advance(now)
            self.sound.tick()
            if trace:
                trace.mark('advance_end')
            # An embedded child can remain mapped even while its host stack
            # shows the menu. Pause/resume already requests its transition
            # frame; avoid spending the menu's CPU budget on hidden gameplay.
            if self.active or self.notice != old_notice:
                self.canvas.queue_draw()
            self.write_metrics()
        except Exception as error:
            self.fatal(error)
            return False
        finally:
            if trace:
                trace.mark('tick_end', time.monotonic()-now, self.game.phase,
                           self.game.time, self.pause_reason)
        return True

    def ui_state(self):
        return dict(notice=self.notice,
            control_mode='gamepad' if getattr(self,'gamepad',None) else 'body',
            pointer=(self.control['x'], self.control['y']) if self.active and self.control.get('valid') else None,
            dwell_progress=self.dwell.progress, offline=self.args.offline_preview,
            resume_countdown=max(0., self.entry_until-self.last_tick,
                self.recovery_until-self.last_tick) if self.active else 0.,
            tracking_lost_seconds=max(0., time.monotonic()-self.invalid_since)
                if self.active and self.invalid_since is not None else 0.,
            fps=self.fps)

    def presentation_snapshot(self, now):
        """Let the winning ink burst finish without advancing game physics."""
        snapshot = self.game.snapshot()
        if snapshot['phase'] != 'won':
            self.end_started = self.end_paused_at = None
            return snapshot
        if self.end_started is None:
            self.end_started = now
            if not self.active:
                self.end_paused_at = now
        effective = self.end_paused_at if self.end_paused_at is not None else now
        elapsed = max(0., effective-self.end_started)
        # Game.snapshot returns detached state. Aging these effect copies never
        # changes score, lives, positions, or the terminal engine state.
        for effect in snapshot.get('effects', []):
            effect['age'] = effect.get('age', 0.)+elapsed
        snapshot['effects'] = [effect for effect in snapshot.get('effects', [])
                               if effect['age'] < effect.get('ttl', 0.)]
        snapshot['special_time'] = max(0., snapshot.get('special_time', 0.)-elapsed)
        if elapsed < CELEBRATION_SECONDS:
            snapshot['phase'] = 'celebrating'
        return snapshot

    def compose_frame(self, width, height, now):
        if self.backbuffer is None or (self.backbuffer.get_width(),self.backbuffer.get_height()) != (width,height):
            self.backbuffer = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
        self.renderer.draw(cairo.Context(self.backbuffer), width, height,
            self.presentation_snapshot(now), self.ui_state())

    def draw(self, widget, cr):
        if self.stop.is_set():
            return False
        started = time.monotonic()
        trace = self.timeline
        if trace:
            trace.mark('draw_begin', self.game.phase, self.game.time, at=started)
        diagnose = bool(self.metrics_path or trace)
        cpu_started = time.thread_time() if diagnose else 0.
        try:
            from render_policy import RenderPolicy, present
            if not hasattr(self,'render_policy'):self.render_policy=RenderPolicy.from_environment()
            layout=self.render_policy.layout(max(1,widget.get_allocated_width()),max(1,widget.get_allocated_height()))
            width,height=layout.render_size
            # Match Fruit's RAM composition path; present the completed frame
            # once instead of issuing every translucent layer to X11.
            self.compose_frame(width,height,started)
            composed = time.monotonic() if diagnose else 0.
            if trace:
                trace.mark('compose_end', at=composed)
            visual=widget.get_visual()
            if visual != self.presentation_visual:
                self.presentation_visual=visual
                self.presentation_visual_is_rgb565=uses_rgb565(visual)
            target=cr.get_target()
            self.presentation_target_format=(int(target.get_format())
                if isinstance(target,cairo.ImageSurface) else None)
            self.presentation_is_rgb565=target_uses_rgb565(target,self.presentation_visual_is_rgb565)
            # Convert only when the destination needs 565. An intermediate
            # RGB24/ARGB32 image keeps the composition's full color even when
            # the eventual X11 window uses a 16-bit visual.
            presented=self.presenter.convert(self.backbuffer) if self.presentation_is_rgb565 else self.backbuffer
            converted = time.monotonic() if diagnose else 0.
            if trace:
                trace.mark('convert_end', at=converted)
            present(cr,presented,layout)
            self.frames_drawn += 1
            self.last_draw_seconds = time.monotonic()-started
            if diagnose:
                self.last_frame_timing = dict(at=started, width=width, height=height,
                    total_ms=self.last_draw_seconds*1000,
                    compose_ms=(composed-started)*1000,
                    convert_ms=(converted-composed)*1000,
                    present_ms=(started+self.last_draw_seconds-converted)*1000,
                    thread_cpu_ms=(time.thread_time()-cpu_started)*1000,
                    phase=self.game.phase, game_time=self.game.time)
                if self.last_draw_seconds > .080:
                    self.long_frames.append(dict(self.last_frame_timing))
            self.draw_samples.append(self.last_draw_seconds)
            self.draw_stamps.append(started)
            if self.last_frame_at is not None and started > self.last_frame_at:
                measured = 1./(started-self.last_frame_at)
                self.fps = .8*self.fps + .2*measured if self.fps else measured
            self.last_frame_at = started
        except Exception as error:
            self.fatal(error)
        finally:
            if trace:
                trace.mark('draw_end', time.monotonic()-started)
        return False

    def performance_snapshot(self):
        values=sorted(self.draw_samples)
        ticks=sorted(self.tick_samples)
        logic=sorted(self.logic_samples)
        percentile=lambda data,p: data[min(len(data)-1,int((len(data)-1)*p))]*1000 if data else 0.
        stamps=self.draw_stamps
        return dict(monotonic_seconds=time.monotonic(),frames_drawn=self.frames_drawn,
            draw_fps=(len(stamps)-1)/(stamps[-1]-stamps[0]) if len(stamps)>1 and stamps[-1]>stamps[0] else 0.,
            draw_ms_p50=percentile(values,.5),draw_ms_p95=percentile(values,.95),
            tick_ms_p50=percentile(ticks,.5),tick_ms_p95=percentile(ticks,.95),
            logic_ms_p50=percentile(logic,.5),logic_ms_p95=percentile(logic,.95),
            enemies=sum(e.get('kind') in ('enemy','elite','boss') for e in self.game.entities),
            enemy_bullets=len(getattr(self.game,'bullets',())),
            player_shots=len(getattr(self.game,'shots',())),effects=len(self.game.effects),
            discarded_tick_seconds=self.discarded_tick_seconds,pause_reason=self.pause_reason,
            phase=self.game.phase,game_time=self.game.time,score=self.game.score,
            tracking_status=self.control.get('status'),recovery_needed=self.recovery_needed,
            recovery_events=list(self.recovery_events),
            audio=self.sound.director.status() if getattr(self,'sound',None) else None,
            renderer='RGB24 memory composition then one GTK blit',
            alpha_backend=getattr(getattr(self.renderer,'blitter',None),'backend','cairo'),
            native_alpha_calls=getattr(getattr(self.renderer,'blitter',None),'native_calls',0),
            cairo_alpha_fallback_calls=getattr(getattr(self.renderer,'blitter',None),'fallback_calls',0),
            presentation_visual_depth=self.presentation_visual.get_depth() if self.presentation_visual else None,
            presentation_target_format=self.presentation_target_format,
            presentation_backend=self.presenter.backend if self.presentation_is_rgb565 else 'rgb24-direct',
            native_presentation_calls=self.presenter.calls,
            long_frames=list(self.long_frames),long_ticks=list(self.long_ticks),
            last_frame_timing=dict(self.last_frame_timing) if self.last_frame_timing else None,
            physical_scanout_measured=False)

    def write_metrics(self):
        now=time.monotonic()
        if not self.metrics_path or now<self.next_metrics:
            return
        self.next_metrics=now+1.
        if self.timeline:
            self.timeline.mark('metrics_begin')
        if self.metrics_writer is None:
            self.metrics_writer = LatestMetricsWriter(self.metrics_path)
        # Capture only detached scalar values on the GTK thread. JSON/file
        # operations belong to the bounded writer, including error reporting.
        if not self.metrics_writer.submit(self.performance_snapshot()):
            self.metrics_path=None
        if self.timeline:
            self.timeline.mark('metrics_end')

    def fatal(self, error):
        if self.fatal_error is not None:
            return
        self.fatal_error = str(error)
        print('sword: '+self.fatal_error, file=sys.stderr, flush=True)
        emit('error', mode='sword', message=self.fatal_error)
        self.operation('shutdown')


def read_commands(panel, stream):
    try:
        for line in stream:
            try:
                value = json.loads(line)
                op = value.get('op') if isinstance(value, dict) else None
            except (ValueError, TypeError):
                continue
            if op == 'action':
                GLib.idle_add(panel.handle_action,value,priority=GLib.PRIORITY_DEFAULT)
                continue
            if op in ('pause', 'resume', 'shutdown'):
                panel.submit_operation(op)
                if op == 'shutdown':
                    return
    except (OSError, ValueError):
        pass
    panel.submit_operation('shutdown')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--embed-socket', type=int)
    parser.add_argument('--base-url', '--base', dest='base', default='http://127.0.0.1:8088')
    parser.add_argument('--offline-preview', action='store_true')
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--entry-delay', type=float, default=3.)
    parser.add_argument('--metrics', type=Path, help='Optional one-second native rendering diagnostics file')
    parser.add_argument('--timeline', type=Path,
        help='Opt-in bounded tick/draw/GC timeline; background JSONL writes, may perturb timings')
    args = parser.parse_args(argv)
    if not math.isfinite(args.entry_delay) or args.entry_delay < 0:
        parser.error('--entry-delay must be a finite nonnegative number')
    return args


def main(argv=None):
    args = parse_args(argv)
    window = Gtk.Plug.new(args.embed_socket) if args.embed_socket else Gtk.Window(title='御剑山河')
    if not args.embed_socket:
        window.set_default_size(1024, 600)
        window.set_size_request(640, 400)
    panel = SwordPanel(args)
    from visitor.reporter import attach
    attach(panel,'sword',GLib,disabled=args.offline_preview)
    window.add(panel)
    window.connect('key-press-event', panel.key)
    window.connect('destroy', lambda *_: panel.operation('shutdown'))
    window.show_all()
    if args.fullscreen and not args.embed_socket:
        window.fullscreen()
    # Desktop launchers commonly supply /dev/null as stdin. Only an embedded
    # child has a host command pipe whose EOF should terminate the process.
    if args.embed_socket:
        threading.Thread(target=read_commands, args=(panel, sys.stdin),
            name='sword-host-commands', daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: panel.submit_operation('shutdown'))
    emit('ready', mode='sword', experience='sword', offline=args.offline_preview)
    try:
        Gtk.main()
    finally:
        panel.operation('shutdown')
        window.destroy()
        if panel.poll_thread:
            panel.poll_thread.join(1.)
        if panel.timeline:
            # Outside the GTK event loop only. A blocked disk still cannot
            # hold application shutdown indefinitely.
            panel.timeline.finished.wait(2.)
    return 1 if panel.fatal_error else 0


if __name__ == '__main__':
    raise SystemExit(main())
