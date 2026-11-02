file mkdir [file join [file dirname [info script]] platform-restore]
# USB camera uses PS USB; the unused MIPI/HLS branch is not part of BodyInk.
set root [file normalize [file dirname [info script]]]
open_project [file join $root build integrated bodyink_integrated.xpr]
save_project_as bodyink_usb [file join $root build usb] -force
set bd [get_files */kv260_ispMipiRx_vcu_DP.bd]
open_bd_design $bd
delete_bd_objs [get_bd_cells capture_pipeline]
delete_bd_objs [get_bd_cells axi_ic_ctrl_300]
delete_bd_objs [get_bd_intf_ports mipi_phy_if]
set_property -dict [list CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__S_AXI_GP2 0] [get_bd_cells PS_0]
# Move the new control slave into the now-vacant M00 slot, then remove M06.
delete_bd_objs [get_bd_intf_nets -of_objects [get_bd_intf_pins preprocess/S_AXI]]
connect_bd_intf_net [get_bd_intf_pins axi_ic_ctrl_100/M00_AXI] [get_bd_intf_pins preprocess/S_AXI]
set_property CONFIG.NUM_MI 6 [get_bd_cells axi_ic_ctrl_100]
foreach pin {In0 In1} {connect_bd_net [get_bd_pins accel_zero/dout] [get_bd_pins xlconcat_0_0/$pin]}
# These annotations described free Vitis insertion ports in the original base.
# This is a fixed integrated design; remove stale extensibility annotations.
foreach obj [concat [get_bd_cells -hier] [get_bd_intf_ports] [get_bd_ports]] {
 foreach prop [list_property $obj] {
  if {[string match PFM.* $prop]} {set_property $prop {} $obj}
 }
}
assign_bd_address -offset 0x81000000 -range 0x1000 -target_address_space [get_bd_addr_spaces PS_0/Data] [get_bd_addr_segs preprocess/S_AXI/registers] -force
validate_bd_design
save_bd_design
set oldxdc [get_files */pin.xdc]
set fd [open $oldxdc r];set txt [read $fd];close $fd
set fd [open [file join $root platform-restore usb_pin.xdc] w]
foreach line [split $txt \n] {if {![string match *mipi_phy_if* $line]} {puts $fd $line}}
close $fd
remove_files $oldxdc
add_files -fileset constrs_1 [file join $root platform-restore usb_pin.xdc]
set_property synth_checkpoint_mode None $bd
generate_target all $bd
set wrapper [make_wrapper -files $bd -top]
add_files -norecurse $wrapper
set_property top kv260_ispMipiRx_vcu_DP_wrapper [current_fileset]
update_compile_order -fileset sources_1
write_bd_tcl -force [file join $root platform-restore usb_integrated.tcl]
puts "PASS: USB platform generated"
exit

