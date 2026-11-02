`timescale 1ns/1ps
`default_nettype none
// Row-burst abstraction, NOT an AXI master. A downstream adapter must split
// commands at AXI burst/4KB boundaries, report errors, and support cancellation.
// Two ROI row spans, synchronous dual read ports per bank. No concurrent fill
// and sample reads, so no hidden third BRAM port or full-frame storage.
module bodyink_roi_frontend #(
 parameter R_ROM="quant_r.hex", G_ROM="quant_g.hex", B_ROM="quant_b.hex"
)(
 input wire clk,rst,start,cancel,
 input wire [63:0] cfg_base,
 input wire [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h,
 output wire busy, output reg done,error,
 output wire req_valid,input wire req_ready,
 output wire [63:0] req_addr,output wire [11:0] req_words,
 input wire rsp_valid,output wire rsp_ready,
 input wire [31:0] rsp_data,input wire rsp_last,rsp_error,
 output wire abort_valid,input wire abort_ready,
 output wire m_valid,input wire m_ready,
 output wire [23:0] m_rgb,m_int8,output wire m_last,output wire [31:0] m_tag
);
 localparam IDLE=0,TOP=1,BOTTOM=2,REQUEST=3,FILL=4,READ=5,OFFER=6,DRAIN=7,ABORT=8;
 reg [3:0] state;
 reg [63:0] base;
 reg [15:0] width,height,stride,rx,ry,rw,rh;
 reg [7:0] ox,oy;
 reg [12:0] row0,row1,load_row;
 reg cached0,cached1,top_bank,bottom_bank,load_bank;
 reg [3:0] resume_state;
 reg [11:0] fill_index;
 reg [11:0] pair_base,span_words;
 (* ram_style="block" *) reg [31:0] bank0[0:2047];
 (* ram_style="block" *) reg [31:0] bank1[0:2047];
 reg [31:0] a0,a1,b0,b1;
 reg parity0,parity1;
 reg [10:0] alpha_hold,beta_hold;
 reg [31:0] tag_hold;
 reg last_hold;
 wire core_ready,core_valid;
 wire [23:0] core_rgb,core_int8;wire core_last;wire [31:0] core_tag;
 // Fixed 256x256 output: half-pixel positions have an exact Q9 representation.
 function automatic [21:0] position(input [7:0] index,input [15:0] offset,length);
  reg signed [31:0] value,lo,hi;
  begin
   lo=$signed({1'b0,offset})*32'sd512;
   hi=($signed({1'b0,offset})+$signed({1'b0,length})-1)*32'sd512;
   value=lo+(2*$signed({1'b0,index})+1)*$signed({1'b0,length})-256;
   if(value<lo)value=lo;if(value>hi)value=hi;
   position=value[21:0];
  end
 endfunction
 wire [21:0] fx=position(ox,rx,rw),fy=position(oy,ry,rh);
 wire [12:0] x0=fx[21:9],y0=fy[21:9];
 wire [12:0] x1=(x0+1<width)?x0+1:x0;
 wire [12:0] y1=(y0+1<height)?y0+1:y0;
 wire config_ok=(cfg_width>=2 && cfg_width<=4096 && !cfg_width[0] &&
  cfg_height>=1 && cfg_height<=4096 && cfg_stride>=({1'b0,cfg_width}<<1) &&
  cfg_stride[1:0]==0 && cfg_base[1:0]==0 && cfg_w>=1 && cfg_h>=1 &&
  ({1'b0,cfg_x}+{1'b0,cfg_w}<={1'b0,cfg_width}) &&
  ({1'b0,cfg_y}+{1'b0,cfg_h}<={1'b0,cfg_height}));
 assign busy=state!=IDLE;
 assign req_valid=!rst && state==REQUEST;
 assign req_addr=base+load_row*{48'b0,stride}+{50'b0,pair_base,2'b0};
 assign req_words=span_words;
 assign rsp_ready=!rst && state==FILL;
 // Adapter acknowledges only after all outstanding responses are drained.
 assign abort_valid=!rst && state==ABORT;
 function automatic [23:0] yuv(input [31:0] pair,input parity);
  begin yuv={pair[31:24],pair[15:8],parity?pair[23:16]:pair[7:0]};end
 endfunction
 wire [31:0] upper0=top_bank?b0:a0,upper1=top_bank?b1:a1;
 wire [31:0] lower0=bottom_bank?b0:a0,lower1=bottom_bank?b1:a1;
 wire [95:0] samples={yuv(lower1,parity1),yuv(lower0,parity0),yuv(upper1,parity1),yuv(upper0,parity0)};
 bodyink_preprocess_core #(.R_ROM(R_ROM),.G_ROM(G_ROM),.B_ROM(B_ROM)) core(
  .clk(clk),.rst(rst || state==IDLE || state==ABORT || cancel),.s_valid(state==OFFER && !cancel),.s_ready(core_ready),
  .s_yuv(samples),.s_alpha(alpha_hold),.s_beta(beta_hold),.s_last(last_hold),.s_tag(tag_hold),
  .m_valid(core_valid),.m_ready(m_ready),.m_rgb(core_rgb),.m_int8(core_int8),.m_last(core_last),.m_tag(core_tag));
 assign m_valid=core_valid && busy && state!=ABORT && !cancel;assign m_rgb=core_rgb;assign m_int8=core_int8;assign m_last=core_last;assign m_tag=core_tag;
 // Synchronous RAM access; bank contents are deliberately not reset.
 always @(posedge clk)begin
  if(!rst && state==FILL && rsp_valid && !rsp_error && !load_bank)bank0[fill_index]<=rsp_data;
  else if(!rst && state==READ)a0<=bank0[x0[11:1]-pair_base];
  if(!rst && state==FILL && rsp_valid && !rsp_error && load_bank)bank1[fill_index]<=rsp_data;
  else if(!rst && state==READ)b0<=bank1[x0[11:1]-pair_base];
  if(!rst && state==READ)begin
   a1<=bank0[x1[11:1]-pair_base];b1<=bank1[x1[11:1]-pair_base];
  end
 end
 always @(posedge clk)begin
  if(rst)begin state<=IDLE;done<=0;error<=0;cached0<=0;cached1<=0;end
  else begin
   done<=0;
   if(cancel && state!=IDLE && state!=ABORT)begin
    state<=ABORT;error<=1;cached0<=0;cached1<=0;
   end else case(state)
    IDLE:if(start)begin
     error<=0;cached0<=0;cached1<=0;
     if(!config_ok)begin done<=1;error<=1;end
     else begin
      base<=cfg_base;width<=cfg_width;height<=cfg_height;stride<=cfg_stride;
      rx<=cfg_x;ry<=cfg_y;rw<=cfg_w;rh<=cfg_h;ox<=0;oy<=0;state<=TOP;
      pair_base<=cfg_x>>1;
      // Include a possible right neighbour; clamp to actual image, never padding.
      span_words<=(((cfg_x+cfg_w<cfg_width)?(cfg_x+cfg_w):(cfg_width-1))>>1)-(cfg_x>>1)+1;
     end
    end
    TOP:begin
     if(cached0 && row0==y0)begin top_bank<=0;state<=BOTTOM;end
     else if(cached1 && row1==y0)begin top_bank<=1;state<=BOTTOM;end
     else begin
      // Preserve a cached bottom row while replacing the other bank.
      load_bank<=cached0 && row0==y1;load_row<=y0;resume_state<=TOP;state<=REQUEST;
     end
    end
    BOTTOM:begin
     if(cached0 && row0==y1)begin bottom_bank<=0;state<=READ;end
     else if(cached1 && row1==y1)begin bottom_bank<=1;state<=READ;end
     else begin load_bank<=!top_bank;load_row<=y1;resume_state<=BOTTOM;state<=REQUEST;end
    end
    REQUEST:if(req_ready)begin fill_index<=0;state<=FILL;end
    FILL:if(rsp_valid)begin
     if(rsp_error || rsp_last!=(fill_index==req_words-1))begin
      state<=ABORT;error<=1;cached0<=0;cached1<=0;
     end else if(rsp_last)begin
      if(load_bank)begin cached1<=1;row1<=load_row;end
      else begin cached0<=1;row0<=load_row;end
      state<=resume_state;
     end else fill_index<=fill_index+1;
    end
    READ:begin
     parity0<=x0[0];parity1<=x1[0];alpha_hold<={fx[8:0],2'b0};beta_hold<={fy[8:0],2'b0};
     tag_hold<={16'b0,oy,ox};last_hold<=ox==255 && oy==255;state<=OFFER;
    end
    OFFER:if(core_ready)begin
     if(ox==255)begin
      ox<=0;
      if(oy==255)state<=DRAIN;else begin oy<=oy+1;state<=TOP;end
     end else begin ox<=ox+1;state<=READ;end
    end
    DRAIN:if(core_valid && m_ready && core_last)begin state<=IDLE;done<=1;end
    ABORT:if(abort_ready)begin state<=IDLE;done<=1;end
    default:begin state<=IDLE;done<=1;error<=1;end
   endcase
  end
 end
endmodule
`default_nettype wire
