"""核心操作层：所有 Vivado/Vitis 能力都在这里实现，server.py (MCP) 与 cli.py 只是封装。"""
from __future__ import annotations
import os

import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

from . import axis_video, config, env_detect, hls as hls_flow, kv260, parsers, tcl_lib, video, vision, vitis_ai, vitis_audit, vitis_py
from . import waveform as waveform_tools
from . import simulation_diagnostics
from .jobs import MANAGER, ResourceBusyError, tail

RPT = "__RPT_DIR__"      # 报告目录占位符, 提交前替换为 job 目录(raw)
RUN_DIR = "__RUN_DIR__"  # impl_1 run 目录占位符(raw)


class BridgeError(RuntimeError):
    pass


_FILESETS = {"sources_1", "constrs_1", "sim_1"}
_SIM_MODES = {
    "behavioral", "post_synth_func", "post_synth_timing",
    "post_impl_func", "post_impl_timing",
}


def _require_xpr(xpr: str) -> Path:
    path = Path(xpr).expanduser()
    if not path.is_file() or path.suffix.lower() != ".xpr":
        raise BridgeError(f"Vivado 工程文件不存在或不是 .xpr: {xpr}")
    return path.resolve()


def _validate_fileset(fileset: str) -> str:
    if fileset not in _FILESETS:
        raise BridgeError(f"不支持的 fileset: {fileset}；可选值: {sorted(_FILESETS)}")
    return fileset


def _bounded_int(value: int, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BridgeError(f"{label} 必须是 {minimum}..{maximum} 的整数")
    return value


def _count_ila_csv_samples(lines: list[str]) -> int:
    """Count data rows, excluding Vivado's header/radix metadata rows."""
    return sum(1 for line in lines[1:] if re.match(r"^\s*\d+\s*,", line))


def _project_target(xpr: str, rel_path: str) -> Path:
    """只允许 write_source 写入工程目录，防止绝对路径和 ../ 越界。"""
    return _relative_target(_require_xpr(xpr).parent, rel_path)


def _relative_target(project_dir: Path, rel_path: str) -> Path:
    """解析工程内相对路径，并拒绝绝对路径、父目录穿越与链接越界。"""
    project_dir = project_dir.resolve()
    rel = Path(rel_path)
    if rel.is_absolute():
        raise BridgeError(f"rel_path 必须是工程内相对路径: {rel_path}")
    target = (project_dir / rel).resolve()
    try:
        target.relative_to(project_dir)
    except ValueError as exc:
        raise BridgeError(f"rel_path 不能越出工程目录: {rel_path}") from exc
    return target


def _prepare_source_batch(
    project_dir: Path, items: list[dict], *, allow_overwrite: bool = True
) -> tuple[str, str, list[str], list[str]]:
    """Prepare content-backed or path-backed sources in one Vivado batch.

    Content item: {rel_path, content, fileset?, top?}
    Path item:    {path, fileset?, top?}
    """
    if not items:
        raise BridgeError("items 不能为空")
    prepared: list[tuple[dict, Path, str, bool, bool]] = []
    tops: dict[str, str] = {}
    for it in items:
        if not isinstance(it, dict):
            raise BridgeError("每个 item 必须是对象")
        content_mode = "rel_path" in it or "content" in it
        path_mode = "path" in it
        if content_mode == path_mode:
            raise BridgeError("每个 item 必须二选一: rel_path+content，或现有文件 path")
        if content_mode:
            if "rel_path" not in it or "content" not in it:
                raise BridgeError("内容 item 必须同时包含 rel_path 与 content")
            if not isinstance(it["content"], str):
                raise BridgeError(f"content 必须是字符串: {it['rel_path']}")
            target = _relative_target(project_dir, it["rel_path"])
            if target.exists() and not allow_overwrite:
                raise BridgeError(
                    f"目标源码已存在，拒绝静默覆盖: {target}；确认后传 overwrite=true"
                )
            write_content = True
        else:
            if not isinstance(it["path"], str) or not it["path"].strip():
                raise BridgeError("path 必须是现有文件路径字符串")
            target = Path(it["path"]).expanduser().resolve()
            if not target.is_file():
                raise BridgeError(f"引用源文件不存在: {it['path']}")
            write_content = False
        fs = _validate_fileset(it.get("fileset", "sources_1"))
        add_to_project = it.get("add_to_project", True)
        if not isinstance(add_to_project, bool):
            raise BridgeError("add_to_project 必须是布尔值")
        if path_mode and not add_to_project:
            raise BridgeError("path 引用 item 必须加入工程")
        top_name = it.get("top", "")
        if top_name and not add_to_project:
            raise BridgeError("未加入工程的文件不能设置 top")
        if top_name:
            if fs == "constrs_1":
                raise BridgeError("constrs_1 不能设置 top")
            if fs in tops and tops[fs] != top_name:
                raise BridgeError(f"同一 fileset 存在冲突的 top: {fs}")
            tops[fs] = top_name
        prepared.append((it, target, fs, add_to_project, write_content))

    add_lines: list[str] = []
    written: list[str] = []
    referenced: list[str] = []
    for it, target, fs, add_to_project, write_content in prepared:
        if write_content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(it["content"], encoding="utf-8")
            written.append(str(target))
        else:
            referenced.append(str(target))
        if not add_to_project:
            continue
        target_q = tcl_lib.q(str(target))
        if fs == "sources_1":
            add_command = f"add_files -norecurse {target_q}"
        else:
            add_command = f"add_files -fileset {fs} -norecurse {target_q}"
        add_lines.append(
            f"if {{[llength [get_files -quiet {target_q}]] == 0}} {{ {add_command} }}"
        )
    top_lines = "\n".join(
        f"set_property top {tcl_lib.q(top_name)} [get_filesets {tcl_lib.q(fs)}]"
        for fs, top_name in tops.items()
    ) or "# top unchanged"
    return "\n".join(add_lines), top_lines, written, referenced


def _append_image_test(project_dir: Path, items: list[dict] | None,
                       image_test: dict | None) -> tuple[list[dict], dict | None]:
    combined = list(items or [])
    if image_test is None:
        return combined, None
    try:
        generated, meta = axis_video.prepare_items(project_dir, image_test)
    except axis_video.AxisVideoSpecError as exc:
        raise BridgeError(str(exc)) from exc
    combined.extend(generated)
    return combined, meta


def kv260_vision_prepare(session_dir: str, input_image: str, golden_image: str = "",
                         pixel_format: str = "rgb", pixels_per_clock: int = 1,
                         thresholds: dict | None = None,
                         overwrite: bool = False,
                         detail: str = "compact") -> dict:
    """Create one token-bounded KV260 frame/test/review session."""
    try:
        prepared = vision.prepare_session(
            session_dir, input_image, golden_image, pixel_format,
            pixels_per_clock, thresholds, overwrite,
        )
        return vision.project_prepare(prepared, detail)
    except vision.VisionError as exc:
        raise BridgeError(str(exc)) from exc


def vivado_video_prepare(session_dir: str, input_video: str,
                         golden_video: str = "", pixel_format: str = "rgb",
                         pixels_per_clock: int = 1, sample_fps: float = 1.0,
                         max_frames: int = 8, start_time: float = 0.0,
                         target_width: int = 0, target_height: int = 0,
                         resize_mode: str = "stretch",
                         pad_color: str = "black",
                         overwrite: bool = False,
                         detail: str = "compact",
                         simulation_pixel_budget: int = 262_144) -> dict:
    """Decode one bounded video clip into a reusable multi-frame image_test."""
    try:
        return video.prepare_session(
            session_dir, input_video, golden_video, pixel_format,
            pixels_per_clock, sample_fps, max_frames, start_time,
            target_width, target_height, resize_mode, pad_color,
            overwrite, detail,
            simulation_pixel_budget=simulation_pixel_budget,
        )
    except video.VideoError as exc:
        raise BridgeError(str(exc)) from exc


def kv260_vision_evaluate(session_dir: str, output_image: str = "",
                          thresholds: dict | None = None, decision: str = "",
                          reviewer: str = "agent", note: str = "",
                          launch_ui: bool = False, port: int = 8765,
                          open_browser: bool = True,
                          detail: str = "compact") -> dict:
    """Evaluate, review, inspect, and optionally open the local companion UI."""
    try:
        vision.validate_detail(detail)
        result: dict = {"session": str(Path(session_dir).expanduser().resolve())}
        if output_image:
            evaluation = vision.evaluate_session(
                session_dir, output_image, thresholds
            )
            if detail == "standard":
                result["evaluation"] = evaluation
            else:
                projected = vision.project_evaluation(evaluation)
                result.update(projected)
        elif thresholds is not None:
            raise vision.VisionError("仅更新 thresholds 没有意义；请同时传 output_image")
        if decision:
            review = vision.record_review(
                session_dir, decision, reviewer, note
            )
            result["review"] = review if detail == "standard" else vision.project_review(review)
        elif note:
            raise vision.VisionError("note 需要与 decision 一起传入")
        if launch_ui:
            ui = vision.launch_review_app(
                session_dir, port, open_browser
            )
            result["ui"] = ui if detail == "standard" else vision.project_ui(ui)
        if len(result) == 1:
            return vision.project_status(vision.session_status(session_dir), detail)
        if "status" not in result:
            result["status"] = "updated"
        return result
    except vision.VisionError as exc:
        raise BridgeError(str(exc)) from exc


def kv260_board_flow(host: str = "", session_dir: str = "", action: str = "inspect",
                     user: str = "ubuntu", port: int = 22,
                     ssh_key: str = "", app_name: str = "",
                     load_app: bool = False, unload_active: bool = False,
                     host_executable: str = "", xclbin: str = "",
                     run_args: list[str] | None = None,
                     output_name: str = "output.bin",
                     output_layout: str = "axis_packed",
                     stride_bytes: int = 0, timeout: int = 120,
                     detail: str = "compact", backend: str = "board",
                     model: str = "", dpu_input: str = "",
                     dpu_threads: int = 1, benchmark_seconds: int = 0,
                     repeat: int = 1, capture_camera: bool = False) -> dict:
    """Inspect or execute one fused KV260 Linux/XRT board workflow."""
    try:
        if action == "host_inspect":
            result = kv260.board_flow(
                host, session_dir, action, user, port, ssh_key, app_name,
                load_app, unload_active, host_executable, xclbin, run_args,
                output_name, output_layout, stride_bytes, timeout, detail,
                backend=backend, model=model, dpu_input=dpu_input,
                dpu_threads=dpu_threads, benchmark_seconds=benchmark_seconds,
                repeat=repeat, capture_camera=capture_camera,
            )
            try:
                scan = hw_list(wait=True, timeout=min(timeout, 180))
                summary = scan.get("summary") or {}
                markers = summary.get("markers") or {}
                devices = []
                for raw in markers.get("DEVICE", [])[:16]:
                    fields = {}
                    for token in raw.split("|"):
                        key, separator, value = token.partition("=")
                        if separator:
                            fields[key] = value
                    devices.append({
                        "index": int(fields["index"]) if fields.get("index", "").isdigit() else None,
                        "name": fields.get("name", "")[:128],
                        "part": fields.get("part", "")[:128],
                        "programmable": fields.get("programmable") == "1",
                    })
                k26 = next((
                    item for item in devices
                    if item["part"].casefold().startswith("xck26") and item["programmable"]
                ), None)
                jtag_status = "ready" if scan.get("status") == "success" and k26 else "incomplete"
                result["jtag"] = {
                    "status": jtag_status,
                    "targets": len(markers.get("TARGET", [])),
                    "devices": devices,
                    "xck26": k26,
                }
                if detail == "standard":
                    result["jtag"]["job_id"] = scan.get("id")
                if jtag_status != "ready":
                    result["failures"].append("xck26_jtag_not_detected")
            except BridgeError as exc:
                result["jtag"] = {"status": "error", "message": str(exc)[:500]}
                result["failures"].append("jtag_scan_failed")
            result["status"] = "ready" if not result["failures"] else "incomplete"
            return result
        if action == "inspect":
            return kv260.board_flow(
                host, session_dir, action, user, port, ssh_key, app_name,
                load_app, unload_active, host_executable, xclbin, run_args,
                output_name, output_layout, stride_bytes, timeout, detail,
                backend=backend, model=model, dpu_input=dpu_input,
                dpu_threads=dpu_threads, benchmark_seconds=benchmark_seconds,
                repeat=repeat, capture_camera=capture_camera,
            )
        resource = (
            kv260.resource_key(host, port) if backend == "board"
            else kv260.offline_resource_key(session_dir)
        )
        with MANAGER.reserve(resource, "kv260"):
            return kv260.board_flow(
                host, session_dir, action, user, port, ssh_key, app_name,
                load_app, unload_active, host_executable, xclbin, run_args,
                output_name, output_layout, stride_bytes, timeout, detail,
                backend=backend, model=model, dpu_input=dpu_input,
                dpu_threads=dpu_threads, benchmark_seconds=benchmark_seconds,
                repeat=repeat, capture_camera=capture_camera,
            )
    except (kv260.Kv260Error, ResourceBusyError) as exc:
        raise BridgeError(str(exc)) from exc


def vitis_ai_flow(action: str = "inspect", work_dir: str = "", model: str = "",
                  script: str = "", args: list[str] | None = None,
                  version: str = "auto", network_name: str = "xab_model",
                  compiler_options: list[str] | None = None,
                  timeout: int = 600, force: bool = False,
                  detail: str = "compact", distro: str = "Ubuntu",
                  refresh: bool = False,
                  cache_inputs: list[str] | None = None,
                  wait: bool = True, arch_json: str = "") -> dict:
    """Inspect or run one fused, token-bounded Vitis AI workflow."""
    try:
        request = vitis_ai.validate_request(
            action, work_dir, model, script, args, version, network_name,
            compiler_options, timeout, force, detail, distro, refresh,
            cache_inputs, arch_json,
        )
        if not isinstance(wait, bool):
            raise vitis_ai.VitisAIError("wait 必须是布尔值")
        if wait:
            return vitis_ai.flow(**request)
        if action not in {"quantize", "compile", "pipeline", "smoke"}:
            raise vitis_ai.VitisAIError(
                "wait=false 仅用于 quantize、compile、pipeline 或 smoke 长任务"
            )
        jdir = MANAGER.alloc_dir("vitis_ai")
        request_path = jdir / "request.json"
        result_path = jdir / "result.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        if work_dir:
            target = work_dir
        elif action == "compile":
            target = f"{model}|xab_vitis_ai|{network_name}"
        else:
            target = str(config.CONFIG_DIR / "vitis_ai" / action)
        resource_digest = hashlib.sha256(
            target.replace("\\", "/").casefold().encode("utf-8")
        ).hexdigest()[:24]
        job = MANAGER.submit(
            "vitis_ai",
            [sys.executable, "-m", "xilinx_bridge.vitis_ai_worker",
             str(request_path), str(result_path)],
            jdir, jdir=jdir,
            meta={
                "vitis_ai_result": str(result_path),
                "completion_result": str(result_path),
                "vitis_ai_action": action,
            },
            resource_key=f"lock:vitis_ai:{resource_digest}",
        )
        # A short publication barrier prevents one-shot CLI callers from
        # exiting before the daemon launcher thread has created the durable
        # worker process. MCP servers normally stay alive, but CLI parity is
        # useful for reproducible validation and crash recovery.
        deadline = time.time() + 2.0
        while job.get("status") == "running" and not job.get("pid") and time.time() < deadline:
            time.sleep(0.01)
            job = MANAGER.get(job["id"]) or job
        if job.get("status") != "running" and not job.get("summary"):
            job["summary"] = summarize_job(job)
            MANAGER.set_summary(job["id"], job["summary"])
        return public_job(job)
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    except vitis_ai.VitisAIError as exc:
        raise BridgeError(str(exc)) from exc


def _source_setup_tcl(add_lines: str, top_lines: str) -> str:
    return (
        f"{add_lines}\n{top_lines}\n"
        "update_compile_order -fileset sources_1\n"
        "update_compile_order -fileset sim_1\n"
        'wb_ok "added"'
    )


def _simulation_steps(tb_top: str, runtime: str, sim_mode: str,
                      waveform: dict | None = None) -> str:
    if sim_mode not in _SIM_MODES:
        raise BridgeError(f"不支持的 sim_mode: {sim_mode}；可选值: {sorted(_SIM_MODES)}")
    if waveform is None:
        runtime_setup = (
            f"set_property -name {{xsim.simulate.runtime}} -value {tcl_lib.q(runtime)} -objects $simset"
            if runtime else "# keep project simulation runtime"
        )
        waveform_steps = "# no VCD capture"
    else:
        runtime_setup = "set_property -name {xsim.simulate.runtime} -value {0ns} -objects $simset"
        try:
            waveform_steps = waveform_tools.capture_tcl(waveform, runtime)
        except waveform_tools.WaveformSpecError as exc:
            raise BridgeError(str(exc)) from exc
    return tcl_lib.render(
        tcl_lib.SIMULATION_STEPS,
        TOP=tcl_lib.q(tb_top) if tb_top else '""',
        SIM_MODE=sim_mode,
        RUNTIME_SETUP=runtime_setup,
        WAVEFORM_STEPS=waveform_steps,
    )


# ---------------------------------------------------------------------------
# 基础执行
# ---------------------------------------------------------------------------
def _require_tool(name: str) -> Path:
    p = env_detect.resolve_tool(name)
    if p is None:
        raise BridgeError(
            f"找不到 {name}。请先用 xilinx_config_set 指定 Vivado/Vitis 安装路径，"
            f"例如 C:/Xilinx/Vivado/2026.1"
        )
    return p


def run_vivado_tcl(tcl_text: str, kind: str, wait: bool = True,
                   timeout: int = 7200, meta: dict | None = None,
                   jdir: Path | None = None, resource_key: str = "") -> dict:
    try:
        vivado, launch_env, launcher_kind = env_detect.resolve_vivado_invocation()
    except ValueError as exc:
        raise BridgeError(str(exc)) from exc
    if vivado is None:
        _require_tool("vivado")  # raises the shared actionable error
        raise BridgeError("找不到 vivado")
    if jdir is None:
        jdir = MANAGER.alloc_dir(kind)
    if waveform_tools.VCD_PLACEHOLDER in tcl_text:
        vcd_path = jdir / "waveform.vcd"
        tcl_text = tcl_text.replace(
            waveform_tools.VCD_PLACEHOLDER, tcl_lib.qraw(str(vcd_path))
        )
        if isinstance(meta, dict) and isinstance(meta.get("waveform"), dict):
            meta["waveform"]["vcd"] = str(vcd_path)
    tcl_path = tcl_lib.write_script(jdir, tcl_text)
    cmd = [str(vivado)]
    if launcher_kind == "unwrapped":
        cmd.extend(("-product", "Vivado"))
    cmd.extend([
        "-mode", "batch", "-notrace",
        "-source", str(tcl_path),
        "-log", str(jdir / "vivado.log"),
        "-journal", str(jdir / "vivado.jou"),
    ])
    if isinstance(meta, dict):
        meta["vivado_launcher"] = launcher_kind
    project_key = str(meta.get("xpr", "")) if isinstance(meta, dict) else ""
    lock_key = resource_key or project_key
    try:
        job = MANAGER.submit(
            kind, cmd, jdir, env=launch_env, meta=meta, tcl_text=tcl_text, jdir=jdir,
            resource_key=lock_key,
        )
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    if wait:
        job = wait_job(job["id"], timeout)
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job["id"], job["summary"])
    return public_job(job)


def run_vitis_python(script_text: str, kind: str, wait: bool = True,
                     timeout: int = 3600, meta: dict | None = None,
                     resource_key: str = "") -> dict:
    vitis = _require_tool("vitis")
    jdir = MANAGER.alloc_dir(kind)
    spath = jdir / "run.py"
    spath.write_text(script_text, encoding="utf-8")
    cmd = [str(vitis), "-s", str(spath)]
    try:
        job = MANAGER.submit(
            kind, cmd, jdir, meta=meta, jdir=jdir, resource_key=resource_key,
        )
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    if wait:
        job = wait_job(job["id"], timeout)
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job["id"], job["summary"])
    return public_job(job)


def run_hls_tcl(tcl_text: str, workspace: Path, meta: dict,
                wait: bool = True, timeout: int = 3600) -> dict:
    """用一趟 HLS Tcl 会话执行所选阶段，避免 csim/csynth 各自冷启动。"""
    missing = hls_flow.missing_host_tools(meta.get("steps", []))
    if missing:
        raise BridgeError(
            "hls_host_dependencies_missing: " + ", ".join(missing)
            + "；请在运行 HLS 的 Linux 环境补齐这些命令后重试。尚未启动 HLS。"
        )
    launcher = env_detect.resolve_tool("vitis_run") or env_detect.resolve_tool("vitis_hls")
    if launcher is None:
        raise BridgeError(
            "找不到 vitis-run/vitis_hls。请先用 xilinx_config_set 指定 Vitis 安装路径"
        )
    jdir = MANAGER.alloc_dir("vitis_hls")
    spath = tcl_lib.write_script(jdir, tcl_text)
    if launcher.stem.casefold().startswith("vitis-run"):
        cmd = [str(launcher), "--mode", "hls", "--tcl", str(spath)]
    else:
        cmd = [str(launcher), "-f", str(spath)]
    try:
        job = MANAGER.submit(
            "vitis_hls", cmd, workspace, meta={"hls": meta},
            tcl_text=tcl_text, jdir=jdir, resource_key=str(workspace),
        )
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    if wait:
        job = wait_job(job["id"], timeout)
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job["id"], job["summary"])
    return public_job(job)


def wait_job(job_id: str, timeout: int = 7200) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = MANAGER.get(job_id)
        if job and job["status"] != "running":
            return job
        if job:
            reason = parsers.detect_infrastructure_failure(tail(job.get("log", ""), 200))
            if reason:
                MANAGER.fail(job_id)
                failed = MANAGER.get(job_id) or job
                failed["infrastructure_failure"] = reason
                return failed
        time.sleep(2)
    MANAGER.timeout(job_id)
    return MANAGER.get(job_id) or {"id": job_id, "status": "timeout"}


def public_job(job: dict, *, include_paths: bool = False) -> dict:
    out = {k: job.get(k) for k in ("id", "kind", "status", "returncode", "started", "ended")}
    for diagnostic in ("scheduler", "process_tree", "termination_requested"):
        if diagnostic in job:
            out[diagnostic] = job[diagnostic]
    if include_paths or out["status"] in {"failed", "timeout", "interrupted"}:
        out["log"] = job.get("log")
        out["dir"] = job.get("dir")
    if job.get("summary") is not None:
        out["summary"] = job["summary"]
    if out["status"] == "running":
        out["hint"] = "后台运行中；用 xilinx_job_status(job_id) 轮询。"
        out["poll_after_seconds"] = (
            10 if out["kind"] in {"synthesize", "implement", "bitstream", "vitis_ai"} else 3
        )
    return out


def _summary_has_failure(job: dict, summary: dict) -> bool:
    markers = summary.get("markers") or {}
    return (
        job.get("status") != "success"
        or summary.get("error_count", 0) > 0
        or bool(summary.get("failure_signature"))
        or bool(markers.get("ERROR"))
        or summary.get("simulation", {}).get("verdict") in ("fail", "unknown")
        or summary.get("simulation_compile", {}).get("issue_count", 0) > 0
        or summary.get("hls", {}).get("quality_gate", {}).get("pass") is False
        or summary.get("drc", {}).get("bitstream_ready") is False
        or summary.get("waveform", {}).get("status") == "missing"
        or summary.get("timing", {}).get("timing_met") is False
        or summary.get("route_status", {}).get("fully_routed") is False
        or summary.get("vitis_ai", {}).get("status") == "error"
    )


def _failure_next_actions(summary: dict) -> list[str]:
    """Return at most three evidence-based hints, only for failed gates."""
    actions: list[str] = []
    signature = summary.get("failure_signature") or {}
    # failure_signature.action is already public; do not duplicate the same
    # sentence in next_actions. Reserve this list for independent failed gates.
    if not signature and summary.get("infrastructure_failure"):
        actions.append("先修复宿主权限/进程启动问题，再重跑；当前不是 HDL 质量结论。")
    verdict = summary.get("simulation", {}).get("verdict")
    compile_issues = summary.get('simulation_compile', {}).get('issues') or []
    if compile_issues:
        actions.append(compile_issues[0]['suggested_check'])
    elif verdict == "fail":
        actions.append("先看 WB_FAIL/首个 mismatch；已有 waveform 时只检查相关 signed/hex transitions。")
    elif verdict == "unknown":
        actions.append("testbench 必须打印 WB_PASS/WB_FAIL；只有 $finish 不能判定通过。")
    timing = summary.get("timing", {})
    if timing.get("timing_met") is False:
        design = timing.get("design", {})
        setup_bad = design.get("WNS(ns)", 0) < 0 or design.get("TNS(ns)", 0) < 0
        hold_bad = design.get("WHS(ns)", 0) < 0 or design.get("THS(ns)", 0) < 0
        failed = "setup/hold" if setup_bad and hold_bad else "setup" if setup_bad else "hold/pulse-width"
        actions.append(f"时序门禁失败（{failed}）；优先查看最差路径并增加流水或修正真实约束。")
    if summary.get("route_status", {}).get("fully_routed") is False:
        actions.append("路由未完成；检查 route_status 的 routing_error_nets 与拥塞/约束报告。")
    if summary.get("drc", {}).get("bitstream_ready") is False:
        rules = "/".join(summary["drc"].get("blocking_rules", [])[:3]) or "Error"
        actions.append(f"DRC 阻止 bitstream（{rules}）；修正器件/引脚/时钟约束，禁止仅降低严重级别。")
    if summary.get("hls", {}).get("quality_gate", {}).get("pass") is False:
        actions.append("按 summary.hls.quality_gate 的失败项修复 II/时钟/资源/cosim，不要只看 csynth 成功。")
    if summary.get("waveform", {}).get("status") == "missing":
        actions.append("VCD 未生成；确认层次路径匹配且仿真确实运行。")
    return actions[:3]


def _compact_job_summary(summary: dict | None) -> dict | None:
    """Project a completed summary to verdicts and gates for low-token polling."""
    if not isinstance(summary, dict):
        return summary
    keep_scalar = (
        "status", "returncode", "error_count", "critical_warning_count",
        "warning_count", "infrastructure_failure", "failure_signature",
        "partial_success", "artifacts_recovered", "workspace_audit", "next_actions",
    )
    out = {key: summary[key] for key in keep_scalar if key in summary}
    if "simulation" in summary:
        sim = summary["simulation"]
        out["simulation"] = {
            key: sim[key] for key in
            ("verdict", "error_count", "errors", "simulation_finished") if key in sim
        }
    if 'simulation_compile' in summary:
        diagnostic = summary['simulation_compile']
        out['simulation_compile'] = {key: diagnostic[key] for key in
            ('status', 'issue_count', 'issues_truncated', 'execution_verified') if key in diagnostic}
        out['simulation_compile']['stages'] = {name: {key: value[key] for key in
            ('cache_state', 'cache_reason', 'incremental_requested', 'activity', 'reuse_scope') if key in value}
            for name, value in diagnostic.get('stages', {}).items()
            if value.get('activity') or value.get('cache_state') != 'unknown'}
        issues = diagnostic.get('issues') or []
        out['simulation_compile']['issues'] = [{key: issue[key] for key in
            ('category', 'code', 'message', 'suggested_check', 'source_location') if key in issue}
            for issue in issues[:2]]
        out['simulation_compile']['issues_truncated'] = (
            diagnostic.get('issues_truncated', False) or len(issues) > 2)
        coverage = diagnostic.get('coverage') or {}
        out['simulation_compile']['coverage'] = {key: coverage[key] for key in
            ('complete', 'sampled', 'bytes_read', 'files_truncated') if key in coverage}
    if "timing" in summary:
        timing = summary["timing"]
        out["timing"] = {
            "timing_met": timing.get("timing_met"),
            "design": timing.get("design", {}),
        }
    for key in ("utilization", "drc", "route_status", "artifacts", "vitis_ai"):
        if key in summary:
            out[key] = summary[key]
    if "waveform" in summary:
        wave = summary["waveform"]
        out["waveform"] = {
            key: wave[key] for key in (
                "status", "timescale", "end_time", "returned_signals",
                "truncated", "capture_start", "capture_duration",
                "csv", "preview_svg", "vcd",
            ) if key in wave
        }
    if "hls" in summary:
        hls = summary["hls"]
        out["hls"] = {
            key: hls[key] for key in ("quality_gate", "cosim", "artifacts") if key in hls
        }
    markers = summary.get("markers") or {}
    if "COMPONENT" in markers or "COMPONENT_COUNT" in markers:
        components = markers.get("COMPONENT", [])
        out["components"] = {
            "items": [value[:256] for value in components[:16]],
            "total": len(components),
            "truncated": len(components) > 16 or any(len(value) > 256 for value in components[:16]),
        }
    important = {key: markers[key] for key in ("PASS", "FAIL", "ERROR") if key in markers}
    if important:
        out["markers"] = important
    return out


def _compact_success_summary(job: dict) -> bool:
    """迁移旧 Job：成功摘要不携带可按需读取的冗长日志字段。"""
    summary = job.get("summary")
    if not isinstance(summary, dict) or _summary_has_failure(job, summary):
        return False
    changed = False
    for key in ("log_tail", "log_file", "vivado_log_file"):
        if key in summary:
            summary.pop(key)
            changed = True
    return changed


def _recover_recent_project_artifacts(job: dict, artifacts: dict[str, list]) -> list[str]:
    """Recover newly written project artifacts when a later Tcl step failed.

    The mtime gate prevents an old BIT/DCP from being presented as evidence for
    the current Job.  Only one newest file of each type is returned.
    """
    meta = job.get("meta") or {}
    xpr_text = meta.get("xpr", "") if isinstance(meta, dict) else ""
    if not xpr_text:
        return []
    project_dir = Path(xpr_text).expanduser().resolve().parent
    started = float(job.get("started") or 0) - 5.0
    patterns = {
        "bit": "*.runs/**/*.bit",
        "ltx": "*.runs/**/*.ltx",
        "dcp": "*.runs/**/*.dcp",
        "xsa": "**/*.xsa",
    }
    recovered: list[str] = []
    for kind, pattern in patterns.items():
        if artifacts.get(kind):
            continue
        try:
            candidates = [
                path for path in project_dir.glob(pattern)
                if path.is_file() and path.stat().st_mtime >= started
            ]
        except OSError:
            candidates = []
        if candidates:
            newest = max(candidates, key=lambda path: path.stat().st_mtime)
            artifacts[kind] = [str(newest)]
            recovered.append(kind)
    return recovered


def summarize_job(job: dict) -> dict:
    log_path = job.get("log", "")
    log_texts = []
    for candidate in (Path(log_path), Path(job.get("dir", ".")) / "vivado.log"):
        try:
            candidate_text = candidate.read_text(encoding="utf-8", errors="replace")
            if candidate_text:
                log_texts.append(candidate_text)
        except OSError:
            pass
    # stdout 与 vivado.log 经常重复；逐行去重避免错误数和 marker 翻倍。
    text = "\n".join(dict.fromkeys("\n".join(log_texts).splitlines()))
    summary: dict = {
        "status": job["status"],
        "returncode": job.get("returncode"),
    }
    meta = job.get("meta") or {}
    if job.get("kind") == "vitis_ai" and isinstance(meta, dict):
        result_path = Path(meta.get("vitis_ai_result", ""))
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result = None
        if isinstance(result, dict):
            summary["vitis_ai"] = result
        if job.get("status") != "success" or not isinstance(result, dict):
            summary["log_file"] = log_path
            summary["log_tail"] = tail(log_path, 40)
        return summary
    existing_summary = job.get("summary")
    if isinstance(existing_summary, dict) and existing_summary.get("exception"):
        summary["exception"] = existing_summary["exception"]
    vivado_log = Path(job.get("dir", ".")) / "vivado.log"
    if job.get("termination_failed"):
        summary["termination_failed"] = True
    infrastructure_failure = (
        job.get("infrastructure_failure") or parsers.detect_infrastructure_failure(text)
    )
    if infrastructure_failure:
        summary["infrastructure_failure"] = infrastructure_failure
    failure_signature = parsers.classify_failure(text)
    if failure_signature:
        if failure_signature.get("message") == infrastructure_failure:
            failure_signature = {
                key: value for key, value in failure_signature.items() if key != "message"
            }
        summary["failure_signature"] = failure_signature
        if failure_signature.get("partial_success"):
            summary["partial_success"] = True
    summary.update(parsers.scan_log(text))
    markers = parsers.wb_markers(text)
    # 收集产物
    artifacts: dict[str, list] = {}
    for v in markers.get("ARTIFACT", []):
        k, _, p = v.partition("|")
        artifacts.setdefault(k, []).append(p)
    # ARTIFACT carries the same long paths as summary.artifacts. Do not return
    # both copies to the Agent; keep every other diagnostic marker intact.
    public_markers = {k: v for k, v in markers.items() if k != "ARTIFACT"}
    if public_markers:
        summary["markers"] = public_markers
    jdir = Path(job.get("dir", "."))
    summary["artifacts"] = artifacts
    recovered = (
        _recover_recent_project_artifacts(job, artifacts)
        if job.get("status") != "success" or summary.get("partial_success") else []
    )
    if recovered:
        summary["artifacts_recovered"] = recovered

    kind = job.get("kind", "")
    # 报告解析
    rpt = jdir / "reports"
    if rpt.is_dir():
        timing_files = sorted(rpt.glob("timing_*.rpt"))
        util_files = sorted(rpt.glob("utilization_*.rpt"))
        drc_files = sorted(rpt.glob("drc_*.rpt"))
        route_files = sorted(rpt.glob("route_status*.rpt"))
        if timing_files:
            t = timing_files[-1].read_text(encoding="utf-8", errors="replace")
            summary["timing"] = parsers.parse_timing_summary(t)
            summary["timing_report"] = str(timing_files[-1])
        if util_files:
            u = util_files[-1].read_text(encoding="utf-8", errors="replace")
            summary["utilization"] = parsers.parse_utilization(u)
            summary["utilization_report"] = str(util_files[-1])
        if drc_files:
            d = drc_files[-1].read_text(encoding="utf-8", errors="replace")
            summary["drc"] = parsers.parse_drc_report(d)
            summary["drc_report"] = str(drc_files[-1])
        if route_files:
            r = route_files[-1].read_text(encoding="utf-8", errors="replace")
            summary["route_status"] = parsers.parse_route_status(r)
            summary["route_status_report"] = str(route_files[-1])
    # 仿真日志
    meta = job.get("meta") or {}
    audit_mode = meta.get("workspace_audit") if isinstance(meta, dict) else None
    workspace = meta.get("workspace", "") if isinstance(meta, dict) else ""
    if audit_mode and workspace:
        try:
            audit = vitis_audit.audit_workspace(workspace)
            if audit_mode == "always" or audit["status"] != "pass":
                summary["workspace_audit"] = audit
        except (OSError, ValueError) as exc:
            summary["workspace_audit"] = {
                "status": "error", "issue_count": 1,
                "issues": [{"code": "audit_failed", "severity": "error", "message": str(exc)}],
            }
    if kind == "vitis_hls" and isinstance(meta.get("hls"), dict):
        summary["hls"] = hls_flow.summarize(meta["hls"], text)
    if kind == "simulate" or meta.get("simulation"):
        xpr = meta.get("xpr", "")
        xsim_logs = []
        started = float(job.get('started') or 0)
        ended = float(job['ended']) if job.get('ended') is not None else None

        def current_stamp(path):
            try:
                value = path.stat()
                if value.st_mtime >= started and (ended is None or value.st_mtime <= ended):
                    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            except OSError:
                pass
            return None

        if xpr:
            proj_dir = Path(xpr).parent
            stem = Path(xpr).stem
            logs = list(proj_dir.glob(f"{stem}.sim/sim_1/**/xsim/*.log"))
            fresh_logs = []
            for path in logs:
                if stamp := current_stamp(path):
                    fresh_logs.append((path, stamp))
            # A current compile failure must never reuse an earlier PASS log.
            fresh_logs.sort(key=lambda item: item[1][-1], reverse=True)
            if fresh_logs:
                current = fresh_logs[0][0].parent
                current_logs = [item for item in fresh_logs if item[0].parent == current]
                xsim_logs = [item for item in current_logs if item[0].name == 'simulate.log']
                candidates = [current/name for name in
                    ('xvlog.log', 'xvhdl.log', 'xelab.log', 'compile.log', 'elaborate.log')]
            else:
                candidates = []
            diagnostic = simulation_diagnostics.diagnose_logs(candidates, not_before=started, not_after=ended)
            # Ordinary successful compiles do not need another page of unknown
            # cache fields. Attach only actionable failures or actual reuse.
            if (job.get('status') != 'success' or diagnostic['issue_count']
                    or any(stage['cache_state'] != 'unknown' for stage in diagnostic['stages'].values())):
                summary['simulation_compile'] = diagnostic
        if xsim_logs:
            path, stamp = xsim_logs[0]
            try:
                xt = path.read_text(encoding="utf-8", errors="replace")
                if current_stamp(path) == stamp:
                    simulation = parsers.parse_xsim_log(xt)
                    if simulation["verdict"] != "pass":
                        simulation["log_tail"] = "\n".join(xt.splitlines()[-30:])
                    summary["simulation"] = simulation
                    summary["xsim_log"] = str(path)
            except OSError:
                pass
        if xpr and 'simulation' not in summary:
            summary['simulation'] = {**parsers.parse_xsim_log(''),
                'evidence_status':'no_fresh_simulation_log'}
        if xpr:
            proj_dir = Path(xpr).parent
            stem = Path(xpr).stem
            wdb_files = [(path, stamp) for path in proj_dir.glob(f"{stem}.sim/sim_1/**/*.wdb")
                         if (stamp := current_stamp(path))]
            if wdb_files:
                summary["wdb"] = str(max(wdb_files, key=lambda item: item[1][-1])[0])
        waveform_meta = meta.get("waveform")
        if isinstance(waveform_meta, dict):
            vcd_path = Path(waveform_meta.get("vcd", jdir / "waveform.vcd"))
            summary["waveform"] = waveform_tools.summarize(vcd_path, waveform_meta, jdir)
    if _summary_has_failure(job, summary):
        summary["log_file"] = log_path
        actions = _failure_next_actions(summary)
        if actions:
            summary["next_actions"] = actions
        summary["log_tail"] = tail(log_path, 40)
        if vivado_log.is_file() and vivado_log.stat().st_size:
            summary["vivado_log_file"] = str(vivado_log)
    return summary


# ---------------------------------------------------------------------------
# 环境
# ---------------------------------------------------------------------------
def detect_tools(refresh: bool = False, detail: str = "compact") -> dict:
    if detail not in {"compact", "standard"}:
        raise BridgeError("detail 必须是 compact 或 standard")
    return env_detect.tool_info(refresh=refresh, detail=detail)


def set_tool_paths(vivado_path: str = "", vitis_path: str = "",
                   license_file: str = "", vivado_launcher: str = "",
                   reset: bool = False, version: str = "") -> dict:
    if not isinstance(reset, bool):
        raise BridgeError("reset 必须是布尔值")
    launcher = vivado_launcher.casefold()
    if launcher and launcher not in {"auto", "wrapper", "unwrapped"}:
        raise BridgeError("vivado_launcher 必须是 auto、wrapper 或 unwrapped")
    if not isinstance(version, str):
        raise BridgeError("version 必须是字符串")
    selected_version = version.strip()
    if selected_version:
        if not re.fullmatch(r"\d{4}\.\d+", selected_version):
            raise BridgeError("version 必须类似 2022.2 或 2026.1")
        if vivado_path or vitis_path:
            raise BridgeError("version 与 vivado_path/vitis_path 不能同时传；请选择一种定位方式")
        selected = env_detect.installations_for_version(selected_version)
        vivado_path = selected.get("vivado_path", "")
        vitis_path = selected.get("vitis_path", "")
        if not vivado_path:
            raise BridgeError(f"未找到 Vivado {selected_version}；先用 xilinx_detect(detail='standard') 查看安装")
        # A version switch must not inherit a launcher override chosen for a
        # different release. Explicit wrapper/unwrapped still wins.
        launcher = launcher or "auto"
    cfg = config.set_paths(
        vivado_path, vitis_path, license_file, launcher, reset, selected_version
    )
    env_detect.invalidate_cache()
    public_cfg = {k: v for k, v in cfg.items() if k != "license_file"}
    license_state = env_detect.license_info()
    public_cfg["license"] = license_state
    public_cfg["license_configured"] = license_state["candidate_found"]
    return {"config": public_cfg, "detected": env_detect.tool_info(refresh=True)}


# ---------------------------------------------------------------------------
# Vivado 工程
# ---------------------------------------------------------------------------
def _submit_project_tcl(tcl_text: str, kind: str, xpr: str,
                        wait: bool, timeout: int,
                        extra_meta: dict | None = None) -> dict:
    _require_xpr(xpr)
    meta = {"xpr": xpr}
    if extra_meta:
        meta.update(extra_meta)
    return run_vivado_tcl(tcl_text, kind, wait=wait, timeout=timeout,
                          meta=meta)


def _run_with_rpt_dir(tcl_with_placeholder: str, kind: str, xpr: str,
                      wait: bool, timeout: int) -> dict:
    """报告类操作需要把 reports 目录指向 job 目录, 先分配 job 目录再替换占位符。"""
    _require_xpr(xpr)
    jdir = MANAGER.alloc_dir(kind)
    rpt = jdir / "reports"
    rpt.mkdir(exist_ok=True)
    tcl = tcl_with_placeholder.replace(RPT, tcl_lib.qraw(str(rpt)))
    run_dir = Path(xpr).parent / f"{Path(xpr).stem}.runs" / "impl_1"
    tcl = tcl.replace(RUN_DIR, tcl_lib.qraw(str(run_dir)))
    return run_vivado_tcl(tcl, kind, wait=wait, timeout=timeout,
                          meta={"xpr": xpr}, jdir=jdir)


def create_project(directory: str, name: str, part: str,
                   wait: bool = True, timeout: int = 600, *,
                   items: list[dict] | None = None,
                   overwrite: bool = False,
                   simulate_after_create: bool = False,
                   tb_top: str = "", runtime: str = "",
                   sim_mode: str = "behavioral",
                   image_test: dict | None = None,
                   waveform: dict | None = None) -> dict:
    if not isinstance(overwrite, bool):
        raise BridgeError("overwrite 必须是布尔值")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise BridgeError(f"非法工程名: {name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", part):
        raise BridgeError(f"非法器件型号: {part}")
    if simulate_after_create and not items and image_test is None:
        raise BridgeError("simulate_after_create=true 时必须同时提供 items")
    if waveform is not None and not simulate_after_create and image_test is None:
        raise BridgeError("waveform 需要 simulate_after_create=true 或 image_test")
    project_dir = (Path(directory).expanduser() / name).resolve()
    xpr = project_dir / f"{name}.xpr"
    if xpr.exists() and not overwrite:
        raise BridgeError(
            f"Vivado 工程已存在，拒绝静默覆盖: {xpr}；确认后传 overwrite=true"
        )
    source_items, image_meta = _append_image_test(project_dir, items, image_test)
    if image_meta is not None:
        simulate_after_create = True
        if tb_top and tb_top != image_meta["tb_top"]:
            raise BridgeError("image_test 与显式 tb_top 冲突")
        tb_top = image_meta["tb_top"]
        runtime = runtime or "all"
    try:
        waveform_meta = waveform_tools.normalize(waveform, tb_top)
    except waveform_tools.WaveformSpecError as exc:
        raise BridgeError(str(exc)) from exc
    written: list[str] = []
    source_setup = "# no sources"
    if source_items:
        add_lines, top_lines, written, referenced = _prepare_source_batch(
            project_dir, source_items, allow_overwrite=overwrite
        )
        source_setup = _source_setup_tcl(add_lines, top_lines)
    else:
        referenced = []
    follow_up = (
        _simulation_steps(tb_top, runtime, sim_mode, waveform_meta)
        if simulate_after_create else "# no simulation"
    )
    tcl = tcl_lib.render(tcl_lib.CREATE_PROJECT,
                         NAME=tcl_lib.q(name),
                         DIR=tcl_lib.q(str(project_dir)),
                         PART=tcl_lib.q(part),
                         FORCE="-force" if overwrite else "",
                         SOURCE_SETUP=source_setup,
                         FOLLOW_UP=follow_up)
    kind = "create_and_simulate" if simulate_after_create else "create_project"
    result = run_vivado_tcl(
        tcl, kind, wait, max(timeout, 1800) if simulate_after_create else timeout,
        meta={"xpr": str(xpr), "simulation": simulate_after_create,
              "image_test": image_meta, "waveform": waveform_meta},
    )
    if written:
        result["written_files"] = written
    if referenced:
        result["referenced_files"] = referenced
    return result


def add_sources(xpr: str, files: list[str], fileset: str = "sources_1",
                top: str = "", wait: bool = True) -> dict:
    _require_xpr(xpr)
    _validate_fileset(fileset)
    if not files:
        raise BridgeError("files 不能为空")
    add_lines = []
    for f in files:
        if not Path(f).expanduser().is_file():
            raise BridgeError(f"待添加文件不存在: {f}")
        if fileset == "constrs_1" or f.lower().endswith(".xdc"):
            add_lines.append(f"add_files -fileset constrs_1 -norecurse {tcl_lib.q(f)}")
        elif fileset == "sim_1":
            add_lines.append(f"add_files -fileset sim_1 -norecurse {tcl_lib.q(f)}")
        else:
            add_lines.append(f"add_files -norecurse {tcl_lib.q(f)}")
    if top and fileset == "constrs_1":
        raise BridgeError("constrs_1 不能设置 top")
    top_lines = (
        f"set_property top {tcl_lib.q(top)} [get_filesets {tcl_lib.q(fileset)}]"
        if top else "# top unchanged"
    )
    tcl = tcl_lib.render(tcl_lib.ADD_SOURCES, XPR=tcl_lib.q(xpr),
                         ADD_LINES="\n".join(add_lines), TOP_LINES=top_lines,
                         FOLLOW_UP="# no simulation")
    return _submit_project_tcl(tcl, "add_sources", xpr, wait, 600)


def write_source(xpr: str, rel_path: str, content: str,
                 fileset: str = "sources_1", top: str = "") -> dict:
    """写 HDL 文件到工程目录并加入工程。"""
    return write_sources(xpr, [{"rel_path": rel_path, "content": content,
                                "fileset": fileset, "top": top}])


def write_sources(xpr: str, items: list[dict], wait: bool = True, *,
                  simulate_after_write: bool = False,
                  tb_top: str = "", runtime: str = "",
                  sim_mode: str = "behavioral",
                  image_test: dict | None = None,
                  waveform: dict | None = None) -> dict:
    """批量写入多个文件并一次性加入工程（只启动一次 Vivado，比逐文件快 N 倍）。
    items: [{"rel_path": "src/a.v", "content": "...", "fileset": "sources_1", "top": ""}]"""
    _require_xpr(xpr)
    project_dir = Path(xpr).resolve().parent
    source_items, image_meta = _append_image_test(project_dir, items, image_test)
    if image_meta is not None:
        simulate_after_write = True
        if tb_top and tb_top != image_meta["tb_top"]:
            raise BridgeError("image_test 与显式 tb_top 冲突")
        tb_top = image_meta["tb_top"]
        runtime = runtime or "all"
    if waveform is not None and not simulate_after_write and image_meta is None:
        raise BridgeError("waveform 需要 simulate_after_write=true 或 image_test")
    try:
        waveform_meta = waveform_tools.normalize(waveform, tb_top)
    except waveform_tools.WaveformSpecError as exc:
        raise BridgeError(str(exc)) from exc
    add_lines, top_lines, written, referenced = _prepare_source_batch(project_dir, source_items)
    follow_up = (
        _simulation_steps(tb_top, runtime, sim_mode, waveform_meta)
        if simulate_after_write else "# no simulation"
    )
    tcl = tcl_lib.render(tcl_lib.ADD_SOURCES, XPR=tcl_lib.q(xpr),
                         ADD_LINES=add_lines, TOP_LINES=top_lines,
                         FOLLOW_UP=follow_up)
    kind = "write_and_simulate" if simulate_after_write else "add_sources"
    result = _submit_project_tcl(
        tcl, kind, xpr, wait, 1800 if simulate_after_write else 600,
        extra_meta={"simulation": simulate_after_write, "image_test": image_meta,
                    "waveform": waveform_meta},
    )
    result["written_files"] = written
    if referenced:
        result["referenced_files"] = referenced
    return result


def list_parts(pattern: str = "*", limit: int = 50) -> dict:
    """查询可用器件型号（验证 part 是否存在于当前安装/许可）。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise BridgeError("limit 必须在 1..10000 之间")
    tcl_code = (
        f"set ps [get_parts -quiet {tcl_lib.q(pattern)}]\n"
        "puts \"WB_COUNT|[llength $ps]\"\n"
        f"foreach p [lrange $ps 0 {limit - 1}] {{ puts \"WB_PART|$p\" }}\n"
        "exit 0\n"
    )
    return run_raw_tcl(tcl_code, "list_parts", True, 300)


def list_sources(xpr: str, audit_only: bool = False,
                 shared_ip_roots: list[str] | None = None,
                 max_files: int = 240, max_issues: int = 12) -> dict:
    if not isinstance(audit_only, bool):
        raise BridgeError("audit_only 必须是布尔值")
    if audit_only:
        from . import project_audit
        try:
            audit = project_audit.audit_project(
                xpr, shared_ip_roots=shared_ip_roots,
                max_files=max_files, max_issues=max_issues,
            )
        except (OSError, ValueError) as exc:
            raise BridgeError(str(exc)) from exc
        return {"status": "success", "vivado_started": False, "project_audit": audit}
    if shared_ip_roots is not None or max_files != 240 or max_issues != 12:
        raise BridgeError("shared_ip_roots/max_files/max_issues 仅用于 audit_only=true")
    tcl = tcl_lib.render(tcl_lib.LIST_SOURCES, XPR=tcl_lib.q(xpr))
    return _submit_project_tcl(tcl, "list_sources", xpr, True, 300)


def elaborate(xpr: str, wait: bool = True, timeout: int = 1800) -> dict:
    tcl = tcl_lib.render(tcl_lib.ELABORATE, XPR=tcl_lib.q(xpr), RPT_DIR=RPT)
    return _run_with_rpt_dir(tcl, "elaborate", xpr, wait, timeout)


def simulate(xpr: str, tb_top: str = "", runtime: str = "",
             sim_mode: str = "behavioral", wait: bool = True,
             timeout: int = 1800, image_test: dict | None = None,
             waveform: dict | None = None) -> dict:
    _require_xpr(xpr)
    if image_test is not None:
        return write_sources(
            xpr, [], wait=wait, simulate_after_write=True,
            tb_top=tb_top, runtime=runtime, sim_mode=sim_mode,
            image_test=image_test, waveform=waveform,
        )
    if sim_mode not in _SIM_MODES:
        raise BridgeError(f"不支持的 sim_mode: {sim_mode}；可选值: {sorted(_SIM_MODES)}")
    try:
        waveform_meta = waveform_tools.normalize(waveform, tb_top)
    except waveform_tools.WaveformSpecError as exc:
        raise BridgeError(str(exc)) from exc
    steps = _simulation_steps(tb_top, runtime, sim_mode, waveform_meta)
    tcl = tcl_lib.COMMON + f"\nopen_project {tcl_lib.q(xpr)}\n{steps}\nclose_project\nexit 0\n"
    return _submit_project_tcl(
        tcl, "simulate", xpr, wait, timeout,
        extra_meta={"simulation": True, "waveform": waveform_meta},
    )


def _resolve_jobs(jobs_n: int) -> int:
    value = config.load().get("default_jobs", 8) if jobs_n == 0 else jobs_n
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
        raise BridgeError("jobs_n/default_jobs 必须是 1..256 的整数，0 表示使用默认值")
    return value


def synthesize(xpr: str, jobs_n: int = 0, wait: bool = False, timeout: int = 7200) -> dict:
    j = _resolve_jobs(jobs_n)
    tcl = tcl_lib.render(tcl_lib.RUN_SYNTH, XPR=tcl_lib.q(xpr), JOBS=j, RPT_DIR=RPT)
    return _run_with_rpt_dir(tcl, "synthesize", xpr, wait, timeout)


def implement(xpr: str, jobs_n: int = 0, wait: bool = False, timeout: int = 10800) -> dict:
    j = _resolve_jobs(jobs_n)
    tcl = tcl_lib.render(tcl_lib.RUN_IMPL, XPR=tcl_lib.q(xpr), JOBS=j, RPT_DIR=RPT)
    return _run_with_rpt_dir(tcl, "implement", xpr, wait, timeout)


def bitstream(xpr: str, jobs_n: int = 0, wait: bool = False, timeout: int = 14400) -> dict:
    j = _resolve_jobs(jobs_n)
    tcl = tcl_lib.render(tcl_lib.RUN_BITSTREAM, XPR=tcl_lib.q(xpr), JOBS=j,
                         RPT_DIR=RPT, RUN_DIR=RUN_DIR)
    return _run_with_rpt_dir(tcl, "bitstream", xpr, wait, timeout)


def build_all(xpr: str, jobs_n: int = 0, wait: bool = False, timeout: int = 21600) -> dict:
    """综合 -> 实现 -> 比特流, 一步到位 (bitstream 流程自动级联前面的步骤)。"""
    return bitstream(xpr, jobs_n, wait, timeout)


def report(xpr: str, run: str = "impl_1", wait: bool = True, timeout: int = 1800) -> dict:
    _require_xpr(xpr)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run):
        raise BridgeError(f"非法 run 名称: {run}")
    tcl = tcl_lib.render(tcl_lib.REPORTS, XPR=tcl_lib.q(xpr), RUN=run, RPT_DIR=RPT)
    return _run_with_rpt_dir(tcl, "report", xpr, wait, timeout)


def run_raw_tcl(tcl_code: str, kind: str = "raw_tcl",
                wait: bool = True, timeout: int = 3600) -> dict:
    """逃生舱: 运行任意 TCL, Agent 可做本桥未覆盖的任意操作。"""
    return run_vivado_tcl(tcl_lib.RAW_TCL_HEADER + "\n" + tcl_code, kind, wait, timeout)


# ---------------------------------------------------------------------------
# 硬件
# ---------------------------------------------------------------------------
def _hardware_lock_key(server: str) -> str:
    normalized = _debug_server_endpoint(server).casefold()
    return f"lock:hw_server:{normalized}"


def _debug_server_endpoint(server: str) -> str:
    """Canonical endpoint shared by XSDB and the cross-tool hardware lock."""
    normalized = server.strip().removeprefix("tcp:")
    if normalized.casefold().startswith("localhost:"):
        return "127.0.0.1:" + normalized.split(":", 1)[1]
    return normalized


def hw_list(server: str = "", wait: bool = True, timeout: int = 120) -> dict:
    srv = server or config.load().get("hw_server_url", "localhost:3121")
    tcl = tcl_lib.render(tcl_lib.HW_LIST, SERVER=tcl_lib.q(srv))
    return run_vivado_tcl(
        tcl, "hw_list", wait, timeout, resource_key=_hardware_lock_key(srv)
    )


def _hw_input(filename: str, label: str) -> str:
    path = Path(filename).expanduser().resolve()
    if not path.is_file():
        raise BridgeError(f"{label} 文件不存在: {path}")
    return str(path)


def program_fpga(bit: str, ltx: str = "", target_index: int = 0,
                 device_index: int = -1, server: str = "",
                 wait: bool = True, timeout: int = 600, *,
                 target_name: str = "", device_name: str = "", device_part: str = "") -> dict:
    bit = _hw_input(bit, "比特流")
    ltx = _hw_input(ltx, "LTX") if ltx else ""
    _bounded_int(target_index, "target_index", 0, 1024)
    _bounded_int(device_index, "device_index", -1, 1024)
    srv = server or config.load().get("hw_server_url", "localhost:3121")
    tcl = tcl_lib.render(tcl_lib.PROGRAM_FPGA, SERVER=tcl_lib.q(srv), BIT=tcl_lib.q(bit),
                         LTX=tcl_lib.q(ltx),
                         TIDX=target_index, DIDX=device_index,
                         TARGET_NAME=tcl_lib.q(target_name), DEVICE_NAME=tcl_lib.q(device_name),
                         DEVICE_PART=tcl_lib.q(device_part))
    return run_vivado_tcl(
        tcl, "program_fpga", wait, timeout, resource_key=_hardware_lock_key(srv)
    )


def ila_capture(ltx: str, bit: str = "", depth: int = 1024,
                trigger: dict | None = None, ila_index: int = 0,
                target_index: int = 0, device_index: int = -1,
                timeout_s: int = 60, server: str = "",
                wait: bool = True, trigger_position: int = 0, *,
                target_name: str = "", device_name: str = "", device_part: str = "") -> dict:
    ltx = _hw_input(ltx, "LTX") if ltx else ""
    bit = _hw_input(bit, "比特流") if bit else ""
    _bounded_int(depth, "depth", 1, 1048576)
    _bounded_int(timeout_s, "timeout_s", 1, 86400)
    _bounded_int(trigger_position, "trigger_position", 0, depth - 1)
    for label, value in (("ila_index", ila_index), ("target_index", target_index)):
        _bounded_int(value, label, 0, 1024)
    _bounded_int(device_index, "device_index", -1, 1024)
    if trigger is not None and (
        not isinstance(trigger, dict)
        or any(
            not isinstance(k, str) or not k or len(k) > 1024
            or not isinstance(v, str) or not v or len(v) > 256
            or any(ord(ch) < 32 for ch in k + v)
            for k, v in trigger.items()
        )
    ):
        raise BridgeError("trigger 必须是非空 probe 名到非空比较值字符串的对象")
    srv = server or config.load().get("hw_server_url", "localhost:3121")
    jdir = MANAGER.alloc_dir("ila_capture")
    jd = str(jdir).replace("\\", "/")
    tcl = tcl_lib.render(
        tcl_lib.ILA_CAPTURE, SERVER=tcl_lib.q(srv),
        BIT=tcl_lib.q(bit), LTX=tcl_lib.q(ltx),
        TIDX=target_index, DIDX=device_index, ILA_IDX=ila_index,
        TARGET_NAME=tcl_lib.q(target_name), DEVICE_NAME=tcl_lib.q(device_name), DEVICE_PART=tcl_lib.q(device_part),
        DEPTH=depth, TIMEOUT=timeout_s, TRIGGER_POSITION=trigger_position,
        TRIGGER_SETUP=tcl_lib.trigger_setup_tcl(trigger),
        RUN_OPTIONS="" if trigger else "-trigger_now",
        CSV=tcl_lib.q(f"{jd}/capture.csv"), ILA_FILE=tcl_lib.q(f"{jd}/capture.ila"),
        CSV_RAW=f"{jd}/capture.csv", ILA_RAW=f"{jd}/capture.ila",
    )
    result = run_vivado_tcl(
        tcl, "ila_capture", wait=False, jdir=jdir,
        resource_key=_hardware_lock_key(srv),
    )
    if wait:
        job = wait_job(result["id"], timeout_s + 300)
        job["summary"] = summarize_job(job)
        csv = jdir / "capture.csv"
        if csv.exists():
            lines = csv.read_text(encoding="utf-8", errors="replace").splitlines()
            job["summary"]["ila_csv"] = str(csv)
            # Vivado CSV has a column header and a radix row before sample 0.
            job["summary"]["ila_samples"] = _count_ila_csv_samples(lines)
            job["summary"]["ila_csv_head"] = lines[:6]
        MANAGER.set_summary(job["id"], job["summary"])
        return public_job(job)
    return result


def generate_mcs(bit: str, out_mcs: str, size_mb: int = 16,
                 interface: str = "SPIx1", wait: bool = True, timeout: int = 600) -> dict:
    if not Path(bit).is_file():
        raise BridgeError(f"比特流文件不存在: {bit}")
    _bounded_int(size_mb, "size_mb", 1, 1048576)
    allowed_interfaces = {"SPIx1", "SPIx2", "SPIx4", "SPIx8", "BPIx16"}
    if interface not in allowed_interfaces:
        raise BridgeError(f"不支持的 interface: {interface}")
    bit_fwd = tcl_lib.qraw(str(bit))
    tcl = tcl_lib.render(tcl_lib.GEN_MCS, BIT_RAW=bit_fwd, OUT=tcl_lib.q(out_mcs),
                         SIZE=size_mb, IFACE=interface)
    return run_vivado_tcl(tcl, "generate_mcs", wait, timeout)


def program_flash(mcs: str, flash_part: str, target_index: int = 0,
                  device_index: int = -1, erase: bool = True, verify: bool = True,
                  server: str = "", wait: bool = True, timeout: int = 3600, *,
                  target_name: str = "", device_name: str = "", device_part: str = "") -> dict:
    mcs = _hw_input(mcs, "MCS")
    _bounded_int(target_index, "target_index", 0, 1024)
    _bounded_int(device_index, "device_index", -1, 1024)
    srv = server or config.load().get("hw_server_url", "localhost:3121")
    tcl = tcl_lib.render(tcl_lib.PROGRAM_FLASH, SERVER=tcl_lib.q(srv), MCS=tcl_lib.q(mcs),
                         FLASH_PART=tcl_lib.q(flash_part),
                         TARGET_NAME=tcl_lib.q(target_name), DEVICE_NAME=tcl_lib.q(device_name), DEVICE_PART=tcl_lib.q(device_part),
                         TIDX=target_index, DIDX=device_index,
                         ERASE=1 if erase else 0, VERIFY=1 if verify else 0)
    return run_vivado_tcl(
        tcl, "program_flash", wait, timeout, resource_key=_hardware_lock_key(srv)
    )


# ---------------------------------------------------------------------------
# Vitis
# ---------------------------------------------------------------------------
def _legacy_vitis() -> bool:
    tool = _require_tool("vitis")
    version = env_detect._version_of(tool.parent.parent)
    return bool(re.fullmatch(r"202[0-2]\.\d+", version))


def _run_legacy_vitis(body: str, workspace: str, kind: str,
                      wait: bool, timeout: int, *, audit_mode: str = "issues") -> dict:
    workspace = str(Path(workspace).expanduser().resolve())
    tool = _require_tool("xsct")
    jdir = MANAGER.alloc_dir(kind)
    proxy = _legacy_vitis_which_proxy(jdir, tool)
    script = "if {[catch {\nsetws " + tcl_lib.q(workspace) + "\n" + body
    script += '\n} msg]} {puts "WB_ERROR|$msg"; exit 1}\nexit 0\n'
    path = tcl_lib.write_script(jdir, script)
    child_env = _legacy_vitis_environment()
    if proxy["enabled"]:
        proxy["trace"] = str(jdir / "which-probes.log")
        child_env["XAB_VITIS_WHICH_TRACE"] = proxy["trace"]
    try:
        job = MANAGER.submit(kind, [str(tool), str(path)], jdir, jdir=jdir, env=child_env,
            tcl_text=script, meta={"workspace": workspace, "workspace_audit": audit_mode,
                                  "vitis_backend": "xsct", "owned_process_tree": True,
                                  "legacy_which_proxy": proxy}, resource_key=workspace)
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    if wait:
        job = wait_job(job["id"], timeout)
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job["id"], job["summary"])
    return public_job(job)


_LEGACY_WHICH_SOURCE = r'''
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;

// The old IDE drains stdout before stderr. GNU which prints the entire PATH on
// a miss, so this process must keep diagnostics bounded and must not use a shell.
class LegacyWhich {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint GetFileAttributesW(string path);
    static string Env(string name) { return Environment.GetEnvironmentVariable(name) ?? ""; }
    static bool IsPath(string name) { return name.IndexOfAny(new char[] {'/', '\\', ':'}) >= 0; }
    static string Display(string path, bool dot, bool tilde) {
        string cwd = Environment.CurrentDirectory.TrimEnd('\\', '/');
        string home = Env("HOME").TrimEnd('\\', '/');
        if (home == "") home = Env("USERPROFILE").TrimEnd('\\', '/');
        if (dot && path.StartsWith(cwd + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            return "." + path.Substring(cwd.Length);
        if (tilde && home != "" && path.StartsWith(home + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            return "~" + path.Substring(home.Length);
        return path;
    }
    static void Trace(string name, bool found) {
        // Only a fixed marker is recorded; caller paths and arbitrary args are not.
        string path = Env("XAB_VITIS_WHICH_TRACE");
        if (path != "" && name == "sdscc") {
            try { File.AppendAllText(path, found ? "sdscc found\n" : "sdscc missing\n"); }
            catch (Exception) { }
        }
    }
    static int Main(string[] args) {
        bool all = false, skipDot = false, skipTilde = false, showDot = false, showTilde = false, options = true;
        var names = new List<string>();
        foreach (string arg in args) {
            if (options && arg == "--") { options = false; continue; }
            if (options && arg.StartsWith("-")) {
                switch (arg) {
                    case "-a": case "--all": all = true; break;
                    case "--skip-dot": skipDot = true; break;
                    case "--skip-tilde": skipTilde = true; break;
                    case "--show-dot": showDot = true; break;
                    case "--show-tilde": showTilde = true; break;
                    case "--skip-alias": case "--skip-functions": case "--tty-only": break;
                    case "--version": case "-v": case "-V": Console.WriteLine("Xilinx bridge bounded which 1"); return 0;
                    case "--help": Console.WriteLine("which [-a|--all] [--skip-dot] [--skip-tilde] [--show-dot] [--show-tilde] [--] program ..."); return 0;
                    default: Console.Error.WriteLine("which: unsupported option (see --help)"); return 2;
                }
            } else names.Add(arg);
        }
        if (names.Count == 0) { Console.Error.WriteLine("which: program name required"); return 2; }
        var dirs = new List<string>();
        if (!skipDot) dirs.Add(Environment.CurrentDirectory);
        string homeDir = Env("HOME");
        if (homeDir == "") homeDir = Env("USERPROFILE");
        foreach (string entry in Env("PATH").Split(';')) {
            string dir = entry.Trim('"');
            if (dir == "") dir = Environment.CurrentDirectory;
            if (skipDot && (dir.StartsWith(".") || dir == Environment.CurrentDirectory)) continue;
            if (skipTilde && (dir.StartsWith("~") || (homeDir != "" && dir.StartsWith(homeDir, StringComparison.OrdinalIgnoreCase)))) continue;
            dirs.Add(dir);
        }
        var extensions = new List<string>();
        extensions.Add("");
        string pathExt = Env("PATHEXT");
        if (pathExt == "") pathExt = ".COM;.EXE;.BAT;.CMD";
        foreach (string ext in pathExt.Split(';')) if (ext.StartsWith(".") && ext.IndexOfAny(new char[] {'/', '\\', ':'}) < 0) extensions.Add(ext);
        int missing = 0;
        foreach (string name in names) {
            bool found = false;
            var emitted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string ext in extensions) {
                foreach (string dir in (IsPath(name) ? new List<string> { Environment.CurrentDirectory } : dirs)) {
                    try {
                        string target = IsPath(name) ? name + ext : Path.Combine(dir, name + ext);
                        string candidate = target.StartsWith(@"\\?\") ? target : Path.GetFullPath(target);
                        uint attributes = GetFileAttributesW(candidate);
                        if (attributes != UInt32.MaxValue && (attributes & 0x10) == 0 && emitted.Add(candidate)) {
                            Console.WriteLine(Display(candidate, showDot, showTilde));
                            found = true;
                            if (!all) break;
                        }
                    } catch (Exception) { }
                }
                if (found && !all) break;
            }
            Trace(name, found);
            if (!found) missing++;
        }
        // A constant-sized diagnostic also bounds output for thousands of misses.
        if (missing > 0) Console.Error.WriteLine("which: program not found");
        return Math.Min(missing, 255);
    }
}
'''


def _legacy_vitis_which_proxy(jdir: Path, tool: Path) -> dict:
    """Install a job-private, bounded which for Windows Vitis 2022.2 only.

    Java executable lookup searches its working directory before PATH. The
    vendor's launchers preserve our job cwd but prepend their own GNU which to
    PATH, so keep this helper in cwd rather than changing the caller's PATH.
    """
    if (not env_detect.IS_WIN or env_detect._version_of(tool.parent.parent) != "2022.2"
            or os.environ.get("XAB_VITIS_LEGACY_WHICH_PROXY", "1") == "0"
            or os.environ.get("XAB_VITIS_LEGACY_MINIMAL_PATH") == "1"):
        return {"enabled": False}
    import subprocess
    import tempfile

    source_hash = hashlib.sha256(_LEGACY_WHICH_SOURCE.encode("utf-8")).hexdigest()
    cache = config.CONFIG_DIR / "helpers" / "legacy-which" / source_hash
    binary = cache / "which.exe"
    checksum = cache / "which.sha256"
    cache.mkdir(parents=True, exist_ok=True)

    def valid() -> bool:
        try:
            return (binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest()
                    == checksum.read_text(encoding="ascii").strip())
        except OSError:
            return False

    if not valid():
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        compiler = next((p for p in (
            windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe",
            windows / "Microsoft.NET/Framework/v4.0.30319/csc.exe",
        ) if p.is_file()), None)
        if compiler is None:
            raise BridgeError("Vitis 2022.2 bounded which needs the Windows .NET csc compiler; "
                              "set XAB_VITIS_LEGACY_WHICH_PROXY=0 only for diagnosis, "
                              "or explicitly set XAB_VITIS_LEGACY_MINIMAL_PATH=1")
        with tempfile.TemporaryDirectory(prefix="compile-", dir=cache) as directory:
            source = Path(directory) / "which.cs"
            output = Path(directory) / "which.exe"
            source.write_text(_LEGACY_WHICH_SOURCE, encoding="utf-8")
            try:
                result = subprocess.run(
                    [str(compiler), "/nologo", "/target:exe", "/optimize+", "/out:" + str(output), str(source)],
                    capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise BridgeError("Vitis 2022.2 bounded which compilation failed: " + str(exc)) from exc
            if result.returncode != 0 or not output.is_file():
                raise BridgeError("Vitis 2022.2 bounded which compilation failed: " +
                                  (result.stdout + result.stderr).decode(errors="replace")[-2048:])
            # Concurrent workspaces can compile the same content. Publish only a
            # completed executable, and accept a valid winner of that race.
            if not valid():
                try:
                    os.replace(output, binary)
                except OSError:
                    if not valid():
                        raise
                checksum.write_text(hashlib.sha256(binary.read_bytes()).hexdigest(), encoding="ascii")
    shutil.copy2(binary, jdir / "which.exe")
    return {"enabled": True, "source_sha256": source_hash, "path": str(jdir / "which.exe")}


def _legacy_vitis_environment() -> dict:
    """Opt-in workaround for old IDE sequential stdout/stderr lookup deadlock.

    Vendor launcher prepends its compiler/tool paths. Never alter the process or
    system PATH, and leave custom build dependencies untouched unless requested.
    """
    if not env_detect.IS_WIN or os.environ.get("XAB_VITIS_LEGACY_MINIMAL_PATH") != "1":
        return {}
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return {"PATH": ";".join(str(p) for p in
            (windows / "System32", windows, windows / "System32" / "Wbem"))}


def vitis_create_platform(workspace: str, name: str, xsa: str,
                          os_name: str = "standalone", cpu: str = "",
                          wait: bool = True, timeout: int = 3600) -> dict:
    if name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise BridgeError(f"非法平台组件名: {name}")
    if not Path(xsa).expanduser().is_file():
        raise BridgeError(f"XSA 文件不存在: {xsa}")
    workspace = str(Path(workspace).expanduser().resolve())
    xsa = str(Path(xsa).expanduser().resolve())
    if _legacy_vitis():
        if not cpu.strip():
            raise BridgeError("旧版 Vitis 创建平台需要显式 cpu，避免选择错误的处理器")
        if (Path(workspace) / name).exists():
            raise BridgeError("平台路径已存在；请复用或选择新的名称")
        body = "platform create -name %s -hw %s -proc %s -os %s\nplatform generate\nputs %s" % (
            tcl_lib.q(name), tcl_lib.q(xsa), tcl_lib.q(cpu), tcl_lib.q(os_name),
            tcl_lib.q("WB_OK|platform_built name=" + name))
        return _run_legacy_vitis(body, workspace, "vitis_platform", wait, timeout)
    script = tcl_lib.render(vitis_py.PLATFORM_CREATE, WS=repr(workspace),
                            NAME=repr(name), XSA=repr(xsa),
                            OS_NAME=repr(os_name), CPU=repr(cpu))
    return run_vitis_python(
        script, "vitis_platform", wait, timeout,
        meta={"workspace": workspace, "workspace_audit": "issues"},
        resource_key=workspace,
    )


def vitis_create_app(workspace: str, name: str, platform: str,
                     template: str = "hello_world", domain: str = "",
                     wait: bool = True, timeout: int = 3600) -> dict:
    if name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise BridgeError(f"非法应用组件名: {name}")
    if not platform.strip():
        raise BridgeError("platform 不能为空")
    workspace = str(Path(workspace).expanduser().resolve())
    if Path(platform).expanduser().is_file():
        platform = str(Path(platform).expanduser().resolve())
    if _legacy_vitis():
        if not domain.strip():
            raise BridgeError("旧版 Vitis 创建应用需要显式 domain")
        if (Path(workspace) / name).exists():
            raise BridgeError("应用路径已存在；请复用或选择新的名称")
        legacy_template = "Hello World" if template == "hello_world" else template
        body = "app create -name %s -platform %s -domain %s -template %s\napp build -name %s\nputs %s" % (
            tcl_lib.q(name), tcl_lib.q(platform), tcl_lib.q(domain),
            tcl_lib.q(legacy_template), tcl_lib.q(name), tcl_lib.q("WB_OK|app_built name=" + name))
        return _run_legacy_vitis(body, workspace, "vitis_app", wait, timeout)
    script = tcl_lib.render(vitis_py.APP_CREATE, WS=repr(workspace),
                            NAME=repr(name), PLATFORM=repr(platform),
                            TEMPLATE=repr(template), DOMAIN=repr(domain))
    return run_vitis_python(
        script, "vitis_app", wait, timeout,
        meta={"workspace": workspace, "workspace_audit": "issues"},
        resource_key=workspace,
    )


def vitis_build(workspace: str, component: str = "",
                wait: bool = True, timeout: int = 3600, *,
                hls: dict | None = None) -> dict:
    if hls is not None:
        if component:
            raise BridgeError("component 与 hls 二选一，不能同时提供")
        try:
            tcl, meta = hls_flow.prepare(workspace, hls)
        except hls_flow.HlsSpecError as exc:
            raise BridgeError(str(exc)) from exc
        return run_hls_tcl(tcl, Path(workspace).expanduser().resolve(), meta,
                           wait=wait, timeout=timeout)
    if not component:
        raise BridgeError("普通 Vitis 构建必须提供 component；HLS 构建请提供 hls")
    workspace = str(Path(workspace).expanduser().resolve())
    if _legacy_vitis():
        body = "if {[catch {platform active %s}]} {app build -name %s} else {platform generate}\nputs %s" % (
            tcl_lib.q(component), tcl_lib.q(component), tcl_lib.q("WB_OK|build_done"))
        return _run_legacy_vitis(body, workspace, "vitis_build", wait, timeout)
    script = tcl_lib.render(vitis_py.BUILD_COMPONENT, WS=repr(workspace),
                            COMP=repr(component))
    return run_vitis_python(
        script, "vitis_build", wait, timeout,
        meta={"workspace": workspace, "workspace_audit": "issues"},
        resource_key=workspace,
    )


def vitis_list(workspace: str, wait: bool = True, timeout: int = 300,
               audit: bool = True, audit_only: bool = False) -> dict:
    if audit_only:
        if not audit:
            raise BridgeError("audit_only=true 时不能关闭 audit")
        try:
            result = vitis_audit.audit_workspace(workspace)
        except (OSError, ValueError) as exc:
            raise BridgeError(str(exc)) from exc
        return {
            "status": "success", "vitis_started": False,
            "workspace_audit": result,
        }
    workspace = str(Path(workspace).expanduser().resolve())
    if not Path(workspace).is_dir():
        raise BridgeError(f"workspace 不存在: {workspace}")
    if _legacy_vitis():
        body = r'''
set wb_count 0
# Use the workspace query already used by vendor platform list. Its 2022.1
# catalog-merge loop overwrites platforms and references unset proj when empty.
# Querying projects avoids that loop and does not add a tool process.
set wb_chan [sdk::getsdkchan]
set wb_reply [sdk::xsdk_eval $wb_chan XSDx getProjects {o{Type s}} eA [list [dict create Type platform]]]
if {[llength $wb_reply] != 2} {error "Unexpected Vitis platform project response"}
if {[lindex $wb_reply 0] ne ""} {error [lindex $wb_reply 0]}
foreach wb_name [split [lindex $wb_reply 1] ";"] {
    if {$wb_name ne ""} {
        puts "WB_COMPONENT|platform $wb_name"
        incr wb_count
    }
}
if {[catch {app list -dict} wb_apps]} {
    if {[string trim $wb_apps] ne "No application exist"} {error $wb_apps}
    set wb_apps {}
}
if {$wb_apps ne "No application exist"} {
    dict for {wb_name wb_props} $wb_apps {
        puts "WB_COMPONENT|application $wb_name"
        incr wb_count
    }
}
puts "WB_COMPONENT_COUNT|$wb_count"
puts "WB_OK|listed"
'''
        return _run_legacy_vitis(body, workspace, "vitis_list", wait, timeout,
                                 audit_mode="always" if audit else "")
    script = tcl_lib.render(vitis_py.LIST_COMPONENTS, WS=repr(workspace))
    return run_vitis_python(
        script, "vitis_list", wait, timeout,
        meta={"workspace": workspace, "workspace_audit": "always"} if audit else None,
        resource_key=workspace,
    )


def _xsdb_symbol_checks(checks: dict[str, str] | None,
                        resume_after_checks: bool) -> str:
    if checks is None:
        return "# no debugger symbol checks requested"
    if not isinstance(checks, dict) or not checks:
        raise BridgeError("symbol_checks 必须是非空的符号名到期望值字符串对象")
    if not isinstance(resume_after_checks, bool):
        raise BridgeError("resume_after_checks 必须是布尔值")
    lines = ["catch {stop}", "set wb_symbol_failed 0"]
    for symbol, expected in checks.items():
        if not isinstance(symbol, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\[\d+\])*", symbol
        ):
            raise BridgeError(f"非法 debugger 符号表达式: {symbol}")
        if not isinstance(expected, str) or not re.fullmatch(
            r"[+-]?(?:0[xX][0-9A-Fa-f]+|\d+)", expected
        ):
            raise BridgeError(f"{symbol} 的期望值必须是整数或十六进制字符串")
        value_var = f"wb_symbol_value_{len(lines)}"
        raw_var = f"wb_symbol_raw_{len(lines)}"
        lines.extend([
            f"if {{[catch {{set {raw_var} [string trim [print {symbol}]]}} wb_symbol_msg]}} "
            f'{{ puts "WB_ERROR|symbol read failed: {symbol}: $wb_symbol_msg"; exit 1 }}',
            f"set {value_var} ${raw_var}",
            f"regexp {{:\\s*(\\S+)\\s*$}} ${raw_var} _ {value_var}",
            f'puts "WB_SYMBOL|{symbol}|${value_var}"',
            f"if {{${value_var} ne {tcl_lib.q(expected)}}} {{ "
            f'puts "WB_ERROR|symbol check failed: {symbol} expected={expected} actual=${value_var}"; '
            "set wb_symbol_failed 1 }",
        ])
    lines.append('if {$wb_symbol_failed} { exit 1 }')
    if resume_after_checks:
        lines.append("con")
    return "\n".join(lines)


def vitis_run_elf(elf: str, bit: str = "", run_ms: int = 10000,
                  xsa: str = "", init_tcl: str = "",
                  symbol_checks: dict[str, str] | None = None,
                  resume_after_checks: bool = True,
                  wait: bool = True, timeout: int = 600) -> dict:
    """通过当前 Vitis 的 XSDB（优先）或 XSCT 下载并运行 ELF。"""
    elf_path = Path(elf).expanduser().resolve()
    if not elf_path.is_file():
        raise BridgeError(f"ELF 文件不存在: {elf}")
    bit_path = Path(bit).expanduser().resolve() if bit else None
    if bit_path is not None and not bit_path.is_file():
        raise BridgeError(f"比特流文件不存在: {bit}")
    xsa_path = Path(xsa).expanduser().resolve() if xsa else None
    if xsa_path is not None and not xsa_path.is_file():
        raise BridgeError(f"XSA 文件不存在: {xsa}")
    init_path = Path(init_tcl).expanduser().resolve() if init_tcl else None
    if init_path is not None and not init_path.is_file():
        raise BridgeError(f"初始化 TCL 文件不存在: {init_tcl}")
    _bounded_int(run_ms, "run_ms", 0, 86400000)
    symbol_check_tcl = _xsdb_symbol_checks(symbol_checks, resume_after_checks)
    backend = env_detect.resolve_tool("xsdb") or env_detect.resolve_tool("xsct")
    if backend is None:
        raise BridgeError("找不到 XSDB/XSCT 调试后端；请检查 Vitis 安装或 xilinx_detect")
    backend_name = "xsdb" if backend.stem.casefold() == "xsdb" else "xsct"
    srv = config.load().get("hw_server_url", "localhost:3121")
    jdir = MANAGER.alloc_dir("vitis_run")
    # XSDB 2026.1 can rewrite the BIT passed to `fpga -f`. Never let a debug
    # session mutate a signed/reproducible source artifact; program a Job-local
    # copy and retain the original path/hash for provenance.
    program_bit = None
    if bit_path is not None:
        program_bit = jdir / "program.bit"
        shutil.copy2(bit_path, program_bit)
    program_xsa = None
    if xsa_path is not None:
        # loadhw may materialize the XSA's embedded BIT next to the handoff.
        # Keep that extraction inside the Job just like the explicit BIT copy.
        program_xsa = jdir / "hardware.xsa"
        shutil.copy2(xsa_path, program_xsa)
    tcl = tcl_lib.render(vitis_py.XSCT_RUN,
                         SERVER=tcl_lib.q(f"tcp:{_debug_server_endpoint(srv)}"),
                         BIT=tcl_lib.q(str(program_bit)) if program_bit else '""',
                         XSA=tcl_lib.q(str(program_xsa)) if program_xsa else '""',
                         INIT_TCL=tcl_lib.q(str(init_path)) if init_path else '""',
                         ELF=tcl_lib.q(str(elf_path)), RUN_MS=run_ms,
                         SYMBOL_CHECKS=symbol_check_tcl)
    sp = tcl_lib.write_script(jdir, tcl)
    try:
        job = MANAGER.submit(
            "vitis_run", [str(backend), str(sp)], jdir,
            meta={"debug_backend": backend_name, "source_bit": str(bit_path or ""),
                  "program_bit": str(program_bit or ""), "source_xsa": str(xsa_path or ""),
                  "program_xsa": str(program_xsa or ""),
                  "init_tcl": str(init_path or "")},
            tcl_text=tcl, jdir=jdir,
            resource_key=_hardware_lock_key(srv),
        )
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    if wait:
        job = wait_job(job["id"], timeout)
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job["id"], job["summary"])
    return public_job(job)


def vitis_program_flash(boot_bin: str, flash_type: str = "qspi-x4-single",
                        offset: str = "0x0", wait: bool = True,
                        timeout: int = 3600) -> dict:
    """用 program_flash 工具烧写 QSPI/OSPI (需要 BOOT.BIN)。"""
    pf = _require_tool("program_flash")
    if not Path(boot_bin).exists():
        raise BridgeError(f"BOOT.BIN 不存在: {boot_bin}")
    srv = config.load().get("hw_server_url", "localhost:3121")
    cmd = [str(pf), "-f", boot_bin, "-offset", offset, "-flash_type", flash_type,
           "-cable", "type", "xilinx_tcf", "url", f"tcp:{srv.removeprefix('tcp:')}"]
    try:
        job = MANAGER.submit(
            "program_flash", cmd, Path.home(),
            resource_key=_hardware_lock_key(srv),
        )
    except ResourceBusyError as exc:
        raise BridgeError(str(exc)) from exc
    if wait:
        job = wait_job(job["id"], timeout)
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job["id"], job["summary"])
    return public_job(job)


# ---------------------------------------------------------------------------
# Job 查询
# ---------------------------------------------------------------------------
def job_status(job_id: str, detail: str = "standard") -> dict:
    if detail not in {"compact", "standard"}:
        raise BridgeError("detail 必须是 compact 或 standard")
    job = MANAGER.get(job_id)
    if job is None:
        raise BridgeError(f"未知 job: {job_id}")
    if job["status"] == "running":
        reason = parsers.detect_infrastructure_failure(tail(job.get("log", ""), 200))
        if reason:
            MANAGER.fail(job_id)
            job = MANAGER.get(job_id) or job
            job["infrastructure_failure"] = reason
    if job["status"] != "running" and not job.get("summary"):
        job["summary"] = summarize_job(job)
        MANAGER.set_summary(job_id, job["summary"])
    elif _compact_success_summary(job):
        MANAGER.set_summary(job_id, job["summary"])
    result = public_job(job, include_paths=detail == "standard")
    if detail == "compact" and job.get("summary") is not None:
        result["summary"] = _compact_job_summary(job["summary"])
    return result


def job_log(job_id: str, lines: int = 80) -> dict:
    job = MANAGER.get(job_id)
    if job is None:
        raise BridgeError(f"未知 job: {job_id}")
    _bounded_int(lines, "lines", 1, 10000)
    return {"job_id": job_id, "status": job["status"],
            "log_tail": tail(job["log"], lines), "log_file": job["log"]}


def job_list(limit: int = 10) -> list[dict]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise BridgeError("limit 必须在 1..100 之间")
    return MANAGER.list(limit)


def job_cancel(job_id: str) -> dict:
    ok = MANAGER.cancel(job_id)
    return {"job_id": job_id, "cancelled": ok}
