# Candidate full BD. No synthesis/bitstream; production address/IRQ ABI unapproved.
set root [file normalize [file dirname [info script]]]
open_project [file join $root build base bodyink_platform.xpr]
save_project_as bodyink_integrated [file join $root build integrated] -force
set_property ip_repo_paths [list [file join $root packaged-ip] [file join $root dpu-package packaged_kernel_DPUCZDX8G_hw_mpsoc]] [current_project]
update_ip_catalog
open_bd_design [get_files */kv260_ispMipiRx_vcu_DP.bd]
create_bd_cell -type ip -vlnv xilinx.com:RTLKernel:DPUCZDX8G:1.0 DPUCZDX8G_1
create_bd_cell -type ip -vlnv bodyink.org:user:bodyink_preprocess:1.0 preprocess
# Official base uses an AXI VIP placeholder at the accelerator control address.
# Replace it only in this candidate copy, as the accelerator linker normally does.
if {[get_property VLNV [get_bd_cells axi_vip_0]] ne "xilinx.com:ip:axi_vip:1.1" && ![string match "xilinx.com:ip:axi_vip:*" [get_property VLNV [get_bd_cells axi_vip_0]]]} {error "Unexpected placeholder type"}
delete_bd_objs [get_bd_cells axi_vip_0]
set_property CONFIG.NUM_MI 7 [get_bd_cells axi_ic_ctrl_100]
connect_bd_intf_net [get_bd_intf_pins axi_ic_accel_ctrl/M00_AXI] [get_bd_intf_pins DPUCZDX8G_1/S_AXI_CONTROL]
connect_bd_intf_net [get_bd_intf_pins axi_ic_ctrl_100/M06_AXI] [get_bd_intf_pins preprocess/S_AXI]
connect_bd_net [get_bd_pins clk_wiz_0/clk_300M] [get_bd_pins DPUCZDX8G_1/aclk]
connect_bd_net [get_bd_pins clk_wiz_0/clk_600M] [get_bd_pins DPUCZDX8G_1/ap_clk_2]
connect_bd_net [get_bd_pins proc_sys_reset_300MHz/peripheral_aresetn] [get_bd_pins DPUCZDX8G_1/aresetn]
connect_bd_net [get_bd_pins proc_sys_reset_600MHz/peripheral_aresetn] [get_bd_pins DPUCZDX8G_1/ap_rst_n_2]
connect_bd_net [get_bd_pins clk_wiz_0/clk_100M] [get_bd_pins preprocess/clk] [get_bd_pins axi_ic_ctrl_100/M06_ACLK]
connect_bd_net [get_bd_pins proc_sys_reset_100MHz/peripheral_reset] [get_bd_pins preprocess/rst]
connect_bd_net [get_bd_pins proc_sys_reset_100MHz/peripheral_aresetn] [get_bd_pins axi_ic_ctrl_100/M06_ARESETN]
foreach {name dpu_port ps_port count} {mem_hpc1 M_AXI_GP0 S_AXI_HPC1_FPD 2 mem_hp1 M_AXI_HP0 S_AXI_HP1_FPD 1 mem_hp3 M_AXI_HP2 S_AXI_HP3_FPD 1} {
 if {[llength [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins PS_0/$ps_port]]]} {error "Unexpected occupied DPU memory port $ps_port"}
 create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 $name
 set_property -dict [list CONFIG.NUM_SI $count CONFIG.NUM_MI 1] [get_bd_cells $name]
 connect_bd_intf_net [get_bd_intf_pins DPUCZDX8G_1/$dpu_port] [get_bd_intf_pins $name/S00_AXI]
 connect_bd_intf_net [get_bd_intf_pins $name/M00_AXI] [get_bd_intf_pins PS_0/$ps_port]
 foreach p {ACLK S00_ACLK M00_ACLK} {connect_bd_net [get_bd_pins clk_wiz_0/clk_300M] [get_bd_pins $name/$p]}
 foreach p {ARESETN S00_ARESETN M00_ARESETN} {connect_bd_net [get_bd_pins proc_sys_reset_300MHz/interconnect_aresetn] [get_bd_pins $name/$p]}
}
connect_bd_intf_net [get_bd_intf_pins preprocess/M_AXI] [get_bd_intf_pins mem_hpc1/S01_AXI]
connect_bd_net [get_bd_pins clk_wiz_0/clk_100M] [get_bd_pins mem_hpc1/S01_ACLK]
connect_bd_net [get_bd_pins proc_sys_reset_100MHz/interconnect_aresetn] [get_bd_pins mem_hpc1/S01_ARESETN]
if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins PS_0/pl_ps_irq0]]]} {error "Unexpected occupied accelerator interrupt bus"}
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 accel_irq
set_property CONFIG.NUM_PORTS 8 [get_bd_cells accel_irq]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 accel_zero
set_property CONFIG.CONST_VAL 0 [get_bd_cells accel_zero]
connect_bd_net [get_bd_pins DPUCZDX8G_1/interrupt] [get_bd_pins accel_irq/In0]
connect_bd_net [get_bd_pins preprocess/irq] [get_bd_pins accel_irq/In1]
for {set i 2} {$i<8} {incr i} {connect_bd_net [get_bd_pins accel_zero/dout] [get_bd_pins accel_irq/In$i]}
connect_bd_net [get_bd_pins accel_irq/dout] [get_bd_pins PS_0/pl_ps_irq0]
assign_bd_address -offset 0xA0000000 -range 0x10000 -target_address_space [get_bd_addr_spaces PS_0/Data] [get_bd_addr_segs DPUCZDX8G_1/S_AXI_CONTROL/reg0]
assign_bd_address -offset 0x81000000 -range 0x1000 -target_address_space [get_bd_addr_spaces PS_0/Data] [get_bd_addr_segs preprocess/S_AXI/registers]
foreach {port seg} {M_AXI_GP0 SAXIGP1/HPC1_DDR_LOW M_AXI_HP0 SAXIGP3/HP1_DDR_LOW M_AXI_HP2 SAXIGP5/HP3_DDR_LOW} {
 assign_bd_address -offset 0 -range 0x80000000 -target_address_space [get_bd_addr_spaces DPUCZDX8G_1/$port] [get_bd_addr_segs PS_0/$seg]
}
assign_bd_address -offset 0 -range 0x80000000 -target_address_space [get_bd_addr_spaces preprocess/M_AXI] [get_bd_addr_segs PS_0/SAXIGP1/HPC1_DDR_LOW]
validate_bd_design
save_bd_design
write_bd_tcl -force [file join $root platform-restore integrated.tcl]
report_ip_status -file [file join $root platform-restore integrated_ip_status.rpt]
puts "PASS: full candidate DPU preprocessing BD validated"
exit
