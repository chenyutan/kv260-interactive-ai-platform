`timescale 1ns/1ps
`default_nettype none
// RGB INT8 low-byte R -> contiguous NHWC bytes. Collect a complete burst before
// AW; after AW publication, finish W/B even when cancelled. One burst outstanding.
module bodyink_axi_writer #(parameter PIXELS=65536,MAX_BURST=256)(
 input wire clk,rst,start,input wire [63:0] cfg_addr,input wire cancel,
 output wire busy,output reg done,error,
 input wire s_valid,output wire s_ready,input wire [23:0] s_data,input wire s_last,input wire [31:0] s_tag,
 output wire awvalid,input wire awready,output wire [63:0] awaddr,output wire [7:0] awlen,
 output wire [2:0] awsize,output wire [1:0] awburst,
 output wire wvalid,input wire wready,output reg [31:0] wdata,output wire [3:0] wstrb,output wire wlast,
 input wire bvalid,output wire bready,input wire [1:0] bresp
);
 localparam WORDS=PIXELS*3/4;
 localparam IDLE=0,PLAN=1,PACK=2,AW=3,LOAD=4,WRITE=5,RESP=6;
 reg [2:0] state;reg cancelling;
 reg [63:0] address;reg [31:0] words_left,pixels_in;
 reg [8:0] burst_size,filled,index;
 reg [55:0] bytes_hold;reg [2:0] byte_count;
 (* ram_style="block" *) reg [31:0] buffer[0:MAX_BURST-1];
 wire [10:0] boundary_words=11'd1024-{1'b0,address[11:2]};
 wire [31:0] bound_limit=words_left<boundary_words?words_left:boundary_words;
 wire [8:0] next_size=bound_limit<MAX_BURST?bound_limit:MAX_BURST;
 wire stopping=cancel||cancelling;
 assign busy=state!=IDLE;
 assign s_ready=!rst && state==PACK && byte_count<4 && pixels_in<PIXELS && !stopping;
 assign awvalid=!rst&&state==AW;assign awaddr=address;assign awlen=burst_size-1;
 assign awsize=3'd2;assign awburst=2'd1;
 assign wvalid=!rst&&state==WRITE;assign wstrb=4'hf;assign wlast=index==burst_size-1;
 assign bready=!rst&&state==RESP;
 localparam BUFFER_AW=MAX_BURST>1?$clog2(MAX_BURST):1;
 wire [BUFFER_AW-1:0] read_index=state==LOAD?{BUFFER_AW{1'b0}}:index+1'b1;
 wire read_enable=state==LOAD || (state==WRITE && wready && !wlast);
 always @(posedge clk)begin
  if(!rst && state==PACK && byte_count>=4 && !stopping)buffer[filled]<=bytes_hold[31:0];
  // A single synchronous read expression infers one BRAM read port.
  if(!rst && read_enable)wdata<=buffer[read_index];
 end
 always @(posedge clk)begin
  if(rst)begin state<=IDLE;done<=0;error<=0;cancelling<=0;end
  else begin
   done<=0;
   if(cancel&&busy)begin cancelling<=1;error<=1;end
   case(state)
    IDLE:if(start)begin
     error<=0;cancelling<=0;bytes_hold<=0;byte_count<=0;pixels_in<=0;words_left<=WORDS;address<=cfg_addr;
     if(cfg_addr[1:0]!=0 || cfg_addr>64'hffffffffffffffff-PIXELS*3 || PIXELS%4!=0)begin done<=1;error<=1;end
     else state<=PLAN;
    end
    PLAN:if(stopping)begin state<=IDLE;done<=1;error<=1;end
     else begin burst_size<=next_size;filled<=0;state<=PACK;end
    PACK:if(stopping)begin state<=IDLE;done<=1;error<=1;end
     else if(byte_count>=4)begin
      bytes_hold<=bytes_hold>>32;byte_count<=byte_count-4;filled<=filled+1;
      if(filled==burst_size-1)state<=AW;
     end else if(s_valid&&s_ready)begin
      if(s_tag!=pixels_in || s_last!=(pixels_in==PIXELS-1))begin state<=IDLE;done<=1;error<=1;end
      else begin bytes_hold<=bytes_hold|({32'b0,s_data}<<(byte_count*8));byte_count<=byte_count+3;pixels_in<=pixels_in+1;end
     end
    AW:if(awready)begin index<=0;state<=LOAD;end
    LOAD:state<=WRITE;
    WRITE:if(wready)begin if(wlast)state<=RESP;else index<=index+1;end
    RESP:if(bvalid)begin
     if(bresp!=0||stopping)begin error<=1;done<=1;state<=IDLE;end
     else if(words_left==burst_size)begin
      if(pixels_in!=PIXELS || byte_count!=0)error<=1;
      state<=IDLE;done<=1;
     end else begin words_left<=words_left-burst_size;address<=address+({55'b0,burst_size}<<2);state<=PLAN;end
    end
    default:begin state<=IDLE;done<=1;error<=1;end
   endcase
  end
 end
endmodule
`default_nettype wire
