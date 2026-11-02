// Original GLES2 closed-course kart scene. No third-party game art or code.
#include "ink_gl_backend.h"
#include "ink_gl_packed_vertex.h"
#include "ink_gl_index_mesh.h"
#include <vector>
#include <cmath>
#include <algorithm>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace {
constexpr float PI=3.14159265358979f,TAU=2*PI;
constexpr int N=384,CHUNKS=24;
struct P {float x,y,z;}; struct Color {float r,g,b,a=1;};
P operator+(P a,P b){return {a.x+b.x,a.y+b.y,a.z+b.z};}
P operator-(P a,P b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
P operator*(P a,float f){return {a.x*f,a.y*f,a.z*f};}
float dot(P a,P b){return a.x*b.x+a.y*b.y+a.z*b.z;}
P cross(P a,P b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
P unit(P p){float d=std::sqrt(dot(p,p));return p*(1/std::max(.0001f,d));}
using Mesh=std::vector<InkGlVertex>;
Color shade(Color c,P n){n=unit(n);float sun=std::max(0.f,dot(n,unit({-.42f,.87f,.28f}))),up=std::max(0.f,n.y);float k=.60f+.30f*sun+.10f*up;return {std::min(1.f,c.r*k+sun*.025f),std::min(1.f,c.g*k+sun*.014f),std::min(1.f,c.b*k),c.a};}
Color tint(Color c,float f){return {std::min(1.f,c.r*f),std::min(1.f,c.g*f),std::min(1.f,c.b*f),c.a};}
void v(Mesh&m,P p,Color c){m.push_back({p.x,p.y,p.z,c.r,c.g,c.b,c.a});}
void tri(Mesh&m,P a,P b,P c,Color col){col=shade(col,cross(b-a,c-a));v(m,a,col);v(m,b,col);v(m,c,col);}
void quad(Mesh&m,P a,P b,P c,P d,Color col){tri(m,a,b,c,col);tri(m,a,c,d,col);}
void oriented_quad(Mesh&m,P a,P b,P c,P d,Color col,P outward){if(dot(cross(b-a,c-a),outward)<0)quad(m,a,d,c,b,col);else quad(m,a,b,c,d,col);}
void oriented_tri(Mesh&m,P a,P b,P c,Color col,P outward){if(dot(cross(b-a,c-a),outward)<0)tri(m,a,c,b,col);else tri(m,a,b,c,col);}
// Sixteen small original material tiles. Their low-frequency marks are baked
// once; no fragment noise or per-frame texture upload is used on Mali-400.
void texture_quad(Mesh&m,P a,P b,P c,P d,Color col,int tile,float repeat=1){
 int count=std::max(1,std::min(4,int(std::ceil(repeat))));float tx=(tile%4)*.25f,ty=(tile/4)*.25f;
 auto at=[&](float u,float v0){return (a*(1-v0)+b*v0)*(1-u)+(d*(1-v0)+c*v0)*u;};
 const float uv[6][2]={{0,0},{0,1},{1,1},{0,0},{1,1},{1,0}};
 for(int y=0;y<count;y++)for(int x=0;x<count;x++){
  float u0=float(x)/count,u1=float(x+1)/count,v0=float(y)/count,v1=float(y+1)/count;size_t first=m.size();
  quad(m,at(u0,v0),at(u0,v1),at(u1,v1),at(u1,v0),col);
  for(int i=0;i<6;i++){m[first+i].u=tx+.006f+uv[i][0]*.238f;m[first+i].v=ty+.006f+uv[i][1]*.238f;}
 }
}
void material_atlas(void*gl){
 constexpr int S=512,T=128;std::vector<unsigned char> px(S*S*4,255);
 for(int y=0;y<S;y++)for(int x=0;x<S;x++){
  int tile=(y/T)*4+x/T;float u=float(x%T)/(T-1),v0=float(y%T)/(T-1);
  float soft=std::sin(u*TAU*2.0f+std::sin(v0*TAU))*std::cos(v0*TAU*2.f),g=1;Color c{1,1,1};
  if(tile==1){g=.91f+.035f*soft+.018f*std::cos(u*34+v0*17);c={g,g,g};}
  else if(tile==2){g=.92f+.065f*soft;c={g,1.f-.025f*soft,g*.94f};}
  else if(tile==3){float edge=(std::fmod(u*4+(int(v0*5)%2)*.5f,1.f)<.055f||std::fmod(v0*5,1.f)<.045f)?.76f:1.f;g=(.93f+.04f*soft)*edge;c={g,g,g};}
  else if(tile==4){g=.88f+.07f*std::sin(v0*TAU*6)+.035f*std::sin(u*4+v0*37);c={g,g*.98f,g*.92f};}
  else if(tile==5){g=.90f+.06f*std::cos(u*TAU*8);if(std::fmod(v0*7,1.f)<.07f)g*=.80f;c={g,g,g};}
  else if(tile==6){g=.84f+.16f*std::pow(std::sin(v0*PI),2.f);c={g,g,g};}
  else if(tile==7){g=.92f+.065f*std::sin(u*TAU*2+std::sin(v0*TAU*3));c={g,1.f,g};}
  else if(tile==8){float arrow=std::abs(v0-(.26f+.70f*std::abs(u-.5f)));bool mark=arrow<.11f||std::abs(v0-(.64f+.70f*std::abs(u-.5f)))<.10f;c=mark?Color{1.f,.88f,.36f}:Color{.14f,.57f,.64f};}
  size_t k=size_t(y*S+x)*4;px[k]=std::max(0,std::min(255,int(c.r*255)));px[k+1]=std::max(0,std::min(255,int(c.g*255)));px[k+2]=std::max(0,std::min(255,int(c.b*255)));
 }
 inkgl_set_material_atlas(gl,px.data(),S,S,S*4);
}
void box(Mesh&m,P p,P s,Color c){float l=p.x-s.x/2,r=p.x+s.x/2,b=p.y,t=p.y+s.y,n=p.z-s.z/2,f=p.z+s.z/2;
 quad(m,{l,b,n},{l,t,n},{r,t,n},{r,b,n},c);quad(m,{r,b,f},{r,t,f},{l,t,f},{l,b,f},c);
 quad(m,{l,b,f},{l,t,f},{l,t,n},{l,b,n},c);quad(m,{r,b,n},{r,t,n},{r,t,f},{r,b,f},c);
 quad(m,{l,t,n},{l,t,f},{r,t,f},{r,t,n},c);quad(m,{l,b,f},{l,b,n},{r,b,n},{r,b,f},c);}
void ellipsoid(Mesh&m,P p,P s,Color c,int rings=5,int sides=10){
 auto at=[&](float a,float b){return P{p.x+s.x*std::sin(a)*std::cos(b),p.y+s.y*std::cos(a),p.z+s.z*std::sin(a)*std::sin(b)};};
 auto emit=[&](P q){P n{(q.x-p.x)/(s.x*s.x),(q.y-p.y)/(s.y*s.y),(q.z-p.z)/(s.z*s.z)};v(m,q,shade(c,n));};
 for(int j=0;j<rings;j++)for(int i=0;i<sides;i++){P a=at(j*PI/rings,i*TAU/sides),b=at(j*PI/rings,(i+1)*TAU/sides),d=at((j+1)*PI/rings,i*TAU/sides),q=at((j+1)*PI/rings,(i+1)*TAU/sides);emit(a);emit(b);emit(q);emit(a);emit(q);emit(d);}
}
void cylinder(Mesh&m,P p,float r,float h,Color c,int sides=8){
 for(int i=0;i<sides;++i){float a=i*TAU/sides,b=(i+1)*TAU/sides;
  P u{p.x+r*std::cos(a),p.y,p.z+r*std::sin(a)},w{p.x+r*std::cos(b),p.y,p.z+r*std::sin(b)};
  quad(m,u,u+P{0,h,0},w+P{0,h,0},w,c);tri(m,p+P{0,h,0},w+P{0,h,0},u+P{0,h,0},tint(c,1.1f));}}
void cone(Mesh&m,P p,float r,float h,Color c,int sides=8){for(int i=0;i<sides;++i){float a=i*TAU/sides,b=(i+1)*TAU/sides;tri(m,{p.x+r*std::cos(a),p.y,p.z+r*std::sin(a)},p+P{0,h,0},{p.x+r*std::cos(b),p.y,p.z+r*std::sin(b)},c);}}
void copy(Mesh&dst,const Mesh&src,P at,float angle=0,float scale=1){float sn=std::sin(angle),cs=std::cos(angle);for(auto q:src){float x=q.x*scale,z=q.z*scale;q.x=at.x+x*cs+z*sn;q.y=at.y+q.y*scale;q.z=at.z+z*cs-x*sn;dst.push_back(q);}}
const Color IVORY{1.f,.95f,.80f},INK{.09f,.16f,.21f},TEAL{.14f,.69f,.64f},RED{.96f,.29f,.22f},GOLD{1.f,.76f,.29f};
struct Track {P p[N+1];float s[N+1];float length;
 Track(){s[0]=0;for(int i=0;i<=N;i++){float t=TAU*i/N,y=0;if(t>3.9f&&t<5.25f)y=4.f*std::pow(std::sin((t-3.9f)*PI/1.35f),2.f);p[i]={112*std::sin(t)+22*std::sin(2*t),y,128*std::cos(t)-22*std::cos(3*t)};if(i)s[i]=s[i-1]+std::sqrt(dot(p[i]-p[i-1],p[i]-p[i-1]));}length=s[N];}
 P at(float d,float lateral=0,float*yaw=nullptr)const{d=std::fmod(d,length);if(d<0)d+=length;int i=int(std::upper_bound(s,s+N+1,d)-s)-1;i=std::max(0,std::min(N-1,i));float f=(d-s[i])/(s[i+1]-s[i]);P t=unit((p[i+1]-p[(i+N-1)%N])*(1-f)+(p[(i+2)%N]-p[i])*f);t.y=0;t=unit(t);if(yaw)*yaw=std::atan2(t.x,t.z);return p[i]+(p[i+1]-p[i])*f+P{t.z,0,-t.x}*lateral;}
 float grade(float d)const{return (at(d+3).y-at(d-3).y)/6;}
 bool bridge(float d)const{d=std::fmod(d+length,length);return d>s[244]&&d<s[314];}
 float curve(float d)const{float a,b;at(d-4,0,&a);at(d+4,0,&b);float q=b-a;while(q>PI)q-=TAU;while(q<-PI)q+=TAU;return q/8;}
};
Track track;
float ground_height(P p){float near2=1e12f;for(int i=0;i<N;i++){float dx=p.x-track.p[i].x,dz=p.z-track.p[i].z;near2=std::min(near2,dx*dx+dz*dz);}
 float f=std::max(0.f,std::min(1.f,(std::sqrt(near2)-32.f)/55.f));f=f*f*(3-2*f);
 return -.25f+f*(4.8f+2.4f*std::sin(p.x*.021f+std::cos(p.z*.017f))+1.8f*std::cos(p.z*.027f));}
void band(Mesh&m,float d0,float d1,float x0,float x1,float y,Color c){
 if(!(y==0&&x0>=-6.8f&&x1<=6.8f&&std::abs(x1-x0)>1.f&&c.r<.5f)){
  P a=track.at(d0,x0);P b=track.at(d1,x0);P q=track.at(d1,x1);P p=track.at(d0,x1);a.y+=y;b.y+=y;q.y+=y;p.y+=y;
  quad(m,a,b,q,p,c);return;
 }
  bool bridge=track.bridge(d0);Color color=bridge?Color{.65f,.48f,.31f}:c;
  // The large central road is vertex shaded; just the two narrow outside
  // strips sample the atlas. Texture cost must not cover every world pixel.
  const float split[]={-6.8f,-5.15f,5.15f,6.8f};
  for(int i=0;i<3;i++){float l=std::max(x0,split[i]),r=std::min(x1,split[i+1]);if(r<=l)continue;
   P p0=track.at(d0,l),p1=track.at(d1,l),p2=track.at(d1,r),p3=track.at(d0,r);
   if(i!=1)texture_quad(m,p0,p1,p2,p3,color,bridge?4:1);
   else {size_t first=m.size();quad(m,p0,p1,p2,p3,bridge?tint(color,.94f+.055f*std::sin(d0*1.7f)):color);
    for(size_t j=first;j<m.size();j++){float f=.96f+.035f*std::sin(m[j].x*.09f+m[j].z*.043f);m[j].r*=f;m[j].g*=f;m[j].b*=f;}}
  }
 }
void strip(Mesh&m,int i,float x0,float x1,float y,Color c){band(m,track.s[i],track.s[i+1],x0,x1,y,c);}
P soil(P p){p.y=-.25f;return p;}
void grounded(Mesh&m,const Mesh&src,P p,float angle=0,float scale=1){
 // Keep complete plant/house footprints outside the open water. In-water
 // roadside placements are omitted rather than floating above the lake.
 float dx=p.x+190,dz=p.z+40;if(dx*dx+dz*dz<138*138)return;
 p.y=ground_height(p);copy(m,src,p,angle,scale);
}
// Original static architecture relight: preserve geometry and correct baked
// local lighting into the common world sun after instance rotation.
void grounded_arch(Mesh&m,const Mesh&src,P p,float angle=0,float scale=1){
 size_t first=m.size();grounded(m,src,p,angle,scale);if(m.size()-first!=src.size())return;
 float sn=std::sin(angle),cs=std::cos(angle);P light=unit({-.42f,.87f,.28f});
 for(size_t i=0;i+2<src.size();i+=3){P a{src[i].x,src[i].y,src[i].z},b{src[i+1].x,src[i+1].y,src[i+1].z},c{src[i+2].x,src[i+2].y,src[i+2].z};P area=cross(b-a,c-a);if(dot(area,area)<.04f)continue;P n=unit(area);
  P world{n.x*cs+n.z*sn,n.y,n.z*cs-n.x*sn};float oldsun=std::max(0.f,dot(n,light)),sun=std::max(0.f,dot(world,light)),up=std::max(0.f,n.y);
  float k=.60f+.30f*oldsun+.10f*up;
  for(size_t j=i;j<i+3;j++){auto&v=m[first+j];float r=std::max(0.f,(v.r-oldsun*.025f)/k),g=std::max(0.f,(v.g-oldsun*.014f)/k),b0=v.b/k;
   v.r=std::min(1.f,r*(.57f+.36f*sun+.10f*up)+sun*.025f);
   v.g=std::min(1.f,g*(.63f+.30f*sun+.10f*up)+sun*.014f);
   v.b=std::min(1.f,b0*(.69f+.23f*sun+.10f*up));
  }
 }
}

Mesh pine(int style,bool low=false){Mesh m;cylinder(m,{0,0,0},.27f,3.7f,{.43f,.31f,.19f},low?5:7);
 // Broad rounded crowns, lit tops and darker undersides form a readable
 // silhouette. One tree is intentionally a small cluster, never a cone.
 for(int j=0;j<(low?3:2);j++){float a=j*2.4f;Color leaf=style?Color{.32f,.66f,.32f}:Color{.20f,.56f,.39f};
  if(low)ellipsoid(m,{std::cos(a)*.77f,3.25f+j*.68f,std::sin(a)*.70f},{2.0f-j*.15f,1.65f,1.8f},tint(leaf,1+j*.06f),2,8);
  else ellipsoid(m,{j?-.57f:.57f,3.48f+j*.84f,j?.32f:-.28f},{j?1.94f:2.18f,j?1.80f:1.65f,j?1.83f:1.94f},tint(leaf,1+j*.08f),4,16);}
 return m;}
Mesh flowers(int style){Mesh m;Color a=style?Color{1.f,.71f,.32f}:Color{1.f,.62f,.70f};
 for(int j=0;j<7;j++){float x=std::sin(j*2.4f)*1.3f,z=std::cos(j*2.4f)*1.1f,h=.25f+(j%3)*.12f;
  box(m,{x,0,z},{.07f,h,.07f},{.28f,.57f,.24f});ellipsoid(m,{x,h,z},{.26f,.17f,.26f},a,2,6);}
 return m;}
// Six composed village beds, identical across both chunk LODs. Reuses the existing box
// and smooth ellipsoid vocabulary; no new material, mesh upload or draw.
Mesh village_bed(int style){Mesh m;
 box(m,{0,0,0},{2.30f,.38f,5.80f},{.55f,.56f,.43f});
 // box emits its bottom face last. It is coplanar with opaque courtyard
 // ground and hidden under the solid stone base; retain every visible face.
 m.resize(m.size()-6);
 Color leaf{.22f,.48f,.31f};
 for(int j=0;j<2;++j)ellipsoid(m,{j?-.10f:.10f,j?.91f:1.07f,j?1.25f:-1.25f},
  {j?.99f:1.10f,j?.63f:.77f,1.62f},tint(leaf,j?1.10f:1.f),3,7);
 Color bloom=style?Color{1.f,.71f,.32f}:Color{1.f,.62f,.70f};
 for(int j=0;j<3;++j)ellipsoid(m,{j==1?.28f:-.24f,j==0?1.73f:j==1?1.61f:1.44f,-1.78f+j*1.62f},
  {j==1?.52f:.43f,.26f,j==1?.62f:.52f},tint(bloom,1-j*.035f),2,6);
 return m;}
Mesh rock(){Mesh m;ellipsoid(m,{0,.3f,0},{2.4f,1.7f,1.6f},{.64f,.70f,.59f},3,9);ellipsoid(m,{1.5f,.08f,.7f},{1.7f,.95f,1.25f},{.74f,.76f,.62f},3,7);return m;}
Mesh bamboo(bool low=false){Mesh m;for(int j=0;j<(low?3:4);++j){float x=(j%2)*1.3f-.6f,z=(j/2)*1.3f-.6f,h=6.1f+j*.55f;cylinder(m,{x,0,z},.13f,h,{.32f,.55f,.25f},low?5:6);
 for(int k=1;k<6;k++)if(!low||k%2==0)box(m,{x,k*1.14f,z},{.28f,.055f,.28f},{.62f,.73f,.34f});
 for(int k=0;k<3;k++){float side=(j+k)%2?-1.f:1.f,yy=h-2.5f+k*.9f;Color leaf=k==2?Color{.34f,.65f,.30f}:Color{.20f,.50f,.29f};
  ellipsoid(m,{x+side*.75f,yy,z},{1.65f,.38f,.70f},leaf,low?2:3,low?6:8);
  for(int q=0;q<3;q++)tri(m,{x,yy,z},{x+side*(1.8f+q*.1f),yy+.28f+q*.1f,z+.35f*q},{x+side*.6f,yy-.14f,z+.22f*q},leaf);
 }}return m;}
void roof(Mesh&m,float w,float d,float y,Color col){
 // Tiled broad hip roof with raised corners and a distinct ridge.
 P tl{-w*.60f,y-.18f,-d*.60f},tr{w*.60f,y-.18f,-d*.60f},bl{-w*.60f,y-.18f,d*.60f},br{w*.60f,y-.18f,d*.60f};
 P a{-w*.31f,y+1.45f,0},b{w*.31f,y+1.45f,0};
 quad(m,tl,a,b,tr,col);quad(m,bl,br,b,a,tint(col,.87f));tri(m,tl,bl,a,tint(col,.9f));tri(m,tr,b,br,tint(col,1.08f));
 box(m,{0,y+1.42f,0},{w*.68f,.16f,.18f},INK);
 for(float side:{-1.f,1.f}){box(m,{0,y-.15f,side*d*.6f},{w*1.24f,.18f,.16f},tint(col,1.18f));
  for(int k=-5;k<=5;k++){float x=k*w*.095f;quad(m,{x-.025f,y-.12f,side*d*.60f},{x-.025f,y+1.4f,0},{x+.025f,y+1.4f,0},{x+.025f,y-.12f,side*d*.60f},tint(col,1.22f));}}
}
Mesh house(int style){Mesh m;float w=6.f,d=5.f,h=3.6f;
 box(m,{0,0,0},{w,h,d},IVORY);box(m,{0,0,0},{w+.3f,.35f,d+.3f},{.42f,.45f,.43f});
 for(float x:{-1.9f,1.9f}){box(m,{x,1.4f,-2.51f},{1.3f,1.25f,.08f},INK);box(m,{x,1.45f,-2.57f},{.08f,1.1f,.04f},RED);box(m,{x,1.98f,-2.57f},{1.2f,.08f,.04f},RED);}
 box(m,{0,.34f,-2.56f},{1.1f,2.25f,.1f},{.32f,.20f,.14f});roof(m,w,d,h,{.24f,.31f,.31f});
 if(style){box(m,{2,0,-3.4f},{.14f,3.2f,.14f},RED);box(m,{2,2.6f,-3.4f},{1.2f,.55f,.12f},GOLD);}
 return m;}
void painted_wall(Mesh&m,P p,P s,Color col){
 float x0=p.x-s.x*.5f,x1=p.x+s.x*.5f,z0=p.z-s.z*.5f,z1=p.z+s.z*.5f;
 const float levels[]={0,.14f,.72f,.91f,1},light[]={.72f,.97f,1.f,.86f,.73f};
 for(int side=0;side<4;side++)for(int band=0;band<4;band++){
  P a,b;switch(side){case 0:a={x0,0,z0};b={x1,0,z0};break;case 1:a={x1,0,z1};b={x0,0,z1};break;case 2:a={x0,0,z1};b={x0,0,z0};break;default:a={x1,0,z0};b={x1,0,z1};}
  float lo=p.y+levels[band]*s.y,hi=p.y+levels[band+1]*s.y;P u{a.x,lo,a.z},v0{a.x,hi,a.z},q{b.x,hi,b.z},w{b.x,lo,b.z};P normal=cross(v0-u,q-u);
  Color lower=shade(tint(col,light[band]),normal),upper=shade(tint(col,light[band+1]),normal);
  v(m,u,lower);v(m,v0,upper);v(m,q,upper);v(m,u,lower);v(m,q,upper);v(m,w,lower);
 }
 quad(m,{x0,p.y+s.y,z0},{x0,p.y+s.y,z1},{x1,p.y+s.y,z1},{x1,p.y+s.y,z0},col);
 quad(m,{x0,p.y,z1},{x0,p.y,z0},{x1,p.y,z0},{x1,p.y,z1},col);
}
Mesh townhouse(int style){Mesh m;float w=7.2f,d=6.2f,h=5.1f;Color timber{.33f,.24f,.18f};
 box(m,{0,-.20f,0},{w+.55f,.60f,d+.55f},{.64f,.64f,.54f});painted_wall(m,{0,.4f,0},{w,h-.4f,d},IVORY);
 for(float x:{-w*.47f,w*.47f}){box(m,{x,.35f,-d*.51f},{.20f,h-.3f,.23f},timber);}
 for(float y:{1.35f,3.6f})for(float x:{-2.25f,2.25f}){
  box(m,{x,y,-d*.51f},{1.47f,1.45f,.16f},timber);box(m,{x,y+.08f,-d*.53f},{1.22f,1.21f,.08f},{.91f,.76f,.47f});
  for(float dx:{-.34f,0.f,.34f})box(m,{x+dx,y+.08f,-d*.55f},{.055f,1.2f,.04f},timber);
  box(m,{x,y+.68f,-d*.55f},{1.27f,.07f,.04f},timber);
 }
 box(m,{0,.4f,-d*.53f},{1.4f,2.5f,.15f},timber);box(m,{0,.40f,-d*.67f},{2.7f,.23f,1.0f},{.75f,.73f,.60f});
 for(float y:{.62f,3.1f,4.8f})box(m,{0,y,-d*.54f},{w,.16f,.16f},timber);
 // Broad side elevations receive beams and one warm upper window.
 for(float side:{-1.f,1.f}){float x=side*(w*.5f+.025f);auto panel=[&](float y0,float y1,float z0,float z1,Color c){oriented_quad(m,{x,y0,z0},{x,y1,z0},{x,y1,z1},{x,y0,z1},c,{side,0,0});};
  for(float y:{.66f,3.10f,4.79f})panel(y,y+.13f,-d*.5f,d*.5f,timber);
  for(float z:{-d*.47f,d*.47f})panel(.53f,4.91f,z-.08f,z+.08f,timber);
  x+=side*.014f;panel(3.48f,4.52f,-.84f,.84f,timber);x+=side*.014f;panel(3.59f,4.41f,-.70f,.70f,{.84f,.74f,.48f});x+=side*.014f;
  panel(3.59f,4.41f,-.035f,.035f,timber);panel(3.97f,4.04f,-.70f,.70f,timber);
 }
 roof(m,w,d,h,{.20f,.37f,.42f});
 for(float x:{-3.15f,3.15f}){cylinder(m,{x,2.15f,-3.55f},.045f,1.5f,timber,5);ellipsoid(m,{x,2.65f,-3.55f},{.38f,.48f,.38f},RED,4,8);}
 if(style){box(m,{0,3.4f,-3.32f},{2.2f,.65f,.18f},TEAL);for(int j=-2;j<=2;j++)box(m,{j*.33f,3.56f,-3.44f},{.15f,.30f,.04f},GOLD);}
 return m;}
Mesh pagoda(){Mesh m;box(m,{0,-.25f,0},{8.4f,.85f,8.4f},{.66f,.67f,.57f});
 for(int j=0;j<3;j++){float w=7.2f-j*1.25f,y=.6f+j*3.2f;box(m,{0,y,0},{w,2.8f,w},IVORY);
  for(float x:{-.38f,.38f})for(float z:{-.38f,.38f})box(m,{x*w,y,z*w},{.22f,2.9f,.22f},RED);
  for(float x:{-.22f,.22f}){box(m,{x*w,y+.7f,-w*.51f},{.76f,1.45f,.10f},INK);box(m,{x*w,y+.8f,-w*.53f},{.065f,1.25f,.04f},GOLD);}
  roof(m,w,w,y+2.8f,{.18f,.40f,.42f});
 }
 cylinder(m,{0,11.3f,0},.10f,1.2f,GOLD,8);ellipsoid(m,{0,12.6f,0},{.24f,.30f,.24f},GOLD,4,8);return m;}
Mesh chevron(bool right){Mesh m;box(m,{0,0,0},{.17f,1.4f,.17f},INK);box(m,{0,1.35f,0},{2.3f,1.1f,.19f},INK);
 float sign=right?1.f:-1.f;
 for(float x:{-.57f,.25f}){P a{x*sign,1.48f,-.105f},b{(x+.34f)*sign,1.89f,-.105f},q{(x-.02f)*sign,2.30f,-.105f};
  quad(m,a,b,b+P{-.28f*sign,0,0},a+P{-.28f*sign,0,0},GOLD);quad(m,b,q,q+P{-.28f*sign,0,0},b+P{-.28f*sign,0,0},GOLD);}
 return m;}
Mesh millhouse(){Mesh m;
 for(float x:{-3.7f,3.7f})for(float z:{-3.f,3.f})cylinder(m,{x,-2.5f,z},.30f,4.6f,{.43f,.35f,.24f},8);
 box(m,{0,1.6f,0},{8.5f,.45f,7.4f},{.57f,.42f,.27f});box(m,{0,2.0f,0},{7.2f,4.1f,5.8f},IVORY);
 for(float x:{-3.3f,3.3f})box(m,{x,2,-3.0f},{.23f,4.2f,.22f},RED);
 for(float x:{-1.9f,1.9f}){box(m,{x,3.1f,-3.03f},{1.35f,1.5f,.12f},INK);box(m,{x,3.7f,-3.11f},{1.12f,.08f,.04f},GOLD);}
 roof(m,7.2f,6,6.1f,{.17f,.37f,.40f});return m;}
Mesh waterwheel(){Mesh m;Color wood{.65f,.42f,.22f};
 for(int j=0;j<20;j++){float a=j*TAU/20,b=(j+1)*TAU/20;
  for(float z:{-.42f,.42f})oriented_quad(m,{2.55f*std::cos(a),2.55f*std::sin(a),z},{2.55f*std::cos(b),2.55f*std::sin(b),z},{2.85f*std::cos(b),2.85f*std::sin(b),z},{2.85f*std::cos(a),2.85f*std::sin(a),z},wood,{0,0,z});
  if(j%2==0){P p{2.70f*std::cos(a),2.70f*std::sin(a),0};box(m,p+P{0,-.12f,0},{.52f,.24f,1.0f},IVORY);}
  if(j%5==0)for(float z:{-.35f,.35f}){P n{-.13f*std::sin(a),.13f*std::cos(a),0},p{2.55f*std::cos(a),2.55f*std::sin(a),z};oriented_quad(m,P{0,0,z}-n,P{0,0,z}+n,p+n,p-n,wood,{0,0,z});}
 }
 ellipsoid(m,{0,0,0},{.42f,.42f,.65f},GOLD,4,10);return m;}
void bridge_arch(Mesh&m,float start,float end){
 const float sections[]={0,.085f,.12f,.20f,.32f,.50f,.68f,.80f,.88f,.915f,1.f};
 for(float side:{-1.f,1.f})for(int j=0;j<10;j++){
  float u=sections[j],v0=sections[j+1],d0=start+(end-start)*u,d1=start+(end-start)*v0;
  P a=track.at(d0,side*7.65f),b=track.at(d1,side*7.65f),q=b,w=a;
  a.y-=.20f;b.y-=.20f;
  auto arch_bottom=[](float fraction,float top){if(fraction<=.085f||fraction>=.915f)return -2.37f;float t=(fraction-.5f)/.415f;return -2.37f+std::sqrt(std::max(0.f,1-t*t))*(top+1.12f);};
  w.y=arch_bottom(u,w.y);q.y=arch_bottom(v0,q.y);
  Color stone=j%2?Color{.77f,.76f,.63f}:Color{.69f,.70f,.59f};
  if(side<0)quad(m,a,b,q,w,stone);else quad(m,w,q,b,a,stone);
  P ai=track.at(d0,side*7.28f),bi=track.at(d1,side*7.28f);ai.y=w.y;bi.y=q.y;quad(m,w,q,bi,ai,{.49f,.51f,.44f});
 }
}
Mesh pavilion(){Mesh m;box(m,{0,0,0},{5.2f,.45f,5.2f},{.58f,.58f,.49f});for(float x:{-1.8f,1.8f})for(float z:{-1.8f,1.8f})cylinder(m,{x,.4f,z},.17f,3.8f,RED,7);roof(m,5,5,4.2f,{.18f,.37f,.36f});return m;}
Mesh gate(){Mesh m;for(float x:{-7.8f,7.8f}){cylinder(m,{x,0,0},.37f,6.8f,RED,10);box(m,{x,0,0},{1.2f,.55f,1.2f},INK);box(m,{x,4.9f,0},{.55f,.16f,.55f},GOLD);}
 box(m,{0,5.5f,0},{17.0f,.70f,.75f},RED);roof(m,15.4f,2.1f,6.0f,{.15f,.36f,.40f});
 box(m,{0,5.55f,-.45f},{4.6f,.73f,.16f},GOLD);box(m,{0,5.65f,-.55f},{4.1f,.44f,.08f},INK);
 for(int k=-3;k<=3;k++){box(m,{k*.48f,5.63f,-.61f},{.22f,.25f,.04f},IVORY);}
 for(float x:{-6.2f,6.2f}){cylinder(m,{x,4.2f,0},.045f,1.2f,INK,5);ellipsoid(m,{x,4.25f,0},{.52f,.60f,.52f},RED,4,10);cylinder(m,{x,3.25f,0},.06f,.48f,GOLD,5);}
 return m;}
Color skin(int n){const Color colors[]={TEAL,{.30f,.46f,.93f},{1.f,.70f,.17f},RED,{.72f,.36f,.86f},{.50f,.74f,.22f}};return colors[n%6];}
// Original jade courier kart: moulded nose, integrated rear cowl, rear wheel
// arches. Author-created vertex geometry, no external asset/material/draw.
struct ShellSection {float z,width,base,height;};
void courier_shell(Mesh&m,const ShellSection*sections,int count,Color col,int sides=14){
 auto at=[&](int j,int k){float a=k*PI/sides;const auto&s=sections[j];
  return P{s.width*std::cos(a),s.base+s.height*std::sin(a),s.z};};
 auto vert=[&](int j,int k){P p=at(j,k);int lo=std::max(0,j-1),hi=std::min(count-1,j+1);
  float a=k*PI/sides;P tangent{-sections[j].width*std::sin(a),sections[j].height*std::cos(a),0};
  P normal=unit(cross(tangent,at(hi,k)-at(lo,k)));
  Color c=shade(col,normal);float lower=.78f+.22f*std::sin(a);c.r*=lower;c.g*=lower;c.b*=lower;v(m,p,c);};
 for(int j=0;j<count-1;j++)for(int k=0;k<sides;k++){
  vert(j,k);vert(j+1,k+1);vert(j+1,k);vert(j,k);vert(j,k+1);vert(j+1,k+1);}
 for(int j:{0,count-1}){P center{0,sections[j].base,sections[j].z};
  for(int k=0;k<sides;k++)oriented_tri(m,center,at(j,k),at(j,k+1),tint(col,.87f),{0,0,j?1.f:-1.f});}
}
Mesh courier_body(int n,bool low=false){Mesh m;Color col=skin(n),rubber{.075f,.10f,.14f},metal{.64f,.71f,.76f};
 box(m,{0,.30f,0},{1.48f,.22f,2.78f},rubber);
 // The front silhouette narrows continuously; the crown rises toward the
 // cockpit without enclosing the driver's hands or steering wheel.
 const ShellSection nose[]={{.40f,.65f,.52f,.48f},{.72f,.76f,.49f,.57f},{1.10f,.66f,.46f,.48f},{1.48f,.46f,.43f,.31f},{1.70f,.18f,.42f,.13f}};
 courier_shell(m,nose,5,col,low?6:14);
 ellipsoid(m,{0,.43f,1.50f},{.68f,.14f,.22f},IVORY,low?2:3,low?6:12);
 // Deep side sills and a substantial tail cowl replace the thin wing.
 for(float x:{-.56f,.56f}){ellipsoid(m,{x,.68f,-.04f},{.24f,.38f,.42f},col,low?2:4,low?6:12);
  box(m,{x,.83f,-.04f},{.22f,.07f,.55f},IVORY);}
 const ShellSection tail[]={{-1.52f,.65f,.48f,.28f},{-1.30f,.76f,.48f,.51f},{-1.00f,.78f,.49f,.57f},{-.70f,.72f,.50f,.40f}};
 courier_shell(m,tail,4,col,low?6:14);
 // Wheel arches follow only the fixed rear axle, leaving both front wheels
 // entirely independent and fully clear through their steering range.
 const int arch_steps=low?6:12;
 for(float sign:{-1.f,1.f})for(int j=0;j<arch_steps;j++){
  float a=-.80f+j*1.6f/arch_steps,b=-.80f+(j+1)*1.6f/arch_steps;
  auto p=[&](float x,float angle,float radius){return P{sign*x,.48f+radius*std::cos(angle),-.92f+radius*std::sin(angle)};};
  auto top=[&](float x,float angle){v(m,p(x,angle,.70f),shade(col,{0,std::cos(angle),std::sin(angle)}));};
  if(sign>0){top(.82f,a);top(1.25f,b);top(1.25f,a);top(.82f,a);top(.82f,b);top(1.25f,b);}
  else{top(.82f,a);top(1.25f,a);top(1.25f,b);top(.82f,a);top(1.25f,b);top(.82f,b);}
  oriented_quad(m,p(1.25f,a,.70f),p(1.25f,a,.63f),p(1.25f,b,.63f),p(1.25f,b,.70f),IVORY,{sign,0,0});
 }
 // Recessed seat remains open, with the original driver pose unchanged.
 box(m,{0,.64f,-.25f},{.78f,.20f,1.02f},rubber);
 ellipsoid(m,{0,.99f,-.68f},{.46f,.49f,.16f},rubber,low?2:4,low?6:10);
 box(m,{0,.96f,-.79f},{.72f,.19f,.12f},IVORY);
 // Two large tail lamps and short exhausts read from the play camera.
 ellipsoid(m,{0,.52f,-1.49f},{.75f,.13f,.20f},IVORY,low?2:3,low?6:12);
 for(float x:{-.52f,.52f}){
  ellipsoid(m,{x,.77f,-1.46f},{.18f,.105f,.065f},RED,low?2:3,low?6:8);
  box(m,{x*.64f,.42f,-1.48f},{.17f,.17f,.36f},metal);
  box(m,{x*.64f,.465f,-1.67f},{.11f,.08f,.015f},rubber);
 }
 // Fitted broad ivory nose stripe; follows the same shell interpolation.
 for(int j=0;j<4;j++){const auto&a=nose[j];const auto&b=nose[j+1];
  quad(m,{-.12f,a.base+a.height+.010f,a.z},{-.12f,b.base+b.height+.010f,b.z},{.12f,b.base+b.height+.010f,b.z},{.12f,a.base+a.height+.010f,a.z},IVORY);}
 // The rear cowl has three generous dark cooling slots rather than tiny fins.
 for(int j=-1;j<=1;j++)box(m,{j*.16f,1.047f,-1.08f},{.075f,.025f,.25f},rubber);
 return m;}

Mesh kart_body(int n){return courier_body(n,false);}
// Original, isolated helmet study. Uses existing flat-colour vertex API only.
// No product edits. Head, body, scarf may be merged for the current one-draw path.
void study_ellipsoid(Mesh&m,P p,P s,Color c,int rings,int sides){
 auto at=[&](int j,int i){float a=j*PI/rings,b=i*TAU/sides;return P{p.x+s.x*std::sin(a)*std::cos(b),p.y+s.y*std::cos(a),p.z+s.z*std::sin(a)*std::sin(b)};};
 auto emit=[&](P q){v(m,q,shade(c,{(q.x-p.x)/(s.x*s.x),(q.y-p.y)/(s.y*s.y),(q.z-p.z)/(s.z*s.z)}));};
 for(int j=0;j<rings;j++)for(int i=0;i<sides;i++){P a=at(j,i),b=at(j,i+1),c0=at(j+1,i+1),d=at(j+1,i);if(j){emit(a);emit(b);emit(c0);}if(j+1<rings){emit(a);emit(c0);emit(d);}}
}
void helmet_stripe(Mesh&m,int segments){
 for(int j=0;j<segments;j++){float a=-1.9f+j*3.4f/segments,b=-1.9f+(j+1)*3.4f/segments;auto p=[](float x,float angle){float f=std::sqrt(1-x*x/(.46f*.46f));return P{x,1.84f+.502f*f*std::cos(angle),-.21f+.452f*f*std::sin(angle)};};quad(m,p(-.09f,a),p(-.09f,b),p(.09f,b),p(.09f,a),IVORY);}
}
Mesh study_head(int n){Mesh m;Color col=skin(n);
 study_ellipsoid(m,{0,1.84f,-.21f},{.46f,.48f,.43f},col,8,16);
 // Fitted ivory racing stripe follows the helmet instead of a separate egg cap.
 helmet_stripe(m,12);
 study_ellipsoid(m,{0,1.86f,.17f},{.36f,.205f,.15f},INK,4,12);
 study_ellipsoid(m,{0,1.72f,.265f},{.27f,.115f,.080f},{1.f,.78f,.55f},4,10);
 for(float x:{-.14f,.14f}){
  study_ellipsoid(m,{x,1.91f,.320f},{.102f,.084f,.025f},IVORY,4,8);
  study_ellipsoid(m,{x,1.90f,.350f},{.037f,.055f,.011f},INK,3,8);
  study_ellipsoid(m,{x-.012f,1.923f,.362f},{.012f,.015f,.004f},IVORY,2,6);
 }
 // A restrained friendly mouth, broad enough to survive the title-camera scale.
 quad(m,{-.065f,1.704f,.347f},{0,1.688f,.351f},{0,1.676f,.351f},{-.065f,1.696f,.347f},INK);
 quad(m,{0,1.688f,.351f},{.065f,1.704f,.347f},{.065f,1.696f,.347f},{0,1.676f,.351f},INK);
 return m;
}
Mesh study_body(int n){Mesh m;Color col=skin(n),glove{.19f,.25f,.30f};
 study_ellipsoid(m,{0,1.19f,-.20f},{.34f,.42f,.28f},IVORY,5,10);
 for(float x:{-.34f,.34f}){study_ellipsoid(m,{x,1.18f,.15f},{.15f,.15f,.35f},col,4,8);study_ellipsoid(m,{x,1.20f,.42f},{.14f,.13f,.13f},glove,3,8);}
 box(m,{0,1.19f,.42f},{.65f,.065f,.10f},glove);
 study_ellipsoid(m,{0,1.44f,-.25f},{.36f,.10f,.30f},{1.f,.48f,.20f},3,10);
 return m;
}
Mesh study_scarf(float wave=0){Mesh m;
 quad(m,{.13f,1.44f,-.49f},{.48f,1.40f,-.88f},{.39f,1.30f+wave,-1.16f},{.03f,1.35f,-.82f},{1.f,.49f,.22f});
 tri(m,{.31f,1.37f+wave,-1.02f},{.46f,1.41f+wave,-1.31f},{.55f,1.32f+wave,-1.18f},GOLD);return m;
}
Mesh driver(int n,float headyaw=0,float wave=0){Mesh m=study_body(n);Mesh head=study_head(n);
 for(auto&v:head){float x=v.x,z=v.z+.21f;v.x=x*std::cos(headyaw)+z*std::sin(headyaw);v.z=-.21f+z*std::cos(headyaw)-x*std::sin(headyaw);}
 copy(m,head,{0,0,0});copy(m,study_scarf(wave),{0,0,0});return m;
}

Mesh wheel_pair(){Mesh m;Color rubber{.085f,.11f,.15f},sidewall{.15f,.19f,.23f},metal{.76f,.83f,.88f};
 for(float x:{-1.06f,1.06f}){float sign=x>0?1.f:-1.f;
  for(int k=0;k<16;k++){float a=k*TAU/16,b=(k+1)*TAU/16;float inner=x-sign*.23f,outer=x+sign*.23f;
   auto ring=[&](float xx,float r,float aa){return P{xx,r*std::cos(aa),r*std::sin(aa)};};
   P radial{0,std::cos((a+b)*.5f),std::sin((a+b)*.5f)};
   oriented_quad(m,ring(inner,.41f,a),ring(inner,.41f,b),ring(x-sign*.14f,.48f,b),ring(x-sign*.14f,.48f,a),rubber,radial+P{-sign*.7f,0,0});
   oriented_quad(m,ring(x-sign*.14f,.48f,a),ring(x-sign*.14f,.48f,b),ring(x+sign*.14f,.48f,b),ring(x+sign*.14f,.48f,a),k%4==0?tint(rubber,1.3f):rubber,radial);
   oriented_quad(m,ring(x+sign*.14f,.48f,a),ring(x+sign*.14f,.48f,b),ring(outer,.40f,b),ring(outer,.40f,a),sidewall,radial+P{sign*.7f,0,0});
   oriented_quad(m,ring(outer,.40f,a),ring(outer,.40f,b),ring(outer+sign*.008f,.26f,b),ring(outer+sign*.008f,.26f,a),rubber,{sign,0,0});
   oriented_quad(m,ring(outer+sign*.012f,.27f,a),ring(outer+sign*.012f,.27f,b),ring(outer+sign*.014f,.20f,b),ring(outer+sign*.014f,.20f,a),IVORY,{sign,0,0});
   oriented_tri(m,{outer+sign*.018f,0,0},ring(outer+sign*.018f,.21f,a),ring(outer+sign*.018f,.21f,b),k%4==0?INK:metal,{sign,0,0});
  }
  ellipsoid(m,{x+sign*.25f,0,0},{.045f,.10f,.10f},GOLD,3,8);
 }
 return m;}
Mesh kart(int n){Mesh m=kart_body(n);copy(m,driver(n),{0,0,0});Mesh wheels=wheel_pair();copy(m,wheels,{0,.48f,.93f});copy(m,wheels,{0,.48f,-.92f});return m;}
Mesh kart_lod(int n){Mesh m=courier_body(n,true);Color col=skin(n),rubber{.085f,.11f,.15f},metal{.70f,.77f,.82f};
 ellipsoid(m,{0,1.20f,-.19f},{.32f,.40f,.28f},IVORY,3,8);study_ellipsoid(m,{0,1.84f,-.21f},{.46f,.48f,.43f},col,4,10);helmet_stripe(m,6);
 study_ellipsoid(m,{0,1.86f,.17f},{.36f,.205f,.15f},INK,3,8);
 for(float x:{-.14f,.14f}){oriented_quad(m,{x-.075f,1.85f,.333f},{x+.075f,1.85f,.333f},{x+.075f,1.98f,.333f},{x-.075f,1.98f,.333f},IVORY,{0,0,1});oriented_quad(m,{x-.022f,1.87f,.337f},{x+.022f,1.87f,.337f},{x+.022f,1.95f,.337f},{x-.022f,1.95f,.337f},INK,{0,0,1});}
 quad(m,{.13f,1.44f,-.49f},{.48f,1.40f,-.88f},{.39f,1.30f,-1.16f},{.03f,1.35f,-.82f},{1.f,.49f,.22f});
 for(float x:{-1.06f,1.06f})for(float z:{-.92f,.93f}){float sign=x>0?1.f:-1.f,inner=x-sign*.22f,outer=x+sign*.22f;
  for(int j=0;j<10;j++){float a=j*TAU/10,b=(j+1)*TAU/10;P p{inner,.48f+.46f*std::cos(a),z+.46f*std::sin(a)},q{inner,.48f+.46f*std::cos(b),z+.46f*std::sin(b)},r{outer,q.y,q.z},s{outer,p.y,p.z};
   oriented_quad(m,p,q,r,s,rubber,{0,std::cos((a+b)*.5f),std::sin((a+b)*.5f)});oriented_tri(m,{outer,.48f,z},s,r,rubber,{sign,0,0});
   P h{outer+sign*.012f,.48f+.24f*std::cos(a),z+.24f*std::sin(a)},h1{h.x,.48f+.24f*std::cos(b),z+.24f*std::sin(b)};
   oriented_tri(m,{h.x,.48f,z},h,h1,metal,{sign,0,0});
  }
 }
 return m;}
void perspective(float*m,float fovy,float aspect,float n,float f){std::fill(m,m+16,0);float d=1/std::tan(fovy*.5f);m[0]=d/aspect;m[5]=d;m[10]=(f+n)/(n-f);m[11]=-1;m[14]=2*f*n/(n-f);}
void lookat(float*m,P eye,P center){P f=unit(center-eye),s=unit(cross(f,{0,1,0})),u=cross(s,f);float a[16]={s.x,u.x,-f.x,0,s.y,u.y,-f.y,0,s.z,u.z,-f.z,0,-dot(s,eye),-dot(u,eye),dot(f,eye),1};std::copy(a,a+16,m);}
void mul(float*out,const float*a,const float*b){float q[16];for(int c=0;c<4;c++)for(int r=0;r<4;r++){q[c*4+r]=0;for(int k=0;k<4;k++)q[c*4+r]+=a[k*4+r]*b[c*4+k];}std::copy(q,q+16,out);}
void model(float*m,P at,float yaw,float scale=1,float pitch=0,float roll=0){
 auto rotate=[&](P p){float sx=std::sin(pitch),cx=std::cos(pitch),sz=std::sin(roll),cz=std::cos(roll),sy=std::sin(yaw),cy=std::cos(yaw);
  P a{p.x,p.y*cx-p.z*sx,p.y*sx+p.z*cx},b{a.x*cz-a.y*sz,a.x*sz+a.y*cz,a.z};return P{b.x*cy+b.z*sy,b.y,b.z*cy-b.x*sy}*scale;};
 P x=rotate({1,0,0}),y=rotate({0,1,0}),z=rotate({0,0,1});float a[16]={x.x,x.y,x.z,0,y.x,y.y,y.z,0,z.x,z.y,z.z,0,at.x,at.y,at.z,1};std::copy(a,a+16,m);}
void copy_pose(Mesh&dst,const Mesh&src,P at,float yaw,float pitch){float m[16];model(m,at,yaw,1,pitch);for(auto v:src){float x=v.x,y=v.y,z=v.z;v.x=m[0]*x+m[4]*y+m[8]*z+m[12];v.y=m[1]*x+m[5]*y+m[9]*z+m[13];v.z=m[2]*x+m[6]*y+m[10]*z+m[14];dst.push_back(v);}}
struct Frame {float s,x,speed,clock,steer,boost,shield,drift,slow,countdown;int state;};
struct Car {float s,x,speed,steer,boost,shield,drift,slow;int skin;};
struct Object {float s,x;int kind;};
struct Bounds {P lo{1e12f,1e12f,1e12f},hi{-1e12f,-1e12f,-1e12f};};
Bounds bounds(const Mesh&m){Bounds b;for(const auto&v:m){b.lo.x=std::min(b.lo.x,v.x);b.lo.y=std::min(b.lo.y,v.y);b.lo.z=std::min(b.lo.z,v.z);b.hi.x=std::max(b.hi.x,v.x);b.hi.y=std::max(b.hi.y,v.y);b.hi.z=std::max(b.hi.z,v.z);}return b;}
bool clip_visible(const Bounds&b,const float*m){unsigned outside=63;
 for(int i=0;i<8;i++){float x=i&1?b.hi.x:b.lo.x,y=i&2?b.hi.y:b.lo.y,z=i&4?b.hi.z:b.lo.z;
  float xx=m[0]*x+m[4]*y+m[8]*z+m[12],yy=m[1]*x+m[5]*y+m[9]*z+m[13],zz=m[2]*x+m[6]*y+m[10]*z+m[14],w=m[3]*x+m[7]*y+m[11]*z+m[15];
  // A two-percent clip-space margin is deliberately conservative around
  // MSAA edges and the near plane. An intersecting AABB is always retained.
  float epsilon=.02f*std::abs(w)+.002f;unsigned mask=0;
  if(xx<-w-epsilon)mask|=1;
  if(xx>w+epsilon)mask|=2;
  if(yy<-w-epsilon)mask|=4;
  if(yy>w+epsilon)mask|=8;
  if(zz<-w-epsilon)mask|=16;
  if(zz>w+epsilon)mask|=32;
  outside&=mask;
 }return outside==0;
}
struct Scene {std::string effective_flags_snapshot;void*gl=nullptr;int width=960,height=540;uint32_t chunks[CHUNKS][2]{},cars[6]{},title_car=0,bodies[6]{},drivers[6]{},wheels=0,background=0,pickups[3]{},attack=0,millwheel=0;int vertices=0,draws=0;Mesh dynamic,local_fx;
 struct Vbo {uint32_t flat=0,textured=0,texture_flat=0;Bounds flat_bounds,texture_bounds;int flat_count=0,texture_count=0;};std::vector<Vbo> vbos;
 bool frustum_cull=true;int culled=0,submitted_vertices=0;
 // A bounded queue covers one background, 24 course chunks and one mill.
 // Each logical mesh keeps its original flat/textured parts together.
 struct WorldDraw {uint32_t ids[2]{};int counts[2]{},parts=0;float mvp[16]{},depth=0;bool background=false;};
 WorldDraw world_draws[CHUNKS+2];int world_draw_count=0;bool world_depth_sort=false,collect_world=false;

 bool pickup_batch=false;std::string info;
 struct ItemTemplate {Mesh unique;std::vector<uint16_t> indices;};ItemTemplate item_templates[3];
 Mesh item_stream;std::vector<uint16_t> item_indices;
 float last_clock=-1,camera_speed=0,camera_boost=0,camera_steer=0,camera_drift=0;
 P mill_at{0,0,0};float mill_yaw=0;
 ~Scene(){if(gl)inkgl_destroy(gl);}
 void prepare_item(int kind,const Mesh&m){
  if(!pickup_batch)return;
  auto&item=item_templates[kind];inkgl_indexing::Result indexed;const char*format=std::getenv("INK_GL_STATIC_FORMAT");
  bool packed=(!format||std::strcmp(format,"float36"))&&inkgl_packing::inspect(m.data(),int(m.size()))==inkgl_packing::Eligibility::packable;
  if(packed){std::vector<inkgl_packing::StaticVertex> input;input.reserve(m.size());for(const auto&v:m)input.push_back(inkgl_packing::pack(v));indexed=inkgl_indexing::build(input.data(),int(m.size()),20);
   if(indexed.enabled)for(int i=0;i<indexed.unique;i++){inkgl_packing::StaticVertex p;std::memcpy(&p,indexed.vertices.data()+size_t(i)*20,20);item.unique.push_back({p.x,p.y,p.z,p.r/255.f,p.g/255.f,p.b/255.f,p.a/255.f,p.u/65535.f,p.v/65535.f});}
  }else{indexed=inkgl_indexing::build(m.data(),int(m.size()),sizeof(InkGlVertex));if(indexed.enabled){item.unique.resize(size_t(indexed.unique));std::memcpy(item.unique.data(),indexed.vertices.data(),indexed.vertices.size());}}
  if(indexed.enabled)item.indices=std::move(indexed.indices);
 }
 void flush_items(const float*vp){
  if(item_indices.empty())return;
  inkgl_draw_indexed(gl,item_stream.data(),int(item_stream.size()),item_indices.data(),int(item_indices.size()),vp);++draws;submitted_vertices+=int(item_indices.size());item_stream.clear();item_indices.clear();
 }
 bool append_item(int kind,P at,float yaw,const float*vp){
  auto&item=item_templates[kind];if(!pickup_batch||item.unique.empty())return false;
  float mm[16],mvp[16];model(mm,at,yaw);mul(mvp,vp,mm);const auto&vbo=vbos[pickups[kind]];
  if(!vbo.flat)return false;
  if(frustum_cull&&!clip_visible(vbo.flat_bounds,mvp)){++culled;return true;}
  if(item_stream.size()+item.unique.size()>65536)flush_items(vp);
  uint16_t offset=static_cast<uint16_t>(item_stream.size());
  for(auto v:item.unique){float x=v.x,y=v.y,z=v.z;v.x=mm[0]*x+mm[4]*y+mm[8]*z+mm[12];v.y=mm[1]*x+mm[5]*y+mm[9]*z+mm[13];v.z=mm[2]*x+mm[6]*y+mm[10]*z+mm[14];item_stream.push_back(v);}
  for(uint16_t index:item.indices)item_indices.push_back(static_cast<uint16_t>(offset+index));
  return true;
 }
 uint32_t upload(const Mesh&m){Mesh flat,textured;flat.reserve(m.size());textured.reserve(m.size()/4);
  for(size_t i=0;i+2<m.size();i+=3){bool uv=false;for(int j=0;j<3;j++)if(m[i+j].u!=0||m[i+j].v!=0)uv=true;Mesh&dst=uv?textured:flat;dst.insert(dst.end(),m.begin()+i,m.begin()+i+3);}
  Vbo vbo;if(!flat.empty()){vertices+=int(flat.size());vbo.flat_bounds=bounds(flat);vbo.flat_count=int(flat.size());vbo.flat=inkgl_mesh_create(gl,flat.data(),int(flat.size()));}
  if(!textured.empty()){vertices+=int(textured.size());vbo.texture_bounds=bounds(textured);vbo.texture_count=int(textured.size());vbo.textured=inkgl_mesh_create(gl,textured.data(),int(textured.size()));for(auto&v:textured)v.u=v.v=0;vertices+=int(textured.size());vbo.texture_flat=inkgl_mesh_create(gl,textured.data(),int(textured.size()));}
  vbos.push_back(vbo);return uint32_t(vbos.size()-1);
 }
};
void build(Scene*c){
 c->vbos.push_back(Scene::Vbo{});
 const char*cull=std::getenv("INK_KART_CULL");c->frustum_cull=!cull||std::strcmp(cull,"0")!=0;
 const char*depth_order=std::getenv("INK_KART_DEPTH_SORT");c->world_depth_sort=!depth_order||!std::strcmp(depth_order,"1");
 const char*batch=std::getenv("INK_KART_PICKUP_BATCH");c->pickup_batch=batch&&!std::strcmp(batch,"1");
 c->info=std::string(inkgl_info(c->gl))+(c->pickup_batch?" | pickup indexed batch on":" | pickup indexed batch off");
 c->info+=c->world_depth_sort?" | opaque world depth order on / background last":" | opaque world legacy order";
 if(c->pickup_batch){c->item_stream.reserve(65536);c->item_indices.reserve(65536);}
 material_atlas(c->gl);
 Mesh bg;
 // Bounded polar terrain cells leave an exact lake hole. Large areas use
 // low-frequency baked vertex colors, so grass never samples a texture.
 // A 32 m flat safety corridor prevents terrain crossing the racing deck.
 const float rings[]={130,142,156,172,188,204,224,248,272,300,332,368,408,456,520,650,850,1100,1500};
 auto land=[&](float r,float a){P p{-190+r*std::cos(a),0,-40+r*std::sin(a)};p.y=ground_height(p);return p;};
 for(int k=0;k<96;k++){float a=k*TAU/96,b=(k+1)*TAU/96;
  for(int j=0;j<18;j++){P u=land(rings[j],a),v0=land(rings[j],b),q=land(rings[j+1],b),w=land(rings[j+1],a);size_t start=bg.size();quad(bg,u,v0,q,w,{.53f,.76f,.39f});
   for(size_t n=start;n<bg.size();n++){float x=bg[n].x,z=bg[n].z,patch=.5f+.5f*std::sin(x*.029f+std::sin(z*.019f))*std::cos(z*.023f);
    float broad=.5f+.5f*std::sin(x*.012f+std::sin(z*.010f))*std::cos(z*.011f);
    float r=.47f+broad*.18f,g=.70f+broad*.10f,b=.31f+broad*.12f;
    bg[n].r=bg[n].r/.53f*r*(.97f+patch*.06f);bg[n].g=bg[n].g/.76f*g;bg[n].b=bg[n].b/.39f*b;}
  }
  P a0=land(130,a),b0=land(130,b),a1{-190+122*std::cos(a),-2.44f,-40+122*std::sin(a)},b1{-190+122*std::cos(b),-2.44f,-40+122*std::sin(b)};
  quad(bg,a0,b0,b1,a1,{.75f,.75f,.54f});
 }
 // Three continuous original painted ridges: layered silhouettes, not
 // repeated mountain ellipsoids. Same background mesh and opaque draw.
 for(int layer=0;layer<3;++layer){
  const float radius[]={360.f,470.f,600.f};
  const Color low[]={{.27f,.47f,.38f},{.42f,.61f,.54f},{.62f,.77f,.72f}};
  const Color high[]={{.43f,.64f,.51f},{.58f,.76f,.65f},{.76f,.87f,.80f}};
  auto height=[&](float a){return 26.f+layer*20.f+
   (14.f+layer*5.f)*std::sin(a*3.f+layer*2.1f)+
   (8.f+layer*3.f)*std::sin(a*7.f+1.2f+layer*.7f);};
  auto point=[&](float a,float y){return P{std::sin(a)*radius[layer],y,std::cos(a)*radius[layer]};};
  for(int k=0;k<96;++k){float a=k*TAU/96,b=(k+1)*TAU/96;
   P al=point(a,-17.f),ah=point(a,height(a)),bh=point(b,height(b)),bl=point(b,-17.f);
   // Direct colors are a painted atmospheric field; no per-face normal seams.
   v(bg,al,low[layer]);v(bg,ah,high[layer]);v(bg,bh,high[layer]);
   v(bg,al,low[layer]);v(bg,bh,high[layer]);v(bg,bl,low[layer]);
  }
 }
 for(int j=0;j<10;j++)for(int k=0;k<64;k++){float r0=j*12.2f,r1=(j+1)*12.2f,a=k*TAU/64,b=(k+1)*TAU/64;
  P u{-190+r0*std::cos(a),-2.45f,-40+r0*std::sin(a)},v0{-190+r0*std::cos(b),-2.45f,-40+r0*std::sin(b)},q{-190+r1*std::cos(b),-2.45f,-40+r1*std::sin(b)},w{-190+r1*std::cos(a),-2.45f,-40+r1*std::sin(a)};
  size_t start=bg.size();quad(bg,u,v0,q,w,{.25f,.69f,.76f});for(size_t n=start;n<bg.size();n++){float f=.95f+.05f*std::sin(bg[n].x*.12f+bg[n].z*.035f);bg[n].r*=f;bg[n].g*=f;bg[n].b*=f;}
 }
 // The sun and long cloud banks share the same warm, clear daylight.
 ellipsoid(bg,{-280,190,280},{18,18,18},{1.f,.92f,.70f},6,16);
 // Six merged cloud banks face the track from their tangential planes.
 // Union-envelope lobes remove end-on pill silhouettes; no alpha or texture.
 auto cloud_triangle=[&](P a,P b,P c,Color ca,Color cb,Color cc){
  P n=cross(b-a,c-a);if(n.x==0.f&&n.y==0.f&&n.z==0.f)return;
  v(bg,a,ca);v(bg,b,cb);v(bg,c,cc);
 };
 const float lobe_x[]={-.62f,-.22f,.28f,.68f};
 const float lobe_radius[]={.38f,.52f,.48f,.32f},lobe_height[]={.53f,1.f,.78f,.46f};
 for(int group=0;group<6;++group){float angle=group*TAU/6+.15f,rad=520.f;
  P center{std::sin(angle)*rad,107.f+(group%3)*10.f,std::cos(angle)*rad};
  P tangent{std::cos(angle),0,-std::sin(angle)};
  float half=73.f+(group%3)*10.f,tall=34.f+(group%2)*12.f;
  auto top=[&](float u){if(group%2)u=-u;float h=0;
   for(int j=0;j<4;++j){float t=(u-lobe_x[j])/lobe_radius[j];if(std::abs(t)<1.f)h=std::max(h,lobe_height[j]*std::sqrt(std::max(0.f,1-t*t)));}
   return h*tall;
  };
  auto point=[&](float u,float y){return center+tangent*(u*half)+P{0,y,0};};
  const Color low{.74f,.85f,.86f},mid{.91f,.95f,.91f},high{.99f,.99f,.95f};
  for(int k=0;k<20;++k){float a=-1.f+2.f*k/20,b=-1.f+2.f*(k+1)/20,ha=top(a),hb=top(b);
   P al=point(a,0),am=point(a,ha*.42f),ah=point(a,ha),bl=point(b,0),bm=point(b,hb*.42f),bh=point(b,hb);
   cloud_triangle(al,am,bm,low,mid,mid);cloud_triangle(al,bm,bl,low,mid,low);
   cloud_triangle(am,ah,bh,mid,high,high);cloud_triangle(am,bh,bm,mid,high,mid);
  }
 }
 c->background=c->upload(bg);
 Mesh pines_near[2]={pine(0),pine(1)},pines_far[2]={pine(0,true),pine(1,true)},bam_near=bamboo(),bam_far=bamboo(true),houses[2]={house(0),house(1)},town[2]={townhouse(0),townhouse(1)},tower=pagoda(),mill=millhouse(),pav=pavilion(),g=gate(),flower[2]={flowers(0),flowers(1)},stone=rock(),boards[2]={chevron(false),chevron(true)};
 for(int chunk=0;chunk<CHUNKS;++chunk)for(int detail=0;detail<2;detail++){Mesh m;Mesh(&pines)[2]=detail?pines_far:pines_near;const Mesh&bam=detail?bam_far:bam_near;
  for(int j=0;j<16;++j){int i=chunk*16+j;float d=track.s[i];
   // No wide offset terrain ribbon: its 29m inner edge folded at the
   // course's sub-29m bend radius. All soil is the stable floor above.
   // Asphalt, markings and kerbs partition one plane, without stacked
   // positive-area polygons that could fight for the depth buffer.
   Color asphalt{.37f,.42f,.46f};
   if(i==0){for(int row=0;row<3;++row)for(int col=0;col<14;++col){
     float d0=track.s[0]+(track.s[1]-track.s[0])*row/3.f,d1=track.s[0]+(track.s[1]-track.s[0])*(row+1)/3.f;
     band(m,d0,d1,-6.8f+13.6f*col/14,-6.8f+13.6f*(col+1)/14,0,(row+col)%2?IVORY:INK);}}
   else if(i%3==0){strip(m,i,-6.8f,-.075f,0,asphalt);strip(m,i,.075f,6.8f,0,asphalt);strip(m,i,-.075f,.075f,0,{.89f,.79f,.50f});}
   else strip(m,i,-6.8f,6.8f,0,asphalt);
   for(float side:{-1.f,1.f}){
    strip(m,i,std::min(side*6.8f,side*7.4f),std::max(side*6.8f,side*7.4f),0,i%2?IVORY:RED);
    strip(m,i,std::min(side*7.4f,side*7.45f),std::max(side*7.4f,side*7.45f),0,{.66f,.69f,.57f});
    P u=track.at(track.s[i],side*7.45f),w=track.at(track.s[i+1],side*7.45f);
    quad(m,u+P{0,-.25f,0},u,w,w+P{0,-.25f,0},{.42f,.44f,.36f});
    // Pale stone shoulder and warm wooden rail have clear silhouettes.
    strip(m,i,side*7.45f,side*8.2f,-.12f,{.77f,.77f,.59f});
    strip(m,i,side*7.55f,side*7.75f,.55f,{.53f,.38f,.23f});
    strip(m,i,side*7.55f,side*7.75f,.85f,IVORY);
    if(i%2==0){float yaw;P p=track.at(d,side*7.65f,&yaw);Mesh post;box(post,{0,-.25f,0},{.20f,1.20f,.20f},INK);copy(m,post,p,yaw);}
   }
   // A built village, a forested bend and a lake causeway are continuous
   // spaces rather than evenly spaced isolated props on a flat green plane.
   bool village=d>174&&d<292,forest=d>315&&d<446,bridge=track.bridge(d);
   if(village)for(float side:{-1.f,1.f}){
    band(m,track.s[i],track.s[i+1],std::min(side*10.6f,side*27.f),std::max(side*10.6f,side*27.f),-.05f,{.72f,.70f,.59f});
    P a=track.at(track.s[i],side*10.15f),b=track.at(track.s[i+1],side*10.15f);
    quad(m,a+P{0,-.2f,0},b+P{0,-.2f,0},b+P{0,1.0f,0},a+P{0,1.0f,0},IVORY);
    band(m,track.s[i],track.s[i+1],std::min(side*10.0f,side*10.4f),std::max(side*10.0f,side*10.4f),1.04f,{.20f,.37f,.41f});
    if(i%4==0){float yaw;P p=track.at(d,side*10.2f,&yaw);Mesh post;box(post,{0,-.20f,0},{.45f,1.50f,.45f},IVORY);box(post,{0,1.30f,0},{.62f,.15f,.62f},TEAL);copy(m,post,p,yaw);}
   }
   if(forest&&i%4==0){float yaw;P p=track.at(d,12.4f+(i%3),&yaw);grounded(m,bam,p,yaw,1.05f);grounded(m,bam,track.at(d+3,19.0f),yaw+.35f,1.20f);
    if(i%12==0){grounded(m,stone,track.at(d,-11.4f),yaw,1.1f);grounded(m,pines[0],track.at(d+6,-16.f),yaw,1.45f);}}
   if(!village&&!forest&&!bridge&&i%13==0){float yaw;P pos=track.at(d,14+(i%5),&yaw);grounded(m,pines[(i/13)%2],pos,yaw,1.f+(i%4)*.10f);
    grounded(m,pines[1],track.at(d+5,20+(i%3)),yaw,1.25f);}
   if((village||d>670)&&i%5==0){float yaw;track.at(d,0,&yaw);for(int j=0;j<3;j++)grounded(m,flower[(i/15)%2],track.at(d+j*1.9f,-9.1f-j*.35f),yaw,1.15f);}
   if(bridge){band(m,track.s[i],track.s[i+1],-7.45f,7.45f,-.35f,{.53f,.53f,.44f});
    for(float side:{-1.f,1.f}){band(m,track.s[i],track.s[i+1],std::min(side*7.45f,side*7.80f),std::max(side*7.45f,side*7.80f),1.04f,IVORY);
     if(i%4==0){float yaw;P p=track.at(d,side*7.65f,&yaw);Mesh post;box(post,{0,-.12f,0},{.42f,1.36f,.42f},IVORY);box(post,{0,1.24f,0},{.57f,.14f,.57f},TEAL);copy(m,post,p,yaw);}}
   }
   // Grouped buildings are placed by distance, so the village fixture sees
   // full-size near and mid-distance facades rather than distant symbols.
   struct Building {float s,side;int style;};const Building buildings[]={{24,1,0},{40,-1,1},{192,1,0},{208,-1,1},{237,-1,1},{249,1,0},{265,-1,0},{281,1,1},{688,-1,0},{707,1,1},{734,-1,0},{766,1,1}};
   for(const auto&h:buildings)if(d<=h.s&&track.s[i+1]>h.s){float yaw;P p=track.at(h.s,h.side*16.7f,&yaw);grounded_arch(m,town[h.style],p,yaw+h.side*PI*.5f);
    if(h.s>190&&h.s<290){grounded_arch(m,houses[h.style],track.at(h.s+5,h.side*30.5f),yaw+h.side*PI*.5f,1.1f);grounded(m,pines[1],track.at(h.s-6,h.side*25.f),yaw,1.35f);}}
     // Doorway approaches remain open: each bed is offset towards a
     // courtyard corner. The full footprint stays outside track |x| >= 9.3.
     for(const auto&h:buildings)if(h.s>190&&h.s<290){
      float ds=h.s-5.5f;if(d<=ds&&track.s[i+1]>ds){float yaw;
       P p=track.at(ds,h.side*12.15f,&yaw);p.y=-.05f;
       copy(m,village_bed(h.style),p,yaw);
      }
     }
   if(d<=276&&track.s[i+1]>276){float yaw;P p=track.at(276,29,&yaw);grounded_arch(m,tower,p,yaw+PI*.5f);}
   if(d<=542&&track.s[i+1]>542){float yaw;P p=track.at(542,-18.5f,&yaw);p.y=0;copy(m,mill,p,yaw-PI*.5f);c->mill_yaw=yaw-PI*.5f;float sn=std::sin(c->mill_yaw),cs=std::cos(c->mill_yaw);c->mill_at=p+P{3.2f*cs-3.72f*sn,1.4f,-3.2f*sn-3.72f*cs};}
   for(int arch=0;arch<7;arch++){float s0=460+arch*(190.f/7);if(d<=s0&&track.s[i+1]>s0)bridge_arch(m,s0,s0+190.f/7);}
   if(((d>175&&d<292)||d>670)&&i%11==0){Mesh flags;for(int k=0;k<9;k++){float x=-8.6f+k*2.15f;Color col=k%3==0?RED:k%3==1?GOLD:TEAL;tri(flags,{x,6.6f,0},{x+1.40f,6.6f,0},{x+.72f,5.65f,0},col);}float yaw;P p=track.at(d,0,&yaw);copy(m,flags,p,yaw);}
   if(i%7==0&&std::abs(track.curve(d+5))>.016f){float curve=track.curve(d+5),side=curve>0?-1.f:1.f,yaw;P p=track.at(d,side*9.65f,&yaw);grounded(m,boards[curve>0?1:0],p,yaw);}
   if(i%96==45){float yaw;P pos=soil(track.at(d,20,&yaw));grounded(m,pav,pos,yaw);}
   // Bamboo stakes carrying small saffron-red racing pennants.
   if(i%16==10){float yaw;P pos=soil(track.at(d,9.3f,&yaw));Mesh flag;cylinder(flag,{0,0,0},.07f,4.3f,INK,5);tri(flag,{0,4.15f,0},{0,3.3f,0},{1.4f,3.95f,0},i%32?GOLD:RED);grounded(m,flag,pos,yaw);}
  }
  if(chunk==0){float yaw;P pos=soil(track.at(2,0,&yaw));copy(m,g,pos,yaw);}
  c->chunks[chunk][detail]=c->upload(m);
 }
 for(int i=0;i<6;++i){c->cars[i]=c->upload(kart_lod(i));c->bodies[i]=c->upload(kart_body(i));c->drivers[i]=c->upload(driver(i));}c->wheels=c->upload(wheel_pair());c->title_car=c->upload(kart(0));
 c->millwheel=c->upload(waterwheel());
 for(int kind=0;kind<3;++kind){Mesh m;Color col=kind==0?GOLD:kind==1?Color{.24f,.76f,.85f}:RED;
  // Original faceted lantern capsules, each with a readable item emblem.
  ellipsoid(m,{0,1.18f,0},{.68f,.74f,.68f},col,5,10);
  for(float y:{.64f,1.68f})cylinder(m,{0,y,0},.55f,.075f,IVORY,10);
  if(kind==0){tri(m,{-.06f,1.58f,-.67f},{-.35f,1.12f,-.67f},{.12f,1.19f,-.67f},IVORY);tri(m,{.12f,1.19f,-.67f},{-.06f,.83f,-.67f},{.36f,1.37f,-.67f},IVORY);}
  else if(kind==1){quad(m,{-.31f,1.48f,-.66f},{.31f,1.48f,-.66f},{.23f,1.04f,-.66f},{0,.84f,-.66f},IVORY);tri(m,{-.31f,1.48f,-.66f},{0,.84f,-.66f},{-.23f,1.04f,-.66f},IVORY);}
  else{ellipsoid(m,{0,1.20f,-.63f},{.28f,.25f,.08f},IVORY,3,8);tri(m,{-.14f,1.05f,-.65f},{-.40f,.86f,-.65f},{-.27f,1.24f,-.65f},GOLD);}
  c->pickups[kind]=c->upload(m);c->prepare_item(kind,m);
 }
 Mesh attack;ellipsoid(attack,{0,.75f,0},{.32f,.32f,.65f},RED,4,8);cone(attack,{0,.5f,-.5f},.3f,.55f,GOLD,6);c->attack=c->upload(attack);
 c->dynamic.reserve(12000);c->local_fx.reserve(768);
}
// Camera-space forward depth is clip w in this perspective projection.
float world_near_depth(const Bounds&b,const float*mvp){float depth=1e30f;
 for(int i=0;i<8;i++){float x=i&1?b.hi.x:b.lo.x,y=i&2?b.hi.y:b.lo.y,z=i&4?b.hi.z:b.lo.z;float w=mvp[3]*x+mvp[7]*y+mvp[11]*z+mvp[15];depth=std::min(depth,w);}return std::isfinite(depth)?depth:0.f;
}
void queue_world_mesh(Scene*c,uint32_t mesh,const float*mvp,bool material){
 const auto&vbo=c->vbos[mesh];Scene::WorldDraw record{};std::copy(mvp,mvp+16,record.mvp);record.background=mesh==c->background;record.depth=1e30f;
 auto part=[&](uint32_t id,int count,const Bounds&b){if(!id)return;if(!c->frustum_cull||clip_visible(b,mvp)){int n=record.parts++;record.ids[n]=id;record.counts[n]=count;record.depth=std::min(record.depth,world_near_depth(b,mvp));}else ++c->culled;};
 part(vbo.flat,vbo.flat_count,vbo.flat_bounds);part(material?vbo.textured:vbo.texture_flat,vbo.texture_count,vbo.texture_bounds);
 if(record.parts)c->world_draws[c->world_draw_count++]=record;
}
void flush_world(Scene*c){
 c->collect_world=false;
 auto before=[](const Scene::WorldDraw&a,const Scene::WorldDraw&b){if(a.background!=b.background)return !a.background;return a.depth<b.depth;};
 // Stable insertion sort over at most 26 entries avoids frame-time allocation.
 for(int i=1;i<c->world_draw_count;i++){auto value=c->world_draws[i];int j=i;while(j>0&&before(value,c->world_draws[j-1])){c->world_draws[j]=c->world_draws[j-1];--j;}c->world_draws[j]=value;}
 for(int i=0;i<c->world_draw_count;i++){const auto&record=c->world_draws[i];for(int part=0;part<record.parts;part++){inkgl_mesh_draw(c->gl,record.ids[part],record.mvp);++c->draws;c->submitted_vertices+=record.counts[part];}}
}
void drawmesh(Scene*c,uint32_t mesh,const float*vp,P at={0,0,0},float yaw=0,float scale=1,float pitch=0,float roll=0,bool material=true){if(!mesh||mesh>=c->vbos.size())return;float mm[16],mvp[16];model(mm,at,yaw,scale,pitch,roll);mul(mvp,vp,mm);const auto&vbo=c->vbos[mesh];
 if(c->collect_world){queue_world_mesh(c,mesh,mvp,material);return;}
 if(vbo.flat){if(!c->frustum_cull||clip_visible(vbo.flat_bounds,mvp)){inkgl_mesh_draw(c->gl,vbo.flat,mvp);++c->draws;c->submitted_vertices+=vbo.flat_count;}else ++c->culled;}
 uint32_t textured=material?vbo.textured:vbo.texture_flat;if(textured){if(!c->frustum_cull||clip_visible(vbo.texture_bounds,mvp)){inkgl_mesh_draw(c->gl,textured,mvp);++c->draws;c->submitted_vertices+=vbo.texture_count;}else ++c->culled;}}
bool visible(float d,float s,float ahead=265){float rel=std::fmod(d-s+track.length,track.length);return rel<ahead||rel>track.length-35;}
void render(Scene*c,const Frame&f,const Car*cars,int nc,const Object*objects,int no){
 float sky[]={.46f,.80f,.95f},horizon[]={.85f,.95f,.95f};inkgl_begin(c->gl,sky,horizon);
 float yaw;P at=track.at(f.s,f.x,&yaw),forward{std::sin(yaw),0,std::cos(yaw)};
 float drift_sign=f.drift==0?0.f:std::copysign(1.f,f.drift),speed_target=std::min(1.f,f.speed/48.f),boost_target=f.boost>0?std::min(1.f,f.boost*2):0;
 if(c->last_clock<0||f.clock<c->last_clock){c->camera_speed=speed_target;c->camera_boost=boost_target;c->camera_steer=f.steer;c->camera_drift=drift_sign;}
 float dt=std::max(0.f,std::min(.1f,f.clock-c->last_clock)),mix=1-std::exp(-dt*7.f);c->last_clock=f.clock;
 c->camera_speed+=(speed_target-c->camera_speed)*mix;c->camera_boost+=(boost_target-c->camera_boost)*mix;c->camera_steer+=(f.steer-c->camera_steer)*mix;c->camera_drift+=(drift_sign-c->camera_drift)*mix;
 float speed=c->camera_speed,boost_mix=c->camera_boost;
 float camera_x=f.x*.66f-c->camera_steer*.55f-c->camera_drift*.28f;
 P eye=track.at(f.s-9.8f-speed*.45f-boost_mix*.35f,camera_x)+P{0,4.65f+speed*.14f,0};
 P target=track.at(f.s+18,f.x*.36f+c->camera_steer*.45f)+P{0,1.1f,0};
 if(f.slow>1.25f){float shake=(f.slow-1.25f)*.14f;eye.x+=std::sin(f.clock*48)*shake;eye.y+=std::cos(f.clock*37)*shake*.55f;}
 float proj[16],view[16],vp[16];perspective(proj,(57.f+speed*3.f+boost_mix*3.5f)*PI/180,float(c->width)/c->height,2.4f,1050);lookat(view,eye,target);mul(vp,proj,view);
 float fog[]={.78f,.91f,.87f};inkgl_set_fog(c->gl,135,610,fog);c->draws=0;c->culled=0;c->submitted_vertices=0;
 c->world_draw_count=0;c->collect_world=c->world_depth_sort;
 drawmesh(c,c->background,vp);
 for(int i=0;i<CHUNKS;++i)if(visible(track.s[i*16],f.s,285)||visible(track.s[i*16+16],f.s,285)){bool close=visible(track.s[i*16],f.s,58)||visible(track.s[i*16+16],f.s,58);drawmesh(c,c->chunks[i][close?0:1],vp,{0,0,0},0,1,0,0,visible(track.s[i*16],f.s,85));}
 if(visible(542,f.s,255))drawmesh(c,c->millwheel,vp,c->mill_at,c->mill_yaw,1,0,-f.clock*.22f);
 if(c->collect_world)flush_world(c);
 Mesh&fx=c->dynamic;fx.clear();
 for(int i=0;i<no&&i<96;++i){auto&o=objects[i];if(!visible(o.s,f.s,245))continue;float angle;P p=track.at(o.s,o.x,&angle);
  if(o.kind<3){P pos=p+P{0,.12f*std::sin(f.clock*3+i),0};float rotation=f.clock*1.35f+i;if(!c->append_item(o.kind,pos,rotation,vp))drawmesh(c,c->pickups[o.kind],vp,pos,rotation);}
  else if(o.kind==3){c->flush_items(vp);drawmesh(c,c->attack,vp,p+P{0,.1f*std::sin(f.clock*15),0},angle);}
  else if(o.kind>=100){int kind=(o.kind-100)/64;float age=float((o.kind-100)%64)/63;
   Color color=kind==1||kind==4?Color{1.f,.52f,.20f}:kind==2?Color{.35f,.87f,1.f}:GOLD;color.a=(1-age)*.8f;
   Mesh&local=c->local_fx;local.clear();
   float radius=.7f+age*3.3f;
   for(int j=0;j<20;j++){float aa=j*TAU/20,bb=(j+1)*TAU/20;
    quad(local,{radius*std::cos(aa),.11f,radius*std::sin(aa)},{radius*std::cos(bb),.11f,radius*std::sin(bb)},
      {(radius+.10f)*std::cos(bb),.11f,(radius+.10f)*std::sin(bb)},{(radius+.10f)*std::cos(aa),.11f,(radius+.10f)*std::sin(aa)},color);
   }
   if(kind<=2||kind==4)for(int j=0;j<9;j++){float a0=j*TAU/9+f.clock*.6f;P q{std::cos(a0)*age*3.2f,.5f+std::sin(age*PI)*2.1f,std::sin(a0)*age*3.2f};
    tri(local,q+P{-.11f,0,0},q+P{.11f,0,0},q+P{0,.28f,0},color);}
   copy_pose(fx,local,p,angle,-std::atan(track.grade(o.s)));
  }
 }
 c->flush_items(vp);
 for(int i=0;i<nc&&i<6;++i){const Car&r=cars[i];if(i&& !visible(r.s,f.s,245))continue;float a;P p=track.at(r.s,r.x,&a);
  float ds=r.drift==0?0.f:std::copysign(1.f,r.drift),charge=std::max(0.f,std::abs(r.drift)-1.f),slip=ds*.27f+r.steer*.045f;
  float bob=.018f*std::sin(r.s*1.8f+i)*std::min(1.f,r.speed/20.f),lean=-r.steer*.045f-ds*.028f;
  float relative=std::fmod(r.s-f.s+track.length*1.5f,track.length)-track.length*.5f,grade=track.grade(r.s),pitch=-std::atan(grade);
  if(i==0&&f.state==0)drawmesh(c,c->title_car,vp,p,a+slip,1,pitch);
  else if(i==0||std::abs(relative)<14){
   drawmesh(c,c->bodies[r.skin%6],vp,p+P{0,bob,0},a+slip,1,pitch,lean);
   drawmesh(c,c->drivers[r.skin%6],vp,p+P{0,bob,0},a+slip+r.steer*.065f,1,pitch,lean*1.9f);
   for(float axle:{-.92f,.93f}){float sn=std::sin(a+slip),cs=std::cos(a+slip);P center=p+P{axle*sn,.48f+grade*axle,axle*cs};
    drawmesh(c,c->wheels,vp,center,a+slip+(axle>0?r.steer*.20f:0),1,-r.s/.48f);}
  }else drawmesh(c,c->cars[r.skin%6],vp,p,a+slip,1,pitch);
  Mesh&local=c->local_fx;local.clear();
  // Ground contact shadow, speed exhaust and alternating drift sparks.
  for(int j=0;j<18;++j){float aa=j*TAU/18,bb=(j+1)*TAU/18;
   size_t start=local.size();tri(local,{0,.027f,0},{1.50f*std::cos(aa),.027f,1.92f*std::sin(aa)},{1.50f*std::cos(bb),.027f,1.92f*std::sin(bb)},{.05f,.09f,.12f,.30f});
   local[start+1].a=0;local[start+2].a=0;
  }
  if(r.boost>0){for(float x:{-.34f,.34f}){float len=1.0f+.4f*std::sin(f.clock*37+i);tri(local,{x-.23f,.48f,-1.54f},{x+.23f,.48f,-1.54f},{x,.65f,-2.0f-len},{1,.68f,.12f,.92f});tri(local,{x-.12f,.53f,-1.56f},{x+.12f,.53f,-1.56f},{x,.65f,-1.85f-len*.7f},{.48f,.88f,1,.95f});}}
  if(r.drift!=0){Color spark=charge<1.25f?Color{.27f,.80f,1.f}:charge<1.95f?GOLD:Color{.94f,.44f,1.f};
   for(int j=0;j<12;++j){float t=std::fmod(f.clock*2.4f+j*.083f,1.f),x=ds*1.09f+ds*t*.9f,z=-.9f-t*(1.4f+charge*.55f);Color col=j%3?spark:IVORY;tri(local,{x,.10f+t*.24f,z},{x+.11f,.14f+t*.24f,z-.17f},{x-.06f,.19f+t*.52f,z-.27f},col);}
  }
  if(r.slow>0){for(int j=0;j<4;++j){float aa=f.clock*6+j*TAU/4;ellipsoid(local,{std::cos(aa)*.7f,2.25f,std::sin(aa)*.7f},{.10f,.10f,.10f},GOLD,2,5);}}
  if(r.shield>0){for(int j=0;j<18;++j){float aa=j*TAU/18,bb=(j+1)*TAU/18;quad(local,{1.5f*std::cos(aa),.25f,1.8f*std::sin(aa)},{1.5f*std::cos(aa),1.65f,1.8f*std::sin(aa)},{1.5f*std::cos(bb),1.65f,1.8f*std::sin(bb)},{1.5f*std::cos(bb),.25f,1.8f*std::sin(bb)},{.28f,.91f,1.f,.16f});}}
  copy_pose(fx,local,p,a+slip,pitch);
 }
 inkgl_set_blend(c->gl,1);if(!fx.empty()){inkgl_draw(c->gl,fx.data(),int(fx.size()),vp);++c->draws;}inkgl_set_blend(c->gl,0);
 (void)at;(void)forward;
}
}
extern "C" {

// BEGIN ADDITIVE SCENE EFFECTIVE FLAGS API
const char*kart_effective_flags(void*p){
 auto*s=(Scene*)p;if(!s)return nullptr;const char*raw=inkgl_effective_flags(s->gl);if(!raw)return nullptr;
 std::string result=raw,extra;const std::string marker="\"effective\":{";const size_t at=result.find(marker);
 if(at==std::string::npos)return nullptr;
 extra+="\"INK_KART_CULL\":\"";extra+=s->frustum_cull?"1":"0";extra+="\",";
 extra+="\"INK_KART_PICKUP_BATCH\":\"";extra+=s->pickup_batch?"1":"0";extra+="\",";
 extra+="\"INK_KART_DEPTH_SORT\":\"";extra+=s->world_depth_sort?"1":"0";extra+="\",";
 result.insert(at+marker.size(),extra);s->effective_flags_snapshot=std::move(result);return s->effective_flags_snapshot.c_str();
}
// END ADDITIVE SCENE EFFECTIVE FLAGS API

int kart_abi_version(){return 1;}int kart_frame_size(){return sizeof(Frame);}int kart_car_size(){return sizeof(Car);}int kart_object_size(){return sizeof(Object);}
void*kart_create(uint64_t xid,int w,int h,int offscreen,char*error,int size){auto*c=new Scene;c->width=w;c->height=h;c->gl=offscreen?inkgl_create_offscreen(w,h,error,size):inkgl_create(xid,w,h,error,size);if(!c->gl){delete c;return nullptr;}build(c);return c;}
int kart_resize(void*p,int w,int h){auto*c=(Scene*)p;if(!c)return 0;c->width=w;c->height=h;return inkgl_resize(c->gl,w,h);}
int kart_frame(void*p,const Frame*f,const Car*cars,int nc,const Object*objects,int no){auto*c=(Scene*)p;if(!c||!f||!cars||nc<1||nc>6||no<0||no>96)return 0;render(c,*f,cars,nc,objects,no);return 1;}
int kart_present(void*p){auto*c=(Scene*)p;return c?inkgl_present(c->gl):0;}
int kart_set_hud(void*p,const unsigned char*px,int w,int h,int stride){auto*c=(Scene*)p;return c?inkgl_set_hud_bgra(c->gl,px,w,h,stride):0;}
int kart_patch_hud(void*p,const unsigned char*px,int x,int y,int w,int h,int stride){auto*c=(Scene*)p;return c?inkgl_patch_hud_bgra(c->gl,px,x,y,w,h,stride):0;}
const char*kart_info(void*p){auto*c=(Scene*)p;return c?c->info.c_str():"no context";}
int kart_vertices(void*p){auto*c=(Scene*)p;return c?c->vertices:0;}int kart_draws(void*p){auto*c=(Scene*)p;return c?c->draws:0;}
int kart_culled(void*p){auto*c=(Scene*)p;return c?c->culled:0;}int kart_submitted_vertices(void*p){auto*c=(Scene*)p;return c?c->submitted_vertices:0;}
int kart_readback(void*p,unsigned char*px,int cap){auto*c=(Scene*)p;return c?inkgl_read_rgba(c->gl,px,cap):0;}
void kart_destroy(void*p){delete (Scene*)p;}
float kart_track_length(){return track.length;}
}
