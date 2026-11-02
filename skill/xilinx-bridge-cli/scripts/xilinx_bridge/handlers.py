"""Bridge tool signatures shared by the direct CLI and optional MCP transport."""
from __future__ import annotations

import json
from typing import Literal

from . import core


TOOLS = {}


def expose(function):
    TOOLS[function.__name__] = function
    return function


def _j(fn, *args, **kwargs) -> str:
    """统一包装: 返回紧凑 JSON，减少 Agent 上下文占用。"""
    try:
        return json.dumps(
            fn(*args, **kwargs), ensure_ascii=False, separators=(",", ":"), default=str
        )
    except core.BridgeError as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False)


# ============================ 环境 ============================
@expose
def xilinx_detect(refresh: bool = False, detail: str = "compact") -> str:
    """快速探测 Vivado/Vitis 与许可证候选来源，不启动工具校验许可证。
    默认 compact 只返回所选版本/能力布尔值，减少 token；诊断所有安装与完整可执行路径时
    传 detail="standard"。结果缓存 60 秒；安装或环境刚变化时传 refresh=true。"""
    return _j(core.detect_tools, refresh, detail)


@expose
def xilinx_config_set(vivado_path: str = "", vitis_path: str = "",
                      license_file: str = "", vivado_launcher: str = "",
                      reset: bool = False, version: str = "") -> str:
    """手动指定 Vivado/Vitis 安装根目录（自动探测失败时使用）。
    最短方式是 version="2022.2"/"2026.1"，bridge 会把同版本 Vivado/Vitis 原子绑定；
    也可显式传 vivado_path="C:/Xilinx/Vivado/2026.1" 与 vitis_path。
    license_file 是可选的显式覆盖，可传 .lic 路径或 端口@许可证服务器；
    默认目录中的许可证无需重复填写，且返回结果不会泄露许可证值。
    2022.2 工程流程优先 vivado_launcher="auto"/"wrapper"；wrapper 静默失败时才尝试
    unwrapped，且 srcscanner 失败摘要会明确提示切回 wrapper 或修复安装；
    reset=true 清除所有持久化覆盖并恢复自动探测。"""
    return _j(
        core.set_tool_paths, vivado_path, vitis_path, license_file,
        vivado_launcher, reset, version,
    )


# ============================ KV260 视觉闭环 ============================
@expose
def vivado_video_prepare(session_dir: str, input_video: str,
                         golden_video: str = "", pixel_format: str = "rgb",
                         pixels_per_clock: int = 1, sample_fps: float = 1.0,
                         max_frames: int = 8, start_time: float = 0.0,
                         target_width: int = 0, target_height: int = 0,
                         resize_mode: str = "stretch",
                         pad_color: str = "black",
                         overwrite: bool = False,
                         detail: str = "compact",
                         simulation_pixel_budget: int = 262_144) -> str:
    """把外部 MP4/MOV/AVI/MKV 等视频转换为 Vivado 可直接消费的多帧 AXI4-Stream
    Video 仿真输入。resize_mode 支持 stretch/fit/pad/crop，pad_color 支持命名或RRGGBB；
    主机侧 FFmpeg 只抽取最多 max_frames 帧并缩放；返回 path-only
    image_test、首帧预览和连续 DMA bin，不把像素/码流放进 Agent 上下文。
    golden_video 为空时显式使用 passthrough 期望，适合先验证链路；验证算法时传软件黄金视频。
    默认总像素预算262144；确需大仿真时显式提高 simulation_pixel_budget，最高16777216，
    紧凑结果会标记大预算。不要为绕过门禁修改源码或拆成逐帧xsim。
    把返回的 image_test 补 dut/parameters 后交给一次 vivado_create_project 或
    vivado_simulate。默认 compact；相同内容与参数命中指纹复用。"""
    return _j(
        core.vivado_video_prepare, session_dir, input_video, golden_video,
        pixel_format, pixels_per_clock, sample_fps, max_frames, start_time,
        target_width, target_height, resize_mode, pad_color,
        overwrite, detail, simulation_pixel_budget,
    )


@expose
def kv260_vision_prepare(session_dir: str, input_image: str,
                         golden_image: str = "", pixel_format: str = "rgb",
                         pixels_per_clock: int = 1, thresholds: dict = None,
                         overwrite: bool = False,
                         detail: str = "compact") -> str:
    """创建 KV260 图像 session，不启动 Vivado/Vitis。读取 input/golden 图片，生成与
    image_test 完全同字节序的 AXIS .mem、板端 DMA .bin、KV260/XPFM profile 和紧凑 manifest。
    返回的 image_test 只需补 dut/parameters 即可传现有仿真工具。大图只传路径，不传像素数组。
    默认 detail=compact；仅排障或需要完整 manifest 路径时用 detail=standard。相同输入和参数会复用。"""
    return _j(
        core.kv260_vision_prepare, session_dir, input_image, golden_image,
        pixel_format, pixels_per_clock, thresholds, overwrite, detail,
    )


@expose
def kv260_vision_evaluate(session_dir: str, output_image: str = "",
                          thresholds: dict = None, decision: str = "",
                          reviewer: str = "agent", note: str = "",
                          launch_ui: bool = False, port: int = 8765,
                          open_browser: bool = True,
                          detail: str = "compact") -> str:
    """KV260 输出图像闭环。传 output_image 时计算 MAE/RMSE/PSNR/全局 SSIM/坏点比例、
    参考无关的亮度/对比度/熵并生成差分图；有 golden 时自动 PASS/FAIL。Agent 可同时传
    decision=accept/reject, reviewer=agent；人工确认用 launch_ui=true 打开本地并排/滑块界面。
    不传动作时返回当前 session 状态。默认 detail=compact，只返回判定和必要证据；仅排障时用
    detail=standard。相同输出和阈值会命中 SHA-256 缓存，不重复解码与计算。"""
    return _j(
        core.kv260_vision_evaluate, session_dir, output_image, thresholds,
        decision, reviewer, note, launch_ui, port, open_browser, detail,
    )


@expose
def kv260_board_flow(host: str = "", session_dir: str = "",
                     action: Literal["host_inspect", "inspect", "run", "dpu_inspect", "dpu_run"] = "inspect",
                     user: str = "ubuntu",
                     port: int = 22, ssh_key: str = "",
                     app_name: str = "", load_app: bool = False,
                     unload_active: bool = False,
                     host_executable: str = "", xclbin: str = "",
                     run_args: list[str] = None,
                     output_name: str = "output.bin",
                     output_layout: str = "axis_packed",
                     stride_bytes: int = 0, timeout: int = 120,
                     detail: str = "compact", backend: str = "board",
                     model: str = "", dpu_input: str = "",
                     dpu_threads: int = 1, benchmark_seconds: int = 0,
                     repeat: int = 1, capture_camera: bool = False) -> str:
    """KV260 Linux/XRT 实板融合流程，只增加板端最后一公里，不重复构建和图像评估。
    action=host_inspect 在没有SD/Linux/SSH时一次只读检查主机FTDI、推荐UART和XCK26 JTAG；
    不枚举、挂载或写入存储设备，并明确返回LINUX_NOT_CHECKED。
    action=inspect 一次 SSH 检查 K26 身份、XRT/xmutil、磁盘及 V4L2/media/GStreamer 节点。
    即使detail=standard也默认不抓帧；仅显式capture_camera=true配合board inspect和standard
    才会打开摄像头作单帧验证。已有相机服务运行时不要请求抓帧；not_requested不代表采集通过。
    action=run 复用 kv260_vision_prepare 的 input.bin，上传现有 Vitis host/xclbin，运行后回收
    output，并把 axis_packed/rgb24/bgr24/gray8 原始帧转换成 PNG 后自动复用现有黄金门禁。
    run_args 是参数数组而非 shell，支持 {xclbin}/{input}/{output}/{width}/{height}/
    {pixel_format}/{pixels_per_clock} 占位符；省略时使用标准 --xclbin/--input/--output 契约。
    load_app=true 才执行 sudo -n xmutil loadapp；不会等待密码。只有明确传
    unload_active=true 才先卸载当前 app。backend=loopback/sw_emu/hw_emu 可在无板卡时验证
    相同 session、buffer contract、host 参数和评价链；默认 board。
    action=dpu_inspect 用一次紧凑调用读取板端 DPU，并在给出 model 时上传到内容寻址缓存、
    解析张量并在运行前精确比较 fingerprint。action=dpu_run 复用缓存执行 XMODEL；dpu_input
    省略时使用确定性全零单样本，repeat>1 校验输出复现性，benchmark_seconds>0 执行有界吞吐
    测试。输出张量保存在 XMODEL 相邻的 .xab-kv260 目录，MCP 只返回形状、哈希、耗时和 FPS。
    物理 KV260 的 Linux/XRT、VART、DPU 推理和 SqueezeNet 吞吐已经验证；显示链路未测试。
    默认 compact，不回传长日志。"""
    return _j(
        core.kv260_board_flow, host, session_dir, action, user, port,
        ssh_key, app_name, load_app, unload_active, host_executable,
        xclbin, run_args, output_name, output_layout, stride_bytes,
        timeout, detail, backend, model, dpu_input, dpu_threads,
        benchmark_seconds, repeat, capture_camera,
    )


@expose
def vitis_ai_flow(action: str = "inspect", work_dir: str = "",
                  model: str = "", script: str = "",
                  args: list[str] | None = None, version: str = "auto",
                  network_name: str = "xab_model",
                  compiler_options: list[str] | None = None,
                  timeout: int = 600, force: bool = False,
                  detail: str = "compact", distro: str = "Ubuntu",
                  refresh: bool = False,
                  cache_inputs: list[str] | None = None,
                  wait: bool = True, arch_json: str = "") -> str:
    """融合调用 WSL Vitis AI，避免 Agent 拼接 Conda/编译器命令并读取长日志。

    action=inspect 返回环境能力；analyze 紧凑解析 XMODEL 分区、DPU指纹和shape；action=smoke
    运行真实 NNDCT 小型视觉网络自检；action=quantize 执行已有量化 script，输出目录通过
    XAB_VAI_OUTPUT_DIR 传入；action=compile 编译 model=.xmodel，并自动选择 KV260 DPUCZDX8G
    arch；可用 arch_json 指定本机架构JSON，内容参与缓存键，不能代替板端指纹检查。
    version=auto 默认3.5，bridge选择2022.2时自动3.0。成功只返回紧凑能力/产物/缓存，
    pipeline 在一个MCP调用中量化并编译；cache_inputs 显式列出全部校准依赖后可安全缓存量化。
    完整日志落盘且仅 standard 返回路径；同一 XMODEL/版本/参数命中缓存时零进程启动。
    大模型传 wait=false 立即返回 job_id，再按 poll_after_seconds 用 xilinx_job_status 轮询。
    主机编译本身不证明目标overlay兼容；上板前用kv260_board_flow的dpu_inspect精确比较指纹。"""
    return _j(
        core.vitis_ai_flow, action, work_dir, model, script, args, version,
        network_name, compiler_options, timeout, force, detail, distro, refresh,
        cache_inputs, wait, arch_json,
    )


# ============================ 工程管理 ============================
@expose
def vivado_create_project(directory: str, name: str, part: str,
                          items: list[dict] | None = None,
                          overwrite: bool = False,
                          simulate_after_create: bool = False,
                          tb_top: str = "", runtime: str = "",
                          sim_mode: str = "behavioral",
                          image_test: dict | None = None,
                          waveform: dict | None = None) -> str:
    """创建 Vivado 工程。part 为器件型号, 如 xc7k325tffg900-2 / xczu9eg-ffvb1156-2-e。
    directory 为父目录。新工程优先同时传 items；现有本地文件用
    {"path":"C:/.../top.sv","fileset":"sources_1","top":"top"}，避免传源码内容；
    需要 bridge 写文件时用 {"rel_path":"src/top.sv","content":"..."}。若 simulate_after_create=true，
    建工程、批量加源和仿真只启动一次 Vivado，避免三次冷启动。
    AXI4-Stream 图像 DUT 可传 image_test={dut,width,height,input_pixels,expected_pixels,...}，
    自动生成黄金向量、协议/背压自检 TB，并隐式启用仿真。
    waveform={signals:[层次路径],radix:{路径:signed/hex},start,duration,max_signals,max_transitions,preview} 可在同一会话导出
    VCD、紧凑转换数据、CSV 与 SVG，不额外启动 Vivado。
    默认拒绝覆盖已有同名 .xpr 或内容 item；只有明确确认后才传 overwrite=true。"""
    return _j(
        core.create_project, directory, name, part,
        items=items, overwrite=overwrite, simulate_after_create=simulate_after_create,
        tb_top=tb_top, runtime=runtime, sim_mode=sim_mode, image_test=image_test,
        waveform=waveform,
    )


@expose
def vivado_add_sources(xpr: str, files: list[str], fileset: str = "sources_1",
                       top: str = "") -> str:
    """把已存在的文件加入工程。fileset: sources_1(设计) / constrs_1(约束) / sim_1(仿真)。
    top 非空时同时设为顶层模块名。"""
    return _j(core.add_sources, xpr, files, fileset, top)


@expose
def vivado_write_source(xpr: str, rel_path: str, content: str,
                        fileset: str = "sources_1", top: str = "") -> str:
    """写入 HDL/约束文件内容到工程目录并加入工程（一步完成写代码+添加）。
    rel_path 如 "src/top.v", content 为完整文件内容。"""
    return _j(core.write_source, xpr, rel_path, content, fileset, top)


@expose
def vivado_write_sources(xpr: str, items: list[dict],
                         simulate_after_write: bool = False,
                         tb_top: str = "", runtime: str = "",
                         sim_mode: str = "behavioral",
                         image_test: dict | None = None,
                         waveform: dict | None = None) -> str:
    """批量写入多个 HDL/约束文件并一次性加入工程（只启动一次 Vivado，
    比逐文件调用 vivado_write_source 快 N 倍，写多文件时优先用本工具）。
    items 支持现有文件 {"path":"C:/repo/a.sv",...}，或写入内容
    {"rel_path":"src/a.v","content":"...",...}，每项必须二选一。
    修改后要复测时设 simulate_after_write=true，可把“写入+仿真”合并为一次 Vivado 启动。
    image_test 可在同一次启动中自动复测标准 AXI4-Stream Video DUT。
    waveform 可同会话捕获 VCD 并返回 Agent 可直接读取的转换摘要/CSV/SVG；默认只返回
    8 个信号×16 次转换，需要时再显式扩大。"""
    return _j(
        core.write_sources, xpr, items,
        simulate_after_write=simulate_after_write,
        tb_top=tb_top, runtime=runtime, sim_mode=sim_mode, image_test=image_test,
        waveform=waveform,
    )


@expose
def vivado_list_parts(pattern: str = "*", limit: int = 50) -> str:
    """查询当前 Vivado 安装/许可下可用的器件型号。pattern 如 "xc7a*" / "xczu9eg*" / "xcv*"。
    建工程前不确定 part 是否存在时先查（新版 Vivado 可能移除了部分旧型号）。"""
    return _j(core.list_parts, pattern, limit)


@expose
def vivado_list_sources(xpr: str, audit_only: bool = False,
                        shared_ip_roots: list[str] | None = None,
                        max_files: int = 240, max_issues: int = 12) -> str:
    """列出工程设计/约束/仿真文件、顶层与器件。audit_only=true 零启动只读检查
    保存的 XPR 路径是否逃逸、缺失或含未解析宏；shared_ip_roots 仅允许显式共享 IP
    仓库，不能放行外部 RTL。复杂 IP/BD/Tcl/文件加载依赖返回 review_required，
    不是综合通过或缓存依赖完整性证明。max_files/max_issues 限制审计与返回规模。"""
    return _j(core.list_sources, xpr, audit_only, shared_ip_roots, max_files, max_issues)


# ============================ 仿真与分析 ============================
@expose
def vivado_simulate(xpr: str, tb_top: str = "", runtime: str = "",
                    sim_mode: str = "behavioral", wait: bool = True,
                    image_test: dict | None = None,
                    waveform: dict | None = None) -> str:
    """运行 xsim 仿真。sim_mode: behavioral / post_synth_func / post_synth_timing /
    post_impl_func / post_impl_timing。runtime 如 "10us", 空则用工程默认。
    image_test 非空时自动生成 AXI4-Stream Video 黄金向量与背压自检 TB，
    一次完成加入和仿真。waveform 可选择层次信号并在同一次 xsim 中导出 VCD；返回
    summary.waveform 的转换序列、CSV、SVG 和原始 VCD/WDB 路径。"""
    return _j(core.simulate, xpr, tb_top, runtime, sim_mode, wait,
              image_test=image_test, waveform=waveform)


@expose
def vivado_rtl_analysis(xpr: str, wait: bool = True) -> str:
    """RTL 分析（Elaborate）：检查语法/层次/连接问题, 生成 methodology 与初步资源估计报告。"""
    return _j(core.elaborate, xpr, wait)


# ============================ 构建 ============================
@expose
def vivado_synthesize(xpr: str, jobs: int = 0, wait: bool = False) -> str:
    """运行综合。默认后台执行(wait=false), 返回 job_id, 用 xilinx_job_status 轮询。
    完成后 summary 含 errors / timing(WNS,TNS,timing_met) / utilization。"""
    return _j(core.synthesize, xpr, jobs, wait)


@expose
def vivado_implement(xpr: str, jobs: int = 0, wait: bool = False) -> str:
    """运行实现（place&route, 会自动先跑综合如果需要）。返回 job_id；成功或时序失败
    都尽量返回 timing/utilization/DRC/route_status，避免另开报告 Job 或扫描长日志。"""
    return _j(core.implement, xpr, jobs, wait)


@expose
def vivado_bitstream(xpr: str, jobs: int = 0, wait: bool = False) -> str:
    """生成比特流（自动级联综合+实现）。完成后 summary.artifacts.bit 为 .bit 路径,
    含 ILA 时 artifacts.ltx 为调试探针文件。"""
    return _j(core.bitstream, xpr, jobs, wait)


@expose
def vivado_build_all(xpr: str, jobs: int = 0, wait: bool = False) -> str:
    """一键全流程: 综合 -> 实现 -> 比特流（等同 vivado_bitstream）。推荐后台运行。"""
    return _j(core.build_all, xpr, jobs, wait)


@expose
def vivado_report(xpr: str, run: str = "impl_1") -> str:
    """对已完成的 run (synth_1/impl_1) 重新生成时序/利用率报告并返回解析结果。"""
    return _j(core.report, xpr, run)


@expose
def vivado_run_tcl(tcl_code: str, wait: bool = True, timeout: int = 3600) -> str:
    """逃生舱: 在 Vivado batch 模式运行任意 TCL 代码（用于本服务未覆盖的操作,
    如 IP 生成、约束检查、自定义报告等）。"""
    return _j(core.run_raw_tcl, tcl_code, "raw_tcl", wait, timeout)


# ============================ 硬件: JTAG / ILA / FLASH ============================
@expose
def hw_list_devices(server: str = "") -> str:
    """扫描 JTAG 下载线与 FPGA 器件。返回 WB_TARGET/WB_DEVICE 列表。
    失败提示通常是 hw_server 未启动或线缆未连接。"""
    return _j(core.hw_list, server)


@expose
def hw_program_fpga(bit_file: str, ltx_file: str = "", target_index: int = 0,
                    device_index: int = -1, server: str = "",
                    target_name: str = "", device_name: str = "", device_part: str = "") -> str:
    """通过 JTAG 把比特流下载到 FPGA。ltx_file 为 ILA 探针文件(可选)。
    device_index=-1 默认自动选择唯一可编程 FPGA；显式索引对应 hw_list_devices。
    K26运行Linux时不要用裸JTAG做在线重配置，已有OS卡死记录；优先xmutil/FPGA Manager。"""
    return _j(core.program_fpga, bit_file, ltx_file, target_index, device_index, server,
              target_name=target_name, device_name=device_name, device_part=device_part)


@expose
def hw_ila_capture(ltx_file: str, bit_file: str = "", depth: int = 1024,
                   trigger: dict = None, ila_index: int = 0,
                   target_index: int = 0, device_index: int = -1,
                   timeout_s: int = 60, server: str = "",
                   trigger_position: int = 0, target_name: str = "", device_name: str = "", device_part: str = "") -> str:
    """抓取 ILA 波形: 可选先下载 bit_file, 然后布防触发并导出 CSV。
    trigger: {"probe名": "比较值"}, 如 {"led": "1'b1"}; 裸值自动按相等比较，
    也可显式写 eq/ne/lt/le/gt/ge；空则立即触发。trigger_position 控制触发前样本数。
    device_index=-1 默认自动选择唯一可编程 FPGA；返回 CSV、真实采样数和触发状态。
    已加载匹配BIT/LTX时省略bit_file；K26裸JTAG重编程可能使运行中Linux卡死。"""
    return _j(core.ila_capture, ltx_file, bit_file, depth, trigger, ila_index,
              target_index, device_index, timeout_s, server,
              trigger_position=trigger_position, target_name=target_name, device_name=device_name, device_part=device_part)


@expose
def hw_generate_mcs(bit_file: str, out_mcs: str, size_mb: int = 16,
                    interface: str = "SPIx1") -> str:
    """把 .bit 转成 FLASH 烧录用 .mcs。size_mb 为 FLASH 容量(MB),
    interface 必须匹配生成 bit 时的 BITSTREAM.Config.SPI_buswidth；Vivado 默认
    通常是 SPIx1，也可显式选择 SPIx2/SPIx4/SPIx8/BPIx16。"""
    return _j(core.generate_mcs, bit_file, out_mcs, size_mb, interface)


@expose
def hw_program_flash(mcs_file: str, flash_part: str, target_index: int = 0,
                     device_index: int = -1, erase: bool = True,
                     verify: bool = True, server: str = "",
                     target_name: str = "", device_name: str = "", device_part: str = "") -> str:
    """通过 JTAG 间接烧写配置 FLASH。flash_part 如 mt25qu128-spi-x1_x2_x4 /
    mx25l25645g-spi-x1_x2_x4 等(可用 vivado_run_tcl 执行 get_cfgmem_parts 查询)。"""
    return _j(core.program_flash, mcs_file, flash_part, target_index,
              device_index, erase, verify, server, target_name=target_name, device_name=device_name, device_part=device_part)


# ============================ Vitis ============================
@expose
def vitis_create_platform(workspace: str, name: str, xsa: str,
                          os_name: str = "standalone", cpu: str = "") -> str:
    """从 XSA 硬件描述创建并编译 Vitis 平台组件。os_name: standalone/freertos/linux。"""
    return _j(core.vitis_create_platform, workspace, name, xsa, os_name, cpu)


@expose
def vitis_create_app(workspace: str, name: str, platform: str,
                     template: str = "hello_world", domain: str = "") -> str:
    """创建并编译 Vitis 应用。platform 为平台名或 platform.json 路径,
    template 如 hello_world / empty_application / lwip_echo_server；多 domain 平台用
    domain 明确选择 standalone_a53_0 等 domain。1.0 已移除从未生效的 lang 参数。"""
    return _j(core.vitis_create_app, workspace, name, platform, template, domain)


@expose
def vitis_build(workspace: str, component: str = "", hls: dict | None = None) -> str:
    """编译普通 Vitis 组件，或用 hls 对象单会话构建 HLS 图像/DSP 核。

    普通模式传 component。HLS 模式省略 component，传
    {name,top,part,files:[{rel_path,content,role}],steps:[csim,csynth,cosim,export],
    clock_ns,dataflow,pipeline_ii,budgets}。同一次进程完成所选阶段，并自动检查
    II、DATAFLOW、BRAM_18K、DSP、LUT、FF 与 latency 预算。
    """
    return _j(core.vitis_build, workspace, component, hls=hls)


@expose
def vitis_list_components(workspace: str, audit: bool = True,
                          audit_only: bool = False) -> str:
    """列出 Vitis 工作区组件。audit=true（默认）在同一调用附加有界本地审计：
    识别复制后仍指向原工程的 XPFM/XSA/BSP、缺失 compile_commands 及仅 -O0。
    复制/迁移预检优先用 audit_only=true，完全不启动 Vitis；此时不列组件。"""
    return _j(core.vitis_list, workspace, audit=audit, audit_only=audit_only)


@expose
def vitis_run_elf(elf_file: str, bit_file: str = "", run_ms: int = 10000,
                  xsa_file: str = "", init_tcl: str = "",
                  symbol_checks: dict[str, str] = None,
                  resume_after_checks: bool = True) -> str:
    """自动优先用当前版 XSDB、兼容 XSCT，把 ELF 下载到 PS 运行；可选先配置 PL。
    Zynq 推荐同时传匹配的 xsa_file 与 ps7_init.tcl。symbol_checks 可断言 ELF
    全局变量（如 {"test_pass":"1"}），避免把仅下载成功误报为应用通过。
    与 JTAG/ILA/FLASH 共用 hw_server 锁。"""
    return _j(core.vitis_run_elf, elf_file, bit_file, run_ms, xsa_file, init_tcl,
              symbol_checks, resume_after_checks)


@expose
def vitis_program_flash(boot_bin: str, flash_type: str = "qspi-x4-single",
                        offset: str = "0x0") -> str:
    """烧写 BOOT.BIN 到 QSPI/OSPI FLASH (Zynq/MPSoC 启动镜像)。"""
    return _j(core.vitis_program_flash, boot_bin, flash_type, offset)


# ============================ Job 管理 ============================
@expose
def xilinx_job_status(job_id: str, detail: str = "compact") -> str:
    """查询后台任务状态。detail=compact 默认返回 verdict/门禁/核心资源且省略波形 transitions
    与报告路径；确需完整标准摘要时传 standard。"""
    return _j(core.job_status, job_id, detail)


@expose
def xilinx_job_log(job_id: str, lines: int = 80) -> str:
    """查看任务日志末尾 N 行（排查失败的第一手信息）。"""
    return _j(core.job_log, job_id, lines)


@expose
def xilinx_job_list(limit: int = 10) -> str:
    """列出最近 1..100 个任务，默认 10；不要为找一个已知 job_id 拉取长列表。"""
    return _j(core.job_list, limit)


@expose
def xilinx_job_cancel(job_id: str) -> str:
    """取消正在运行的任务（会杀掉对应 Vivado 进程树）。"""
    return _j(core.job_cancel, job_id)
