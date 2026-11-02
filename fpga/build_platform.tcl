set root [file normalize [file dirname [info script]]]
set out [file join $root build buildusb]
file mkdir $out
set_param general.maxThreads 8
open_project [file join $root build usb bodyink_usb.xpr]
set bd [get_files */kv260_ispMipiRx_vcu_DP.bd]
open_bd_design $bd
foreach obj [concat [get_bd_cells -hier] [get_bd_intf_ports] [get_bd_ports]] {
 foreach prop [list_property $obj] {
  if {[string match PFM.* $prop]} {set_property $prop {} $obj}
 }
}
validate_bd_design
save_bd_design
# Global synthesis avoids broken Windows runme.bat child launchers.
set_property synth_checkpoint_mode None $bd
generate_target all $bd
remove_files [get_files -quiet */kv260_ispMipiRx_vcu_DP_wrapper.v]
set wrapper [make_wrapper -files $bd -top]
add_files -norecurse $wrapper
update_compile_order -fileset sources_1
synth_design -top kv260_ispMipiRx_vcu_DP_wrapper -part [get_property PART [current_project]] -flatten_hierarchy rebuilt
write_checkpoint -force $out/synth.dcp
report_utilization -file $out/synth_utilization.rpt
report_timing_summary -file $out/synth_timing.rpt
puts "PASS: full platform synthesis"
opt_design
write_checkpoint -force $out/opt.dcp
place_design
phys_opt_design
write_checkpoint -force $out/placed.dcp
route_design
write_checkpoint -force $out/routed.dcp
report_timing_summary -delay_type min_max -report_unconstrained -file $out/routed_timing.rpt
report_utilization -file $out/routed_utilization.rpt
report_drc -file $out/routed_drc.rpt
report_route_status -file $out/route_status.rpt
report_clock_interaction -file $out/clock_interaction.rpt
report_cdc -file $out/cdc.rpt
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup] || ![llength $hold]} {error "Missing timing paths"}
if {[get_property SLACK $setup] < 0 || [get_property SLACK $hold] < 0} {error "Routed timing not met; bitstream withheld"}
write_bitstream -force -bin_file $out/bodyink.bit
write_hw_platform -fixed -force -file $out/bodyink.xsa
puts "PASS: full platform implementation and bitstream"
exit
