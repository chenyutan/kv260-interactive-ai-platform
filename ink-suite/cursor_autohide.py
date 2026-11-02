"""One host-owned idle cursor for all native/XEmbed experiences.

XFixes hides the screen cursor, including explicit cursors in foreign Plug
windows. It does not change any widget cursor or focus. Only this application's
mapped, active windows are eligible; leaving them immediately balances hide.
The dedicated X11 connection also releases its hide on process termination.
"""
import ctypes
import ctypes.util
import math
import sys
import time


class CursorPolicy:
    def __init__(self, hide_seconds=2.):
        if not math.isfinite(hide_seconds) or hide_seconds <= 0:
            raise ValueError('Cursor idle delay must be positive and finite')
        self.hide_seconds = hide_seconds
        self.reset()

    def reset(self):
        self.visible = True
        self.point = None
        self.last_motion = None
        self.buttons_down = False

    def update(self, now, pointer, *, eligible=True, buttons_down=False):
        try:
            point = tuple(pointer)
            valid = len(point) == 2 and all(math.isfinite(float(v)) for v in (*point, now))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not eligible or not valid:
            self.reset()
            return True
        if (point != self.point or self.last_motion is None or
                now < self.last_motion or buttons_down or self.buttons_down):
            self.point, self.last_motion = point, now
            self.visible = True
        else:
            self.visible = now - self.last_motion < self.hide_seconds
        self.buttons_down = bool(buttons_down)
        return self.visible


class X11CursorVisibility:
    """Paired server hide/show calls, not a replacement transparent cursor."""
    def __init__(self, display_name):
        self.display = None
        self.hidden = False
        self.hide_calls = self.show_calls = 0
        self.x11 = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.fixes = ctypes.CDLL(ctypes.util.find_library('Xfixes') or 'libXfixes.so.3')
        ptr, window = ctypes.c_void_p, ctypes.c_ulong
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ptr
        self.x11.XDefaultRootWindow.argtypes = [ptr]
        self.x11.XDefaultRootWindow.restype = window
        self.x11.XFlush.argtypes = [ptr]
        self.x11.XCloseDisplay.argtypes = [ptr]
        self.fixes.XFixesQueryExtension.argtypes = [ptr, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.fixes.XFixesQueryExtension.restype = ctypes.c_int
        self.fixes.XFixesQueryVersion.argtypes = [ptr, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.fixes.XFixesQueryVersion.restype = ctypes.c_int
        for name in ('XFixesHideCursor', 'XFixesShowCursor'):
            getattr(self.fixes, name).argtypes = [ptr, window]
            getattr(self.fixes, name).restype = None
        self.display = self.x11.XOpenDisplay(display_name.encode() if display_name else None)
        if not self.display:
            raise RuntimeError('No X11 display for cursor idle control')
        try:
            event, error = ctypes.c_int(), ctypes.c_int()
            major, minor = ctypes.c_int(4), ctypes.c_int(0)
            if not self.fixes.XFixesQueryExtension(self.display, ctypes.byref(event), ctypes.byref(error)):
                raise RuntimeError('XFixes extension is unavailable')
            if not self.fixes.XFixesQueryVersion(self.display, ctypes.byref(major), ctypes.byref(minor)) or major.value < 4:
                raise RuntimeError('XFixes 4 is required for cursor idle control')
            self.root = self.x11.XDefaultRootWindow(self.display)
        except BaseException:
            self.close()
            raise

    def set_visible(self, visible):
        if not self.display:
            return
        hide = not bool(visible)
        if hide == self.hidden:
            return
        if hide:
            self.fixes.XFixesHideCursor(self.display, self.root)
            self.hide_calls += 1
        else:
            self.fixes.XFixesShowCursor(self.display, self.root)
            self.show_calls += 1
        self.x11.XFlush(self.display)
        self.hidden = hide

    def close(self):
        if self.display:
            self.set_visible(True)
            self.x11.XCloseDisplay(self.display)
            self.display = None


class MouseCursor:
    """100 ms pointer sampling also covers motion inside foreign XEmbed."""
    def __init__(self, owner, *, hide_seconds=2., clock=time.monotonic,
                 pointer_provider=None, scope_provider=None, backend=None):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, Gdk, GLib
        self.Gtk, self.Gdk, self.GLib = Gtk, Gdk, GLib
        self.owner, self.clock = owner, clock
        self.pointer_provider, self.scope_provider = pointer_provider, scope_provider
        self.policy = CursorPolicy(hide_seconds)
        self.closed = False
        self.timer = None
        self.backend = backend
        self.error = None
        self.polls = 0
        try:
            if self.backend is None:
                self.backend = X11CursorVisibility(owner.get_display().get_name())
            self.timer = GLib.timeout_add(100, self.poll)
            print('鼠标自动隐藏已启用：静止 '+format(hide_seconds, 'g')+' 秒', file=sys.stderr, flush=True)
        except (OSError, RuntimeError, AttributeError) as error:
            self.error = str(error)
            print('鼠标自动隐藏暂不可用：'+self.error, file=sys.stderr, flush=True)

    def _pointer(self):
        if self.pointer_provider:
            return self.pointer_provider()
        pointer = self.owner.get_display().get_default_seat().get_pointer()
        _, x, y = pointer.get_position()
        position = self.Gdk.get_default_root_window().get_device_position(pointer)
        mask = position[-1]
        buttons = (self.Gdk.ModifierType.BUTTON1_MASK | self.Gdk.ModifierType.BUTTON2_MASK |
                   self.Gdk.ModifierType.BUTTON3_MASK | self.Gdk.ModifierType.BUTTON4_MASK |
                   self.Gdk.ModifierType.BUTTON5_MASK)
        return (x, y), bool(mask & buttons)

    def _eligible(self, point):
        if self.scope_provider:
            return bool(self.scope_provider(point))
        # Every GTK top-level here belongs to this host process: main window,
        # transient settings/dialogs, toolbar and combo popups. Foreign games
        # are descendants of the main window and need no cursor modifications.
        windows = []
        active = False
        for widget in self.Gtk.Window.list_toplevels():
            window = widget.get_window()
            if not window or not widget.get_mapped():
                continue
            if window.get_state() & self.Gdk.WindowState.ICONIFIED:
                continue
            active = active or widget.is_active()
            windows.append(window)
        if not active:
            return False
        x, y = point
        for window in windows:
            left, top = window.get_origin()[-2:]
            if left <= x < left+window.get_width() and top <= y < top+window.get_height():
                return True
        return False

    def poll(self):
        if self.closed:
            return False
        if self.backend is None:
            return False
        try:
            point, pressed = self._pointer()
            visible = self.policy.update(self.clock(), point, eligible=self._eligible(point), buttons_down=pressed)
            self.backend.set_visible(visible)
            self.polls += 1
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError):
            # A disappearing popup/window must not leave the desktop hidden.
            self.policy.reset()
            self.backend.set_visible(True)
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.policy.reset()
        if self.timer is not None:
            self.GLib.source_remove(self.timer)
            self.timer = None
        if self.backend:
            self.backend.close()
