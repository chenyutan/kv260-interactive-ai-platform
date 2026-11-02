"""Global gamepad mouse via a private X11/XTest connection, without GLib.

Right stick and R3 are always available. enabled only enables LB/RB scrolling
when a host dialog is active. BACK+R3 right-click consumes BACK until release;
the host must run update before handling BACK *release*, and consult the return
value's back_release_consumed. No subprocess, per-frame library load, or GRAB.
"""
import ctypes
import ctypes.util
import math
import os


class _X11Backend:
    def __init__(self, display=None):
        self.x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.xtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestQueryExtension.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_int)] * 4
        self.xtst.XTestQueryExtension.restype = ctypes.c_int
        self.xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        self.xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.display = self.x11.XOpenDisplay((display or os.environ.get("DISPLAY") or ":0").encode())
        if not self.display:
            raise OSError("Cannot open X11 display")
        event, error, major, minor = (ctypes.c_int() for _ in range(4))
        if not self.xtst.XTestQueryExtension(self.display, ctypes.byref(event), ctypes.byref(error),
                                            ctypes.byref(major), ctypes.byref(minor)):
            self.close()
            raise OSError("XTest extension unavailable")

    def move(self, dx, dy):
        self.xtst.XTestFakeRelativeMotionEvent(self.display, dx, dy, 0)

    def button(self, number, down):
        self.xtst.XTestFakeButtonEvent(self.display, number, int(down), 0)

    def flush(self):
        self.x11.XFlush(self.display)

    def close(self):
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


class DesktopPointer:
    """update(snapshot, dt, enabled=False) returns operation/consumption flags.

    backend is injectable for tests. Failure leaves game input usable, records
    error, and does not silently substitute any global keyboard shortcuts.
    """
    def __init__(self, *, backend=None, display=None, speed=1000.0):
        self._backend = None
        self.error = ""
        self.speed = max(100.0, min(2400.0, float(speed)))
        self._fraction = [0.0, 0.0]
        self._held_mouse = set()
        self._closed = False
        self._back_combo = False
        self._r3_previous = False
        self._scroll_wait = 0.0
        self._scroll_direction = 0
        try:
            self._backend = backend if backend is not None else _X11Backend(display)
        except (OSError, AttributeError) as exc:
            self.error = str(exc)

    @property
    def available(self):
        return self._backend is not None and not self._closed

    def _mouse_button(self, number, down):
        if down == (number in self._held_mouse):
            return False
        self._backend.button(number, down)
        if down: self._held_mouse.add(number)
        else: self._held_mouse.discard(number)
        return True

    def _release(self):
        if not self._backend:
            self._held_mouse.clear()
            return
        for number in tuple(self._held_mouse):
            self._mouse_button(number, False)
        self._backend.flush()

    def update(self, snapshot, dt, enabled=False):
        buttons = set(snapshot.get("buttons", ()))
        pressed = set(snapshot.get("pressed", ()))
        released = set(snapshot.get("released", ()))
        connected = bool(snapshot.get("connected", False))
        was_combo = self._back_combo
        if ("BACK" in buttons or "BACK" in pressed) and ("R3" in buttons or "R3" in pressed):
            self._back_combo = True
        release_consumed = ("BACK" in released and (was_combo or self._back_combo))
        result = dict(available=self.available, moved=False, clicked=False,
                      scrolled=False, back_consumed=self._back_combo,
                      back_release_consumed=release_consumed)
        if "BACK" not in buttons:
            self._back_combo = False
        if not self.available:
            return result
        try:
            if not connected:
                self._release()
                self._fraction[:] = [0.0, 0.0]
                self._back_combo = self._r3_previous = False
                self._scroll_direction = 0
                self._scroll_wait = 0.0
                return result
            elapsed = float(dt)
            if not math.isfinite(elapsed): elapsed = 0.0
            elapsed = max(0.0, min(0.05, elapsed))
            axes = snapshot.get("axes", {})
            motion = []
            for index, name in enumerate(("rx", "ry")):
                value = float(axes.get(name, 0.0))
                if not math.isfinite(value): value = 0.0
                value = max(-1.0, min(1.0, value))
                if abs(value) < 0.025: value = 0.0
                # Fine small movements; full stick still crosses 960px in 1s.
                delta = math.copysign(abs(value) ** 1.55, value) * self.speed * elapsed
                self._fraction[index] += delta
                integer = int(self._fraction[index])
                self._fraction[index] -= integer
                motion.append(integer)
            if motion[0] or motion[1]:
                self._backend.move(*motion)
                result["moved"] = True
            r3 = "R3" in buttons
            # Clicking R3 then adding BACK must release left before right.
            desired = 3 if r3 and self._back_combo else (1 if r3 else None)
            for number in tuple(self._held_mouse):
                if number != desired:
                    self._mouse_button(number, False)
            if desired is not None:
                result["clicked"] = self._mouse_button(desired, True)
            # A fast press+release inside one host tick must still click once.
            elif "R3" in pressed and "R3" in released and not self._r3_previous:
                number = 3 if was_combo or result["back_consumed"] else 1
                self._backend.button(number, True)
                self._backend.button(number, False)
                result["clicked"] = True
            self._r3_previous = r3
            direction = (int("RB" in buttons) - int("LB" in buttons)) if enabled else 0
            self._scroll_wait -= elapsed
            if direction != self._scroll_direction:
                self._scroll_wait = 0.0
                self._scroll_direction = direction
            if direction and self._scroll_wait <= 0.0:
                wheel = 5 if direction > 0 else 4
                self._backend.button(wheel, True)
                self._backend.button(wheel, False)
                self._scroll_wait = 0.14
                result["scrolled"] = True
            self._backend.flush()
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.error = str(exc)
            try: self._release()
            except Exception: pass
        return result

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._release()
        finally:
            if self._backend:
                self._backend.close()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
