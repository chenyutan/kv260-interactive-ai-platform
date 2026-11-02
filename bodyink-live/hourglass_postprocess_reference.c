// BodyInk Hourglass CPU decode: OpenCV INTER_CUBIC (A=-0.75), 4x resize,
// and Vitis AI 2.5 hourglass find_point traversal/tie/threshold semantics.
// Keeps interpolated heatmap amplitude; it is not a probability.
#include <math.h>
#include <stdint.h>
#include <stdlib.h>

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

int bodyink_hourglass_decode(const int8_t* heat,int fix,float* out) {
  if(!heat || !out || fix<0 || fix>15)return -1;
    Sample s[O]; samples(s);
    // Horizontal pass is shared by all output rows. At most 1 MiB temporary.
    float* horizontal=(float*)malloc(H*O*K*sizeof(float));
    if(!horizontal)return -2;
    const float scale=ldexpf(1.f,-fix);
    for(int y=0;y<H;++y)for(int x=0;x<O;++x) {
      const Sample p=s[x];
      for(int k=0;k<K;++k) {
        const float v0=heat[(y*W+p.index[0])*K+k]*scale;
        const float v1=heat[(y*W+p.index[1])*K+k]*scale;
        const float v2=heat[(y*W+p.index[2])*K+k]*scale;
        const float v3=heat[(y*W+p.index[3])*K+k]*scale;
        horizontal[(y*O+x)*K+k]=v0*p.weight[0]+v1*p.weight[1]+v2*p.weight[2]+v3*p.weight[3];
      }
    }
    float score[K]={0}; int best_x[K]={0},best_y[K]={0};
    for(int y=1;y<O-1;++y) {
      const Sample p=s[y];
      for(int x=1;x<O-1;++x)for(int k=0;k<K;++k) {
        const float value=horizontal[(p.index[0]*O+x)*K+k]*p.weight[0]
                         +horizontal[(p.index[1]*O+x)*K+k]*p.weight[1]
                         +horizontal[(p.index[2]*O+x)*K+k]*p.weight[2]
                         +horizontal[(p.index[3]*O+x)*K+k]*p.weight[3];
        if(value<=.001f)continue;
        // Official loops x before y. Preserve that tie-break in this row scan.
        if(value>score[k] || (value==score[k] &&
           (x<best_x[k] || (x==best_x[k] && y<best_y[k])))) {
          score[k]=value;best_x[k]=x;best_y[k]=y;
        }
      }
    }
    for(int k=0;k<K;++k) {
      out[3*k]=score[k]>.001f ? best_x[k]/256.f:-1.f;
      out[3*k+1]=score[k]>.001f ? best_y[k]/256.f:-1.f;
      out[3*k+2]=score[k];
    }
    free(horizontal);
  return 0;
}
