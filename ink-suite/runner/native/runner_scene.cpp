// Original native three-dimensional railway scene. Physics and input stay in Python.
#include "ink_gl_backend.h"
#include <vector>
#include <cmath>
#include <algorithm>
#include <cstring>
#include <cstdio>
#include <chrono>
#include <string>
#include <cstdlib>
#include <limits>
#include "runner_character.h"

namespace {
constexpr float PI=3.14159265359f;
struct P {float x,y,z;};
struct C {float r,g,b,a=1;};
P operator+(P a,P b){return {a.x+b.x,a.y+b.y,a.z+b.z};}
P operator-(P a,P b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
P cross(P a,P b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
using Mesh=std::vector<InkGlVertex>;
void vertex(Mesh&m,P p,C c){m.push_back({p.x,p.y,p.z,c.r,c.g,c.b,c.a});}
C shade(C c,P n){
 // Baked warm sunlight and cool sky fill. Static scenery pays this once at
 // creation, with no normal attribute or extra fragment work on Mali-400.
 float len=std::max(.001f,std::sqrt(n.x*n.x+n.y*n.y+n.z*n.z));
 float sun=std::max(0.f,(-n.x*.48f+n.y*.80f-n.z*.36f)/len),sky=std::max(0.f,n.y/len);
 return{std::min(1.f,c.r*(.65f+.37f*sun+.05f*sky)),std::min(1.f,c.g*(.70f+.31f*sun+.035f*sky)),std::min(1.f,c.b*(.76f+.20f*sun)),c.a};
}
void quad(Mesh&m,P a,P b,P c,P d,C col,bool reverse=false){P n=cross(b-a,c-a);if(reverse)n={-n.x,-n.y,-n.z};C sh=shade(col,n);for(P p:{a,b,c,a,c,d})vertex(m,p,sh);}
// Atlas coordinates are inset four texels so mip levels do not bleed between materials.
void vertex_uv(Mesh&m,P p,C c,float u,float v){m.push_back({p.x,p.y,p.z,c.r,c.g,c.b,c.a,u,v});}
void uvquad(Mesh&m,P a,P b,P c,P d,C col,int tile,bool reverse=false){
 P n=cross(b-a,c-a);if(reverse)n={-n.x,-n.y,-n.z};C sh=shade(col,n);float u=(tile%4)*.25f+4.f/1024,v=(tile/4)*.25f+4.f/1024,e=.25f-8.f/1024;
 vertex_uv(m,a,sh,u,v+e);vertex_uv(m,b,sh,u,v);vertex_uv(m,c,sh,u+e,v);
 vertex_uv(m,a,sh,u,v+e);vertex_uv(m,c,sh,u+e,v);vertex_uv(m,d,sh,u+e,v+e);
}
// A surface region samples its own part of one tile. Shared mesh edges therefore
// keep identical UVs instead of restarting an entire texture on every quad.
void uvregion(Mesh&m,P a,P b,P c,P d,C col,int tile,float u0,float v0,float u1,float v1,bool reverse=false){
 P ps[]={a,b,c,d};float us[]={u0,u0,u1,u1},vs[]={v0,v1,v1,v0};
 C sh=shade(col,reverse?cross(d-a,c-a):cross(b-a,c-a));
 float x=(tile%4)*.25f+4.f/1024,y=(tile/4)*.25f+4.f/1024,e=.25f-8.f/1024;
 const int normal[]={0,1,2,0,2,3},back[]={0,3,2,0,2,1};const int*ix=reverse?back:normal;
 for(int j=0;j<6;++j){int k=ix[j];vertex_uv(m,ps[k],sh,x+us[k]*e,y+vs[k]*e);}
}
void uvgradient(Mesh&m,P a,P b,P c,P d,C low,C high,int tile,bool reverse=false){
 P n=cross(b-a,c-a);if(reverse)n={-n.x,-n.y,-n.z};C lo=shade(low,n),hi=shade(high,n);float u=(tile%4)*.25f+4.f/1024,v=(tile/4)*.25f+4.f/1024,e=.25f-8.f/1024;
 vertex_uv(m,a,lo,u,v+e);vertex_uv(m,b,hi,u,v);vertex_uv(m,c,hi,u+e,v);
 vertex_uv(m,a,lo,u,v+e);vertex_uv(m,c,hi,u+e,v);vertex_uv(m,d,lo,u+e,v+e);
}
void uvcolors(Mesh&m,P a,P b,P c,P d,C ca,C cb,C cc,C cd,int tile,bool reverse=false){
 P n=cross(b-a,c-a);if(reverse)n={-n.x,-n.y,-n.z};
 P points[]={a,b,c,d};C colors[]={shade(ca,n),shade(cb,n),shade(cc,n),shade(cd,n)};
 float u=(tile%4)*.25f+4.f/1024,v=(tile/4)*.25f+4.f/1024,e=.25f-8.f/1024;
 const float us[]={0,0,1,1},vs[]={1,0,0,1};
 for(int k:{0,1,2,0,2,3})vertex_uv(m,points[k],colors[k],u+us[k]*e,v+vs[k]*e);
}
// Continuous atlas coordinates and per-height ambient occlusion. All bands
// replace the original wall plane; there are no coplanar shadow overlays.
void wall_bands(Mesh&m,P foot0,P foot1,float top,C color,int tile,bool reverse=false){
 float bottom=foot0.y,h=top-bottom;
 const float heights[]={0.f,.09f,.40f,.88f,1.f},light[]={.66f,.89f,1.08f,1.01f,.65f};
 P normal=cross(P{foot0.x,top,foot0.z}-foot0,foot1-foot0);if(reverse)normal={-normal.x,-normal.y,-normal.z};
 float x=(tile%4)*.25f+4.f/1024,y=(tile/4)*.25f+4.f/1024,e=.25f-8.f/1024;
 for(int j=0;j<4;++j){float ya=bottom+h*heights[j],yb=bottom+h*heights[j+1];
  C a=shade({color.r*light[j],color.g*light[j],color.b*light[j],color.a},normal),b=shade({color.r*light[j+1],color.g*light[j+1],color.b*light[j+1],color.a},normal);
  P p[]={P{foot0.x,ya,foot0.z},P{foot0.x,yb,foot0.z},P{foot1.x,yb,foot1.z},P{foot1.x,ya,foot1.z}};
  C cs[]={a,b,b,a};float us[]={0,0,1,1},vs[]={1-heights[j],1-heights[j+1],1-heights[j+1],1-heights[j]};
  for(int k:{0,1,2,0,2,3})vertex_uv(m,p[k],cs[k],x+us[k]*e,y+vs[k]*e);
 }
}
void box(Mesh&m,float x,float y,float z,float w,float h,float d,C c,bool top=true,bool front=true){
 float l=x-w*.5f,r=x+w*.5f,b=y,t=y+h,n=z,f=z+d;
 if(front)quad(m,{l,b,n},{l,t,n},{r,t,n},{r,b,n},c);
 quad(m,{r,b,f},{r,t,f},{l,t,f},{l,b,f},c);
 quad(m,{l,b,f},{l,t,f},{l,t,n},{l,b,n},c);quad(m,{r,b,n},{r,t,n},{r,t,f},{r,b,f},c);
 if(top)quad(m,{l,t,n},{l,t,f},{r,t,f},{r,t,n},c);
 quad(m,{l,b,f},{l,b,n},{r,b,n},{r,b,f},c);
}
void disk(Mesh&m,float x,float y,float z,float rx,float rz,C c,int n=20){
 for(int i=0;i<n;++i){float a=i*2*PI/n,b=(i+1)*2*PI/n;vertex(m,{x,y,z},c);vertex(m,{x+rx*std::cos(a),y,z+rz*std::sin(a)},c);vertex(m,{x+rx*std::cos(b),y,z+rz*std::sin(b)},c);}
}
void contact_shadow(Mesh&m,float x,float y,float z,float rx,float rz,float alpha){
 // One small interpolated penumbra. It shares the existing blended shadow
 // pass and fades to zero at the edge, instead of a hard floating ellipse.
 const int n=16;C inner{.10f,.18f,.23f,alpha},outer{.10f,.18f,.23f,0};
 for(int k=0;k<n;++k){float a=k*2*PI/n,b=(k+1)*2*PI/n;
  P ia{x+rx*.50f*std::cos(a),y,z+rz*.50f*std::sin(a)},ib{x+rx*.50f*std::cos(b),y,z+rz*.50f*std::sin(b)},oa{x+rx*std::cos(a),y,z+rz*std::sin(a)},ob{x+rx*std::cos(b),y,z+rz*std::sin(b)};
  vertex(m,{x,y,z},inner);vertex(m,ia,inner);vertex(m,ib,inner);
  vertex(m,ia,inner);vertex(m,oa,outer);vertex(m,ob,outer);vertex(m,ia,inner);vertex(m,ob,outer);vertex(m,ib,inner);
 }
}
void sphere(Mesh&m,P at,P scale,C c,int rings=5,int seg=10){
 auto p=[&](float a,float b){return P{at.x+scale.x*std::sin(a)*std::cos(b),at.y+scale.y*std::cos(a),at.z+scale.z*std::sin(a)*std::sin(b)};};
 for(int j=0;j<rings;++j)for(int i=0;i<seg;++i){float a=j*PI/rings,aa=(j+1)*PI/rings,b=i*2*PI/seg,bb=(i+1)*2*PI/seg;quad(m,p(a,b),p(a,bb),p(aa,bb),p(aa,b),c);}
}
void copy(Mesh&dst,const Mesh&src,float x=0,float y=0,float z=0,float angle=0,float alpha=1){
 float s=std::sin(angle),c=std::cos(angle);for(auto v:src){float ox=v.x;v.x=x+ox*c+v.z*s;v.y+=y;v.z=z+v.z*c-ox*s;v.a*=alpha;dst.push_back(v);}
}
const C CREAM{.96f,.88f,.68f},TEAL{.13f,.52f,.51f},CORAL{.82f,.23f,.16f},NAVY{.10f,.22f,.28f},GOLD{1.f,.73f,.24f};

// Baked vertex illumination: each wall has shaded feet/eaves and a warm midtone.
// This adds only immutable vertices and no per-fragment material work on Mali.
C tint(C c,float f,float warm=0){return {std::min(1.f,c.r*f+warm),std::min(1.f,c.g*f+warm*.60f),std::min(1.f,c.b*f),c.a};}
void gradient_quad(Mesh&m,P a,P b,P c,P d,C low,C high,bool reverse=false){
 P n=cross(b-a,c-a);if(reverse)n={-n.x,-n.y,-n.z};C lo=shade(low,n),hi=shade(high,n);
 vertex(m,a,lo);vertex(m,b,hi);vertex(m,c,hi);vertex(m,a,lo);vertex(m,c,hi);vertex(m,d,lo);
}
void plaster_wall(Mesh&m,float x,float z,float w,float h,float d,C color){
 float levels[]={.18f,.48f,h*.40f,h-.32f,h+.18f};
 float light[]={.64f,.88f,1.12f,1.f,.63f};
 float l=x-w*.5f,r=x+w*.5f;
 for(int j=0;j<4;++j){float a=levels[j],b=levels[j+1];
  C lo=tint(color,light[j],j==1?.025f:0),hi=tint(color,light[j+1],j==1?.025f:0);
  gradient_quad(m,{l,a,z},{l,b,z},{r,b,z},{r,a,z},lo,hi);
  if(x<0)gradient_quad(m,{r,a,z},{r,b,z},{r,b,z+d},{r,a,z+d},lo,hi);
  else gradient_quad(m,{l,a,z+d},{l,b,z+d},{l,b,z},{l,a,z},lo,hi);
 }
}
void rounded_box(Mesh&m,float x,float y,float z,float w,float h,float d,C c,float bevel){
 charactermesh::Mesh source;charactermesh::bevel_box(source,{x-w/2,y,z},{x+w/2,y+h,z+d},bevel,{c.r,c.g,c.b});
 for(auto idx:source.indices){const auto&v=source.vertices[idx];C sh=shade(c,{v.nx,v.ny,v.nz});vertex(m,{v.x,v.y,v.z},sh);}
}

Mesh make_train(int style){
 Mesh m; m.reserve(6200);
 C body=style==0?C{.12f,.68f,.59f}:style==1?C{.96f,.31f,.20f}:C{.28f,.53f,.84f};
 C dark{.07f,.16f,.21f},glass{.10f,.31f,.42f},ivory{1.f,.96f,.81f};
 // Continuous rounded shoulder and tucked-under skirt, not a bevelled box.
 const float xx[]={-.81f,-.91f,-.935f,-.935f,-.92f,-.88f,-.84f,-.79f,.79f,.84f,.88f,.92f,.935f,.935f,.91f,.81f};
 const float yy[]={.48f,.64f,.90f,1.94f,2.12f,2.28f,2.42f,2.49f,2.49f,2.42f,2.28f,2.12f,1.94f,.90f,.64f,.48f};
 const float zz[]={0,.08f,.22f,.40f,3.f,4.10f,6.8f,6.98f,7.12f,7.20f};
 const float scale[]={.88f,.94f,.985f,1,1,1,1,.985f,.94f,.88f};
 const float bow[]={1,.82f,.48f,0,0,0,0,.48f,.82f,1};
 auto shift=[](float y){return std::max(0.f,y-1.10f)*.24f;};
 auto cab_curve=[](float x,float y){float u=x/.93f,shoulder=1-std::max(0.f,std::min(1.f,(y-2.10f)/.39f));return .12f*u*u*u*u*shoulder;};
 auto point=[&](int i,int j){float x=xx[i]*scale[j],z=zz[j]+(j<4?1.f:-1.f)*bow[j]*(.012f+shift(yy[i]));if(j<4)z+=cab_curve(x,yy[i])*bow[j];return P{x,yy[i],z};};
 auto normal=[&](int i){int a=(i+15)%16,b=(i+1)%16;return P{-(yy[b]-yy[a]),xx[b]-xx[a],0};};
 for(int j=0;j<9;++j)for(int i=0;i<16;++i){int n=(i+1)%16;
  // Real recessed paired doors interrupt the straight side wall, not an overlaid box.
  if(j==4&&((i>=1&&i<=3)||(i>=11&&i<=13)))continue;
  // Broad value bands describe the continuous shoulder, painted shell and
  // recessed undercarriage before the material adds its smaller detail.
  C col=i==7?ivory:((i>=5&&i<=9)?tint(body,1.15f):tint(body,(i==0||i==15)?.59f:(i==1||i==14)?.82f:1.f));
  P a=point(i,j),b=point(i,j+1),c=point(n,j+1),d=point(n,j);C ca=shade(col,normal(i)),cb=shade(col,normal(n));
  float u0=7%4*.25f+4.f/1024,v0=7/4*.25f+4.f/1024,e=.25f-8.f/1024;
  for(int k:{0,1,2,0,2,3}){P p=k==0?a:k==1?b:k==2?c:d;C co=k<2?ca:cb;vertex_uv(m,p,co,u0+(k==1||k==2?e:0),v0+(k>=2?0:e));}
 }
 // Sloped cab bulkheads terminate at the same gameplay end planes.
 for(int end:{0,9}){P center{0,1.38f,zz[end]+(end==0?1:-1)*(.012f+shift(1.38f))};
  auto painted=[&](P p){C col=tint(body,p.y<.90f?.69f:p.y>2.16f?1.10f:1.f);return shade(col,{p.x*.40f,.15f,end==0?-1.f:1.f});};
  for(int i=0;i<16;++i){P a=point(i,end),b=point((i+1)%16,end);vertex(m,center,painted(center));vertex(m,a,painted(a));vertex(m,b,painted(b));}}
 box(m,0,.27f,.12f,1.69f,.26f,6.96f,dark);
 rounded_box(m,0,.51f,.012f,1.59f,.13f,.095f,{.37f,.45f,.45f},.055f);
 // The cab and glass bend across their width. A real open gasket surrounds
 // the glass, with shared boundary vertices instead of a stack of rectangles.
 auto front=[&](float x,float y,float delta=-.013f){return P{x,y,std::max(0.f,shift(y)+delta)+cab_curve(x,y)};};
 const float wx[]={-.62f,.62f,.70f,.65f,.58f,-.58f,-.65f,-.70f},wy[]={1.27f,1.27f,1.35f,2.01f,2.09f,2.09f,2.01f,1.35f};
 for(int k=0;k<8;++k){int next=(k+1)%8;float ya=1.68f+(wy[k]-1.68f)*1.15f,yb=1.68f+(wy[next]-1.68f)*1.15f;
  quad(m,front(wx[k]*1.08f,ya,-.029f),front(wx[k],wy[k],-.029f),front(wx[next],wy[next],-.029f),front(wx[next]*1.08f,yb,-.029f),dark);}
 const float gy[]={1.27f,1.35f,1.68f,2.01f,2.09f},gw[]={.62f,.70f,.675f,.65f,.58f};
 for(int row=0;row<4;++row)for(int col=0;col<8;++col){
  float u0=col/8.f,u1=(col+1)/8.f,v0=(gy[row]-1.27f)/.82f,v1=(gy[row+1]-1.27f)/.82f;
  P p[]={front((u0*2-1)*gw[row],gy[row],-.029f),front((u0*2-1)*gw[row+1],gy[row+1],-.029f),front((u1*2-1)*gw[row+1],gy[row+1],-.029f),front((u1*2-1)*gw[row],gy[row],-.029f)};
  float us[]={u0,u0,u1,u1},vs[]={v0,v1,v1,v0};
  for(int k:{0,1,2,0,2,3}){float v=vs[k];C colr=shade({.10f+.10f*v,.29f+.22f*v,.39f+.25f*v},{p[k].x*.40f,.24f,-1});vertex_uv(m,p[k],colr,4.f/1024+us[k]*(.25f-8.f/1024),.5f+4.f/1024+(1-v)*(.25f-8.f/1024));}
 }
 // Broad sky reflection curves with the windshield. Its quiet lower edge
 // leaves most of the glass dark enough to read as a recessed volume.
 for(int k=0;k<8;++k){float xa=-.60f+k*.145f,xb=xa+.145f,ta=(xa+.60f)/1.16f,tb=(xb+.60f)/1.16f;
  float ya=1.88f+.07f*ta,yb=1.88f+.07f*tb;
  gradient_quad(m,front(xa,ya-.10f,-.037f),front(xa,2.025f,-.037f),front(xb,2.025f,-.037f),front(xb,yb-.10f,-.037f),{.29f,.57f,.64f},{.72f,.88f,.88f});
 }
 quad(m,front(-.018f,1.28f,-.042f),front(-.018f,2.09f,-.042f),front(.018f,2.09f,-.042f),front(.018f,1.28f,-.042f),ivory);
 uvquad(m,front(-.33f,2.18f),front(-.33f,2.31f),front(.33f,2.31f),front(.33f,2.18f),dark,7);
 for(float dx:{-.2f,0.f,.2f})quad(m,front(dx-.042f,2.21f,-.018f),front(dx-.042f,2.27f,-.018f),front(dx+.042f,2.27f,-.018f),front(dx+.042f,2.21f,-.018f),GOLD);
 quad(m,front(-.78f,.93f),front(-.77f,1.03f),front(.77f,.85f),front(.78f,.75f),ivory);
 for(float x:{-.61f,.61f}){rounded_box(m,x,.63f,.003f,.31f,.20f,.055f,dark,.055f);rounded_box(m,x,.67f,0,.22f,.10f,.012f,{1.f,.96f,.72f},.035f);}
 for(float sign:{-1.f,1.f}){
  float x=sign*.936f;
  for(float z:{.48f,1.78f,4.30f,5.57f}){
   uvquad(m,{x,1.19f,z},{x,1.99f,z+.025f},{x,1.99f,z+1.01f},{x,1.19f,z+1.05f},glass,8,sign<0);
   uvquad(m,{x+sign*.002f,1.73f,z+.07f},{x+sign*.002f,1.93f,z+.07f},{x+sign*.002f,1.93f,z+.94f},{x+sign*.002f,1.80f,z+.75f},{.58f,.78f,.79f},8,sign<0);
  }
  // Narrow trim is flat geometry, so it does not turn the side into a stack of blocks.
  for(float y:{.97f,2.06f})uvquad(m,{x+sign*.003f,y,.42f},{x+sign*.003f,y+.055f,.42f},{x+sign*.003f,y+.055f,6.78f},{x+sign*.003f,y,6.78f},ivory,7,sign<0);
  float inset=sign*.886f;
  uvquad(m,{inset,.64f,3.f},{inset,2.12f,3.f},{inset,2.12f,4.10f},{inset,.64f,4.10f},tint(body,.68f),7,sign<0);
  for(float z:{3.04f,3.57f}){
   uvquad(m,{inset+sign*.004f,.68f,z},{inset+sign*.004f,2.07f,z},{inset+sign*.004f,2.07f,z+.49f},{inset+sign*.004f,.68f,z+.49f},tint(body,1.05f),7,sign<0);
   uvquad(m,{inset+sign*.008f,1.30f,z+.065f},{inset+sign*.008f,1.96f,z+.065f},{inset+sign*.008f,1.96f,z+.425f},{inset+sign*.008f,1.30f,z+.425f},glass,8,sign<0);
  }
  for(float z:{3.f,4.10f})quad(m,{inset,.64f,z},{inset,2.12f,z},{x,2.12f,z},{x,.64f,z},tint(body,.60f));
  quad(m,{x,.64f,3.f},{inset,.64f,3.f},{inset,.64f,4.10f},{x,.64f,4.10f},ivory);
  // Circular wheels, inset beneath the skirt with visible concentric hubs.
  for(float z:{1.01f,1.65f,5.62f,6.26f}){float cy=.29f,r=.245f;
   for(int k=0;k<12;++k){float a=k*2*PI/12,b=(k+1)*2*PI/12;
    P p{sign*.915f,cy+r*std::cos(a),z+r*std::sin(a)},q{sign*.915f,cy+r*std::cos(b),z+r*std::sin(b)};
    vertex(m,{sign*.917f,cy,z},dark);vertex(m,p,dark);vertex(m,q,dark);
    P h{sign*.919f,cy+.105f*std::cos(a),z+.105f*std::sin(a)},hh{sign*.919f,cy+.105f*std::cos(b),z+.105f*std::sin(b)};
    vertex(m,{sign*.921f,cy,z},{.48f,.53f,.51f});vertex(m,h,{.48f,.53f,.51f});vertex(m,hh,{.48f,.53f,.51f});
   }
  }
 }
 // Full-width top remains at y=2.49. Paint makes the safe route and real gaps legible.
 uvquad(m,{-.78f,2.491f,.40f},{-.78f,2.491f,6.80f},{.78f,2.491f,6.80f},{.78f,2.491f,.40f},{.32f,.55f,.54f},7);
 for(float x:{-.74f,.74f})quad(m,{x-.018f,2.494f,.38f},{x-.018f,2.494f,6.82f},{x+.018f,2.494f,6.82f},{x+.018f,2.494f,.38f},GOLD);
 for(float z:{.36f,6.67f}){
  quad(m,{-.80f,2.496f,z},{-.80f,2.496f,z+.16f},{.80f,2.496f,z+.16f},{.80f,2.496f,z},ivory);
  for(int j=0;j<5;++j){float x=-.75f+j*.32f;quad(m,{x,2.497f,z},{x+.09f,2.497f,z+.16f},{x+.21f,2.497f,z+.16f},{x+.12f,2.497f,z},body);}
 }
 uvquad(m,{-.43f,2.494f,2.55f},{-.43f,2.494f,4.25f},{.43f,2.494f,4.25f},{.43f,2.494f,2.55f},{.36f,.53f,.53f},7);
 for(float z:{1.5f,3.6f,5.7f})quad(m,{-.77f,2.495f,z},{-.77f,2.495f,z+.022f},{.77f,2.495f,z+.022f},{.77f,2.495f,z},tint(ivory,.7f));
 for(float x:{-.62f,.62f}){quad(m,{x-.11f,.75f,7.198f},{x-.11f,.87f,7.198f},{x+.11f,.87f,7.198f},{x+.11f,.75f,7.198f},CORAL);}
 return m;
}

Mesh make_barrier(){
 Mesh m;for(float x:{-.73f,.73f}){box(m,x,.05f,-.12f,.15f,.73f,.23f,NAVY);box(m,x,.04f,-.24f,.44f,.08f,.65f,{.29f,.38f,.44f});}
 box(m,0,.36f,0,1.91f,.43f,.22f,CREAM);
 for(int i=0;i<5;++i){float x=-.95f+i*.42f;quad(m,{x,.37f,-.008f},{x+.26f,.79f,-.008f},{x+.47f,.79f,-.008f},{x+.21f,.37f,-.008f},CORAL);}
 return m;
}
Mesh make_arch(){
 Mesh m;for(float x:{-.92f,.92f}){box(m,x,0,0,.18f,2.0f,.24f,{.23f,.39f,.47f});box(m,x,.15f,-.015f,.21f,.13f,.27f,CORAL);}
 box(m,0,1.35f,0,1.86f,.49f,.3f,CORAL);box(m,0,1.78f,-.015f,1.97f,.09f,.33f,CREAM);
 for(float x:{-.65f,0.f,.65f})quad(m,{x-.16f,1.74f,-.013f},{x,1.47f,-.013f},{x+.16f,1.74f,-.013f},{x+.06f,1.74f,-.013f},CREAM);
 return m;
}
Mesh make_coin(){
 Mesh m;for(int i=0;i<16;++i){float a=i*2*PI/16,b=(i+1)*2*PI/16;float r=.28f;P p{r*std::cos(a),r*std::sin(a),-.065f},q{r*std::cos(b),r*std::sin(b),-.065f};C c=GOLD;vertex(m,{0,0,-.066f},c);vertex(m,q,c);vertex(m,p,c);quad(m,p,q,{q.x,q.y,.065f},{p.x,p.y,.065f},{.91f,.47f,.06f});vertex(m,{0,0,.066f},c);vertex(m,{p.x,p.y,.066f},c);vertex(m,{q.x,q.y,.066f},c);}
 box(m,0,-.064f,-.079f,.13f,.13f,.014f,{.68f,.34f,.045f});box(m,0,-.064f,.066f,.13f,.13f,.014f,{1.f,.94f,.44f});return m;
}

void emblem(Mesh&m,const std::vector<P>&outline,C front,C side,float depth=.15f){
 P centre{0,0,-depth};for(const auto&p:outline){centre.x+=p.x;centre.y+=p.y;}centre.x/=outline.size();centre.y/=outline.size();
 for(size_t i=0;i<outline.size();++i){P a=outline[i],b=outline[(i+1)%outline.size()];a.z=-depth;b.z=-depth;
  vertex(m,centre,front);vertex(m,b,front);vertex(m,a,front);
  quad(m,a,b,{b.x,b.y,depth},{a.x,a.y,depth},side);
  vertex(m,{centre.x,centre.y,depth},front);vertex(m,{a.x,a.y,depth},front);vertex(m,{b.x,b.y,depth},front);
 }
}
void cylinder_y(Mesh&m,float x,float y,float z,float radius,float height,C color,int sides=10){
 for(int j=0;j<sides;++j){float a=j*2*PI/sides,b=(j+1)*2*PI/sides;
  P p{x+radius*std::cos(a),y,z+radius*std::sin(a)},q{x+radius*std::cos(b),y,z+radius*std::sin(b)};
  quad(m,p,{p.x,y+height,p.z},{q.x,y+height,q.z},q,color);
  vertex(m,{x,y+height,z},tint(color,1.2f));vertex(m,{p.x,y+height,p.z},tint(color,1.2f));vertex(m,{q.x,y+height,q.z},tint(color,1.2f));
 }
}
Mesh make_powerup(int kind){
 Mesh m;
 if(kind==4){
  emblem(m,{{-.37f,.27f,0},{0,.44f,0},{.37f,.27f,0},{.31f,-.18f,0},{0,-.47f,0},{-.31f,-.18f,0}},GOLD,{.59f,.36f,.07f},.13f);
  Mesh inner;emblem(inner,{{-.29f,.21f,0},{0,.34f,0},{.29f,.21f,0},{.23f,-.13f,0},{0,-.35f,0},{-.23f,-.13f,0}},{.20f,.77f,.77f},TEAL,.025f);copy(m,inner,0,0,-.16f);
  box(m,0,-.16f,-.195f,.075f,.42f,.02f,CREAM);box(m,0,-.025f,-.195f,.30f,.07f,.02f,CREAM);
 }else if(kind==5){
  // A real U silhouette, readable from all approaches; two distinct pole tips.
  for(int k=0;k<8;++k){float a=PI+k*PI/8,b=PI+(k+1)*PI/8;
   float ro=.39f,ri=.20f;quad(m,{ro*std::cos(a),ro*std::sin(a),-.14f},{ri*std::cos(a),ri*std::sin(a),-.14f},{ri*std::cos(b),ri*std::sin(b),-.14f},{ro*std::cos(b),ro*std::sin(b),-.14f},CORAL);
   quad(m,{ro*std::cos(a),ro*std::sin(a),.14f},{ro*std::cos(b),ro*std::sin(b),.14f},{ri*std::cos(b),ri*std::sin(b),.14f},{ri*std::cos(a),ri*std::sin(a),.14f},CORAL);
   quad(m,{ro*std::cos(a),ro*std::sin(a),-.14f},{ro*std::cos(b),ro*std::sin(b),-.14f},{ro*std::cos(b),ro*std::sin(b),.14f},{ro*std::cos(a),ro*std::sin(a),.14f},tint(CORAL,.72f));
  }
  for(float x:{-.295f,.295f}){box(m,x,0,-.14f,.19f,.25f,.28f,CORAL);box(m,x,.25f,-.14f,.19f,.16f,.28f,CREAM);box(m,x,.38f,-.145f,.20f,.04f,.29f,GOLD);}
 }else if(kind==7){
  rounded_box(m,0,-.23f,-.12f,.42f,.57f,.30f,TEAL,.05f);
  for(float x:{-.27f,.27f}){cylinder_y(m,x,-.30f,0,.14f,.64f,CREAM,8);cylinder_y(m,x,-.34f,0,.11f,.10f,CORAL,8);cylinder_y(m,x,.22f,0,.146f,.08f,GOLD,8);}
  box(m,0,-.08f,-.16f,.21f,.26f,.035f,NAVY);box(m,0,-.02f,-.20f,.10f,.13f,.035f,GOLD);
  for(float s:{-1.f,1.f}){quad(m,{s*.34f,.13f,0},{s*.64f,.30f,.05f},{s*.48f,-.20f,.05f},{s*.34f,-.25f,0},TEAL);}
 }else if(kind==8){
  for(float s:{-1.f,1.f}){float x=s*.23f;
   rounded_box(m,x,-.28f,-.27f,.34f,.25f,.54f,{.21f,.66f,.72f},.055f);
   box(m,x,-.30f,-.29f,.36f,.06f,.58f,CREAM);
   rounded_box(m,x,-.06f,-.01f,.27f,.35f,.25f,TEAL,.04f);
   for(float y:{.01f,.11f})box(m,x,y,-.033f,.25f,.045f,.025f,GOLD);
   for(int k=0;k<3;++k){float yy=-.02f+k*.10f;
    quad(m,{x+s*.12f,yy,-.06f},{x+s*(.33f+.05f*k),yy+.18f,.03f},{x+s*(.28f+.05f*k),yy+.055f,.03f},{x+s*.12f,yy-.06f,-.06f},CREAM);}
  }
 }else if(kind==9){
  Mesh coin=make_coin();for(auto&v:coin){v.x*=1.08f;v.y*=1.08f;}
  copy(m,coin,-.16f,.10f,.08f,-.18f);copy(m,coin,.16f,-.10f,-.08f,.18f);
  // Double reward distinguished by a paired-coin silhouette and jade central ribbon.
  box(m,0,-.055f,-.21f,.58f,.11f,.035f,TEAL);
  for(float x:{-.12f,.12f})box(m,x,-.035f,-.25f,.065f,.07f,.025f,CREAM);
 }
 return m;
}

// Architectural silhouettes are built once; no scene geometry is rebuilt at runtime.
void roof(Mesh&m,float x,float y,float z,float w,float d,C c,int lod=0,float rise=.70f,float corner=.18f,float wall_width=0,float wall_depth=0){
 // Main roof and narrow arcade canopy share the same curve but have separately
 // scaled rise. Only real walls receive gables; an overhang is a thin dark fascia.
 int bands=lod>=3?2:lod>=2?3:5,across=lod>=2?4:8;
 auto point=[&](int side,float u,float t,float sink=0.f){
  float end=std::pow(std::abs(u),6.f),height=.035f-rise*.19f*std::sin(t*PI)+rise*std::pow(t,1.48f);
  float lift=end*(corner*std::pow(1-t,1.4f)+.025f*t);
  return P{x+u*w*.5f,y+height+lift-sink,side?z+d-t*d*.5f:z+t*d*.5f};
 };
 for(int side=0;side<2;++side)for(int j=0;j<bands;++j)for(int i=0;i<across;++i){
  float u=-1.f+2.f*i/across,v=-1.f+2.f*(i+1)/across,t=float(j)/bands,tt=float(j+1)/bands;
  uvregion(m,point(side,u,t),point(side,u,tt),point(side,v,tt),point(side,v,t),tint(c,1.08f+.05f*t),3,(u+1)*.5f,t,(v+1)*.5f,tt,side==1);
 }
 for(int side=0;side<2;++side)for(int i=0;i<across;++i){float u=-1.f+2.f*i/across,v=-1.f+2.f*(i+1)/across;
  uvregion(m,point(side,u,0,.08f),point(side,u,0),point(side,v,0),point(side,v,0,.08f),tint(c,.71f),4,(u+1)*.5f,0,(v+1)*.5f,1);
  uvregion(m,point(side,u,0,.08f),point(side,u,.12f,.10f),point(side,v,.12f,.10f),point(side,v,0,.08f),tint(c,.62f),2,(u+1)*.5f,0,(v+1)*.5f,1);
 }
 // End fascias follow the roof with an 8cm thickness, never a pale outer triangle.
 for(float u:{-1.f,1.f})for(int side=0;side<2;++side)for(int j=0;j<bands;++j){float t=float(j)/bands,tt=float(j+1)/bands;P a=point(side,u,t),b=point(side,u,tt);
  uvregion(m,{a.x,a.y-.08f,a.z},a,b,{b.x,b.y-.08f,b.z},tint(c,.80f),4,0,t,1,tt);
 }
 // Continuous closed gables sit on the actual masonry ends. Their front/back
 // margins begin at the true walls, so the eaves remain visibly unsupported.
 if(wall_width>0){
  float u=std::min(1.f,wall_width/w),tmin=wall_depth>0?std::max(0.f,(d-wall_depth)/d):0.f;
  for(float end:{-u,u})for(int side=0;side<2;++side)for(int j=0;j<bands;++j){
   float t=tmin+(1-tmin)*j/bands,tt=tmin+(1-tmin)*(j+1)/bands;P a=point(side,end,t),b=point(side,end,tt);
   // Share exact wall coordinates; avoid an arithmetic-rounding sliver.
   a.x=b.x=x+(end<0?-wall_width*.5f:wall_width*.5f);
   uvregion(m,{a.x,y-.12f,a.z},a,b,{b.x,y-.12f,b.z},tint(CREAM,.87f),1,t,1,tt,0);
  }
 }
 int segments=lod>=2?4:8;
 for(int j=0;j<segments;++j){float u=-1.f+2.f*j/segments,v=-1.f+2.f*(j+1)/segments;
  float ya=y+.035f+rise+.025f*std::pow(std::abs(u),6.f),yb=y+.035f+rise+.025f*std::pow(std::abs(v),6.f);
  for(int k=0;k<4;++k){float a=k*PI/4,b=(k+1)*PI/4;
   uvregion(m,{x+u*w*.5f,ya+.055f*std::sin(a),z+d*.5f+.065f*std::cos(a)},{x+v*w*.5f,yb+.055f*std::sin(a),z+d*.5f+.065f*std::cos(a)},{x+v*w*.5f,yb+.055f*std::sin(b),z+d*.5f+.065f*std::cos(b)},{x+u*w*.5f,ya+.055f*std::sin(b),z+d*.5f+.065f*std::cos(b)},tint(c,1.18f),4,(u+1)*.5f,float(k)/4,(v+1)*.5f,float(k+1)/4);
  }
 }
}

void lantern(Mesh&m,float x,float y,float z,float scale=1,int lod=0){
 // Curved paper shell with narrow timber caps: no square luminous panel.
 // Far silhouettes retain the same outer dimensions and paper material.
 // Small cap/tassel facets are not visible at those distances.
 const int rings=lod>=3?3:lod>=2?4:6,sides=lod>=2?8:12;
 auto point=[&](int j,int k){float a=j*PI/rings,b=k*2*PI/sides;return P{x+.25f*scale*std::sin(a)*std::cos(b),y+.34f*scale*std::cos(a),z+.25f*scale*std::sin(a)*std::sin(b)};};
 for(int j=0;j<rings;++j)for(int k=0;k<sides;++k){
  const int rows[]={j,j+1,j+1,j},cols[]={k,k,k+1,k+1};
  for(int ix:{0,3,2,0,2,1}){int row=rows[ix],col=cols[ix];float a=row*PI/rings,b=col*2*PI/sides;
   P p=point(row,col),normal{std::sin(a)*std::cos(b)/.25f,std::cos(a)/.34f,std::sin(a)*std::sin(b)/.25f};
   C paper{1.f,.80f,.43f},lit=shade(paper,normal);lit.r=.40f*paper.r+.60f*lit.r;lit.g=.40f*paper.g+.60f*lit.g;lit.b=.40f*paper.b+.60f*lit.b;
   vertex_uv(m,p,lit,.75f+4.f/1024+float(col)/sides*(.25f-8.f/1024),.5f+4.f/1024+float(row)/rings*(.25f-8.f/1024));
  }
 }
 cylinder_y(m,x,y-.36f*scale,z,.145f*scale,.065f*scale,CORAL,lod>=2?6:10);
 cylinder_y(m,x,y+.29f*scale,z,.145f*scale,.065f*scale,CORAL,lod>=2?6:10);
 if(lod<3)cylinder_y(m,x,y-.57f*scale,z,.018f*scale,.21f*scale,GOLD,lod>=2?4:6);
}

// Isolated original street art study. Construction-time work only.
void quiet_materials(Mesh&m){
 for(auto&v:m){if(v.u==0&&v.v==0)continue;int tile=int(v.u*4)+4*int(v.v*4);
  if(tile==1){v.r*=.90f;v.g*=.90f;v.b*=.85f;v.u=v.v=0;}
  else if(tile==3||tile==4){float localv=v.v*4-std::floor(v.v*4),band=.98f+.025f*std::cos(localv*4*PI);v.r*=.84f*band;v.g*=.84f*band;v.b*=.81f*band;v.u=v.v=0;}
 }
}
void shop_materials(Mesh&m,size_t begin,int style){
 // Some shops have quiet blue glazing or an open dark interior, not identical
 // glowing lattice panels. Keep remaining shops' authored lattice texture.
 if(style%3==0||style==2)return;
 for(size_t i=begin;i<m.size();++i){auto&v=m[i];int tile=int(v.u*4)+4*int(v.v*4);if(tile!=12)continue;
  float level=1.f-(v.v*4-std::floor(v.v*4));
  if(style%3==1){v.r*=.18f+.55f*level;v.g*=(.34f+.60f*level)/.85f;v.b*=(.45f+.50f*level)/.52f;}
  else {v.r*=.25f;v.g*=.34f;v.b*=.43f;}
  v.u=v.v=0;
 }
}
void broad_shop_awning(Mesh&m,float face,float z,float side,int style,int lod){
 if(lod>=3||(style!=1&&style!=4))return;
 float outside=face+side*std::min(.58f,std::max(0.f,std::abs(face)-4.94f));
 C fabric=style==1?C{.86f,.31f,.22f}:C{.20f,.48f,.54f};
 // Four broad cloth panels make one architectural-scale shape, not tiny props.
 for(int k=0;k<4;++k){float a=z+.35f+k*.96f,b=a+.96f;C c=k%2?C{.96f,.87f,.64f}:fabric;
  quad(m,{face,2.63f,a},{face,2.63f,b},{outside,2.27f,b},{outside,2.27f,a},c,side<0);
  gradient_quad(m,{outside,2.10f,a},{outside,2.27f,a},{outside,2.27f,b},{outside,2.10f,b},tint(c,.71f),c,side<0);
 }
 if(lod<2){box(m,face-side*.10f,.36f,z+.55f,.43f,.55f,3.25f,{.39f,.31f,.24f});box(m,face-side*.10f,.91f,z+.50f,.49f,.09f,3.35f,{.86f,.75f,.53f});}
}
void pocket_court(Mesh&m,float side,float z,int lod){
 float face=side*5.65f;
 // One quiet setback court per 72m; it replaces a whole repeated shop.
 wall_bands(m,{face,.24f,z},{face,.24f,z+4.8f},1.45f,{.87f,.86f,.70f},1,side>0);
 box(m,face,1.45f,z,.24f,.13f,4.8f,{.24f,.45f,.44f});
 box(m,face+side*.025f,.24f,z+1.40f,.08f,1.25f,1.35f,{.29f,.38f,.34f});
 if(lod<2){
  box(m,face-side*.40f,.65f,z+.28f,.52f,.12f,1.0f,{.54f,.36f,.23f});
  for(float zz:{z+.36f,z+1.12f})box(m,face-side*.40f,.24f,zz,.12f,.42f,.12f,{.32f,.29f,.24f});
 }
}

void building(Mesh&m,float x,float z,int style,int lod,int district){
 size_t material_begin=m.size();
 float w=3.65f+(style%3)*.28f,d=4.6f,h=style%3==0?2.70f:style%3==1?4.10f:3.48f;
 if(district)h+=.55f;
 C plaster=style%3==0?C{1.f,.93f,.76f}:style%3==1?C{.77f,.91f,.79f}:C{.97f,.70f,.48f};
 C wood{.37f,.28f,.22f},ink{.27f,.51f,.54f},glow{1.f,.85f,.52f};
 bool featured=(style+(x>0?2:0))%6==1;
 float l=x-w*.5f,r=x+w*.5f,side=x<0?1.f:-1.f,face=x+side*w*.5f,inset=face-side*.46f;
 float porch=std::min(2.38f,h-.22f),sill=.29f;
 const float main_roof_y=h+.15f,upper_wall_top=main_roof_y-.12f;
 // Continuous stone plinth meets the district terrace. Its broad dark riser
 // grounds the masonry without a translucent shadow sheet under every house.
 box(m,x,.105f,z-.08f,w+.20f,.165f,d+.16f,{.46f,.52f,.47f},true,false);
 uvquad(m,{l-.10f,.105f,z-.08f},{l-.10f,.27f,z-.08f},{r+.10f,.27f,z-.08f},{r+.10f,.105f,z-.08f},{.59f,.62f,.53f},5);
 // Warm plaster broad planes carry material scale and baked ambient shading.
 wall_bands(m,{l,sill,z},{r,sill,z},h+.16f,plaster,1);
 uvquad(m,{l,.17f,z-.005f},{l,.43f,z-.005f},{r,.43f,z-.005f},{r,.17f,z-.005f},{.56f,.57f,.49f},5);
 // Actual track-facing lower porch is recessed by 46cm, with visible returns.
 wall_bands(m,{inset,sill,z},{inset,sill,z+d},porch,tint(plaster,.79f),1,side<0);
  uvcolors(m,{face,sill,z},{face,sill,z+d},{inset,sill,z+d},{inset,sill,z},{.80f,.78f,.64f},{.80f,.78f,.64f},{.40f,.46f,.42f},{.40f,.46f,.42f},5,side>0);
  uvquad(m,{inset,porch,z},{inset,porch,z+d},{face,porch,z+d},{face,porch,z},tint(wood,.62f),2,side>0);
 // The full front wall already owns this entire porch return rectangle.
 // Do not draw a second, differently shaded face over the same plane.
 uvquad(m,{inset,sill,z+d},{inset,porch,z+d},{face,porch,z+d},{face,sill,z+d},tint(plaster,.86f),1,side>0);
 // The gable owns everything above this exact shared horizontal edge.
 if(h>porch+.25f)wall_bands(m,{face,porch,z},{face,porch,z+d},upper_wall_top,plaster,1,side<0);
 roof(m,x,main_roof_y,z-.42f,w+.87f,d+.84f,ink,lod,.70f,.18f,w,d);
 if(featured){
  // One featured building per six variants, offset between the two street sides.
  // Offset smaller upper room gives a stepped shop-house silhouette.
  float wingx=x-side*.46f,ww=w*.71f,yy=h+.18f;
  uvgradient(m,{wingx-ww*.5f,yy,z+1.10f},{wingx-ww*.5f,yy+1.24f,z+1.10f},{wingx+ww*.5f,yy+1.24f,z+1.10f},{wingx+ww*.5f,yy,z+1.10f},tint(plaster,.82f),plaster,1);
  roof(m,wingx,yy+1.27f,z+.84f,ww+.60f,2.72f,ink,lod,.67f,.16f,ww,2.20f);
 }
 if(lod>=3){shop_materials(m,material_begin,style);return;}
 if(district)broad_shop_awning(m,face,z,side,style,lod);
 // Three tapered round columns frame a deep open arcade, not a flat facade.
 for(float zz:{.12f,2.30f,4.48f}){
  cylinder_y(m,face,.30f,z+zz,.085f,porch-.26f,wood,lod==0?10:6);
  cylinder_y(m,face,.23f,z+zz,.13f,.17f,{.62f,.60f,.51f},8);
 }
 // A single connected lintel and a light masonry cornice frame each arcade.
 box(m,face,porch-.13f,z,.15f,.18f,d,wood);
 box(m,x,h-.13f,z-.075f,w+.14f,.13f,.14f,tint(plaster,.92f));
 box(m,face,h-.13f,z,.14f,.13f,d,tint(plaster,.92f));
 // Two broad groups of lattice doors and windows; warm interiors stay behind pillars.
 for(float zz:{.49f,2.66f}){
  float z0=z+zz,z1=z0+1.36f;
  uvquad(m,{inset+side*.006f,.42f,z0},{inset+side*.006f,2.10f,z0},{inset+side*.006f,2.10f,z1},{inset+side*.006f,.42f,z1},wood,2,side<0);
  uvquad(m,{inset+side*.012f,.94f,z0+.09f},{inset+side*.012f,2.01f,z0+.09f},{inset+side*.012f,2.01f,z1-.09f},{inset+side*.012f,.94f,z1-.09f},featured?C{.22f,.37f,.37f}:glow,12,side<0);
  uvquad(m,{inset+side*.018f,.42f,z0+.09f},{inset+side*.018f,.89f,z0+.09f},{inset+side*.018f,.89f,z1-.09f},{inset+side*.018f,.42f,z1-.09f},tint(wood,1.35f),2,side<0);
  if(featured&&lod<2){
   float counter=inset+side*.18f;C cloth=style%2?C{.78f,.25f,.17f}:C{.15f,.55f,.49f};
   box(m,counter,.51f,z0,.34f,.38f,1.36f,tint(wood,1.30f));
   box(m,counter,.89f,z0-.035f,.39f,.07f,1.43f,{.86f,.79f,.60f});
   // Three simple cloth drops and one broad shop mark form a recognizable
   // shop front; display objects are reserved for the nearest LOD.
   for(int panel=0;panel<3;++panel){float za=z0+.06f+panel*.42f,zb=za+.38f;
    uvquad(m,{face+side*.04f,1.72f,za},{face+side*.04f,2.18f,za},{face+side*.04f,2.18f,zb},{face+side*.04f,1.72f,zb},cloth,6,side<0);
   }
   float markx=face+side*.046f,markz=z0+.68f;
   quad(m,{markx,1.82f,markz},{markx,1.96f,markz-.10f},{markx,2.10f,markz},{markx,1.96f,markz+.10f},CREAM,side<0);
   if(lod==0){
    cylinder_y(m,counter,.96f,z0+.24f,.12f,.28f,{.65f,.32f,.18f},10);
    cylinder_y(m,counter,1.24f,z0+.24f,.075f,.045f,CREAM,10);
    rounded_box(m,counter,.96f,z0+.70f,.23f,.21f,.28f,{.80f,.65f,.35f},.035f);
   }
  }
 }
 // Front elevation receives a grouped doorway; no wall-sized checkerboard grid.
 uvquad(m,{x-.52f,.33f,z-.012f},{x-.52f,1.98f,z-.012f},{x+.52f,1.98f,z-.012f},{x+.52f,.33f,z-.012f},wood,2);
 uvquad(m,{x-.45f,1.00f,z-.018f},{x-.45f,1.90f,z-.018f},{x+.45f,1.90f,z-.018f},{x+.45f,1.00f,z-.018f},glow,12);
 for(float dx:{-1.22f,1.22f}){
  float xx=x+dx;
  uvquad(m,{xx-.37f,1.04f,z-.012f},{xx-.37f,1.96f,z-.012f},{xx+.37f,1.96f,z-.012f},{xx+.37f,1.04f,z-.012f},wood,2);
  uvquad(m,{xx-.31f,1.11f,z-.019f},{xx-.31f,1.89f,z-.019f},{xx+.31f,1.89f,z-.019f},{xx+.31f,1.11f,z-.019f},glow,12);
 }
 if(lod>=2){shop_materials(m,material_begin,style);return;}
 // Thin curved secondary canopy and indigo cloth reinforce the market arcade.
 if(style%3==2&&!featured){
  roof(m,face+side*.25f,porch+.04f,z-.11f,1.48f,d+.22f,ink,lod+1,.31f,.10f);
  for(float zz:{1.07f,3.45f})lantern(m,face+side*.26f,porch-.31f,z+zz,.60f,lod);
  if(style%3==2){float xx=face+side*.31f;uvquad(m,{xx,1.27f,z+3.04f},{xx,2.14f,z+3.04f},{xx,2.14f,z+3.71f},{xx,1.34f,z+3.71f},{.32f,.56f,.60f},6,side<0);}
 }
 if(h>3.6f){
  for(float zz:{.68f,2.72f}){
   uvquad(m,{face+side*.008f,2.82f,z+zz},{face+side*.008f,3.70f,z+zz},{face+side*.008f,3.70f,z+zz+1.14f},{face+side*.008f,2.82f,z+zz+1.14f},wood,2,side<0);
   uvquad(m,{face+side*.013f,2.91f,z+zz+.08f},{face+side*.013f,3.62f,z+zz+.08f},{face+side*.013f,3.62f,z+zz+1.06f},{face+side*.013f,2.91f,z+zz+1.06f},tint(glow,.94f),12,side<0);
  }
 }
 if(lod==0){
  // Stone thresholds, ceramic planters and soft foliage are sparse near props.
  rounded_box(m,x,.21f,z-.31f,1.26f,.12f,.37f,{.67f,.65f,.54f},.045f);
  if(featured){float px=face+side*.12f,pz=z+4.16f;
   cylinder_y(m,px,.28f,pz,.20f,.35f,{.46f,.62f,.55f},12);
   sphere(m,{px,.81f,pz},{.31f,.38f,.31f},{.40f,.56f,.40f},4,8);
   sphere(m,{px+side*.19f,.88f,pz+.12f},{.22f,.26f,.23f},{.48f,.64f,.44f},3,8);}
 }
 shop_materials(m,material_begin,style);
}

void district_terrace(Mesh&m,int district){
 // The skyline used to show through the missing ground around every building.
 // These connected terraces lie entirely outside the three playable lanes.
 // Metre-scale paving uses mirrored repeats, continuous at 12m chunk seams.
 float inner=district==0?8.40f:district==1?4.82f:6.90f;
 C stone=district==2?C{.67f,.73f,.65f}:C{.76f,.76f,.63f};
 for(float side:{-1.f,1.f}){
  // Fine paving is useful only beside the first row. The courtyard behind it
  // is a broad baked colour surface rather than 24m of hidden texture samples.
  for(int strip=0;strip<1;++strip){float x0=inner+strip*4.8f,x1=x0+4.8f,u0=strip%2?1.f:0.f,u1=1-u0;
   for(int part=0;part<2;++part){float z0=part*6.f,z1=z0+6.f,v0=part?1.f:0.f,v1=1-v0;
    uvregion(m,{side*x0,.105f,z0},{side*x0,.105f,z1},{side*x1,.105f,z1},{side*x1,.105f,z0},stone,5,u0,v0,u1,v1,side<0);
   }
  }
  gradient_quad(m,{side*(inner+4.8f),.105f,0},{side*(inner+4.8f),.105f,12},{side*(inner+24.f),.105f,12},{side*(inner+24.f),.105f,0},tint(stone,.89f),tint(stone,1.01f),side<0);
  // A broad quiet outer garden closes the world to the horizon. It carries no
  // geometry detail, pickups or collision, and stays behind the architecture.
  float edge=inner+24.f;
  quad(m,{side*edge,.075f,0},{side*edge,.075f,12},{side*65.f,.075f,12},{side*65.f,.075f,0},{.43f,.58f,.48f},side<0);
  if(district==0){
   box(m,side*8.48f,-.10f,0,.17f,.29f,12,{.36f,.46f,.43f});
   box(m,side*8.48f,.19f,0,.25f,.10f,12,{.77f,.76f,.62f});
  }
 }
}

void skyline_roof(Mesh&m,float x,float y,float z,float w,float d,C color,float rise=.65f){
 // At this distance the broad curved silhouette matters, not individual tiles.
 auto p=[&](int side,float t,float end){return P{x+end*w*.5f,y+rise*std::pow(t,1.48f)-.10f*std::sin(t*PI)+.12f*std::pow(std::abs(end),6.f)*(1-t),side?z+d-t*d*.5f:z+t*d*.5f};};
 for(int side=0;side<2;++side)for(int band=0;band<3;++band){float a=band/3.f,b=(band+1)/3.f;
  quad(m,p(side,a,-1),p(side,b,-1),p(side,b,1),p(side,a,1),color,side==1);}
 for(float end:{-1.f,1.f})for(int side=0;side<2;++side)for(int band=0;band<3;++band){
  P a=p(side,band/3.f,end),b=p(side,(band+1)/3.f,end);
  quad(m,{a.x,y-.12f,a.z},a,b,{b.x,y-.12f,b.z},{.73f,.75f,.63f},end<0);}
 box(m,x,y+rise-.025f,z+d*.5f,w,.07f,.09f,tint(color,1.12f));
}
void rear_roofline(Mesh&m,float side,int style,int district){
 // Quiet second-row silhouettes give aerial views a city behind the shops.
 // These broad vertex-colour masses have neither windows nor texture fetches.
 for(int group=0;group<2;++group){
  float x=side*(district?14.2f:17.8f)+side*((style+group)%3)*.42f;
  float z=group*6.f+.45f,h=2.65f+((style+group*2)%4)*.44f;
  C plaster=group?C{.65f,.72f,.63f}:C{.76f,.75f,.62f};
  box(m,x,.11f,z,4.15f,h,5.25f,plaster,true,false);
  skyline_roof(m,x,h+.24f,z-.30f,4.88f,5.80f,{.36f,.53f,.51f});
 }
 if(!district&&style==0&&side>0){
  // One distant pavilion per 72m block, never an overhead gameplay obstacle.
  float x=20.4f,z=5.5f;
  for(int tier=0;tier<3;++tier){float y=.12f+tier*2.28f,w=4.0f-tier*.65f;
   box(m,x,y,z,w,2.03f,w,{.78f,.71f,.55f},true,false);
   skyline_roof(m,x,y+2.06f,z-.34f,w+.85f,w+.70f,{.25f,.44f,.44f},.62f);
   if(tier>0)for(float dx:{-.65f,.65f})box(m,x+dx,y+.67f,z-.009f,.24f,.90f,.018f,{.21f,.35f,.34f});
  }
 }
}

void canal_bridge(Mesh&m,float side,float z,int lod){
 // One humpback pedestrian bridge per 72m river block. The complete arch,
 // railing and shadow stay beyond x=4.94, outside the playable railway.
 int segments=lod<2?12:lod==2?8:6;C stone{.82f,.79f,.65f},dark{.43f,.52f,.47f};
 auto point=[&](float t,float zz,float lift=0.f){return P{side*(4.94f+3.46f*t),.24f+.62f*std::sin(t*PI)+lift,zz};};
 for(int k=0;k<segments;++k){float t=k/float(segments),tt=(k+1)/float(segments);
  P a=point(t,z),b=point(tt,z),c=point(tt,z+1.32f),d=point(t,z+1.32f);
  uvregion(m,a,b,c,d,stone,5,t,0,tt,1,side>0);
  for(float zz:{z,z+1.32f}){
   P p=point(t,zz),q=point(tt,zz);
   quad(m,{p.x,p.y-.21f,p.z},p,q,{q.x,q.y-.21f,q.z},dark,zz==z+1.32f);
   quad(m,{p.x,p.y+.06f,p.z},{p.x,p.y+.36f,p.z},{q.x,q.y+.36f,q.z},{q.x,q.y+.06f,q.z},stone,zz==z+1.32f);
   quad(m,{p.x,p.y+.36f,p.z-.035f},{p.x,p.y+.36f,p.z+.035f},{q.x,q.y+.36f,q.z+.035f},{q.x,q.y+.36f,q.z-.035f},CREAM,side<0);
  }
 }
 for(float t:{0.f,1.f})for(float zz:{z-.02f,z+1.34f}){P p=point(t,zz);box(m,p.x,.12f,p.z,.18f,.68f,.18f,stone);}
 quad(m,{side*4.99f,-.055f,z+.10f},{side*4.99f,-.055f,z+1.50f},{side*8.34f,-.055f,z+1.50f},{side*8.34f,-.055f,z+.10f},{.12f,.33f,.35f},side<0);
}

Mesh make_station_chunk(int style,int lod){
 Mesh m; C stone{.66f,.65f,.55f}, steel{.13f,.27f,.33f}, brick{.63f,.37f,.27f};
 box(m,0,-.22f,0,9.4f,.19f,12,{.29f,.34f,.35f});
 for(float x:{-2.4f,0.f,2.4f}){
  for(int part=0;part<4;++part){float z=part*3.f,v0=part%2?1.f:0.f,v1=1-v0;
   uvregion(m,{x-1.06f,.01f,z},{x-1.06f,.01f,z+3},{x+1.06f,.01f,z+3},{x+1.06f,.01f,z},{.33f,.40f,.42f},10,0,v0,1,v1);}
  for(float z=.2f;z<12;z+=(lod<2?1.5f:3.f))quad(m,{x-.95f,.035f,z},{x-.95f,.035f,z+.20f},{x+.95f,.035f,z+.20f},{x+.95f,.035f,z},{.44f,.39f,.31f});
  for(float dx:{-.64f,.64f})box(m,x+dx,.065f,0,.075f,.075f,12,{.73f,.80f,.77f});
 }
 for(float side:{-1.f,1.f}){
  box(m,side*5.35f,-.02f,0,3.20f,.46f,12,stone,false);
  // Broad stone surface has the same metre-scale material as the open street.
  for(float z=0;z<12;z+=3)uvquad(m,{side*3.75f,.44f,z},{side*3.75f,.44f,z+3},{side*6.95f,.44f,z+3},{side*6.95f,.44f,z},{.83f,.81f,.68f},5,side<0);
  quad(m,{side*3.82f,.445f,0},{side*3.82f,.445f,12},{side*4.02f,.445f,12},{side*4.02f,.445f,0},GOLD,side<0);
  for(float z=.45f;z<12;z+=1.5f)quad(m,{side*4.12f,.448f,z},{side*4.12f,.448f,z+.035f},{side*6.90f,.448f,z+.035f},{side*6.90f,.448f,z},{.47f,.50f,.47f},side<0);
  // Recessed bay wall, base band, and broad glazed panels establish a real concourse.
  box(m,side*7.0f,.42f,0,.28f,5.1f,12,brick);
  box(m,side*6.83f,.44f,0,.08f,.66f,12,steel);
  box(m,side*6.81f,4.65f,0,.12f,.24f,12,CREAM);
  for(float z:{.85f,4.85f,8.85f}){
   box(m,side*6.82f,1.39f,z,.025f,2.70f,2.60f,steel);
   float pane=side*6.775f,za=side>0?z+2.48f:z+.12f,zb=side>0?z+.12f:z+2.48f;
   gradient_quad(m,{pane,1.51f,za},{pane,3.94f,za},{pane,3.94f,zb},{pane,1.51f,zb},{.12f,.35f,.44f},{.73f,.90f,.84f});
   quad(m,{pane-side*.008f,3.34f,z+.26f},{pane-side*.008f,3.78f,z+.26f},{pane-side*.008f,3.78f,z+2.32f},{pane-side*.008f,3.53f,z+1.95f},{.65f,.79f,.74f},side>0);
   box(m,side*6.75f,1.51f,z+1.27f,.03f,2.43f,.055f,CREAM);
   box(m,side*6.75f,2.69f,z+.12f,.03f,.055f,2.36f,CREAM);
  }
  // Side canopy is outside playable lanes: no invisible overhead collision.
  float inner=side*3.86f,outer=side*7.16f;
  // Broad baked light gradient and sparse sheet seams give a metal roof depth.
  gradient_quad(m,{inner,3.74f,0},{outer,4.54f,0},{outer,4.54f,12},{inner,3.74f,12},{.13f,.36f,.40f},{.45f,.74f,.66f},side<0);
  if(lod<2)for(float zz:{2.f,5.f,8.f,11.f})quad(m,{inner,3.75f,zz},{outer,4.55f,zz},{outer,4.55f,zz+.045f},{inner,3.75f,zz+.045f},{.40f,.58f,.53f},side<0);
  box(m,side*3.92f,3.58f,0,.12f,.14f,12,{.16f,.29f,.32f});
  box(m,side*3.92f,3.72f,0,.12f,.08f,12,CREAM);
  for(float z:{.35f,6.35f}){
   box(m,side*4.56f,.44f,z,.17f,3.45f,.18f,steel);
   box(m,side*4.56f,.44f,z,.34f,.18f,.35f,CREAM);
   // A single diagonal gusset, not a dense truss mesh.
   quad(m,{side*4.56f,2.80f,z},{side*4.56f,3.00f,z},{side*5.50f,4.06f,z},{side*5.75f,4.12f,z},steel);
   if(lod<2){box(m,side*5.3f,3.42f,z+1.45f,1.7f,.13f,.22f,{1.f,.91f,.61f});}
  }
  if(lod<2){
   box(m,side*5.77f,.88f,3.00f,.62f,.14f,2.1f,{.48f,.31f,.21f});
   box(m,side*6.02f,1.0f,3.00f,.12f,.53f,2.1f,{.64f,.42f,.25f});
   for(float z:{3.17f,4.72f})box(m,side*5.77f,.44f,z,.48f,.44f,.12f,steel);
   if((style+(side>0?1:0))%3==0){
    box(m,side*5.98f,.44f,9.5f,.78f,1.21f,.55f,{.20f,.46f,.49f});
    box(m,side*5.98f,1.43f,9.48f,.58f,.09f,.60f,steel);
   }
   // Station board uses graphic line/dots, no per-frame text rasterization.
   box(m,side*5.55f,2.60f,6.50f,2.55f,.53f,.10f,steel);
   box(m,side*5.55f,2.83f,6.48f,1.90f,.045f,.02f,GOLD);
   for(float dx:{-.70f,0.f,.70f})box(m,side*5.55f+dx,2.75f,6.46f,.13f,.18f,.02f,CREAM);
  }
 }
 if(style%3==1){
  // Portal frame is 8m above track, safely above roof and boosted-jump envelope.
  for(float x:{-7.0f,7.0f})box(m,x,.4f,2.0f,.32f,8.2f,.36f,steel);
  box(m,0,8.35f,2.0f,14.3f,.24f,.36f,steel);
  quad(m,{-7.0f,8.5f,1.9f},{0,9.9f,1.9f},{0,10.08f,1.9f},{-7.0f,8.70f,1.9f},CREAM);
  quad(m,{0,9.9f,1.9f},{7.0f,8.5f,1.9f},{7.0f,8.70f,1.9f},{0,10.08f,1.9f},CREAM);
  for(float x:{-3.5f,0.f,3.5f})box(m,x,8.40f,2.0f,.09f,.65f,.12f,steel);
  // One clock per 72m station block; the other portal remains uncluttered.
  if(style==1){
  for(int k=0;k<32;++k){float a=k*2*PI/32,b=(k+1)*2*PI/32;
   vertex(m,{0,8.94f,1.76f},CREAM);vertex(m,{.49f*std::cos(a),8.94f+.49f*std::sin(a),1.76f},CREAM);vertex(m,{.49f*std::cos(b),8.94f+.49f*std::sin(b),1.76f},CREAM);}
  quad(m,{-.032f,8.91f,1.75f},{-.032f,9.29f,1.75f},{.032f,9.29f,1.75f},{.032f,8.91f,1.75f},steel);
  quad(m,{-.025f,8.91f,1.748f},{-.025f,8.97f,1.748f},{.29f,9.08f,1.748f},{.31f,9.02f,1.748f},steel);
  }
 }
 district_terrace(m,2);return m;
}

// Original construction-time shadow proxy study. No runtime shadow pass,
// GPU light attribute or texture. Proxies follow the actual shop placements.
struct LightCaster {float l,r,n,f,h;};
std::vector<LightCaster> light_casters(int style,int district){
 std::vector<LightCaster> out;
 auto house=[&](float x,float z,int variant){float w=3.65f+(variant%3)*.28f,h=variant%3==0?2.70f:variant%3==1?4.10f:3.48f;
  if(district)h+=.55f;out.push_back({x-w*.5f-.22f,x+w*.5f+.22f,z-.18f,z+4.78f,h+.45f});};
 for(int chunk=-1;chunk<=1;chunk++){int s=(style+chunk+6)%6;float offset=chunk*12.f;
  for(float side:{-1.f,1.f}){
   if(!district){house(side*(10.5f+(s%2)*.55f),offset+(side<0?.7f:2.65f),(s+(side<0?0:2))%6);
    if((s+(side<0?0:1))%2==0)house(side*10.9f,offset+7.f,(s+1)%6);}
   else{house(side*(7.35f+(s%3)*.28f),offset+(side<0?.25f:1.65f),(s+(side<0?0:2))%6);
    if(!(s==2&&side>0))house(side*(7.6f+((s+1)%3)*.17f),offset+(side<0?6.5f:7.05f),(s+(side<0?2:4))%6);}
  }
 }
 return out;
}
float light_visibility(P p,const std::vector<LightCaster>&casters){
 float visible=0;
 for(int ray=0;ray<5;ray++){float dx=-.60f+(ray==1?.075f:ray==2?-.075f:0),dz=-.45f+(ray==3?.075f:ray==4?-.075f:0);bool blocked=false;
  for(const auto&b:casters){
   // A receiver on a building is already directionally shaded by its normal.
   // Skip its own coarse proxy to avoid stamping a second self-shadow on it.
   if(p.y>.30f&&p.x>b.l-.05f&&p.x<b.r+.05f&&p.z>b.n-.05f&&p.z<b.f+.05f)continue;
   float low=.08f,high=b.h-p.y;if(high<=low)continue;
   float tx0=(b.l-p.x)/dx,tx1=(b.r-p.x)/dx,tz0=(b.n-p.z)/dz,tz1=(b.f-p.z)/dz;
   low=std::max(low,std::max(std::min(tx0,tx1),std::min(tz0,tz1)));high=std::min(high,std::min(std::max(tx0,tx1),std::max(tz0,tz1)));
   if(high>low){blocked=true;break;}
  }
  visible+=blocked?0.f:.2f;
 }
 return visible;
}
void baked_street_light(Mesh&m,int style,int lod,int district){
 if(district==2)return;
 const auto casters=light_casters(style,district);
 for(auto&vertex:m){float visibility=light_visibility({vertex.x,vertex.y,vertex.z},casters),shadow=1-visibility;
  vertex.r*=1-.34f*shadow;vertex.g*=1-.25f*shadow;vertex.b*=1-.15f*shadow;
 }
}

Mesh make_chunk_unlit(int style,int lod,int district){
 if(district==2){Mesh m=make_station_chunk(style,lod);quiet_materials(m);return m;}
 Mesh m;m.reserve(15000);
 box(m,0,-.22f,0,9.4f,.19f,12,{.32f,.39f,.40f});
 for(float x:{-2.4f,0.f,2.4f}){
  // A three-metre mirrored stone repeat avoids the former twelve-metre
  // stretched streaks. Even repeats share identical UVs at chunk boundaries.
  for(int part=0;part<4;++part){float z=part*3.f,v0=part%2?1.f:0.f,v1=1-v0;
   uvregion(m,{x-1.04f,.01f,z},{x-1.04f,.01f,z+3},{x+1.04f,.01f,z+3},{x+1.04f,.01f,z},{.35f,.42f,.43f},10,0,v0,1,v1);}
  for(float z=.2f;z<12;z+=(lod<2?1.5f:3.f)){
   quad(m,{x-.93f,.035f,z},{x-.93f,.035f,z+.20f},{x+.93f,.035f,z+.20f},{x+.93f,.035f,z},{.42f,.40f,.33f});
  }
  for(float dx:{-.64f,.64f}){box(m,x+dx,.065f,0,.075f,.075f,12,{.74f,.81f,.77f});}
 }
 for(float side:{-1.f,1.f}){
  box(m,side*4.30f,.015f,0,1.23f,.20f,12,{.65f,.68f,.60f});
  for(float zz=0;zz<12;zz+=2.f)uvquad(m,{side*3.80f,.216f,zz},{side*3.80f,.216f,zz+2.f},{side*4.82f,.216f,zz+2.f},{side*4.82f,.216f,zz},{.80f,.79f,.67f},5,side<0);
  box(m,side*3.71f,.035f,0,.11f,.23f,12,CREAM);
  quad(m,{side*3.8f,.218f,0},{side*3.8f,.218f,12},{side*4.04f,.218f,12},{side*4.04f,.218f,0},{.42f,.49f,.47f},side<0);
  if(lod<2)for(float z=.4f;z<12;z+=1.8f){
   quad(m,{side*3.86f,.22f,z},{side*3.86f,.22f,z+.035f},{side*4.82f,.22f,z+.035f},{side*4.82f,.22f,z},{.41f,.47f,.44f},side<0);
  }
  if(!district){
   // Broad water separates the railway from courtyard architecture.
   uvquad(m,{side*4.94f,-.09f,0},{side*4.94f,-.09f,12},{side*8.4f,-.09f,12},{side*8.4f,-.09f,0},{.20f,.57f,.62f},15,side<0);
   for(int j=0;j<4;++j){float z=j*3.f+.6f;quad(m,{side*5.3f,-.08f,z},{side*5.3f,-.08f,z+.06f},{side*(6.3f+j*.18f),-.08f,z+.06f},{side*(6.3f+j*.18f),-.08f,z},{.45f,.60f,.57f},side<0);}
   building(m,side*(10.5f+(style%2)*.55f),side<0?.7f:2.65f,(style+(side<0?0:2))%6,lod,0);
   if((style+(side<0?0:1))%2==0)building(m,side*10.9f,7.f,(style+1)%6,lod,0);
   if(style%3==0 && lod<2 && side<0){
    // Small moored sampan. The water corridor remains entirely outside gameplay.
    float bx=side*6.7f,bz=3.f;C hull{.20f,.26f,.25f};
    quad(m,{bx-.52f,.04f,bz},{bx-.39f,.04f,bz+2.8f},{bx+.39f,.04f,bz+2.8f},{bx+.52f,.04f,bz},hull);
    quad(m,{bx-.52f,.04f,bz},{bx,.30f,bz-.50f},{bx+.52f,.04f,bz},{bx,.02f,bz+.25f},hull);
    for(float z:{bz+.55f,bz+1.9f})box(m,bx,.12f,z,.85f,.07f,.16f,{.62f,.49f,.31f});
    // Faceted arched cloth canopy with warm paper ends.
    for(int k=0;k<6;++k){float a=k*PI/6,b=(k+1)*PI/6;
     quad(m,{bx+.46f*std::cos(a),.22f+.50f*std::sin(a),bz+.9f},{bx+.46f*std::cos(b),.22f+.50f*std::sin(b),bz+.9f},{bx+.46f*std::cos(b),.22f+.50f*std::sin(b),bz+1.85f},{bx+.46f*std::cos(a),.22f+.50f*std::sin(a),bz+1.85f},NAVY);
    }
    lantern(m,bx,.64f,bz+.35f,.40f,lod);
    for(int k=0;k<4;++k)quad(m,{bx-.12f-k*.035f,-.075f,bz-.7f-k*.25f},{bx-.12f-k*.035f,-.075f,bz-.63f-k*.25f},{bx+.12f+k*.035f,-.075f,bz-.63f-k*.25f},{bx+.12f+k*.035f,-.075f,bz-.7f-k*.25f},{.72f,.52f,.25f});
   }
   if(style==0&&side>0)canal_bridge(m,side,5.4f,lod);
   for(float z:{1.f,7.f}){box(m,side*4.81f,.21f,z,.13f,.75f,.13f,{.46f,.50f,.44f});}
   box(m,side*4.81f,.77f,0,.08f,.09f,12,{.63f,.65f,.54f});
  }else{
   building(m,side*(7.35f+(style%3)*.28f),side<0?.25f:1.65f,(style+(side<0?0:2))%6,lod,1);
   if(style==2&&side>0)pocket_court(m,side,7.05f,lod);
   else building(m,side*(7.6f+((style+1)%3)*.17f),side<0?6.5f:7.05f,(style+(side<0?2:4))%6,lod,1);
  }
  rear_roofline(m,side,style,district);
  // Slim timber lamps; a pair every twelve metres is enough to establish rhythm.
  box(m,side*4.14f,.2f,9.6f,.10f,2.8f,.10f,NAVY);
  box(m,side*3.97f,2.9f,9.6f,.44f,.08f,.10f,NAVY);
  lantern(m,side*3.78f,2.58f,9.6f,.90f,lod);
  if(style%3==1&&lod<2){
   // Bamboo silhouettes are a few tapered triangles rather than sphere trees.
   for(int j=0;j<3;++j){float xx=side*(district?5.0f:8.5f)+j*.17f;
    box(m,xx,.1f,4.2f+j*.12f,.045f,2.7f+j*.28f,.045f,{.23f,.39f,.29f});
    for(int k=0;k<3;++k){float yy=1.4f+k*.5f;float zz=4.2f+j*.12f;
     vertex(m,{xx,yy,zz},{.22f,.42f,.34f});vertex(m,{xx-.65f,yy+.26f,zz},{.22f,.42f,.34f});vertex(m,{xx-.13f,yy+.03f,zz-.12f},{.22f,.42f,.34f});
     vertex(m,{xx,yy+.2f,zz},{.29f,.48f,.37f});vertex(m,{xx+.6f,yy+.44f,zz},{.29f,.48f,.37f});vertex(m,{xx+.13f,yy+.24f,zz+.1f},{.29f,.48f,.37f});
    }
   }
  }
 }
 if(district&&style==2){
  // High footbridge spans a fully clear boosted-jump and jetpack corridor, not a fake slide obstacle.
  for(float x:{-4.4f,4.4f})box(m,x,.2f,7,.27f,8.1f,.3f,CORAL);
  box(m,0,8.25f,6.3f,9.2f,.23f,1.6f,NAVY);
  for(float x:{-4.f,-3.f,-2.f,-1.f,0.f,1.f,2.f,3.f,4.f})box(m,x,8.48f,6.3f,.07f,.70f,.08f,CORAL);
  box(m,0,9.15f,6.3f,9.2f,.08f,.09f,CORAL);
  roof(m,0,9.35f,6.05f,9.7f,2.1f,NAVY);
  for(float x:{-3.f,0.f,3.f})lantern(m,x,7.70f,6.65f,.75f,lod);
 }
 // Emit the opaque terrace last so buildings can reject hidden floor pixels.
 district_terrace(m,district);quiet_materials(m);return m;
}

Mesh make_chunk(int style,int lod,int district,bool baked_shadow=false){Mesh m=make_chunk_unlit(style,lod,district);if(baked_shadow)baked_street_light(m,style,lod,district);return m;}

Mesh make_backdrop(){
 Mesh m; // Parallax-free painted mountains, with their own atmospheric light.
 auto painted=[&](P a,P b,P c,P d,C low,C high){vertex(m,a,low);vertex(m,b,high);vertex(m,c,high);vertex(m,a,low);vertex(m,c,high);vertex(m,d,low);};
 C ridges[]={{.72f,.82f,.82f},{.60f,.75f,.77f},{.47f,.65f,.69f}};
 for(int layer=0;layer<3;++layer){float z=100.f-layer*9.f;
  auto ridge=[&](float x){
   return 5.3f+(2-layer)*1.45f+2.4f*std::sin(x*.083f+layer*1.7f)
       +1.5f*std::sin(x*.173f-layer*.9f)+.8f*std::sin(x*.31f+layer*.4f);
  };
  for(int j=0;j<80;++j){float x=-100.f+j*2.5f,xx=x+2.5f;
   painted({x,-2,z},{x,ridge(x),z},{xx,ridge(xx),z},{xx,-2,z},{.77f,.84f,.81f},ridges[layer]);}
 }
 // Three continuous painted cloud banks. Their upper silhouette is the union
 // envelope of four lobes; no overlapping disks, alpha, textures or extra pass.
 // Keep every nonzero triangle byte-for-byte; endpoints can collapse one
 // half of a painted quad. Test exact area, never an epsilon or relaxed gate.
 auto cloud_painted=[&](P a,P b,P c,P d,C low,C high){
  auto triangle=[&](P p,P q,P r,C pc,C qc,C rc){
   float ax=q.x-p.x,ay=q.y-p.y,az=q.z-p.z,bx=r.x-p.x,by=r.y-p.y,bz=r.z-p.z;
   if(ay*bz-az*by==0.f&&az*bx-ax*bz==0.f&&ax*by-ay*bx==0.f)return;
   vertex(m,p,pc);vertex(m,q,qc);vertex(m,r,rc);
  };
  triangle(a,b,c,low,high,high);triangle(a,c,d,low,high,low);
 };
 const float cloud_cx[]={-45.f,5.f,49.f},cloud_cy[]={22.f,24.f,21.f};
 const float cloud_half[]={15.f,17.f,19.f},cloud_height[]={4.2f,5.0f,4.0f};
 const float lobe_x[]={-.62f,-.22f,.28f,.68f};
 const float lobe_radius[]={.38f,.52f,.48f,.32f};
 const float lobe_height[]={.53f,1.f,.78f,.46f};
 for(int group=0;group<3;++group){
  auto top=[&](float u){if(group==1)u=-u;float h=0;for(int j=0;j<4;++j){float t=(u-lobe_x[j])/lobe_radius[j];if(std::abs(t)<1.f)h=std::max(h,lobe_height[j]*(group==2?(j==1?.90f:j==2?1.10f:1.f):1.f)*std::sqrt(std::max(0.f,1-t*t)));}return h*cloud_height[group];};
  const C low{.70f,.83f,.85f},mid{.88f,.93f,.90f},high{.96f,.97f,.91f};
  for(int k=0;k<24;++k){float a=-1.f+2.f*k/24,b=-1.f+2.f*(k+1)/24;
   float x=cloud_cx[group]+a*cloud_half[group],xx=cloud_cx[group]+b*cloud_half[group];
   float y=cloud_cy[group],ha=top(a),hb=top(b),z=102.f;
   cloud_painted({x,y,z},{x,y+ha*.42f,z},{xx,y+hb*.42f,z},{xx,y,z},low,mid);
   cloud_painted({x,y+ha*.42f,z},{x,y+ha,z},{xx,y+hb,z},{xx,y+hb*.42f,z},mid,high);
  }
 }
 // Sun disk remains a quiet warm accent behind the cool mountain layers.
 for(int i=0;i<48;++i){float a=i*2*PI/48,b=(i+1)*2*PI/48;C moon{1.f,.84f,.56f};
  vertex(m,{-21.f,19.f,101.f},moon);vertex(m,{-21.f+4.2f*std::cos(a),19.f+4.2f*std::sin(a),101.f},moon);vertex(m,{-21.f+4.2f*std::cos(b),19.f+4.2f*std::sin(b),101.f},moon);}
 return m;
}

Mesh make_ramp(){
 Mesh m;C wood{.19f,.43f,.43f};float h=2.49f,d=6.f,w=.94f;
 quad(m,{-w,0,0},{-w,h,d},{w,h,d},{w,0,0},wood);
 quad(m,{-w,0,0},{w,0,0},{w,0,d},{-w,0,d},NAVY);
 for(float x:{-w,w}){vertex(m,{x,0,0},NAVY);vertex(m,{x,h,d},NAVY);vertex(m,{x,0,d},NAVY);}
 for(float side:{-1.f,1.f})quad(m,{side*(w-.055f),.018f,0},{side*(w-.055f),h+.018f,d},{side*w,h+.018f,d},{side*w,.018f,0},GOLD);
 for(float z:{1.4f,3.2f,5.f}){float y=z*h/d+.025f;C c{.91f,.86f,.56f};
  quad(m,{-.43f,y-.16f,z-.4f},{0,y+.04f,z+.1f},{0,y+.16f,z+.4f},{-.43f,y-.04f,z-.1f},c);
  quad(m,{0,y+.04f,z+.1f},{.43f,y-.16f,z-.4f},{.43f,y-.04f,z-.1f},{0,y+.16f,z+.4f},c);
 }
 return m;
}

void multiply(float*out,const float*a,const float*b){float tmp[16]={};for(int c=0;c<4;++c)for(int r=0;r<4;++r)for(int k=0;k<4;++k)tmp[c*4+r]+=a[k*4+r]*b[c*4+k];std::copy(tmp,tmp+16,out);}
struct ChaseCamera {
 float altitude=0,anchor=0,eye_y=3.55f,pitch=.12f,eye_z=-7.f;
 bool initialized=false;
 void update(float surface,float feet,bool supported,bool flying,float dt,bool reset){
  // Track the support plane, not the jump arc. Hold this anchor while crossing
  // a gap; a missed jump still falls normally and the camera follows down.
  if(!initialized){initialized=true;reset=true;anchor=std::max(0.f,std::min(surface,feet));}
  if(supported)anchor=surface;
  else if(flying)anchor=feet*.80f;
  else if(feet<anchor-.20f)anchor=std::max(0.f,feet);
  if(reset)altitude=anchor;
  else altitude+=(anchor-altitude)*(1.f-std::exp(-std::max(0.f,dt)*(anchor>altitude?7.f:4.f)));
  float roof=std::max(0.f,std::min(1.f,altitude/2.49f));roof=roof*roof*(3-2*roof);
  eye_y=3.55f+altitude+roof;pitch=.12f+.16f*roof;eye_z=-7.f-1.2f*roof;
 }
};
void camera(float*out,int w,int h,float px,const ChaseCamera& chase){
 float eyeX=(px-1.f)*.28f,eyeY=chase.eye_y,eyeZ=chase.eye_z,pitch=chase.pitch;
 // A small ground-only framing change gives the hero a readable silhouette.
 // Roof altitude restores exactly the original 55-degree clearance view.
 float roof=std::max(0.f,std::min(1.f,chase.altitude/2.49f));roof=roof*roof*(3-2*roof);
 float fov=51.5f+3.5f*roof;
 float s=std::sin(pitch),c=std::cos(pitch),f=1.f/std::tan(fov*PI/360.f),n=.24f,far=128;
 // Left-handed camera keeps positive world x on screen right and z forward.
 float v[16]={1,0,0,0,0,c,-s,0,0,s,c,0,-eyeX,-c*eyeY-s*eyeZ,s*eyeY-c*eyeZ,1};
 float p[16]={f/(float(w)/h),0,0,0,0,f,0,0,0,0,(far+n)/(far-n),1,0,0,-2*far*n/(far-n),0};multiply(out,p,v);
}
struct Fx {float born,x;int kind;};
struct Model{
 uint32_t id=0;int count=0;P lower{},upper{};bool bounded=false;
};
// A linear homogeneous half-space has its extreme over an AABB at a corner.
// Reject only a common outside plane, never an estimated screen/distance box.
// Mixed/near-zero w, non-finite math, and unknown bounds remain drawable.
bool outside_frustum(const Model&model,const float*matrix){
 if(!model.bounded||!matrix||model.lower.x>model.upper.x||model.lower.y>model.upper.y||model.lower.z>model.upper.z)return false;
 for(int i=0;i<16;++i)if(!std::isfinite(matrix[i]))return false;
 unsigned common=63;bool positive=false,negative=false;
 for(int k=0;k<8;++k){
  const double p[]={k&1?model.upper.x:model.lower.x,k&2?model.upper.y:model.lower.y,k&4?model.upper.z:model.lower.z,1.};
  double clip[4]={},sum[4]={};
  for(int row=0;row<4;++row)for(int col=0;col<4;++col){double term=double(matrix[col*4+row])*p[col];clip[row]+=term;sum[row]+=std::abs(term);}
  for(int row=0;row<4;++row)if(!std::isfinite(clip[row])||!std::isfinite(sum[row])||sum[row]>double(std::numeric_limits<float>::max())*.5)return false;
  const double roundoff=16.*std::numeric_limits<float>::epsilon();
  double wguard=roundoff*std::max(1.,sum[3]);
  if(std::abs(clip[3])<=wguard)return false;
  positive=positive||clip[3]>0;negative=negative||clip[3]<0;
  unsigned outside=0;
  for(int axis=0;axis<3;++axis){
   double guard=roundoff*std::max(1.,sum[axis]+sum[3]);
   if(clip[3]+clip[axis]<-guard)outside|=1u<<(axis*2);
   if(clip[3]-clip[axis]<-guard)outside|=1u<<(axis*2+1);
  }
  common&=outside;
 }
 return !(positive&&negative)&&common!=0;
}
struct Impact{unsigned id;float born;};
struct Scene {std::string effective_flags_snapshot;bool baked_shadow=false;void*gl=nullptr;int width=960,height=540;Model chunk[3][6][4],trains[3],powerups[10],barrier,arch,coin,ramp,backdrop;Mesh vertices;std::vector<uint16_t> actor_indices;size_t actor_tail=0;bool indexed_actor=false;std::string info;std::vector<Fx> fx;std::vector<Impact>impacts;float clock=0,last_distance=0;int visible_vertices=0,peak_dynamic_vertices=0;runner_motion::AnimatorState animator;ChaseCamera chase;float warning_edge=-1;int animation_view=0,animation_mode=0;float jetpack_time=0,boots_time=0,coinboost_time=0,flight_blend=0;};
void append_stream_character(Scene*s,float x,float jump,float slide,float distance,float clock,const runner_motion::Pose&motion){
 s->actor_indices.clear();
 if(s->indexed_actor){
  for(size_t i=0;i<s->vertices.size();++i)s->actor_indices.push_back(uint16_t(i));
  importedcourier::append_indexed(s->vertices,s->actor_indices,x,jump,slide,distance,clock,0,&motion);
 }else append_character(s->vertices,x,jump,slide,distance,clock,0,&motion);
 s->actor_tail=s->vertices.size();
}
bool draw_actor_stream(Scene*s,const float*matrix){
 auto&m=s->vertices;s->peak_dynamic_vertices=std::max(s->peak_dynamic_vertices,int(m.size()));
 if(s->indexed_actor){
  for(size_t i=s->actor_tail;i<m.size();++i)s->actor_indices.push_back(uint16_t(i));
  s->visible_vertices+=int(s->actor_indices.size());
  return inkgl_draw_indexed(s->gl,m.data(),int(m.size()),s->actor_indices.data(),int(s->actor_indices.size()),matrix)!=0;
 }
 s->visible_vertices+=int(m.size());return inkgl_draw(s->gl,m.data(),int(m.size()),matrix)!=0;
}
void animation_camera(float*out,int w,int h,int view){
 P eye=view==3?P{0,1.9f,5.6f}:view==1?P{5.6f,2.0f,-.15f}:P{3.8f,2.1f,-4.5f},target{0,1.45f,0};
 auto unit=[](P p){float n=std::sqrt(p.x*p.x+p.y*p.y+p.z*p.z);return P{p.x/n,p.y/n,p.z/n};};
 auto dot=[](P a,P b){return a.x*b.x+a.y*b.y+a.z*b.z;};
 P z=unit(target-eye),x=unit(cross({0,1,0},z)),y=cross(z,x);
 float v[]={x.x,y.x,z.x,0,x.y,y.y,z.y,0,x.z,y.z,z.z,0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1};
 float f=1/std::tan(48.f*PI/360),n=.1f,far=30;
 float p[]={f/(float(w)/h),0,0,0,0,f,0,0,0,0,(far+n)/(far-n),1,0,0,-2*far*n/(far-n),0};multiply(out,p,v);
}
Model upload(Scene*s,const Mesh&m){
 // Pole/cap builders contain exact zero-area triangles. Drop only exact
 // degenerates once before VBO upload; visible triangles keep their byte data.
 Mesh clean;clean.reserve(m.size());
 for(size_t i=0;i+2<m.size();i+=3){P a{m[i].x,m[i].y,m[i].z},b{m[i+1].x,m[i+1].y,m[i+1].z},c{m[i+2].x,m[i+2].y,m[i+2].z};P n=cross(b-a,c-a);
  if(n.x==0.f&&n.y==0.f&&n.z==0.f)continue;
  clean.insert(clean.end(),m.begin()+i,m.begin()+i+3);
 }
 Model model;model.id=inkgl_mesh_create(s->gl,clean.data(),int(clean.size()));model.count=int(clean.size());
 if(!clean.empty()){
  model.lower=model.upper={clean[0].x,clean[0].y,clean[0].z};model.bounded=true;
  for(const auto&v:clean){
   if(!std::isfinite(v.x)||!std::isfinite(v.y)||!std::isfinite(v.z)){model.bounded=false;break;}
   model.lower.x=std::min(model.lower.x,v.x);model.lower.y=std::min(model.lower.y,v.y);model.lower.z=std::min(model.lower.z,v.z);
   model.upper.x=std::max(model.upper.x,v.x);model.upper.y=std::max(model.upper.y,v.y);model.upper.z=std::max(model.upper.z,v.z);
  }
 }
 return model;
}
int draw_model(Scene*s,const Model&model,const float*mvp,float x,float y,float z,float angle=0){
 if(!model.id)return 0;float c=std::cos(angle),sn=std::sin(angle);
 float t[]={c,0,-sn,0,0,1,0,0,sn,0,c,0,x,y,z,1},out[16];multiply(out,mvp,t);
 if(outside_frustum(model,out))return 1;
 s->visible_vertices+=model.count;return inkgl_mesh_draw(s->gl,model.id,out);
}
}

extern "C" {

// BEGIN ADDITIVE SCENE EFFECTIVE FLAGS API
const char*rv_effective_flags(void*p){
 auto*s=(Scene*)p;if(!s)return nullptr;const char*raw=inkgl_effective_flags(s->gl);if(!raw)return nullptr;
 std::string result=raw,extra;const std::string marker="\"effective\":{";const size_t at=result.find(marker);
 if(at==std::string::npos)return nullptr;
 extra+="\"INK_GL_DYNAMIC_INDEXED\":\"";extra+=s->indexed_actor?"1":"0";extra+="\",";
 extra+="\"INK_RUNNER_BAKED_SHADOW\":\"";extra+=s->baked_shadow?"1":"0";extra+="\",";
 result.insert(at+marker.size(),extra);s->effective_flags_snapshot=std::move(result);return s->effective_flags_snapshot.c_str();
}
// END ADDITIVE SCENE EFFECTIVE FLAGS API

struct RunnerFrame {float distance,player_x,jump_height,slide_phase,shield_time,magnet_time,clock;int state;float player_y;};
struct RunnerEntity {int kind,lane;float z;int collected;float y;};
int rv_abi_version(){return 2;}
int rv_material_api_version(){return 1;}
int rv_set_material_atlas(void*p,const unsigned char*rgba,int w,int h,int stride){return p?inkgl_set_material_atlas(((Scene*)p)->gl,rgba,w,h,stride):0;}
int rv_material_etc1_api_version(){return 1;}
int rv_material_etc1_supported(void*p){return p?inkgl_material_etc1_supported(((Scene*)p)->gl):0;}
int rv_set_material_atlas_etc1(void*p,const unsigned char*data,int bytes,int w,int h){return p?inkgl_set_material_atlas_etc1(((Scene*)p)->gl,data,bytes,w,h):0;}
int rv_material_storage_bits(void*p){return p?inkgl_material_storage_bits(((Scene*)p)->gl):0;}
int rv_material_mips_api_version(){return inkgl_material_mips_api_version();}
const char*rv_material_mips_base_sha256(){return inkgl_material_mips_base_sha256();}
const char*rv_material_mips_source_sha256(){return inkgl_material_mips_source_sha256();}
int rv_set_material_mips(void*p,const char*base,const char*png){return p?inkgl_set_material_mips(((Scene*)p)->gl,base,png):0;}
int rv_material_mips_levels(void*p){return p?inkgl_material_mips_levels(((Scene*)p)->gl):0;}
int rv_material_mips_gpu_bytes(void*p){return p?inkgl_material_mips_gpu_bytes(((Scene*)p)->gl):0;}
int rv_material_mips_upload_bytes(void*p){return p?inkgl_material_mips_upload_bytes(((Scene*)p)->gl):0;}
const char*rv_material_mips_reason(void*p){return p?inkgl_material_mips_reason(((Scene*)p)->gl):"no scene";}
int rv_frame_size(){return sizeof(RunnerFrame);}
int rv_entity_size(){return sizeof(RunnerEntity);}
// Diagnostic only: production remains view 0; no existing frame/entity ABI changes.
int rv_set_animation_view(void*p,int view){if(!p||view<0||view>3)return 0;((Scene*)p)->animation_view=view;return 1;}
int rv_animation_mode(void*p){return p?((Scene*)p)->animation_mode:-1;}
// Additive read-only QA hook: actual camera values, no renderer/physics override.
int rv_camera_state(void*p,float*out,int count){if(!p||!out||count<5)return 0;Scene*s=(Scene*)p;out[0]=s->chase.eye_y;out[1]=s->chase.pitch;out[2]=s->chase.eye_z;out[3]=s->chase.altitude;out[4]=s->warning_edge;return 1;}
// Optional additive ABI: validate all values before committing any context state.
int rv_set_powerups(void*p,float jetpack,float boots,float coinboost){
 if(!p||!std::isfinite(jetpack)||!std::isfinite(boots)||!std::isfinite(coinboost)||jetpack<0||boots<0||coinboost<0)return 0;
 Scene*s=(Scene*)p;s->jetpack_time=jetpack;s->boots_time=boots;s->coinboost_time=coinboost;return 1;
}
void*rv_create(uint64_t xid,int width,int height,int off,char*err,int cap){
 // The board supports 4x tile MSAA. Override INK_GL_SAMPLES=0 for profiling.
 if(!std::getenv("INK_GL_SAMPLES"))setenv("INK_GL_SAMPLES","4",0);
 Scene*s=new Scene;s->gl=off?inkgl_create_offscreen(width,height,err,cap):inkgl_create(xid,width,height,err,cap);
 if(!s->gl){delete s;return nullptr;}s->width=width;s->height=height;
 const char*light=std::getenv("INK_RUNNER_BAKED_SHADOW");s->baked_shadow=!light||!std::strcmp(light,"1");
 try{for(int d=0;d<3;++d)for(int j=0;j<6;++j)for(int lod=0;lod<4;++lod){s->chunk[d][j][lod]=upload(s,make_chunk(j,lod,d,s->baked_shadow));if(!s->chunk[d][j][lod].id)throw 1;}
  for(int j=0;j<3;++j){s->trains[j]=upload(s,make_train(j));if(!s->trains[j].id)throw 1;}
  s->barrier=upload(s,make_barrier());s->arch=upload(s,make_arch());s->coin=upload(s,make_coin());s->ramp=upload(s,make_ramp());s->backdrop=upload(s,make_backdrop());
  // One bounded 2.36MB allocation covers the imported hero, accessories and
  // all 24 simultaneous FX events. clear() retains it across game frames.
  s->vertices.reserve(65536);
  s->actor_indices.reserve(65536);importedcourier::warm_indexed_character();
  const char*indexed=std::getenv("INK_GL_DYNAMIC_INDEXED");s->indexed_actor=!indexed||std::strcmp(indexed,"1")==0;
  for(int kind:{4,5,7,8,9}){s->powerups[kind]=upload(s,make_powerup(kind));if(!s->powerups[kind].id)throw 1;}
  if(!s->barrier.id||!s->arch.id||!s->coin.id||!s->ramp.id||!s->backdrop.id)throw 1;}
 catch(...){inkgl_destroy(s->gl);delete s;if(err&&cap)std::snprintf(err,cap,"scene allocation failed");return nullptr;}
 s->info=std::string(inkgl_info(s->gl))+" | Lantern Courier / river / lantern market / station concourse / roof relay / ABI 2";return s;
}
int rv_resize(void*p,int w,int h){Scene*s=(Scene*)p;if(!s||w<1||h<1)return 0;if(!inkgl_resize(s->gl,w,h))return 0;s->width=w;s->height=h;return 1;}
int rv_set_hud(void*p,const unsigned char*rgba,int w,int h,int stride){Scene*s=(Scene*)p;return s?inkgl_set_hud(s->gl,rgba,w,h,stride):0;}
int rv_patch_hud(void*p,const unsigned char*rgba,int x,int y,int w,int h,int stride){Scene*s=(Scene*)p;return s?inkgl_patch_hud(s->gl,rgba,x,y,w,h,stride):0;}
int rv_set_hud_bgra(void*p,const unsigned char*bgra,int w,int h,int stride){Scene*s=(Scene*)p;return s?inkgl_set_hud_bgra(s->gl,bgra,w,h,stride):0;}
int rv_patch_hud_bgra(void*p,const unsigned char*bgra,int x,int y,int w,int h,int stride){Scene*s=(Scene*)p;return s?inkgl_patch_hud_bgra(s->gl,bgra,x,y,w,h,stride):0;}
const char*rv_info(void*p){return p?((Scene*)p)->info.c_str():"no scene";}
const char*rv_error(void*p){return p?inkgl_error(((Scene*)p)->gl):"no scene";}
int rv_event(void*p,int kind,int lane){Scene*s=(Scene*)p;if(!s||lane<0||lane>2||kind<0||kind>4)return 0;if(s->fx.size()>=24)s->fx.erase(s->fx.begin());s->fx.push_back({s->clock,(lane-1)*2.4f,kind});return 1;}
int rv_frame(void*p,const RunnerFrame*f,const RunnerEntity*items,int count){
 Scene*s=(Scene*)p;if(!s||!f||count<0||count>160||(count&&!items))return 0;
 if(!std::isfinite(f->distance)||f->distance<0||f->distance>1e8f||!std::isfinite(f->player_x)||f->player_x<0||f->player_x>2||!std::isfinite(f->clock)||!std::isfinite(f->player_y)||f->player_y<0||f->player_y>20||!std::isfinite(f->jump_height)||!std::isfinite(f->slide_phase)||!std::isfinite(f->shield_time)||!std::isfinite(f->magnet_time)||f->state<0||f->state>3)return 0;
 auto&m=s->vertices;m.clear();s->visible_vertices=0;float anim_dt=std::max(0.f,std::min(.1f,f->clock-s->clock));if(f->state!=1)anim_dt=0;s->clock=f->clock;float distance=std::max(0.f,f->distance);float top[]={.27f,.54f,.74f},bottom[]={.85f,.88f,.81f},haze[]={.75f,.84f,.82f};
 bool restart=distance+.0001f<s->last_distance||f->state==0;
 if(restart){s->impacts.clear();s->fx.clear();s->flight_blend=0;s->chase=ChaseCamera{};}s->last_distance=distance;
 bool supported=f->player_y<.04f;float shadow_surface=0;
 for(int i=0;i<count;++i){const auto&e=items[i];
  if((unsigned(e.collected)&3)||std::abs(f->player_x-e.lane)>=.43f)continue;
  float surface=-100;
  if(e.kind==0&&e.z<=.03f&&e.z+7.2f>=-.03f)surface=e.y+2.49f;
  if(e.kind==6&&e.z<=0&&e.z+6.f>=-.03f)surface=e.y+2.49f*std::min(1.f,-e.z/6.f);
  if(std::abs(f->player_y-surface)<.055f)supported=true;
  if(surface<=f->player_y+.055f)shadow_surface=std::max(shadow_surface,surface);
 }
 s->chase.update(shadow_surface,f->player_y,supported,s->jetpack_time>0,anim_dt,restart);
 runner_motion::Input motion_input{f->distance,f->clock,f->player_x,f->player_y,f->jump_height,f->slide_phase,supported,f->state};
 runner_motion::Pose motion=s->animator.update(motion_input);int base_mode=motion.mode;
 float target_flight=s->jetpack_time>0?1.f:0.f;
 if(f->state==0){s->flight_blend=0;}else if(anim_dt>0){float step=anim_dt/(target_flight>.5f?.18f:.24f);s->flight_blend+=std::max(-step,std::min(step,target_flight-s->flight_blend));}
 if(s->flight_blend>0){
  runner_motion::Pose fly=runner_motion::jumping_pose(.46f,motion.phase,0);
  fly.joint[1].x=.10f;fly.joint[5]={-.16f,0,-.48f};fly.joint[10]={-.16f,0,.48f};
  fly.joint[6].x=fly.joint[11].x=-.65f;
  fly.joint[7].x=.12f;fly.joint[12].x=.18f;fly.joint[8].x=.50f;fly.joint[13].x=.56f;
  fly.joint[9].x=-.24f;fly.joint[14].x=-.26f;fly.foot_lift=0;fly.height_cap=0;
  motion=runner_motion::blend(motion,fly,runner_motion::ease(s->flight_blend));motion.height_cap=0;motion.foot_lift=0;
 }
 s->animation_mode=s->jetpack_time>0?5:base_mode;
 if(!inkgl_begin(s->gl,top,bottom))return 0;
 if(s->animation_view){
  float diagnostic[16];animation_camera(diagnostic,s->width,s->height,s->animation_view);inkgl_set_fog(s->gl,0,0,bottom);
  quad(m,{-3,0,-3},{-3,0,3},{3,0,3},{3,0,-3},{.26f,.34f,.37f});
  for(float z=-2;z<=2;z+=.5f)quad(m,{-2,.003f,z},{-2,.003f,z+.012f},{2,.003f,z+.012f},{2,.003f,z},{.40f,.48f,.48f});
  disk(m,0,.006f,0,.32f,.24f,{.16f,.23f,.26f});
  size_t start=m.size();append_stream_character(s,0,std::max(0.f,f->jump_height),f->slide_phase,distance,f->clock,motion);
  for(size_t j=start;j<m.size();++j)m[j].y+=f->player_y-std::max(0.f,std::min(1.f,f->jump_height))*1.35f;
  return draw_actor_stream(s,diagnostic)&&inkgl_present(s->gl);
 }
 float matrix[16];camera(matrix,s->width,s->height,f->player_x,s->chase);
 inkgl_set_fog(s->gl,0,0,bottom);
 if(!draw_model(s,s->backdrop,matrix,0,0,0))return 0;
 inkgl_set_fog(s->gl,42,105,haze);
 int tile=int(std::floor(distance/12));float offset=std::fmod(distance,12.f);
 for(int j=-1;j<9;++j){int lod=j<2?0:j<3?1:j<6?2:3;if(!draw_model(s,s->chunk[((tile+j+60000)/20)%3][((tile+j)%6+6)%6][lod],matrix,0,0,j*12.f-offset))return 0;}
 s->warning_edge=-1;
 for(int i=0;i<count;++i){const auto&e=items[i];unsigned flags=unsigned(e.collected),id=flags>>2;
  if((flags&1)||e.lane<0||e.lane>2||!std::isfinite(e.z)||!std::isfinite(e.y)||e.y<0||e.y>20||(e.kind==0?e.z+7.2f<-7.f:e.kind==6?e.z+6.f<-7.f:e.z<-2.f)||e.z>85)continue;float x=(e.lane-1)*2.4f;
  if(e.kind==0||e.kind==6){
   float opacity=1;
   if(flags&2){auto it=std::find_if(s->impacts.begin(),s->impacts.end(),[&](const Impact&a){return a.id==id;});
    if(it==s->impacts.end()){if(s->impacts.size()>=160)s->impacts.erase(s->impacts.begin());s->impacts.push_back({id,f->clock});it=s->impacts.end()-1;}
    opacity=std::max(0.f,1-(f->clock-it->born)/.12f);if(opacity<=0)continue;
    inkgl_set_opacity(s->gl,opacity);
   }
   if(!draw_model(s,(e.kind==0?s->trains[(e.lane+id)%3]:s->ramp),matrix,x,e.y+.14f,e.z))return 0;
   if(flags&2)inkgl_set_opacity(s->gl,1);
   // Mark only a real exposed outgoing edge. Joined car seams never flash;
   // the paint stays on the physical roof and never fills the empty gap.
   float edge=e.z+7.2f;
   if(e.kind==0&&!(flags&3)&&edge>.2f&&edge<18.f&&std::abs(f->player_x-e.lane)<.45f&&f->player_y>e.y+2.10f&&s->jetpack_time<=0){
    bool joined=false,landing=false;
    for(int j=0;j<count;++j){const auto&n=items[j];
     if(j==i||n.kind!=0||(unsigned(n.collected)&3)||std::abs(n.y-e.y)>=.04f)continue;
     if(n.lane==e.lane&&n.z<=edge+.08f&&n.z+7.2f>edge+.08f)joined=true;
     if(std::abs(n.lane-e.lane)<=1&&n.z>edge+.08f&&n.z<=edge+4.f)landing=true;
    }
    if(!joined&&landing){
     s->warning_edge=s->warning_edge<0?edge:std::min(edge,s->warning_edge);
     float y=e.y+2.66f;C cue=edge<7.f?C{1.f,.48f+.16f*std::sin(f->clock*9),.16f}:GOLD;
     quad(m,{x-.74f,y,edge-.27f},{x-.74f,y,edge-.08f},{x+.74f,y,edge-.08f},{x+.74f,y,edge-.27f},cue);
     for(float dz:{.85f,1.55f}){float z=edge-dz;
      quad(m,{x-.43f,y,z-.25f},{x,y,z+.12f},{x,y,z+.36f},{x-.43f,y,z-.01f},cue);
      quad(m,{x,y,z+.12f},{x+.43f,y,z-.25f},{x+.43f,y,z-.01f},{x,y,z+.36f},cue);
     }
    }
   }
  }
  else if(e.kind==1){if(!draw_model(s,s->barrier,matrix,x,e.y+.14f,e.z))return 0;}
  else if(e.kind==2){if(!draw_model(s,s->arch,matrix,x,e.y+.14f,e.z))return 0;}
  else if(e.kind==3){if(!draw_model(s,s->coin,matrix,x,e.y+1.02f+.05f*std::sin(f->clock*3+e.z),e.z,f->clock*2.8f+e.z*.09f))return 0;}

  else if(e.kind==4||e.kind==5||e.kind==7||e.kind==8||e.kind==9){
   float bob=.065f*std::sin(f->clock*3.0f+e.z*.12f),turn=.28f*std::sin(f->clock*1.8f+e.z);
   if(!draw_model(s,s->powerups[e.kind],matrix,x,e.y+1.08f+bob,e.z,turn))return 0;
   // A small orbit identifies pickups without a translucent column hiding routes.
   for(int j=0;j<3;++j){float a=f->clock*2+j*2*PI/3,xx=x+.48f*std::cos(a),yy=e.y+1.08f+bob+.48f*std::sin(a);
    quad(m,{xx-.035f,yy,e.z},{xx,yy+.075f,e.z},{xx+.035f,yy,e.z},{xx,yy-.075f,e.z},CREAM);}
  }
 }
 float x=(std::max(0.f,std::min(2.f,f->player_x))-1.f)*2.4f;
 // Alpha shadow is a tiny separate batch, all city/obstacle geometry is opaque.
 contact_shadow(m,x,.148f+shadow_surface,0,.43f+.10f*f->jump_height,.34f,.29f/(1+.40f*std::max(0.f,f->jump_height)));inkgl_set_blend(s->gl,1);
 s->visible_vertices+=int(m.size());
 s->peak_dynamic_vertices=std::max(s->peak_dynamic_vertices,int(m.size()));
 if(!inkgl_draw(s->gl,m.data(),int(m.size()),matrix))return 0;m.clear();inkgl_set_blend(s->gl,0);
 if(s->jetpack_time>0){
  if(!draw_model(s,s->powerups[7],matrix,x,f->player_y+1.10f,-.36f))return 0;
  for(float sign:{-1.f,1.f}){float xx=x+sign*.27f,yy=f->player_y+.80f,flame=.32f+.10f*std::sin(f->clock*21);
   quad(m,{xx-.10f,yy,-.37f},{xx,yy-flame,-.37f},{xx+.10f,yy,-.37f},{xx,yy+.04f,-.37f},GOLD);
   quad(m,{xx-.045f,yy,-.38f},{xx,yy-flame*.65f,-.38f},{xx+.045f,yy,-.38f},{xx,yy+.025f,-.38f},CREAM);
  }
 }
 if(s->boots_time>0){for(float side:{-1.f,1.f}){float xx=x+side*.24f,yy=f->player_y+.22f;quad(m,{xx-.10f,yy,-.16f},{xx,yy+.18f,-.16f},{xx+.10f,yy,-.16f},{xx,yy-.04f,-.16f},{.40f,.96f,.94f});}}
 if(s->coinboost_time>0){for(int k=0;k<3;++k){float a=f->clock*2.2f+k*2*PI/3;float xx=x+.63f*std::cos(a),yy=f->player_y+1.04f+.13f*std::sin(a);quad(m,{xx-.045f,yy,-.12f},{xx,yy+.09f,-.12f},{xx+.045f,yy,-.12f},{xx,yy-.09f,-.12f},GOLD);}}
 size_t hero_begin=m.size();append_stream_character(s,x,std::max(0.f,f->jump_height),f->slide_phase,distance,f->clock,motion);
 for(size_t j=hero_begin;j<m.size();++j)m[j].y+=.14f+f->player_y-std::max(0.f,std::min(1.f,f->jump_height))*1.35f;
 for(const auto&fx:s->fx){float age=f->clock-fx.born;if(age<0||age>.65f)continue;C c=fx.kind==0?GOLD:(fx.kind==2||fx.kind==4)?TEAL:CORAL;
  if(fx.kind==4){float radius=.40f+age*2.2f;for(int k=0;k<16;++k){float a=k*2*PI/16,b=(k+1)*2*PI/16,y=f->player_y+.18f;
   quad(m,{fx.x+radius*std::cos(a),y,radius*std::sin(a)},{fx.x+radius*std::cos(b),y,radius*std::sin(b)},{fx.x+(radius+.065f)*std::cos(b),y,(radius+.065f)*std::sin(b)},{fx.x+(radius+.065f)*std::cos(a),y,(radius+.065f)*std::sin(a)},CREAM);}}
  for(int j=0;j<12;++j){float a=j*2*PI/12;float r=.22f+age*1.5f;float xx=fx.x+r*std::cos(a),yy=f->player_y+.8f+r*std::sin(a)+age*.45f;float d=.06f*(1-age/.65f);quad(m,{xx-d,yy,0},{xx,yy+d*2,0},{xx+d,yy,0},{xx,yy-d*2,0},c);}}
 s->fx.erase(std::remove_if(s->fx.begin(),s->fx.end(),[&](const Fx&fx){return f->clock-fx.born>.65f||f->clock<fx.born;}),s->fx.end());
 if(f->shield_time>0){for(int i=0;i<16;++i){float a=i*2*PI/16;float b=(i+1)*2*PI/16;quad(m,{x+.58f*std::cos(a),f->player_y+.18f,.58f*std::sin(a)},{x+.58f*std::cos(b),f->player_y+.18f,.58f*std::sin(b)},{x+.56f*std::cos(b),f->player_y+.25f,.56f*std::sin(b)},{x+.56f*std::cos(a),f->player_y+.25f,.56f*std::sin(a)},{.30f,.95f,.95f,.75f});}}
 return draw_actor_stream(s,matrix)&&inkgl_present(s->gl);
}
int rv_finish(void*p){return p?inkgl_finish(((Scene*)p)->gl):0;}
int rv_vertex_count(void*p){return p?((Scene*)p)->visible_vertices:0;}
// Read-only additive QA hooks; do not resize or mutate the production buffer.
int rv_dynamic_capacity(void*p){return p?int(((Scene*)p)->vertices.capacity()):0;}
int rv_dynamic_peak(void*p){return p?((Scene*)p)->peak_dynamic_vertices:0;}
int rv_dynamic_indexed(void*p){return p&&((Scene*)p)->indexed_actor?1:0;}
int rv_gpu_stream_vertex_bytes(void*p){return p?inkgl_stream_vertex_bytes(((Scene*)p)->gl):0;}
int rv_gpu_stream_index_bytes(void*p){return p?inkgl_stream_index_bytes(((Scene*)p)->gl):0;}
int rv_gpu_stream_allocations(void*p){return p?inkgl_stream_allocations(((Scene*)p)->gl):0;}
int rv_gpu_stream_ring_size(void*p){return p?inkgl_stream_ring_size(((Scene*)p)->gl):0;}
int rv_gpu_stream_ring_vertex_bytes(void*p){return p?inkgl_stream_ring_vertex_bytes(((Scene*)p)->gl):0;}
int rv_gpu_stream_ring_index_bytes(void*p){return p?inkgl_stream_ring_index_bytes(((Scene*)p)->gl):0;}
uint64_t rv_gpu_stream_ring_bytes(void*p){return p?inkgl_stream_ring_bytes(((Scene*)p)->gl):0;}
const char*rv_gpu_stream_ring_reason(void*p){return p?inkgl_stream_ring_reason(((Scene*)p)->gl):"no scene";}
int rv_readback(void*p,unsigned char*rgba,int stride){Scene*s=(Scene*)p;return s&&stride==s->width*4?inkgl_read_rgba(s->gl,rgba,stride*s->height):0;}
void rv_destroy(void*p){Scene*s=(Scene*)p;if(s){inkgl_destroy(s->gl);delete s;}}
}
