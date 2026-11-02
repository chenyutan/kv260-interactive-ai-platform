set root [file normalize [file join [file dirname [info script]] .]]
set out [file join $root ooc]
file mkdir $out
cd $out
foreach c {r g b} {file copy -force [file join $root vectors quant_${c}.hex] .}
create_project -in_memory -part xck26-sfvc784-2LV-c
set_param general.maxThreads 8
foreach f {bodyink_preprocess_core bodyink_roi_frontend bodyink_axi_reader bodyink_axi_writer bodyink_preprocess_dma} {
 read_verilog -sv [file join $root rtl ${f}.sv]
}
synth_design -top bodyink_preprocess_dma -part xck26-sfvc784-2LV-c -mode out_of_context
create_clock -name core_clk -period 10.000 [get_ports clk]
set_clock_uncertainty 0.200 [get_clocks core_clk]
set inputs [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_input_delay -clock core_clk -max 2.000 $inputs
set_input_delay -clock core_clk -min 0.000 $inputs
set_output_delay -clock core_clk -max 2.000 [all_outputs]
set_output_delay -clock core_clk -min 0.000 [all_outputs]
report_utilization -hierarchical -file utilization.rpt
report_timing_summary -delay_type min_max -report_unconstrained -file timing_synth.rpt
report_timing -max_paths 10 -file paths_synth.rpt
report_drc -file drc_synth.rpt
write_checkpoint -force preprocess_synth.dcp
puts "PASS: OOC synthesis reports written"
exit
