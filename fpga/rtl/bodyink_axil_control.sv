`timescale 1ns/1ps
`default_nettype none
// Single clock, one outstanding transaction per direction. AW and W independent.
// Synchronous rst MUST reset the attached DMA and its fabric coherently.
module bodyink_axil_control(
 input wire clk,rst,
 input wire [7:0] s_awaddr,input wire s_awvalid,output wire s_awready,
 input wire [31:0] s_wdata,input wire [3:0] s_wstrb,input wire s_wvalid,output wire s_wready,
 output reg [1:0] s_bresp,output reg s_bvalid,input wire s_bready,
 input wire [7:0] s_araddr,input wire s_arvalid,output wire s_arready,
 output reg [31:0] s_rdata,output reg [1:0] s_rresp,output reg s_rvalid,input wire s_rready,
 output reg dma_start,dma_cancel,input wire dma_done,dma_error,
 output reg [63:0] cfg_base,cfg_output,
 output wire [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h,
 output wire irq
);
 reg aw_full,w_full;reg [7:0] wa;reg [31:0] wd;reg [3:0] ws;
 reg [31:0] shadow[0:8];reg [31:0] geometry,step,origin,extent;
 reg active,completed,failed,cancelled,cancel_seen,irq_enable;
 reg [31:0] active_tag,done_tag,cycles,done_cycles;
 integer i;
 wire [31:0] mask={{8{ws[3]}},{8{ws[2]}},{8{ws[1]}},{8{ws[0]}}};
 wire [31:0] command=wd&mask;
 assign s_awready=!rst&&!aw_full&&!s_bvalid;
 assign s_wready=!rst&&!w_full&&!s_bvalid;
 assign s_arready=!rst&&!s_rvalid;
 assign irq=!rst&&irq_enable&&completed;
 assign cfg_width=geometry[15:0];assign cfg_height=geometry[31:16];
 assign cfg_stride=step[15:0];assign cfg_x=origin[15:0];assign cfg_y=origin[31:16];
 assign cfg_w=extent[15:0];assign cfg_h=extent[31:16];
 always @(posedge clk)begin
  if(rst)begin
   aw_full<=0;w_full<=0;s_bvalid<=0;s_rvalid<=0;s_bresp<=0;s_rresp<=0;s_rdata<=0;
   dma_start<=0;dma_cancel<=0;active<=0;completed<=0;failed<=0;cancelled<=0;cancel_seen<=0;irq_enable<=0;
   active_tag<=0;done_tag<=0;cycles<=0;done_cycles<=0;
   cfg_base<=0;cfg_output<=0;geometry<=0;step<=0;origin<=0;extent<=0;
   for(i=0;i<9;i=i+1)shadow[i]<=0;
  end else begin
   dma_start<=0;dma_cancel<=0;
   if(active&&cycles!=32'hffffffff)cycles<=cycles+1'b1;
   if(s_bvalid&&s_bready)s_bvalid<=0;
   if(s_rvalid&&s_rready)s_rvalid<=0;
   if(s_awvalid&&s_awready)begin wa<=s_awaddr;aw_full<=1;end
   if(s_wvalid&&s_wready)begin wd<=s_wdata;ws<=s_wstrb;w_full<=1;end
   if(aw_full&&w_full&&!s_bvalid)begin
    aw_full<=0;w_full<=0;s_bvalid<=1;s_bresp<=0;
    if(wa[1:0]!=0)s_bresp<=2;
    else if(wa>=8'h10&&wa<=8'h30)shadow[(wa-8'h10)>>2]<=(shadow[(wa-8'h10)>>2]&~mask)|command;
    else case(wa)
     8'h04:begin
      if(command[31:3]!=0 || command[1:0]==3 || (command[0]&&active))s_bresp<=2;
      else begin
       if(command[2])begin completed<=0;failed<=0;cancelled<=0;end
       if(command[0])begin
        cfg_base<={shadow[1],shadow[0]};cfg_output<={shadow[3],shadow[2]};
        geometry<=shadow[4];step<=shadow[5];origin<=shadow[6];extent<=shadow[7];active_tag<=shadow[8];
        active<=1;dma_start<=1;completed<=0;failed<=0;cancelled<=0;cancel_seen<=0;cycles<=0;
       end
       if(command[1]&&active)begin dma_cancel<=1;cancel_seen<=1;end
      end
     end
     8'h0c:if(ws[0])irq_enable<=wd[0];
     default:s_bresp<=2;
    endcase
   end
   // Completion wins over simultaneous software acknowledgement. A cancel
   // accepted on this same edge is too late to change this completed job.
   if(active&&dma_done)begin
    active<=0;completed<=1;failed<=dma_error;cancelled<=cancel_seen;
    done_tag<=active_tag;done_cycles<=cycles;
   end
   if(s_arvalid&&s_arready)begin
    s_rvalid<=1;s_rresp<=0;s_rdata<=0;
    if(s_araddr[1:0]!=0)s_rresp<=2;
    else if(s_araddr>=8'h10&&s_araddr<=8'h30)s_rdata<=shadow[(s_araddr-8'h10)>>2];
    else case(s_araddr)
     8'h00:s_rdata<=32'h42495001;
     8'h04:s_rdata<=0;
     8'h08:s_rdata<={28'b0,cancelled,failed,completed,active};
     8'h0c:s_rdata<={31'b0,irq_enable};
     8'h34:s_rdata<=done_tag;
     8'h38:s_rdata<=done_cycles;
     8'h3c:s_rdata<=active_tag;
     default:s_rresp<=2;
    endcase
   end
  end
 end
endmodule
`default_nettype wire
