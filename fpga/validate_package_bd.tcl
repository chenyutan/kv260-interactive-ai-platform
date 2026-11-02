# Isolated PS/SmartConnect integration fixture; NOT the production DPU platform.
set root [file normalize [file dirname [info script]]]
create_project -force bodyink_connectivity [file join $root bd-check] -part xck26-sfvc784-2LV-c
set_property ip_repo_paths [file join $root packaged-ip] [current_project]
update_ip_catalog
create_bd_design connectivity
create_bd_port -dir I -type clk clk100
set_property CONFIG.FREQ_HZ 100000000 [get_bd_ports clk100]
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.4 ps
set_property -dict [list CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} CONFIG.PSU__USE__S_AXI_GP2 {1} CONFIG.PSU__USE__IRQ0 {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100}] [get_bd_cells ps]
create_bd_cell -type ip -vlnv bodyink.org:user:bodyink_preprocess:1.0 preprocess
foreach name {control memory} {
 create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 $name
 set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells $name]
}
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one
set_property CONFIG.CONST_VAL 1 [get_bd_cells one]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero
set_property CONFIG.CONST_VAL 0 [get_bd_cells zero]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 interrupts
set_property CONFIG.NUM_PORTS 8 [get_bd_cells interrupts]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins control/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control/M00_AXI] [get_bd_intf_pins preprocess/S_AXI]
connect_bd_intf_net [get_bd_intf_pins preprocess/M_AXI] [get_bd_intf_pins memory/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins memory/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
connect_bd_net [get_bd_ports clk100] [get_bd_pins ps/maxihpm0_fpd_aclk] [get_bd_pins ps/saxihp0_fpd_aclk] [get_bd_pins control/aclk] [get_bd_pins memory/aclk] [get_bd_pins preprocess/clk] [get_bd_pins reset/slowest_sync_clk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins reset/ext_reset_in]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset/dcm_locked]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins reset/aux_reset_in] [get_bd_pins reset/mb_debug_sys_rst]
connect_bd_net [get_bd_pins reset/peripheral_reset] [get_bd_pins preprocess/rst]
connect_bd_net [get_bd_pins reset/interconnect_aresetn] [get_bd_pins control/aresetn] [get_bd_pins memory/aresetn]
connect_bd_net [get_bd_pins preprocess/irq] [get_bd_pins interrupts/In0]
for {set i 1} {$i<8} {incr i} {connect_bd_net [get_bd_pins zero/dout] [get_bd_pins interrupts/In$i]}
connect_bd_net [get_bd_pins interrupts/dout] [get_bd_pins ps/pl_ps_irq0]
# Reserve only within this test fixture. No production register address assigned.
assign_bd_address -offset 0xA1000000 -range 0x1000 -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs preprocess/S_AXI/registers]
assign_bd_address -offset 0 -range 0x80000000 -target_address_space [get_bd_addr_spaces preprocess/M_AXI] [get_bd_addr_segs ps/SAXIGP2/HP0_DDR_LOW]
validate_bd_design
save_bd_design
write_bd_tcl -force [file join $root bd-check/connectivity.tcl]
set fd [open [file join $root bd-check/connections.txt] w]
foreach x [get_bd_addr_segs -of_objects [get_bd_addr_spaces]] {puts $fd "$x [get_property OFFSET $x] [get_property RANGE $x]"}
close $fd
puts "PASS: packaged IP PS connectivity validated"
exit
