#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int bodyink_yuyv_roi(const uint8_t*,int,int,int,uint8_t*,int,int,int,int,int,int);
int main(int argc,char**argv){
 if(argc!=9)return 2;
 int w=atoi(argv[3]),h=atoi(argv[4]),x=atoi(argv[5]),y=atoi(argv[6]),rw=atoi(argv[7]),rh=atoi(argv[8]);
 size_t n=(size_t)w*h*2;uint8_t*src=malloc(n),*dst=malloc(256*256*3);
 FILE*f=fopen(argv[1],"rb");if(!f||!src||!dst)return 3;
 if(fread(src,1,n,f)!=n)return 4;fclose(f);
 if(bodyink_yuyv_roi(src,w,h,w*2,dst,256,256,x,y,rw,rh))return 5;
 f=fopen(argv[2],"wb");if(!f)return 6;if(fwrite(dst,1,256*256*3,f)!=256*256*3)return 7;fclose(f);free(src);free(dst);return 0;
}
