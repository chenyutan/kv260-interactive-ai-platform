"""Submit one public synthetic ROI to the installed KV260 preprocessor and compare every output byte."""
import argparse
import errno
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=root / "bodyink-live",
                        help="bodyink-live directory containing the compiled ARM libraries")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    sys.path[:0] = [str(runtime / "pl_bridge"), str(runtime)]
    from pl_adapter import PLAdapter

    config = json.loads((root / "data/synthetic-roi.json").read_text(encoding="utf-8"))
    with np.load(root / "data/synthetic-roi.npz", allow_pickle=False) as example:
        source, expected = example["source"], example["int8_tensor"]
    if hashlib.sha256(source.tobytes()).hexdigest() != config["source_sha256"]:
        raise ValueError("source fixture integrity check failed")
    if hashlib.sha256(expected.tobytes()).hexdigest() != config["int8_sha256"]:
        raise ValueError("expected tensor integrity check failed")
    try:
        adapter = PLAdapter()
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            sys.exit("PL already has an owner. Run the check during a maintenance window.")
        raise
    try:
        result = adapter.resize(source, config["width"], config["height"], config["stride"],
                                roi=tuple(config["roi_xywh"])).data
        np.testing.assert_array_equal(result, expected[np.newaxis])
        print(json.dumps({"equal_bytes": int(result.nbytes), "sha256": hashlib.sha256(result.tobytes()).hexdigest(),
                          "hardware_ms": adapter.last_timing["hardware_ms"]}))
    finally:
        if adapter.pp.active_buffers is not None:
            # Preserve the same BO ownership rule used by the production board test.
            print("DMA has not drained. Keep this process alive and reset the PL before terminating it.", file=sys.stderr, flush=True)
            while True:
                time.sleep(60)
        adapter.close()


if __name__ == "__main__":
    main()
