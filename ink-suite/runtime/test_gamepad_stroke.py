"""Pure gate tests plus the actual existing renderer/HTTP contract, no camera."""
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gamepad_stroke import GamepadStrokeGate, apply_controller_stroke, validate_request


class GateTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.gate = GamepadStrokeGate(clock=lambda: self.now)
        self.target = queue.Queue(maxsize=4)
        self.suite = dict(active=True, mode="ink", menu=False, epoch=6)

    def enqueue(self, command=None, **kwargs):
        return self.gate.enqueue(command or dict(command="gamepad_stroke", action="A01", speed=.5),
                                 suite=kwargs.get("suite", self.suite), input_mode=kwargs.get("input_mode", "gamepad"),
                                 theme="mixed", generation=2, target=self.target)

    def test_six_actions_and_speed_boundaries(self):
        for index in range(1, 7):
            for speed in (0, 1, .4):
                self.assertEqual(validate_request(dict(command="gamepad_stroke", action="A%02d" % index, speed=speed)),
                                 ("A%02d" % index, float(speed)))

    def test_invalid_action_speed_unknown_fields(self):
        for change in ({"action": "A00"}, {"action": []}, {"speed": "1"}, {"speed": True},
                       {"speed": float("nan")}, {"speed": float("inf")}, {"speed": -1}, {"speed": 1.01},
                       {"pose": [[1, 2]]}, {"command": "unknown"}):
            cmd = dict(command="gamepad_stroke", action="A01", speed=.5)
            cmd.update(change)
            with self.assertRaises(ValueError): validate_request(cmd)

    def test_not_active_wrong_mode_menu_body_rejected(self):
        for change in ({"active": False}, {"mode": "fruit"}, {"menu": True}):
            suite = dict(self.suite, **change)
            self.assertFalse(self.enqueue(suite=suite)["accepted"])
        self.assertFalse(self.enqueue(input_mode="body")["accepted"])
        self.assertTrue(self.target.empty())

    def test_queued_epoch_generation_origin_no_pose(self):
        self.assertTrue(self.enqueue()["accepted"])
        event, theme, generation, epoch = self.target.get_nowait()
        self.assertEqual((theme, generation, epoch), ("mixed", 2, 6))
        self.assertEqual(event["origin"], "gamepad")
        self.assertEqual(event["geometry_source"], "canonical_action_template_v1")
        self.assertNotIn("pose", event)
        self.assertNotIn("points17", event)

    def test_cooldown_and_no_burst(self):
        self.assertTrue(self.enqueue()["accepted"])
        for _ in range(100): self.assertEqual(self.enqueue()["reason"], "stroke_rate_limited")
        self.assertEqual(self.target.qsize(), 1)
        self.now += .5
        self.assertTrue(self.enqueue()["accepted"])

    def test_full_queue_never_drops_old_art_or_spends_cooldown(self):
        for index in range(4): self.target.put_nowait(index)
        self.assertEqual(self.enqueue()["reason"], "paint_queue_full")
        self.assertEqual(list(self.target.queue), [0, 1, 2, 3])
        self.target.get_nowait()
        self.assertTrue(self.enqueue()["accepted"])


def renderer_source():
    configured = os.environ.get("GAMEPAD_RENDERER_SOURCE_DIR")
    return Path(configured) if configured else HERE.parents[2] / "audit/external-sources"


class RendererContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = renderer_source()
        sys.path.insert(0, str(source))
        try:
            from ink_renderer_fast import InkRenderer
            cls.Renderer = InkRenderer
        except ImportError as exc:
            raise unittest.SkipTest("Existing renderer dependencies unavailable: " + str(exc))

    def event(self, action="A01", speed=.5):
        return dict(origin="gamepad", action=action, controller_speed_unit=speed)

    def test_all_existing_landscape_templates_render(self):
        renderer = self.Renderer(320, 240)
        for action in ("A01", "A02", "A03", "A04", "A05", "A06"):
            record = apply_controller_stroke(renderer, self.event(action), "landscape")
            self.assertTrue(record["accepted"])
            self.assertEqual(record["origin"], "gamepad")
        self.assertEqual(len(renderer.events), 6)
        stream = io.BytesIO(); renderer.image().save(stream, format="PNG")
        self.assertGreater(len(stream.getvalue()), 1000)

    def test_parent_rules_and_mixed_theme_unchanged(self):
        renderer = self.Renderer(320, 240)
        self.assertFalse(apply_controller_stroke(renderer, self.event("A06"), "bamboo")["accepted"])
        self.assertEqual(len(renderer.events), 0)
        self.assertEqual(apply_controller_stroke(renderer, self.event("A02"), "mixed")["theme"], "bamboo")
        self.assertEqual(apply_controller_stroke(renderer, self.event("A06"), "mixed")["theme"], "bamboo")
        self.assertEqual(apply_controller_stroke(renderer, self.event("A03"), "mixed")["theme"], "plum")
        self.assertEqual(apply_controller_stroke(renderer, self.event("A06"), "mixed")["theme"], "plum")

    def test_speed_changes_real_brush_profile_no_pose_evidence(self):
        renderer = self.Renderer(320, 240)
        slow = apply_controller_stroke(renderer, self.event(speed=0), "landscape")
        fast = apply_controller_stroke(renderer, self.event(speed=1), "landscape")
        self.assertGreater(slow["width_mean"], fast["width_mean"])
        self.assertGreater(slow["opacity_mean"], fast["opacity_mean"])
        self.assertEqual(len(fast["speed_profile"]), 96)
        self.assertEqual(slow["speed_source"], "controller_trigger")
        self.assertNotIn("pose", renderer.events[0])

    def test_replay_and_undo_preserve_controller_origin(self):
        renderer = self.Renderer(320, 240)
        apply_controller_stroke(renderer, self.event("A02"), "mixed")
        apply_controller_stroke(renderer, self.event("A06"), "mixed")
        restored = self.Renderer(320, 240)
        for event in renderer.events:
            restored._paint(event); restored._records.append(event)
        self.assertEqual(renderer.image().tobytes(), restored.image().tobytes())
        restored.undo()
        self.assertEqual(len(restored.events), 1)
        self.assertEqual(restored.events[0]["origin"], "gamepad")


class AppContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the actual changed LiveApp class while replacing hardware-only
        # constructors. No model, camera, board, or application __init__ runs.
        imports = {
            "movenet": ("MoveNet", "YuyvPreprocessor", "RoiSelector", "body_rois"),
            "hourglass": ("Hourglass",), "hourglass_select": ("rois",),
            "pose_pipeline": ("description", "identity", "infer_hourglass"),
            "continuous_pose_pipeline": ("infer_continuous",), "roi_continuity": ("ContinuousRoi",),
            "body_crop": ("BodyCrop",), "snapshot": ("capture_snapshot",),
            "action_engine": ("ACTIONS", "quality_thresholds"), "flow_action_engine": ("ActionEngine",),
            "resume_art_once": ("restore",), "scene_store": ("SceneStore",),
            "camera_settings": ("CameraSettings",), "camera_link": ("read_camera_link",),
            "pose_overlay": ("draw_pose",), "environment_capture": ("EnvironmentCapture",),
        }
        stubs = {}
        for name, attrs in imports.items():
            module = types.ModuleType(name)
            for attr in attrs: setattr(module, attr, lambda *a, **k: None)
            stubs[name] = module
        source = renderer_source()
        sys.path.insert(0, str(source))
        sys.path.insert(0, str(HERE))
        try:
            # Do not load binary extensions while patch.dict holds a temporary
            # sys.modules snapshot; restoring it would unload NumPy's registry.
            __import__('numpy');__import__('PIL.Image');__import__('ink_renderer_fast')
            spec = importlib.util.spec_from_file_location("gamepad_changed_runtime_app", HERE / "app.py")
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, stubs): spec.loader.exec_module(module)
            cls.App = module.LiveApp
            cls.Suite = module.SuiteControl
        except ImportError as exc:
            raise unittest.SkipTest("Existing runtime dependencies unavailable: " + str(exc))

    def setUp(self):
        self.app = self.App.__new__(self.App)
        self.app.suite = self.Suite()
        self.app.lock = threading.RLock(); self.app.paint_lock = threading.RLock()
        self.app.input_mode = "body"; self.app.gamepad_strokes = GamepadStrokeGate()
        self.app.theme = "mixed"; self.app.generation = 5
        self.app.paint_queue = queue.Queue(maxsize=4)
        self.app.state = {}; self.app.reset_requested = False

    def enter(self, mode="gamepad", **kwargs):
        return self.app.command(dict(command="suite", active=True, mode="ink", menu=False, input_mode=mode, **kwargs))

    def test_unknown_command_cannot_return_success(self):
        self.assertFalse(self.app.command({"command": "nonsense"})["accepted"])

    def test_default_body_compatibility_and_explicit_mode(self):
        self.enter()
        self.assertFalse(self.app.body_ink_allowed())
        result = self.app.command(dict(command="suite", active=True, mode="ink", menu=False))
        self.assertEqual(result["suite"]["input_mode"], "body")
        self.assertTrue(self.app.body_ink_allowed())

    def test_valid_stroke_in_queue_and_mode_epoch_invalidates_it(self):
        self.enter()
        result = self.app.command(dict(command="gamepad_stroke", action="A02", speed=.2))
        self.assertTrue(result["accepted"])
        event, theme, generation, epoch = self.app.paint_queue.get_nowait()
        self.assertTrue(self.app.paint_event_allowed(event, epoch))
        self.app.command(dict(command="suite", active=True, mode="ink", menu=True, input_mode="gamepad"))
        self.assertFalse(self.app.paint_event_allowed(event, epoch))
        self.assertFalse(self.app.command(dict(command="gamepad_stroke", action="A02", speed=.2))["accepted"])

    def test_body_events_cannot_paint_in_controller_mode(self):
        self.enter()
        epoch = self.app.suite.epoch
        self.assertFalse(self.app.paint_event_allowed(dict(action="A01"), epoch))
        self.enter("body")
        self.assertTrue(self.app.paint_event_allowed(dict(action="A01"), self.app.suite.epoch))


class HttpContractTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("gamepad_changed_web_ui", HERE / "web_ui.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.commands = []
        self.server = module.ViewerServer("127.0.0.1", 0, lambda: {}, lambda: b"", lambda: b"",
                                          lambda cmd: self.commands.append(cmd) or dict(accepted=True)).start()

    def tearDown(self): self.server.stop()

    def post(self, command, origin=None):
        url = "http://127.0.0.1:%d/api/command" % self.server.address[1]
        headers = {"Content-Type": "application/json"}
        if origin: headers["Origin"] = origin
        req = urllib.request.Request(url, data=json.dumps(command).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as exc: return exc.code, json.load(exc)

    def test_loopback_controller_command_accepted(self):
        status, body = self.post(dict(command="gamepad_stroke", action="A01", speed=.5))
        self.assertEqual(status, 200); self.assertTrue(body["accepted"])

    def test_nan_unknown_and_foreign_origin_rejected_before_dispatch(self):
        self.assertEqual(self.post(dict(command="gamepad_stroke", action="A01", speed=float("nan")))[0], 400)
        self.assertEqual(self.post(dict(command="not_a_command"))[0], 400)
        self.assertEqual(self.post(dict(command="gamepad_stroke", action="A01", speed=.5), "http://foreign.invalid")[0], 403)
        self.assertEqual(self.commands, [])

    def test_nonloopback_controller_request_rejected(self):
        from email.message import Message
        handler = self.server._server.RequestHandlerClass.__new__(self.server._server.RequestHandlerClass)
        payload = json.dumps(dict(command='gamepad_stroke',action='A01',speed=.5)).encode()
        handler.path='/api/command';handler.client_address=('198.51.100.10',12345)
        handler.headers=Message();handler.headers['Content-Length']=str(len(payload))
        handler.rfile=io.BytesIO(payload);responses=[]
        handler.send=lambda status, data, *a, **k:responses.append((status,data))
        handler.do_POST()
        self.assertEqual(responses[0][0],403)
        self.assertEqual(self.commands,[])


if __name__ == "__main__": unittest.main()
