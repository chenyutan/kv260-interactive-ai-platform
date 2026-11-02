`timescale 1ns/1ps
module tb_roi;
 reg clk=0;always #5 clk=~clk;
 reg rst=1,start=0;
 wire cancel=1'b0;
 reg [63:0] cfg_base=64'h100002000;
 reg [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h;
 wire busy,done,error,req_valid,rsp_ready,m_valid,m_last;
 wire abort_valid;
 wire abort_ready;
 wire [63:0] req_addr;wire [11:0] req_words;wire [23:0] m_rgb,m_int8;wire [31:0] m_tag;
 reg rsp_valid=0,rsp_last=0,rsp_error=0,m_ready=0;
 reg [31:0] rsp_data=0;
 reg [31:0] memory[0:1100000];reg [47:0] expected[0:65535];
 reg pending=0;integer address_index,remaining;
 integer cycles=0,received=0,requests=0,read_words=0,total_checks=0;
 assign abort_ready=cycles%17==0;
 integer fixture=0,fd,rc,ncases,n,begin_cycle,stress=0,inject=0;
 integer saved_width,saved_height,saved_stride,saved_start,saved_words;
 reg checking=0,held=0,req_held=0;
 reg [80:0] held_data;reg [75:0] held_req;
 reg [31:0] random_state=32'h2600222;
 wire req_ready=!rst&&!pending&&!rsp_valid&&(!stress||random_state[2:0]!=0);
 bodyink_roi_frontend dut(.*);
 always @(posedge clk)begin
  cycles=cycles+1;
  if(cycles>18000000)$fatal(1,"FAIL: watchdog");
  random_state<={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
  if(rst)begin pending<=0;rsp_valid<=0;held=0;req_held=0;end
  else begin
   if(held && !abort_valid && {m_last,m_tag,m_int8,m_rgb}!==held_data)$fatal(1,"FAIL: output changed under stall");
   held=m_valid&&!m_ready;held_data={m_last,m_tag,m_int8,m_rgb};
   if(req_held && (!req_valid || {req_addr,req_words}!==held_req))$fatal(1,"FAIL: request changed under stall");
   req_held=req_valid&&!req_ready;held_req={req_addr,req_words};
   if(req_valid&&req_ready)begin
    if(req_addr<64'h100002000+saved_start || (req_addr-64'h100002000-saved_start)%saved_stride!=0 || (req_addr-64'h100002000-saved_start)/saved_stride>=saved_height || req_words!=saved_words)$fatal(1,"FAIL: request bounds/stride/length");
    requests=requests+1;pending<=1;address_index<=(req_addr-64'h100002000)/4;remaining<=req_words;
   end
   if(!rsp_valid||rsp_ready)begin
    rsp_valid<=0;
    if(pending && (!stress || (random_state[5:3]!=0 && cycles%127<111)))begin
     rsp_valid<=1;rsp_data<=memory[address_index];rsp_last<=remaining==1;
     rsp_error<=inject==1 && remaining==saved_words-4;
     if(inject==2 && remaining==saved_words-4)rsp_last<=1;
     address_index<=address_index+1;remaining<=remaining-1;
     if(remaining==1)pending<=0;
    end
   end
   if(rsp_valid&&rsp_ready)read_words=read_words+1;
   if(abort_valid)begin
    if(req_valid||m_valid||done||!busy)$fatal(1,"FAIL: cancellation isolation");
    if(abort_ready)begin pending<=0;rsp_valid<=0;end
   end
   if(m_valid&&m_ready && checking)begin
    if(received>=65536)$fatal(1,"FAIL: extra output");
    if({m_int8,m_rgb}!==expected[received])begin
     $display("FAIL_DETAIL case=%0d index=%0d actual=%012h expected=%012h",fixture,received,{m_int8,m_rgb},expected[received]);$fatal(1,"FAIL: RGB/INT8 mismatch");
    end
    if(m_tag!==received || m_last!==(received==65535))$fatal(1,"FAIL: tag/last");
    received=received+1;total_checks=total_checks+1;
   end
  end
 end
 always @(negedge clk)m_ready=!rst && (!stress || (random_state[8:6]!=0 && cycles%173<143));
 task reset_all;
  begin
   @(negedge clk);rst=1;start=0;checking=0;
   repeat(4)@(negedge clk);rst=0;received=0;requests=0;read_words=0;
  end
 endtask
 task load_fixture(input integer id);
  string path;
  begin
   fixture=id;
   path=$sformatf("config%0d.txt",id);fd=$fopen(path,"r");if(!fd)$fatal(1,"FAIL: config file");
   rc=$fscanf(fd,"%d %d %d %d %d %d %d",cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h);$fclose(fd);
   if(rc!=7)$fatal(1,"FAIL: config fields");
   saved_width=cfg_width;saved_height=cfg_height;saved_stride=cfg_stride;cfg_base=64'h100002000;
   saved_start=(cfg_x/2)*4;saved_words=(((cfg_x+cfg_w<cfg_width)?(cfg_x+cfg_w):(cfg_width-1))/2)-(cfg_x/2)+1;
   path=$sformatf("frame%0d.hex",id);$readmemh(path,memory,0,saved_height*saved_stride/4-1);
   path=$sformatf("expected%0d.hex",id);$readmemh(path,expected);
  end
 endtask
 task launch;
  begin @(negedge clk);start=1;@(negedge clk);start=0;end
 endtask
 task run_good(input integer id,input integer stalls);
  begin
   reset_all();load_fixture(id);stress=stalls;inject=0;checking=1;begin_cycle=cycles;launch();
   // Configuration changes while busy must not affect the accepted task.
   cfg_width=2;cfg_stride=4;cfg_x=0;cfg_w=1;cfg_base=0;
   while(!done)@(negedge clk);
   if(error || received!=65536)$fatal(1,"FAIL: successful job completion");
   if(id==5 || id==6)if(requests!=1)$fatal(1,"FAIL: single-row reuse");
   $display("ROI_PASS case=%0d stress=%0d cycles=%0d rows=%0d read_bytes=%0d",id,stalls,cycles-begin_cycle,requests,read_words*4);
   checking=0;repeat(12)@(negedge clk);if(m_valid||busy||done)$fatal(1,"FAIL: job not drained");
  end
 endtask
 initial begin
  fd=$fopen("cases.txt","r");rc=$fscanf(fd,"%d",ncases);$fclose(fd);if(rc!=1)$fatal(1,"FAIL: cases");
  for(n=0;n<ncases;n=n+1)run_good(n,1);
  run_good(0,0);
  for(n=1;n<=2;n=n+1)begin
   reset_all();load_fixture(0);inject=n;stress=1;launch();
   while(!done)@(negedge clk);
   if(!error||busy)$fatal(1,"FAIL: memory error not reported");
  end
  reset_all();load_fixture(0);inject=0;launch();
  while(read_words<13)@(negedge clk);
  reset_all();repeat(10)@(negedge clk);if(busy||m_valid||req_valid)$fatal(1,"FAIL: reset failed to cancel job");
  // Full restart proves an interrupted fill did not leave a usable stale tag.
  run_good(4,1);
  reset_all();load_fixture(0);cfg_x=65535;launch();
  if(!done||!error||busy||requests)$fatal(1,"FAIL: invalid ROI accepted");
  $display("PASS: ROI pipeline checks=%0d memory_errors=2 mid_fill_reset=1 invalid_config=1",total_checks);$finish;
 end
endmodule
