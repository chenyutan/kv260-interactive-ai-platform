#!/usr/bin/env python3
"""Host verification of real C producer / Python consumer compatibility."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from snapshot import capture_snapshot


def main():
    producer = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="bodyink-camera-selftest-") as directory:
        root = Path(directory)
        child = subprocess.Popen([str(producer), directory])
        try:
            for _ in range(100):
                if (root / "snapshot.sock").exists():
                    break
                if child.poll() is not None:
                    raise RuntimeError("Producer exited before listening")
                time.sleep(0.01)
            metadata, pixels = capture_snapshot(str(root / "snapshot.sock"))
            assert metadata["sequence"] == 42
            assert metadata["timestamp_seconds"] == 123.456789
            assert len(pixels) == 4147200
            expected = bytes(range(251)) * (len(pixels) // 251) + bytes(range(len(pixels) % 251))
            assert pixels == expected, "Frame changed or was truncated during fd transfer"
            assert child.wait(timeout=3) == 0
            status = json.loads((root / "status.json").read_text())
            assert status["fps_timestamp"] == status["fps_monotonic"] == 60.0
            assert status["frames"] == 601 and status["good_frames"] == 599
            assert status["sequence_gaps"] == 3
            assert status["error_frames"] == status["short_frames"] == 1
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    print("PASS: immutable 4,147,200-byte C memfd snapshot, source timestamps, Python consumer, atomic JSON and measured FPS/counters")


if __name__ == "__main__":
    main()
