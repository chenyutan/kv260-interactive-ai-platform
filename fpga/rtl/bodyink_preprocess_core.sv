`timescale 1ns/1ps
`default_nettype none
// Four source samples (00,10,01,11), each little-endian {V,U,Y}.
// Crop/address generation and memory transport are outside this arithmetic core.
// Q11 bilinear weights; clip RGB BEFORE interpolation; channel order R,G,B.
module bodyink_preprocess_core #(
 parameter R_ROM="quant_r.hex", G_ROM="quant_g.hex", B_ROM="quant_b.hex"
)(
 input wire clk, input wire rst,
 input wire s_valid, output wire s_ready,
 input wire [95:0] s_yuv, input wire [10:0] s_alpha,s_beta,
 input wire s_last, input wire [31:0] s_tag,
 output wire m_valid, input wire m_ready,
 output reg [23:0] m_rgb, output reg [23:0] m_int8,
 output wire m_last, output wire [31:0] m_tag
);
 reg [6:0] valid,last;
 reg [31:0] tag[0:6];
 wire advance=~valid[6] | m_ready;
 assign s_ready=~rst & advance;
 assign m_valid=~rst & valid[6];
 assign m_last=last[6]; assign m_tag=tag[6];
 reg [7:0] qr[0:255],qg[0:255],qb[0:255];
 initial begin $readmemh(R_ROM,qr);$readmemh(G_ROM,qg);$readmemh(B_ROM,qb);end
 reg signed [31:0] cy[0:3],vr[0:3],ug[0:3],vg[0:3],ub[0:3];
 reg [7:0] rgb1[0:3][0:2];
 reg [10:0] alpha[0:1],beta[0:3];
 reg signed [31:0] hp2[0:1][0:2];
 reg [7:0] base2[0:1][0:2];
 reg signed [31:0] horiz3[0:1][0:2];
 reg signed [31:0] vp4[0:2],top4[0:2];
 reg [7:0] rgb5[0:2];
 function automatic [7:0] color_clip(input signed [31:0] v);
  reg signed [31:0] z;
  begin z=v>>>20;if(z<0)color_clip=0;else if(z>255)color_clip=255;else color_clip=z[7:0];end
 endfunction
 integer i,j;
 always @(posedge clk) begin
  if(rst) begin valid<=0;last<=0;end
  else if(advance) begin
   valid<={valid[5:0],s_valid};last<={last[5:0],s_last};
   tag[0]<=s_tag;for(i=1;i<7;i=i+1)tag[i]<=tag[i-1];
   alpha[0]<=s_alpha;alpha[1]<=alpha[0];
   beta[0]<=s_beta;for(i=1;i<4;i=i+1)beta[i]<=beta[i-1];
   // S0: 20 independent constant products. Signed 32-bit headroom includes rounding.
   for(i=0;i<4;i=i+1)begin
    cy[i]<=((s_yuv[i*24+:8]>16)?(s_yuv[i*24+:8]-32'd16):32'd0)*32'd1220542+32'd524288;
    vr[i]<=($signed({1'b0,s_yuv[i*24+16+:8]})-32'sd128)*32'sd1673527;
    ug[i]<=($signed({1'b0,s_yuv[i*24+8+:8]})-32'sd128)*32'sd409993;
    vg[i]<=($signed({1'b0,s_yuv[i*24+16+:8]})-32'sd128)*32'sd852492;
    ub[i]<=($signed({1'b0,s_yuv[i*24+8+:8]})-32'sd128)*32'sd2116026;
   end
   // S1: exact limited-range BT.601 conversion, saturation of all four samples.
   for(i=0;i<4;i=i+1)begin
    rgb1[i][0]<=color_clip(cy[i]+vr[i]);
    rgb1[i][1]<=color_clip(cy[i]-ug[i]-vg[i]);
    rgb1[i][2]<=color_clip(cy[i]+ub[i]);
   end
   // S2/S3: horizontal interpolation, retained in Q11 (no intermediate rounding).
   for(i=0;i<2;i=i+1)for(j=0;j<3;j=j+1)begin
    hp2[i][j]<=($signed({1'b0,rgb1[i*2+1][j]})-$signed({1'b0,rgb1[i*2][j]}))*$signed({1'b0,alpha[1]});
    base2[i][j]<=rgb1[i*2][j];
    horiz3[i][j]<=$signed({1'b0,base2[i][j],11'b0})+hp2[i][j];
   end
   // S4/S5: vertical interpolation; legal weights keep the final sum in int32.
   for(j=0;j<3;j=j+1)begin
    vp4[j]<=(horiz3[1][j]-horiz3[0][j])*$signed({1'b0,beta[3]});
    top4[j]<=horiz3[0][j];
    rgb5[j]<=((top4[j]<<<11)+vp4[j]+32'sd2097152)>>>22;
   end
   // S6: model-specific 256-entry ROM/channel, exact production INT8 mapping.
   m_rgb<={rgb5[2],rgb5[1],rgb5[0]};
   m_int8<={qb[rgb5[2]],qg[rgb5[1]],qr[rgb5[0]]};
  end
 end
endmodule
`default_nettype wire
