"""Generate explicit wiring wrapper and reuse the existing DMA scoreboard."""
from pathlib import Path
P=Path(__file__).resolve().parent
dma=(P/'rtl/bodyink_preprocess_dma.sv').read_text()
ctrl=(P/'rtl/bodyink_axil_control.sv').read_text()
ports=dma.split(' output reg busy,done,error,')[1].split('\n);')[0]
axil=ctrl.split(' input wire [7:0] s_awaddr')[1].split(' output reg dma_start')[0].rstrip().rstrip(',')
text='''`timescale 1ns/1ps
`default_nettype none
module bodyink_preprocess_ip #(
 parameter MAX_BURST=256,R_ROM="quant_r.hex",G_ROM="quant_g.hex",B_ROM="quant_b.hex"
)(
 input wire clk,rst,
 input wire [7:0] s_awaddr'''+axil+''',
 output wire irq,busy,done,error,'''+ports+'''
);
 wire dma_start,dma_cancel;
 wire [63:0] cfg_base,cfg_output;
 wire [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h;
 bodyink_axil_control control(.dma_done(done),.dma_error(error),.*);
 bodyink_preprocess_dma #(.MAX_BURST(MAX_BURST),.R_ROM(R_ROM),.G_ROM(G_ROM),.B_ROM(B_ROM)) dma(
  .start(dma_start),.cancel(dma_cancel),.*);
endmodule
`default_nettype wire
'''
(P/'rtl/bodyink_preprocess_ip.sv').write_text(text)
tb=(P/'sim/tb_dma.sv').read_text().replace('module tb_dma;','module tb_ip;')
tb=tb.replace(' bodyink_preprocess_dma #(.MAX_BURST(MAX_BURST)) dut(.*);','''
 reg [7:0] s_awaddr=0,s_araddr=0;reg s_awvalid=0,s_wvalid=0,s_arvalid=0,s_bready=0,s_rready=0;
 reg [31:0] s_wdata=0;reg [3:0] s_wstrb=15;
 wire s_awready,s_wready,s_bvalid,s_arready,s_rvalid,irq;
 wire [1:0] s_bresp,s_rresp;wire [31:0] s_rdata;
 bodyink_preprocess_ip #(.MAX_BURST(MAX_BURST)) dut(
 .clk(clk),.rst(rst),.busy(busy),.done(done),.error(error),
 .arvalid(arvalid),.arready(arready),.araddr(araddr),.arlen(arlen),.arsize(arsize),.arburst(arburst),
 .rvalid(rvalid),.rready(rready),.rdata(rdata),.rresp(rresp),.rlast(rlast),
 .awvalid(awvalid),.awready(awready),.awaddr(awaddr),.awlen(awlen),.awsize(awsize),.awburst(awburst),
 .wvalid(wvalid),.wready(wready),.wdata(wdata),.wstrb(wstrb),.wlast(wlast),
 .bvalid(bvalid),.bready(bready),.bresp(bresp),.*);
 task axwrite(input [7:0] addr,input [31:0] data);
 begin
  @(negedge clk);s_awaddr=addr;s_awvalid=1;
  do @(posedge clk);while(!s_awready);
  @(negedge clk);s_awvalid=0;s_wdata=data;s_wvalid=1;
  do @(posedge clk);while(!s_wready);
  @(negedge clk);s_wvalid=0;s_bready=1;
  do @(posedge clk);while(!s_bvalid);
  if(s_bresp!=0)$fatal(1,"FAIL: AXIL integration write");
  @(negedge clk);s_bready=0;
 end
 endtask
''')
tb=tb.replace('begin @(negedge clk);start=1;@(negedge clk);start=0;end','''begin
 axwrite('h10,cfg_base[31:0]);axwrite('h14,cfg_base[63:32]);
 axwrite('h18,cfg_output[31:0]);axwrite('h1c,cfg_output[63:32]);
 axwrite('h20,{cfg_height,cfg_width});axwrite('h24,cfg_stride);
 axwrite('h28,{cfg_y,cfg_x});axwrite('h2c,{cfg_h,cfg_w});axwrite('h30,fixture);
 axwrite('h0c,1);axwrite('h04,1);
 end''')
tb=tb.replace('cancel=1;','axwrite(4,2);').replace('cancel=0;force_','force_')
tb=tb.replace('repeat(8)@(negedge clk);if(busy','repeat(8)@(negedge clk);if(!irq || dut.control.done_tag!=fixture)$fatal(1,"FAIL: completion IRQ/tag");if(busy')
tb=tb.replace('PASS: AXI DMA','PASS: AXIL integrated DMA')
(P/'sim/tb_ip.sv').write_text(tb)
