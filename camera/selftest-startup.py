#!/usr/bin/env python3
"""Exercise actual host executable's offline startup failure paths."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    program = str(Path(sys.argv[1]).resolve())
    assert not Path("/sys/module/uvcvideo/parameters/bodyink_ss_bandwidth").exists(), "Run this check on an unpatched host, not the camera board"
    assert subprocess.run([program, "--help"], capture_output=True).returncode == 0
    assert subprocess.run([program, "--exposure", "200"], capture_output=True).returncode == 64
    with tempfile.TemporaryDirectory(prefix="bodyink-camera-startup-") as temporary:
        root = Path(temporary)
        result = subprocess.run([program, "--runtime-dir", temporary], capture_output=True)
        assert result.returncode == 78, result.stderr
        before = (root / "status.json").read_bytes()
        status = json.loads(before)
        assert status["state"] == "failed" and status["reason"] == "bandwidth_patch_unavailable"
        with (root / "capture.lock").open("r+") as held_lock:
            fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([program, "--runtime-dir", temporary], capture_output=True)
            assert result.returncode == 75, result.stderr
            assert (root / "status.json").read_bytes() == before, "Competing owner corrupted existing status"
    print("PASS: help/argument validation, unsupported-kernel exit 78, exclusive owner exit 75 and status preservation")


if __name__ == "__main__":
    main()
