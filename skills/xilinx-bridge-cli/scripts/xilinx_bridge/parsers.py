"""解析 Vivado / xsim 日志与报告，给 Agent 返回结构化摘要而不是几万行原始日志。"""
from __future__ import annotations

import re


_FAILURE_SIGNATURES = (
    {
        "category": "vivado_systemc_netlist_crash",
        "pattern": r"(?:unexpected error has occurred \(11\)|SIGSEGV)[\s\S]{0,1500}HRSBSysCInst::getSignalInterface",
        "message": "Vivado 在平台 SystemC 接口网表生成阶段崩溃",
        "action": "保留 CrashLog 和已完成 XO；核对平台仿真模型及同版本工具。不要重编算法、重装许可证或把 sw_emu 当成 hw_emu 通过。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "simulation_module_missing",
        "pattern": r"\[VRFC 10-2063\][^\r\n]{0,400}Module\s*<[^>]+>\s*not found",
        "message": "仿真展开缺少被实例化模块或其仿真模型",
        "action": "检查首个 VRFC 10-2063 的模块、IP 仿真支持和编译顺序；不要用空壳模块伪造通过。VCU 缺模型时，关闭混合 SystemC 不能视为完整修复。",
        "scope": "simulation", "retryable": False,
    },
    {
        "category": "vitis_linker_crash",
        "pattern": r"\[VPL 60-777\][^\r\n]{0,300}terminated unexpectedly",
        "message": "Vitis 平台链接的厂商子进程异常退出",
        "action": "读取本次 _x/logs/link/CrashLog 中的崩溃栈再定位；复用已完成 XO，禁止无变化地反复链接。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "linux_locale_missing",
        "pattern": r"locale::facet::_S_create_c_locale name not valid",
        "message": "Xilinx Linux 工具需要的 locale 尚未生成",
        "action": "在工具所在 Linux 环境生成 en_US.UTF-8 后重试；不要修改算法或重装整套工具。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "linux_tinfo_missing",
        "pattern": r"libtinfo\.so\.5: cannot open shared object file",
        "message": "Linux 工具缺少 libtinfo.so.5",
        "action": "安装与当前发行版匹配的 libtinfo5 兼容包；不要把新版库强制软链接为旧 ABI。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "hls_zip_missing",
        "pattern": r'''couldn't execute "zip": no such file or directory''',
        "message": "HLS 报告归档所需 zip 不存在，可能导致仿真收尾 FAIL",
        "action": "在 HLS 所在 Linux 环境安装 zip 后复测原流程，不能仅凭像素 PASS 跳过 cosim 门禁。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "linux_toolchain_sysroot",
        "pattern": r"fatal error: sys/cdefs\.h: No such file|cannot find crt[1in]\.o:",
        "message": "Linux 编译器缺少系统开发文件或 multiarch 搜索路径",
        "action": "检查发行版开发包和匹配架构路径；Ubuntu 20.04 HLS 2022.1 实测需 libc6-dev-i386，并按任务设置 LIBRARY_PATH=/usr/lib/x86_64-linux-gnu。勿全局替换库。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "windows_launcher_denied",
        "pattern": r"rundef\.js[\s\S]{0,500}?Access denied",
        "message": "Vivado 派生 run 的 rundef.js 被 Windows Script Host/权限策略拒绝",
        "action": "修复宿主脚本执行权限后重跑；当前不是 HDL 质量结论。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "windows_spawn_failed",
        "pattern": r"ERROR:\s*\[Common 17-180\]\s*Spawn failed",
        "message": "Vivado 无法启动生成的 Windows 子流程",
        "action": "确认安装环境和残留进程后只重试一次；重复失败时检查子进程日志。",
        "scope": "infrastructure", "retryable": True,
    },
    {
        "category": "srcscanner_environment",
        "pattern": r"\[filemgmt 56-218\].{0,200}srcscanner",
        "message": "Vivado srcscanner 未能建立工程源文件 design graph",
        "action": "若当前使用 unwrapped 启动器，切回 vivado_launcher='wrapper'；wrapper 仍失败时修复对应版本安装，勿把它误判为 HDL 错误。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "runtime_dll_missing",
        "pattern": r"librdi_commontasks\.dll|0xc0000135|-1073741515",
        "message": "Xilinx 启动环境缺少运行库/DLL",
        "action": "Windows 可设 xilinx_config_set(vivado_launcher='unwrapped') 由 bridge 注入完整环境；不要手工拼 PATH。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "unconstrained_io",
        "pattern": r"\[DRC (?:NSTD-1|UCIO-1)\]",
        "message": "顶层端口缺少板级 I/O 标准或引脚位置约束",
        "action": "补充与目标板原理图一致的 IOSTANDARD/LOC；不要降低 NSTD-1/UCIO-1 严重级别来伪造 bitstream。",
        "scope": "constraints", "retryable": False,
    },
    {
        "category": "xsdb_api_drift",
        "pattern": r"(?:invalid|illegal|unknown).{0,80}(?:print\s+-x|-x)|invalid command name.{0,40}print",
        "message": "当前 XSDB/XSCT 不支持旧版调试命令语法",
        "action": "按检测到的 debug_backend 使用当前版命令；不要原样重放旧 XSCT 脚本。",
        "scope": "debug", "retryable": False,
    },
    {
        "category": "jtag_ap_timeout",
        "pattern": r"(?:JTAG\s+AP|AP transaction|DAP transaction)[^\r\n]{0,160}(?:timeout|timed out)",
        "message": "JTAG AP 事务超时，目标可能处于部分初始化或被其他会话占用",
        "action": "先关闭并发硬件客户端、检查目标状态；必要时冷启动板卡后再运行一次。",
        "scope": "hardware", "retryable": False,
    },
    {
        "category": "hw_server_version_mismatch",
        "pattern": r"Cannot support (?:older|newer) hw_server version|hw_server version[^\r\n]{0,100}(?:incompatible|mismatch)",
        "message": "Vivado 与已运行的 hw_server 版本不兼容",
        "action": "连接所选 Vivado 同版本 hw_server；本地自动启动的旧服务退出后再试，或使用同版本独立端口。不要终止其他任务的硬件服务。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "debug_core_path_too_long",
        "pattern": r"\[Chipscope 16-302\][\s\S]{0,800}temporary directory path[\s\S]{0,300}(?:exceed|longer than)",
        "message": "Vivado 调试核临时路径超过 Windows Chipscope 路径上限",
        "action": "把工程或 Vivado 工作目录映射/移动到更短的绝对路径后重跑实现；无需修改 HDL 或 ILA 时钟。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "debug_core_generation_failed",
        "pattern": r"\[Chipscope 16-(?:330|331|338)\]",
        "message": "Vivado 调试核生成/综合失败，尚非实板时钟故障",
        "action": "读取 dbg_hub/xsdbm 子进程的首个错误，检查路径和工具环境；不要据此修改板端时钟。",
        "scope": "infrastructure", "retryable": False,
    },
    {
        "category": "debug_hub_clock_stopped",
        "pattern": r"debug hub.{0,120}(?:clock|not detected|not found)",
        "message": "ILA debug hub 不可见或其时钟已停止",
        "action": "确认应用未关闭 PL/debug 时钟，且 BIT/LTX 匹配；在资源清理前抓取。",
        "scope": "hardware", "retryable": False,
    },
    {
        "category": "no_debug_target",
        "pattern": r"no (?:JTAG |hw_)?targets?|no targets found|(?:debug|JTAG|hw)\s+target[^\r\n]{0,80}not found",
        "message": "未发现可用 JTAG/调试目标",
        "action": "先运行 hw_list_devices，检查线缆、驱动、供电和 hw_server，再下载 ELF。",
        "scope": "hardware", "retryable": True,
    },
    {
        "category": "workspace_locked",
        "pattern": r"workspace.{0,100}(?:locked|in use)|cannot lock workspace",
        "message": "Vitis workspace 正被其他进程占用",
        "action": "关闭残留 Vitis/Java/Python 构建进程或等待 owner Job，勿并行重建同一 workspace。",
        "scope": "workspace", "retryable": True,
    },
    {
        "category": "bsp_link_missing",
        "pattern": r"cannot find\s+-l(?:xil|metal|lwip)|undefined reference.{0,120}(?:Xil_|xil_|metal_|lwip)",
        "message": "应用链接未正确带入 BSP 库或其搜索路径",
        "action": "核对应用绑定的平台/domain和 BSP lib 路径；不要只因库文件存在就判定配置正确。",
        "scope": "vitis", "retryable": False,
    },
    {
        "category": "stale_generated_wrapper",
        "pattern": r"(?:module|port).{0,120}(?:not found|does not exist|has no port).{0,120}(?:wrapper|design_1_wrapper)|design_1_wrapper.{0,160}(?:port|stale)",
        "message": "Block Design 与自动生成 wrapper 可能不同步",
        "action": "重新生成/同步 BD wrapper 与 compile order，再做增量综合。",
        "scope": "vivado", "retryable": False,
    },
    {
        "category": "xilinx_property_drift",
        "pattern": r"(?:Invalid property name|No property named|Property[^\r\n]{0,80}does not exist)[^\r\n]{0,160}(?:PROGRAM|PROBES|CONTROL|TRIGGER|hw_ila)|hw_ila property[^\r\n]{0,160}is read-only",
        "message": "Vivado/ILA 属性名与当前工具版本不兼容",
        "action": "先检查 WB_OK/产物确认已完成阶段，再用 list_property 适配当前版本；勿因冗余查询重跑实现。",
        "scope": "vivado", "retryable": False,
    },
    {
        "category": "artifact_export_after_bitstream",
        "pattern": r"write_bitstream completed successfully[\s\S]{0,4000}(?:ERROR:|WB_ERROR\|).{0,300}(?:write_hw_platform|export|\.xsa|property)",
        "message": "比特流已完成，但后续硬件平台导出步骤失败",
        "action": "复用现有 BIT/DCP，只重跑 XSA/export 步骤，避免重新综合实现。",
        "scope": "vivado", "retryable": False, "partial_success": True,
    },
    {
        "category": "missing_project_reference",
        "pattern": r"(?:Could not find file|No such file or directory|cannot open file).{0,240}(?:\.xsa|\.xpfm|\.xdc|\.vhd?|\.sv|\.xci)|(?:\.xsa|\.xpfm|\.xdc|\.vhd?|\.sv|\.xci)[^\r\n]{0,160}(?:does not exist|not found)",
        "message": "工程引用的硬件描述、平台或源文件不存在",
        "action": "审计复制后的绝对/相对路径并重新绑定 XSA/XPFM/源文件，不要盲目重跑。",
        "scope": "workspace", "retryable": False,
    },
)


def classify_failure(text: str) -> dict | None:
    """Return one bounded, actionable failure fingerprint from a tool log."""
    for signature in _FAILURE_SIGNATURES:
        if re.search(signature["pattern"], text, re.I):
            return {key: value for key, value in signature.items() if key != "pattern"}
    return None


def detect_infrastructure_failure(text: str) -> str | None:
    """识别父 Vivado 可能继续等待、但派生启动器已经失败的宿主级错误。"""
    signature = classify_failure(text)
    if signature and signature.get("scope") == "infrastructure":
        return str(signature["message"])
    return None


def scan_log(text: str, max_items: int = 30) -> dict:
    """从任意 Vivado 日志中提取 ERROR / CRITICAL WARNING / WARNING。"""
    errors, crits = [], []
    warn_count = 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("ERROR:"):
            errors.append(s[:500])
        elif s.startswith("CRITICAL WARNING:"):
            crits.append(s[:500])
        elif s.startswith("WARNING:"):
            warn_count += 1
    return {
        "error_count": len(errors),
        "errors": errors[:max_items],
        "critical_warning_count": len(crits),
        "critical_warnings": crits[:max_items],
        "warning_count": warn_count,
    }


def wb_markers(text: str) -> dict:
    """提取 TCL 脚本打印的 WB_<TAG>|... 结构化标记行。"""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("WB_"):
            parts = s.split("|")
            tag = parts[0][3:]
            out.setdefault(tag, []).append("|".join(parts[1:]))
    return out


# ---------------------------------------------------------------------------
# report_timing_summary 文本解析
# ---------------------------------------------------------------------------
_TIMING_KEYS = {
    "WNS": r"WNS\(ns\)",
    "TNS": r"TNS\(ns\)",
    "WHS": r"WHS\(ns\)",
    "THS": r"THS\(ns\)",
    "TPWS": r"TPWS\(ns\)",
    "TNS_Failing_Endpoints": r"TNS Failing Endpoints",
    "WNS_Failing_Endpoints": r"WNS Failing Endpoints",
    "Total_Endpoints": r"Total Endpoints",
}


def _is_dash_row(s: str) -> bool:
    return bool(s.strip()) and set(s.strip()) <= set("- ")


def _parse_table_block(lines: list[str], start: int, headers: list[str],
                       max_look: int = 5) -> dict:
    """从表头行之后找第一条纯数值行, 与 headers 对齐。"""
    for j in range(start + 1, min(start + max_look, len(lines))):
        row = lines[j].strip()
        if _is_dash_row(row) or not row:
            continue
        vals = row.split()
        numeric = all(re.match(r"^[-+0-9.]+$", v) for v in vals)
        if len(vals) >= len(headers) and numeric:
            out = {}
            for h, v in zip(headers, vals):
                try:
                    out[h] = float(v)
                except ValueError:
                    out[h] = v
            return out
        break
    return {}


_TIMING_COLS = [
    "WNS(ns)", "TNS(ns)", "TNS Failing Endpoints", "TNS Total Endpoints",
    "WHS(ns)", "THS(ns)", "THS Failing Endpoints", "THS Total Endpoints",
    "WPWS(ns)",
    "TPWS(ns)", "TPWS Failing Endpoints", "TPWS Total Endpoints",
]


def parse_timing_summary(text: str) -> dict:
    result: dict = {"design": {}, "clocks": []}
    lines = text.splitlines()
    in_clock_section = False
    for i, line in enumerate(lines):
        s = line.strip()
        # Design Timing Summary 主表: 按已知列名在表头行中出现的顺序对齐数值
        if "WNS(ns)" in s and "TNS(ns)" in s and not in_clock_section:
            cols = [c for c in _TIMING_COLS if c in s]
            block = _parse_table_block(lines, i, [str(k) for k in range(len(cols))])
            for col, val in zip(cols, block.values()):
                result["design"][col] = val
        # Clock Summary 区块
        if "Clock Summary" in s:
            in_clock_section = True
            continue
        if in_clock_section:
            if s.startswith(("WNS(ns)", "-----")) or _is_dash_row(s) or not s:
                if s and not _is_dash_row(s) and "WNS" in s:
                    pass
                continue
            if s.startswith(("All user", "Intra Clock", "Inter Clock")):
                in_clock_section = False
                continue
            parts = s.split()
            # 时钟行: 名字 + 8 个数值 (WNS TNS TNS_fail TNS_total WHS THS THS_fail THS_total)
            if len(parts) >= 9 and re.match(r"^[-+0-9.]+$", parts[1]):
                try:
                    result["clocks"].append(
                        {
                            "clock": parts[0],
                            "WNS": float(parts[1]),
                            "TNS": float(parts[2]),
                            "TNS_failing": int(float(parts[3])),
                            "TNS_total": int(float(parts[4])),
                            "WHS": float(parts[5]),
                            "THS": float(parts[6]),
                        }
                    )
                except ValueError:
                    pass
    # 兜底：主表没解析到时逐个找 "WNS(ns)  0.123"
    if not result["design"]:
        for name, pat in _TIMING_KEYS.items():
            mm = re.search(pat + r"\s+([-+0-9]*\d[-+0-9.]*)\s", text)
            if mm:
                try:
                    result["design"][name] = float(mm.group(1))
                except ValueError:
                    pass
    d = result["design"]
    checked = []
    for key in ("WNS(ns)", "TNS(ns)", "WHS(ns)", "THS(ns)",
                "WPWS(ns)", "TPWS(ns)"):
        if key in d and isinstance(d[key], (int, float)):
            checked.append(d[key] >= 0)
    for key in ("TNS Failing Endpoints", "THS Failing Endpoints",
                "TPWS Failing Endpoints"):
        if key in d and isinstance(d[key], (int, float)):
            checked.append(d[key] == 0)
    result["timing_met"] = None if not checked else all(checked)
    return result


# ---------------------------------------------------------------------------
# report_utilization 文本解析: 取 Slice 级核心资源
# ---------------------------------------------------------------------------
_KEEP = (
    "Slice LUTs", "Slice Registers", "Slice", "LUT as Logic", "LUT as Memory",
    "Block RAM Tile", "URAM", "DSPs", "Bonded IOB", "BUFGCE", "MMCM", "PLL",
    "GTHE", "GTYE", "IBUFDS", "Registers",
)

_REPORT_ZERO = {
    "Slice LUTs", "LUT as Logic", "LUT as Memory", "Slice Registers",
    "Block RAM Tile", "URAM", "DSPs", "Bonded IOB",
}


def parse_utilization(text: str) -> dict:
    resources = []
    seen_resources = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        name = cells[0].rstrip("*").strip()
        used, avail, pct = cells[1], cells[-2], cells[-1]
        if not re.fullmatch(r"\d+(?:\.\d+)?", used):
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)?", avail):
            continue
        if any(name.startswith(k) for k in _KEEP):
            if name in seen_resources:
                continue
            used_num = float(used)
            avail_num = float(avail)
            # Zero BRAM/DSP/etc. is meaningful. Zero clocks, transceivers, and
            # every optional primitive are noise unless actually instantiated.
            if used_num == 0 and name not in _REPORT_ZERO:
                continue
            seen_resources.add(name)
            resources.append(
                {
                    "resource": name,
                    "used": int(used_num) if used_num.is_integer() else used_num,
                    "available": int(avail_num) if avail_num.is_integer() else avail_num,
                    "util_pct": pct,
                }
            )
    return {"resources": resources}


def parse_drc_report(text: str) -> dict:
    """Extract the compact DRC summary table without verbose rule details."""
    violations = []
    for line in text.splitlines():
        match = re.match(
            r"^\|\s*([A-Za-z0-9_-]+)\s*\|\s*(Critical Warning|Warning|Error|Info)\s*\|\s*(.*?)\s*\|\s*(\d+)\s*\|$",
            line.strip(),
        )
        if match:
            violations.append({
                "rule": match.group(1),
                "severity": match.group(2),
                "description": match.group(3),
                "checks": int(match.group(4)),
            })
    found = re.search(r"Checks found:\s*(\d+)", text)
    blocking_rules = [
        violation["rule"] for violation in violations
        if violation["severity"] == "Error" or violation["rule"] in {"NSTD-1", "UCIO-1"}
    ]
    return {
        "checks_found": int(found.group(1)) if found else sum(v["checks"] for v in violations),
        "critical_warning_count": sum(v["checks"] for v in violations if v["severity"] == "Critical Warning"),
        "warning_count": sum(v["checks"] for v in violations if v["severity"] == "Warning"),
        "error_count": sum(v["checks"] for v in violations if v["severity"] == "Error"),
        "bitstream_ready": not blocking_rules,
        "blocking_rules": blocking_rules,
        "violations": violations,
    }


def parse_route_status(text: str) -> dict:
    """Return routed-net counts and an explicit completion verdict."""
    def count(label: str):
        match = re.search(rf"# of {label}[^:]*:\s*(\d+)\s*:", text, re.IGNORECASE)
        return int(match.group(1)) if match else None

    logical = count("logical nets")
    routable = count("routable nets")
    routed = count("fully routed nets")
    errors = count("nets with routing errors")
    fully_routed = None
    if routable is not None and routed is not None and errors is not None:
        fully_routed = routed == routable and errors == 0
    return {
        "logical_nets": logical,
        "routable_nets": routable,
        "fully_routed_nets": routed,
        "routing_error_nets": errors,
        "fully_routed": fully_routed,
    }


# ---------------------------------------------------------------------------
# xsim 仿真日志
# ---------------------------------------------------------------------------
def parse_xsim_log(text: str) -> dict:
    errors = [
        l.strip()[:400]
        for l in text.splitlines()
        if re.match(r"\s*(Error|ERROR|Fatal)", l)
    ]
    finished = "$finish" in text or "run: Time" in text or "$stop" in text
    # 常见测试台通过/失败标志
    passed = bool(re.search(
        r"(WB_PASS\||TEST\s*(PASSED|PASS)|TESTBENCH\s*OK|SIMULATION\s*PASS)",
        text,
        re.I,
    ))
    failed = bool(re.search(
        r"(WB_FAIL\||FAIL_DETAIL|TEST\s*FAILED|SIMULATION\s*FAIL|MISMATCH)",
        text,
        re.I,
    ))
    verdict = "unknown"
    if errors or failed:
        verdict = "fail"
    elif passed:
        verdict = "pass"
    return {
        "verdict": verdict,
        "error_count": len(errors),
        "errors": errors[:30],
        "simulation_finished": finished,
    }
