#ifndef INK_GL_PACKED_VERTEX_H
#define INK_GL_PACKED_VERTEX_H
// Internal immutable-VBO representation; the public CPU vertex stays 36 bytes.
#include "ink_gl_backend.h"
#include <cstddef>
#include <cmath>
#include <cstdint>
#include <type_traits>
namespace inkgl_packing {
struct StaticVertex {float x,y,z;uint8_t r,g,b,a;uint16_t u,v;};
static_assert(sizeof(InkGlVertex)==36,"public CPU vertex ABI changed");
static_assert(std::is_standard_layout<StaticVertex>::value,"packed vertex must be standard layout");
static_assert(sizeof(StaticVertex)==20,"static VBO stride must be 20 bytes");
static_assert(offsetof(StaticVertex,x)==0&&offsetof(StaticVertex,r)==12&&offsetof(StaticVertex,u)==16&&offsetof(StaticVertex,v)==18,"static VBO offsets changed");
enum class Eligibility {packable,float_required,nonfinite};
inline Eligibility inspect(const InkGlVertex*v,int n){
    bool outside=false;
    for(int i=0;i<n;++i){const auto&a=v[i];
        if(!std::isfinite(a.x)||!std::isfinite(a.y)||!std::isfinite(a.z))return Eligibility::nonfinite;
        const float values[]={a.r,a.g,a.b,a.a,a.u,a.v};
        for(float f:values){if(!std::isfinite(f))return Eligibility::nonfinite;if(f<0.f||f>1.f)outside=true;}
    }
    // Do not clip HDR/negative colors or out-of-range UVs before interpolation.
    return outside?Eligibility::float_required:Eligibility::packable;
}
inline uint8_t unorm8(float f){return static_cast<uint8_t>(double(f)*255.0+.5);}
inline uint16_t unorm16(float f){return static_cast<uint16_t>(double(f)*65535.0+.5);}
// Caller must inspect first; this helper never clamps silently.
inline StaticVertex pack(const InkGlVertex&a){return {a.x,a.y,a.z,unorm8(a.r),unorm8(a.g),unorm8(a.b),unorm8(a.a),unorm16(a.u),unorm16(a.v)};}
}
#endif
