"""Token-bounded external-video preparation for AXI4-Stream Video simulation."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import axis_video


class VideoError(ValueError):
    pass


SCHEMA_VERSION = 1
MAX_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
MAX_SIM_PIXELS = 262_144
MAX_EXPLICIT_SIM_PIXELS = 16_777_216
DEFAULT_MAX_WIDTH = 160
DEFAULT_MAX_HEIGHT = 120
RESIZE_MODES = {"stretch", "fit", "pad", "crop"}
_HEX_COLOR = re.compile(r"(?:#|0x)?([0-9A-Fa-f]{6})\Z")


def validate_detail(value: str) -> str:
    if value not in {"compact", "standard"}:
        raise VideoError("detail 必须是 compact 或 standard")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _source_video(path: str, label: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise VideoError(f"{label} 必须是非空路径")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise VideoError(f"{label} 不存在: {source}")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise VideoError(f"无法读取 {label}: {source}") from exc
    if not 1 <= size <= MAX_VIDEO_BYTES:
        raise VideoError(f"{label} 文件大小必须在 1..{MAX_VIDEO_BYTES} 字节")
    return source


def _session(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise VideoError("session_dir 必须是非空路径")
    session = Path(path).expanduser().resolve()
    session.mkdir(parents=True, exist_ok=True)
    return session


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise VideoError(
            f"找不到 {name}；请安装 FFmpeg 并确保 ffmpeg/ffprobe 在 PATH 中"
        )
    return str(Path(executable).resolve())


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoError(f"视频工具执行失败: {type(exc).__name__}: {exc}") from exc


def _fraction(value: object) -> float:
    text = str(value or "0")
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            return float(numerator) / float(denominator) if float(denominator) else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _probe(source: Path, ffprobe: str) -> dict:
    result = _run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames:format=duration",
        "-of", "json", str(source),
    ], timeout=30)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")[-1200:].strip()
        raise VideoError(f"ffprobe 无法读取视频: {error or 'unknown error'}")
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
        stream = payload["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoError("输入文件没有可用的视频流或元数据不完整") from exc
    if not 1 <= width <= 32768 or not 1 <= height <= 32768:
        raise VideoError(f"视频尺寸超出支持范围: {width}x{height}")
    fps = _fraction(stream.get("avg_frame_rate")) or _fraction(stream.get("r_frame_rate"))
    duration = _fraction(payload.get("format", {}).get("duration"))
    try:
        declared_frames = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        declared_frames = 0
    return {
        "width": width, "height": height,
        "fps": round(fps, 6), "duration_seconds": round(duration, 6),
        "declared_frames": declared_frames,
    }


def _positive_number(value: object, label: str, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VideoError(f"{label} 必须是数字")
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > maximum:
        raise VideoError(f"{label} 必须在 0..{maximum} 之间")
    return number


def _normalized_pad_color(value: str) -> str:
    if not isinstance(value, str):
        raise VideoError("pad_color 必须是 black/white/gray 或 RRGGBB")
    name = value.strip().lower()
    if name in {"black", "white", "gray"}:
        return name
    match = _HEX_COLOR.fullmatch(name)
    if not match:
        raise VideoError("pad_color 必须是 black/white/gray 或 6位十六进制颜色")
    return "0x" + match.group(1).upper()


def _resize_geometry(probe: dict, target_width: int, target_height: int,
                     resize_mode: str, pad_color: str) -> dict:
    for value, label in ((target_width, "target_width"), (target_height, "target_height")):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4096:
            raise VideoError(f"{label} 必须在 0..4096 之间")
    mode = str(resize_mode).strip().lower()
    if mode not in RESIZE_MODES:
        raise VideoError("resize_mode 必须是 stretch/fit/pad/crop")
    # An irrelevant pad color must not invalidate/redecode fit, crop, or stretch sessions.
    color = _normalized_pad_color(pad_color) if mode == "pad" else ""
    source_width, source_height = probe["width"], probe["height"]
    explicit_box = bool(target_width or target_height)
    if target_width and not target_height:
        target_height = max(1, round(source_height * target_width / source_width))
    elif target_height and not target_width:
        target_width = max(1, round(source_width * target_height / source_height))
    elif not target_width and not target_height:
        target_width, target_height = DEFAULT_MAX_WIDTH, DEFAULT_MAX_HEIGHT
    if target_width > 4096 or target_height > 4096:
        raise VideoError(
            "按源宽高比推导后的 target_width/target_height 超过 4096；请显式给出两维"
        )

    if mode == "stretch":
        if explicit_box:
            width, height = target_width, target_height
        else:
            scale = min(
                1.0, target_width / source_width, target_height / source_height
            )
            width = max(1, round(source_width * scale))
            height = max(1, round(source_height * scale))
    elif mode == "fit":
        scale = min(target_width / source_width, target_height / source_height)
        if not explicit_box:
            scale = min(1.0, scale)
        width = max(1, min(target_width, round(source_width * scale)))
        height = max(1, min(target_height, round(source_height * scale)))
    else:
        width, height = target_width, target_height
    return {
        "mode": mode, "target_width": target_width,
        "target_height": target_height, "width": width, "height": height,
        "pad_color": color,
    }


def _decoder_version(ffmpeg: str) -> str:
    result = _run([ffmpeg, "-version"], timeout=10)
    line = result.stdout.decode("utf-8", errors="replace").splitlines()
    return line[0][:240] if line else "unknown"


def _filter_chain(width: int, height: int, resize_mode: str,
                  pad_color: str, sample_fps: float) -> str:
    if resize_mode in {"stretch", "fit"}:
        resize = f"scale={width}:{height}:flags=area"
    elif resize_mode == "pad":
        resize = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=area,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"
        )
    else:
        resize = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=area,"
            f"crop={width}:{height}"
        )
    return f"fps=fps={sample_fps:.9g},{resize},setsar=1"


def _decode_raw(source: Path, ffmpeg: str, *, width: int, height: int,
                pixel_format: str, sample_fps: float, max_frames: int,
                start_time: float, resize_mode: str,
                pad_color: str) -> tuple[bytes, str]:
    output_format = "gray" if pixel_format == "gray" else "rgb24"
    filters = _filter_chain(
        width, height, resize_mode, pad_color, sample_fps
    )
    command = [ffmpeg, "-v", "error"]
    if start_time:
        command.extend(["-ss", f"{start_time:.9g}"])
    command.extend([
        "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", filters, "-frames:v", str(max_frames),
        "-pix_fmt", output_format, "-f", "rawvideo", "pipe:1",
    ])
    result = _run(command, timeout=180)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")[-1600:].strip()
        raise VideoError(f"ffmpeg 解码失败: {error or 'unknown error'}")
    channels = 1 if pixel_format == "gray" else 3
    frame_bytes = width * height * channels
    if not result.stdout or len(result.stdout) % frame_bytes:
        raise VideoError(
            f"ffmpeg 输出长度异常: {len(result.stdout)} 字节，单帧应为 {frame_bytes} 字节"
        )
    frames = len(result.stdout) // frame_bytes
    if frames > max_frames:
        raise VideoError("ffmpeg 返回帧数超过 max_frames")
    return result.stdout, result.stderr.decode("utf-8", errors="replace")[-800:]


def _row_pixels(raw: bytes, pixel_format: str) -> list[object]:
    if pixel_format == "gray":
        return list(raw)
    return [[raw[index], raw[index + 1], raw[index + 2]]
            for index in range(0, len(raw), 3)]


def _write_axis_clip(base: Path, raw: bytes, *, width: int, height: int,
                     frames: int, pixel_format: str,
                     pixels_per_clock: int) -> tuple[Path, Path, dict]:
    mem_path, bin_path = base.with_suffix(".mem"), base.with_suffix(".bin")
    channels = 1 if pixel_format == "gray" else 3
    row_bytes = width * channels
    frame_bytes = row_bytes * height
    packed_meta: dict | None = None
    beats = 0
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    with mem_path.open("w", encoding="ascii", newline="\n") as mem_stream, \
            bin_path.open("wb") as bin_stream:
        for frame in range(frames):
            frame_offset = frame * frame_bytes
            for row in range(height):
                offset = frame_offset + row * row_bytes
                packed = axis_video.pack_frame(
                    _row_pixels(raw[offset:offset + row_bytes], pixel_format),
                    width, 1, pixel_format, 8, pixels_per_clock,
                )
                if packed_meta is None:
                    packed_meta = {key: value for key, value in packed.items()
                                   if key != "beats"}
                digits = (packed["data_width"] + 3) // 4
                for value in packed["beats"]:
                    mem_stream.write(f"{value:0{digits}x}\n")
                    bin_stream.write(value.to_bytes(packed["beat_bytes"], "little"))
                    beats += 1
    assert packed_meta is not None
    packed_meta["beats"] = beats
    return mem_path, bin_path, packed_meta


def _save_preview(path: Path, raw: bytes, width: int, height: int,
                  pixel_format: str) -> None:
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise VideoError(
            "视频预览需要 Pillow；执行 pip install -e '.[vision]' 或 pip install Pillow"
        ) from exc
    mode = "L" if pixel_format == "gray" else "RGB"
    channels = 1 if pixel_format == "gray" else 3
    image = Image.frombytes(mode, (width, height), raw[:width * height * channels])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def _artifact(path: str, session: Path) -> bool:
    try:
        candidate = Path(path).resolve()
        return candidate.is_relative_to(session) and candidate.is_file()
    except (OSError, ValueError):
        return False


def _project(manifest: dict, detail: str, reused: bool = False) -> dict:
    if validate_detail(detail) == "standard":
        result = dict(manifest)
        result["status"] = "ready"
        result["reused"] = reused
        return result
    result = {
        "status": "ready", "session": manifest["session_dir"],
        "reused": reused,
        "source": manifest["source"], "clip": manifest["clip"],
        "expected_mode": manifest["expected_mode"],
        "image_test": manifest["image_test"],
        "board_input_bin": manifest["paths"]["input_bin"],
        "preview": manifest["paths"]["input_preview"],
        "efficiency": manifest["efficiency"],
    }
    return result


def prepare_session(session_dir: str, input_video: str, golden_video: str = "",
                    pixel_format: str = "rgb", pixels_per_clock: int = 1,
                    sample_fps: float = 1.0, max_frames: int = 8,
                    start_time: float = 0.0, target_width: int = 0,
                    target_height: int = 0, resize_mode: str = "stretch",
                    pad_color: str = "black", overwrite: bool = False,
                    detail: str = "compact",
                    simulation_pixel_budget: int = MAX_SIM_PIXELS) -> dict:
    """Decode a bounded clip once and return a path-only multi-frame image_test."""
    validate_detail(detail)
    if not isinstance(overwrite, bool):
        raise VideoError("overwrite 必须是布尔值")
    fmt = str(pixel_format).lower().replace("gray8", "gray").replace("rgb888", "rgb")
    if fmt not in {"gray", "rgb"}:
        raise VideoError("pixel_format 当前支持 gray/rgb")
    if pixels_per_clock not in {1, 2, 4, 8} or isinstance(pixels_per_clock, bool):
        raise VideoError("pixels_per_clock 必须是 1/2/4/8")
    fps = _positive_number(sample_fps, "sample_fps", maximum=1000.0)
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 256:
        raise VideoError("max_frames 必须在 1..256 之间")
    if (isinstance(start_time, bool) or not isinstance(start_time, (int, float)) or
            not math.isfinite(float(start_time)) or not 0 <= float(start_time) <= 86400):
        raise VideoError("start_time 必须在 0..86400 秒之间")
    if (isinstance(simulation_pixel_budget, bool) or
            not isinstance(simulation_pixel_budget, int) or
            not 1 <= simulation_pixel_budget <= MAX_EXPLICIT_SIM_PIXELS):
        raise VideoError(
            f"simulation_pixel_budget 必须在 1..{MAX_EXPLICIT_SIM_PIXELS} 之间"
        )

    source = _source_video(input_video, "input_video")
    golden_source = _source_video(golden_video, "golden_video") if golden_video else None
    session = _session(session_dir)
    manifest_path = session / "video_session.json"
    ffmpeg, ffprobe = _tool("ffmpeg"), _tool("ffprobe")
    source_probe = _probe(source, ffprobe)
    resize = _resize_geometry(
        source_probe, target_width, target_height, resize_mode, pad_color
    )
    width, height = resize["width"], resize["height"]
    if width * height * max_frames > simulation_pixel_budget:
        allowed = simulation_pixel_budget // (width * height)
        raise VideoError(
            f"请求最多 {max_frames} 帧共 {width * height * max_frames} 像素，"
            f"超过 simulation_pixel_budget={simulation_pixel_budget}；"
            f"该尺寸最多 {allowed} 帧。确认资源后可显式提高预算"
        )
    decoder_version = _decoder_version(ffmpeg)
    fingerprint = {
        "input_sha256": _sha256(source),
        "golden_sha256": _sha256(golden_source) if golden_source else "",
        "pixel_format": fmt, "pixels_per_clock": pixels_per_clock,
        "sample_fps": fps, "max_frames": max_frames,
        "start_time": float(start_time), "width": width, "height": height,
        "resize_mode": resize["mode"],
        "target_width": resize["target_width"],
        "target_height": resize["target_height"],
        "pad_color": resize["pad_color"],
        "simulation_pixel_budget": simulation_pixel_budget,
        "decoder_version": decoder_version,
    }
    if manifest_path.is_file() and not overwrite:
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VideoError(f"video session manifest 无法读取: {manifest_path}") from exc
        if existing.get("fingerprint") != fingerprint:
            raise VideoError(
                f"video session 已存在且输入或参数变化，拒绝覆盖: {session}；"
                "确认后传 overwrite=true"
            )
        required = [existing.get("paths", {}).get(key, "") for key in (
            "input_mem", "input_bin", "input_preview",
        )]
        if golden_source:
            required.extend(existing.get("paths", {}).get(key, "") for key in (
                "golden_mem", "golden_bin", "golden_preview",
            ))
        if not all(_artifact(path, session) for path in required):
            raise VideoError("video session 指纹匹配但产物不完整；传 overwrite=true 重建")
        return _project(existing, detail, reused=True)

    raw, decoder_log = _decode_raw(
        source, ffmpeg, width=width, height=height, pixel_format=fmt,
        sample_fps=fps, max_frames=max_frames, start_time=float(start_time),
        resize_mode=resize["mode"], pad_color=resize["pad_color"],
    )
    channels = 1 if fmt == "gray" else 3
    frame_bytes = width * height * channels
    frames = len(raw) // frame_bytes
    if frames < 1:
        raise VideoError("选定时间窗没有解码出视频帧")
    golden_raw = None
    if golden_source:
        if fingerprint["golden_sha256"] == fingerprint["input_sha256"]:
            golden_raw = raw
        else:
            golden_raw, _ = _decode_raw(
                golden_source, ffmpeg, width=width, height=height, pixel_format=fmt,
                sample_fps=fps, max_frames=max_frames, start_time=float(start_time),
                resize_mode=resize["mode"], pad_color=resize["pad_color"],
            )
        golden_frames = len(golden_raw) // frame_bytes
        if golden_frames != frames:
            raise VideoError(
                f"golden_video 抽取 {golden_frames} 帧，input_video 抽取 {frames} 帧；"
                "请统一时长或调整 start_time/sample_fps/max_frames"
            )

    frame_dir, preview_dir = session / "frames", session / "previews"
    input_mem, input_bin, packed = _write_axis_clip(
        frame_dir / "input", raw, width=width, height=height, frames=frames,
        pixel_format=fmt, pixels_per_clock=pixels_per_clock,
    )
    input_preview = preview_dir / "input_first.png"
    _save_preview(input_preview, raw, width, height, fmt)
    if golden_raw is not None:
        golden_mem, golden_bin, _ = _write_axis_clip(
            frame_dir / "golden", golden_raw, width=width, height=height,
            frames=frames, pixel_format=fmt, pixels_per_clock=pixels_per_clock,
        )
        golden_preview = preview_dir / "golden_first.png"
        _save_preview(golden_preview, golden_raw, width, height, fmt)
        expected_mem, expected_mode = golden_mem, "golden_video"
    else:
        golden_mem = golden_bin = None
        golden_preview = None
        expected_mem, expected_mode = input_mem, "passthrough"
        for stale in (frame_dir / "golden.mem", frame_dir / "golden.bin",
                      preview_dir / "golden_first.png"):
            stale.unlink(missing_ok=True)

    image_test = {
        "width": width, "height": height, "frames": frames,
        "output_width": width, "output_height": height, "output_frames": frames,
        "pixel_format": fmt, "component_bits": 8,
        "pixels_per_clock": pixels_per_clock,
        "input_mem": str(input_mem), "expected_mem": str(expected_mem),
        "pixel_budget": simulation_pixel_budget,
        "inter_frame_gap": 2, "backpressure": True,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION, "created": time.time(),
        "status": "ready", "session_dir": str(session),
        "source": source_probe,
        "clip": {
            "width": width, "height": height, "frames": frames,
            "sample_fps": fps, "start_time": float(start_time),
            "resize_mode": resize["mode"],
            "target_width": resize["target_width"],
            "target_height": resize["target_height"],
            "simulation_pixel_budget": simulation_pixel_budget,
            "pixel_format": fmt, "pixels_per_clock": pixels_per_clock,
            "beats_per_line": packed["beats_per_line"],
            "beats": packed["beats"], "beat_bytes": packed["beat_bytes"],
            "data_width": packed["data_width"],
        },
        "resize": resize,
        "expected_mode": expected_mode, "image_test": image_test,
        "paths": {
            "input_video": str(source), "golden_video": str(golden_source or ""),
            "input_mem": str(input_mem), "input_bin": str(input_bin),
            "input_preview": str(input_preview),
            "golden_mem": str(golden_mem or ""), "golden_bin": str(golden_bin or ""),
            "golden_preview": str(golden_preview or ""),
        },
        "decoder": {
            "ffmpeg": ffmpeg, "ffprobe": ffprobe,
            "version": decoder_version, "log_tail": decoder_log,
        },
        "fingerprint": fingerprint,
        "efficiency": {
            "path_only": True,
            "pixels_kept_out_of_agent_context": width * height * frames,
            "vivado_simulation_launches_required": 1,
            "large_simulation_budget": simulation_pixel_budget > MAX_SIM_PIXELS,
            "simulation_pixel_budget": simulation_pixel_budget,
            "raw_decode_byte_cap": simulation_pixel_budget * channels,
            "golden_decode_reused": bool(
                golden_source and fingerprint["golden_sha256"] == fingerprint["input_sha256"]
            ),
        },
    }
    _atomic_json(manifest_path, manifest)
    return _project(manifest, detail)
