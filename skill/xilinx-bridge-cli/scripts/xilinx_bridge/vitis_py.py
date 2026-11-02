"""Vitis 2026.1 Python 脚本模板 (vitis -s script.py 运行) 与 xsct 运行脚本。

占位符同样使用 @NAME@ 风格, 由 tcl_lib.render() 替换。
Python 代码中的值用 repr() 注入, TCL 代码中的路径用 tcl_lib.q() 注入。
"""
from __future__ import annotations

VITIS_CLIENT_HEADER = r"""
import sys, traceback
try:
    import vitis
except Exception as e:
    print("WB_ERROR|import vitis failed: %s" % e)
    sys.exit(1)

def wb_ok(msg): print("WB_OK|%s" % msg)
def wb_err(msg): print("WB_ERROR|%s" % msg)
"""

PLATFORM_CREATE = VITIS_CLIENT_HEADER + r"""
try:
    client = vitis.create_client()
    client.set_workspace(path=@WS@)
    kwargs = {"name": @NAME@, "hw_design": @XSA@, "os": @OS_NAME@}
    cpu = @CPU@
    if cpu:
        kwargs["cpu"] = cpu
    pf = client.create_platform_component(**kwargs)
    st = pf.build()
    wb_ok("platform_built name=%s status=%s" % (@NAME@, st))
except Exception:
    wb_err(traceback.format_exc().replace("\n", " | "))
    sys.exit(1)
"""

APP_CREATE = VITIS_CLIENT_HEADER + r"""
try:
    client = vitis.create_client()
    client.set_workspace(path=@WS@)
    kwargs = {"name": @NAME@, "platform": @PLATFORM@, "template": @TEMPLATE@}
    domain = @DOMAIN@
    if domain:
        kwargs["domain"] = domain
    app = client.create_app_component(**kwargs)
    st = app.build()
    wb_ok("app_built name=%s status=%s" % (@NAME@, st))
except Exception:
    wb_err(traceback.format_exc().replace("\n", " | "))
    sys.exit(1)
"""

BUILD_COMPONENT = VITIS_CLIENT_HEADER + r"""
try:
    client = vitis.create_client()
    client.set_workspace(path=@WS@)
    comp = client.get_component(@COMP@)
    st = comp.build()
    wb_ok("build_done status=%s" % st)
except Exception:
    wb_err(traceback.format_exc().replace("\n", " | "))
    sys.exit(1)
"""

LIST_COMPONENTS = VITIS_CLIENT_HEADER + r"""
try:
    client = vitis.create_client()
    client.set_workspace(path=@WS@)
    count = 0
    for c in client.list_components():
        print("WB_COMPONENT|%s" % c)
        count += 1
    print("WB_COMPONENT_COUNT|%s" % count)
    wb_ok("listed")
except Exception:
    wb_err(traceback.format_exc().replace("\n", " | "))
    sys.exit(1)
"""

# XSDB/XSCT: 下载 bit + 运行 elf（裸机/standalone 程序）
XSCT_RUN = r"""
puts "WB_INFO|connecting"
if {[catch {connect -url @SERVER@} msg]} {
    puts "WB_ERROR|connect: $msg"
    exit 1
}
if {@BIT@ ne "" || @XSA@ ne "" || @INIT_TCL@ ne ""} {
    set reset_selected 0
    foreach reset_filter [list {name =~ "APU*"} {name =~ "PSU*"}] {
        if {![catch {targets -set -nocase -filter $reset_filter}]} {
            set reset_selected 1
            break
        }
    }
    # A Zynq booted into a bad/stale state can expose only a DAP with an APB
    # transaction error. The DAP can still accept a system reset and restore
    # the APU/Cortex targets without requiring a second debugger session.
    if {!$reset_selected && ![catch {targets -set -nocase -filter {name =~ "DAP*"}}]} {
        set reset_selected 1
        puts "WB_INFO|recovering_apu_via_dap_reset"
    }
    if {$reset_selected} {
        if {[catch {rst -system} msg]} { puts "WB_ERROR|system reset: $msg"; exit 1 }
        puts "WB_INFO|system_reset_before_standalone"
    } else {
        puts "WB_WARN|no APU/PSU system-reset target; programming PL without system reset"
    }
}
if {@XSA@ ne ""} {
    if {[catch {targets -set -nocase -filter {name =~ "APU*" || name =~ "PSU*"}}]} {
        puts "WB_ERROR|no APU/PSU target for hardware handoff"
        exit 1
    }
    if {[catch {loadhw -hw @XSA@} msg]} {
        puts "WB_ERROR|loadhw: $msg"
        exit 1
    }
    puts "WB_INFO|hardware_handoff_loaded"
}
catch {configparams force-mem-access 1}
set wb_ps_init_family ""
if {@INIT_TCL@ ne ""} {
    if {[catch {source @INIT_TCL@} msg]} { puts "WB_ERROR|source init: $msg"; exit 1 }
    set wb_has_ps7 [expr {[llength [info commands ps7_init]] > 0}]
    set wb_has_psu [expr {[llength [info commands psu_init]] > 0}]
    if {$wb_has_ps7 == $wb_has_psu} {
        puts "WB_ERROR|init script must define exactly one of ps7_init or psu_init"
        exit 1
    }
    if {$wb_has_ps7} {
        set wb_ps_init_family ps7
        if {[catch {ps7_init} msg]} { puts "WB_ERROR|ps7_init: $msg"; exit 1 }
        puts "WB_INFO|ps7_init_complete"
    } else {
        set wb_ps_init_family psu
        # For an explicitly configured PL, both generated handoff operations
        # are required. Do not program and then silently skip a missing one.
        if {@BIT@ ne ""} {
            foreach wb_psu_step {psu_ps_pl_isolation_removal psu_ps_pl_reset_config} {
                if {![llength [info commands $wb_psu_step]]} {
                    puts "WB_ERROR|missing required PSU handoff command: $wb_psu_step"
                    exit 1
                }
            }
        }
    }
}
if {@BIT@ ne ""} {
    targets -set -nocase -filter {name =~ "xc*"}
    if {[catch {fpga -f @BIT@} msg]} {
        puts "WB_ERROR|fpga: $msg"
        exit 1
    }
    puts "WB_INFO|fpga_programmed"
}
if {$wb_ps_init_family eq "psu"} {
    # AMD UG1400 ZynqMP standalone flow: configure PL, initialize PSU, then
    # release PS/PL isolation/reset before downloading to a processor. PS7
    # retains its separate pre-BIT init and post-BIT configuration sequence.
    if {[catch {targets -set -nocase -filter {name =~ "PSU*"}} msg]} {
        puts "WB_ERROR|no PSU target for psu_init: $msg"
        exit 1
    }
    if {[catch {psu_init} msg]} { puts "WB_ERROR|psu_init: $msg"; exit 1 }
    puts "WB_INFO|psu_init_complete"
    if {[llength [info commands psu_post_config]]} {
        if {[catch {psu_post_config} msg]} { puts "WB_ERROR|psu_post_config: $msg"; exit 1 }
        puts "WB_INFO|psu_post_config_complete"
    }
    # Supplying init_tcl alone does not authorize enabling an old/stale PL.
    if {@BIT@ ne ""} {
        after 1000
        if {[catch {psu_ps_pl_isolation_removal} msg]} {
            puts "WB_ERROR|psu_ps_pl_isolation_removal: $msg"; exit 1
        }
        puts "WB_INFO|psu_ps_pl_isolation_removal_complete"
        after 1000
        if {[catch {psu_ps_pl_reset_config} msg]} {
            puts "WB_ERROR|psu_ps_pl_reset_config: $msg"; exit 1
        }
        puts "WB_INFO|psu_ps_pl_reset_config_complete"
    }
}
if {$wb_ps_init_family eq "ps7" && [llength [info commands ps7_post_config]]} {
    if {[catch {targets -set -nocase -filter {name =~ "APU*" || name =~ "PSU*"}} msg]} {
        puts "WB_ERROR|no APU/PSU target for ps7_post_config: $msg"
        exit 1
    }
    if {[catch {ps7_post_config} msg]} {
        puts "WB_ERROR|ps7_post_config: $msg"
        exit 1
    }
    puts "WB_INFO|ps7_post_config_complete"
}
# Prevent a stale second Cortex-A9 from continuing old boot software while
# CPU0 runs a standalone image. This is intentionally best-effort for SoCs
# without a second application core.
foreach secondary_filter [list {name =~ "*Cortex-A*#1"} {name =~ "ARM*#1"}] {
    if {![catch {targets -set -nocase -filter $secondary_filter}]} {
        catch {stop}
        puts "WB_INFO|secondary_processor_stopped"
        break
    }
}
set selected 0
set processor_filters [list \
    {name =~ "*Cortex-A*#0"} \
    {name =~ "*Cortex-R*#0"} \
    {name =~ "*MicroBlaze*#0"} \
    {name =~ "*MicroBlaze*"} \
    {name =~ "ARM*#0"} \
    {name =~ "APU*" || name =~ "PSU*"}]
foreach processor_filter $processor_filters {
    if {![catch {targets -set -nocase -filter $processor_filter}]} {
        set selected 1
        break
    }
}
if {!$selected} {
    puts "WB_ERROR|no supported processor target"
    targets
    exit 1
}
catch {configparams force-mem-access 1}
if {[catch {rst -processor} msg]} { puts "WB_WARN|processor reset: $msg" }
after 250
if {[catch { dow @ELF@ } msg]} {
    puts "WB_ERROR|dow: $msg"
    exit 1
}
puts "WB_OK|elf_downloaded running..."
con
after @RUN_MS@
@SYMBOL_CHECKS@
puts "WB_OK|run_finished"
exit 0
"""
