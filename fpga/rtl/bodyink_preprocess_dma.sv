`timescale 1ns/1ps
`default_nettype none
// Register-level job interface; AXI-Lite/interrupt/driver packaging is separate.
module bodyink_preprocess_dma #(
 parameter MAX_BURST=256,R_ROM="quant_r.hex",G_ROM="quant_g.hex",B_ROM="quant_b.hex"
)(
 input wire clk,rst,start,cancel,
 input wire [63:0] cfg_base,cfg_output,
 input wire [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h,
 output reg busy,done,error,
 output wire arvalid,input wire arready,output wire [63:0] araddr,output wire [7:0] arlen,
 output wire [2:0] arsize,output wire [1:0] arburst,
 input wire rvalid,output wire rready,input wire [31:0] rdata,input wire [1:0] rresp,input wire rlast,
 output wire awvalid,input wire awready,output wire [63:0] awaddr,output wire [7:0] awlen,
 output wire [2:0] awsize,output wire [1:0] awburst,
 output wire wvalid,input wire wready,output wire [31:0] wdata,output wire [3:0] wstrb,output wire wlast,
 input wire bvalid,output wire bready,input wire [1:0] bresp
);
 wire launch=start&&!busy&&!cancel;
 wire front_busy,front_done,front_error,write_busy,write_done,write_error;
 reg front_finished,write_finished,stop_job;
 wire req_valid,req_ready,rsp_valid,rsp_ready,rsp_last,rsp_error,abort_valid,abort_ready;
 wire [63:0] req_addr;wire [11:0] req_words;wire [31:0] rsp_data;
 wire pixels_valid,pixels_ready,pixels_last;wire [23:0] rgb,quant;wire [31:0] tag;
 // Child errors belong to the active job. A new launch resets sticky child flags.
 wire kill=busy&&(cancel||stop_job||front_error||write_error);
 bodyink_roi_frontend #(.R_ROM(R_ROM),.G_ROM(G_ROM),.B_ROM(B_ROM)) front(
  .clk(clk),.rst(rst),.start(launch),.cancel(kill),.cfg_base(cfg_base),
  .cfg_width(cfg_width),.cfg_height(cfg_height),.cfg_stride(cfg_stride),.cfg_x(cfg_x),.cfg_y(cfg_y),.cfg_w(cfg_w),.cfg_h(cfg_h),
  .busy(front_busy),.done(front_done),.error(front_error),
  .req_valid(req_valid),.req_ready(req_ready),.req_addr(req_addr),.req_words(req_words),
  .rsp_valid(rsp_valid),.rsp_ready(rsp_ready),.rsp_data(rsp_data),.rsp_last(rsp_last),.rsp_error(rsp_error),
  .abort_valid(abort_valid),.abort_ready(abort_ready),
  .m_valid(pixels_valid),.m_ready(pixels_ready),.m_rgb(rgb),.m_int8(quant),.m_last(pixels_last),.m_tag(tag));
 bodyink_axi_reader #(.MAX_BURST(MAX_BURST)) reader(
  .clk(clk),.rst(rst),.req_valid(req_valid),.req_ready(req_ready),.req_addr(req_addr),.req_words(req_words),
  .rsp_valid(rsp_valid),.rsp_ready(rsp_ready),.rsp_data(rsp_data),.rsp_last(rsp_last),.rsp_error(rsp_error),.abort_valid(abort_valid),.abort_ready(abort_ready),
  .arvalid(arvalid),.arready(arready),.araddr(araddr),.arlen(arlen),.arsize(arsize),.arburst(arburst),.rvalid(rvalid),.rready(rready),.rdata(rdata),.rresp(rresp),.rlast(rlast));
 bodyink_axi_writer #(.MAX_BURST(MAX_BURST)) writer(
  .clk(clk),.rst(rst),.start(launch),.cfg_addr(cfg_output),.cancel(kill),.busy(write_busy),.done(write_done),.error(write_error),
  .s_valid(pixels_valid),.s_ready(pixels_ready),.s_data(quant),.s_last(pixels_last),.s_tag(tag),
  .awvalid(awvalid),.awready(awready),.awaddr(awaddr),.awlen(awlen),.awsize(awsize),.awburst(awburst),
  .wvalid(wvalid),.wready(wready),.wdata(wdata),.wstrb(wstrb),.wlast(wlast),.bvalid(bvalid),.bready(bready),.bresp(bresp));
 always @(posedge clk)begin
  if(rst)begin busy<=0;done<=0;error<=0;front_finished<=0;write_finished<=0;stop_job<=0;end
  else begin
   done<=0;
   if(launch)begin busy<=1;error<=0;front_finished<=0;write_finished<=0;stop_job<=0;end
   else if(busy)begin
    if(front_done)front_finished<=1;
    if(write_done)write_finished<=1;
    if(kill)begin stop_job<=1;error<=1;end
    if((front_finished||front_done)&&(write_finished||write_done))begin busy<=0;done<=1;end
   end
  end
 end
endmodule
`default_nettype wire
