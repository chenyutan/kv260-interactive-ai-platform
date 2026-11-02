"""Check the archived KV260 comparisons and optionally rebuild the CPU pixel reference."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent
ROOT = DATA.parent


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(array):
    return hashlib.sha256(array.tobytes()).hexdigest()


def check_recorded_rows():
    board = load_json(ROOT / "board/roi-equivalence.json")
    require(board["actual_pl_dma_tested"] and not board["live_camera_read"], "unexpected source")
    require(board["cases"] == len(board["rows"]) == 36, "expected 36 board cases")
    reference = load_json(DATA / "roi-reference.json")
    keys = []
    by_key = {row["key"]: row for row in reference["cases"]}
    counts = Counter((row["height"], row["roi"]) for row in board["rows"])
    require(counts == {(h, r): 6 for h in (540, 720, 1080) for r in (0, 1)}, "incomplete ROI coverage")
    with np.load(DATA / "roi-heatmaps.npz", allow_pickle=False) as stored:
        for row in board["rows"]:
            key = "{height}_{frame}_{roi}".format(**row)
            keys.append(key)
            require(all(row[field] for field in ("input_equal", "heatmaps_equal", "guard_equal", "old_dpu_equal")), key)
            require(row["status"] == 2 and row["cycles"] > 0, "hardware status: " + key)
            heatmap = stored[key]
            require(heatmap.shape == tuple(reference["heatmap_shape"]) and heatmap.dtype == np.int8, key)
            require(digest(heatmap) == by_key[key]["heatmap_sha256"], "golden heatmap: " + key)
        require(len(set(keys)) == 36 and set(keys) == set(by_key) == set(stored.files), "unmatched references")
    decoder = load_json(ROOT / "board/decoder-comparison.json")
    require(decoder["passed"] and decoder["bitwise_equal"] and decoder["equal_cases"] == 275, "decoder comparison")
    require(decoder["stored_heatmaps"] == 8 and all(decoder["versions"][v]["samples"] == 160 for v in ("baseline", "neon1")), "decoder sample count")


def check_fixture(compiler):
    config = load_json(DATA / "synthetic-roi.json")
    with np.load(DATA / "synthetic-roi.npz", allow_pickle=False) as fixture:
        source = fixture["source"]
        rgb = fixture["rgb"]
        tensor = fixture["int8_tensor"]
    for arr, key in [(source, "source"), (rgb, "rgb"), (tensor, "int8")]:
        require(digest(arr) == config[key + "_sha256"], "synthetic fixture: " + key)
    require(source.shape == (config["height"], config["stride"]), "source layout")
    require(rgb.shape == tensor.shape == (256, 256, 3), "output layout")
    if compiler:
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            main = work / ("reference.exe" if os.name == "nt" else "reference")
            subprocess.run([compiler, "-std=c99", "-O2", str(ROOT/"fpga/sim/reference_driver.c"),
                            str(ROOT/"bodyink-live/preprocess.c"), "-o", str(main)], check=True)
            # reference_driver expects tightly packed YUYV, without AXI row padding.
            source[:, :config["width"] * 2].tofile(work / "source.yuyv")
            args = [config["width"], config["height"], *config["roi_xywh"]]
            subprocess.run([str(main), str(work/"source.yuyv"), str(work/"rgb.bin"), *map(str,args)], check=True)
            actual = np.fromfile(work/"rgb.bin", np.uint8).reshape(rgb.shape)
            require(np.array_equal(actual, rgb), "CPU RGB reconstruction differs from the archived RGB")
        tables = [np.array([int(v, 16) for v in (ROOT/f"fpga/vectors/quant_{ch}.hex").read_text().split()], np.uint8) for ch in "rgb"]
        restored = np.stack([table[actual[:, :, ch]] for ch, table in enumerate(tables)], axis=2)
        require(np.array_equal(restored.view(np.int8), tensor), "ROM-quantized CPU pixels differ from the archived INT8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", help="optional C99 compiler, for example gcc or clang")
    args = parser.parse_args()
    check_recorded_rows()
    check_fixture(args.cc)
    print("PASS: 36 historical PL/DPU comparisons; 36 golden heatmaps; 275 historical decoder comparisons; synthetic ROI fixture.")
    if args.cc:
        print("PASS: rebuilt CPU reference and quantization ROMs reproduce all 65,536 synthetic RGB/INT8 pixels.")
