"""Validated controller ink events, without inventing camera poses.

The existing painter already consumes compact rendering records for replay and
progressive animation. Controller input creates that same record format from an
explicit canonical geometry recipe; origin and speed provenance remain visible.
No model, camera, NumPy dependency, network, or unbounded pending list lives here.
"""
import copy
import math
import queue
import threading
import time

ACTIONS = ("A01", "A02", "A03", "A04", "A05", "A06")
THEMES = ("landscape", "bamboo", "plum", "mixed")
MAPPING = {
    "landscape": ("远山", "主峰", "双峰", "斜坡", "水岸", "云雾"),
    "bamboo": ("竹丛", "主竹干", "双竹", "弯竹", "叶簇", "衬叶"),
    "plum": ("展开枝", "向上主枝", "分枝", "弯枝", "短枝", "梅花"),
}
GEOMETRY = {
    "A01": dict(amplitude=1.65, direction=.22, lean=0.),
    "A02": dict(amplitude=1.40, direction=.08, lean=0.),
    "A03": dict(amplitude=1.55, direction=.18, lean=0.),
    "A04": dict(amplitude=1.35, direction=.65, lean=.14),
    "A05": dict(amplitude=1.10, direction=.35, lean=0.),
    "A06": dict(amplitude=1.20, direction=.12, lean=0.),
}


def validate_request(command):
    if not isinstance(command, dict) or command.get("command") != "gamepad_stroke":
        raise ValueError("Expected gamepad_stroke command")
    if set(command) - {"command", "action", "speed"}:
        raise ValueError("Unexpected controller stroke fields")
    action, speed = command.get("action"), command.get("speed", .5)
    if action not in ACTIONS:
        raise ValueError("Expected action A01–A06")
    if type(speed) not in (int, float) or not math.isfinite(speed) or not 0 <= speed <= 1:
        raise ValueError("Controller speed must be finite and between 0 and 1")
    return action, float(speed)


def controller_allowed(suite, input_mode):
    return (input_mode == "gamepad" and suite.get("active") is True
            and suite.get("mode") == "ink" and suite.get("menu") is False)


class GamepadStrokeGate:
    def __init__(self, *, cooldown=.4, clock=time.monotonic):
        self.cooldown = max(.2, float(cooldown))
        self.clock = clock
        self.last_accepted = -float("inf")
        self.lock = threading.Lock()

    def enqueue(self, command, *, suite, input_mode, theme, generation, target):
        action, speed = validate_request(command)
        if theme not in THEMES: raise ValueError("Unknown theme")
        if not controller_allowed(suite, input_mode):
            return dict(accepted=False, reason="controller_ink_not_active")
        with self.lock:
            now = self.clock()
            if now - self.last_accepted < self.cooldown:
                return dict(accepted=False, reason="stroke_rate_limited",
                            retry_after_ms=round(max(0., self.cooldown - (now - self.last_accepted)) * 1000))
            event = dict(action=action, controller_speed_unit=speed, origin="gamepad",
                         input_mode="gamepad", geometry_source="canonical_action_template_v1",
                         speed_source="controller_trigger", timestamp_seconds=now,
                         render_queued_monotonic=now)
            try:
                target.put_nowait((event, theme, generation, suite["epoch"]))
            except queue.Full:
                return dict(accepted=False, reason="paint_queue_full")
            self.last_accepted = now
            return dict(accepted=True, queued=True, origin="gamepad", action=action,
                        speed=speed, queue_size=target.qsize())


def apply_controller_stroke(renderer, event, theme="mixed"):
    """Reuse InkRenderer._paint and its locked event/parent history verbatim.

    No synthetic pose enters InkRenderer.add or action logs. The only new
    provenance is explicitly controller-based. Existing image/scene/undo/save
    and animation consumers receive their original render-record schema.
    """
    if event.get("origin") != "gamepad":
        raise ValueError("Controller event origin missing")
    action, speed = validate_request(dict(command="gamepad_stroke", action=event.get("action"),
                                         speed=event.get("controller_speed_unit")))
    if theme not in THEMES: raise ValueError("Unknown theme")
    from ink_motifs import ART_VERSION
    from ink_composition import COMPOSITION_VERSION
    profile = [.2 + 2.3 * speed] * 96
    widths = [1.4 + 5.6 * (1. - speed)] * 96
    opacity = [.18 + .65 * (1. - speed)] * 96
    with renderer._lock:
        resolved = renderer._resolve_theme(action, theme)
        if action in ("A05", "A06") and resolved in ("bamboo", "plum") and resolved not in renderer._parents:
            return dict(accepted=False, reason="请先画竹干或梅枝，再添叶添花",
                        needs_parent=resolved, event_count=len(renderer._records))
        record = dict(accepted=True, id=len(renderer._records) + 1, theme=resolved,
                      requested_theme=theme, action=action, art_version=ART_VERSION,
                      composition_version=COMPOSITION_VERSION,
                      element=MAPPING[resolved][ACTIONS.index(action)], **GEOMETRY[action],
                      duration=2.8 - .8 * speed, speed_limits=[.2, 2.5],
                      speed_profile=profile, width_profile=widths, opacity_profile=opacity,
                      speed_mean=profile[0], width_mean=widths[0], opacity_mean=opacity[0],
                      seed=renderer.seed + (len(renderer._records) + 1) * 7919,
                      origin="gamepad", input_mode="gamepad", controller_speed_unit=speed,
                      geometry_source="canonical_action_template_v1", speed_source="controller_trigger")
        renderer._paint(record)
        renderer._records.append(record)
        return dict(copy.deepcopy(record), event_count=len(renderer._records))
