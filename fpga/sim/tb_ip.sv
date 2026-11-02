`timescale 1ns/1ps
module tb_ip;
 parameter MAX_BURST=256,QUICK=0;
 reg clk=0;always #5 clk=~clk;
 reg rst=1,start=0,cancel=0;
 localparam [63:0] SRC=64'h100000ffc,DST=64'h200000ffc;
 reg [63:0] cfg_base=SRC,cfg_output=DST;
 reg [15:0] cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h;
 wire busy,done,error,arvalid,rready,awvalid,wvalid,wlast,bready;
 wire [63:0] araddr,awaddr;wire [7:0] arlen,awlen;wire [2:0] arsize,awsize;wire [1:0] arburst,awburst;
 wire [31:0] wdata;wire [3:0] wstrb;
 reg rvalid=0,rlast=0,bvalid=0;reg [31:0] rdata=0;reg [1:0] rresp=0,bresp=0;
 reg read_pending=0,write_pending=0,response_pending=0;
 integer ri,rl,wi,wl,cycles=0,source_words,received_words=0,read_beats=0,ars=0,aws=0;
 integer fd,rc,fixture=0,mode=0,i,j,begin_cycle,checks=0;
 reg [31:0] memory[0:1100000];reg [47:0] gold[0:65535];reg written[0:49151];
 reg [31:0] random_state=32'h260333;
 reg force_ar=0,force_aw=0,force_w=0,inject_used=0;
 reg hold_ar=0,hold_aw=0,hold_w=0;reg [71:0] old_ar,old_aw;reg [36:0] old_w;
 wire arready=!rst&&!read_pending&&!rvalid&&!force_ar&&random_state[2:0]!=0;
 wire awready=!rst&&!write_pending&&!response_pending&&!bvalid&&!force_aw&&random_state[5:3]!=0;
 wire wready=!rst&&write_pending&&!force_w&&random_state[8:6]!=0&&cycles%149<127;

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

 function automatic [31:0] expected_word(input integer word_index);
  integer k,offset,pixel,channel;
  begin
   for(k=0;k<4;k=k+1)begin offset=word_index*4+k;pixel=offset/3;channel=offset%3;expected_word[k*8+:8]=gold[pixel][24+channel*8+:8];end
  end
 endfunction
 always @(posedge clk)begin
  cycles=cycles+1;if(cycles>20000000)$fatal(1,"FAIL: DMA watchdog");
  random_state<={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
  if(rst)begin
   read_pending<=0;write_pending<=0;response_pending<=0;rvalid<=0;bvalid<=0;hold_ar=0;hold_aw=0;hold_w=0;
  end else begin
   if(hold_ar&&(!arvalid||{araddr,arlen}!==old_ar))$fatal(1,"FAIL: stalled AR changed");
   if(hold_aw&&(!awvalid||{awaddr,awlen}!==old_aw))$fatal(1,"FAIL: stalled AW changed");
   if(hold_w&&(!wvalid||{wlast,wstrb,wdata}!==old_w))$fatal(1,"FAIL: stalled W changed");
   hold_ar=arvalid&&!arready;old_ar={araddr,arlen};hold_aw=awvalid&&!awready;old_aw={awaddr,awlen};hold_w=wvalid&&!wready;old_w={wlast,wstrb,wdata};
   if(arvalid&&arready)begin
    if(arsize!=2||arburst!=1||araddr[1:0]!=0||({1'b0,araddr[11:0]}+(arlen+1)*4)>4096||arlen+1>MAX_BURST)$fatal(1,"FAIL: invalid AR burst");
    if(araddr<SRC||(araddr-SRC)/4+arlen>=source_words)$fatal(1,"FAIL: source bounds");
    ars=ars+1;read_pending<=1;ri<=(araddr-SRC)/4;rl<=arlen+1;
   end
   if(!rvalid||rready)begin
    rvalid<=0;
    if(read_pending&&random_state[11:9]!=0&&cycles%127<109)begin
     rvalid<=1;rdata<=memory[ri];rresp<=0;rlast<=rl==1;ri<=ri+1;rl<=rl-1;
     if(mode==1&&!inject_used&&read_beats>3)begin rresp<=2;inject_used<=1;end
     if(mode==6&&!inject_used&&rl>2)begin rlast<=1;read_pending<=0;inject_used<=1;end
     if(rl==1)read_pending<=0;
     // Malformed slave: omit the expected RLAST, then terminate one beat later.
     if(mode==8&&!inject_used&&rl==1)begin rlast<=0;rl<=1;read_pending<=1;inject_used<=1;end
    end
   end
   if(rvalid&&rready)read_beats=read_beats+1;
   if(awvalid&&awready)begin
    if(awsize!=2||awburst!=1||awaddr[1:0]!=0||({1'b0,awaddr[11:0]}+(awlen+1)*4)>4096||awlen+1>MAX_BURST)$fatal(1,"FAIL: invalid AW burst");
    if(awaddr<DST||(awaddr-DST)/4+awlen>=49152)$fatal(1,"FAIL: destination bounds");
    aws=aws+1;write_pending<=1;wi<=(awaddr-DST)/4;wl<=awlen+1;
   end
   if(wvalid&&wready)begin
    if(wstrb!=15||wlast!==(wl==1))$fatal(1,"FAIL: WSTRB/WLAST");
    if(written[wi])$fatal(1,"FAIL: duplicate destination write");
    if(wdata!==expected_word(wi))begin $display("FAIL_DETAIL case=%0d word=%0d actual=%08h expected=%08h",fixture,wi,wdata,expected_word(wi));$fatal(1,"FAIL: tensor bytes");end
    written[wi]=1;checks=checks+1;received_words=received_words+1;wi<=wi+1;wl<=wl-1;
    if(wl==1)begin write_pending<=0;response_pending<=1;end
   end
   if(bvalid&&bready)bvalid<=0;
   if(response_pending&&!bvalid&&random_state[15:12]==0)begin
    response_pending<=0;bvalid<=1;bresp<=0;
    if(mode==2&&!inject_used)begin bresp<=2;inject_used<=1;end
   end
   if(done&&(read_pending||rvalid||write_pending||response_pending||bvalid||arvalid||awvalid||wvalid))$fatal(1,"FAIL: completion before bus drained");
  end
 end
 task reset_all;
  begin
   @(negedge clk);rst=1;start=0;force_ar=0;force_aw=0;force_w=0;
   repeat(4)@(negedge clk);rst=0;received_words=0;read_beats=0;ars=0;aws=0;inject_used=0;
   for(j=0;j<49152;j=j+1)written[j]=0;
  end
 endtask
 task fixture_load(input integer id);
  string path;
  begin
   fixture=id;cfg_base=SRC;cfg_output=DST;
   path=$sformatf("config%0d.txt",id);fd=$fopen(path,"r");rc=$fscanf(fd,"%d %d %d %d %d %d %d",cfg_width,cfg_height,cfg_stride,cfg_x,cfg_y,cfg_w,cfg_h);$fclose(fd);if(rc!=7)$fatal(1,"FAIL: config");
   source_words=cfg_stride*cfg_height/4;
   path=$sformatf("frame%0d.hex",id);$readmemh(path,memory,0,source_words-1);
   path=$sformatf("expected%0d.hex",id);$readmemh(path,gold);
  end
 endtask
 task launch;
  begin
 axwrite('h10,cfg_base[31:0]);axwrite('h14,cfg_base[63:32]);
 axwrite('h18,cfg_output[31:0]);axwrite('h1c,cfg_output[63:32]);
 axwrite('h20,{cfg_height,cfg_width});axwrite('h24,cfg_stride);
 axwrite('h28,{cfg_y,cfg_x});axwrite('h2c,{cfg_h,cfg_w});axwrite('h30,fixture);
 axwrite('h0c,1);axwrite('h04,1);
 end
 endtask
 task wait_done(input integer success);
  begin
   while(!done)@(negedge clk);
   if(error!==!success)$fatal(1,"FAIL: job error status");
   if(success)begin
    if(received_words!=49152)$fatal(1,"FAIL: tensor size");
    for(j=0;j<49152;j=j+1)if(!written[j])$fatal(1,"FAIL: missing tensor word");
   end
   $display("DMA_PASS case=%0d mode=%0d max_burst=%0d cycles=%0d read_bytes=%0d write_bytes=%0d AR=%0d AW=%0d",fixture,mode,MAX_BURST,cycles-begin_cycle,read_beats*4,received_words*4,ars,aws);
   repeat(8)@(negedge clk);if(!irq || dut.control.done_tag!=fixture)$fatal(1,"FAIL: completion IRQ/tag");if(busy||done||arvalid||awvalid||wvalid)$fatal(1,"FAIL: idle after completion");
  end
 endtask
 initial begin
  for(i=0;i<(QUICK?1:4);i=i+1)begin reset_all();fixture_load(QUICK?4:i);mode=0;begin_cycle=cycles;launch();wait_done(1);end
  for(i=1;i<=2;i=i+1)begin reset_all();fixture_load(0);mode=i;begin_cycle=cycles;launch();wait_done(0);end
  reset_all();fixture_load(0);mode=3;force_ar=1;begin_cycle=cycles;launch();
  while(!arvalid)@(negedge clk);axwrite(4,2);repeat(13)@(negedge clk);if(!busy||done||!arvalid)$fatal(1,"FAIL: cancelled stalled AR withdrawn");force_ar=0;wait_done(0);
  reset_all();fixture_load(0);mode=4;force_w=1;begin_cycle=cycles;launch();
  while(!wvalid)@(negedge clk);axwrite(4,2);repeat(13)@(negedge clk);if(!busy||done||!wvalid)$fatal(1,"FAIL: cancelled stalled W withdrawn");force_w=0;wait_done(0);
  reset_all();fixture_load(0);mode=7;force_aw=1;begin_cycle=cycles;launch();
  while(!awvalid)@(negedge clk);axwrite(4,2);repeat(13)@(negedge clk);if(!busy||done||!awvalid)$fatal(1,"FAIL: cancelled stalled AW withdrawn");force_aw=0;wait_done(0);
  reset_all();fixture_load(0);mode=6;begin_cycle=cycles;launch();wait_done(0);
  reset_all();fixture_load(0);mode=8;begin_cycle=cycles;launch();wait_done(0);
  fixture_load(4);mode=0;inject_used=0;received_words=0;read_beats=0;ars=0;aws=0;for(j=0;j<49152;j=j+1)written[j]=0;begin_cycle=cycles;launch();wait_done(1);
  reset_all();fixture_load(0);mode=0;launch();while(read_beats<15)@(negedge clk);reset_all();repeat(20)@(negedge clk);if(busy||rvalid||wvalid)$fatal(1,"FAIL: coordinated reset");
  // Restart without another reset after successful completion exercises sticky flags.
  fixture_load(4);mode=0;begin_cycle=cycles;launch();wait_done(1);
  fixture_load(6);for(j=0;j<49152;j=j+1)written[j]=0;received_words=0;read_beats=0;ars=0;aws=0;begin_cycle=cycles;launch();wait_done(1);
  reset_all();fixture_load(0);mode=9;cfg_output=DST+1;begin_cycle=cycles;launch();wait_done(0);if(aws)$fatal(1,"FAIL: unaligned destination accepted");
  reset_all();fixture_load(0);mode=10;cfg_base=SRC+1;begin_cycle=cycles;launch();wait_done(0);if(ars||aws)$fatal(1,"FAIL: unaligned source accepted");
  $display("PASS: AXIL integrated DMA checks=%0d errors=4 cancel_AR_AW_W=3 coordinated_reset=1 restart_after_error=1 unaligned_rejected=2",checks);$finish;
 end
endmodule
