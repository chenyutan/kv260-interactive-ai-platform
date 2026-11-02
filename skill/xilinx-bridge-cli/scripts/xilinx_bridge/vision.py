"""KV260 image-session preparation, quantitative evaluation, and human review state."""
from __future__ import annotations

import json
import hashlib
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import axis_video, env_detect


class VisionError(ValueError):
    pass


SCHEMA_VERSION = 1
DEFAULT_THRESHOLDS = {
    "pixel_tolerance": 0,
    "max_mae": 0.0,
    "max_rmse": 0.0,
    "min_psnr_db": 99.0,
    "min_ssim": 1.0,
    "max_bad_pixel_ratio": 0.0,
    "max_abs_error": 0,
}
KV260_PART = "xck26-sfvc784-2LV-c"
KV260_BOARD_PARTS = [
    "xilinx.com:kv260_som:part0:2.0",
    "xilinx.com:kv260_som:part0:1.4",
    "xilinx.com:kv260_som:part0:1.3",
    "xilinx.com:kv260_som:part0:1.2",
]
_MAX_IMAGE_BYTES = 128 * 1024 * 1024
_MAX_RAW_FRAME_BYTES = 512 * 1024 * 1024


def _pillow():
    try:
        from PIL import Image, ImageChops, ImageEnhance  # type: ignore
    except ImportError as exc:
        raise VisionError(
            "视觉工具需要 Pillow；执行 pip install -e '.[vision]' 或 pip install Pillow"
        ) from exc
    return Image, ImageChops, ImageEnhance


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_exists(path: str, session: Path) -> bool:
    if not path:
        return False
    try:
        candidate = Path(path).resolve()
        return candidate.is_relative_to(session) and candidate.is_file()
    except (OSError, ValueError):
        return False


def validate_detail(value: str) -> str:
    if value not in {"compact", "standard"}:
        raise VisionError("detail 必须是 compact 或 standard")
    return value


def _session(path: str, *, create: bool = False) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise VisionError("session_dir 必须是非空路径")
    target = Path(path).expanduser().resolve()
    if create:
        target.mkdir(parents=True, exist_ok=True)
    elif not (target / "session.json").is_file():
        raise VisionError(f"视觉 session 不存在或缺少 session.json: {target}")
    return target


def _source_image(path: str, label: str) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise VisionError(f"{label} 不存在: {path}")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise VisionError(f"无法读取 {label}: {source}") from exc
    if size <= 0 or size > _MAX_IMAGE_BYTES:
        raise VisionError(f"{label} 文件大小必须在 1..{_MAX_IMAGE_BYTES} 字节")
    return source


def _load_normalized(path: Path, pixel_format: str):
    Image, *_ = _pillow()
    mode = "L" if pixel_format == "gray" else "RGB"
    try:
        with Image.open(path) as image:
            image.load()
            return image.convert(mode)
    except Exception as exc:  # Pillow exposes format-specific exception types
        raise VisionError(f"无法解码图像 {path}: {exc}") from exc


def _save_image(image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)


def _pixel_iter(image):
    """Pillow 12.3+ name with a compatibility fallback for Pillow 10/11."""
    getter = getattr(image, "get_flattened_data", None)
    return iter(getter() if getter else image.getdata())


def _pixels(image) -> list:
    return list(_pixel_iter(image))


def _image_values(image, pixel_format: str) -> list[object]:
    values = _pixels(image)
    if pixel_format == "gray":
        return [int(value) for value in values]
    return [[int(value[0]), int(value[1]), int(value[2])] for value in values]


def _write_image_frame(base: Path, image, pixel_format: str,
                       pixels_per_clock: int) -> tuple[Path, Path, dict]:
    """Stream rows through the shared AXIS packer to bound full-frame memory."""
    mem = base.with_suffix(".mem")
    binary = base.with_suffix(".bin")
    packed_meta = None
    total_beats = 0
    with mem.open("w", encoding="ascii", newline="\n") as mem_stream, binary.open("wb") as binary_stream:
        for y in range(image.height):
            row = image.crop((0, y, image.width, y + 1))
            packed = axis_video.pack_frame(
                _image_values(row, pixel_format), image.width, 1,
                pixel_format, 8, pixels_per_clock,
            )
            if packed_meta is None:
                packed_meta = {key: value for key, value in packed.items() if key != "beats"}
            digits = (packed["data_width"] + 3) // 4
            for value in packed["beats"]:
                mem_stream.write(f"{value:0{digits}x}\n")
                binary_stream.write(value.to_bytes(packed["beat_bytes"], "little"))
                total_beats += 1
    assert packed_meta is not None
    packed_meta["beats"] = total_beats
    return mem, binary, packed_meta


def raw_frame_to_png(session_dir: str, raw_frame: str,
                     layout: str = "axis_packed", stride_bytes: int = 0) -> str:
    """Normalize one KV260 host buffer into the existing review session.

    ``axis_packed`` is exactly the little-endian layout emitted by prepare.
    The other layouts cover common file-backed XRT/V4L2 host outputs while
    keeping stride and channel-order choices explicit.
    """
    session = _session(session_dir)
    manifest = _read_manifest(session)
    source = Path(raw_frame).expanduser().resolve()
    if not source.is_file():
        raise VisionError(f"板端原始帧不存在: {raw_frame}")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise VisionError(f"无法读取板端原始帧: {source}") from exc
    if not 1 <= size <= _MAX_RAW_FRAME_BYTES:
        raise VisionError(f"板端原始帧必须在 1..{_MAX_RAW_FRAME_BYTES} 字节")
    if layout not in {"axis_packed", "rgb24", "bgr24", "gray8"}:
        raise VisionError("output_layout 必须是 axis_packed/rgb24/bgr24/gray8/image")
    if isinstance(stride_bytes, bool) or not isinstance(stride_bytes, int) or not 0 <= stride_bytes <= 1_048_576:
        raise VisionError("stride_bytes 必须是 0..1048576 的整数")

    frame = manifest["frame"]
    width, height = frame["width"], frame["height"]
    pixel_format = frame["pixel_format"]
    raw = source.read_bytes()
    Image, *_ = _pillow()

    if layout == "axis_packed":
        if stride_bytes:
            raise VisionError("axis_packed 的行宽由 session 决定，stride_bytes 必须为 0")
        if frame["component_bits"] != 8 or pixel_format not in {"gray", "rgb"}:
            raise VisionError("axis_packed 原始帧当前要求 gray/rgb、8-bit 分量")
        row_bytes = frame["beats_per_line"] * frame["beat_bytes"]
        expected = row_bytes * height
        if len(raw) != expected:
            raise VisionError(f"axis_packed 字节数应为 {expected}，实际 {len(raw)}")
        channels = 1 if pixel_format == "gray" else 3
        lane_bits = channels * 8
        pixels = bytearray(width * height * channels)
        for y in range(height):
            row_offset = y * row_bytes
            for beat_index in range(frame["beats_per_line"]):
                offset = row_offset + beat_index * frame["beat_bytes"]
                beat = int.from_bytes(raw[offset:offset + frame["beat_bytes"]], "little")
                for lane in range(frame["pixels_per_clock"]):
                    x = beat_index * frame["pixels_per_clock"] + lane
                    if x >= width:
                        break
                    word = beat >> (lane * lane_bits)
                    target = (y * width + x) * channels
                    if channels == 1:
                        pixels[target] = word & 0xFF
                    else:
                        # Shared packer encodes one RGB lane as G,B,R from LSB.
                        pixels[target:target + 3] = bytes((
                            (word >> 16) & 0xFF, word & 0xFF, (word >> 8) & 0xFF,
                        ))
        image = Image.frombytes("L" if channels == 1 else "RGB", (width, height), bytes(pixels))
    else:
        channels = 1 if layout == "gray8" else 3
        active_row = width * channels
        row_stride = stride_bytes or active_row
        if row_stride < active_row:
            raise VisionError(f"stride_bytes 至少为 {active_row}")
        expected = row_stride * height
        if len(raw) != expected:
            raise VisionError(f"{layout} 字节数应为 {expected}，实际 {len(raw)}")
        contiguous = bytearray(active_row * height)
        for y in range(height):
            source_row = raw[y * row_stride:y * row_stride + active_row]
            target = y * active_row
            if layout == "bgr24":
                for x in range(width):
                    b, g, r = source_row[x * 3:x * 3 + 3]
                    contiguous[target + x * 3:target + x * 3 + 3] = bytes((r, g, b))
            else:
                contiguous[target:target + active_row] = source_row
        image = Image.frombytes("L" if channels == 1 else "RGB", (width, height), bytes(contiguous))

    output = session / "board" / "output.png"
    _save_image(image, output)
    return str(output)


def _platform_profile() -> dict:
    installed: list[str] = []
    try:
        detected = env_detect.detect()
        for entry in detected.get("vitis", []):
            candidate = Path(entry["root"]) / "base_platforms" / "kv260_base" / "kv260_base.xpfm"
            if candidate.is_file():
                installed.append(str(candidate.resolve()))
    except Exception:
        pass
    return {
        "name": "kv260_base", "device_part": KV260_PART,
        "board_part_candidates": KV260_BOARD_PARTS,
        "installed_xpfm": installed,
        "recommended_xpfm": installed[0] if installed else "",
        "runtime": "linux_xrt", "processor": "cortex-a53",
    }


def _thresholds(value: dict | None, base: dict | None = None) -> dict:
    result = dict(DEFAULT_THRESHOLDS)
    if base is not None:
        result.update(base)
    if value is not None:
        if not isinstance(value, dict):
            raise VisionError("thresholds 必须是对象")
        unknown = set(value) - set(DEFAULT_THRESHOLDS)
        if unknown:
            raise VisionError(f"thresholds 含未知字段: {sorted(unknown)}")
        result.update(value)
    for key, raw in result.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise VisionError(f"thresholds.{key} 必须是有限数字")
    tolerance = result["pixel_tolerance"]
    if int(tolerance) != tolerance or not 0 <= tolerance <= 255:
        raise VisionError("thresholds.pixel_tolerance 必须是 0..255 的整数")
    if any(result[key] < 0 for key in (
        "max_mae", "max_rmse", "min_psnr_db", "min_ssim",
        "max_bad_pixel_ratio", "max_abs_error",
    )):
        raise VisionError("thresholds 数值不能为负")
    if result["min_ssim"] > 1 or result["max_bad_pixel_ratio"] > 1:
        raise VisionError("thresholds.min_ssim/max_bad_pixel_ratio 必须在 0..1 之间")
    if int(result["max_abs_error"]) != result["max_abs_error"] or result["max_abs_error"] > 255:
        raise VisionError("thresholds.max_abs_error 必须是 0..255 的整数")
    result["pixel_tolerance"] = int(tolerance)
    result["max_abs_error"] = int(result["max_abs_error"])
    return result


def _prepare_result(manifest: dict, reused: bool = False) -> dict:
    return {
        "status": "ready", "reused": reused,
        "session": manifest["session_dir"], "platform": manifest["platform"],
        "frame": manifest["frame"], "paths": manifest["paths"],
        "image_test": manifest["image_test"], "next_action": (
            "把 image_test 加上 dut/parameters 后直接传给现有仿真工具；上板结果保存为图像后调用 kv260_vision_evaluate"
        ),
    }


def project_prepare(result: dict, detail: str = "compact") -> dict:
    if validate_detail(detail) == "standard":
        return result
    platform = result["platform"]
    paths = result["paths"]
    compact = {
        "status": result["status"], "session": result["session"],
        "part": platform["device_part"], "xpfm": platform["recommended_xpfm"],
        "frame": result["frame"], "image_test": result["image_test"],
        "board_input_bin": paths["input_bin"],
    }
    if result.get("reused"):
        compact["reused"] = True
    return compact


def project_evaluation(result: dict, detail: str = "compact") -> dict:
    if validate_detail(detail) == "standard":
        return result
    compact = {
        "status": result["status"],
        "artifacts": {"output": result["output_png"]},
    }
    if result["status"] == "review_required":
        compact["quality"] = result["quality"]
    if result.get("cached"):
        compact["cached"] = True
    if "metrics" in result:
        compact["metrics"] = result["metrics"]
    if result.get("failures"):
        compact["failures"] = result["failures"]
        compact["thresholds"] = result["thresholds"]
    if result.get("diff_png"):
        compact["artifacts"]["diff"] = result["diff_png"]
    return compact


def project_review(result: dict) -> dict:
    if result.get("status") in {"cleared", "unreadable"}:
        return {"status": result["status"]}
    review = result.get("review", {})
    return {
        "decision": review.get("decision"), "reviewer": review.get("reviewer"),
        "note": review.get("note", ""),
    }


def project_ui(result: dict) -> dict:
    return {
        "url": result.get("url", ""), "status": result.get("status", ""),
        "reused": bool(result.get("reused", False)),
    }


def project_status(result: dict, detail: str = "compact") -> dict:
    if validate_detail(detail) == "standard":
        return result
    platform = result["platform"]
    compact = {
        "status": result["status"], "session": result["session"],
        "part": platform["device_part"], "xpfm": platform["recommended_xpfm"],
        "frame": result["frame"],
    }
    if "evaluation" in result:
        evaluation = result["evaluation"]
        compact["evaluation"] = (
            {"status": "unreadable"}
            if evaluation.get("status") == "unreadable"
            else project_evaluation(evaluation)
        )
    if "review" in result:
        review = result["review"]
        compact["review"] = (
            project_review({"review": review}) if "review" not in review else project_review(review)
        )
    if "review_server" in result:
        compact["ui"] = project_ui(result["review_server"])
    return compact


def prepare_session(session_dir: str, input_image: str, golden_image: str = "",
                    pixel_format: str = "rgb", pixels_per_clock: int = 1,
                    thresholds: dict | None = None, overwrite: bool = False) -> dict:
    if not isinstance(overwrite, bool):
        raise VisionError("overwrite 必须是布尔值")
    fmt = str(pixel_format).lower().replace("gray8", "gray").replace("rgb888", "rgb")
    if fmt not in {"gray", "rgb"}:
        raise VisionError("pixel_format 当前支持 gray/rgb")
    session = _session(session_dir, create=True)
    manifest_path = session / "session.json"
    source = _source_image(input_image, "input_image")
    golden_source = _source_image(golden_image, "golden_image") if golden_image else None
    threshold_values = _thresholds(thresholds)
    fingerprint = {
        "input_sha256": _sha256(source),
        "golden_sha256": _sha256(golden_source) if golden_source else "",
        "pixel_format": fmt, "pixels_per_clock": pixels_per_clock,
        "thresholds": threshold_values,
    }
    if manifest_path.exists() and not overwrite:
        existing = _read_manifest(session)
        if existing.get("fingerprint") == fingerprint:
            required = [
                existing.get("paths", {}).get("input_png", ""),
                existing.get("paths", {}).get("input_mem", ""),
                existing.get("paths", {}).get("input_bin", ""),
            ]
            if golden_source:
                required.extend([
                    existing.get("paths", {}).get("golden_png", ""),
                    existing.get("paths", {}).get("golden_mem", ""),
                    existing.get("paths", {}).get("golden_bin", ""),
                ])
            if not all(_artifact_exists(path, session) for path in required):
                raise VisionError(
                    f"session 指纹匹配但产物不完整: {session}；传 overwrite=true 重建"
                )
            return _prepare_result(existing, reused=True)
        raise VisionError(
            f"session 已存在且输入或参数已变化，拒绝覆盖: {session}；确认后传 overwrite=true"
        )
    input_normalized = _load_normalized(source, fmt)
    golden_normalized = _load_normalized(golden_source, fmt) if golden_source else None
    if golden_normalized and golden_normalized.size != input_normalized.size:
        raise VisionError(
            f"golden 尺寸 {golden_normalized.size} 与 input {input_normalized.size} 不一致"
        )
    images = session / "images"
    frames = session / "frames"
    images.mkdir(exist_ok=True)
    frames.mkdir(exist_ok=True)
    # A confirmed overwrite starts a new evidence set while keeping a running
    # local review server reusable for this directory.
    if overwrite:
        for stale in (
            session / "evaluation.json", session / "review.json",
            images / "output.png", images / "diff.png",
        ):
            stale.unlink(missing_ok=True)
    input_png = images / "input.png"
    golden_png = images / "golden.png"
    _save_image(input_normalized, input_png)
    if golden_normalized:
        _save_image(golden_normalized, golden_png)
    elif golden_png.exists():
        golden_png.unlink()
    if not golden_normalized:
        (frames / "golden.mem").unlink(missing_ok=True)
        (frames / "golden.bin").unlink(missing_ok=True)

    width, height = input_normalized.size
    try:
        input_mem, input_bin, packed_input = _write_image_frame(
            frames / "input", input_normalized, fmt, pixels_per_clock
        )
        golden_mem = golden_bin = None
        if golden_normalized:
            golden_mem, golden_bin, _ = _write_image_frame(
                frames / "golden", golden_normalized, fmt, pixels_per_clock
            )
    except axis_video.AxisVideoSpecError as exc:
        raise VisionError(str(exc)) from exc

    image_test = {
        "width": width, "height": height, "output_width": width, "output_height": height,
        "pixel_format": fmt, "component_bits": 8, "pixels_per_clock": pixels_per_clock,
        "input_mem": str(input_mem),
    }
    if golden_mem:
        image_test["expected_mem"] = str(golden_mem)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created": time.time(),
        "session_dir": str(session),
        "platform": _platform_profile(),
        "frame": {
            "width": width, "height": height, "pixel_format": fmt,
            "component_bits": 8, "pixels_per_clock": pixels_per_clock,
            "data_width": packed_input["data_width"],
            "beats_per_line": packed_input["beats_per_line"],
            "beat_bytes": packed_input["beat_bytes"],
            "beats": packed_input["beats"],
        },
        "paths": {
            "input_png": str(input_png), "golden_png": str(golden_png) if golden_normalized else "",
            "input_mem": str(input_mem), "input_bin": str(input_bin),
            "golden_mem": str(golden_mem or ""), "golden_bin": str(golden_bin or ""),
        },
        "thresholds": threshold_values,
        "fingerprint": fingerprint,
        "image_test": image_test,
    }
    _atomic_json(manifest_path, manifest)
    return _prepare_result(manifest)


def _read_manifest(session: Path) -> dict:
    try:
        payload = json.loads((session / "session.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisionError(f"session.json 无法读取: {session}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise VisionError(f"不支持的视觉 session schema: {payload.get('schema_version')}")
    return payload


def _luma(value, mode: str) -> float:
    if mode == "L":
        return float(value)
    r, g, b = value
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _quality_metrics(image) -> dict:
    histogram = [0] * 256
    count = 0
    total = total_sq = 0.0
    dark = bright = 0
    for pixel in _pixel_iter(image):
        value = _luma(pixel, image.mode)
        count += 1
        total += value
        total_sq += value * value
        dark += value <= 1
        bright += value >= 254
        histogram[min(255, max(0, int(round(value))))] += 1
    mean = total / count
    variance = max(0.0, total_sq / count - mean * mean)
    entropy = -sum((n / count) * math.log2(n / count) for n in histogram if n)
    return {
        "mean_luma": round(mean, 6), "luma_stddev": round(math.sqrt(variance), 6),
        "dark_clip_ratio": round(dark / count, 8),
        "bright_clip_ratio": round(bright / count, 8),
        "luma_entropy_bits": round(entropy, 6),
    }


def _comparison(output, golden, thresholds: dict) -> dict:
    channels = 1 if output.mode == "L" else 3
    bad_pixels = 0
    pixel_count = component_count = 0
    abs_sum = square_sum = 0.0
    max_abs = 0
    channel_sums = [0.0] * channels
    sum_o = sum_g = sum_o2 = sum_g2 = sum_og = 0.0
    tolerance = thresholds["pixel_tolerance"]
    for actual, expected in zip(_pixel_iter(output), _pixel_iter(golden)):
        a = (actual,) if channels == 1 else actual
        e = (expected,) if channels == 1 else expected
        diffs = [abs(int(x) - int(y)) for x, y in zip(a, e)]
        pixel_count += 1
        component_count += channels
        abs_sum += sum(diffs)
        square_sum += sum(diff * diff for diff in diffs)
        max_abs = max(max_abs, *diffs)
        for index, diff in enumerate(diffs):
            channel_sums[index] += diff
        if any(diff > tolerance for diff in diffs):
            bad_pixels += 1
        lo, lg = _luma(actual, output.mode), _luma(expected, golden.mode)
        sum_o += lo; sum_g += lg
        sum_o2 += lo * lo; sum_g2 += lg * lg; sum_og += lo * lg
    mse = square_sum / component_count
    mae = abs_sum / component_count
    rmse = math.sqrt(mse)
    psnr = math.inf if mse == 0 else 20 * math.log10(255.0 / rmse)
    mean_o, mean_g = sum_o / pixel_count, sum_g / pixel_count
    var_o = max(0.0, sum_o2 / pixel_count - mean_o * mean_o)
    var_g = max(0.0, sum_g2 / pixel_count - mean_g * mean_g)
    cov = sum_og / pixel_count - mean_o * mean_g
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    denom = (mean_o * mean_o + mean_g * mean_g + c1) * (var_o + var_g + c2)
    ssim = ((2 * mean_o * mean_g + c1) * (2 * cov + c2) / denom) if denom else 1.0
    metrics = {
        "exact_match": max_abs == 0,
        "mae": round(mae, 8), "rmse": round(rmse, 8),
        "psnr_db": "inf" if math.isinf(psnr) else round(psnr, 6),
        "global_ssim": round(max(-1.0, min(1.0, ssim)), 8),
        "max_abs_error": max_abs,
        "bad_pixel_ratio": round(bad_pixels / pixel_count, 8),
        "bad_pixels": bad_pixels, "pixels": pixel_count,
        "channel_mae": [round(value / pixel_count, 8) for value in channel_sums],
    }
    return metrics


def _gate(metrics: dict, thresholds: dict) -> tuple[str, list[str]]:
    failures: list[str] = []
    checks = (
        (metrics["mae"] <= thresholds["max_mae"], "mae", metrics["mae"], thresholds["max_mae"]),
        (metrics["rmse"] <= thresholds["max_rmse"], "rmse", metrics["rmse"], thresholds["max_rmse"]),
        (metrics["global_ssim"] >= thresholds["min_ssim"], "ssim", metrics["global_ssim"], thresholds["min_ssim"]),
        (metrics["bad_pixel_ratio"] <= thresholds["max_bad_pixel_ratio"], "bad_pixel_ratio", metrics["bad_pixel_ratio"], thresholds["max_bad_pixel_ratio"]),
        (metrics["max_abs_error"] <= thresholds["max_abs_error"], "max_abs_error", metrics["max_abs_error"], thresholds["max_abs_error"]),
    )
    for passed, name, actual, limit in checks:
        if not passed:
            failures.append(f"{name} actual={actual} limit={limit}")
    psnr = math.inf if metrics["psnr_db"] == "inf" else float(metrics["psnr_db"])
    if psnr < thresholds["min_psnr_db"]:
        failures.append(f"psnr_db actual={psnr} limit={thresholds['min_psnr_db']}")
    return ("pass" if not failures else "fail"), failures


def evaluate_session(session_dir: str, output_image: str,
                     thresholds: dict | None = None) -> dict:
    session = _session(session_dir)
    manifest = _read_manifest(session)
    source = _source_image(output_image, "output_image")
    threshold_values = _thresholds(thresholds, manifest["thresholds"])
    output_hash = _sha256(source)
    evaluation_path = session / "evaluation.json"
    if evaluation_path.is_file():
        try:
            cached = json.loads(evaluation_path.read_text(encoding="utf-8"))
            output_exists = _artifact_exists(cached.get("output_png", ""), session)
            diff_exists = (
                not cached.get("diff_png")
                or _artifact_exists(cached["diff_png"], session)
            )
            if (
                cached.get("output_sha256") == output_hash
                and cached.get("thresholds") == threshold_values
                and output_exists and diff_exists
            ):
                return {"session": str(session), **cached, "cached": True}
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    fmt = manifest["frame"]["pixel_format"]
    output = _load_normalized(source, fmt)
    expected_size = (manifest["frame"]["width"], manifest["frame"]["height"])
    if output.size != expected_size:
        raise VisionError(f"output 尺寸 {output.size} 与 session {expected_size} 不一致")
    output_png = session / "images" / "output.png"
    _save_image(output, output_png)
    evaluation = {
        "status": "review_required", "evaluated": time.time(),
        "output_png": str(output_png), "thresholds": threshold_values,
        "quality": _quality_metrics(output), "output_sha256": output_hash,
    }
    golden_path = Path(manifest["paths"].get("golden_png", "")) if manifest["paths"].get("golden_png") else None
    if golden_path and golden_path.is_file():
        golden = _load_normalized(golden_path, fmt)
        metrics = _comparison(output, golden, threshold_values)
        status, failures = _gate(metrics, threshold_values)
        evaluation.update({"status": status, "metrics": metrics, "failures": failures})
        _, ImageChops, ImageEnhance = _pillow()
        diff = ImageEnhance.Brightness(ImageChops.difference(output, golden)).enhance(4.0)
        diff_png = session / "images" / "diff.png"
        _save_image(diff, diff_png)
        evaluation["diff_png"] = str(diff_png)
    _atomic_json(evaluation_path, evaluation)
    return {"session": str(session), **evaluation}


def record_review(session_dir: str, decision: str, reviewer: str = "agent",
                  note: str = "") -> dict:
    session = _session(session_dir)
    if decision not in {"accept", "reject", "clear"}:
        raise VisionError("decision 必须是 accept/reject/clear")
    if reviewer not in {"agent", "user"}:
        raise VisionError("reviewer 必须是 agent/user")
    if not isinstance(note, str) or len(note) > 2000 or any(ord(ch) < 9 for ch in note):
        raise VisionError("note 必须是不超过 2000 字符的文本")
    path = session / "review.json"
    if decision == "clear":
        path.unlink(missing_ok=True)
        return {"status": "cleared", "session": str(session)}
    review = {
        "decision": decision, "reviewer": reviewer,
        "note": note.strip(), "reviewed": time.time(),
    }
    _atomic_json(path, review)
    return {"status": "reviewed", "session": str(session), "review": review}


def session_status(session_dir: str) -> dict:
    session = _session(session_dir)
    manifest = _read_manifest(session)
    result = {
        "status": "ready", "session": str(session), "platform": manifest["platform"],
        "frame": manifest["frame"], "paths": manifest["paths"],
        "thresholds": manifest["thresholds"], "image_test": manifest["image_test"],
    }
    for name in ("evaluation", "review", "review_server"):
        path = session / f"{name}.json"
        if path.is_file():
            try:
                result[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result[name] = {"status": "unreadable"}
    return result


def _free_port(preferred: int) -> int:
    if isinstance(preferred, bool) or not isinstance(preferred, int) or not 0 <= preferred <= 65535:
        raise VisionError("port 必须是 0..65535 的整数")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _local_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def launch_review_app(session_dir: str, port: int = 8765,
                      open_browser: bool = True) -> dict:
    if not isinstance(open_browser, bool):
        raise VisionError("open_browser 必须是布尔值")
    session = _session(session_dir)
    state_path = session / "review_server.json"
    if state_path.is_file():
        try:
            existing = json.loads(state_path.read_text(encoding="utf-8"))
            existing_port = int(existing.get("port", 0))
            if existing_port and _local_port_open(existing_port):
                return {"session": str(session), **existing, "reused": True}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    selected_port = _free_port(port)
    log_path = session / "review_server.log"
    cmd = [
        sys.executable, "-m", "xilinx_bridge.vision_app",
        "--session", str(session), "--port", str(selected_port),
    ]
    if open_browser:
        cmd.append("--open-browser")
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            cmd, cwd=str(session), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags,
        )
    state = {
        "status": "running", "pid": process.pid, "host": "127.0.0.1",
        "port": selected_port, "url": f"http://127.0.0.1:{selected_port}/",
        "log": str(log_path), "started": time.time(),
    }
    _atomic_json(state_path, state)
    return {"session": str(session), **state}
