// BodyInk Hourglass CPU decode: OpenCV INTER_CUBIC (A=-0.75), 4x resize,
// and Vitis AI 2.5 hourglass find_point traversal/tie/threshold semantics.
// Keeps interpolated heatmap amplitude; it is not a probability.
#include <math.h>
#include <stdint.h>
#ifndef BODYINK_NEON_BOUNDS
#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#define BODYINK_NEON_BOUNDS 1
#else
#define BODYINK_NEON_BOUNDS 0
#endif
#endif
#if BODYINK_NEON_BOUNDS
#include <arm_neon.h>
#endif
#ifndef BODYINK_DECODE_THREADS
#define BODYINK_DECODE_THREADS 1
#endif

enum {H=64, W=64, K=16, O=256};
typedef struct { int index[4]; float weight[4]; } Sample;
static void samples(Sample* s) {
  for(int x=0;x<O;++x) {
    const float source=(x+.5f)*.25f-.5f;
    const int base=(int)floorf(source);
    const float f=source-base, a=-.75f;
    s[x].weight[0]=((a*(f+1)-5*a)*(f+1)+8*a)*(f+1)-4*a;
    s[x].weight[1]=((a+2)*f-(a+3))*f*f+1;
    s[x].weight[2]=((a+2)*(1-f)-(a+3))*(1-f)*(1-f)+1;
    s[x].weight[3]=1-s[x].weight[0]-s[x].weight[1]-s[x].weight[2];
    for(int j=0;j<4;++j){int i=base+j-1;s[x].index[j]=i<0?0:i>=W?W-1:i;}
  }
}

static void tile(const int8_t* heat,int k,float scale,const Sample* samples,
                 int bx,int by,const int8_t* bounds_min,const int8_t* bounds_max,
                 float* best,int* best_x,int* best_y) {
  const int x0=4*bx+2<1?1:4*bx+2;
  const int y0=4*by+2<1?1:4*by+2;
  const int x1=4*bx+5>254?254:4*bx+5;
  const int y1=4*by+5>254?254:4*by+5;
  const Sample* sy=&samples[y0];
  const int offset=((by+1)*(W+1)+bx+1)*K+k;
  const int minimum=bounds_min[offset],maximum=bounds_max[offset];
  // Cubic A=-.75 has negative 1D mass <= 3/16; the separable
  // 2D negative mass is <= 2*(3/16)*(1+3/16) = .4453125.
  // Absolute 1/1024 slack (before power-of-two scaling) exceeds float
  // evaluation roundoff for int8 samples, including cancellation near zero.
  // This bounds every tile, including clamped edges; it is not top-k search.
  float upper=(maximum+(maximum-minimum)*.4453125f+.0009765625f)*scale;
  if(upper<*best || (upper==*best &&
       (x0>*best_x || (x0==*best_x && y0>=*best_y))))return;
  float horizontal[4][4];
  for(int yy=0;yy<4;++yy)for(int x=x0;x<=x1;++x) {
    const Sample p=samples[x];
    const int row=sy->index[yy]*W*K+k;
    const float v0=heat[row+p.index[0]*K]*scale;
    const float v1=heat[row+p.index[1]*K]*scale;
    const float v2=heat[row+p.index[2]*K]*scale;
    const float v3=heat[row+p.index[3]*K]*scale;
    horizontal[yy][x-x0]=v0*p.weight[0]+v1*p.weight[1]+v2*p.weight[2]+v3*p.weight[3];
  }
  for(int x=x0;x<=x1;++x)for(int y=y0;y<=y1;++y) {
    const Sample p=samples[y]; const int dx=x-x0;
    const float value=horizontal[0][dx]*p.weight[0]+horizontal[1][dx]*p.weight[1]
                    +horizontal[2][dx]*p.weight[2]+horizontal[3][dx]*p.weight[3];
    if(value<=.001f)continue;
    if(value>*best || (value==*best &&
       (x<*best_x || (x==*best_x && y<*best_y)))) {
      *best=value;*best_x=x;*best_y=y;
    }
  }
}

// Separable 4x4 min/max bounds. Channel-inner loops allow ARM NEON / host
// auto-vectorization; every heatmap byte is reused across adjacent tiles.
// Scratch is per call (no shared mutable state).
static void tile_bounds(const int8_t* heat,int8_t* lo,int8_t* hi,
                        int* maxima,int* minima,int* peaks) {
  int8_t row_lo[H*(W+1)*K],row_hi[H*(W+1)*K];
#if BODYINK_NEON_BOUNDS
  // Integer-only SIMD: signed extrema and strict-gt coarse peak updates.
  // A peak fits in uint16 (0..4095); sign extension of each 0xff mask
  // makes a uint16 0xffff selection mask without changing tie semantics.
  int8x16_t mx=vdupq_n_s8(-128),mn=vdupq_n_s8(127);
  uint16x8_t peak_l=vdupq_n_u16(0),peak_h=vdupq_n_u16(0);
  for(int y=0;y<H;++y) {
    const int8_t* row=heat+y*W*K;
    int8x16_t a=vld1q_s8(row),b=a,c=a,d=vld1q_s8(row+K);
    for(int bx=-1;bx<W;++bx) {
      const int q=(y*(W+1)+bx+1)*K;
      vst1q_s8(row_lo+q,vminq_s8(vminq_s8(a,b),vminq_s8(c,d)));
      vst1q_s8(row_hi+q,vmaxq_s8(vmaxq_s8(a,b),vmaxq_s8(c,d)));
      if(bx>=0) {
        const uint8x16_t gt=vcgtq_s8(b,mx);
        const uint16x8_t ml=vreinterpretq_u16_s16(vmovl_s8(
            vreinterpret_s8_u8(vget_low_u8(gt))));
        const uint16x8_t mh=vreinterpretq_u16_s16(vmovl_s8(
            vreinterpret_s8_u8(vget_high_u8(gt))));
        const uint16x8_t index=vdupq_n_u16((uint16_t)(y*W+bx));
        peak_l=vbslq_u16(ml,index,peak_l);
        peak_h=vbslq_u16(mh,index,peak_h);
        mx=vmaxq_s8(mx,b);mn=vminq_s8(mn,b);
      }
      a=b;b=c;c=d;
      const int next=bx+3<W?bx+3:W-1;
      d=vld1q_s8(row+next*K);
    }
  }
  int8_t max8[K],min8[K];uint16_t peak16[K];
  vst1q_s8(max8,mx);vst1q_s8(min8,mn);
  vst1q_u16(peak16,peak_l);vst1q_u16(peak16+8,peak_h);
  for(int k=0;k<K;++k){maxima[k]=max8[k];minima[k]=min8[k];peaks[k]=peak16[k];}
  for(int by=-1;by<H;++by) {
    int iy[4];
    for(int j=0;j<4;++j){int y=by+j-1;iy[j]=y<0?0:y>=H?H-1:y;}
    for(int bx=-1;bx<W;++bx) {
      const int p=(bx+1)*K,q=((by+1)*(W+1)+bx+1)*K;
      const int8x16_t a=vld1q_s8(row_lo+iy[0]*(W+1)*K+p);
      const int8x16_t b=vld1q_s8(row_lo+iy[1]*(W+1)*K+p);
      const int8x16_t c=vld1q_s8(row_lo+iy[2]*(W+1)*K+p);
      const int8x16_t d=vld1q_s8(row_lo+iy[3]*(W+1)*K+p);
      vst1q_s8(lo+q,vminq_s8(vminq_s8(a,b),vminq_s8(c,d)));
      const int8x16_t e=vld1q_s8(row_hi+iy[0]*(W+1)*K+p);
      const int8x16_t f=vld1q_s8(row_hi+iy[1]*(W+1)*K+p);
      const int8x16_t g=vld1q_s8(row_hi+iy[2]*(W+1)*K+p);
      const int8x16_t h=vld1q_s8(row_hi+iy[3]*(W+1)*K+p);
      vst1q_s8(hi+q,vmaxq_s8(vmaxq_s8(e,f),vmaxq_s8(g,h)));
    }
  }
#else
  for(int k=0;k<K;++k){maxima[k]=-129;minima[k]=128;peaks[k]=0;}
  for(int y=0;y<H;++y)for(int bx=-1;bx<W;++bx) {
    int ix[4];
    for(int j=0;j<4;++j){int x=bx+j-1;ix[j]=x<0?0:x>=W?W-1:x;}
    for(int k=0;k<K;++k){
      int a=127,b=-128;
      for(int j=0;j<4;++j){int v=heat[(y*W+ix[j])*K+k];if(v<a)a=v;if(v>b)b=v;}
      int q=(y*(W+1)+bx+1)*K+k;row_lo[q]=a;row_hi[q]=b;
      // The middle source sample visits each raw pixel exactly once,
      // in the original row-major order. Fuse its extrema/peak scan.
      if(bx>=0){
        const int v=heat[(y*W+bx)*K+k];
        if(v>maxima[k]){maxima[k]=v;peaks[k]=y*W+bx;}
        if(v<minima[k])minima[k]=v;
      }
    }
  }
  for(int by=-1;by<H;++by)for(int bx=-1;bx<W;++bx) {
    int iy[4];
    for(int j=0;j<4;++j){int y=by+j-1;iy[j]=y<0?0:y>=H?H-1:y;}
    for(int k=0;k<K;++k){
      int a=127,b=-128;
      for(int j=0;j<4;++j){int q=(iy[j]*(W+1)+bx+1)*K+k;
        int l=row_lo[q],h=row_hi[q];if(l<a)a=l;if(h>b)b=h;}
      int q=((by+1)*(W+1)+bx+1)*K+k;lo[q]=a;hi[q]=b;
    }
  }
#endif
}

int bodyink_hourglass_decode(const int8_t* heat,int fix,float* out) {
  if(!heat || !out || fix<0 || fix>15)return -1;
  Sample s[O]; samples(s);
  int8_t bounds_min[(H+1)*(W+1)*K],bounds_max[(H+1)*(W+1)*K];
  int maxima[K],minima[K],peaks[K];
  tile_bounds(heat,bounds_min,bounds_max,maxima,minima,peaks);
  const float scale=ldexpf(1.f,-fix);
  // Independent heatmaps: no floating-point reduction or shared winners.
#if BODYINK_DECODE_THREADS > 1
#pragma omp parallel for num_threads(BODYINK_DECODE_THREADS) schedule(static)
#endif
  for(int k=0;k<K;++k) {
    float score=0; int best_x=0,best_y=0;
    const int maximum=maxima[k],minimum=minima[k],peak=peaks[k];
    // Constants have exactly constant interpolation and the first valid point.
    if(minimum==maximum) {
      score=maximum*scale;
      if(score>.001f){best_x=1;best_y=1;}else score=0;
    } else {
      const int px=peak%W,py=peak/W;
      // Seed a useful lower bound, then still examine every tile with the
      // rigorous upper bound. This is never a top-k approximation.
      for(int bx=px-1;bx<=px;++bx)for(int by=py-1;by<=py;++by)
        tile(heat,k,scale,s,bx,by,bounds_min,bounds_max,&score,&best_x,&best_y);
      // Bounds are row-major. Explicit tie-breaking inside tile() preserves
      // the official x-first result independently of traversal order.
      for(int by=-1;by<H;++by)for(int bx=-1;bx<W;++bx)
        tile(heat,k,scale,s,bx,by,bounds_min,bounds_max,&score,&best_x,&best_y);
    }
    out[3*k]=score>.001f?best_x/256.f:-1.f;
    out[3*k+1]=score>.001f?best_y/256.f:-1.f;
    out[3*k+2]=score;
  }
  return 0;
}
