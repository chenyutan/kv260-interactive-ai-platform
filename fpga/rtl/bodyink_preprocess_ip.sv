`timescale 1ns/1ps
`default_nettype none
module bodyink_preprocess_ip #(
 parameter MAX_BURST=256,R_ROM="quant_r.hex",G_ROM="quant_g.hex",B_ROM="quant_b.hex"
)(
 input wire clk,rst,
 input wire [7:0] s_awaddr,input wire s_awvalid,output wire s_awready,
 input wire [31:0] s_wdata,input wire [3:0] s_wstrb,input wire s_wvalid,output wire s_wready,
 output reg [1:0] s_bresp,output reg s_bvalid,input wire s_bready,
 input wire [7:0] s_araddr,input wire s_arvalid,output wire s_arready,
 output reg [31:0] s_rdata,output reg [1:0] s_rresp,output reg s_rvalid,input wire s_rready,
 output wire irq,busy,done,error,
 output wire arvalid,input wire arready,output wire [63:0] araddr,output wire [7:0] arlen,
 output wire [2:0] arsize,output wire [1:0] arburst,
 input wire rvalid,output wire rready,input wire [31:0] rdata,input wire [1:0] rresp,input wire rlast,
 output wire awvalid,input wire awready,output wire [63:0] awaddr,output wire [7:0] awlen,
 output wire [2:0] awsize,output wire [1:0] awburst,
 output wire wvalid,input wire wready,output wire [31:0] wdata,output wire [3:0] wstrb,output wire wlast,
 input wire bvalid,output wire bready,input wire [1:0] bresp
);
 wire dma_start,dma_cancel;
 wire [63:0] cfg_base,cfg_output;
 wire [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h;
 bodyink_axil_control control(.dma_done(done),.dma_error(error),.*);
 bodyink_preprocess_dma #(.MAX_BURST(MAX_BURST),.R_ROM(R_ROM),.G_ROM(G_ROM),.B_ROM(B_ROM)) dma(
  .start(dma_start),.cancel(dma_cancel),.*);
endmodule
`default_nettype wire
