#!/usr/bin/env python3
"""8 Hz fixed-period client against real C limiter + 60 Hz timer/25 ms stalls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from snapshot import capture_snapshot


def main():
    producer = str(Path(sys.argv[1]).resolve())
    count = 64
    expected = bytes(range(251)) * (4147200 // 251) + bytes(range(4147200 % 251))
    digest = hashlib.sha256(expected).digest()
    with tempfile.TemporaryDirectory(prefix="bodyink-limiter-selftest-") as directory:
        root = Path(directory)
        child = subprocess.Popen([producer, directory, str(count)])
        try:
            for _ in range(300):
                if (root / "snapshot.sock").exists():
                    break
                if child.poll() is not None:
                    raise RuntimeError("Producer failed before starting")
                time.sleep(0.01)
            start = time.monotonic()
            requests = []
            for i in range(count):
                time.sleep(max(0, start + i * 0.125 - time.monotonic()))
                requested = time.monotonic()
                metadata, frame = capture_snapshot(str(root / "snapshot.sock"), timeout=2.0)
                requests.append(dict(sequence=metadata["sequence"], latency=time.monotonic() - requested))
                assert hashlib.sha256(frame).digest() == digest
            assert child.wait(timeout=3) == 0
            status = json.loads((root / "status.json").read_text())
            assert status["snapshot_count"] == count
            assert status["snapshot_rejected"] == status["snapshot_failures"] == 0
            assert all(a["sequence"] < b["sequence"] for a, b in zip(requests, requests[1:]))
            maximum = max(r["latency"] for r in requests)
            assert maximum < 0.5, maximum
            print(json.dumps(dict(result="PASS", requests=count, requested_hz=8,
                                  delivered=status["snapshot_count"], rejected=status["snapshot_rejected"],
                                  deferred=status["snapshot_deferred"], max_latency_seconds=maximum,
                                  mean_latency_seconds=sum(r["latency"] for r in requests) / count,
                                  simulated_frame_hz=60, injected_work_ms=25), indent=2))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
