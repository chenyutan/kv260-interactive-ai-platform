"""Bounded native guided-course view; no camera reads or inferred joints.

The host continues to own shared inference and menu policy.  This widget consumes
observed state dictionaries and a pure LessonSession, with one bounded report
writer and no extra GL context. Gesture selection is disabled while an exercise runs.
"""
from collections import OrderedDict
import copy
import math
import datetime
import json
from pathlib import Path
import sys
import time
import weakref

import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

from render_policy import RenderPolicy, present
from .engine import LessonSession
from .media_flow import LessonVideoFlow, LessonVideos
from .video import TeachingVideoPlayer
from .course_registry import get_course
from .pose_display import project_observed, ObservedViewport, _project as project_with_transform
from .display_filter import DisplayFilter
from player_context import context_key
from .autosave import ReportWriter
from .guide_visual import draw_practice, guide_button_label, guide_identity
from .reference_demo import ReferenceLibrary


ROOT = Path(__file__).resolve().parent.parent
BODY_EDGES = ((5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
              (5, 11), (6, 12), (11, 12), (11, 13), (13, 15),
              (12, 14), (14, 16))
PAPER = (.978, .969, .948)
INK = (.105, .225, .218)
GREEN = (.16, .35, .32)
MUTED = (.43, .48, .45)
GOLD = (.64, .49, .28)
CINNABAR = (.66, .26, .20)
MODE_NAMES = {'learn': '分解学习', 'follow': '完整跟练', 'free': '自主练习'}
WELCOME_BUTTONS = (
    ('health_start_learn', '分解学习', '逐式示意，分段跟着练', (494, 146, 920, 220)),
    ('health_start_follow', '完整跟练', '按课程节奏连续练习', (494, 233, 920, 307)),
    ('health_start_free', '自主练习', '自行决定节奏，需手动换到下一式', (494, 320, 920, 394)),
    ('health_history', '练习记录', '', (494, 407, 700, 451)),
    ('health_lessons', '选择动作', '', (714, 407, 920, 451)),
    ('health_info', '课程说明', '', (494, 463, 710, 508)),
    ('health_home', '返回健康', '', (748, 463, 920, 508)),
)
COURSE_BUTTONS = (
    ('health_previous', '上一式', '', (34, 464, 150, 510)),
    ('health_repeat', '重新练习', '', (160, 464, 280, 510)),
    ('health_pause', '暂停', '', (290, 464, 406, 510)),
    ('health_lessons', '选择动作', '', (416, 464, 536, 510)),
    ('health_next', '下一式', '', (546, 464, 650, 510)),
    ('health_info', '课程说明', '', (660, 464, 790, 510)),
    ('health_home', '返回健康', '', (800, 464, 926, 510)),
)
VIDEO_BUTTONS = (
    ('health_video_rewatch', '从头播放', '', (34, 464, 154, 510)),
    ('health_video_skip', '开始跟练', '', (164, 464, 340, 510)),
    ('health_pause', '暂停视频', '', (350, 464, 470, 510)),
    ('health_lessons', '选择动作', '', (480, 464, 600, 510)),
    ('health_next', '下一式', '', (610, 464, 700, 510)),
    ('health_info', '课程说明', '', (710, 464, 810, 510)),
    ('health_home', '返回健康', '', (820, 464, 926, 510)),
)
INFO_BUTTONS = (('health_info_back', '返回', '', (754, 464, 926, 510)),)
COMPLETED_BUTTONS = (
    ('health_restart', '再练一次', '', (250, 463, 466, 509)),
    ('health_home', '返回健康', '', (494, 463, 710, 509)),
)
RETRY_BUTTONS = (
    ('health_retry_save', '重试保存', '', (128, 463, 344, 509)),
    ('health_restart', '再练一次', '', (372, 463, 588, 509)),
    ('health_home', '返回健康', '', (616, 463, 832, 509)),
)
RECOVERY_BUTTONS = (
    ('health_retry_save', '重试保存', '', (250, 463, 466, 509)),
    ('health_home', '返回健康', '', (494, 463, 710, 509)),
)
HISTORY_BUTTONS = (
    ('health_history_back', '返回练习', '', (250, 463, 466, 509)),
    ('health_home', '返回健康', '', (494, 463, 710, 509)),
)
LESSONS_PER_PAGE = 12


def valid_points(points, score=.12):
    """Return only directly supplied, finite normalized body observations."""
    result = []
    for index in range(17):
        try:
            raw = points[index]
            x, y, confidence = map(float, raw[:3])
            valid = (all(math.isfinite(v) for v in (x, y, confidence)) and
                     0 <= x <= 1 and 0 <= y <= 1 and confidence >= score)
            result.append((x, y, confidence) if valid else None)
        except (TypeError, ValueError, IndexError, KeyError):
            result.append(None)
    return tuple(result)


def design_transform(width, height):
    """Fixed 960x540 content, centered for non-widescreen allocations."""
    width, height = max(1., float(width)), max(1., float(height))
    scale = min(width / 960., height / 540.)
    return scale, (width - 960. * scale) / 2., (height - 540. * scale) / 2.


def normalized_rectangles(buttons, width=960, height=540):
    scale, ox, oy = design_transform(width, height)
    return tuple(((ox + a * scale) / width, (oy + b * scale) / height,
                  (ox + c * scale) / width, (oy + d * scale) / height)
                 for _, _, _, (a, b, c, d) in buttons)


def hit_rectangles(rectangles, x, y):
    try:
        x, y = float(x), float(y)
        if not all(math.isfinite(v) for v in (x, y)):
            return None
    except (TypeError, ValueError):
        return None
    return next((i for i, (a, b, c, d) in enumerate(rectangles)
                 if a <= x <= c and b <= y <= d), None)


def project_body(points, box, mirror=True, aspect=16/9):
    """Torso-anchored projection; no joint filling or observation smoothing."""
    return project_observed(points, box, mirror, aspect)


def project_reference(points, box, mirror=True, aspect=16/9):
    """Use one fixed illustration stage: arm motion never rescales the body."""
    points = valid_points(points)
    a, b, c, d = box
    scale = min((c-a)*.94/aspect, (d-b)*.94)
    output = []
    for point in points:
        if point is None:
            output.append(None)
            continue
        x, y, confidence = point
        dx = (x-.5)*aspect
        output.append(((a+c)/2.+(-dx if mirror else dx)*scale,
                       (b+d)/2.+(y-.5)*scale, confidence))
    return tuple(output)


def _rounded(cr, x, y, width, height, radius=14):
    cr.new_sub_path()
    cr.arc(x+width-radius, y+radius, radius, -math.pi/2, 0)
    cr.arc(x+width-radius, y+height-radius, radius, 0, math.pi/2)
    cr.arc(x+radius, y+height-radius, radius, math.pi/2, math.pi)
    cr.arc(x+radius, y+radius, radius, math.pi, math.pi*1.5)
    cr.close_path()


class HealthView(Gtk.DrawingArea):
    """A lesson player embedded in Suite.stack, not another game process."""

    def __init__(self, on_home, on_report=None, report_directory=None,
                 save_reports=True, video_enabled=True, video_player=None,
                 video_library=None, course_id='baduanjin',on_ai=None,on_start=None):
        super().__init__()
        self.on_home = on_home
        self.on_ai = on_ai
        self.on_report = on_report
        self.on_start = on_start
        self.report_directory = Path(report_directory) if report_directory else ROOT/'data/health_reports'
        self.save_reports = bool(save_reports)
        self.session = None
        self.course=get_course(course_id);self.course_id=self.course['id']
        self.pending_course_id=None
        self.video_enabled = bool(video_enabled)
        self.video_player = video_player or TeachingVideoPlayer()
        self._custom_video_library=video_library is not None
        self.video_library = video_library or LessonVideos(self.course['video_directory'])
        self.reference_library = ReferenceLibrary()
        self.observed_viewport = ObservedViewport()
        self.display_filter = DisplayFilter(hold_seconds=.2)
        self._display_context = None
        self.video_flow = None
        self.current = {}
        self.last_mode = 'learn'
        self.active = False
        self.timer = None
        self.policy = RenderPolicy.from_environment()
        self.buffer = None
        self.background = None
        self.background_key = None
        self._welcome_art_cache = None
        self.text_cache = OrderedDict()
        self.layout_builds = 0
        self.background_builds = 0
        self.pointer = None
        self.pointer_received = None
        self.hover = None
        self.progress = 0.
        self.focus = 0
        self.selection_source = 'discrete'
        self.selection_enabled = True
        self.availability_message = ''
        self.latest_key = None
        self.pose_received = None
        self.report_emitted = False
        self.report_path = None
        self.save_error = None
        self._pending_report = None
        self._report_notifications = set()
        self._save_idle_pending = False
        self._save_draining = False
        self._save_revisions = {}
        self._final_notifications = OrderedDict()
        self._final_save_pending = False
        self._save_retry_timer = None
        self._retry_snapshot = None
        self._pending_start_request = None
        self._autosave_hidden = False
        self._autosave_state = dict(state='idle', error=None, last_saved_at=None,
                                   last_saved_wall=None, session_id=None,
                                   record_state=None, save_reason=None)
        self._next_autosave = 0.
        reference = weakref.ref(self)
        def saved():
            widget = reference()
            if widget is not None and not widget._save_idle_pending:
                widget._save_idle_pending = True
                GLib.idle_add(widget._drain_saves)
        self._report_writer = ReportWriter(notify=saved)
        self.pending_recovery = False
        self.history_open = False
        self.history = []
        self.history_error = None
        self.lesson_picker_open = False
        self.lesson_page = 0
        self.selected_lesson_index = 0
        self._picker_resume = False
        self.info_open = False
        self._info_resume = False
        self._info_clip = None
        # Optional host-owned speech output can update this status later.  The
        # initial native player is fully usable with text and diagram guidance.
        self.voice_enabled = False
        self.voice_status = '文字指导'
        self.dirty = True
        self._closed = False
        self.set_size_request(640, 360)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect('button-press-event', self.click)
        self.connect('draw', self.draw)
        self.connect('map', self.mapped)
        self.connect('unmap', self.unmapped)
        self.connect('destroy', lambda *_: self.close())

    @property
    def gesture_navigation(self):
        return (self.info_open or self.lesson_picker_open or self.history_open or self.session is None or self.current.get('phase') == 'video' or
                self.current.get('status') in ('ready', 'paused', 'completed'))

    @property
    def buttons(self):
        if self.pending_recovery:
            if self._final_save_pending and not self.save_error:
                return (RECOVERY_BUTTONS[-1],)
            return RECOVERY_BUTTONS
        if self.info_open:
            return ((('health_ai','AI 讲解与建议','',(34,464,270,510)),)+INFO_BUTTONS) if self.on_ai else INFO_BUTTONS
        if self.lesson_picker_open:
            return self._lesson_picker_buttons()
        if self.history_open:
            return HISTORY_BUTTONS
        if self.session is None:
            return WELCOME_BUTTONS
        if self.current.get('status') == 'completed':
            return RETRY_BUTTONS if self.save_error else COMPLETED_BUTTONS
        if self.current.get('phase') == 'video':
            paused = self.current.get('status') == 'paused'
            return self._guided_buttons(tuple((identifier, '重试视频' if identifier == 'health_video_rewatch' and self.current.get('video', {}).get('error_message') else
                          '继续视频' if identifier == 'health_pause' and paused else label, subtitle, box)
                         for identifier, label, subtitle, box in VIDEO_BUTTONS))
        paused = self.current.get('status') == 'paused'
        buttons = COURSE_BUTTONS
        if self.video_enabled:
            buttons = tuple(('health_video_rewatch', '回看视频', subtitle, box) if identifier == 'health_repeat' else
                            (identifier, label, subtitle, box) for identifier, label, subtitle, box in buttons)
        return self._guided_buttons(tuple((identifier, '继续练习' if identifier == 'health_pause' and paused else label,
                      subtitle, box)
                     for identifier, label, subtitle, box in buttons))

    def _guided_buttons(self, rows):
        return tuple((identifier, guide_button_label(identifier, label, self.current), subtitle, box)
                     for identifier, label, subtitle, box in rows)

    def _lesson_choice_label(self, lesson, index):
        if self.course_id == 'baduanjin':
            if lesson['id'] == 'prepare':
                return '预备式 · 站姿准备'
            if lesson['id'] == 'finish':
                return '收势'
            return '第{}式 {}'.format(index, lesson['name'])
        return '第{}式 {}'.format(index+1, lesson['name'])

    @property
    def choices(self):
        return tuple(row[0] for row in self.buttons)

    @property
    def mode_choices(self):
        return self.choices

    @property
    def rectangles(self):
        return normalized_rectangles(self.buttons, max(1, self.get_allocated_width()),
                                     max(1, self.get_allocated_height()))

    def hit_index(self, x, y):
        return hit_rectangles(self.rectangles, x, y)

    def _lesson_picker_buttons(self):
        lessons = self.course['lessons']
        first = self.lesson_page * LESSONS_PER_PAGE
        buttons = []
        for offset, lesson in enumerate(lessons[first:first+LESSONS_PER_PAGE]):
            row, column = divmod(offset, 3)
            x, y = 34+column*302, 132+row*72
            buttons.append(('health_pick_'+lesson['id'],
                            self._lesson_choice_label(lesson, first+offset), '',
                            (x, y, x+286, y+58)))
        if self.lesson_page:
            buttons.append(('health_lessons_previous', '上一页', '', (34, 464, 206, 510)))
        if first+LESSONS_PER_PAGE < len(lessons):
            buttons.append(('health_lessons_next', '下一页', '', (222, 464, 394, 510)))
        buttons.append(('health_lessons_back', '返回', '', (754, 464, 926, 510)))
        return tuple(buttons)

    def _focus_choice(self):
        choices = self.choices
        return choices[self.focus] if self.focus in range(len(choices)) else None

    def _accept_current(self, snapshot):
        selected = self._focus_choice()
        if snapshot.get('lesson_id') != self.current.get('lesson_id'):
            self._reset_body_display()
        self.current = dict(snapshot)
        clip = (self.current.get('companion_video') or {}).get('clip') or {}
        self.reference_library.select(self.course_id, self.current.get('lesson_id'),
                                      clip.get('local_path'))
        self.focus = (self.choices.index(selected) if selected in self.choices else
                      min(max(0, self.focus), len(self.choices)-1))

    def _reset_body_display(self):
        self.observed_viewport.reset()
        self.display_filter.reset()
        self._display_context = None

    def _open_lesson_picker(self):
        if self.lesson_picker_open:
            return self.choices
        self._picker_resume = self.current.get('status') == 'running'
        self.pause()
        if self.video_flow:self.video_flow.set_companion_suspended(True)
        index = self.current.get('lesson_index', self.selected_lesson_index)
        self.lesson_page = int(index)//LESSONS_PER_PAGE
        self.lesson_picker_open = True
        self.focus = int(index) % LESSONS_PER_PAGE
        self.clear_pointer();self.dirty = True;self.queue_draw()
        return self.choices

    def _close_lesson_picker(self, resume=True):
        self.lesson_picker_open = False
        if self.video_flow:self.video_flow.set_companion_suspended(False)
        self.focus = self.choices.index('health_lessons') if 'health_lessons' in self.choices else 0
        if resume and self._picker_resume:
            self.resume()
        self._picker_resume = False
        self.clear_pointer();self.dirty = True;self.queue_draw()
        return self.choices

    def _open_info(self):
        if self.info_open:
            return self.choices
        self._info_resume = self.current.get('status') == 'running'
        self.pause()
        if self.video_flow:self.video_flow.set_companion_suspended(True)
        index = int(self.current.get('lesson_index', self.selected_lesson_index))
        index = max(0, min(len(self.course['lessons'])-1, index))
        self._info_clip = self.video_library.lookup(self.course['lessons'][index]['id'])
        self.info_open = True
        self.focus = 0;self.clear_pointer();self.dirty = True;self.queue_draw()
        return self.choices

    def _close_info(self):
        self.info_open = False
        if self.video_flow:self.video_flow.set_companion_suspended(False)
        if self._info_resume:
            self.resume()
        self._info_resume = False
        self.focus = self.choices.index('health_info') if 'health_info' in self.choices else 0
        self.clear_pointer();self.dirty = True;self.queue_draw()
        return self.choices

    def back(self):
        """Controller B backs out one visible level; Guide exits the course."""
        if self.info_open:
            return self._close_info()
        if self.lesson_picker_open:
            return self._close_lesson_picker()
        if self.history_open:
            return self.action('health_history_back')
        if self.pending_recovery or self.session is None:
            return self.action('health_home')
        if self.current.get('status') == 'running':
            return self.pause()
        self._emit_report('returned_to_course_menu')
        if self.video_flow:self.video_flow.stop()
        self._pause_pending_session()
        if self._has_unsaved_report():
            self.pending_recovery = True
        else:
            self.selected_lesson_index = self.session.lesson_index
            self.session = self.video_flow = None
            self.current = {}
        self.focus = 0;self.clear_pointer();self.dirty = True;self.queue_draw()
        return self.choices

    def enter(self,course_id=None):
        self._pending_start_request = None
        self._drain_saves(recover_ui=False)
        requested=get_course(course_id or self.pending_course_id or self.course_id)
        self.info_open = False
        self._info_resume = False
        self.lesson_picker_open = False
        if self.video_flow:
            self.video_flow.stop()
        if self.session is not None:
            self._emit_report('reopened_course')
        if self._has_unsaved_report():
            self.pending_course_id=requested['id']
            self._pause_pending_session()
            self.active = True
            self.pending_recovery = True
            self.history_open = False
            self.focus = 0
            self.clear_pointer()
            self.dirty = True
            self.queue_draw()
            return self.choices
        self.course=requested;self.course_id=requested['id'];self.pending_course_id=None
        if not self._custom_video_library:self.video_library=LessonVideos(self.course['video_directory'])
        self.session = None
        self.video_flow = None
        self.current = {}
        self.selected_lesson_index = 0
        self.pending_recovery = False
        self.history_open = False
        self.active = True
        self.report_emitted = False
        self.report_path = self.save_error = None
        self._pending_report = None
        self._report_notifications.clear()
        self.latest_key = self.pose_received = None
        self._reset_body_display()
        self.focus = 0
        self.clear_pointer()
        self.dirty = True
        self.queue_draw()
        return self.choices

    def start(self, mode='learn'):
        self._drain_saves(recover_ui=False)
        if mode not in MODE_NAMES:
            raise ValueError('Unknown health lesson mode')
        if self.session is not None:
            self._emit_report('restarted_course')
        if self._has_unsaved_report():
            # Preserve an explicit restart intent while its previous final
            # snapshot is written. It is cancelled by leaving the course and
            # never starts an exercise while this view is hidden.
            self._pending_start_request = ((mode, self.course_id)
                                           if self._final_save_pending and not self.save_error else None)
            if self.video_flow:
                self.video_flow.stop()
            self._pause_pending_session()
            self.pending_recovery = True
            self.active = True
            self.focus = 0
            self.clear_pointer();self.dirty = True;self.queue_draw()
            return self.current
        self.last_mode = mode
        self.pending_recovery = False
        self.history_open = False
        self.lesson_picker_open = False
        self.session = LessonSession(mode=mode,course_id=self.course_id)
        self._autosave_state.update(state='idle', error=None, last_saved_at=None,
                                   last_saved_wall=None, session_id=self.session.session_id,
                                   record_state=None, save_reason=None)
        if self.on_start:
            try:self.on_start(self.session.session_id)
            except Exception:print('health_practice_owner_unavailable',file=sys.stderr,flush=True)
        self.info_open = False
        self._info_resume = False
        if self.selected_lesson_index:
            self.session.action('select_'+self.course['lessons'][self.selected_lesson_index]['id'])
        self.current = dict(self.session.start())
        if self.video_flow:
            self.video_flow.stop()
        self.video_flow = (LessonVideoFlow(self.session, self.video_player, self.video_library)
                           if self.video_enabled else None)
        if self.video_flow:
            self.current = self.video_flow.snapshot()
        self.active = True
        self.report_emitted = False
        self._final_save_pending = False
        self.report_path = self.save_error = None
        self._pending_report = None
        self._report_notifications.clear()
        self.latest_key = self.pose_received = None
        self._reset_body_display()
        self.focus = 2
        self.clear_pointer()
        self.dirty = True
        self.queue_draw()
        self._queue_checkpoint('started', force=True)
        return self.current

    def receive(self, state):
        self._drain_saves()
        if not self.active or self.session is None or self.pending_recovery or self.lesson_picker_open or self.info_open:
            return
        state = state if isinstance(state, dict) else {}
        key = (state.get('pid'), state.get('sequence'), state.get('timestamp_seconds'))
        source, _ = context_key(state)
        selection = state.get('roi_selection')
        history = selection.get('continuity') if isinstance(selection, dict) else None
        hard_reset = (any(state.get(flag) for flag in ('motion_discontinuity', 'pose_discontinuity', 'motion_history_discontinuity', 'stale')) or
                state.get('available') is False or
                isinstance(history, dict) and any(history.get(flag) for flag in
                    ('motion_discontinuity', 'reset_motion_history', 'tracking_gap')) or
                self._display_context is not None and source != self._display_context or
                self.latest_key is not None and key[0] != self.latest_key[0])
        if hard_reset:
            self._reset_body_display()
        new_frame = key != self.latest_key and any(value is not None for value in key)
        if new_frame:
            self.latest_key = key
            self.pose_received = time.monotonic()
        self._accept_current(self.video_flow.update(state) if self.video_flow else self.session.update(state))
        self._display_context = source
        if new_frame or hard_reset:
            self.display_filter.update(self.current.get('display_pose_points') or (),
                                       now=time.monotonic(), frame_key=key,
                                       discontinuity=bool(hard_reset))
        if self.current.get('status') == 'completed':
            self._emit_report('completed')
        else:
            self._queue_checkpoint('periodic')
        self.dirty = True

    def action(self, name):
        name = str(name)
        if name in ('health_home', 'home', 'back'):
            self._pending_start_request = None
        self._drain_saves(recover_ui=False)
        if name == 'health_retry_save':
            if self.save_error and self._pending_report is not None:
                self._persist_report()
                self.focus = min(self.focus, len(self.choices)-1)
                self.dirty = True
                self.queue_draw()
                return self.report_path
            return self.report_path
        if self.pending_recovery and name not in ('health_home', 'home', 'back'):
            # START/Menu must not restart the paused old timer while a frozen
            # report is waiting for explicit retry. New sessions cannot replace it.
            return self.current
        if name == 'health_info':
            return self._open_info()
        if name == 'health_info_back' and self.info_open:
            return self._close_info()
        if name == 'health_ai' and self.info_open and self.on_ai:
            self.pause();self.on_ai();return self.current
        if self.info_open and name not in ('health_home', 'home', 'back'):
            return self.current
        if name == 'health_lessons':
            return self._open_lesson_picker()
        if name in ('health_lessons_previous', 'health_lessons_next') and self.lesson_picker_open:
            maximum = (len(self.course['lessons'])-1)//LESSONS_PER_PAGE
            self.lesson_page = max(0, min(maximum, self.lesson_page+(1 if name.endswith('next') else -1)))
            self.focus = 0;self.clear_pointer();self.dirty = True;self.queue_draw()
            return self.choices
        if name == 'health_lessons_back' and self.lesson_picker_open:
            return self._close_lesson_picker()
        if name.startswith('health_pick_') and self.lesson_picker_open:
            identifier = name[len('health_pick_'):]
            identifiers = tuple(item['id'] for item in self.course['lessons'])
            if identifier not in identifiers:raise ValueError('Unknown health lesson selection')
            resume_after = self._picker_resume
            self.selected_lesson_index = identifiers.index(identifier)
            self._close_lesson_picker(resume=False)
            if self.session is not None:
                self._accept_current(self.video_flow.action('select_'+identifier) if self.video_flow else
                                     self.session.action('select_'+identifier))
                if self.video_flow:
                    self.video_flow.resume_after_video = resume_after
                elif resume_after:
                    self._accept_current(self.session.action('play'))
            self.focus = self.choices.index('health_pause') if 'health_pause' in self.choices else 0
            self._queue_checkpoint('lesson_selected', force=True)
            return self.current if self.session is not None else self.choices
        if self.lesson_picker_open and name not in ('health_home', 'home', 'back'):
            # Host Menu still sends health_pause.  It must not resume a hidden
            # practice clock while the user is choosing a lesson.
            return self.current
        if name == 'health_history':
            self.history_open = True
            self.history = self._load_history()
            self.focus = 0
            self.clear_pointer()
            self.dirty = True
            self.queue_draw()
            return self.history
        if name == 'health_history_back':
            self.history_open = False
            self.focus = 0
            self.clear_pointer()
            self.dirty = True
            self.queue_draw()
            return self.choices
        if name in ('health_home', 'home', 'back'):
            self._pending_start_request = None
            self._emit_report('left_course')
            if self.video_flow:
                self.video_flow.stop()
            self._pause_pending_session()
            if self._has_unsaved_report():
                self.pending_recovery = True
            self.lesson_picker_open = False
            self.info_open = False
            self._info_resume = False
            self.active = False
            self.clear_pointer()
            self.on_home()
            return True
        if name.startswith('health_start_'):
            return self.start(name[len('health_start_'):])
        if name == 'health_restart':
            return self.start(self.last_mode)
        if self.session is None:
            return None
        operation = {'health_pause': 'toggle_pause', 'health_previous': 'previous',
                     'health_next': 'next', 'health_repeat': 'repeat',
                     'health_video_skip': 'video_skip', 'health_video_rewatch': 'video_rewatch'}.get(name, name)
        self._accept_current(self.video_flow.action(operation) if self.video_flow else self.session.action(operation))
        self.focus = min(self.focus, len(self.choices)-1)
        self.clear_pointer()
        if self.current.get('status') == 'completed':
            self._emit_report('completed')
        else:
            self._queue_checkpoint(operation, force=True)
        self.dirty = True
        self.queue_draw()
        return self.current

    def pause(self):
        if self.session is not None and self.current.get('status') == 'running':
            return self.action('pause')
        return self.current

    def resume(self):
        if self.session is not None and self.current.get('status') == 'paused':
            return self.action('play')
        return self.current

    def finish(self):
        if self.session is not None:
            return self.action('finish')
        return None

    def _emit_report(self, reason):
        if self.session is None or self.report_emitted:
            return
        # Freeze exactly once, before IO or callbacks. An explicit retry must
        # not advance lesson time or mutate the first attempt's statistics.
        self._pending_report = self._report_snapshot(reason, final=True)
        self._pending_report['exit_reason'] = reason
        self.report_emitted = True
        self._persist_report()

    def _has_unsaved_report(self):
        return bool(self.save_reports and self._pending_report is not None
                    and (self._final_save_pending or self.save_error))

    def _report_snapshot(self, reason, final=False):
        report = copy.deepcopy(self.session.report())
        if self.video_flow:
            report['teaching_video'] = self.video_flow.report_metadata()
        lesson = self.session.lesson
        phase = self.current.get('phase', self.session.phase)
        report.update(current_lesson_id=self.current.get('lesson_id', lesson['id']),
                      current_lesson_name=self.current.get('lesson_name', lesson['name']),
                      current_phase=phase,
                      current_status=self.current.get('status', self.session.status),
                      ui_stage='lesson_picker' if self.lesson_picker_open else
                               'info' if self.info_open else
                               'history' if self.history_open else phase,
                      record_state='final' if final else 'in_progress',
                      save_reason=str(reason)[:80],
                      autosaved_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        return report

    def _submit_save(self, report, final=False):
        session = self.session
        identifier = report['session_id']
        # Capture this exact session's writer, not self.session at completion.
        # save_report validates identity and receives a frozen explicit payload;
        # it never needs to advance or observe a live LessonSession on the worker.
        save = session.save_report
        try:
            revision = self._report_writer.submit(
                self.report_directory, report, final=final,
                save=lambda directory, frozen:save(directory, report=frozen))
        except (OSError, ValueError, RuntimeError) as error:
            self.save_error = str(error)
            self._autosave_state.update(state='error', error=self.save_error,
                                       session_id=identifier,
                                       record_state=report.get('record_state'),
                                       save_reason=report.get('save_reason'))
            self._final_save_pending = False if final else self._final_save_pending
            self._schedule_save_retry(report, final)
            if final:
                self._notify_final_report(dict(report, report_save_error=self.save_error,
                                               report_save_state='pending'))
            return False
        if revision is None:
            return False
        self._save_revisions[identifier] = revision
        self._retry_snapshot = None
        if final:
            self._final_save_pending = True
        self.save_error = None
        self._autosave_state.update(state='saving', error=None, session_id=identifier,
                                   record_state=report.get('record_state'),
                                   save_reason=report.get('save_reason'))
        return True

    def _queue_checkpoint(self, reason='periodic', force=False):
        if (not self.save_reports or self.session is None or self.report_emitted
                or self._closed):
            return False
        now = time.monotonic()
        if not force and self.current.get('status') != 'running':
            return False
        if not force and now < self._next_autosave:
            return False
        self._next_autosave = now + 5.
        return self._submit_save(self._report_snapshot(reason), final=False)

    def _schedule_save_retry(self, report, final):
        if self._closed:
            return
        self._retry_snapshot = (copy.deepcopy(report), bool(final))
        if self._save_retry_timer is None:
            self._save_retry_timer = GLib.timeout_add(3000, self._retry_autosave)

    def _retry_autosave(self):
        if self._closed or self._retry_snapshot is None:
            self._save_retry_timer = None
            return False
        report, final = self._retry_snapshot
        if self.session is None or report['session_id'] != self.session.session_id:
            self._retry_snapshot = None
            self._save_retry_timer = None
            return False
        if self._autosave_state['state'] == 'saving':
            return True
        self._submit_save(report, final=final)
        return True

    def _notify_final_report(self, report):
        key = (report['session_id'], report['report_save_state'])
        if key in self._final_notifications:
            return
        self._final_notifications[key] = True
        while len(self._final_notifications) > 128:
            self._final_notifications.popitem(last=False)
        if self.session is not None and self.session.session_id == report['session_id']:
            self._report_notifications.add(report['report_save_state'])
        if self.on_report:
            try:
                self.on_report(report)
            except Exception as error:
                print('health_report_callback_failed: '+str(error), file=sys.stderr, flush=True)

    def _drain_saves(self, recover_ui=True):
        if self._save_draining:
            return False
        self._save_idle_pending = False
        self._save_draining = True
        recover = False
        try:
            for result in self._report_writer.results():
                identifier = result['session_id']
                if self._save_revisions.get(identifier) != result['revision']:
                    continue
                current = self.session is not None and self.session.session_id == identifier
                if current:
                    self._autosave_state.update(
                        state='error' if result['error'] else 'saved', error=result['error'],
                        session_id=identifier, record_state=result['report'].get('record_state'),
                        save_reason=result['report'].get('save_reason'))
                    self.save_error = result['error']
                    if not result['error']:
                        self._retry_snapshot = None
                        self.report_path = result['path']
                        self._autosave_state.update(last_saved_at=result['saved_at'],
                                                   last_saved_wall=datetime.datetime.now(datetime.timezone.utc).isoformat())
                    if result['final']:
                        self._final_save_pending = False
                        recover = bool(not result['error'] and self.pending_recovery
                                       and self.active and not self._closed
                                       and not self._autosave_hidden and recover_ui)
                    self.dirty = True
                    if result['error']:
                        self._schedule_save_retry(result['report'], result['final'])
                if result['final']:
                    report = dict(result['report'], report_save_state='pending' if result['error'] else 'saved')
                    if result['error']:
                        report['report_save_error'] = result['error']
                        print('health_report_save_failed: '+result['error'], file=sys.stderr, flush=True)
                    else:
                        report['report_path'] = result['path']
                        self._save_revisions.pop(identifier, None)
                    self._notify_final_report(report)
            if (recover_ui and self.pending_recovery and not self._has_unsaved_report()
                    and self.active and not self._closed and not self._autosave_hidden
                    and self._autosave_state.get('record_state') == 'final'
                    and self._autosave_state.get('state') == 'saved'):
                recover = True
            if recover:
                requested_start = self._pending_start_request
                self.enter()
                if requested_start and self.active and self.course_id == requested_start[1]:
                    self.start(requested_start[0])
        finally:
            self._save_draining = False
        return False

    def autosave_status(self):
        """Lightweight, side-effect-free UI/monitor status, never filesystem IO."""
        result = dict(self._autosave_state)
        result['saving'] = result['state'] == 'saving'
        result['final_pending'] = self._final_save_pending
        result['message'] = ('自动保存中' if result['saving'] else
                             '自动保存暂未完成，将自动重试' if result['state'] == 'error' else
                             '已自动保存' if result['state'] == 'saved' else '练习记录会自动保存')
        return result

    def wait_for_saves(self, timeout=1.5):
        """Tests/shutdown only; normal controls never wait for disk IO."""
        deadline = time.perf_counter() + max(0., float(timeout))
        while True:
            complete = self._report_writer.flush(max(0., deadline-time.perf_counter()))
            self._drain_saves()
            status = self._report_writer.status()
            if not complete:
                return False
            if not status['writing'] and not status['pending']:
                return True
            if time.perf_counter() >= deadline:
                return False

    def _pause_pending_session(self):
        if self.session is not None and self.current.get('status') == 'running':
            self.current = dict(self.session.action('pause'))

    def _persist_report(self):
        report = copy.deepcopy(self._pending_report)
        if report is None:
            return
        if self.save_reports:
            self._submit_save(report, final=True)
        else:
            self.save_error = None
            self._notify_final_report(dict(report, report_save_state='preview'))

    def _load_history(self):
        """Read at most five bounded local reports; never follow linked files."""
        self.history_error = None
        directory = self.report_directory
        if not directory.exists():
            return []
        if directory.is_symlink() or not directory.is_dir():
            self.history_error = '记录目录暂时不可用。'
            return []
        result = []
        try:
            candidates = []
            for path in directory.glob('*.json'):
                # Bound IO per course so another course cannot hide this one.
                if path.name.startswith('taichi-') != (self.course_id=='taichi'):
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                info = path.stat()
                if info.st_size <= 256*1024:
                    candidates.append((info.st_mtime, path))
            # The local store is flat. Only the newest twenty-five candidates
            # are parsed, so damaged historical files cannot cause unbounded IO.
            for _, path in sorted(candidates, key=lambda item:item[0], reverse=True)[:25]:
                try:
                    data = json.loads(path.read_bytes())
                    if (not isinstance(data, dict) or data.get('kind') != self.course['report_kind']
                            or data.get('course_id',self.course_id)!=self.course_id):
                        continue
                    mode = data.get('mode')
                    seconds, repetitions = data.get('active_seconds'), data.get('observed_repetitions')
                    if mode not in MODE_NAMES or any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0 for value in (seconds,repetitions)):
                        continue
                    created = data.get('created_at')
                    try:
                        stamp = datetime.datetime.fromisoformat(created)
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
                        created_label = stamp.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime('%m-%d %H:%M')
                    except (TypeError, ValueError):
                        created_label = '时间未记录'
                    result.append(dict(mode=mode, seconds=int(seconds), repetitions=int(repetitions),
                                       completed=data.get('status')=='completed',
                                       timer_finished=data.get('course_timer_finished') is True,
                                       record_state=data.get('record_state'),
                                       current_lesson_id=data.get('current_lesson_id'),
                                       created=created_label))
                    if len(result) == 5:
                        break
                except (OSError, ValueError, UnicodeError):
                    continue
        except OSError as exc:
            self.history_error = '练习记录暂时无法读取。'
            print('health_history_read_failed: '+str(exc), file=sys.stderr, flush=True)
        return result

    def navigate(self, direction):
        """Discrete gamepad navigation uses the same visible choice order."""
        if direction in ('left', 'up', -1):
            step = -1
        elif direction in ('right', 'down', 1):
            step = 1
        else:
            return None
        self.selection_source = 'discrete'
        self.focus = (self.focus + step) % len(self.choices)
        self.hover = None
        self.progress = 0.
        self.dirty = True
        self.queue_draw()
        return self.choices[self.focus]

    def set_gamepad_focus(self, index):
        """Keep the host's discrete focus independent of gesture freshness."""
        if isinstance(index, bool) or not isinstance(index, int) or index not in range(len(self.choices)):
            return False
        self.selection_source = 'discrete'
        self.focus = index
        self.clear_pointer()
        self.dirty = True
        self.queue_draw()
        return True

    def activate(self):
        # Mouse/gamepad controls remain usable when pose is unavailable; only
        # dwell navigation depends on fresh observed body input.
        if self.choices:
            return self.action(self.choices[self.focus % len(self.choices)])
        return None

    def clear_pointer(self):
        self.pointer = self.pointer_received = None
        self.hover = None
        self.progress = 0.

    def set_pointer(self, point, *args, **kwargs):
        self.update_selection(dict(cursor=point, hover=kwargs.get('hover'),
                                   progress=kwargs.get('progress', 0.)))

    def set_selection(self, result, progress=0.):
        if isinstance(result, dict):
            self.update_selection(result)
        else:
            self.update_selection(dict(cursor=self.pointer, hover=result, progress=progress))

    def update_selection(self, result):
        result = result or {}
        if not self.gesture_navigation or not self.selection_enabled:
            self.clear_pointer()
            return
        raw = result.get('cursor')
        try:
            pointer = tuple(map(float, raw))
            if len(pointer) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in pointer):
                pointer = None
        except (TypeError, ValueError):
            pointer = None
        self.pointer = pointer
        self.pointer_received = time.monotonic() if pointer is not None else None
        if pointer is not None:
            self.selection_source = 'gesture'
        hover = result.get('hover')
        self.hover = hover if pointer is not None and isinstance(hover, int) and hover in range(len(self.choices)) else None
        try:
            progress = float(result.get('progress', 0.))
            self.progress = max(0., min(1., progress)) if math.isfinite(progress) else 0.
        except (TypeError, ValueError):
            self.progress = 0.
        self.dirty = True

    @property
    def highlighted_index(self):
        # Preserve discrete navigation memory without drawing its old focus
        # after body input takes over, even over blank space or a lost hand.
        index = self.hover if self.selection_source == 'gesture' else self.focus
        return index if isinstance(index, int) and not isinstance(index, bool) and index in range(len(self.choices)) else None

    def set_availability(self, ready, message=''):
        self.selection_enabled = bool(ready)
        self.availability_message = str(message or '')
        if not ready:
            self.clear_pointer()
        self.dirty = True

    def set_voice_status(self, enabled, status=''):
        self.voice_enabled = bool(enabled)
        self.voice_status = str(status or ('语音口令' if enabled else '文字指导'))
        self.dirty = True

    def set_audio_inventory(self, snapshot):
        from audio.menu_music import _select_cube
        self.video_player.set_audio_route(_select_cube(snapshot))

    def mapped(self, *_):
        self._autosave_hidden = False
        if (self.pending_recovery and not self._has_unsaved_report()
                and self.session is not None and self.report_emitted):
            requested_start = self._pending_start_request
            self.enter()
            if requested_start and self.active and self.course_id == requested_start[1]:
                self.start(requested_start[0])
        if self.video_flow and not self.info_open and not self.lesson_picker_open:
            self.video_flow.set_companion_suspended(False)
        if self.timer is None:
            self.timer = GLib.timeout_add(34, self.tick)
        self.dirty = True

    def unmapped(self, *_):
        self._autosave_hidden = True
        # Hiding/minimizing the course must not let a monotonic clock silently
        # finish unseen lessons.  Returning leaves an explicit Continue button.
        if self.active and not self._closed:
            self.pause()
            if self.video_flow:self.video_flow.set_companion_suspended(True)
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.clear_pointer()

    def tick(self):
        self._drain_saves()
        if self._closed:
            return False
        if not self.get_mapped():
            return True
        if self.active and self.session is not None and not self.lesson_picker_open and not self.info_open:
            self._accept_current(self.video_flow.snapshot() if self.video_flow else self.session.snapshot())
            if self.current.get('status') == 'completed':
                self._emit_report('completed')
            else:
                self._queue_checkpoint('periodic')
            self.dirty = True
        if self.pointer_received is not None and time.monotonic()-self.pointer_received > .25:
            self.clear_pointer()
            self.dirty = True
        if self.dirty:
            self.queue_draw()
            self.dirty = False
        return True

    def close(self):
        if self._closed:
            return
        self._emit_report('application_closed')
        self._pause_pending_session()
        self._closed = True
        self._pending_start_request = None
        if self._save_retry_timer is not None:
            GLib.source_remove(self._save_retry_timer)
            self._save_retry_timer = None
        complete = self._report_writer.close(timeout=1.5)
        self._drain_saves()
        if not complete and self._pending_report is not None:
            self.save_error = '自动保存尚未完成，请保留最近的自动保存记录。'
            self._autosave_state.update(state='error', error=self.save_error)
            self._notify_final_report(dict(self._pending_report,
                                           report_save_state='pending', report_save_error=self.save_error))
        self.video_player.close()
        self.active = False
        self.unmapped()

    def click(self, _, event):
        if event.button != 1:
            return False
        index = self.hit_index(event.x/max(1, self.get_allocated_width()),
                               event.y/max(1, self.get_allocated_height()))
        if index is None:
            return False
        self.selection_source = 'discrete'
        self.focus = index
        self.action(self.choices[index])
        return True

    def _text(self, cr, value, x, y, size=16, color=INK, width=None,
              height=None, bold=False, centered=False):
        value = str(value or '')
        key = (value, size, width, height, bold, centered)
        layout = self.text_cache.get(key)
        if layout is None:
            layout = PangoCairo.create_layout(cr)
            description = Pango.FontDescription('Noto Sans CJK SC')
            description.set_absolute_size(int(size*Pango.SCALE))
            if bold:
                description.set_weight(Pango.Weight.BOLD)
            layout.set_font_description(description)
            layout.set_text(value, -1)
            if width is not None:
                layout.set_width(max(1, int(width*Pango.SCALE)))
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            if height is not None:
                layout.set_height(max(1, int(height*Pango.SCALE)))
                layout.set_ellipsize(Pango.EllipsizeMode.END)
            if centered:
                layout.set_alignment(Pango.Alignment.CENTER)
            self.text_cache[key] = layout
            self.layout_builds += 1
            while len(self.text_cache) > 160:
                self.text_cache.popitem(last=False)
        else:
            self.text_cache.move_to_end(key)
        cr.set_source_rgb(*color)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)
        cr.new_path()

    def _background(self, w, h):
        if self.background_key == (w, h):return
        self.background = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(self.background);cr.set_source_rgb(*PAPER);cr.paint()
        scale, ox, oy = design_transform(w, h)
        cr.translate(ox, oy);cr.scale(scale, scale)
        # Fixed paper fibers and contour ornament are rasterized once per size.
        cr.set_source_rgba(.34,.30,.22,.018);cr.set_line_width(.5)
        for y in range(5,540,8):
            cr.move_to(0,y);cr.line_to(960,y+.35)
        cr.stroke()
        cr.set_line_width(.7)
        for index in range(5):
            cr.set_source_rgba(*GOLD,.035)
            cr.move_to(706+index*18,0)
            cr.curve_to(790+index*16,44,774+index*18,75,960,107+index*18)
            cr.stroke()
        cr.set_source_rgba(*INK,.12);cr.set_line_width(.7)
        cr.move_to(34,113);cr.line_to(926,113);cr.stroke()
        cr.set_source_rgb(*CINNABAR);cr.rectangle(34,113,36,2);cr.fill()
        cr.set_source_rgba(*INK,.08);cr.move_to(34,454);cr.line_to(926,454);cr.stroke()
        self.background_key=(w,h);self.background_builds+=1

    def _panel(self, cr, box):
        a,b,c,d=box
        cr.set_source_rgba(*INK,.035)
        _rounded(cr,a,b+3,c-a,d-b,15);cr.fill()
        cr.set_source_rgb(.996,.991,.974)
        _rounded(cr,a,b,c-a,d-b,15);cr.fill_preserve()
        cr.set_source_rgba(*INK,.12);cr.set_line_width(.8);cr.stroke()

    def _badge(self,cr,label,x,y,width=104,color=GREEN):
        cr.set_source_rgba(*color,.085)
        _rounded(cr,x,y,width,26,13);cr.fill()
        cr.set_source_rgb(*color);cr.arc(x+12,y+13,2.3,0,math.tau);cr.fill()
        self._text(cr,label,x+21,y+5,12,color=color,width=width-27,height=19)

    def _course_header(self,cr,title,subtitle,index=None):
        x=34
        if index is not None:
            cr.set_source_rgb(*INK);_rounded(cr,34,29,46,45,11);cr.fill()
            self._text(cr,'{:02}'.format(index+1),36,36,21,color=(.90,.81,.61),bold=True,width=42,centered=True)
            x=96
        self._text(cr,title,x,26,29,bold=True,width=650-(x-34),height=43)
        self._text(cr,subtitle,35,79,13,color=MUTED,width=725,height=24)

    def _welcome_art(self,cr):
        if self._welcome_art_cache is None:
            surface=cairo.ImageSurface(cairo.FORMAT_RGB24,424,286)
            art=cairo.Context(surface)
            art.set_source_rgb(*PAPER);art.paint()
            gradient=cairo.LinearGradient(0,0,424,286)
            gradient.add_color_stop_rgb(0,.12,.29,.27);gradient.add_color_stop_rgb(1,.08,.21,.21)
            art.set_source(gradient);_rounded(art,0,0,424,286,17);art.fill()
            art.save();_rounded(art,0,0,424,286,17);art.clip()
            art.set_line_width(.8)
            for radius,alpha in ((73,.17),(95,.12),(119,.075)):
                art.set_source_rgba(.78,.79,.62,alpha);art.arc(267,126,radius,0,math.tau);art.stroke()
            art.set_source_rgba(.90,.79,.55,.8);art.rectangle(24,26,3,18);art.fill()
            self._text(art,'动作示意',37,26,14,color=(.91,.91,.83),bold=True)
            neutral=[None]*17
            for index,point in {5:(.42,.28),6:(.58,.28),7:(.29,.20),8:(.71,.20),9:(.27,.10),10:(.73,.10),11:(.44,.54),12:(.56,.54),13:(.40,.73),14:(.60,.73),15:(.36,.91),16:(.64,.91)}.items():neutral[index]=(*point,1.)
            self._body(art,neutral,(105,47,394,241),True,on_dark=True)
            art.set_source_rgba(.91,.88,.74,.18);art.move_to(24,248);art.line_to(400,248);art.stroke()
            self._text(art,'01 看示范    02 跟练    03 看建议',24,262,13,color=(.88,.88,.79),width=376,centered=True)
            art.restore();self._welcome_art_cache=surface
        cr.set_source_surface(self._welcome_art_cache,34,130);cr.paint()

    def _body(self, cr, points, box, reference=False, highlight=(), on_dark=False,
              held=(), anchor_points=None):
        viewport = getattr(self, 'observed_viewport', None)
        if reference:
            points = project_reference(points, box)
        elif viewport is not None:
            # A held hip may be drawn faintly, but cannot anchor the viewport.
            viewport.project(anchor_points if anchor_points is not None else points,
                             box, now=time.monotonic())
            points = project_with_transform(points, box, viewport.transform, True, 16/9)
        else:
            points = project_body(points, box)
        cr.save()
        if not reference:
            a,b,c,d=box;cr.rectangle(a,b,c-a,d-b);cr.clip()
        tone=(.87,.90,.80) if on_dark else GREEN
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        # The reference-only circle is explicitly an illustration. Actual
        # observation draws no invented head, wrist, ankle or connecting edge.
        if reference and points[5] and points[6]:
            a, b = points[5], points[6]
            cx, cy = (a[0]+b[0])/2., (a[1]+b[1])/2.
            radius = max(9., min(19., math.hypot(a[0]-b[0], a[1]-b[1])*.20))
            head = points[0]
            hx, hy = head[:2] if head is not None else (cx, cy-radius*2.0)
            cr.set_source_rgba(*tone, .13)
            cr.arc(hx, hy, radius*1.08, 0, math.tau)
            cr.fill()
            cr.set_source_rgb(*tone)
            cr.set_line_width(2.4)
            cr.arc(hx, hy, radius, 0, math.tau)
            cr.stroke()
        for first, second in BODY_EDGES:
            a, b = points[first], points[second]
            if a is None or b is None:
                continue
            if not reference and (first in held or second in held):
                cr.set_source_rgba(*GOLD,.34);cr.set_line_width(1.4)
                cr.set_dash((2.,3.));cr.move_to(*a[:2]);cr.line_to(*b[:2]);cr.stroke();cr.set_dash(())
                continue
            weak=not reference and min(a[2],b[2])<.15
            if weak:
                cr.set_source_rgba(*GOLD,.85);cr.set_line_width(1.6)
                cr.set_dash((4.,3.));cr.move_to(*a[:2]);cr.line_to(*b[:2]);cr.stroke();cr.set_dash(())
                continue
            cr.set_source_rgba(*tone, .12 if reference else .10)
            cr.set_line_width(14. if reference else 11.)
            cr.move_to(*a[:2]);cr.line_to(*b[:2]);cr.stroke()
            cr.set_source_rgba(*tone, .92 if reference else .85)
            cr.set_line_width(5. if reference else 3.8)
            cr.move_to(*a[:2]);cr.line_to(*b[:2]);cr.stroke()
        for index, point in enumerate(points[5:17], 5):
            if point is None:
                continue
            if not reference and index in held:
                cr.set_source_rgba(*GOLD,.38);cr.set_line_width(1.3)
                cr.set_dash((2.,3.));cr.arc(*point[:2],4.3,0,math.tau);cr.stroke();cr.set_dash(())
                continue
            if not reference and point[2]<.15:
                cr.set_source_rgba(*GOLD,.95);cr.set_line_width(1.7)
                cr.arc(*point[:2],4.8,0,math.tau);cr.stroke()
                continue
            cr.set_source_rgb(*((.18,.34,.31) if on_dark else (.99,.98,.93)))
            cr.arc(*point[:2], 4.4, 0, math.tau)
            cr.fill_preserve()
            cr.set_source_rgb(*tone)
            cr.set_line_width(1.5)
            cr.stroke()
            if index in highlight:
                cr.set_source_rgba(*GOLD, .85)
                cr.set_line_width(2)
                cr.arc(*point[:2], 8, 0, math.tau)
                cr.stroke()
        cr.restore()

    def _buttons(self, cr):
        for index,(identifier,label,subtitle,box) in enumerate(self.buttons):
            a,b,c,d=box;focused=index==self.highlighted_index
            hovered=index==self.hover and self.gesture_navigation
            emphasis=focused
            pick=identifier.startswith('health_pick_')
            # Filled selection, quiet secondary controls; hit geometry unchanged.
            cr.set_source_rgba(*INK,.035);_rounded(cr,a,b+2,c-a,d-b,11);cr.fill()
            cr.set_source_rgb(*(INK if emphasis else (.998,.993,.978)))
            _rounded(cr,a,b,c-a,d-b,11);cr.fill_preserve()
            cr.set_source_rgba(*(GOLD if emphasis else INK),.70 if emphasis else .14)
            cr.set_line_width(1.3 if emphasis else .8);cr.stroke()
            foreground=(.98,.97,.92) if emphasis else INK
            secondary=(.78,.83,.75) if emphasis else MUTED
            if emphasis:
                cr.set_source_rgb(*CINNABAR);_rounded(cr,a+1,b+12,3,max(12,d-b-24),1.5);cr.fill()
            if subtitle:
                number={'health_start_learn':'01','health_start_follow':'02','health_start_free':'03'}.get(identifier,'')
                self._text(cr,number,a+20,b+20,21,color=(.85,.76,.55) if emphasis else GOLD,width=42,bold=True)
                self._text(cr,label,a+77,b+10,21,color=foreground,bold=True,width=c-a-98,height=30)
                self._text(cr,subtitle,a+78,b+43,12,color=secondary,width=c-a-100,height=22)
                self._text(cr,'›',c-25,b+19,25,color=secondary,width=16,centered=True)
            elif pick:
                lesson_id=identifier[len('health_pick_'):]
                lesson_index=next(i for i,item in enumerate(self.course['lessons']) if item['id']==lesson_id)
                lesson=self.course['lessons'][lesson_index]
                number=('备' if lesson_id=='prepare' else '收' if lesson_id=='finish' else str(lesson_index)) if self.course_id=='baduanjin' else str(lesson_index+1)
                name=lesson['name']
                self._text(cr,number.zfill(2) if number.isdigit() else number,a+15,b+14,20,color=(.86,.76,.54) if emphasis else GOLD,bold=True,width=40,centered=True)
                cr.set_source_rgba(*(foreground),.16);cr.set_line_width(.7)
                cr.move_to(a+65,b+17);cr.line_to(a+65,d-17);cr.stroke()
                self._text(cr,name,a+81,b+18,16,color=foreground,bold=emphasis,width=c-a-92,height=25)
            else:
                self._text(cr,label,a+6,b+(d-b-23)/2.,16,color=foreground,bold=emphasis,width=c-a-12,centered=True,height=27)
            if hovered and self.progress>0:
                cr.set_source_rgb(*GOLD);cr.rectangle(a+12,d-5,(c-a-24)*self.progress,3);cr.fill()

    def _welcome(self, cr):
        self._course_header(cr,self.course['subtitle']+'跟练','选择适合自己的练习方式')
        selected=self.course['lessons'][self.selected_lesson_index]
        selected_identity=guide_identity(dict(course_id=self.course_id,lesson_id=selected['id'],
            lesson_name=selected['name'],lesson_index=self.selected_lesson_index,
            lesson_count=len(self.course['lessons'])),self.course)
        self._badge(cr,'从'+selected_identity['ordinal']+'开始',774,34,150)
        self._welcome_art(cr)
        self._text(cr,'当前动作  '+selected['name'],38,426,14,color=GREEN,bold=True,width=670,height=22)
        self._text(cr,'按自身舒适幅度练习，不追求极限伸展。',38,447,12,color=MUTED,width=670,height=19)
        self._text(cr,'Menu 暂停 / 继续    B 逐级返回',38,478,12,color=MUTED,width=438,height=22)
        self._text(cr,'Guide 退出课程',38,499,11,color=MUTED,width=438,height=19)
        self._buttons(cr)

    def _course_information(self, cr):
        self._course_header(cr, '课程说明', self.course['subtitle']+' · 素材来源与功能说明')
        self._panel(cr, (34, 119, 926, 448))
        clip = self._info_clip or {}
        sources = self.course.get('sources') or ()
        primary = sources[0] if sources else {}
        self._text(cr, '教学素材', 55, 137, 18, color=GREEN, bold=True)
        self._text(cr, clip.get('attribution') or primary.get('title') or '来源记录见本地素材清单',
                   55, 169, 17, color=INK, width=848, height=56)
        self._text(cr, primary.get('url') or '', 55, 226, 12, color=MUTED, width=848, height=37)
        self._text(cr, '教学视频用于本地学习演示，保留原画面、原声与片内署名。',
                   55, 268, 14, color=MUTED, width=848, height=32)
        self._text(cr, '本系统提供骨架示意、可见关节提示、练习记录与交互控制。',
                   55, 310, 16, color=GREEN, bold=True, width=848, height=36)
        self._text(cr, '跟练小窗静音循环当前示范，不与自己的动作逐帧同步；可用“回看视频”放大观看。',
                   55, 354, 14, color=INK, width=848, height=43)
        self._text(cr, '二维骨架不判断掌型、呼吸、发力、真实重心或专业动作合格。',
                   55, 406, 13, color=MUTED, width=848, height=28)
        self._buttons(cr)

    def _lesson_picker(self, cr):
        pages=(len(self.course['lessons'])-1)//LESSONS_PER_PAGE+1
        self._course_header(cr,'选择动作',self.course['subtitle']+' · 选式期间暂停计时')
        self._badge(cr,'第 {} / {} 页'.format(self.lesson_page+1,pages),774,34,150)
        self._text(cr,'方向键选择 · A 确认 · B 返回',35,429,12,color=MUTED,width=600,height=20)
        self._buttons(cr)

    def _lesson(self, cr):
        return draw_practice(self, cr, save_status=self.autosave_status())

    def _video(self, cr):
        state=self.current;media=state.get('video') or {};clip=media.get('clip') or {}
        self._course_header(cr,state.get('lesson_name'),'真人示范  →  跟练  →  动作建议',int(state.get('lesson_index') or 0))
        self._badge(cr,'视频已暂停' if state.get('status')=='paused' else '正在示范',774,34,150)
        box=(34,118,674,424);self._panel(cr,box)
        frame=self.video_player.frame()
        if frame:
            surface,width,height=frame;scale=min(640/width,306/height)
            cr.save();cr.rectangle(*box[:2],640,306);cr.clip()
            cr.set_source_rgb(.055,.08,.075);cr.paint()
            cr.translate(34+(640-width*scale)/2,118+(306-height*scale)/2);cr.scale(scale,scale)
            cr.set_source_surface(surface,0,0);cr.paint();cr.restore()
        else:
            self._text(cr,'视频暂不可用' if media.get('error_message') else '正在准备教学视频…',66,245,21,color=MUTED,width=574,centered=True)
        cr.set_source_rgb(*INK);_rounded(cr,692,118,234,306,15);cr.fill()
        self._text(cr,'01',712,135,38,color=(.82,.73,.53),bold=True,width=160)
        self._text(cr,'真人示范',713,193,20,color=(.98,.97,.91),bold=True,width=193)
        self._text(cr,'先看清完整动作，再按自己的节奏跟练。',713,235,15,color=(.88,.90,.83),width=193,height=61)
        cr.set_source_rgba(.91,.88,.71,.19);cr.set_line_width(.7);cr.move_to(713,304);cr.line_to(905,304);cr.stroke()
        self._text(cr,'视频不计入练习时长。可随时跳过，跟练时也可回看。',713,318,12,color=(.75,.82,.75),width=193,height=53)
        line=media.get('error_message') or ('已暂停' if state.get('status')=='paused' else
             '视频音频暂不可用，已静音播放' if media.get('audio_error') else
             '视频静音 · 音响未连接' if media.get('muted') else '原视频讲解')
        self._text(cr,line,713,380,12,color=(.86,.75,.53),width=193,height=37)
        position=max(0,int(media.get('position_seconds') or 0))
        duration=max(0,int(media.get('duration_seconds') or clip.get('duration_seconds') or 0))
        cr.set_source_rgb(*GOLD);cr.rectangle(34,422,640*min(1.,position/max(1,duration)),2);cr.fill()
        self._text(cr,'{:02}:{:02} / {:02}:{:02}'.format(position//60,position%60,duration//60,duration%60),37,432,12,color=GREEN,width=155,height=20)
        self._text(cr,'教学视频 · 来源于网络',205,432,12,color=MUTED,width=718,height=20)
        self._buttons(cr)

    def _completed(self, cr):
        state = self.current
        self._text(cr, '本次练习结束', 34, 27, 30, bold=True)
        self._text(cr, self.course['title']+' · '+MODE_NAMES.get(state.get('mode'), '跟练'), 35, 73, 15, color=MUTED)
        self._panel(cr, (124, 135, 836, 420))
        self._text(cr, '练习记录', 166, 163, 22, bold=True, color=GREEN)
        elapsed = max(0, int(state.get('session_elapsed') or 0))
        repetitions = sum(int(value) for value in (state.get('counts') or {}).values()
                          if isinstance(value, (int, float)) and math.isfinite(value))
        self._text(cr, '{} 分 {} 秒'.format(elapsed//60, elapsed%60), 166, 215, 34, bold=True)
        self._text(cr, '本次跟练时长', 166, 266, 14, color=MUTED)
        self._text(cr, '已记录' if self.course_id=='taichi' else '{} 次'.format(repetitions), 567, 215, 34, bold=True)
        self._text(cr, '时长与可见关节跟练' if self.course_id=='taichi' else '可观测动作记录', 567, 266, 14, color=MUTED)
        message = ('记录尚未保存，可重试保存。' if self.save_error else
                   '正在自动保存练习记录。' if self._final_save_pending else
                   '练习记录已自动保存。' if self.report_path else '练习记录会自动保存。')
        self._text(cr, message, 166, 322, 16, color=GREEN, width=635, height=48)
        self._text(cr, '记录反映可观察的练习过程，不代表专业动作考核。', 166, 377, 13,
                   color=MUTED, width=630)
        self._buttons(cr)

    def _history(self, cr):
        self._text(cr, self.course['title']+'练习记录', 34, 27, 30, bold=True)
        self._text(cr, '本机最近五次 · 练习时间与可见动作记录', 35, 73, 15, color=MUTED)
        if not self.history:
            self._panel(cr, (124, 145, 836, 420))
            self._text(cr, '还没有练习记录', 204, 230, 25, color=GREEN, bold=True,
                       width=552, centered=True)
            self._text(cr, self.history_error or '完成或退出一次跟练后，记录会保存在这里。',
                       174, 282, 15, color=MUTED, width=612, centered=True)
        else:
            for index, item in enumerate(self.history):
                top = 126+index*58
                self._panel(cr, (65, top, 895, top+49))
                self._text(cr, item['created'], 82, top+15, 14, color=MUTED, width=128)
                self._text(cr, MODE_NAMES[item['mode']], 224, top+13, 18, bold=True, width=160)
                seconds = item['seconds']
                self._text(cr, '{}分{}秒'.format(seconds//60, seconds%60), 403, top+15,
                           15, color=GREEN, width=128)
                self._text(cr, '跟练记录' if self.course_id=='taichi' else '观测{}次'.format(item['repetitions']), 546, top+15,
                           15, color=GREEN, width=135)
                status = ('已自动保留进度' if item.get('record_state') == 'in_progress' else
                          '课程计时完成' if item.get('timer_finished') else
                          '手动结束' if item['completed'] else '中途离开')
                self._text(cr, status, 731, top+15,
                           14, color=MUTED, width=135)
            self._text(cr, '计时完成只表示课程播放结束，不代表动作合格。', 68, 432, 13,
                       color=MUTED, width=820, centered=True)
        self._buttons(cr)

    def _recovery(self, cr):
        report = self._pending_report or {}
        saving = self._final_save_pending and not self.save_error
        self._text(cr, '正在自动保存' if saving else '练习记录待重试', 34, 27, 30, bold=True)
        self._text(cr, '无需点击保存，完成后会继续' if saving else '正在自动重试，也可选择下方重试保存', 35, 73, 15, color=MUTED)
        self._panel(cr, (124, 135, 836, 420))
        self._text(cr, MODE_NAMES.get(report.get('mode'), '跟练'),
                   166, 163, 22, bold=True, color=GREEN)
        elapsed = max(0, int(report.get('active_seconds') or 0))
        repetitions = max(0, int(report.get('observed_repetitions') or 0))
        self._text(cr, '{} 分 {} 秒'.format(elapsed//60, elapsed%60),
                   166, 215, 34, bold=True)
        self._text(cr, '首次保存时的练习时长', 166, 266, 14, color=MUTED)
        self._text(cr, '已记录' if report.get('course_id')=='taichi' else '{} 次'.format(repetitions), 567, 215, 34, bold=True)
        self._text(cr, '时长与可见关节跟练' if report.get('course_id')=='taichi' else '可观测动作记录', 567, 266, 14, color=MUTED)
        self._text(cr, '正在保留本次练习记录，请稍候。' if saving else '暂时未能写入，原记录已保留并会自动重试。', 166, 322, 17,
                   color=GREEN, width=635, height=42)
        self._text(cr, '离开期间已暂停计时，重试保留首次记录。',
                   166, 377, 13, color=MUTED, width=630)
        self._buttons(cr)

    def paint(self, cr, size=(960, 540)):
        w, h = size
        self._background(w, h)
        cr.set_source_surface(self.background, 0, 0)
        cr.paint()
        scale, ox, oy = design_transform(w, h)
        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)
        if self.pending_recovery:
            self._recovery(cr)
        elif self.info_open:
            self._course_information(cr)
        elif self.lesson_picker_open:
            self._lesson_picker(cr)
        elif self.history_open:
            self._history(cr)
        elif self.session is None:
            self._welcome(cr)
        elif self.current.get('status') == 'completed':
            self._completed(cr)
        elif self.current.get('phase') == 'video':
            self._video(cr)
        else:
            self._lesson(cr)
        cr.restore()
        if self.pointer is not None and self.gesture_navigation:
            x, y = self.pointer[0]*w, self.pointer[1]*h
            cr.set_source_rgba(*GREEN, .13)
            cr.arc(x, y, 13*scale, 0, math.tau);cr.fill()
            cr.set_source_rgb(*GREEN)
            cr.arc(x, y, 5*scale, 0, math.tau);cr.fill()
        return False

    def draw(self, _, target):
        ow, oh = self.get_allocated_width(), self.get_allocated_height()
        if ow < 1 or oh < 1:
            return False
        layout = self.policy.layout(ow, oh)
        w, h = layout.render_size
        if self.buffer is None or (self.buffer.get_width(), self.buffer.get_height()) != (w, h):
            self.buffer = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        self.paint(cairo.Context(self.buffer), (w, h))
        present(target, self.buffer, layout, PAPER)
        return False
