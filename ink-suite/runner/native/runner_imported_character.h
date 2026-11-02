#ifndef RUNNER_IMPORTED_CHARACTER_H
#define RUNNER_IMPORTED_CHARACTER_H
// Optional authored-mesh candidate. Original rig/motion retained, CC0 source
// and provenance in runner/character_source/quaternius-hoodie/.
// Included after rig/matrix declarations by runner_character.h.
#include "runner_imported_data.h"
namespace importedcourier {
inline charactermesh::PoseMatrix affine(const float*m){return {{m[0],m[4],m[8]},{m[1],m[5],m[9]},{m[2],m[6],m[10]},{m[3],m[7],m[11]}};}
inline charactermesh::PoseMatrix linear(const float*m){return {{m[0],m[3],m[6]},{m[1],m[4],m[7]},{m[2],m[5],m[8]},{0,0,0}};}
inline const charactermesh::Mesh& straps(){
 static const charactermesh::Mesh mesh=[](){using namespace charactermesh;Mesh m;
  for(int side:{-1,1}){
   V3 line[]={{side*.145f,.395f,-.112f},{side*.145f,.29f,-.128f},{side*.13f,.18f,-.130f},{side*.12f,.08f,-.128f}};
   for(int k=0;k<3;++k){V3 a=line[k],b=line[k+1],u{.014f,0,0};quad(m,a-u,b-u,b+u,a+u,{0,0,-1},{.11f,.21f,.25f});quad(m,a+u,b+u,b-u,a-u,{0,0,1},{.11f,.21f,.25f});}
  }return m;}();return mesh;
}
inline const charactermesh::Mesh& accessory_mesh(const charactermesh::Rig&rig,int node){return node==1?straps():rig.nodes[node].mesh;}
inline charactermesh::V3 accessory_position(const charactermesh::Vertex&v,int node){return {v.x,v.y,v.z+(node==2?.07f:0)};}

constexpr size_t body_vertex_count=sizeof(vertices)/sizeof(vertices[0]);
inline std::array<InkGlVertex,body_vertex_count>& body_scratch(){
 static thread_local std::array<InkGlVertex,body_vertex_count> storage;return storage;
}
struct Prepared {charactermesh::PoseMatrix pose[15];float x,minfoot,scale,foot_lift,jump,pulse;};
inline const charactermesh::Mesh& indexed_accessory_mesh(int node){
 if(node==1){static const charactermesh::Mesh mesh=[](){auto m=straps();charactermesh::deduplicate_mesh(m);return m;}();return mesh;}
 return charactermesh::character_cached_rig().nodes[node].mesh;
}
inline size_t indexed_vertex_count(){size_t n=body_vertex_count;for(int node:{1,2,4})n+=indexed_accessory_mesh(node).vertices.size();return n;}
inline size_t triangle_index_count(){size_t n=sizeof(indices)/sizeof(indices[0]);for(int node:{1,2,4})n+=indexed_accessory_mesh(node).indices.size();return n;}
inline void warm_indexed_character(){(void)body_scratch();(void)indexed_vertex_count();}
inline Prepared prepare(float x,float jump_height,float slide_phase,float distance,float clock,float invulnerable,const runner_motion::Pose*supplied){
 using namespace charactermesh;
 runner_motion::Pose fallback;
 if(!supplied){fallback=runner_motion::running_pose(runner_motion::wrap(distance/6.67f),1.65f,0);if(jump_height>0)fallback=runner_motion::jumping_pose(std::asin(clamp01(jump_height))/PI,fallback.phase,0);if(slide_phase>=0){fallback=runner_motion::sliding_pose(fallback.phase,0);fallback.height_cap=.97f;}}
 const auto&motion=supplied?*supplied:fallback;const auto&rig=runner_rig();Prepared result;PoseMatrix*pose=result.pose;PoseMatrix skin[15],normals[15];
 for(int i=0;i<15;++i){const auto&node=rig.nodes[i];const auto&j=motion.joint[i];auto pivot=node.pivot;if(i==0){pivot.y=motion.pelvis_y;pivot.x+=motion.sway;}PoseMatrix local=rotation(j.x,j.y,j.z);local.t=pivot;pose[i]=node.parent<0?local:combine(pose[node.parent],local);skin[i]=combine(pose[i],affine(bind[i]));normals[i]=combine(pose[i],linear(normal_bind[i]));}
 // Trivial TLS storage has no allocation/destructor and isolates render threads.
 auto&scratch=body_scratch();
 float minfoot=1e9f,maxheight=-1e9f;const V3 sun=unit({-.4f,.8f,-.3f});float pulse=invulnerable>0?.30f+.25f*std::sin(clock*32):0;
 for(size_t i=0;i<scratch.size();++i){const auto&v=vertices[i];V3 p{0,0,0},n{0,0,0};
  for(int k=0;k<4;++k)if(v.w[k]>0){p=p+position(skin[v.bone[k]],{v.p[0],v.p[1],v.p[2]})*v.w[k];n=n+direction(normals[v.bone[k]],{v.n[0],v.n[1],v.n[2]})*v.w[k];}
  n=unit({n.x/1.16f,n.y/1.14f,n.z/1.10f});float key=std::max(0.f,dot(n,sun)),light=(v.flags&2)? .82f+.18f*key:.58f+.40f*key;
  float r=v.c[0]*light+.035f*(1-key),g=v.c[1]*light+.045f*(1-key),b=v.c[2]*light+.065f*(1-key);
  scratch[i]={p.x,p.y,p.z,r*(1-pulse)+pulse,g*(1-pulse)+pulse,b*(1-pulse)+pulse,1};
  if(v.flags&1)minfoot=std::min(minfoot,p.y);maxheight=std::max(maxheight,p.y);
 }
 // Accessories participate in the cap before any vertices are emitted.
 for(int nodeIndex:{1,2,4})for(const auto&v:accessory_mesh(rig,nodeIndex).vertices)maxheight=std::max(maxheight,position(pose[nodeIndex],accessory_position(v,nodeIndex)).y);
 float height=(maxheight-minfoot+.012f)*1.14f,scale=motion.height_cap>0?std::min(1.f,motion.height_cap/std::max(.001f,height)):1.f;
 for(auto&v:scratch){v.x=x+v.x*1.16f;v.y=(v.y-minfoot+.012f)*1.14f*scale+motion.foot_lift+clamp01(jump_height)*1.35f;v.z*=1.10f;}

 result.x=x;result.minfoot=minfoot;result.scale=scale;result.foot_lift=motion.foot_lift;result.jump=clamp01(jump_height);result.pulse=pulse;return result;
}
inline InkGlVertex transform_accessory(const charactermesh::Vertex&v,int nodeIndex,const Prepared&prepared){
 using namespace charactermesh;const auto&pose=prepared.pose;const V3 sun=unit({-.4f,.8f,-.3f});
 float x=prepared.x,minfoot=prepared.minfoot,scale=prepared.scale,pulse=prepared.pulse;
 V3 p=position(pose[nodeIndex],accessory_position(v,nodeIndex)),n=direction(pose[nodeIndex],{v.nx,v.ny,v.nz});
 n=unit({n.x/1.16f,n.y/1.14f,n.z/1.10f});float key=std::max(0.f,dot(n,sun)),light=nodeIndex==2&&v.r>.95f?1.f:.58f+.40f*key;
 float r=v.r*light+.035f*(1-key),g=v.g*light+.045f*(1-key),b=v.b*light+.065f*(1-key);
 return {x+p.x*1.16f,(p.y-minfoot+.012f)*1.14f*scale+prepared.foot_lift+prepared.jump*1.35f,p.z*1.10f,r*(1-pulse)+pulse,g*(1-pulse)+pulse,b*(1-pulse)+pulse,1};
}
// Compatibility stream: preserves the original triangle and vertex ordering.
inline void append(std::vector<InkGlVertex>&out,float x,float jump_height,float slide_phase,float distance,float clock,float invulnerable,const runner_motion::Pose*supplied=nullptr){
 auto prepared=prepare(x,jump_height,slide_phase,distance,clock,invulnerable,supplied);const auto&scratch=body_scratch();const auto&rig=charactermesh::runner_rig();
 for(unsigned short i:indices)out.push_back(scratch[i]);
 for(int node:{1,2,4}){const auto&mesh=accessory_mesh(rig,node);for(unsigned short i:mesh.indices)out.push_back(transform_accessory(mesh.vertices[i],node,prepared));}
}
// Indexed stream for the native GLES2 path. Caller reserves the two capacities
// reported above, clears/reuses its buffers, and may prewarm once. No allocation
// is performed internally after warmup. Existing output vertices are supported.
inline void append_indexed(std::vector<InkGlVertex>&out,std::vector<uint16_t>&out_indices,float x,float jump_height,float slide_phase,float distance,float clock,float invulnerable,const runner_motion::Pose*supplied=nullptr){
 if(out.size()+indexed_vertex_count()>65535)throw std::length_error("indexed character exceeds uint16 range");
 auto prepared=prepare(x,jump_height,slide_phase,distance,clock,invulnerable,supplied);const auto&scratch=body_scratch();
 size_t base=out.size();for(const auto&v:scratch)out.push_back(v);for(unsigned short i:indices)out_indices.push_back(static_cast<uint16_t>(base+i));
 for(int node:{1,2,4}){const auto&mesh=indexed_accessory_mesh(node);base=out.size();for(const auto&v:mesh.vertices)out.push_back(transform_accessory(v,node,prepared));for(unsigned short i:mesh.indices)out_indices.push_back(static_cast<uint16_t>(base+i));}
}
}
#endif
