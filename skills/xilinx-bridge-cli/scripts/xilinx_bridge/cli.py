"""命令行入口: 不经过 Agent 也能直接验证各项能力。

示例:
  python -m xilinx_bridge.cli detect
  python -m xilinx_bridge.cli serve                       # 启动 MCP stdio 服务
  python -m xilinx_bridge.cli create C:/work demo xc7k325tffg900-2
  python -m xilinx_bridge.cli build C:/work/demo/demo.xpr --wait
  python -m xilinx_bridge.cli sim C:/work/demo/demo.xpr --tb tb_top --runtime 10us
  python -m xilinx_bridge.cli devices
  python -m xilinx_bridge.cli program top.bit --ltx top.ltx
"""
from __future__ import annotations

import argparse
import json
import sys

from . import core


def _configure_utf8_output() -> None:
    """Keep JSON/diagnostics machine-readable across Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def failed_result(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("status") in ("error", "failed", "timeout", "cancelled"):
        return True
    summary = obj.get("summary") or {}
    return isinstance(summary, dict) and (summary.get("simulation") or {}).get("verdict") == "fail"


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    if failed_result(obj):
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_output()
    ap = argparse.ArgumentParser(prog="xilinx-bridge", description="Xilinx Agent Bridge CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("detect", help="探测 Vivado/Vitis 安装")
    p.add_argument("--detail", choices=("compact", "standard"), default="compact")
    sub.add_parser("serve", help="启动 MCP stdio 服务")

    p = sub.add_parser("config", help="设置安装路径")
    p.add_argument("--vivado", default="")
    p.add_argument("--vitis", default="")
    p.add_argument("--license", default="", help=".lic 路径或 端口@许可证服务器")
    p.add_argument("--vivado-launcher", choices=("auto", "wrapper", "unwrapped"), default="")
    p.add_argument("--version", default="", help="按版本原子选择 Vivado/Vitis，如 2022.2")
    p.add_argument("--reset", action="store_true", help="清除持久化覆盖并恢复自动探测")

    p = sub.add_parser("create", help="创建工程")
    p.add_argument("directory"); p.add_argument("name"); p.add_argument("part")

    p = sub.add_parser("sim", help="仿真")
    p.add_argument("xpr"); p.add_argument("--tb", default="")
    p.add_argument("--runtime", default=""); p.add_argument("--mode", default="behavioral")

    p = sub.add_parser("elaborate", help="RTL 分析")
    p.add_argument("xpr")

    p = sub.add_parser("build", help="综合+实现+比特流")
    p.add_argument("xpr"); p.add_argument("--wait", action="store_true")

    p = sub.add_parser("devices", help="扫描 JTAG 设备")

    p = sub.add_parser("program", help="JTAG 下载比特流")
    p.add_argument("bit"); p.add_argument("--ltx", default="")

    p = sub.add_parser("ila", help="ILA 抓取")
    p.add_argument("ltx"); p.add_argument("--bit", default="")
    p.add_argument("--depth", type=int, default=1024)

    p = sub.add_parser("mcs", help="bit -> mcs")
    p.add_argument("bit"); p.add_argument("out"); p.add_argument("--size", type=int, default=16)
    p.add_argument("--interface", default="SPIx1")

    p = sub.add_parser("flash", help="烧写 FLASH")
    p.add_argument("mcs"); p.add_argument("part")

    p = sub.add_parser("vision-prepare", help="创建 KV260 图像验证 session")
    p.add_argument("session"); p.add_argument("input"); p.add_argument("--golden", default="")
    p.add_argument("--format", choices=("rgb", "gray"), default="rgb")
    p.add_argument("--ppc", type=int, choices=(1, 2, 4, 8), default=1)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--detail", choices=("compact", "standard"), default="compact")

    p = sub.add_parser("video-prepare", help="外部视频转多帧 AXI4-Stream 仿真输入")
    p.add_argument("session"); p.add_argument("input"); p.add_argument("--golden", default="")
    p.add_argument("--format", choices=("rgb", "gray"), default="rgb")
    p.add_argument("--ppc", type=int, choices=(1, 2, 4, 8), default=1)
    p.add_argument("--sample-fps", type=float, default=1.0)
    p.add_argument("--max-frames", type=int, default=8)
    p.add_argument("--start-time", type=float, default=0.0)
    p.add_argument("--width", type=int, default=0)
    p.add_argument("--height", type=int, default=0)
    p.add_argument("--resize-mode", choices=("stretch", "fit", "pad", "crop"),
                   default="stretch")
    p.add_argument("--pad-color", default="black")
    p.add_argument("--simulation-pixel-budget", type=int, default=262144)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--detail", choices=("compact", "standard"), default="compact")

    p = sub.add_parser("vision-evaluate", help="评估 KV260 输出图像")
    p.add_argument("session"); p.add_argument("output")
    p.add_argument("--detail", choices=("compact", "standard"), default="compact")

    p = sub.add_parser("vision-review", help="打开 KV260 视觉人工确认界面")
    p.add_argument("session"); p.add_argument("--port", type=int, default=8765)

    p = sub.add_parser("kv260-board", help="KV260 实板预检或融合部署/运行/回收")
    p.add_argument("host", nargs="?", default=""); p.add_argument("--session", default="")
    p.add_argument("--action", choices=(
        "host_inspect", "inspect", "run", "dpu_inspect", "dpu_run",
    ), default="inspect")
    p.add_argument("--backend", choices=("board", "loopback", "sw_emu", "hw_emu"),
                   default="board")
    p.add_argument("--user", default="ubuntu"); p.add_argument("--port", type=int, default=22)
    p.add_argument("--ssh-key", default=""); p.add_argument("--app-name", default="")
    p.add_argument("--load-app", action="store_true")
    p.add_argument("--unload-active", action="store_true")
    p.add_argument("--host-executable", default=""); p.add_argument("--xclbin", default="")
    p.add_argument("--run-arg", action="append", dest="run_args")
    p.add_argument("--output-name", default="output.bin")
    p.add_argument("--output-layout",
                   choices=("axis_packed", "rgb24", "bgr24", "gray8", "image"),
                   default="axis_packed")
    p.add_argument("--stride-bytes", type=int, default=0)
    p.add_argument("--model", default="")
    p.add_argument("--dpu-input", default="")
    p.add_argument("--dpu-threads", type=int, default=1)
    p.add_argument("--benchmark-seconds", type=int, default=0)
    p.add_argument("--capture-camera", action="store_true",
                   help="显式单帧捕获，仅board inspect + --detail standard可用；默认不占相机")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--detail", choices=("compact", "standard"), default="compact")

    p = sub.add_parser("vitis-ai", help="融合 Vitis AI 探测/量化/XMODEL 编译/自检")
    p.add_argument("--action", choices=("inspect", "analyze", "quantize", "compile", "pipeline", "smoke"),
                   default="inspect")
    p.add_argument("--work-dir", default="")
    p.add_argument("--model", default="")
    p.add_argument("--script", default="")
    p.add_argument("--arg", action="append", dest="vai_args")
    p.add_argument("--version", choices=("auto", "3.5", "3.0"), default="auto")
    p.add_argument("--network-name", default="xab_model")
    p.add_argument("--compiler-option", action="append", dest="compiler_options")
    p.add_argument("--cache-input", action="append", dest="cache_inputs")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--force", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--distro", default="Ubuntu")
    p.add_argument("--detail", choices=("compact", "standard"), default="compact")

    p = sub.add_parser("job", help="查询任务")
    p.add_argument("job_id")

    args = ap.parse_args(argv)

    try:
        if args.cmd == "detect":
            _print(core.detect_tools(detail=args.detail))
        elif args.cmd == "serve":
            from .server import main as serve_main
            serve_main()
        elif args.cmd == "config":
            _print(core.set_tool_paths(
                args.vivado, args.vitis, args.license, args.vivado_launcher,
                args.reset, args.version,
            ))
        elif args.cmd == "create":
            _print(core.create_project(args.directory, args.name, args.part))
        elif args.cmd == "sim":
            _print(core.simulate(args.xpr, args.tb, args.runtime, args.mode))
        elif args.cmd == "elaborate":
            _print(core.elaborate(args.xpr))
        elif args.cmd == "build":
            _print(core.build_all(args.xpr, wait=args.wait))
        elif args.cmd == "devices":
            _print(core.hw_list())
        elif args.cmd == "program":
            _print(core.program_fpga(args.bit, args.ltx))
        elif args.cmd == "ila":
            _print(core.ila_capture(args.ltx, args.bit, args.depth))
        elif args.cmd == "mcs":
            _print(core.generate_mcs(args.bit, args.out, args.size, args.interface))
        elif args.cmd == "flash":
            _print(core.program_flash(args.mcs, args.part))
        elif args.cmd == "vision-prepare":
            _print(core.kv260_vision_prepare(
                args.session, args.input, args.golden, args.format, args.ppc,
                overwrite=args.overwrite, detail=args.detail,
            ))
        elif args.cmd == "video-prepare":
            _print(core.vivado_video_prepare(
                args.session, args.input, args.golden, args.format, args.ppc,
                args.sample_fps, args.max_frames, args.start_time,
                args.width, args.height, args.resize_mode, args.pad_color,
                args.overwrite, args.detail,
                simulation_pixel_budget=args.simulation_pixel_budget,
            ))
        elif args.cmd == "vision-evaluate":
            _print(core.kv260_vision_evaluate(
                args.session, args.output, detail=args.detail
            ))
        elif args.cmd == "vision-review":
            _print(core.kv260_vision_evaluate(
                args.session, launch_ui=True, port=args.port
            ))
        elif args.cmd == "kv260-board":
            _print(core.kv260_board_flow(
                args.host, args.session, args.action, args.user, args.port,
                args.ssh_key, args.app_name, args.load_app,
                args.unload_active, args.host_executable, args.xclbin,
                args.run_args, args.output_name, args.output_layout,
                args.stride_bytes, args.timeout, args.detail, args.backend,
                args.model, args.dpu_input, args.dpu_threads,
                args.benchmark_seconds, args.repeat, args.capture_camera,
            ))
        elif args.cmd == "vitis-ai":
            _print(core.vitis_ai_flow(
                args.action, args.work_dir, args.model, args.script,
                args.vai_args, args.version, args.network_name,
                args.compiler_options, args.timeout, args.force,
                args.detail, args.distro, args.refresh,
                args.cache_inputs,
                not args.no_wait,
            ))
        elif args.cmd == "job":
            _print(core.job_status(args.job_id))
    except core.BridgeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
