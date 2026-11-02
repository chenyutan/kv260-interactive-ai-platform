"""A host-owned mouse toolbar which never resizes an XEmbed experience.

The policy is independent of GTK so its deadlines can be tested without a
display. Pointer polling is deliberate: embedded X11 child windows do not
deliver their motion events to the host's GTK widget hierarchy.
"""
import math
import time


def inside(point, rect):
    if point is None or rect is None:
        return False
    try:
        x, y = point
        left, top, width, height = rect
        if not all(math.isfinite(float(v)) for v in (*point, *rect)):
            return False
        return width > 0 and height > 0 and left <= x < left + width and top <= y < top + height
    except (TypeError, ValueError):
        return False


class ToolbarPolicy:
    def __init__(self, hot_height=3, hide_seconds=3.):
        self.hot_height = hot_height
        self.hide_seconds = hide_seconds
        self.visible = False
        self.hide_at = None

    def reset(self):
        self.visible = False
        self.hide_at = None

    def update(self, now, pointer, owner_rect, toolbar_rect, *, available=True, buttons_down=False):
        if not available or not inside(pointer, owner_rect):
            self.reset()
            return False
        hot_rect = (owner_rect[0], owner_rect[1], owner_rect[2], self.hot_height)
        if inside(pointer, hot_rect) or (self.visible and inside(pointer, toolbar_rect)):
            self.visible = True
            self.hide_at = None
        elif self.visible:
            if buttons_down:
                self.hide_at = None
            elif self.hide_at is None:
                self.hide_at = now + self.hide_seconds
            elif now >= self.hide_at:
                self.reset()
        return self.visible


def hide_embedded_toolbar(toolbar, embedded):
    """Retain standalone controls; Plug.show_all must not revive hidden ones."""
    if embedded:
        toolbar.set_no_show_all(True)
        toolbar.hide()


class MouseToolbar:
    """One no-focus native popup above the Socket, not another game window."""
    def __init__(self, owner, toolbar, blocked=None, *, clock=time.monotonic,
                 pointer_provider=None, active_provider=None):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, Gdk, GLib
        self.Gdk, self.GLib = Gdk, GLib
        self.owner, self.toolbar = owner, toolbar
        self.blocked = blocked or (lambda: False)
        self.clock = clock
        self.pointer_provider = pointer_provider
        self.active_provider = active_provider
        self.policy = ToolbarPolicy()
        self.closed = False
        self.geometry = None
        self.popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.popup.set_transient_for(owner)
        self.popup.set_screen(owner.get_screen())
        self.popup.set_decorated(False)
        self.popup.set_resizable(False)
        self.popup.set_accept_focus(False)
        self.popup.set_focus_on_map(False)
        self.popup.set_skip_taskbar_hint(True)
        self.popup.set_skip_pager_hint(True)
        self.popup.set_type_hint(Gdk.WindowTypeHint.TOOLBAR)
        self.popup.add(toolbar)
        # These are mouse controls. Keyboard and body navigation continue to
        # use the existing host/action menu, rather than focus an X11 popup.
        for child in toolbar.get_children():
            child.set_can_focus(False)
        toolbar.set_size_request(-1, 46)
        toolbar.show_all()
        self.timer = GLib.timeout_add(100, self.poll)

    def _owner_rect(self):
        window = self.owner.get_window()
        if window is None:
            return None
        origin = window.get_origin()
        left, top = origin[-2:]
        return left, top, window.get_width(), window.get_height()

    def _pointer(self):
        if self.pointer_provider:
            return self.pointer_provider()
        pointer = self.owner.get_display().get_default_seat().get_pointer()
        _, x, y = pointer.get_position()
        position = self.owner.get_window().get_device_position(pointer)
        # PyGObject versions expose either (x,y,mask) or
        # (inside_window,x,y,mask). The final item is the modifier state.
        if len(position) not in (3,4):raise ValueError('Unexpected GDK pointer position result')
        mask = position[-1]
        buttons = (self.Gdk.ModifierType.BUTTON1_MASK | self.Gdk.ModifierType.BUTTON2_MASK |
                   self.Gdk.ModifierType.BUTTON3_MASK | self.Gdk.ModifierType.BUTTON4_MASK |
                   self.Gdk.ModifierType.BUTTON5_MASK)
        return (x, y), bool(mask & buttons)

    def suppress(self):
        self.policy.reset()
        if not self.closed:
            self.popup.hide()

    def poll(self):
        if self.closed:
            return False
        window = self.owner.get_window()
        active = self.active_provider() if self.active_provider else self.owner.is_active()
        available = bool(window and self.owner.get_mapped() and active and not self.blocked())
        if available:
            available = not bool(window.get_state() & self.Gdk.WindowState.ICONIFIED)
        if not available:
            self.suppress()
            return True
        try:
            rect = self._owner_rect()
            point, buttons = self._pointer()
        except (AttributeError, TypeError, ValueError, RuntimeError):
            self.suppress()
            return True
        left, top, width, _ = rect
        height = max(46, self.toolbar.get_preferred_height()[1])
        geometry = (left, top, width, height)
        visible = self.policy.update(self.clock(), point, rect, geometry, buttons_down=buttons)
        if visible:
            if geometry != self.geometry:
                self.popup.set_size_request(width, height)
                self.popup.move(left, top)
                self.popup.resize(width, height)
                self.geometry = geometry
            if not self.popup.get_visible():
                self.popup.show_all()
                self.popup.get_window().raise_()
        elif self.popup.get_visible():
            self.popup.hide()
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.policy.reset()
        if self.timer is not None:
            self.GLib.source_remove(self.timer)
            self.timer = None
        self.popup.destroy()
