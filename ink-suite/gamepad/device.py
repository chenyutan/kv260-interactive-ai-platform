"""Non-grabbing Linux evdev reader, with bounded edges and hot-plug recovery.

Axes use positive right/down, triggers 0..1. Snapshots consume edges, never held
state. On attachment/resync, a held control must first return to neutral/release.
No camera, GTK, SDL, udev edits, or privileged device access are needed.
"""
from collections import deque
import math
import os
from pathlib import Path
import select
import struct
import threading
import time

try:
    import fcntl
except ImportError:  # Pure feed tests and imports also work on Windows.
    fcntl = None

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_DROPPED = 0, 3
# Linux timeval uses pointer-sized signed longs, unlike Windows' LLP64 longs.
EVENT = struct.Struct("=qqHHi" if struct.calcsize("P") == 8 else "=iiHHi")
KEYS = {304: "A", 305: "B", 307: "X", 308: "Y", 310: "LB",
        311: "RB", 312: "LT", 313: "RT", 314: "BACK", 315: "START",
        316: "GUIDE", 317: "L3", 318: "R3", 544: "UP", 545: "DOWN",
        546: "LEFT", 547: "RIGHT"}
AXIS_NAMES = ("lx", "ly", "rx", "ry", "lt", "rt")


def _ior(number, size):
    return (2 << 30) | (int(size) << 16) | (ord("E") << 8) | number


def _ioctl_bytes(fd, number, size):
    data = bytearray(size)
    fcntl.ioctl(fd, _ior(number, size), data, True)
    return data


def _bit(data, index):
    return index // 8 < len(data) and bool(data[index // 8] & (1 << (index % 8)))


def _sysfs_bitmap(text):
    # sysfs prints high-to-low machine words without zero-padding each word.
    width = 64 if struct.calcsize("P") == 8 else 32
    return sum(int(word, 16) << (width * index)
               for index, word in enumerate(reversed(text.split())))


def _clamp(value, low, high):
    return max(low, min(high, value))


class GamepadReader:
    """Start a daemon reader, or pass start=False for deterministic event feeds.

    feed(type, code, value) accepts individual evdev events. attach() accepts
    EVIOCGABS tuples (value, min, max, fuzz, flat, resolution), and is also the
    explicit pure-test entrypoint. Hardware discovery prefers GameSir G7 Pro.
    """
    def __init__(self, start=True, *, deadzone=0.18, edge_limit=64,
                 device_path=None, scan_interval=0.75, clock=time.monotonic):
        self.deadzone = _clamp(float(deadzone), 0.05, 0.45)
        self._clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._edges = deque(maxlen=max(8, int(edge_limit)))
        self._edge_overflows = 0
        self._sequence = 0
        self._timestamp = self._clock()
        self._connected = False
        self._name = ""
        self._path = device_path
        self._scan_interval = max(0.2, float(scan_interval))
        self._fd = None
        self._thread = None
        self._axis_info = {}
        self._raw_axes = {}
        self._raw_keys = set()
        self._buttons = set()
        self._blocked = set()
        self._stick_armed = {"l": False, "r": False}
        self._axes = {name: 0.0 for name in AXIS_NAMES}
        self._axis_map = {0: "lx", 1: "ly", 3: "rx", 4: "ry", 2: "lt", 5: "rt"}
        self._dropping = False
        self._last_error = ""
        if start:
            self._thread = threading.Thread(target=self._run, name="gamepad-evdev", daemon=True)
            self._thread.start()

    def attach(self, name="Test gamepad", axis_info=None, held_keys=()):
        """Seed neutral-safe input state, without synthesizing press edges."""
        with self._lock:
            self._connected, self._name = True, str(name)
            self._axis_info = {int(k): tuple(v) for k, v in (axis_info or {}).items()}
            self._raw_axes = {code: info[0] for code, info in self._axis_info.items()}
            # G7 Pro 3537:1022 on kernel 5.15 is generic HID, not xpad:
            # X/Y, Z/RZ are sticks, BRAKE/GAS are LT/RT (all 0..255).
            generic = 9 in self._axis_info and 10 in self._axis_info and 3 not in self._axis_info
            self._axis_map = ({0: "lx", 1: "ly", 2: "rx", 5: "ry", 10: "lt", 9: "rt"}
                              if generic else {0: "lx", 1: "ly", 3: "rx", 4: "ry", 2: "lt", 5: "rt"})
            self._raw_keys = set(int(x) for x in held_keys)
            self._buttons.clear()
            self._edges.clear()
            self._axes = dict.fromkeys(AXIS_NAMES, 0.0)
            self._blocked = self._physical_buttons()
            self._stick_armed = {"l": False, "r": False}
            self._dropping = False
            self._recompute()
            self._mark()

    def disconnect(self):
        with self._lock:
            old = set(self._buttons)
            self._connected = False
            self._buttons.clear()
            self._raw_keys.clear()
            self._raw_axes.clear()
            self._axes = dict.fromkeys(AXIS_NAMES, 0.0)
            self._blocked.clear()
            self._edges.clear()  # Discard unsent presses after disconnect.
            for button in sorted(old):
                self._edge("released", button)
            self._stick_armed = {"l": False, "r": False}
            self._mark()

    def _mark(self):
        self._sequence += 1
        self._timestamp = self._clock()

    def _edge(self, kind, button):
        if len(self._edges) == self._edges.maxlen:
            # A stalled consumer must not replay a long burst of old actions.
            self._edges.clear()
            self._edge_overflows += 1
        self._edges.append((kind, button))

    def _normal(self, code, trigger=False):
        info = self._axis_info.get(code)
        if info is None or info[2] <= info[1]:
            return 0.0
        value = self._raw_axes.get(code, info[0])
        if trigger:
            return _clamp((value - info[1]) / (info[2] - info[1]), 0.0, 1.0)
        center = 0.0 if info[1] < 0 < info[2] else (info[1] + info[2]) * 0.5
        extent = (info[2] - center) if value >= center else (center - info[1])
        return _clamp((value - center) / extent, -1.0, 1.0)

    def _physical_buttons(self):
        buttons = {KEYS[code] for code in self._raw_keys if code in KEYS}
        x, y = self._raw_axes.get(16, 0), self._raw_axes.get(17, 0)
        if x < 0: buttons.add("LEFT")
        if x > 0: buttons.add("RIGHT")
        if y < 0: buttons.add("UP")
        if y > 0: buttons.add("DOWN")
        for code, name in self._axis_map.items():
            if name in ("lt", "rt"):
                # Hysteresis prevents trigger threshold chatter.
                threshold = 0.38 if name.upper() in self._buttons else 0.55
                if self._normal(code, True) >= threshold:
                    buttons.add(name.upper())
        return buttons

    def _recompute(self):
        values = {name: self._normal(code, name in ("lt", "rt"))
                  for code, name in self._axis_map.items()}
        for side in ("l", "r"):
            x, y = values.get(side + "x", 0.0), values.get(side + "y", 0.0)
            magnitude = math.hypot(x, y)
            if magnitude <= self.deadzone:
                self._stick_armed[side] = True
                x = y = 0.0
            elif self._stick_armed[side]:
                gain = _clamp((magnitude - self.deadzone) / (1.0 - self.deadzone), 0.0, 1.0) / magnitude
                x, y = x * gain, y * gain
            else:
                x = y = 0.0
            self._axes[side + "x"], self._axes[side + "y"] = x, y
        physical = self._physical_buttons()
        self._blocked.intersection_update(physical)
        buttons = physical - self._blocked
        for code, name in self._axis_map.items():
            if name in ("lt", "rt"):
                self._axes[name] = 0.0 if name.upper() in self._blocked else values.get(name, 0.0)
        for button in sorted(buttons - self._buttons): self._edge("pressed", button)
        for button in sorted(self._buttons - buttons): self._edge("released", button)
        self._buttons = buttons

    def feed(self, event_type, code, value):
        """Feed raw input; EV_SYN/SYN_REPORT commits each atomic report."""
        with self._lock:
            if not self._connected:
                return
            if event_type == EV_SYN and code == SYN_DROPPED:
                old = set(self._buttons)
                self._edges.clear()
                self._buttons.clear()
                self._axes = dict.fromkeys(AXIS_NAMES, 0.0)
                self._blocked = set(KEYS.values()) | {"UP", "DOWN", "LEFT", "RIGHT", "LT", "RT"}
                self._stick_armed = {"l": False, "r": False}
                self._dropping = True
                for button in sorted(old): self._edge("released", button)
                self._mark()
                return
            if self._dropping:
                # Linux requires skipping through the next SYN_REPORT, then
                # querying authoritative key/axis state with EVIOCG* ioctls.
                return
            if event_type == EV_KEY and code in KEYS:
                if value: self._raw_keys.add(code)
                else: self._raw_keys.discard(code)
            elif event_type == EV_ABS and (code in self._axis_map or code in (16,17)):
                self._raw_axes[int(code)] = int(value)
            elif event_type == EV_SYN and code == SYN_REPORT:
                self._recompute()
                self._mark()

    def snapshot(self, *, consume_edges=True):
        """Read state, optionally peeking at edges for input arbitration.

        The host's gesture gate peeks before its dispatch timer runs. Default
        callers retain the single-consumer behavior used by game adapters.
        """
        with self._lock:
            result = dict(connected=self._connected, name=self._name,
                          axes=dict(self._axes), buttons=frozenset(self._buttons),
                          pressed=frozenset(b for k, b in self._edges if k == "pressed"),
                          released=frozenset(b for k, b in self._edges if k == "released"),
                          sequence=self._sequence, timestamp=self._timestamp,
                          sampled_at=self._clock(),
                          edge_overflows=self._edge_overflows, error=self._last_error)
            if consume_edges:
                self._edges.clear()
            return result

    @staticmethod
    def discover():
        candidates = []
        for event in Path("/sys/class/input").glob("event*"):
            device = event / "device"
            try:
                name = (device / "name").read_text().strip()
                abs_bits = _sysfs_bitmap((device / "capabilities/abs").read_text())
                key_bits = _sysfs_bitmap((device / "capabilities/key").read_text())
                path = "/dev/input/" + event.name
                if not (abs_bits & 3 == 3 and key_bits >> 304 & 1 and os.access(path, os.R_OK)):
                    continue
                score = 0 if "gamesir" in name.lower() and "g7" in name.lower() else 1
                candidates.append((score, path, name))
            except (OSError, ValueError):
                continue
        return [(path, name) for _, path, name in sorted(candidates)]

    def _read_state(self, fd):
        axes = {}
        supported = _ioctl_bytes(fd, 0x20 + EV_ABS, 8)
        for code in range(64):
            if _bit(supported, code):
                axes[code] = struct.unpack("6i", _ioctl_bytes(fd, 0x40 + code, 24))
        bits = _ioctl_bytes(fd, 0x18, 128)
        return axes, [code for code in KEYS if _bit(bits, code)]

    def _resync(self, fd):
        axes, keys = self._read_state(fd)
        # Keep disconnect/release edges but never manufacture a press from the
        # queried held state. The next actual release re-arms that button.
        with self._lock:
            releases = [(kind, b) for kind, b in self._edges if kind == "released"]
            self.attach(self._name, axes, keys)
            self._edges.extend(releases)

    def _run(self):
        if fcntl is None:
            self._last_error = "Linux evdev unavailable"
            return
        while not self._stop.is_set():
            fd = None
            try:
                devices = [(self._path, "Gamepad")] if self._path else self.discover()
                if not devices:
                    self._stop.wait(self._scan_interval)
                    continue
                path, name = devices[0]
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                self._fd = fd
                try:
                    name = _ioctl_bytes(fd, 0x06, 256).split(b"\0", 1)[0].decode("utf-8", "replace")
                except OSError:
                    pass
                axes, keys = self._read_state(fd)
                self.attach(name, axes, keys)
                self._last_error = ""
                pending = b""
                while not self._stop.is_set():
                    ready, _, exceptional = select.select([fd], [], [fd], 0.1)
                    if exceptional: raise OSError("gamepad exception")
                    if not ready: continue
                    chunk = os.read(fd, EVENT.size * 128)
                    if not chunk: raise OSError("gamepad disconnected")
                    pending += chunk
                    whole = len(pending) // EVENT.size * EVENT.size
                    for offset in range(0, whole, EVENT.size):
                        _, _, event_type, code, value = EVENT.unpack_from(pending, offset)
                        was_dropping = self._dropping
                        self.feed(event_type, code, value)
                        if was_dropping and event_type == EV_SYN and code == SYN_REPORT:
                            self._resync(fd)
                    pending = pending[whole:]
            except (OSError, ValueError) as exc:
                self._last_error = str(exc)
            finally:
                if fd is not None:
                    try: os.close(fd)
                    except OSError: pass
                self._fd = None
                if self._connected: self.disconnect()
            self._stop.wait(self._scan_interval)

    def close(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self.disconnect()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
