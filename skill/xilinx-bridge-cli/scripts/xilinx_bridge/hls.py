"""Vitis HLS 单会话构建、报告解析与质量门禁。"""
from __future__ import annotations

import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from . import tcl_lib


class HlsSpecError(ValueError):
    pass


def missing_host_tools(steps: list[str]) -> list[str]:
    """Cheap native-Linux preflight; no subprocess, network or auto-install."""
    if not sys.platform.startswith("linux"):
        return []
    required = set()
    if any(step in steps for step in ("csim", "cosim")):
        required.add("make")
    if any(step in steps for step in ("cosim", "export")):
        required.add("zip")
    return [tool for tool in sorted(required) if shutil.which(tool) is None]


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ROLES = {"source", "testbench"}
_STEPS = ("csim", "csynth", "cosim", "export")
_BUDGET_ALIASES = {
    "max_ii": "ii_max", "max_latency": "latency_max",
    "max_bram": "bram_18k_max", "max_bram_18k": "bram_18k_max",
    "max_dsp": "dsp_max", "max_ff": "ff_max", "max_lut": "lut_max",
}


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise HlsSpecError(f"hls.{label} 不是合法标识符: {value}")
    return value


def _target(workspace: Path, rel_path: str) -> Path:
    rel = Path(rel_path)
    if rel.is_absolute():
        raise HlsSpecError(f"hls.files.rel_path 必须是工作区相对路径: {rel_path}")
    target = (workspace / rel).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise HlsSpecError(f"hls.files.rel_path 不能越出工作区: {rel_path}") from exc
    return target


def _source_path(workspace: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise HlsSpecError(f"{label} 必须是路径字符串")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if not path.is_file():
        raise HlsSpecError(f"{label} 不存在: {raw}")
    return path


def _normalize_steps(raw: object) -> list[str]:
    if raw is None:
        raw = ["csim", "csynth"]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise HlsSpecError("hls.steps 必须是非空数组")
    requested = []
    for step in raw:
        if step not in _STEPS:
            raise HlsSpecError(f"hls.steps 不支持 {step}；可选 {_STEPS}")
        if step not in requested:
            requested.append(step)
    if any(step in requested for step in ("cosim", "export")) and "csynth" not in requested:
        requested.append("csynth")
    return [step for step in _STEPS if step in requested]


def _normalize_budgets(raw: object, dataflow: bool) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HlsSpecError("hls.budgets 必须是对象")
    budgets: dict = {}
    allowed = {"ii_max", "latency_max", "bram_18k_max", "dsp_max", "ff_max", "lut_max", "require_dataflow"}
    for key, value in raw.items():
        key = _BUDGET_ALIASES.get(key, key)
        if key not in allowed:
            raise HlsSpecError(f"hls.budgets 含未知键: {key}")
        if key == "require_dataflow":
            if not isinstance(value, bool):
                raise HlsSpecError("hls.budgets.require_dataflow 必须是布尔值")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise HlsSpecError(f"hls.budgets.{key} 必须是非负数")
        budgets[key] = value
    if dataflow:
        budgets.setdefault("require_dataflow", True)
    return budgets


def prepare(workspace_raw: str, spec: dict) -> tuple[str, dict]:
    """写入可选源码并生成把多个阶段融合到同一 HLS 进程的 Tcl。"""
    if not isinstance(spec, dict):
        raise HlsSpecError("hls 必须是对象")
    workspace = Path(workspace_raw).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    name = _identifier(spec.get("name", "hls_component"), "name")
    top = _identifier(spec.get("top"), "top")
    solution = _identifier(spec.get("solution", "solution1"), "solution")
    part = spec.get("part")
    if not isinstance(part, str) or not part.strip():
        raise HlsSpecError("hls.part 不能为空")
    clock_ns = spec.get("clock_ns", 10.0)
    if isinstance(clock_ns, bool) or not isinstance(clock_ns, (int, float)) or not 0.1 <= clock_ns <= 10_000:
        raise HlsSpecError("hls.clock_ns 必须在 0.1..10000 之间")
    dataflow = spec.get("dataflow", False)
    if not isinstance(dataflow, bool):
        raise HlsSpecError("hls.dataflow 必须是布尔值")
    pipeline_ii = spec.get("pipeline_ii")
    if pipeline_ii is not None and (isinstance(pipeline_ii, bool) or not isinstance(pipeline_ii, int) or not 1 <= pipeline_ii <= 65535):
        raise HlsSpecError("hls.pipeline_ii 必须是 1..65535 的整数")
    if dataflow and pipeline_ii is not None:
        raise HlsSpecError("dataflow 与顶层 pipeline_ii 不能同时设置；请在各 process loop 内 PIPELINE")
    steps = _normalize_steps(spec.get("steps"))
    export_format = spec.get("export_format", "ip_catalog")
    if export_format not in ("ip_catalog", "xo"):
        raise HlsSpecError("hls.export_format 必须是 ip_catalog 或 xo")
    flow_target = "vitis" if export_format == "xo" else "vivado"
    budgets = _normalize_budgets(spec.get("budgets"), dataflow)

    sources: list[Path] = []
    testbench: list[Path] = []
    written: list[str] = []
    files = spec.get("files", [])
    if not isinstance(files, (list, tuple)):
        raise HlsSpecError("hls.files 必须是数组")
    for index, item in enumerate(files):
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise HlsSpecError(f"hls.files[{index}] 必须含 rel_path 与字符串 content")
        role = item.get("role", "source")
        if role not in _ROLES:
            raise HlsSpecError(f"hls.files[{index}].role 必须是 source/testbench")
        target = _target(workspace, item.get("rel_path", ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
        written.append(str(target))
        (sources if role == "source" else testbench).append(target)
    for index, raw in enumerate(spec.get("sources", [])):
        sources.append(_source_path(workspace, raw, f"hls.sources[{index}]"))
    for index, raw in enumerate(spec.get("testbench", [])):
        testbench.append(_source_path(workspace, raw, f"hls.testbench[{index}]"))
    sources = list(dict.fromkeys(sources))
    testbench = list(dict.fromkeys(testbench))
    if not sources:
        raise HlsSpecError("hls 至少需要一个 source")
    if any(step in steps for step in ("csim", "cosim")) and not testbench:
        raise HlsSpecError("csim/cosim 至少需要一个 testbench")
    source_has_dataflow = any(
        re.search(r"#\s*pragma\s+HLS\s+DATAFLOW\b", path.read_text(encoding="utf-8", errors="replace"), re.I)
        for path in sources
    )

    include_dirs: list[Path] = []
    for index, raw in enumerate(spec.get("include_dirs", [])):
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = workspace / path
        path = path.resolve()
        if not path.is_dir():
            raise HlsSpecError(f"hls.include_dirs[{index}] 不存在: {raw}")
        include_dirs.append(path)
    cflags = " ".join(f'-I"{str(path).replace(chr(92), "/")}"' for path in include_dirs)
    project_dir = (workspace / ".xab" / "hls" / name).resolve()
    project_parent = project_dir.parent

    lines = [
        f"file mkdir {tcl_lib.q(str(project_parent))}",
        f"cd {tcl_lib.q(str(project_parent))}",
        f"open_project -reset {tcl_lib.q(name)}",
        f"set_top {tcl_lib.q(top)}",
    ]
    for path in sources:
        suffix = f" -cflags {tcl_lib.q(cflags)}" if cflags else ""
        lines.append(f"add_files{suffix} {tcl_lib.q(str(path))}")
    for path in testbench:
        suffix = f" -cflags {tcl_lib.q(cflags)}" if cflags else ""
        lines.append(f"add_files -tb{suffix} {tcl_lib.q(str(path))}")
    lines.extend([
        f"open_solution -reset {tcl_lib.q(solution)} -flow_target {flow_target}",
        f"set_part {tcl_lib.q(part)}",
        f"create_clock -period {clock_ns} -name default",
    ])
    if dataflow and not source_has_dataflow:
        lines.append(f"set_directive_dataflow {tcl_lib.q(top)}")
    if pipeline_ii is not None:
        lines.append(f"set_directive_pipeline -II {pipeline_ii} {tcl_lib.q(top)}")
    for step in steps:
        command = {"csim": "csim_design -clean", "csynth": "csynth_design",
                   "cosim": "cosim_design -rtl verilog",
                   "export": f"export_design -format {export_format}"}[step]
        lines.extend([f'puts "WB_HLS_STEP|{step}|start"', command,
                      f'puts "WB_HLS_STEP|{step}|done"'])
    lines.extend(['puts "WB_PASS|vitis_hls|completed"', "exit"])
    meta = {
        "workspace": str(workspace), "hls_project_dir": str(project_dir),
        "name": name, "top": top, "solution": solution, "part": part,
        "clock_ns": float(clock_ns), "steps": steps, "written": written,
        "export_format": export_format, "flow_target": flow_target,
        "source_count": len(sources), "testbench_count": len(testbench),
        "dataflow_requested": dataflow, "pipeline_ii": pipeline_ii,
        "dataflow_source_pragma": source_has_dataflow,
        "budgets": budgets,
    }
    return "\n".join(lines) + "\n", meta


def _tag(root: ET.Element, name: str) -> str | None:
    wanted = name.casefold().replace("-", "").replace("_", "")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold().replace("-", "").replace("_", "")
        if tag == wanted and element.text and element.text.strip():
            return element.text.strip()
    return None


def _number(raw: str | None) -> int | float | None:
    if raw is None or raw.upper() in {"NA", "N/A", "?"}:
        return None
    try:
        value = float(raw)
        return int(value) if value.is_integer() else value
    except ValueError:
        return None


def parse_csynth_xml(text: str) -> dict:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    resources = {
        "bram_18k": _number(_tag(root, "BRAM_18K")),
        "dsp": _number(_tag(root, "DSP")),
        "ff": _number(_tag(root, "FF")),
        "lut": _number(_tag(root, "LUT")),
        "uram": _number(_tag(root, "URAM")),
    }
    pipeline_iis = []
    dataflow = False
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        value = (element.text or "").strip()
        if tag == "pipelineii":
            number = _number(value)
            if isinstance(number, (int, float)) and number > 0:
                pipeline_iis.append(number)
        if tag == "pipelinetype" and value.casefold() == "dataflow":
            dataflow = True
        if tag == "pragma" and element.attrib.get("type", "").casefold() == "dataflow" and element.attrib.get("status", "").casefold() == "implemented":
            dataflow = True
    return {
        "target_clock_ns": _number(_tag(root, "TargetClockPeriod")),
        "estimated_clock_ns": _number(_tag(root, "EstimatedClockPeriod")),
        "latency_min": _number(_tag(root, "Best-caseLatency")),
        "latency_max": _number(_tag(root, "Worst-caseLatency")),
        "interval_min": _number(_tag(root, "Interval-min")),
        "interval_max": _number(_tag(root, "Interval-max")),
        "pipeline_ii_max": max(pipeline_iis) if pipeline_iis else None,
        "pipeline_ii_values": sorted(set(pipeline_iis)),
        "dataflow_implemented": dataflow,
        "resources": {k: v for k, v in resources.items() if v is not None},
    }


def _parse_cosim(text: str) -> dict:
    fallback = None
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 9 and cells[0].casefold() in {"verilog", "vhdl"}:
            def value(index: int):
                return _number(cells[index]) if index < len(cells) else None
            result = {
                "rtl": cells[0], "verdict": cells[1].casefold(),
                "latency_min": value(2), "latency_avg": value(3), "latency_max": value(4),
                "interval_min": value(5), "interval_avg": value(6), "interval_max": value(7),
                "execution_cycles": value(8),
            }
            if result["verdict"] == "pass":
                return result
            fallback = result
    return fallback or {"verdict": "unknown"}


def _quality_gate(metrics: dict, budgets: dict, dataflow_verified: bool,
                  steps: list[str], cosim: dict, deadlock_risks: list[str]) -> dict:
    checks: list[dict] = []
    mapping = {
        "ii_max": ("pipeline_ii_max", "pipeline II"), "latency_max": ("latency_max", "latency"),
        "bram_18k_max": ("bram_18k", "BRAM_18K"), "dsp_max": ("dsp", "DSP"),
        "ff_max": ("ff", "FF"), "lut_max": ("lut", "LUT"),
    }
    resources = metrics.get("resources", {})
    if "csynth" in steps:
        estimated, target = metrics.get("estimated_clock_ns"), metrics.get("target_clock_ns")
        checks.append({"metric": "estimated clock", "actual": estimated, "limit": target,
                       "pass": estimated is not None and target is not None and estimated <= target})
        checks.append({"metric": "stream deadlock risk", "actual": len(deadlock_risks),
                       "limit": 0, "pass": not deadlock_risks})
    for budget_key, (metric_key, label) in mapping.items():
        if budget_key not in budgets:
            continue
        actual = metrics.get(metric_key) if metric_key in metrics else resources.get(metric_key)
        if budget_key == "ii_max" and actual is None:
            actual = metrics.get("interval_max")
        limit = budgets[budget_key]
        checks.append({"metric": label, "actual": actual, "limit": limit,
                       "pass": actual is not None and actual <= limit})
    if budgets.get("require_dataflow"):
        checks.append({"metric": "DATAFLOW", "actual": dataflow_verified,
                       "required": True, "pass": dataflow_verified})
    if "cosim" in steps:
        checks.append({"metric": "RTL cosim", "actual": cosim.get("verdict"),
                       "required": "pass", "pass": cosim.get("verdict") == "pass"})
    return {"pass": all(check["pass"] for check in checks), "checks": checks}


def summarize(meta: dict, log_text: str) -> dict:
    project_dir = Path(meta.get("hls_project_dir", "."))
    xml_files = sorted(project_dir.glob("**/syn/report/csynth.xml"),
                       key=lambda p: p.stat().st_mtime, reverse=True) if project_dir.is_dir() else []
    metrics: dict = {}
    if xml_files:
        metrics = parse_csynth_xml(xml_files[0].read_text(encoding="utf-8", errors="replace"))
        metrics["report"] = str(xml_files[0])
    report_text = log_text
    report_files = sorted(project_dir.glob("**/syn/report/*_csynth.rpt")) if project_dir.is_dir() else []
    for path in report_files:
        report_text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    dataflow_verified = bool(metrics.get("dataflow_implemented")) or bool(re.search(
        r"(dataflow[^\n]*(enabled|yes|pipeline)|applying[^\n]*dataflow|set_directive_dataflow)",
        report_text, re.I,
    ))
    metrics["dataflow_verified"] = dataflow_verified
    cosim_files = sorted(project_dir.glob("*/sim/report/*_cosim.rpt")) if project_dir.is_dir() else []
    cosim = {"verdict": "not_requested"}
    if cosim_files:
        cosim = _parse_cosim(cosim_files[-1].read_text(encoding="utf-8", errors="replace"))
        cosim["report"] = str(cosim_files[-1])
    steps = meta.get("steps", [])
    deadlock_risks = [
        line.strip()[:500] for line in log_text.splitlines()
        if "[HLS 200-805]" in line or re.search(r"WARNING:.*dataflow.*deadlock", line, re.I)
    ]
    gate = _quality_gate(metrics, meta.get("budgets", {}), dataflow_verified,
                         steps, cosim, deadlock_risks)
    artifacts = []
    if project_dir.is_dir():
        for pattern in ("*/impl/export.zip", "*/impl/ip/*.zip", "**/*.xo", "*/sim/report/*_cosim.rpt"):
            artifacts.extend(str(path) for path in project_dir.glob(pattern) if path.is_file())
    if "export" in steps:
        suffix = ".xo" if meta.get("export_format") == "xo" else ".zip"
        exported = any(Path(path).suffix == suffix and Path(path).stat().st_size > 0
                       for path in artifacts)
        gate["checks"].append({"metric": "export artifact", "actual": exported,
                               "required": True, "pass": exported})
        gate["pass"] = gate["pass"] and exported
    return {
        "top": meta.get("top"), "steps": steps,
        "export_format": meta.get("export_format", "ip_catalog"),
        "deployment": "link_required" if meta.get("export_format") == "xo" else "platform_integration_required",
        "metrics": metrics, "cosim": cosim, "risks": deadlock_risks,
        "quality_gate": gate,
        "artifacts": list(dict.fromkeys(artifacts)),
    }
