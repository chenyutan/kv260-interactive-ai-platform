#ifndef RUNNER_CHARACTER_H
#define RUNNER_CHARACTER_H
// Original starter assets. No GL dependency. x right, y up, z forward.
// Geometry is in metres; triangles face outward, normals are unit length.
#include "ink_gl_backend.h"
#include "runner_motion.h"
#include <vector>
#include <string>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <algorithm>
#include <array>
#include <cstring>
#include <unordered_map>
#include <limits>

namespace charactermesh {
constexpr float PI=3.14159265358979323846f;
struct V3 { float x,y,z; };
inline V3 operator+(V3 a,V3 b){return {a.x+b.x,a.y+b.y,a.z+b.z};}
inline V3 operator-(V3 a,V3 b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
inline V3 operator*(V3 a,float s){return {a.x*s,a.y*s,a.z*s};}
inline float dot(V3 a,V3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
inline V3 cross(V3 a,V3 b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
inline V3 unit(V3 a){float l=std::sqrt(dot(a,a));return l>1e-9f?a*(1/l):V3{0,1,0};}
struct Color {float r,g,b;};
struct Vertex {float x,y,z,nx,ny,nz,r,g,b;};
struct Mesh {std::vector<Vertex> vertices;std::vector<uint16_t> indices;};
inline uint16_t vertex(Mesh& m,V3 p,V3 n,Color c){
    if(m.vertices.size()>=65535)throw std::length_error("mesh exceeds uint16 index range");
    uint16_t i=static_cast<uint16_t>(m.vertices.size());
    m.vertices.push_back({p.x,p.y,p.z,n.x,n.y,n.z,c.r,c.g,c.b});return i;
}
inline void triangle(Mesh& m,V3 a,V3 b,V3 c,V3 outward,Color col){
    V3 n=unit(cross(b-a,c-a));if(dot(n,outward)<0){std::swap(b,c);n=n*(-1);}
    if(dot(cross(b-a,c-a),cross(b-a,c-a))<1e-14f)return;
    uint16_t i=vertex(m,a,n,col);vertex(m,b,n,col);vertex(m,c,n,col);
    m.indices.insert(m.indices.end(),{i,static_cast<uint16_t>(i+1),static_cast<uint16_t>(i+2)});
}
inline void quad(Mesh& m,V3 a,V3 b,V3 c,V3 d,V3 outward,Color col){
    triangle(m,a,b,c,outward,col);triangle(m,a,c,d,outward,col);
}
inline void box(Mesh& m,V3 lo,V3 hi,Color c){
    float x=lo.x,y=lo.y,z=lo.z,X=hi.x,Y=hi.y,Z=hi.z;
    quad(m,{x,y,z},{x,y,Z},{x,Y,Z},{x,Y,z},{-1,0,0},c);
    quad(m,{X,y,z},{X,Y,z},{X,Y,Z},{X,y,Z},{1,0,0},c);
    quad(m,{x,y,z},{X,y,z},{X,y,Z},{x,y,Z},{0,-1,0},c);
    quad(m,{x,Y,z},{x,Y,Z},{X,Y,Z},{X,Y,z},{0,1,0},c);
    quad(m,{x,y,z},{x,Y,z},{X,Y,z},{X,y,z},{0,0,-1},c);
    quad(m,{x,y,Z},{X,y,Z},{X,Y,Z},{x,Y,Z},{0,0,1},c);
}
// Rounded silhouette via an octagonal x/z section and tapered top/bottom.
inline void bevel_box(Mesh& m,V3 lo,V3 hi,float bevel,Color c){
    float b=std::min(bevel,std::min(hi.x-lo.x,hi.z-lo.z)*.45f);
    V3 ring[8]={{lo.x+b,0,lo.z},{hi.x-b,0,lo.z},{hi.x,0,lo.z+b},
      {hi.x,0,hi.z-b},{hi.x-b,0,hi.z},{lo.x+b,0,hi.z},
      {lo.x,0,hi.z-b},{lo.x,0,lo.z+b}};
    V3 center={(lo.x+hi.x)/2,0,(lo.z+hi.z)/2};
    for(int i=0;i<8;++i){int j=(i+1)%8;
        V3 a=ring[i],d=ring[j];a.y=d.y=lo.y;V3 B=a,C=d;B.y=C.y=hi.y;
        V3 normal=unit(V3{(a.x+d.x)*.5f-center.x,0,(a.z+d.z)*.5f-center.z});
        quad(m,a,d,C,B,normal,c);
        V3 ct=center;ct.y=hi.y;triangle(m,ct,B,C,{0,1,0},c);
        ct.y=lo.y;triangle(m,ct,d,a,{0,-1,0},c);
    }
}
inline void ellipsoid(Mesh& m,V3 center,V3 radii,Color c,int slices=10,int stacks=6){
    if(slices<3||stacks<2||radii.x<=0||radii.y<=0||radii.z<=0)throw std::invalid_argument("ellipsoid parameters");
    auto point=[&](int j,int i){float a=PI*j/stacks,t=2*PI*i/slices;
        return center+V3{radii.x*std::sin(a)*std::cos(t),radii.y*std::cos(a),radii.z*std::sin(a)*std::sin(t)};};
    // Small smooth asset meshes, independent vertices keep implementation simple.
    auto tri=[&](V3 a,V3 b,V3 d){
        if(dot(cross(b-a,d-a),cross(b-a,d-a))<1e-14f)return;
        V3 mid=(a+b+d)*(1.f/3)-center;
        if(dot(cross(b-a,d-a),mid)<0)std::swap(b,d);
        uint16_t first=static_cast<uint16_t>(m.vertices.size());
        for(V3 p:{a,b,d}){V3 q=p-center;vertex(m,p,unit({q.x/(radii.x*radii.x),q.y/(radii.y*radii.y),q.z/(radii.z*radii.z)}),c);}
        m.indices.insert(m.indices.end(),{first,static_cast<uint16_t>(first+1),static_cast<uint16_t>(first+2)});
    };
    for(int j=0;j<stacks;++j)for(int i=0;i<slices;++i){
        V3 a=point(j,i),b=point(j,i+1),d=point(j+1,i),e=point(j+1,i+1);
        tri(a,d,e);tri(a,e,b);
    }
}
// Tapered octagonal limb: pivot at y=0, extending down to -length.
inline void limb(Mesh& m,float length,float top,float bottom,Color c){
    for(int i=0;i<8;++i){float a=2*PI*i/8,b=2*PI*(i+1)/8;
        V3 p={top*std::cos(a),0,top*std::sin(a)},q={top*std::cos(b),0,top*std::sin(b)};
        V3 r={bottom*std::cos(b),-length,bottom*std::sin(b)},s={bottom*std::cos(a),-length,bottom*std::sin(a)};
        quad(m,p,q,r,s,{std::cos((a+b)/2),0,std::sin((a+b)/2)},c);
        triangle(m,{0,0,0},p,q,{0,1,0},c);triangle(m,{0,-length,0},r,s,{0,-1,0},c);
    }
}
struct Node {std::string name;int parent;V3 pivot;Mesh mesh;};
struct Rig {std::vector<Node> nodes;};
// Node transform = parent_world * translate(pivot) * local_rotation.
// The root pivot is the standing pelvis height; jump translation is external.
inline Rig make_runner(){
    const Color cream={.94f,.91f,.78f},navy={.12f,.19f,.30f},teal={.08f,.55f,.55f},
      coral={.98f,.28f,.20f},skin={.76f,.49f,.31f},hair={.12f,.10f,.12f},sole={.98f,.96f,.85f};
    Rig r;
    auto add=[&](const char* name,int parent,V3 pivot){r.nodes.push_back({name,parent,pivot,{}});return int(r.nodes.size()-1);};
    int pelvis=add("pelvis",-1,{0,.83f,0});
    bevel_box(r.nodes[pelvis].mesh,{-.22f,-.10f,-.12f},{.22f,.11f,.13f},.06f,navy);
    int torso=add("torso",pelvis,{0,.10f,0});
    bevel_box(r.nodes[torso].mesh,{-.24f,0,-.15f},{.24f,.43f,.16f},.07f,cream);
    box(r.nodes[torso].mesh,{-.24f,.02f,-.156f},{.24f,.09f,-.151f},teal);
    int pack=add("backpack",torso,{0,.19f,-.17f});
    bevel_box(r.nodes[pack].mesh,{-.16f,-.15f,-.13f},{.16f,.16f,0},.055f,teal);
    bevel_box(r.nodes[pack].mesh,{-.11f,-.13f,-.16f},{.11f,-.025f,-.12f},.025f,navy);
    box(r.nodes[pack].mesh,{-.05f,.035f,-.136f},{.05f,.085f,-.13f},coral);
    int head=add("head",torso,{0,.48f,0});
    ellipsoid(r.nodes[head].mesh,{0,.12f,.015f},{.18f,.21f,.17f},skin,12,8);
    ellipsoid(r.nodes[head].mesh,{0,.19f,-.025f},{.185f,.16f,.16f},hair,12,6);
    for(int sign:{-1,1}){
        ellipsoid(r.nodes[head].mesh,{sign*.18f,.12f,-.005f},{.04f,.085f,.08f},teal,8,6);
        ellipsoid(r.nodes[head].mesh,{sign*.209f,.12f,-.005f},{.015f,.052f,.052f},coral,8,4);
        // Jacket seam and backpack straps.
        box(r.nodes[torso].mesh,{sign*.14f-.025f,.10f,-.165f},{sign*.14f+.025f,.41f,-.155f},navy);
    }
    int scarf=add("scarf",torso,{.04f,.40f,-.12f});
    bevel_box(r.nodes[scarf].mesh,{-.18f,-.015f,-.035f},{.14f,.055f,.22f},.03f,coral);
    // A ribbon has thickness and a tapered tip; animator rotates this node gently.
    quad(r.nodes[scarf].mesh,{0,.02f,-.02f},{.12f,.02f,-.02f},{.15f,-.05f,-.42f},{.04f,-.10f,-.38f},{0,1,0},coral);
    quad(r.nodes[scarf].mesh,{0,.012f,-.02f},{.04f,-.108f,-.38f},{.15f,-.058f,-.42f},{.12f,.012f,-.02f},{0,-1,0},coral);
    for(int sign:{-1,1}){
        std::string prefix=sign<0?"left_":"right_";
        int arm=add((prefix+"upper_arm").c_str(),torso,{sign*.29f,.35f,0});
        limb(r.nodes[arm].mesh,.25f,.09f,.075f,cream);
        int fore=add((prefix+"forearm").c_str(),arm,{0,-.25f,0});
        limb(r.nodes[fore].mesh,.22f,.075f,.055f,teal);
        ellipsoid(r.nodes[fore].mesh,{0,-.245f,0},{.065f,.075f,.06f},skin,8,5);
        int thigh=add((prefix+"thigh").c_str(),pelvis,{sign*.135f,-.06f,0});
        limb(r.nodes[thigh].mesh,.36f,.105f,.075f,navy);
        int shin=add((prefix+"shin").c_str(),thigh,{0,-.36f,0});
        limb(r.nodes[shin].mesh,.30f,.075f,.055f,navy);
        int shoe=add((prefix+"shoe").c_str(),shin,{0,-.30f,0});
        bevel_box(r.nodes[shoe].mesh,{-.08f,-.09f,-.08f},{.08f,-.035f,.20f},.035f,sole);
        ellipsoid(r.nodes[shoe].mesh,{0,-.015f,.06f},{.078f,.065f,.135f},coral,8,5);
    }
    return r;
}

inline Mesh make_train(float length=6.8f,Color body={.09f,.63f,.66f}){
    if(length<3)throw std::invalid_argument("train length >= 3 required");
    Mesh m;Color dark={.07f,.16f,.24f},cream={.96f,.91f,.75f},roof={.76f,.85f,.82f},metal={.28f,.35f,.39f};
    bevel_box(m,{-.88f,.42f,0},{.88f,2.02f,length},.16f,body);
    bevel_box(m,{-.84f,1.98f,.04f},{.84f,2.19f,length-.04f},.16f,roof);
    box(m,{-.89f,.80f,.08f},{.89f,.94f,length-.08f},cream);
    bevel_box(m,{-.69f,1.20f,-.012f},{.69f,1.80f,.012f},.07f,dark);
    box(m,{-.03f,1.20f,-.019f},{.03f,1.80f,-.013f},cream);
    box(m,{-.80f,.46f,-.025f},{.80f,.60f,.025f},dark);
    for(int sign:{-1,1}){
        box(m,{sign*.62f-.09f,.99f,-.021f},{sign*.62f+.09f,1.08f,-.013f},{1.f,.88f,.40f});
        float a=sign<0?-.893f:.881f,b=a+.012f;
        for(float z=.48f;z<length-.65f;z+=1.14f){
            box(m,{a,1.22f,z},{b,1.77f,z+.78f},dark);
            box(m,{a-.001f,1.72f,z+.05f},{b+.001f,1.75f,z+.72f},{.39f,.73f,.79f});
        }
        // Bogies and wheels are outside the body, visible in three-quarter view.
        for(float z:{1.1f,length-1.1f}){
            box(m,{sign*.72f-.09f,.21f,z-.45f},{sign*.72f+.09f,.47f,z+.45f},dark);
            for(float d:{-.27f,.27f})ellipsoid(m,{sign*.87f,.26f,z+d},{.075f,.18f,.18f},metal,8,5);
        }
        box(m,{a-.001f,1.0f,length*.48f},{b+.001f,1.92f,length*.48f+.025f},cream);
    }
    for(float z:{length*.28f,length*.68f}){
        bevel_box(m,{-.35f,2.18f,z-.32f},{.35f,2.28f,z+.32f},.06f,metal);
        for(int i=0;i<4;++i)box(m,{-.28f,2.28f,z-.22f+i*.14f},{.28f,2.29f,z-.20f+i*.14f},dark);
    }
    return m;
}

inline Mesh make_coin(){
    Mesh m;Color gold={1,.68f,.08f},rim={.68f,.33f,.025f},face={1,.84f,.24f};
    // Disk initially in x/y plane, rotating around y in the renderer.
    for(int i=0;i<12;++i){float a=2*PI*i/12,b=2*PI*(i+1)/12;
        V3 p={.22f*std::cos(a),.22f*std::sin(a),-.035f},q={.22f*std::cos(b),.22f*std::sin(b),-.035f};
        V3 r=q,s=p;r.z=s.z=.035f;
        quad(m,p,q,r,s,{std::cos((a+b)/2),std::sin((a+b)/2),0},rim);
        triangle(m,{0,0,-.035f},q,p,{0,0,-1},gold);triangle(m,{0,0,.035f},s,r,{0,0,1},face);
    }
    box(m,{-.035f,-.12f,-.045f},{.035f,.12f,.045f},face);
    box(m,{-.10f,-.035f,-.046f},{.10f,.035f,.046f},face);
    return m;
}
} // namespace charactermesh

namespace charactermesh {
struct PoseMatrix {V3 u,v,w,t;};
inline PoseMatrix identity(){return {{1,0,0},{0,1,0},{0,0,1},{0,0,0}};}
inline V3 direction(const PoseMatrix& m,V3 p){return m.u*p.x+m.v*p.y+m.w*p.z;}
inline V3 position(const PoseMatrix& m,V3 p){return direction(m,p)+m.t;}
inline PoseMatrix combine(const PoseMatrix& a,const PoseMatrix& b){return {direction(a,b.u),direction(a,b.v),direction(a,b.w),position(a,b.t)};}
inline PoseMatrix rotation(float ax,float ay,float az){
    float sx=std::sin(ax),cx=std::cos(ax),sy=std::sin(ay),cy=std::cos(ay),sz=std::sin(az),cz=std::cos(az);
    PoseMatrix x={{1,0,0},{0,cx,sx},{0,-sx,cx},{0,0,0}};
    PoseMatrix y={{cy,0,-sy},{0,1,0},{sy,0,cy},{0,0,0}};
    PoseMatrix z={{cz,sz,0},{-sz,cz,0},{0,0,1},{0,0,0}};
    return combine(z,combine(y,x));
}
inline float clamp01(float f){return std::max(0.f,std::min(1.f,f));}
inline float smooth(float f){f=clamp01(f);return f*f*(3-2*f);}

// Only positions/colors are emitted; static geometry is constructed once.
// x is WORLD metres, jump_height is the engine's normalized [0,1] value.
// slide_phase < 0 means not sliding; invulnerable is remaining time, or 0.
#include "runner_costume.h"
// Preserve exact float bits, including signed zero and normal/material seams.
struct VertexBits {
    std::array<uint32_t,9> words;
    bool operator==(const VertexBits& other)const{return words==other.words;}
};
struct VertexBitsHash {
    size_t operator()(const VertexBits& key)const{
        size_t h=2166136261u;for(uint32_t w:key.words){h^=w;h*=16777619u;}return h;
    }
};
inline void deduplicate_mesh(Mesh& mesh){
    static_assert(sizeof(Vertex)==9*sizeof(float),"vertex key must contain exactly nine floats");
    if(mesh.vertices.size()>65535)throw std::length_error("character source vertex range exceeds uint16 contract");
    if(mesh.indices.size()%3)throw std::invalid_argument("character index stream is not triangles");
    for(uint16_t i:mesh.indices)if(i>=mesh.vertices.size())throw std::out_of_range("character mesh index");
    std::unordered_map<VertexBits,uint16_t,VertexBitsHash> lookup;lookup.reserve(mesh.vertices.size());
    std::vector<Vertex> unique;unique.reserve(mesh.vertices.size());
    std::vector<uint16_t> remap;remap.reserve(mesh.vertices.size());
    for(const Vertex&v:mesh.vertices){
        const float values[]={v.x,v.y,v.z,v.nx,v.ny,v.nz,v.r,v.g,v.b};
        for(float f:values)if(!std::isfinite(f))throw std::invalid_argument("non-finite character mesh vertex");
        VertexBits key;std::memcpy(key.words.data(),&v,sizeof(v));
        auto found=lookup.find(key);
        if(found!=lookup.end()){remap.push_back(found->second);continue;}
        if(unique.size()>=65535)throw std::length_error("character unique vertex range exceeds uint16 contract");
        uint16_t index=static_cast<uint16_t>(unique.size());unique.push_back(v);lookup.emplace(key,index);remap.push_back(index);
    }
    // First-occurrence order is retained even for unreferenced vertices, so the
    // min-foot / height-cap reductions keep their original order and extrema.
    for(uint16_t&i:mesh.indices)i=remap[i];mesh.vertices.swap(unique);
}
// Keep the original factory initialization expression and its own static storage.
// Cache construction copies the completed immutable rig; it never enters or
// changes the arithmetic context of make_courier().
inline const Rig& runner_rig(){
    static const Rig rig=make_courier();
    return rig;
}
inline const Rig& character_cached_rig(){
    static const Rig cached=[](){Rig r=runner_rig();
        if(r.nodes.size()!=15)throw std::length_error("character rig node count must be 15");
        for(auto&node:r.nodes)deduplicate_mesh(node.mesh);return r;
    }();
    return cached;
}
struct CharacterScratch {
    std::vector<InkGlVertex> transformed;
    bool in_use=false;
    CharacterScratch(){size_t largest=0;for(const auto&node:character_cached_rig().nodes)largest=std::max(largest,node.mesh.vertices.size());transformed.resize(largest);}
};
inline CharacterScratch& character_scratch(){static thread_local CharacterScratch scratch;return scratch;}
inline void warm_character_scratch(){(void)character_scratch();}
struct CharacterScratchLease {
    CharacterScratch& scratch;
    explicit CharacterScratchLease(CharacterScratch&s):scratch(s){if(s.in_use)throw std::logic_error("reentrant character append on one thread");s.in_use=true;}
    ~CharacterScratchLease(){scratch.in_use=false;}
    CharacterScratchLease(const CharacterScratchLease&)=delete;
    CharacterScratchLease& operator=(const CharacterScratchLease&)=delete;
};

struct CharacterPose {
    PoseMatrix transforms[15];
    float min_foot=1e9f,height_scale=1,max_height=0;
};
inline CharacterPose character_pose(const runner_motion::Pose& motion){
    const Rig& rig=runner_rig();CharacterPose result;
    for(size_t i=0;i<rig.nodes.size();++i){
        const Node& node=rig.nodes[i];const auto& j=motion.joint[i];
        V3 pivot=node.pivot;
        if(i==0){pivot.y=motion.pelvis_y;pivot.x+=motion.sway;}
        PoseMatrix local=rotation(j.x,j.y,j.z);local.t=pivot;
        result.transforms[i]=node.parent<0?local:combine(result.transforms[node.parent],local);
        if(i==9||i==14)for(const auto&v:node.mesh.vertices)
            result.min_foot=std::min(result.min_foot,position(result.transforms[i],{v.x,v.y,v.z}).y);
    }
    if(motion.height_cap>0){
        for(size_t i=0;i<rig.nodes.size();++i)for(const auto&v:rig.nodes[i].mesh.vertices)
            result.max_height=std::max(result.max_height,(position(result.transforms[i],{v.x,v.y,v.z}).y-result.min_foot+.012f)*1.14f);
        result.height_scale=std::min(1.f,motion.height_cap/std::max(.001f,result.max_height));
    }
    return result;
}

inline void append_character(std::vector<InkGlVertex>& out,float x,float jump_height,
                             float slide_phase,float distance,float clock,float invulnerable,
                             const runner_motion::Pose* supplied=nullptr){
    const Rig& rig=character_cached_rig();
    runner_motion::Pose fallback;
    if(!supplied){
        fallback=runner_motion::running_pose(runner_motion::wrap(distance/6.67f),1.65f,0);
        if(jump_height>0)fallback=runner_motion::jumping_pose(std::asin(clamp01(jump_height))/PI,fallback.phase,0);
        if(slide_phase>=0){fallback=runner_motion::sliding_pose(fallback.phase,0);fallback.height_cap=.97f;}
    }
    const auto& motion=supplied?*supplied:fallback;
    CharacterPose posed=character_pose(motion);const auto&transforms=posed.transforms;
    float min_foot=posed.min_foot,jump=clamp01(jump_height);
    V3 sun=unit({-.4f,.8f,-.3f});
    float pulse=invulnerable>0?.30f+.25f*std::sin(clock*32):0;
    auto& scratch=character_scratch();CharacterScratchLease lease(scratch);
    for(size_t i=0;i<rig.nodes.size();++i){
        const Mesh& mesh=rig.nodes[i].mesh;const auto& transform=transforms[i];
        for(size_t index=0;index<mesh.vertices.size();++index){const auto& v=mesh.vertices[index];
            // Rig normals are unit vectors and joint matrices are orthonormal.
            // Avoid a redundant per-vertex square root at animation frame rate.
            V3 p=position(transform,{v.x,v.y,v.z}),n=direction(transform,{v.nx,v.ny,v.nz});
            float key=std::max(0.f,dot(n,sun));
            // Sky fill keeps the face readable while the world sun is behind it.
            // This is vertex colour only, with no runtime texture or draw call.
            // A warm face fill preserves expression, while hair and clothing
            // keep enough form shading to read as volumes rather than cutouts.
            const bool face=i==3&&v.r>.35f;
            float light=face?.82f+.18f*key:.58f+.40f*key;
            if(i==3&&!face)light=.50f+.50f*key;
            if(i==2 && v.r>.95f)light=1.f;
            float r=v.r*light+.035f*(1-key),g=v.g*light+.045f*(1-key),b=v.b*light+.065f*(1-key);
            // Warm bounce from the lantern on the back-facing cloak and shoulder.
            if(i==1 && v.z<-.15f){r=std::min(1.f,r+.10f);g=std::min(1.f,g+.035f);}
            r=r*(1-pulse)+pulse;g=g*(1-pulse)+pulse;b=b*(1-pulse)+pulse;
            scratch.transformed[index]={x+p.x*1.16f,(p.y-min_foot+.012f)*1.14f*posed.height_scale+motion.foot_lift+jump*1.35f,p.z*1.10f,r,g,b,1};
        }
        for(uint16_t index:mesh.indices)out.push_back(scratch.transformed[index]);
    }
}
} // namespace charactermesh

#include "runner_imported_character.h"

inline void append_character(std::vector<InkGlVertex>& out,float x,float jump_height,
                             float slide_phase,float distance,float clock,float invulnerable,
                             const runner_motion::Pose* motion=nullptr){
    importedcourier::append(out,x,jump_height,slide_phase,distance,clock,invulnerable,motion);
}
#endif




