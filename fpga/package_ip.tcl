set root [file normalize [file dirname [info script]]]
set repo [file join $root packaged-ip]
set work [file join $root package-work]
file mkdir $work
create_project -force package_preprocess $work -part xck26-sfvc784-2LV-c
foreach f {bodyink_preprocess_core bodyink_roi_frontend bodyink_axi_reader bodyink_axi_writer bodyink_preprocess_dma bodyink_axil_control bodyink_preprocess_ip} {
 add_files [file join $root rtl ${f}.sv]
}
foreach c {r g b} {add_files [file join $root vectors quant_${c}.hex]}
set_property top bodyink_preprocess_ip [current_fileset]
update_compile_order -fileset sources_1
ipx::package_project -root_dir $repo -vendor bodyink.org -library user -taxonomy /UserIP -import_files -set_current true
set core [ipx::current_core]
set_property name bodyink_preprocess $core
set_property version 1.0 $core
set_property display_name {BodyInk ROI preprocessing DMA} $core
set_property description {Fixed 256x256 RGB INT8 preprocessing, custom AXI-Lite control, single clock. Not an ap_ctrl_hs XRT kernel.} $core
foreach intf [ipx::get_bus_interfaces -of_objects $core] {ipx::remove_bus_interface [get_property NAME $intf] $core}
foreach m [ipx::get_memory_maps -of_objects $core] {ipx::remove_memory_map [get_property NAME $m] $core}
file mkdir [file join $repo data]
foreach c {r g b} {
 file copy -force [file join $root vectors quant_${c}.hex] [file join $repo data quant_${c}.hex]
 foreach group {xilinx_anylanguagesynthesis xilinx_anylanguagebehavioralsimulation} {
  set fg [ipx::get_file_groups $group -of_objects $core]
  set f [ipx::add_file data/quant_${c}.hex $fg]
  set_property type mif $f
 }
}
foreach group {xilinx_anylanguagesynthesis xilinx_anylanguagebehavioralsimulation} {
 set fg [ipx::get_file_groups $group -of_objects $core]
 ipx::remove_file src/bodyink_preprocess_ip.sv $fg
 set f [ipx::add_file src/bodyink_preprocess_ip.sv $fg]
 set_property type systemVerilogSource $f
}
proc param {bus name value} {
 set p [ipx::add_bus_parameter $name $bus];set_property value $value $p
}
proc portmap {bus logical physical} {
 set p [ipx::add_port_map $logical $bus];set_property physical_name $physical $p
}
foreach {name mode prefix protocol} {S_AXI slave s_ AXI4LITE M_AXI master {} AXI4} {
 set bus [ipx::add_bus_interface $name $core]
 set_property interface_mode $mode $bus
 set_property bus_type_vlnv xilinx.com:interface:aximm:1.0 $bus
 set_property abstraction_type_vlnv xilinx.com:interface:aximm_rtl:1.0 $bus
 foreach signal {AWADDR AWVALID AWREADY WDATA WSTRB WVALID WREADY BRESP BVALID BREADY ARADDR ARVALID ARREADY RDATA RRESP RVALID RREADY} {
  portmap $bus $signal ${prefix}[string tolower $signal]
 }
 if {$mode eq "master"} {foreach signal {ARLEN ARSIZE ARBURST RLAST AWLEN AWSIZE AWBURST WLAST} {portmap $bus $signal [string tolower $signal]}}
 param $bus PROTOCOL $protocol
 param $bus DATA_WIDTH 32
 param $bus ADDR_WIDTH [expr {$mode eq "master"?64:8}]
 param $bus NUM_READ_OUTSTANDING 1
 param $bus NUM_WRITE_OUTSTANDING 1
 if {$mode eq "master"} {
  set a [ipx::add_address_space M_AXI $core];set_property width 64 $a;set_property range 4294967296 $a
  set_property master_address_space_ref M_AXI $bus
 } else {
  set m [ipx::add_memory_map S_AXI $core]
  set b [ipx::add_address_block registers $m]
  set_property range 256 $b;set_property width 32 $b;set_property base_address 0 $b;set_property usage register $b
  set_property slave_memory_map_ref S_AXI $bus
 }
}
foreach {name physical type} {CLK clk clock RST rst reset IRQ irq interrupt} {
 set b [ipx::add_bus_interface $name $core]
 set_property interface_mode [expr {$type eq "interrupt"?"master":"slave"}] $b
 set_property bus_type_vlnv xilinx.com:signal:${type}:1.0 $b
 set_property abstraction_type_vlnv xilinx.com:signal:${type}_rtl:1.0 $b
 portmap $b [expr {$type eq "interrupt"?"INTERRUPT":[string toupper $physical]}] $physical
 if {$type eq "clock"} {param $b ASSOCIATED_BUSIF S_AXI:M_AXI;param $b ASSOCIATED_RESET rst}
 if {$type eq "reset"} {param $b POLARITY ACTIVE_HIGH}
 if {$type eq "interrupt"} {param $b SENSITIVITY LEVEL_HIGH}
}
ipx::create_xgui_files $core
ipx::update_checksums $core
ipx::check_integrity $core
ipx::save_core $core
puts "PASS: IP package saved"
exit
