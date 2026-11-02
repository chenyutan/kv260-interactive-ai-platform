file mkdir [file join [file dirname [info script]] platform-restore]
# Restore official base BD only: no output products, synthesis or bitstream.
set root [file normalize [file dirname [info script]]]
set source_root [file join $root platform-source kria-vitis-platforms-xlnx_rel_v2022.1]
set base [file join $source_root kv260 platforms vivado kv260_ispMipiRx_vcu_DP]
set boards [get_board_parts -quiet *:kv260_som:* -latest_file_version]
puts "KV260_BOARD_PARTS: $boards"
if {[llength $boards]!=1} {error "Exactly one installed KV260 SOM board definition required"}
set board [lindex $boards 0]
create_project -force bodyink_platform [file join $root build base] -part [get_property PART_NAME [get_board_parts $board]]
set_property board_part $board [current_project]
set_property board_connections {som240_1_connector xilinx.com:kv260_carrier:som240_1_connector:1.3} [current_project]
set_property ip_repo_paths [list [file join $root packaged-ip] [file join $source_root kv260 overlays dpu_ip]] [current_project]
update_ip_catalog
add_files -fileset constrs_1 [file join $base xdc pin.xdc]
set design_name kv260_ispMipiRx_vcu_DP
create_bd_design $design_name
source [file join $base scripts config_bd.tcl]
validate_bd_design
save_bd_design
write_bd_tcl -force [file join $root platform-restore restored_base.tcl]
report_ip_status -file [file join $root platform-restore ip_status.rpt]
set fd [open [file join $root platform-restore interfaces.txt] w]
foreach p [get_bd_intf_pins -hier] {
 if {[string match *PS_0/* $p] || [string match *axi_ic_accel_ctrl/* $p]} {
  puts $fd "$p [get_bd_intf_nets -quiet -of_objects $p]"
 }
}
close $fd
puts "PASS: official base BD restored and validated"
exit
