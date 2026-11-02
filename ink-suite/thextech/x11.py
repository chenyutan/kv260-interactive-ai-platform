"""Reparent only this child's exact SDL2 window; never change global focus."""
import ctypes as C
import ctypes.util


class Window(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('width', C.c_int), ('height', C.c_int),
                ('border_width', C.c_int), ('depth', C.c_int), ('visual', C.c_void_p),
                ('root', C.c_ulong), ('c_class', C.c_int), ('bit_gravity', C.c_int),
                ('win_gravity', C.c_int), ('backing_store', C.c_int), ('backing_planes', C.c_ulong),
                ('backing_pixel', C.c_ulong), ('save_under', C.c_int), ('colormap', C.c_ulong),
                ('map_installed', C.c_int), ('map_state', C.c_int), ('all_event_masks', C.c_long),
                ('your_event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
                ('override_redirect', C.c_int), ('screen', C.c_void_p)]


class EmbedWindow:
    def __init__(self):
        self.lib = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        self.lib.XOpenDisplay.argtypes = [C.c_char_p]
        self.lib.XOpenDisplay.restype = C.c_void_p
        self.display = self.lib.XOpenDisplay(None)
        if not self.display: raise RuntimeError('X11 display unavailable')
        self.xid = self.parent = None
        # Xlib's handler is process-global. Only errors on our own Display
        # enter this transaction; GTK's independent Display keeps its handler.
        self.errors = []
        self.handler_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
        self.old_callback = None
        def handle(display, event):
            if display == self.display:
                self.errors.append('X11 error'); return 0
            return self.old_callback(display, event) if self.old_callback else 0
        self.handler = self.handler_type(handle)
        self.lib.XSetErrorHandler.argtypes = [C.c_void_p]
        self.lib.XSetErrorHandler.restype = C.c_void_p
        self.old_handler = self.lib.XSetErrorHandler(C.cast(self.handler, C.c_void_p))
        if self.old_handler: self.old_callback = self.handler_type(self.old_handler)
        for name, args in {
            'XCloseDisplay': [C.c_void_p], 'XSync': [C.c_void_p, C.c_int],
            'XGetWindowAttributes': [C.c_void_p, C.c_ulong, C.POINTER(Window)],
            'XReparentWindow': [C.c_void_p, C.c_ulong, C.c_ulong, C.c_int, C.c_int],
            'XMapWindow': [C.c_void_p, C.c_ulong], 'XUnmapWindow': [C.c_void_p, C.c_ulong],
            'XMoveResizeWindow': [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint],
            'XSetWindowBorderWidth': [C.c_void_p, C.c_ulong, C.c_uint],
            'XInternAtom': [C.c_void_p, C.c_char_p, C.c_int],
            'XGetWindowProperty': [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long,
                C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)],
            'XFree': [C.c_void_p],
        }.items(): getattr(self.lib, name).argtypes = args
        self.lib.XInternAtom.restype = C.c_ulong

    def _sync(self):
        self.lib.XSync(self.display, 0)
        if self.errors:
            self.errors.clear()
            raise RuntimeError('Native game window is unavailable')

    def _pid(self, xid):
        atom = self.lib.XInternAtom(self.display, b'_NET_WM_PID', 1)
        if not atom: return None
        actual, fmt, count, remaining, data = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong(), C.c_void_p()
        result = self.lib.XGetWindowProperty(self.display, xid, atom, 0, 1, 0, 6,
            C.byref(actual), C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data))
        try:
            if result == 0 and actual.value == 6 and fmt.value == 32 and count.value == 1 and data:
                return C.cast(data, C.POINTER(C.c_ulong))[0]
        finally:
            if data: self.lib.XFree(data)
        return None

    def attach(self, xid, parent, pid, size):
        if self.xid:
            if xid != self.xid or parent != self.parent:
                raise RuntimeError('Unexpected native window replacement')
            self.resize(size)
            return
        if not all(isinstance(i, int) and i > 0 for i in (xid, parent, pid)) or xid == parent:
            raise ValueError('Invalid game window identity')
        attrs, parent_attrs = Window(), Window()
        if not self.lib.XGetWindowAttributes(self.display, xid, C.byref(attrs)) or not self.lib.XGetWindowAttributes(self.display, parent, C.byref(parent_attrs)):
            self._sync()
            raise RuntimeError('Game window not created')
        if self._pid(xid) != pid or attrs.root != parent_attrs.root:
            raise RuntimeError('Native game window identity mismatch')
        self.lib.XUnmapWindow(self.display, xid)
        self.lib.XReparentWindow(self.display, xid, parent, 0, 0)
        self.lib.XSetWindowBorderWidth(self.display, xid, 0)
        self.xid, self.parent = xid, parent
        self.resize(size)
        self.lib.XMapWindow(self.display, xid)
        self._sync()

    def resize(self, size):
        if self.xid:
            self.lib.XMoveResizeWindow(self.display, self.xid, 0, 0, *size)
            self._sync()

    def close(self):
        if self.display:
            self.lib.XSync(self.display, 0)
            self.lib.XSetErrorHandler(self.old_handler)
            self.lib.XCloseDisplay(self.display)
            self.display = None
