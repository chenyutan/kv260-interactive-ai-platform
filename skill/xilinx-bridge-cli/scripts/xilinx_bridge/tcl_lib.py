"""Vivado TCL 脚本模板库。

约定:
- 占位符写作 @NAME@, 由 render() 替换 (避免与 TCL 的 $ 变量冲突)
- 每个脚本通过 WB_<TAG>|<字段> 打印结构化标记, Python 端 parsers.wb_markers() 解析
- 所有路径传入前已由 Python 转为正斜杠并做 TCL 双引号转义 (用 q())
- 出错时打印 WB_ERROR|... 并 exit 1, 让 Job 状态正确反映失败
"""
from __future__ import annotations

import re
from pathlib import Path


def write_script(directory: Path, text: str) -> Path:
    """Keep the payload UTF-8 while vendor Tcl may default to a Windows codepage.

    The tiny ASCII launcher adds no tool process. A relative sibling lookup avoids
    embedding another user path and preserves run.tcl for Job recovery/diagnosis.
    """
    (directory / "run.tcl").write_text(text, encoding="utf-8")
    launcher = directory / "launch.tcl"
    launcher.write_text(
        'source -encoding utf-8 [file join [file dirname [info script]] run.tcl]\n',
        encoding="ascii",
    )
    return launcher


def render(template: str, **kw) -> str:
    missing = re.findall(r"@([A-Z_]+)@", template)
    for name in set(missing):
        if name not in kw:
            raise KeyError(f"TCL 模板缺少参数: {name}")
    out = template
    for k, v in kw.items():
        out = out.replace(f"@{k}@", str(v))
    return out


def q(path: str) -> str:
    """把字符串转成安全的 TCL 双引号字面量。"""
    return '"' + qraw(path) + '"'


def qraw(path: str) -> str:
    """转义后的原始字符串（不含包裹引号），用于嵌入模板中已有的引号内。"""
    s = str(path).replace("\\", "/")
    # Quotes do not shield braces while Tcl parses an enclosing braced script
    # (for example catch/if/foreach). Escape them even inside a quoted literal.
    for ch in ('"', "$", "[", "]", "{", "}"):
        s = s.replace(ch, "\\" + ch)
    return s


COMMON = r"""
proc wb_emit {line} {
    # Vivado 2022.2 can sporadically close the Tcl stdout channel after xsim
    # returns even though the simulation completed. A diagnostic marker must
    # never turn that successful vendor operation into a failed Job.
    if {[catch { puts $line }]} {
        catch { puts stderr $line }
    }
}
proc wb_ok {msg} { wb_emit "WB_OK|$msg" }
proc wb_err {msg} { wb_emit "WB_ERROR|$msg" }
proc wb_info {tag msg} { wb_emit "WB_INFO|$tag|$msg" }
"""


HW_COMMON = COMMON + r"""
proc wb_open_hw_target {target_index {target_name ""}} {
    set targets [get_hw_targets]
    if {[llength $targets] == 0} { error "no_hw_target" }
    if {$target_name ne ""} {
        set matches [lsearch -all -exact $targets $target_name]
        if {[llength $matches] != 1} { error "expected_exactly_one_target name=$target_name" }
        set target_index [lindex $matches 0]
    }
    if {$target_index < 0 || $target_index >= [llength $targets]} {
        error "target_index_out_of_range index=$target_index count=[llength $targets]"
    }
    set target [lindex $targets $target_index]
    open_hw_target $target
    puts "WB_HW_SELECTED|target_index=$target_index|target=$target"
    return $target
}

proc wb_select_programmable_device {device_index {device_name ""} {expected_part ""}} {
    set devs [get_hw_devices]
    if {[llength $devs] == 0} { error "no_hw_device" }
    if {$device_name ne ""} {
        set matches {}
        foreach dev $devs {
            if {[get_property NAME $dev] eq $device_name} { lappend matches $dev }
        }
        if {[llength $matches] != 1} { error "expected_exactly_one_device name=$device_name" }
        set device_index [lsearch -exact $devs [lindex $matches 0]]
    }
    if {$device_index == -1} {
        set candidates {}
        foreach dev $devs {
            if {![catch {get_property PROGRAM.IS_SUPPORTED $dev} supported] && $supported} {
                lappend candidates $dev
            }
        }
        if {[llength $candidates] == 0} {
            error "no_programmable_fpga_device"
        }
        if {[llength $candidates] > 1} {
            set indexes {}
            foreach dev $candidates { lappend indexes [lsearch -exact $devs $dev] }
            error "multiple_programmable_devices indexes=$indexes; pass device_index explicitly"
        }
        set dev [lindex $candidates 0]
        set selected_index [lsearch -exact $devs $dev]
    } else {
        if {$device_index < 0 || $device_index >= [llength $devs]} {
            error "device_index_out_of_range index=$device_index count=[llength $devs]"
        }
        set dev [lindex $devs $device_index]
        set selected_index $device_index
        if {[catch {get_property PROGRAM.IS_SUPPORTED $dev} supported] || !$supported} {
            error "device_not_programmable index=$device_index name=[get_property NAME $dev] part=[get_property PART $dev]"
        }
    }
    set actual_part [get_property PART $dev]
    if {$expected_part ne "" && ![string equal -nocase $actual_part $expected_part]} {
        error "device_part_mismatch expected=$expected_part actual=$actual_part"
    }
    puts "WB_DEVICE_SELECTED|index=$selected_index|name=[get_property NAME $dev]|part=$actual_part"
    return $dev
}

proc wb_warn_k26_os_lifecycle {dev} {
    if {[string match -nocase "xck26*" [get_property PART $dev]]} {
        puts "WB_WARN|k26_os_lifecycle_unmanaged: JTAG programming does not quiesce Linux drivers or manage DT overlays; running Linux may hang. Prefer Linux FPGA Manager/xmutil for live reconfiguration. DONE/ILA do not verify OS health."
    }
}

proc wb_verify_program_done {dev} {
    set props [list_property $dev]
    set checked 0
    foreach prop {
        REGISTER.CONFIG_STATUS.BIT14_DONE_PIN
        REGISTER.CONFIG_STATUS.BIT13_DONE_INTERNAL_SIGNAL_STATUS
    } {
        if {[lsearch -exact $props $prop] >= 0} {
            set value [get_property $prop $dev]
            puts "WB_PROGRAM_STATUS|$prop=$value"
            incr checked
            if {$value ne "1"} { error "fpga_done_low property=$prop value=$value" }
        }
    }
    if {$checked == 0} {
        puts "WB_WARN|DONE status property unavailable for [get_property PART $dev]"
    }
}
"""

# ---------------------------------------------------------------------------
# 工程管理
# ---------------------------------------------------------------------------
CREATE_PROJECT = COMMON + r"""
create_project @NAME@ @DIR@ -part @PART@ @FORCE@
set_property target_language Verilog [current_project]
wb_ok "project=[get_property DIRECTORY [current_project]] part=[get_property PART [current_project]]"
@SOURCE_SETUP@
@FOLLOW_UP@
close_project
exit 0
"""

ADD_SOURCES = COMMON + r"""
open_project @XPR@
@ADD_LINES@
@TOP_LINES@
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
wb_ok "added"
@FOLLOW_UP@
close_project
exit 0
"""

LIST_SOURCES = COMMON + r"""
open_project @XPR@
foreach f [get_files -of_objects [current_fileset]] {
    puts "WB_FILE|src|[file tail $f]|$f"
}
foreach f [get_files -of_objects [get_filesets constrs_1]] {
    puts "WB_FILE|xdc|[file tail $f]|$f"
}
foreach f [get_files -of_objects [get_filesets sim_1]] {
    puts "WB_FILE|sim|[file tail $f]|$f"
}
wb_info top [get_property top [current_fileset]]
wb_info sim_top [get_property top [get_filesets sim_1]]
wb_info part [get_property PART [current_project]]
close_project
exit 0
"""

# ---------------------------------------------------------------------------
# RTL 分析 (Elaborate)
# ---------------------------------------------------------------------------
ELABORATE = COMMON + r"""
open_project @XPR@
if {[catch {synth_design -rtl -name rtl_1} msg]} {
    puts "WB_WARN|project-mode elaborate failed, retry non-project: $msg"
    reset_project
    foreach f [get_files -of_objects [current_fileset]] {
        set ext [string tolower [file extension $f]]
        if {$ext eq ".v" || $ext eq ".sv"} { read_verilog -sv $f }
        if {$ext eq ".vhd"} { read_vhdl $f }
    }
    set top_name [get_property top [current_fileset]]
    set part_name [get_property PART [current_project]]
    if {[catch {synth_design -rtl -top $top_name -part $part_name} msg2]} {
        wb_err "elaborate_failed: $msg2"
        exit 1
    }
}
catch { report_methodology -file "@RPT_DIR@/methodology_rtl.rpt" }
catch { report_utilization -hierarchical -file "@RPT_DIR@/utilization_rtl.rpt" }
wb_ok "elaborated top=[get_property top [current_fileset]]"
exit 0
"""

# ---------------------------------------------------------------------------
# 仿真 (xsim 行为级 / 综合后 / 实现后)
# ---------------------------------------------------------------------------
SIMULATION_STEPS = r"""
set simset [get_filesets sim_1]
if {@TOP@ ne ""} {
    set_property top @TOP@ $simset
}
@RUNTIME_SETUP@
# Selective VCD capture is emitted after launch_simulation when requested. Logging
# the full hierarchy here makes ordinary simulations and WDB files needlessly
# large, especially for image pipelines.
set_property -name {xsim.simulate.log_all_signals} -value {false} -objects $simset
update_compile_order -fileset $simset
set mode_args "-mode behavioral"
if {"@SIM_MODE@" eq "post_synth_func"} { set mode_args "-mode post-synthesis -type functional" }
if {"@SIM_MODE@" eq "post_synth_timing"} { set mode_args "-mode post-synthesis -type timing" }
if {"@SIM_MODE@" eq "post_impl_func"} { set mode_args "-mode post-implementation -type functional" }
if {"@SIM_MODE@" eq "post_impl_timing"} { set mode_args "-mode post-implementation -type timing" }
if {[catch {eval launch_simulation $mode_args} msg]} {
    wb_err "launch_simulation: $msg"
    exit 1
}
@WAVEFORM_STEPS@
catch { close_sim -quiet }
wb_ok "simulation_done"
"""

SIMULATE = COMMON + r"""
open_project @XPR@
""" + SIMULATION_STEPS + r"""
close_project
exit 0
"""

# ---------------------------------------------------------------------------
# 综合 / 实现 / 比特流
# ---------------------------------------------------------------------------
RUN_SYNTH = COMMON + r"""
open_project @XPR@
reset_run synth_1
launch_runs synth_1 -jobs @JOBS@
if {[catch {wait_on_run synth_1} wb_wait_msg]} {
    puts "WB_WARN|wait_on_run synth_1|$wb_wait_msg"
}
set st [get_property STATUS [get_runs synth_1]]
set pr [get_property PROGRESS [get_runs synth_1]]
wb_info synth_status $st
if {$st ne "synth_design Complete!"} {
    wb_err "synthesis_failed status=$st progress=$pr"
    exit 1
}
open_run synth_1
catch { report_utilization -file "@RPT_DIR@/utilization_synth.rpt" }
catch { report_timing_summary -file "@RPT_DIR@/timing_synth.rpt" }
catch { report_methodology -file "@RPT_DIR@/methodology_synth.rpt" }
wb_ok "synthesis_complete"
exit 0
"""

RUN_IMPL = COMMON + r"""
open_project @XPR@
launch_runs impl_1 -jobs @JOBS@
if {[catch {wait_on_run impl_1} wb_wait_msg]} {
    puts "WB_WARN|wait_on_run impl_1|$wb_wait_msg"
}
set st [get_property STATUS [get_runs impl_1]]
wb_info impl_status $st
set wb_impl_ok [expr {
    [string match "*route_design Complete!*" $st] ||
    [string match "*write_bitstream Complete!*" $st]
}]
# A routed checkpoint still exists when Vivado reports "Failed Timing".  Open
# it and return the reports before failing so the Agent can repair the actual
# path without launching another Vivado session or scraping the full log.
if {[catch {open_run impl_1} wb_open_msg]} {
    puts "WB_WARN|cannot_open_impl_run|$wb_open_msg"
} else {
    catch { report_utilization -file "@RPT_DIR@/utilization_impl.rpt" }
    catch { report_timing_summary -file "@RPT_DIR@/timing_impl.rpt" }
    catch { report_drc -file "@RPT_DIR@/drc_impl.rpt" }
    catch { report_methodology -file "@RPT_DIR@/methodology_impl.rpt" }
    catch { report_route_status -file "@RPT_DIR@/route_status.rpt" }
    catch { report_power -file "@RPT_DIR@/power_impl.rpt" }
}
if {!$wb_impl_ok} {
    wb_err "implementation_failed status=$st"
    exit 1
}
wb_ok "implementation_complete"
exit 0
"""

RUN_BITSTREAM = COMMON + r"""
open_project @XPR@
launch_runs impl_1 -to_step write_bitstream -jobs @JOBS@
if {[catch {wait_on_run impl_1} wb_wait_msg]} {
    puts "WB_WARN|wait_on_run impl_1|$wb_wait_msg"
}
set st [get_property STATUS [get_runs impl_1]]
wb_info impl_status $st
if {[catch {open_run impl_1} wb_open_msg]} {
    puts "WB_WARN|cannot_open_impl_run|$wb_open_msg"
} else {
    catch { report_utilization -file "@RPT_DIR@/utilization_impl.rpt" }
    catch { report_timing_summary -file "@RPT_DIR@/timing_impl.rpt" }
    catch { report_drc -file "@RPT_DIR@/drc_impl.rpt" }
    catch { report_route_status -file "@RPT_DIR@/route_status.rpt" }
}
set bits [glob -nocomplain "@RUN_DIR@/*.bit"]
foreach b $bits { puts "WB_ARTIFACT|bit|$b" }
set ltxs [glob -nocomplain "@RUN_DIR@/*.ltx"]
foreach l $ltxs { puts "WB_ARTIFACT|ltx|$l" }
if {![string match "*write_bitstream Complete*" $st]} {
    wb_err "bitstream_failed status=$st"
    exit 1
}
if {[llength $bits] == 0} { wb_err "no bit file produced"; exit 1 }
wb_ok "bitstream_complete"
exit 0
"""

REPORTS = COMMON + r"""
open_project @XPR@
if {[catch {open_run @RUN@} msg]} {
    wb_err "cannot open run @RUN@: $msg"
    exit 1
}
catch { report_utilization -file "@RPT_DIR@/utilization_@RUN@.rpt" }
catch { report_timing_summary -file "@RPT_DIR@/timing_@RUN@.rpt" }
wb_ok "reports_written"
exit 0
"""

# ---------------------------------------------------------------------------
# 硬件: 设备发现 / JTAG 下载 / ILA / FLASH
# ---------------------------------------------------------------------------
HW_LIST = HW_COMMON + r"""
open_hw_manager
if {[catch {connect_hw_server -url @SERVER@} msg]} {
    wb_err "connect_hw_server: $msg (hint: hw_server 未启动, 可运行 hw_server -s tcp::3121)"
    exit 1
}
set targets [get_hw_targets]
if {[llength $targets] == 0} {
    wb_err "no JTAG cable/target found. 检查下载线连接与驱动"
    exit 1
}
foreach t $targets {
    puts "WB_TARGET|$t"
    if {[catch {open_hw_target $t} msg]} {
        puts "WB_TARGET_OPEN_FAIL|$t|$msg"
        continue
    }
    set device_index 0
    foreach d [get_hw_devices] {
        set dn [get_property NAME $d]
        set id [get_property IDCODE $d]
        set part [get_property PART $d]
        set programmable 0
        catch { set programmable [get_property PROGRAM.IS_SUPPORTED $d] }
        puts "WB_DEVICE|index=$device_index|name=$dn|part=$part|programmable=$programmable|idcode=$id|target=$t"
        incr device_index
    }
    close_hw_target $t
}
wb_ok "scan_done"
exit 0
"""

PROGRAM_FPGA = HW_COMMON + r"""
open_hw_manager
if {[catch {connect_hw_server -url @SERVER@} msg]} { wb_err "connect_hw_server: $msg"; exit 1 }
if {[catch {wb_open_hw_target @TIDX@ @TARGET_NAME@} msg]} { wb_err $msg; exit 1 }
if {[catch {set dev [wb_select_programmable_device @DIDX@ @DEVICE_NAME@ @DEVICE_PART@]} msg]} { wb_err $msg; exit 1 }
current_hw_device $dev
set_property PROGRAM.FILE @BIT@ $dev
wb_warn_k26_os_lifecycle $dev
if {@LTX@ ne ""} {
    if {[file exists @LTX@]} {
        set_property PROBES.FILE @LTX@ $dev
        set_property FULL_PROBES.FILE @LTX@ $dev
    } else {
        puts "WB_WARN|ltx not found: @LTX@"
    }
}
if {[catch {program_hw_devices $dev} msg]} {
    wb_err "program_failed: $msg"
    exit 1
}
if {[catch {refresh_hw_device $dev} msg]} { wb_err "refresh_after_program_failed: $msg"; exit 1 }
if {[catch {wb_verify_program_done $dev} msg]} { wb_err $msg; exit 1 }
if {@LTX@ ne ""} {
    set ilas [get_hw_ilas -of_objects $dev]
    if {[llength $ilas] == 0} { wb_err "ltx_loaded_but_no_ila_found"; exit 1 }
    puts "WB_ILA_COUNT|[llength $ilas]"
}
wb_ok "fpga_programmed_and_done device=[get_property NAME $dev]"
exit 0
"""

ILA_CAPTURE = HW_COMMON + r"""
open_hw_manager
if {[catch {connect_hw_server -url @SERVER@} msg]} { wb_err "connect_hw_server: $msg"; exit 1 }
if {[catch {wb_open_hw_target @TIDX@ @TARGET_NAME@} msg]} { wb_err $msg; exit 1 }
if {[catch {set dev [wb_select_programmable_device @DIDX@ @DEVICE_NAME@ @DEVICE_PART@]} msg]} { wb_err $msg; exit 1 }
current_hw_device $dev
if {@BIT@ ne ""} {
    wb_warn_k26_os_lifecycle $dev
    set_property PROGRAM.FILE @BIT@ $dev
    if {@LTX@ ne ""} { set_property PROBES.FILE @LTX@ $dev; set_property FULL_PROBES.FILE @LTX@ $dev }
    if {[catch {program_hw_devices $dev} msg]} { wb_err "program_failed: $msg"; exit 1 }
    if {[catch {refresh_hw_device $dev} msg]} { wb_err "refresh_after_program_failed: $msg"; exit 1 }
    if {[catch {wb_verify_program_done $dev} msg]} { wb_err $msg; exit 1 }
} else {
    if {@LTX@ ne ""} { set_property PROBES.FILE @LTX@ $dev; set_property FULL_PROBES.FILE @LTX@ $dev }
    if {[catch {refresh_hw_device $dev} msg]} { wb_err "refresh_device_failed: $msg"; exit 1 }
}
set ilas [get_hw_ilas -of_objects $dev]
if {[llength $ilas] == 0} {
    wb_err "no ILA core found on device. 确认比特流含 ILA 且 ltx 匹配"
    exit 1
}
if {@ILA_IDX@ >= [llength $ilas]} {
    wb_err "ila_index_out_of_range index=@ILA_IDX@ count=[llength $ilas]"
    exit 1
}
set ila [lindex $ilas @ILA_IDX@]
puts "WB_ILA|[get_property NAME $ila]"
if {[catch {
    set_property CONTROL.DATA_DEPTH @DEPTH@ $ila
    set_property CONTROL.TRIGGER_POSITION @TRIGGER_POSITION@ $ila
    # BASIC requires storage qualification in the IP; continuous capture does not.
    # Skip writes to capability-dependent properties already at the target value.
    # Do not suppress a genuine failure to change a differing value.
    foreach {prop value} {
        CONTROL.CAPTURE_MODE ALWAYS
        CONTROL.TRIGGER_MODE BASIC_ONLY
        CONTROL.TRIGGER_CONDITION AND
    } {
        if {[get_property $prop $ila] ne $value} {
            set_property $prop $value $ila
        }
    }
} msg]} { wb_err "ila_config_failed: $msg"; exit 1 }
@TRIGGER_SETUP@
if {[catch {run_hw_ila @RUN_OPTIONS@ $ila} msg]} { wb_err "arm_trigger: $msg"; exit 1 }
if {[catch {wait_on_hw_ila -timeout @TIMEOUT@ $ila} msg]} {
    wb_err "ila_wait_failed_or_timeout: $msg"
    exit 1
}
puts "WB_ILA_STATUS|capture_complete"
if {[catch {set data [upload_hw_ila_data $ila]} msg]} { wb_err "ila_upload_failed: $msg"; exit 1 }
current_hw_ila_data $data
write_hw_ila_data -csv_file -force @CSV@ $data
write_hw_ila_data -force @ILA_FILE@ $data
puts "WB_ARTIFACT|csv|@CSV_RAW@"
puts "WB_ARTIFACT|ila|@ILA_RAW@"
wb_ok "ila_captured samples=@DEPTH@"
exit 0
"""

GEN_MCS = COMMON + r"""
write_cfgmem -format mcs -size @SIZE@ -interface @IFACE@ \
    -loadbit "up 0x00000000 @BIT_RAW@" -force @OUT@
wb_ok "mcs created"
exit 0
"""

PROGRAM_FLASH = HW_COMMON + r"""
open_hw_manager
if {[catch {connect_hw_server -url @SERVER@} msg]} { wb_err "connect_hw_server: $msg"; exit 1 }
if {[catch {wb_open_hw_target @TIDX@ @TARGET_NAME@} msg]} { wb_err $msg; exit 1 }
if {[catch {set dev [wb_select_programmable_device @DIDX@ @DEVICE_NAME@ @DEVICE_PART@]} msg]} { wb_err $msg; exit 1 }
current_hw_device $dev
set parts [get_cfgmem_parts @FLASH_PART@]
if {[llength $parts] == 0} {
    wb_err "unknown flash part: @FLASH_PART@ (用 get_cfgmem_parts 查支持列表)"
    exit 1
}
create_hw_cfgmem -hw_device $dev [lindex $parts 0]
set cm [current_hw_cfgmem]
set_property PROGRAM.BLANK_CHECK 0 $cm
set_property PROGRAM.ERASE @ERASE@ $cm
set_property PROGRAM.CFG_PROGRAM 1 $cm
set_property PROGRAM.VERIFY @VERIFY@ $cm
set_property PROGRAM.ADDRESS_RANGE {use_file} $cm
set_property PROGRAM.FILES [list @MCS@] $cm
set_property PROGRAM.PRM_FILE {} $cm
set_property PROGRAM.UNUSED_PIN_TERMINATION {pull-none} $cm
if {![get_property PROGRAM.HW_CFGMEM $dev]} {
    set_property PROGRAM.HW_CFGMEM_TYPE [get_property MEM_TYPE [get_property CFGMEM_PART $cm]] $dev
}
if {[catch {program_hw_cfgmem -hw_cfgmem $cm} msg]} {
    wb_err "flash_program_failed: $msg"
    exit 1
}
wb_ok "flash_programmed"
exit 0
"""

# ---------------------------------------------------------------------------
# 通用逃生舱: 跑任意 TCL
# ---------------------------------------------------------------------------
RAW_TCL_HEADER = COMMON


def trigger_setup_tcl(trigger: dict[str, str] | None) -> str:
    """Generate ILA trigger setup, accepting concise values such as ``1'b1``.

    Vivado's native ``TRIGGER_COMPARE_VALUE`` spelling includes an operator
    prefix (for example ``eq1'b1``).  Agents should not have to remember that
    tool-specific detail, so a bare value is treated as equality while an
    explicit eq/ne/lt/le/gt/ge expression is preserved.
    """
    if not trigger:
        return "# no custom trigger: 立即触发 (trigger-now)"
    lines = []
    for probe, value in trigger.items():
        compare_value = value if re.match(r"^(?:eq|ne|lt|le|gt|ge)", value, re.I) else f"eq{value}"
        probe_var = f"wb_probe_{len(lines)}"
        lines.extend([
            f"set {probe_var} [get_hw_probes -quiet {q(probe)} -of_objects $ila]",
            f"if {{[llength ${probe_var}] == 0}} {{ wb_err \"ILA probe not found: {qraw(probe)}\"; exit 1 }}",
            f"if {{[catch {{ set_property TRIGGER_COMPARE_VALUE {q(compare_value)} ${probe_var} }} wb_trigger_msg]}} "
            "{ wb_err \"ILA trigger setup failed: $wb_trigger_msg\"; exit 1 }",
        ])
    lines.append(
        "if {[catch { set_property CONTROL.TRIGGER_CONDITION OR $ila } wb_condition_msg]} "
        "{ wb_err \"ILA trigger condition failed: $wb_condition_msg\"; exit 1 }"
    )
    return "\n".join(lines)
