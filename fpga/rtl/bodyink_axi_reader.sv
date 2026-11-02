`timescale 1ns/1ps
`default_nettype none
// One outstanding AXI4 INCR burst, 32-bit data, aligned row requests.
// Never withdraws a stalled AR. Cancellation drains to RLAST before ack.
module bodyink_axi_reader #(parameter MAX_BURST=256)(
 input wire clk,rst,
 input wire req_valid,output wire req_ready,input wire [63:0] req_addr,input wire [11:0] req_words,
 output wire rsp_valid,input wire rsp_ready,output wire [31:0] rsp_data,output wire rsp_last,rsp_error,
 input wire abort_valid,output wire abort_ready,
 output wire arvalid,input wire arready,output wire [63:0] araddr,output wire [7:0] arlen,
 output wire [2:0] arsize,output wire [1:0] arburst,
 input wire rvalid,output wire rready,input wire [31:0] rdata,input wire [1:0] rresp,input wire rlast
);
 localparam IDLE=0,PLAN=1,AR=2,DATA=3,BAD=4;
 reg [2:0] state;reg cancelling;
 reg [63:0] address;reg [11:0] remaining;reg [8:0] burst_size,beats_left;
 wire cancel=cancelling||abort_valid;
 wire [10:0] boundary_words=11'd1024-{1'b0,address[11:2]};
 wire [11:0] bound_limit=remaining<boundary_words?remaining:boundary_words;
 wire [8:0] next_size=bound_limit<MAX_BURST?bound_limit:MAX_BURST;
 wire beat_fault=rresp!=0 || rlast!=(beats_left==1);
 assign req_ready=!rst && state==IDLE && !cancel;
 assign abort_ready=!rst && state==IDLE;
 assign arvalid=!rst && state==AR;assign araddr=address;assign arlen=burst_size-1;
 assign arsize=3'd2;assign arburst=2'd1;
 assign rsp_valid=!rst && !cancel && ((state==DATA&&rvalid)||state==BAD);
 assign rsp_data=state==BAD?0:rdata;assign rsp_last=state==BAD||remaining==1;
 assign rsp_error=state==BAD||beat_fault;
 assign rready=!rst && state==DATA && (cancel||rsp_ready);
 always @(posedge clk)begin
  if(rst)begin state<=IDLE;cancelling<=0;end
  else begin
   if(abort_valid)cancelling<=1;
   case(state)
    IDLE:begin
     if(abort_valid)cancelling<=0;
     if(req_valid&&req_ready)begin
      address<=req_addr;remaining<=req_words;
      if(req_addr[1:0]!=0||req_words==0||req_words>2048 || req_addr>64'hffffffffffffffff-({52'b0,req_words}<<2))state<=BAD;
      else state<=PLAN;
     end
    end
    PLAN:if(cancel)state<=IDLE;else begin burst_size<=next_size;beats_left<=next_size;state<=AR;end
    AR:if(arready)state<=DATA;
    DATA:if(rvalid&&rready)begin
     if(beat_fault)cancelling<=1;
     if(!cancel)remaining<=remaining-1;
     if(beats_left>0)beats_left<=beats_left-1;
     if(rlast)begin
      if(cancel||beat_fault||remaining==1)state<=IDLE;
      else begin address<=address+({55'b0,burst_size}<<2);state<=PLAN;end
     end
    end
    BAD:if(cancel||(rsp_valid&&rsp_ready))state<=IDLE;
    default:begin state<=IDLE;cancelling<=1;end
   endcase
  end
 end
endmodule
`default_nettype wire
