"""Rebuild the pinned photo model from authorized upstream weights and project changes."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def restore(base, output):
    here = Path(__file__).resolve().parent
    layout = json.loads((here / "layout.json").read_text(encoding="utf-8"))
    delta = memoryview((here / "graph-and-training-delta.bin").read_bytes())
    cursor = 0
    result = bytearray()
    with np.load(base / "vgg_normalised.npz", allow_pickle=False) as encoder, np.load(base / "decoder.npz", allow_pickle=False) as decoder:
        for part in layout["parts"]:
            kind, size = part[0], part[-1]
            if kind == "literal":
                result.extend(delta[cursor:cursor + size])
            elif kind == "encoder":
                result.extend(encoder[part[1]].tobytes())
            elif kind == "decoder":
                original = np.frombuffer(decoder[part[1]].tobytes(), dtype=np.uint8)
                changes = np.frombuffer(delta[cursor:cursor + size], dtype=np.uint8)
                result.extend(np.bitwise_xor(original, changes).tobytes())
            else:
                raise ValueError(kind)
            if kind != "encoder":
                cursor += size
    digest = hashlib.sha256(result).hexdigest()
    if cursor != len(delta) or digest != layout["sha256"]:
        raise ValueError("Upstream weights or project patch do not match expected SHA-256")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    print(digest, output)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=root / "qa-adain-v4/upstream/models")
    parser.add_argument("--output", type=Path, default=root.parents[1] / "ink-suite/environment/models/figure-ink-v3.onnx")
    args = parser.parse_args()
    restore(args.base, args.output)
