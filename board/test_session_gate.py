"""Run the board's login timer against a private run directory and a fake systemd."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'bodyink-live/session_gate_0917.py'
spec = importlib.util.spec_from_file_location('session_gate', path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class LoginGateTest(unittest.TestCase):
    def test_retries_rejected_start_and_leaves_accepted_start_alone(self):
        listing = ['1 1000 ubuntu seat0',
                   'Name=ubuntu\nClass=user\nType=x11\nActive=yes\nRemote=no\nSeat=seat0']
        for failure in (subprocess.CalledProcessError(1, 'systemctl'),
                        subprocess.TimeoutExpired('systemctl', 8), OSError('systemctl unavailable')):
            with self.subTest(failure=type(failure)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ready = root / 'bodyink-session-ready'
                (root / 'bodyink-session-gate.json').write_text(json.dumps({'session': '1', 'since': 1}))
                def enqueue(*args, **kwargs):
                    self.assertTrue(ready.exists())
                    if start.call_count == 1:
                        raise failure
                with patch.object(gate, 'Path', side_effect=lambda p: root / Path(p).name), \
                     patch.object(gate.time, 'monotonic', return_value=120.), \
                     patch.object(gate, 'command', side_effect=listing * 2), \
                     patch.object(gate.subprocess, 'run', side_effect=enqueue) as start:
                    with self.assertRaises(type(failure)):
                        gate.main()
                    self.assertFalse(ready.exists())
                    gate.main()
                    gate.main()
                    self.assertTrue(ready.exists())
                    self.assertEqual(start.call_count, 2)


if __name__ == '__main__':
    unittest.main()
