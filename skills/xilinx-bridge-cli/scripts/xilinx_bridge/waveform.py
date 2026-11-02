"""XSim VCD 捕获配置、紧凑解析与无依赖 SVG/CSV 预览。"""
from __future__ import annotations

import csv
import fnmatch
import html
import re
from decimal import Decimal
from pathlib import Path


class WaveformSpecError(ValueError):
    pass


VCD_PLACEHOLDER = "__XAB_WAVEFORM_VCD__"
_TIME_VALUE = re.compile(r"^\d+(?:\.\d+)?\s*(?:fs|ps|ns|us|ms|s)$", re.I)
_TIME_SCALE_FS = {
    "fs": Decimal(1), "ps": Decimal(1_000), "ns": Decimal(1_000_000),
    "us": Decimal(1_000_000_000), "ms": Decimal(1_000_000_000_000),
    "s": Decimal(1_000_000_000_000_000),
}


def normalize(spec: dict | None, tb_top: str = "") -> dict | None:
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise WaveformSpecError("waveform 必须是对象")
    raw_signals = spec.get("signals")
    if raw_signals is None:
        raw_signals = [f"/{tb_top}/*"] if tb_top else ["/*"]
    if (not isinstance(raw_signals, (list, tuple)) or not raw_signals or
            len(raw_signals) > 128):
        raise WaveformSpecError("waveform.signals 必须是 1..128 项的层次路径数组")
    signals: list[str] = []
    for index, value in enumerate(raw_signals):
        if (not isinstance(value, str) or not value.strip() or len(value) > 512 or
                any(ch in value for ch in "\r\n;")):
            raise WaveformSpecError(f"waveform.signals[{index}] 不是安全的 xsim 层次路径")
        signals.append(value.strip())
    # Defaults are intentionally small: enough to diagnose a pipeline without
    # turning every simulation response into thousands of tokens.
    max_signals = spec.get("max_signals", 8)
    max_transitions = spec.get("max_transitions", 16)
    if isinstance(max_signals, bool) or not isinstance(max_signals, int) or not 1 <= max_signals <= 128:
        raise WaveformSpecError("waveform.max_signals 必须在 1..128 之间")
    if (isinstance(max_transitions, bool) or not isinstance(max_transitions, int) or
            not 2 <= max_transitions <= 2000):
        raise WaveformSpecError("waveform.max_transitions 必须在 2..2000 之间")
    preview = spec.get("preview", True)
    if not isinstance(preview, bool):
        raise WaveformSpecError("waveform.preview 必须是布尔值")
    raw_radix = spec.get("radix", {})
    if not isinstance(raw_radix, dict) or len(raw_radix) > 128:
        raise WaveformSpecError("waveform.radix 必须是 路径:bin/hex/unsigned/signed 对象")
    radix: dict[str, str] = {}
    for path, mode in raw_radix.items():
        if not isinstance(path, str) or mode not in {"bin", "hex", "unsigned", "signed"}:
            raise WaveformSpecError("waveform.radix 必须是 路径:bin/hex/unsigned/signed 对象")
        radix[path] = mode
    capture_start = spec.get("start", "")
    capture_duration = spec.get("duration", "")
    for name, value in (("start", capture_start), ("duration", capture_duration)):
        if not isinstance(value, str) or (value and not _TIME_VALUE.fullmatch(value.strip())):
            raise WaveformSpecError(
                f"waveform.{name} 必须为空或带单位的时间，如 100ns / 2.5us"
            )
    return {
        "requested_signals": signals,
        "max_signals": max_signals,
        "max_transitions": max_transitions,
        "preview": preview,
        "radix": radix,
        "capture_start": capture_start.strip(),
        "capture_duration": capture_duration.strip(),
    }


def capture_tcl(config: dict, runtime: str) -> str:
    patterns = " ".join(_tcl_quote(pattern) for pattern in config["requested_signals"])
    run_value = config.get("capture_duration") or (runtime.strip() if runtime else "all")
    run_command = "run all" if run_value.casefold() == "all" else f"run {_tcl_quote(run_value)}"
    start_value = config.get("capture_start", "")
    resume_command = _resume_command(runtime, start_value, config.get("capture_duration", ""))
    resume = ""
    if resume_command:
        resume = f"""if {{[catch {{{resume_command}}} wb_wave_msg]}} {{
    wb_emit "WB_WAVE_WARN|resume_after_capture|$wb_wave_msg"
}}"""
    pre_run = ""
    if start_value:
        pre_run = f"""if {{[catch {{run {_tcl_quote(start_value)}}} wb_wave_msg]}} {{
    wb_err "waveform_preroll: $wb_wave_msg"
    exit 1
}}"""
    return f"""
{pre_run}
if {{[catch {{open_vcd \"{VCD_PLACEHOLDER}\"}} wb_wave_msg]}} {{
    wb_err "open_vcd: $wb_wave_msg"
    exit 1
}}
set wb_wave_objects 0
foreach wb_wave_pattern [list {patterns}] {{
    if {{[catch {{set wb_wave_matches [get_objects -r $wb_wave_pattern]}} wb_wave_msg]}} {{
        wb_emit "WB_WAVE_WARN|pattern=$wb_wave_pattern|$wb_wave_msg"
        continue
    }}
    if {{[llength $wb_wave_matches] > 0}} {{
        log_vcd $wb_wave_matches
        incr wb_wave_objects [llength $wb_wave_matches]
    }} else {{
        wb_emit "WB_WAVE_WARN|pattern=$wb_wave_pattern|no_match"
    }}
}}
if {{$wb_wave_objects == 0}} {{
    wb_err "waveform_no_matching_signals"
    catch {{close_vcd}}
    exit 1
}}
wb_emit "WB_WAVE|logged_objects=$wb_wave_objects"
if {{[catch {{{run_command}}} wb_wave_msg]}} {{
    wb_err "waveform_run: $wb_wave_msg"
    catch {{close_vcd}}
    exit 1
}}
close_vcd
wb_emit "WB_ARTIFACT|vcd|{VCD_PLACEHOLDER}"
{resume}
"""


def _time_parts(value: str) -> tuple[Decimal, str]:
    match = _TIME_VALUE.fullmatch(value.strip())
    if not match:
        raise WaveformSpecError(f"无效时间值: {value}")
    compact = value.strip().lower().replace(" ", "")
    unit = next(unit for unit in _TIME_SCALE_FS if compact.endswith(unit))
    return Decimal(compact[:-len(unit)]), unit


def _resume_command(runtime: str, start: str, duration: str) -> str:
    """Return the post-capture run command needed to honor total runtime.

    Capturing a short VCD window must not silently truncate an otherwise full
    regression.  Numeric runtimes are treated as an absolute horizon from t=0.
    """
    if not duration:
        return ""
    total = runtime.strip() if runtime else "all"
    if total.casefold() == "all":
        return "run all"
    total_value, total_unit = _time_parts(total)
    duration_value, duration_unit = _time_parts(duration)
    elapsed_fs = duration_value * _TIME_SCALE_FS[duration_unit]
    if start:
        start_value, start_unit = _time_parts(start)
        elapsed_fs += start_value * _TIME_SCALE_FS[start_unit]
    remaining = (total_value * _TIME_SCALE_FS[total_unit] - elapsed_fs) / _TIME_SCALE_FS[total_unit]
    if remaining < 0:
        raise WaveformSpecError("waveform.start + duration 不能超过仿真 runtime")
    if remaining == 0:
        return ""
    remaining_text = format(remaining.normalize(), "f")
    return f"run {_tcl_quote(remaining_text + total_unit)}"


def _tcl_quote(value: str) -> str:
    value = value.replace("\\", "/")
    for char in ('"', "$", "[", "]"):
        value = value.replace(char, "\\" + char)
    return f'"{value}"'


def _parse_timescale(line: str) -> str:
    match = re.search(r"\$timescale\s+(.+?)\s+\$end", line)
    return _normalize_timescale(" ".join(match.group(1).split())) if match else "unknown"


def _normalize_timescale(value: str) -> str:
    compact = value.replace(" ", "")
    match = re.fullmatch(r"(\d+)([fpnum]?s)", compact, re.I)
    return f"{match.group(1)} {match.group(2)}" if match else value or "unknown"


def parse_vcd(path: Path, config: dict) -> dict:
    if not path.is_file():
        return {"status": "missing", "vcd": str(path), "message": "VCD 未生成"}
    scopes: list[str] = []
    declarations: list[dict] = []
    timescale = "unknown"
    timescale_parts: list[str] = []
    in_timescale = False
    header_done = False
    current_time = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            line = raw.strip()
            if not header_done:
                if in_timescale:
                    if line.startswith("$end"):
                        timescale = _normalize_timescale(" ".join(timescale_parts))
                        in_timescale = False
                    else:
                        timescale_parts.append(line)
                elif line == "$timescale":
                    in_timescale = True
                elif line.startswith("$timescale"):
                    timescale = _parse_timescale(line)
                elif line.startswith("$scope"):
                    parts = line.split()
                    if len(parts) >= 3:
                        scopes.append(parts[2])
                elif line.startswith("$upscope"):
                    if scopes:
                        scopes.pop()
                elif line.startswith("$var"):
                    parts = line.split()
                    if len(parts) >= 6:
                        try:
                            width = int(parts[2])
                        except ValueError:
                            width = 1
                        reference = parts[4]
                        bit_range = " ".join(parts[5:-1])
                        declarations.append({
                            "code": parts[3], "width": width,
                            "name": "/" + "/".join([*scopes, reference]),
                            **({"range": bit_range} if bit_range else {}),
                        })
                elif line.startswith("$enddefinitions"):
                    header_done = True
                    break
        selected = _select_declarations(declarations, config)
        by_code: dict[str, list[dict]] = {}
        for signal in selected:
            signal["changes"] = []
            signal["transition_count"] = 0
            by_code.setdefault(signal["code"], []).append(signal)
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                try:
                    current_time = int(line[1:])
                except ValueError:
                    pass
                continue
            code = ""
            value = ""
            if line[0] in "01xXzZ":
                value, code = line[0].lower(), line[1:]
            elif line[0] in "bBrR":
                parts = line.split(None, 1)
                if len(parts) == 2:
                    value, code = parts[0][1:].lower(), parts[1]
            if code not in by_code:
                continue
            for signal in by_code[code]:
                signal["transition_count"] += 1
                changes = signal["changes"]
                shown = _format_value(value, signal["width"], signal.get("radix", "bin"))
                if len(changes) < config["max_transitions"]:
                    if changes and changes[-1][1] == shown:
                        continue
                    changes.append([current_time, shown])
    for signal in selected:
        signal.pop("code", None)
        signal["truncated"] = signal["transition_count"] > len(signal["changes"])
    return {
        "status": "ready", "vcd": str(path), "timescale": timescale,
        "end_time": current_time, "declared_signals": len(declarations),
        "returned_signals": len(selected), "signals": selected,
        "truncated": len(declarations) > len(selected) or any(s["truncated"] for s in selected),
    }


def _select_declarations(declarations: list[dict], config: dict) -> list[dict]:
    patterns = config["requested_signals"]
    selected = [
        dict(item) for item in declarations
        if any(fnmatch.fnmatchcase(item["name"], pattern) for pattern in patterns)
    ]
    if not selected:
        selected = [dict(item) for item in declarations]
    seen = set()
    unique = []
    for item in selected:
        item["radix"] = next(
            (mode for pattern, mode in config.get("radix", {}).items()
             if fnmatch.fnmatchcase(item["name"], pattern)),
            "bin",
        )
        key = item["name"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
        if len(unique) >= config["max_signals"]:
            break
    return unique


def _format_value(value: str, width: int, mode: str) -> str:
    if any(char in value for char in "xz") or not value:
        return value
    try:
        number = int(value, 2)
    except ValueError:
        return value
    if mode == "unsigned":
        return str(number)
    if mode == "signed":
        if width > 0 and number & (1 << (width - 1)):
            number -= 1 << width
        return str(number)
    if mode == "hex":
        return f"0x{number:0{max(1, (width + 3) // 4)}x}"
    return value


def write_csv(summary: dict, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["signal", "width", "radix", "time", "value"])
        for signal in summary.get("signals", []):
            for timestamp, value in signal["changes"]:
                writer.writerow([signal["name"], signal["width"], signal.get("radix", "bin"),
                                 timestamp, value])


def write_svg(summary: dict, path: Path) -> None:
    signals = summary.get("signals", [])[:16]
    end_time = max(1, int(summary.get("end_time") or 1))
    left, width, row_h = 260, 1120, 54
    height = 46 + row_h * len(signals)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{left + width + 30}" height="{height}" viewBox="0 0 {left + width + 30} {height}">',
        '<rect width="100%" height="100%" fill="#10151d"/>',
        '<style>text{font-family:Consolas,monospace;font-size:12px;fill:#dce6f2}.grid{stroke:#344050;stroke-width:1}.wave{stroke:#55d6be;stroke-width:2;fill:none}.bus{stroke:#72a7ff;stroke-width:2}.value{fill:#ffd166;font-size:11px}</style>',
        f'<text x="12" y="22">XSim VCD · timescale {html.escape(str(summary.get("timescale")))}</text>',
    ]
    for tick in range(11):
        x = left + width * tick / 10
        timestamp = int(end_time * tick / 10)
        lines.append(f'<line class="grid" x1="{x:.1f}" y1="32" x2="{x:.1f}" y2="{height - 10}"/>')
        lines.append(f'<text x="{x + 2:.1f}" y="42">{timestamp}</text>')
    for row, signal in enumerate(signals):
        y = 52 + row * row_h
        name = html.escape(signal["name"][-38:])
        lines.append(f'<text x="8" y="{y + 22}">{name}</text>')
        lines.append(f'<line class="grid" x1="{left}" y1="{y + 27}" x2="{left + width}" y2="{y + 27}"/>')
        changes = signal.get("changes", [])
        if signal.get("width") == 1:
            points = []
            for index, (timestamp, value) in enumerate(changes):
                x = left + width * timestamp / end_time
                level = y + (8 if value == "1" else 34 if value == "0" else 21)
                if index and points:
                    points.append((x, points[-1][1]))
                points.append((x, level))
            if points:
                points.append((left + width, points[-1][1]))
                data = " ".join(f"{x:.1f},{py:.1f}" for x, py in points)
                lines.append(f'<polyline class="wave" points="{data}"/>')
        else:
            lines.append(f'<line class="bus" x1="{left}" y1="{y + 21}" x2="{left + width}" y2="{y + 21}"/>')
            for timestamp, value in changes[:12]:
                x = left + width * timestamp / end_time
                label = value if len(value) <= 12 else value[:9] + "..."
                lines.append(f'<line class="bus" x1="{x:.1f}" y1="{y + 12}" x2="{x:.1f}" y2="{y + 30}"/>')
                lines.append(f'<text class="value" x="{x + 3:.1f}" y="{y + 17}">{html.escape(label)}</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def summarize(vcd_path: Path, config: dict, output_dir: Path) -> dict:
    summary = parse_vcd(vcd_path, config)
    if summary.get("status") != "ready":
        return summary
    csv_path = output_dir / "waveform_transitions.csv"
    write_csv(summary, csv_path)
    summary["csv"] = str(csv_path)
    if config.get("capture_start"):
        summary["capture_start"] = config["capture_start"]
    if config.get("capture_duration"):
        summary["capture_duration"] = config["capture_duration"]
    if config.get("preview"):
        svg_path = output_dir / "waveform_preview.svg"
        write_svg(summary, svg_path)
        summary["preview_svg"] = str(svg_path)
    return summary
