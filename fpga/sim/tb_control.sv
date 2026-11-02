`timescale 1ns/1ps
module tb_control;
 reg clk=0;always #5 clk=~clk;
 reg rst=1;reg [7:0] s_awaddr=0,s_araddr=0;
 reg s_awvalid=0,s_wvalid=0,s_arvalid=0,s_bready=0,s_rready=0;
 reg [31:0] s_wdata=0;reg [3:0] s_wstrb=15;
 wire s_awready,s_wready,s_bvalid,s_arready,s_rvalid,irq;
 wire [31:0] s_rdata;wire [1:0] s_bresp,s_rresp;
 wire dma_start,dma_cancel;reg dma_done=0,dma_error=0;
 wire [63:0] cfg_base,cfg_output;wire [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h;
 integer checks=0,starts=0,cancels=0,ticks=0,i;
 reg bh=0,rh=0;reg [1:0] br;reg [33:0] rr;
 bodyink_axil_control dut(.*);
 always @(posedge clk)begin
  ticks=ticks+1;if(ticks>20000)$fatal(1,"FAIL: watchdog");
  if(!rst)begin
   if(bh&&(!s_bvalid||s_bresp!==br))$fatal(1,"FAIL: B changed under stall");
   if(rh&&(!s_rvalid||{s_rresp,s_rdata}!==rr))$fatal(1,"FAIL: R changed under stall");
   if(dma_start)starts=starts+1;if(dma_cancel)cancels=cancels+1;
  end
  bh=!rst&&s_bvalid&&!s_bready;br=s_bresp;rh=!rst&&s_rvalid&&!s_rready;rr={s_rresp,s_rdata};
 end
 task address(input [7:0] a,input integer delay_n);
 begin
  repeat(delay_n)@(negedge clk);s_awaddr=a;s_awvalid=1;
  do @(posedge clk);while(!s_awready);
  @(negedge clk);s_awvalid=0;
 end endtask
 task data(input [31:0] d,input [3:0] st,input integer delay_n);
 begin
  repeat(delay_n)@(negedge clk);s_wdata=d;s_wstrb=st;s_wvalid=1;
  do @(posedge clk);while(!s_wready);
  @(negedge clk);s_wvalid=0;
 end endtask
 task wr(input [7:0] a,input [31:0] d,input [3:0] st,input integer ad,dd,input [1:0] response);
 begin
  @(negedge clk);
  fork address(a,ad);data(d,st,dd);join
  while(!s_bvalid)@(negedge clk);
  repeat(5)@(negedge clk);
  if(s_bresp!==response)$fatal(1,"FAIL: B response at %h",a);
  s_bready=1;@(negedge clk);s_bready=0;checks=checks+1;
 end endtask
 task rd(input [7:0] a,input [31:0] value,input [1:0] response);
 begin
  @(negedge clk);s_araddr=a;s_arvalid=1;
  do @(posedge clk);while(!s_arready);
  @(negedge clk);s_arvalid=0;while(!s_rvalid)@(negedge clk);
  repeat(5)@(negedge clk);
  if(s_rresp!==response||s_rdata!==value)$fatal(1,"FAIL: read %h got %h expected %h",a,s_rdata,value);
  s_rready=1;@(negedge clk);s_rready=0;checks=checks+1;
 end endtask
 task finish_job(input bit err);
 begin @(negedge clk);dma_error=err;dma_done=1;@(negedge clk);dma_done=0;end endtask
 initial begin
  repeat(4)@(negedge clk);rst=0;
  rd(0,32'h42495001,0);rd(8,0,0);
  wr('h10,'h12345678,15,1,7,0);wr('h10,'h00ab00cd,5,7,1,0);rd('h10,'h12ab56cd,0);
  wr('h10,0,0,0,0,0);rd('h10,'h12ab56cd,0);
  wr('h14,1,15,0,3,0);wr('h18,'h1000,15,3,0,0);wr('h1c,2,15,0,0,0);
  wr('h20,{16'd1080,16'd1920},15,1,2,0);wr('h24,3840,15,2,1,0);
  wr('h28,420,15,0,0,0);wr('h2c,{16'd1080,16'd1080},15,0,0,0);wr('h30,77,15,0,0,0);
  wr('h0c,1,1,0,0,0);wr(4,1,0,0,0,0);if(starts!=0)$fatal(1,"FAIL: zero strobe launched");
  wr(4,1,15,0,0,0);rd(8,1,0);
  if(starts!=1||cfg_base!==64'h112ab56cd||cfg_output!==64'h200001000||cfg_width!=1920||cfg_x!=420)$fatal(1,"FAIL: snapshot");
  wr('h10,999,15,0,0,0);wr('h30,88,15,0,0,0);if(cfg_base!==64'h112ab56cd)$fatal(1,"FAIL: active changed");
  wr(4,1,15,0,0,2);wr(4,3,15,0,0,2);if(starts!=1)$fatal(1,"FAIL: duplicate start");
  wr(4,2,15,3,0,0);rd(8,1,0);if(cancels!=1||irq)$fatal(1,"FAIL: cancellation prematurely complete");
  finish_job(1);rd(8,14,0);rd('h34,77,0);if(!irq)$fatal(1,"FAIL: missing IRQ");
  wr(4,4,15,0,0,0);rd(8,0,0);if(irq)$fatal(1,"FAIL: IRQ not acknowledged");
  wr(4,1,15,0,0,0);if(cfg_base!==64'h1000003e7)$fatal(1,"FAIL: next snapshot");
  finish_job(0);rd(8,2,0);rd('h34,88,0);
  wr('h0c,0,1,0,0,0);if(irq)$fatal(1,"FAIL: IRQ disable");
  wr('h0c,1,1,0,0,0);if(!irq)$fatal(1,"FAIL: sticky completion");
  wr(0,0,15,0,0,2);wr('h11,0,15,0,0,2);wr('h40,0,15,0,0,2);wr(4,8,15,0,0,2);
  rd('h41,0,2);rd('h40,0,2);
  // An acknowledgement concurrent with completion must not lose the interrupt.
  wr(4,1,15,0,0,0);
  fork
   wr(4,4,15,0,0,0);
   begin
    while(!dut.aw_full||!dut.w_full)@(negedge clk);
    dma_done=1;dma_error=0;@(negedge clk);dma_done=0;
   end
  join
  rd(8,2,0);if(!irq)$fatal(1,"FAIL: completion lost to clear");
  // Read and write channels may progress independently.
  fork wr('h30,101,15,1,4,0);rd(0,32'h42495001,0);join
  rd('h30,101,0);
  // Discard an unmatched AW at reset; it must not combine with a later W.
  address('h30,1);@(negedge clk);rst=1;repeat(3)@(negedge clk);rst=0;
  rd('h30,0,0);rd(8,0,0);wr('h30,99,15,4,0,0);rd('h30,99,0);
  $display("PASS: AXIL control checks=%0d independent_AW_W=1 stalled_responses=1 shadow_lock=1 cancel_drain=1",checks);$finish;
 end
endmodule
