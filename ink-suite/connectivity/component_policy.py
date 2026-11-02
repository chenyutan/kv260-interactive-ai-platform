"""Pure, per-launch missing-device alert policy; no hardware access or GUI.

Observations are authoritative facts, not user preferences. Dismissing a popup
never makes a device ready. Network and audio availability do not gate camera
apps; camera readiness here still requires the separate inference readiness
gate before gestures can be accepted. All times use one monotonic clock.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MissingComponent:
    key: str
    name: str
    detail: str
    episode: int

    @property
    def message(self):
        return self.name + '未连接'


@dataclass
class _State:
    connected: bool = False
    observed: bool = False
    detail: str = ''
    missing_since: float = 0.0
    episode: int = 0
    alerted: bool = False
    dismissed: bool = False
    generation: int = 0
    pending: object = None


class ComponentPolicy:
    """One alert per loss episode, with startup grace and hot-unplug debounce.

    ``update`` accepts partial snapshots; absent keys retain their prior state.
    Values are booleans or mappings with ``connected``, optional ``detail`` and
    optional ``name``. Only literal True establishes a connection. An unknown
    component stays unavailable but cannot trigger an alert before observation.

    ``retry`` arms exactly one new attempt. Controllers with asynchronous work
    can call ``apply_result`` with its token; any intervening observation,
    dismissal, retry, or close invalidates that token. Run ``update({}, now)``
    afterwards to collect an accepted retry failure's alert.
    """
    DEFAULT_NAMES = {'camera': '摄像头', 'wifi': 'WiFi网络', 'audio': 'Cube音箱'}

    def __init__(self, started_at=None, initial_delay=8.0, loss_debounce=2.0):
        self.initial_delay = self._time(initial_delay)
        self.loss_debounce = self._time(loss_debounce)
        if self.initial_delay < 0 or self.loss_debounce < 0:
            raise ValueError('Delays must be nonnegative')
        self.started_at = None if started_at is None else self._time(started_at)
        self._last_now = self.started_at
        self._states = {key: _State() for key in self.DEFAULT_NAMES}
        self._names = dict(self.DEFAULT_NAMES)
        self._closed = False

    @staticmethod
    def _time(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Expected finite monotonic seconds')
        if not math.isfinite(value):
            raise ValueError('Expected finite monotonic seconds')
        return float(value)

    def _observe(self, key, value, now):
        state = self._states[key]
        if isinstance(value, Mapping):
            connected = value.get('connected') is True
            state.detail = str(value.get('detail') or '')
            name = value.get('name')
            if isinstance(name, str) and name.strip():
                self._names[key] = name.strip()
        else:
            connected = value is True
            state.detail = ''
        state.generation += 1
        state.pending = None
        if connected:
            state.alerted = state.dismissed = False
        elif not state.observed or state.connected:
            state.episode += 1
            state.missing_since = now
            state.alerted = state.dismissed = False
        state.connected = connected
        state.observed = True

    def update(self, snapshot, now):
        now = self._time(now)
        if self._closed:
            return []
        if not isinstance(snapshot, Mapping):
            raise TypeError('snapshot must be a mapping')
        if self._last_now is not None and now < self._last_now:
            # Reject delayed batches; do not let old losses replace recovery.
            return []
        if self.started_at is None:
            self.started_at = now
        self._last_now = now
        for key, value in snapshot.items():
            if key in self._states:
                self._observe(key, value, now)
        alerts = []
        for key, state in self._states.items():
            if self.should_alert(key, now):
                state.alerted = True
                alerts.append(MissingComponent(key, self._names[key],
                                               state.detail, state.episode))
        return alerts

    def ready(self, key):
        state = self._states[key]
        return not self._closed and state.observed and state.connected

    def should_alert(self, key, now):
        now = self._time(now)
        state = self._states[key]
        return (not self._closed and state.observed and not state.connected
                and not state.alerted and not state.dismissed
                and state.pending is None and self.started_at is not None
                and now >= self.started_at + self.initial_delay
                and now >= state.missing_since + self.loss_debounce)

    def dismiss(self, key):
        """Keep disconnected for this episode; recovery enables future alerts."""
        state = self._states[key]
        state.dismissed = True
        state.generation += 1
        state.pending = None

    def retry(self, key):
        """Return a generation token; suppress alerts while this retry runs."""
        state = self._states[key]
        state.generation += 1
        state.pending = state.generation
        state.alerted = state.dismissed = False
        return state.generation

    def apply_result(self, key, connected, token, now, detail=''):
        now = self._time(now)
        state = self._states[key]
        if (self._closed or state.pending is None or token != state.pending
                or (self._last_now is not None and now < self._last_now)):
            return False
        self._last_now = now
        self._observe(key, {'connected': connected, 'detail': detail}, now)
        return True

    def close(self):
        """Invalidate outstanding work when the owning UI is destroyed."""
        self._closed = True
        for state in self._states.values():
            state.generation += 1
            state.pending = None
