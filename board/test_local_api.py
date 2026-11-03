"""Exercise both shipped viewer implementations over real loopback HTTP."""
from http.client import HTTPConnection
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LocalApiTests(unittest.TestCase):
    def test_loopback_camera_and_control_boundaries(self):
        for folder in ("bodyink-live", "ink-suite/runtime"):
            with self.subTest(folder=folder):
                spec = spec_from_file_location("viewer", ROOT / folder / "web_ui.py")
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                commands = []
                callbacks = (lambda: {}, lambda: b"", lambda: b"jpeg", commands.append)
                with self.assertRaisesRegex(ValueError, "loopback"):
                    module.ViewerServer("0.0.0.0", 0, *callbacks)
                viewer = module.ViewerServer("127.0.0.1", 0, *callbacks)
                viewer.start()
                try:
                    host, port = viewer.address
                    def request(method, path, headers=None):
                        conn = HTTPConnection(host, port, timeout=3)
                        try:
                            body = b'{"command":"clear"}' if method == "POST" else None
                            conn.request(method, path, body=body, headers=headers or {})
                            response = conn.getresponse()
                            response.read()
                            return response.status
                        finally:
                            conn.close()
                    self.assertEqual(request("GET", "/api/state"), 200)
                    self.assertEqual(request("GET", "/api/state", {"Host":"external.test"}), 403)
                    self.assertEqual(request("POST", "/api/command", {"Origin":"https://external.test"}), 403)
                    self.assertEqual(commands, [])
                    self.assertEqual(request("POST", "/api/command", {"Origin":f"http://{host}:{port}"}), 200)
                    self.assertEqual(commands, [{"command":"clear"}])
                finally:
                    viewer.stop()


if __name__ == "__main__":
    unittest.main()
