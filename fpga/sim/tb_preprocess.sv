`timescale 1ns/1ps
module tb_preprocess;
 reg clk=0;always #5 clk=~clk;
 reg rst=1,s_valid=0,m_ready=0,s_last=0;
 reg [95:0] s_yuv=0;reg [10:0] s_alpha=0,s_beta=0;reg [31:0] s_tag=0;
 wire s_ready,m_valid,m_last;wire [23:0] m_rgb,m_int8;wire [31:0] m_tag;
 bodyink_preprocess_core dut(.*);
 localparam MAX=500000;
 reg [118:0] vectors[0:MAX-1];reg [47:0] expected[0:MAX-1];
 integer total,fd,rc,sent=0,received=0,cycles=0,stall_cycles=0,checks=0;
 reg [31:0] random_state=32'h2600913;
 reg running=0,held=0;reg [80:0] held_data;
 always @(posedge clk)begin
  cycles=cycles+1;
  if(cycles>6000000)$fatal(1,"FAIL: watchdog");
  if(rst)held=0;
  else begin
   if(held && {m_last,m_tag,m_int8,m_rgb}!==held_data)$fatal(1,"FAIL: changed output during backpressure");
   held=m_valid&&!m_ready;held_data={m_last,m_tag,m_int8,m_rgb};
   if(held)stall_cycles=stall_cycles+1;
   if(running)begin
    if(s_valid&&s_ready)sent=sent+1;
    if(m_valid&&m_ready)begin
     if(received>=sent)$fatal(1,"FAIL: unexpected output");
     if({m_int8,m_rgb}!==expected[received])begin
      $display("FAIL_DETAIL index=%0d actual=%012h expected=%012h",received,{m_int8,m_rgb},expected[received]);$fatal(1,"FAIL: pixel mismatch");
     end
     if(m_tag!==received || m_last!==vectors[received][118])$fatal(1,"FAIL: metadata mismatch");
     received=received+1;checks=checks+1;
    end
   end
  end
 end
 task reset_core;
  begin
   @(negedge clk);running=0;rst=1;s_valid=0;m_ready=0;
   repeat(3)@(negedge clk);
   if(m_valid!==0||s_ready!==0)$fatal(1,"FAIL: reset interface");
   rst=0;sent=0;received=0;running=1;
  end
 endtask
 task run_case(input integer n,input integer stalls);
  integer first_cycle;
  begin
   reset_core();first_cycle=cycles;
   while(received<n)begin
    random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
    m_ready=stalls?(random_state[2:0]!=0 && cycles%311<279):1;
    // sent is updated only on a sampled rising-edge handshake. Do not infer
    // acceptance from combinational ready after changing downstream ready.
    if(!s_valid||s_tag<sent)begin
     s_valid=sent<n && (!stalls||random_state[5:4]!=0);
     if(sent<n)begin
      s_yuv=vectors[sent][95:0];s_alpha=vectors[sent][106:96];s_beta=vectors[sent][117:107];s_last=vectors[sent][118];s_tag=sent;
     end
    end
    @(negedge clk);
   end
   s_valid=0;repeat(10)@(negedge clk);
   if(m_valid)$fatal(1,"FAIL: output after drain");
   $display("CASE_PASS n=%0d stalls=%0d cycles=%0d",n,stalls,cycles-first_cycle-10);
   if(!stalls && cycles-first_cycle-10!=n+7)$fatal(1,"FAIL: continuous II not 1");
  end
 endtask
 initial begin
  $readmemh("input.hex",vectors);$readmemh("expected.hex",expected);
  fd=$fopen("count.txt","r");if(!fd)$fatal(1,"FAIL: missing vectors");rc=$fscanf(fd,"%d",total);$fclose(fd);if(rc!=1||total>MAX)$fatal(1,"FAIL: count");
  repeat(3)@(negedge clk);rst=0;s_valid=1;m_ready=0;
  repeat(12)@(negedge clk);
  if(!m_valid||s_ready)$fatal(1,"FAIL: failed to fill stalled pipeline");
  run_case(1024,1);run_case(total,0);run_case(total,1);
  $display("PASS: exact RGB/INT8 checks=%0d output_stall_cycles=%0d reset_flush=1",checks,stall_cycles);
  $finish;
 end
endmodule
