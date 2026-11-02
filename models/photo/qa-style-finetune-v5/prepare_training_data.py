"""Bounded, reproducible content-frame extraction for pretrained AdaIN fine-tuning.

Reads only the explicit P001/P002 allowlist below. Never opens a camera, updates a
source video, or extracts holdout P003/P004/latest-capture images.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


# Two speeds at most per participant/action. Cover every A01-A06 and B01-B08.
# P002 has only the two BODY A01 source videos in this corpus; retain both.
SELECTION = [
    ("P001", "BODY", "A01", "MID"),
    ("P001", "BODY", "A02", "FAST"),
    ("P001", "BODY", "A02", "SLOW"),
    ("P001", "BODY", "A03", "MID"),
    ("P001", "BODY", "A03", "FS"),
    ("P001", "BODY", "A04", "SF"),
    ("P001", "BODY", "A05", "SLOW"),
    ("P001", "BODY", "A05", "FAST"),
    ("P001", "BODY", "A06", "MID"),
    ("P001", "BODY", "A06", "FS"),
    ("P001", "HAND", "B01", "MID"),
    ("P001", "HAND", "B01", "FAST"),
    ("P001", "HAND", "B02", "SLOW"),
    ("P001", "HAND", "B03", "MID"),
    ("P001", "HAND", "B04", "FAST"),
    ("P001", "HAND", "B04", "SLOW"),
    ("P001", "HAND", "B05", "MID"),
    ("P001", "HAND", "B05", "SLOW"),
    ("P001", "HAND", "B06", "FAST"),
    ("P001", "HAND", "B07", "SLOW"),
    ("P001", "HAND", "B07", "MID"),
    ("P001", "HAND", "B08", "FAST"),
    ("P002", "BODY", "A01", "MID"),
    ("P002", "BODY", "A01", "SLOW"),
]
POSITIONS = (0.20, 0.50, 0.80)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_image(path: Path, image: np.ndarray, quality: int = 95) -> None:
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if path.suffix == ".jpg" else []
    ok, data = cv2.imencode(path.suffix, image, params)
    if not ok:
        raise RuntimeError(f"Image encode failed: {path}")
    path.write_bytes(data.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("E:/P001"))
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "training-data")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    images = output / "images"
    audit = output / "audit"
    images.mkdir(parents=True, exist_ok=True)
    audit.mkdir(parents=True, exist_ok=True)

    selected = []
    for person, kind, action, speed in SELECTION:
        assert person in ("P001", "P002") and kind in ("BODY", "HAND")
        path = source / f"{person}_S01_C01_{kind}_{action}_{speed}_R01.mp4"
        if not path.is_file() or path.resolve().parent != source:
            raise RuntimeError(f"Allowlisted source absent or outside source root: {path}")
        selected.append((path, person, kind, action, speed))
    assert len(selected) == 24 and len({x[0] for x in selected}) == 24

    records, videos, previews = [], [], []
    for path, person, kind, action, speed in selected:
        original_stat = path.stat()
        source_sha256 = sha256(path)
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open source: {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or frame_count < 3:
            raise RuntimeError(f"Invalid metadata: {path}, fps={fps}, frames={frame_count}")
        video_record = {
            "source": str(path), "source_sha256": source_sha256,
            "source_size_bytes": original_stat.st_size,
            "source_mtime_ns": original_stat.st_mtime_ns,
            "participant": person, "kind": kind, "action": action, "speed": speed,
            "fps": fps, "frame_count": frame_count, "duration_seconds": frame_count / fps,
        }
        try:
            for position in POSITIONS:
                target = round(position * (frame_count - 1))
                if not cap.set(cv2.CAP_PROP_POS_FRAMES, target):
                    raise RuntimeError(f"Seek failed: {path}@{target}")
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError(f"Frame decode failed: {path}@{target}")
                actual = round(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                if actual != target:
                    raise RuntimeError(f"Unexpected decode index {actual}, requested {target}")
                decoded_timestamp_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
                raw_hash = hashlib.sha256(frame.tobytes()).hexdigest()
                height, width = frame.shape[:2]
                scale = min(1.0, 768 / max(height, width))
                resized = cv2.resize(frame, (round(width * scale), round(height * scale)),
                                     interpolation=cv2.INTER_AREA) if scale < 1 else frame
                name = f"{path.stem}__p{int(position * 100):02d}__f{actual:06d}.jpg"
                target_path = images / name
                write_image(target_path, resized)
                record = {
                    **video_record, "position_fraction": position,
                    "frame_index_zero_based": actual,
                    "timestamp_seconds_from_fps": actual / fps,
                    "decoded_timestamp_seconds": decoded_timestamp_ms / 1000,
                    "source_frame_width": width, "source_frame_height": height,
                    "decoded_bgr_sha256": raw_hash,
                    "output": target_path.relative_to(output).as_posix(),
                    "output_width": int(resized.shape[1]), "output_height": int(resized.shape[0]),
                    "output_sha256": sha256(target_path), "jpeg_quality": 95,
                }
                records.append(record)
                if position == 0.5:
                    thumb = cv2.resize(resized, (320, 180), interpolation=cv2.INTER_AREA)
                    tile = np.full((221, 320, 3), 245, dtype=np.uint8)
                    tile[:180] = thumb
                    label = f"{person} {kind} {action} {speed}"
                    cv2.putText(tile, label, (5, 199), cv2.FONT_HERSHEY_SIMPLEX,
                                0.47, (35, 35, 35), 1, cv2.LINE_AA)
                    cv2.putText(tile, f"50% / {actual / fps:.3f}s", (5, 216),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (65, 65, 65), 1, cv2.LINE_AA)
                    previews.append(tile)
        finally:
            cap.release()
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (original_stat.st_size, original_stat.st_mtime_ns):
            raise RuntimeError(f"Source changed during read: {path}")
        videos.append(video_record)
        print(f"Extracted {len(videos):02d}/24: {path.name}", flush=True)

    assert len(records) == 72
    assert {r["participant"] for r in records} == {"P001", "P002"}
    assert len({r["output_sha256"] for r in records}) == 72, "Exact duplicate output frames"
    assert all(max(r["output_width"], r["output_height"]) <= 768 for r in records)
    expected = {r["output"] for r in records}
    actual_files = {p.relative_to(output).as_posix() for p in images.glob("*") if p.is_file()}
    if actual_files != expected:
        raise RuntimeError("Unexpected pre-existing training images; do not train until inspected")
    for record in records:
        reread = cv2.imread(str(output / record["output"]))
        if reread is None or reread.shape[:2] != (record["output_height"], record["output_width"]):
            raise RuntimeError(f"Saved image integrity failure: {record['output']}")

    contact = np.concatenate([np.concatenate(previews[n:n + 4], axis=1)
                              for n in range(0, len(previews), 4)], axis=0)
    write_image(audit / "midpoint-contact-sheet.png", contact)
    report = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Content images for fixed-style fine-tuning of a pretrained AdaIN decoder",
        "source_root": str(source), "opencv_version": cv2.__version__,
        "script_sha256": sha256(Path(__file__)),
        "selection": "Explicit 24-video allowlist; 14 action types; at most two speed variants per participant/action; no duplicate repetitions",
        "holdout_policy": "Never read P003/P004 or latest-capture photos for training; only P001/P002 BODY/HAND allowlist",
        "source_files_unchanged_size_and_mtime": True,
        "video_count": len(videos), "image_count": len(records),
        "images_by_participant": dict(Counter(r["participant"] for r in records)),
        "images_by_kind": dict(Counter(r["kind"] for r in records)),
        "images_by_action": dict(Counter(r["action"] for r in records)),
        "max_long_side": 768, "sample_positions": list(POSITIONS),
        "limitations": [
            "P002 contains only two eligible A01 videos; this corpus is not participant-balanced.",
            "Fixed time samples cover content variety, not manually verified action-state labels.",
            "This is a small same-environment style-tuning content set, not a new general pose dataset.",
        ],
        "videos": videos, "images": records,
    }
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "train-images.txt").write_text("\n".join(r["output"] for r in records) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# AdaIN fixed-style content samples\n\n"
        "72 JPEG images from 24 allowlisted P001/P002 BODY/HAND videos. "
        "Read only `images/*.jpg` (or `train-images.txt`) for training; `audit/` is not training data.\n\n"
        "Each source is sampled at 20%, 50%, and 80% of its frame-index range; "
        "aspect ratio is preserved and the long side is at most 768 px (JPEG quality 95). "
        "The manifest records each source SHA-256, size, mtime, frame index, timestamp, "
        "decoded-frame SHA-256, and JPEG SHA-256. Source videos were not modified.\n\n"
        "P003/P004 videos and all latest-capture photos are strictly excluded and remain holdouts. "
        "P002 contributes only six images because only two eligible source videos exist. "
        "P001 contributes 66 images; BODY/HAND each contribute 36. "
        "The set covers A01-A06 and B01-B08, with at most two speeds per person/action.\n\n"
        "These are style-training content samples, not new ground-truth action or joint labels. "
        "No training was started by this preparation script.\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("video_count", "image_count", "images_by_participant", "images_by_kind")}))


if __name__ == "__main__":
    main()
