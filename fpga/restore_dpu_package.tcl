set root [file normalize [file dirname [info script]]]
set overlays [file join $root platform-source kria-vitis-platforms-xlnx_rel_v2022.1 kv260 overlays]
set work [file join $root dpu-package]
file mkdir $work
foreach item {scripts kernel_xml dpu_conf.vh} {
 file copy -force [file join $overlays examples benchmark $item] $work
}
cd $work
set env(TRD_PATH) $overlays
set suffix DPUCZDX8G_hw_mpsoc
source [file join $work scripts package_dpu_kernel.tcl]
if {![file exists [file join $work packaged_kernel_${suffix} component.xml]]} {error "DPU package missing"}
puts "PASS: official B4096 kernel package restored"
exit
