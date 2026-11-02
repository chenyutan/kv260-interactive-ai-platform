// Scalar float oracle following hourglass_quant_reference.cpp, fixed-point 7.
#include <stdio.h>
#include <stdint.h>
int main(int argc,char **argv){
 if(argc!=3)return 2;uint8_t bgr[768];int8_t out[768];FILE*f=fopen(argv[1],"rb");if(!f)return 3;if(fread(bgr,1,768,f)!=768)return 4;fclose(f);
 float means[3]={112.302f,113.22f,110.3385f};float scale=.00392157f*128.f;
 for(int i=0;i<256;i++)for(int c=0;c<3;c++){int v=(int)(((float)bgr[3*i+c]-means[c])*scale);out[3*i+2-c]=(int8_t)v;}
 f=fopen(argv[2],"wb");if(!f)return 5;int ok=fwrite(out,1,768,f)==768;fclose(f);return ok?0:6;
}
