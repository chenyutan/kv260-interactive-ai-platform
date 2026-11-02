"""One gamepad host for menus and desktop pointer; games own their inputs."""
import time
from gi.repository import Gtk, GLib
from .device import GamepadReader
from .pointer import DesktopPointer
from .navigation import Navigation
from .dialog_navigation import DialogNavigation
from ink_feedback import menu_message


class HostControls:
    def __init__(self, suite):
        self.suite = suite
        self.reader = GamepadReader()
        self.pointer = DesktopPointer()
        self.navigation = Navigation()
        self.dialog_navigation = DialogNavigation()
        self.connected = False
        self.name = ''
        self.sequence = 0
        self.last = time.monotonic()
        self.closed = False
        self.navigation_until=0.
        self.last_error = ''
        self.error_count = 0
        self.tick_count = 0
        self.last_success = None
        self._last_error_log = float('-inf')
        self.timer = GLib.timeout_add(16, self.tick)

    def dialog(self):
        for window in reversed(Gtk.Window.list_toplevels()):
            if window is self.suite or not window.get_visible():
                continue
            belongs=(getattr(window,'get_transient_for',lambda:None)() is self.suite)
            accepts=getattr(window,'get_accept_focus',lambda:True)()
            if (isinstance(window, Gtk.Dialog) or window is getattr(self.suite, 'device_view', None)
                    or window is getattr(self.suite,'display_dialog',None) or (belongs and accepts)):
                return window
        return None

    def _ownership(self):
        if not hasattr(self, 'priority'):
            from gamepad.priority import InputPriority
            self.priority = InputPriority()
        return self.priority

    def _update_priority(self, snapshot, now):
        previous_connection = getattr(self, 'connected', False)
        self.connected = bool(snapshot.get('connected'))
        self.name = snapshot.get('name', '')
        if self.connected != previous_connection:
            import sys
            print('[gamepad-host] connected=%s name=%r' % (self.connected, self.name),
                  file=sys.stderr, flush=True)
        priority = self._ownership()
        priority.update(snapshot, now)
        self.navigation_until = priority.navigation_until
        return priority

    def gestures_blocked(self, now=None):
        """Refresh ownership without consuming events needed by tick()."""
        now = time.monotonic() if now is None else now
        priority = self._update_priority(self.reader.snapshot(consume_edges=False), now)
        return priority.gestures_blocked(now, getattr(self.suite, 'control_mode', 'body'))

    def navigation_active(self,now=None):
        now = time.monotonic() if now is None else now
        return self._ownership().navigation_active(now, getattr(self.suite, 'control_mode', 'body'))

    def tick(self):
        # A GTK timeout is removed when its callback raises. Keep subsequent
        # inputs working while preserving the full first/recurrent traceback
        # and an inspectable diagnostic; never replay a failed action edge.
        self.tick_count = getattr(self, 'tick_count', 0) + 1
        try:
            result = self._tick()
        except Exception as exc:
            import logging
            now = time.monotonic()
            error = '%s: %s' % (type(exc).__name__, exc)
            previous = getattr(self, 'last_error', '')
            self.last_error = error
            self.error_count = getattr(self, 'error_count', 0) + 1
            if error != previous or now - getattr(self, '_last_error_log', float('-inf')) >= 5.0:
                logging.getLogger('gamepad.host').exception('Gamepad host tick failed; retrying next tick')
                self._last_error_log = now
            return not (self.closed or self.suite.stop.is_set())
        if getattr(self, 'last_error', ''):
            import sys
            print('[gamepad-host] recovered after errors=%d tick=%d' %
                  (self.error_count, self.tick_count), file=sys.stderr, flush=True)
        self.last_error = ''
        self.last_success = time.monotonic()
        return result

    def _tick(self):
        if self.closed or self.suite.stop.is_set():
            return False
        s = self.suite
        now = time.monotonic()
        snapshot = self.reader.snapshot()
        self._update_priority(snapshot, now)
        suppress = getattr(s, 'suppress_gesture_navigation', None)
        if suppress is not None:
            suppress(now)
        dt = max(0., min(.05, now-self.last)); self.last = now
        dialog = self.dialog()
        scrolling = (dialog is not None or getattr(s,'menu_level',None)=='photo') and not getattr(dialog,'gamepad_owns_shoulders',False)
        pointer = self.pointer.update(snapshot, dt, enabled=scrolling) or {}
        buttons = snapshot.get('pressed', ())
        released = snapshot.get('released', ())
        back = 'BACK' in released and not pointer.get('back_release_consumed')
        if not self.connected:
            return True
        if dialog is not None:
            if hasattr(dialog, 'gamepad'):
                # Photo files use an explicit list/button navigation model.
                # Avoid GTK chooser child-focus ordering and combo popups.
                local = snapshot
                if pointer.get('back_release_consumed'):
                    local = dict(snapshot, released=tuple(key for key in released if key != 'BACK'))
                dialog.gamepad(local)
                return True
            if not hasattr(self,'dialog_navigation'):self.dialog_navigation=DialogNavigation()
            self.dialog_navigation.update(dialog,snapshot,now,
                back_release_consumed=pointer.get('back_release_consumed',False))
            return True
        if getattr(s,'menu_level',None)=='photo':
            local = snapshot
            if pointer.get('back_release_consumed'):
                local = dict(snapshot, released=tuple(key for key in released if key != 'BACK'))
            s.photo_view.gamepad(local)
            return True
        if getattr(s,'health_active',False):
            view=s.health_view
            if 'START' in buttons:view.action('health_pause')
            elif 'GUIDE' in buttons:view.action('health_home')
            elif 'B' in buttons or back:
                view.back()
            else:
                # A connected but idle pad must not erase the body cursor on
                # health welcome/pause pages. Only recent pad input owns focus.
                if not self.navigation_active(now):return True
                choices,rects=tuple(view.choices),tuple(view.rectangles)
                index=self.navigation.update(snapshot,choices,rects,('health',getattr(view,'course_id',None),choices),now)
                if index is not None:
                    view.set_gamepad_focus(index)
                    if 'A' in buttons:view.action(choices[index])
            return True
        if 'START' in buttons:
            if not s.menu_open:s.open_menu()
            elif s.menu_level == 'control':s.cancel_control_choice()
            elif s.child:s.menu_back()
            return True
        if 'GUIDE' in buttons:
            s.open_menu(main=True);return True
        if not s.menu_open:
            return True
        if not self.navigation_active(now):return True
        if 'B' in buttons or back:
            s.navigate_back()
            return True
        if s.switching or s.pending_action or s.visitor_busy:
            return True
        if s.entry_pending:
            s.entry_deadline=now+s.entry_pending;s.entry_pending=0.
        if s.entry_deadline:
            if now < s.entry_deadline:
                s.set_status('入场倒计时 %d 秒'%int(s.entry_deadline-now+1));return True
            s.entry_deadline=0.
        if s.menu_level=='main':
            view,choices,rects=s.menu,s.menu.mode_choices,s.menu.rectangles
        elif s.menu_level in ('actions','control','visitor_confirm'):
            view,choices,rects=s.actions,s.actions.choices,s.actions.rectangles
        elif s.menu_level=='visitor':
            view,choices,rects=s.visitor_view,s.visitor_view.choices,s.visitor_view.rectangles
        elif s.menu_level=='pose':
            view,choices,rects=s.pose_view,s.pose_view.choices,s.pose_view.rectangles
        elif s.menu_level=='ai':
            view,choices,rects=s.ai_view,s.ai_view.choices,s.ai_view.rectangles
            if not any(getattr(view,key,False) for key in ('phone_open','clear_confirm','project_picker')):
                # AI reading owns shoulder scrolling locally. The right-stick
                # pointer still runs above, but no global wheel is generated.
                # Keep focus and dwell targets unchanged while reading.
                if 'Y' in buttons:
                    view.scroll_follow()
                    if view.hover in range(len(choices)):view.set_gamepad_focus(view.hover)
                    return True
                if view.controller_scroll(snapshot,dt):
                    if view.hover in range(len(choices)):view.set_gamepad_focus(view.hover)
                    return True
        else:return True
        page=(s.navigation_token,s.menu_level,s.action_page,tuple(choices))
        # Remember the card only on non-destructive main-menu pages. Confirm
        # pages always start fresh; returning from a game keeps its card.
        memory_key=('main',getattr(s.menu,'section','home')) if s.menu_level=='main' else None
        preserve_identity=s.menu_level=='ai'
        default_choice=None
        if preserve_identity:
            overlay=('phone' if getattr(view,'phone_open',False) else 'projects' if getattr(view,'project_picker',False) else
                     'clear' if getattr(view,'clear_confirm',False) else 'answer')
            # Enabled choices are dynamic during AI requests. They are not a
            # new navigation page; only a fresh visit or overlay is one.
            page=(s.navigation_token,'ai',s.action_page,overlay)
            memory_key=('ai',s.navigation_token,overlay)
            kind=getattr(view,'kind','guide')
            default_choice={'phone':'ai_phone_close','projects':'ai_project_close',
                'clear':'ai_clear_cancel'}.get(overlay,'ai_system' if kind=='guide' and
                    getattr(view,'project',None)=='system' else 'ai_kind:'+kind)
        index=self.navigation.update(snapshot,choices,rects,page,now,memory_key=memory_key,
            preserve_identity=preserve_identity,default_choice=default_choice)
        message='方向键选择 · A 确认 · B 返回 · Menu 菜单 · 右摇杆鼠标 / R3 点击'
        message=menu_message(s,now,message)
        # PoseView exposes update_selection for its return button but keeps
        # camera availability in its own preview status, unlike menu canvases.
        availability = getattr(view, 'set_availability', None)
        if callable(availability):
            availability(True,message)
        if index is not None:
            if s.menu_level == 'pose':
                # This page uses a native button instead of a painted card.
                # Only keyboard navigation reaches here; right-stick mouse
                # ownership leaves the native hover/focus untouched.
                view.return_button.grab_focus()
            r=rects[index];self.sequence+=1
            view.update_selection(dict(cursor=((r[0]+r[2])/2,(r[1]+r[3])/2),
                hover=index,progress=0.,sample_key=('gamepad',self.sequence)))
            if 'A' in buttons:
                choice=choices[index]
                if s.menu_level=='ai':s.ai_select(choice)
                elif s.menu_level=='control':s.choose_control(choice)
                elif s.menu_level in ('visitor','visitor_confirm'):s.visitor_choose(choice)
                elif s.menu_level=='actions':s.choose_action(choice)
                elif s.menu_level=='pose':s.open_menu(main=True)
                else:s.select(choice)
        return True

    def close(self):
        if self.closed:return
        self.closed=True
        if self.timer:GLib.source_remove(self.timer);self.timer=None
        self.reader.close();self.pointer.close()
