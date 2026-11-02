// Offline-only ETC1 encoder. rg_etc1 sources retain their upstream Zlib license.
#include "rg_etc1.h"
#include <vector>
#include <fstream>
#include <cstring>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
int main(int argc,char**argv){
 if(argc!=5)return 2;int w=std::atoi(argv[2]),h=std::atoi(argv[3]);
 if(w<1||h<1||w>1024||h>1024)return 3;
 std::vector<unsigned char> rgba(w*h*4);std::ifstream input(argv[1],std::ios::binary);
 input.read((char*)rgba.data(),rgba.size());if(input.gcount()!=int(rgba.size())||input.peek()!=std::char_traits<char>::eof())return 4;
 for(int i=0;i<w*h;++i)if(rgba[i*4+3]!=255)return 5;
 int bw=(w+3)/4,bh=(h+3)/4;std::vector<unsigned char> data(bw*bh*8);
 rg_etc1::pack_etc1_block_init();rg_etc1::etc1_pack_params params;params.m_quality=rg_etc1::cHighQuality;params.m_dithering=false;
 unsigned long long squared=0;int invalid=0;
 for(int by=0;by<bh;++by)for(int bx=0;bx<bw;++bx){unsigned int block[16],decoded[16];
  for(int yy=0;yy<4;++yy)for(int xx=0;xx<4;++xx){int x=std::min(w-1,bx*4+xx),y=std::min(h-1,by*4+yy);std::memcpy(block+yy*4+xx,&rgba[(y*w+x)*4],4);}
  auto*dst=data.data()+(by*bw+bx)*8;rg_etc1::pack_etc1_block(dst,block,params);
  if(!rg_etc1::unpack_etc1_block(dst,decoded))++invalid;
  for(int yy=0;yy<4;++yy)for(int xx=0;xx<4;++xx)if(bx*4+xx<w&&by*4+yy<h){
   auto*a=(unsigned char*)(block+yy*4+xx);auto*b=(unsigned char*)(decoded+yy*4+xx);
   for(int c=0;c<3;++c){int d=int(a[c])-int(b[c]);squared+=d*d;}
  }
 }
 std::ofstream output(argv[4],std::ios::binary);output.write((char*)data.data(),data.size());if(!output)return 6;
 std::printf("{\"bytes\":%zu,\"invalid\":%d,\"mse\":%.8f}\n",data.size(),invalid,double(squared)/(w*h*3));return invalid?7:0;
}
