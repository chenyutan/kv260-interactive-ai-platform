#define MESA_EGL_NO_X11_HEADERS 1
#include "ink_gl_backend.h"
#include "ink_gl_packed_vertex.h"
#include "ink_gl_index_mesh.h"
#include "runner_material_mips.h"
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <dlfcn.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>
#include <unordered_map>
#include <cstdlib>
#include <cmath>

// BEGIN EXACT DIAGNOSTIC HUD SUPPORT HELPER
#ifndef INK_GL_HUD_SUPPORT_H
#define INK_GL_HUD_SUPPORT_H
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstring>
namespace inkgl_hud {
struct Rect {int x=0,y=0,w=0,h=0;};
struct Support {
    // Monotone patch union: clearing content cannot cause an unsafe omission.
    std::array<int,4096> left{},right{};
    std::array<Rect,4> rectangles{};
    int width=0,height=0,output_width=0,output_height=0,count=0;
    bool valid=false,dirty=true;
    void invalidate(){valid=false;dirty=true;}
    void reset(int w,int h){width=w;height=h;left.fill(w);right.fill(-1);valid=true;dirty=true;}
    void include(const unsigned char*p,int x,int y,int w,int h,int stride){
        if(!valid)return;
        for(int row=0;row<h;++row){const auto*line=p+size_t(row)*stride;int lo=w,hi=-1;
            for(int col=0;col<w;++col){uint32_t rgba;std::memcpy(&rgba,line+size_t(col)*4,4);if(rgba){lo=std::min(lo,col);hi=col;}}
            if(hi>=0){left[y+row]=std::min(left[y+row],x+lo);right[y+row]=std::max(right[y+row],x+hi);}
        }dirty=true;
    }
    void plan(int W,int H){
        if(!dirty&&output_width==W&&output_height==H)return;
        output_width=W;output_height=H;dirty=false;count=0;
        if(!valid||W<1||H<1||width<1||height<1)return;
        // Eight texels exceed linear-filter radius and target mediump UV
        // precision error at max 4096 texture size; two screen pixels also
        // cover MSAA sample locations and conservative integer rounding.
        const int halo=8;
        for(int band=0;band<4;++band){int b0=int(int64_t(H)*band/4),b1=int(int64_t(H)*(band+1)/4);
            int x0=W,x1=0,y0=H,y1=0;
            for(int row=0;row<height;++row)if(right[row]>=left[row]){
                int t=std::max(0,row-halo),b=std::min(height,row+halo+1);
                int sy0=std::max(b0,int(int64_t(t)*H/height)-2);
                int sy1=std::min(b1,int((int64_t(b)*H+height-1)/height)+2);
                if(sy1<=sy0)continue;
                int l=std::max(0,left[row]-halo),r=std::min(width,right[row]+halo+1);
                x0=std::min(x0,std::max(0,int(int64_t(l)*W/width)-2));
                x1=std::max(x1,std::min(W,int((int64_t(r)*W+width-1)/width)+2));
                y0=std::min(y0,sy0);y1=std::max(y1,sy1);
            }
            // GL origin is bottom-left; bands are disjoint before/after flip.
            if(x1>x0&&y1>y0)rectangles[count++]={x0,H-y1,x1-x0,y1-y0};
        }
        int64_t area=0;for(int i=0;i<count;++i)area+=int64_t(rectangles[i].w)*rectangles[i].h;
        // Avoid extra draws when there is little conservative area to save.
        if(area*5>=int64_t(W)*H*4){count=1;rectangles[0]={0,0,W,H};}
    }
};
}
#endif
// END EXACT DIAGNOSTIC HUD SUPPORT HELPER

// Minimal public Xlib ABI: runtime-only dependency, no X11 development package required.
struct _XDisplay; typedef _XDisplay Display;
typedef unsigned long Window; typedef unsigned long VisualID;
struct Visual {void *ext_data; VisualID visualid; int c_class; unsigned long red_mask,green_mask,blue_mask; int bits_per_rgb,map_entries;};
struct XWindowAttributes {int x,y,width,height,border_width,depth; Visual *visual; Window root; int c_class,bit_gravity,win_gravity,backing_store; unsigned long backing_planes,backing_pixel; int save_under; unsigned long colormap; int map_installed,map_state; long all_event_masks,your_event_mask,do_not_propagate_mask; int override_redirect; void *screen;};

#define GL_FUNCS(X) \
 X(glScissor) X(glGetString) X(glGetError) X(glViewport) X(glClearColor) X(glClear) X(glEnable) X(glDisable) X(glDepthFunc) X(glDepthMask) X(glBlendFunc) X(glPixelStorei) \
 X(glGenFramebuffers) X(glBindFramebuffer) X(glDeleteFramebuffers) X(glCheckFramebufferStatus) X(glFramebufferTexture2D) X(glFramebufferRenderbuffer) X(glGetFramebufferAttachmentParameteriv) X(glGenRenderbuffers) X(glBindRenderbuffer) X(glDeleteRenderbuffers) X(glRenderbufferStorage) X(glGetRenderbufferParameteriv) \
 X(glCreateShader) X(glShaderSource) X(glCompileShader) X(glGetShaderiv) X(glGetShaderInfoLog) X(glDeleteShader) X(glCreateProgram) X(glAttachShader) X(glBindAttribLocation) X(glLinkProgram) X(glGetProgramiv) X(glGetProgramInfoLog) X(glDeleteProgram) X(glUseProgram) \
 X(glGetUniformLocation) X(glUniformMatrix4fv) X(glUniform1i) X(glUniform1f) X(glUniform2f) X(glUniform3fv) X(glEnableVertexAttribArray) X(glDisableVertexAttribArray) X(glVertexAttribPointer) X(glDrawArrays) X(glDrawElements) \
 X(glGenBuffers) X(glBindBuffer) X(glBufferData) X(glBufferSubData) X(glDeleteBuffers) X(glGenTextures) X(glBindTexture) X(glTexParameteri) X(glTexImage2D) X(glTexSubImage2D) X(glActiveTexture) X(glDeleteTextures) X(glFinish) X(glFlush) X(glReadPixels)
#define EGL_FUNCS(X) X(eglGetDisplay) X(eglInitialize) X(eglGetConfigs) X(eglGetConfigAttrib) X(eglBindAPI) X(eglCreateContext) X(eglCreateWindowSurface) X(eglCreatePbufferSurface) X(eglMakeCurrent) X(eglGetError) X(eglQueryString) X(eglGetProcAddress) X(eglSwapInterval) X(eglSwapBuffers) X(eglDestroySurface) X(eglDestroyContext) X(eglTerminate)
struct Api {
#define DECL(n) decltype(&::n) n=nullptr;
 GL_FUNCS(DECL) EGL_FUNCS(DECL)
 decltype(&::glCompressedTexImage2D) glCompressedTexImage2D=nullptr;
 void (*glRenderbufferStorageMultisampleEXT)(GLenum,GLsizei,GLenum,GLsizei,GLsizei)=nullptr;
 void (*glFramebufferTexture2DMultisampleEXT)(GLenum,GLenum,GLenum,GLuint,GLint,GLsizei)=nullptr;
#undef DECL
};
struct Context {
 std::string effective_flags_snapshot;
 Api a; void *lib=nullptr,*xlib=nullptr; Display *xd=nullptr; Window window=0;
 Display* (*XOpenDisplay)(const char*)=nullptr;
 int (*XCloseDisplay)(Display*)=nullptr;
 int (*XGetWindowAttributes)(Display*,Window,XWindowAttributes*)=nullptr;
 EGLDisplay display=EGL_NO_DISPLAY; EGLContext context=EGL_NO_CONTEXT; EGLSurface surface=EGL_NO_SURFACE; EGLConfig config=nullptr;
 GLuint program=0,flat_program=0,hud_program=0,vbo=0,hud_vbo=0,stream_ebo=0,actor_vbo=0,sky_vbo=0,texture=0,material_texture=0;
 GLint mvp=-1,fog_affine=-1,fog_rgb=-1,opacity_uniform=-1,hud_sampler=-1,hud_swap=-1,material_sampler=-1;
 GLint flat_mvp=-1,flat_fog_affine=-1,flat_fog_rgb=-1,flat_opacity=-1;
 int width=0,height=0,hud_width=0,hud_height=0,samples=0; size_t vbo_capacity=0; bool offscreen=false;
 size_t stream_index_capacity=0,actor_capacity=0;int stream_allocations=0;bool split_streams=false,sky_ready=false;float sky_colors[6]{};
 struct ActorStreamSlot{GLuint vertex=0,index=0;size_t vertex_capacity=0,index_capacity=0;};
 ActorStreamSlot actor_ring_extra[2];int stream_ring_size=1,stream_ring_next=0,stream_ring_selected=0;
 std::string stream_ring_reason="ring1 requested";
 int texture_width=0,texture_height=0,hud_allocations=0;bool hud_bgra=false;
 // 0: original MSAA-only path; 1: resolve-only diagnostic; 2: edge5.
 // The world is filtered before the original sharp HUD compositing pass.
 int post_requested=0,post_mode=0,post_width=0,post_height=0,post_samples=0,post_depth_bits=0;
 bool post_supported=false;
 GLuint post_program=0,post_texture=0,post_fbo=0,post_depth=0;
 GLint post_sampler=-1,post_pixel=-1,post_enabled=-1;
 std::string post_reason;
 struct MeshRecord{int count;bool textured;bool packed;GLuint indices=0;int unique_vertices=0;
  GLuint flat_indices=0,textured_indices=0;int flat_count=0,textured_count=0,split_skip=0;size_t vertex_bytes=0,index_bytes=0;
 };
 bool static_split=false;int static_draw_calls=0,static_flat_vertices=0,static_textured_vertices=0;
 bool static_packed=true;bool mediump_varyings=false;bool static_indexed=false;
 bool geometry_cache=false,known_geometry_attribs=false,known_geometry_depth=false,cached_geometry_depth=true;
 bool known_geometry_blend=false,cached_geometry_blend=false,known_geometry_blend_func=false;
 struct GeometryUniforms{bool fog=false,opacity=false;float cached_opacity=1;};GeometryUniforms geometry_flat,geometry_textured;
 bool hud_scissor=true;inkgl_hud::Support hud_support;int hud_viewport_width=0,hud_viewport_height=0;
 // Cache only within an uninterrupted backend-owned rendering epoch.
 bool state_cache=true,known_program=false,known_active0=false,known_texture0=false;
 GLuint cached_program=0,cached_texture0=0;
 bool material_sampler_zero=false,hud_sampler_zero=false;
 bool material_rgba8=false,material_allow_etc1=true,material_has_etc1=false;int material_storage_bits=0;
 int material_width=0,material_height=0,material_mip_levels=0,material_mip_bytes=0,material_mip_upload_bytes=0;
 bool material_mips_requested=false;std::vector<unsigned char>material_etc1_base;std::string material_mip_reason="not requested";
 bool blend=false;float opacity=1;std::unordered_map<GLuint,MeshRecord> meshes;
 float fog_start=0,fog_end=0,fog_scale=0,fog_bias=0,fog_color[3]={.8f,.9f,1.f};
 std::string error,info; std::vector<unsigned char> packed_hud;
};
static const float identity[16]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
// ARM's two window-system builds cannot safely be loaded sequentially in one process.
// Normal application and offscreen QA already run separately; reject a mixed caller.
static int process_platform=-1;
static int fail(Context*c,const char*message){if(c)c->error=message;return 0;}
static int egl_fail(Context*c,const char*what){char b[200];std::snprintf(b,sizeof b,"%s (EGL 0x%x)",what,c->a.eglGetError());return fail(c,b);}
static void invalidate_state(Context*c){
 if(!c)return;
 c->known_program=c->known_active0=c->known_texture0=false;
 c->known_geometry_attribs=c->known_geometry_depth=c->known_geometry_blend=c->known_geometry_blend_func=false;
 c->geometry_flat.fog=c->geometry_flat.opacity=c->geometry_textured.fog=c->geometry_textured.opacity=false;
 c->material_sampler_zero=c->hud_sampler_zero=false;
}
static void use_program(Context*c,GLuint id){
 if(!c->state_cache||!c->known_program||c->cached_program!=id)c->a.glUseProgram(id);
 c->cached_program=id;c->known_program=true;
}
static void texture0(Context*c,GLuint id){
 if(!c->state_cache||!c->known_active0)c->a.glActiveTexture(GL_TEXTURE0);
 c->known_active0=true;
 if(!c->state_cache||!c->known_texture0||c->cached_texture0!=id)c->a.glBindTexture(GL_TEXTURE_2D,id);
 c->cached_texture0=id;c->known_texture0=true;
}
static void sampler0(Context*c,GLint location,bool&known){
 if(!c->state_cache||!known)c->a.glUniform1i(location,0);
 known=true;
}
static bool current(Context*c){
 // Context acquisition, frame begin, uploads and offscreen resize are cache
 // boundaries. Never trust a binding left by a caller or another context.
 invalidate_state(c);
 return c&&c->a.eglMakeCurrent(c->display,c->surface,c->surface,c->context);
}
static GLuint shader(Context*c,GLenum type,const char*source){auto&a=c->a;GLuint s=a.glCreateShader(type);a.glShaderSource(s,1,&source,nullptr);a.glCompileShader(s);GLint ok=0;a.glGetShaderiv(s,GL_COMPILE_STATUS,&ok);if(!ok){char err[2048]={};a.glGetShaderInfoLog(s,sizeof(err)-1,nullptr,err);c->error=std::string("shader: ")+err;a.glDeleteShader(s);return 0;}return s;}
static GLuint program(Context*c,const char*vs,const char*fs){auto&a=c->a;GLuint v=shader(c,GL_VERTEX_SHADER,vs);if(!v)return 0;GLuint f=shader(c,GL_FRAGMENT_SHADER,fs);if(!f){a.glDeleteShader(v);return 0;}GLuint p=a.glCreateProgram();a.glAttachShader(p,v);a.glAttachShader(p,f);a.glBindAttribLocation(p,0,"position");a.glBindAttribLocation(p,1,"color");a.glBindAttribLocation(p,2,"texcoord");a.glLinkProgram(p);a.glDeleteShader(v);a.glDeleteShader(f);GLint ok=0;a.glGetProgramiv(p,GL_LINK_STATUS,&ok);if(!ok){char err[2048]={};a.glGetProgramInfoLog(p,sizeof(err)-1,nullptr,err);c->error=std::string("program: ")+err;a.glDeleteProgram(p);return 0;}return p;}
static bool extension_present(const char*extensions,const char*name){
 if(!extensions||!name||!*name)return false;
 const size_t length=std::strlen(name);const char*at=extensions;
 while((at=std::strstr(at,name))){if((at==extensions||at[-1]==' ')&&(at[length]==0||at[length]==' '))return true;at+=length;}return false;
}
static void delete_post_targets(Context*c,GLuint framebuffer,GLuint depth,GLuint texture){
 auto&a=c->a;if(framebuffer)a.glDeleteFramebuffers(1,&framebuffer);if(depth)a.glDeleteRenderbuffers(1,&depth);if(texture)a.glDeleteTextures(1,&texture);
}
static int allocate_post_targets(Context*c,int width,int height){
 // Stage replacements first. Failed allocations never destroy a working target
 // and never touch either the material atlas or the independent HUD texture.
 auto&a=c->a;GLuint texture=0,framebuffer=0,depth=0;GLint color_samples=0,depth_samples=0,depth_bits=0;
 a.glGenTextures(1,&texture);texture0(c,texture);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
 a.glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
 a.glGenFramebuffers(1,&framebuffer);a.glBindFramebuffer(GL_FRAMEBUFFER,framebuffer);
 a.glFramebufferTexture2DMultisampleEXT(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,texture,0,c->samples);
 a.glGenRenderbuffers(1,&depth);a.glBindRenderbuffer(GL_RENDERBUFFER,depth);
 a.glRenderbufferStorageMultisampleEXT(GL_RENDERBUFFER,c->samples,0x81A6/*DEPTH_COMPONENT24*/,width,height);
 a.glFramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,depth);
 const GLenum status=a.glCheckFramebufferStatus(GL_FRAMEBUFFER);
 a.glGetFramebufferAttachmentParameteriv(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,0x8D6C/*TEXTURE_SAMPLES_EXT*/,&color_samples);
 a.glGetRenderbufferParameteriv(GL_RENDERBUFFER,0x8CAB/*RENDERBUFFER_SAMPLES_EXT*/,&depth_samples);
 a.glGetRenderbufferParameteriv(GL_RENDERBUFFER,GL_RENDERBUFFER_DEPTH_SIZE,&depth_bits);
 const GLenum error=a.glGetError();a.glBindFramebuffer(GL_FRAMEBUFFER,0);a.glBindRenderbuffer(GL_RENDERBUFFER,0);invalidate_state(c);
 if(!texture||!framebuffer||!depth||error!=GL_NO_ERROR||status!=GL_FRAMEBUFFER_COMPLETE||color_samples!=c->samples||depth_samples!=c->samples||depth_bits!=24){
  delete_post_targets(c,framebuffer,depth,texture);for(int i=0;i<16&&a.glGetError()!=GL_NO_ERROR;++i){}char message[200];std::snprintf(message,sizeof message,"world AA target unavailable (status 0x%x GL 0x%x color/depth samples %d/%d depth %d)",status,error,color_samples,depth_samples,depth_bits);return fail(c,message);
 }
 delete_post_targets(c,c->post_fbo,c->post_depth,c->post_texture);
 c->post_fbo=framebuffer;c->post_depth=depth;c->post_texture=texture;c->post_width=width;c->post_height=height;c->post_samples=color_samples;c->post_depth_bits=depth_bits;return 1;
}
static void init_post_pipeline(Context*c,const char*extensions){
 if(!c->post_requested){c->post_reason="explicit off";return;}
 auto&a=c->a;
 if(c->samples!=4){c->post_reason="requires original MSAA4 configuration";return;}
 if(!extension_present(extensions,"GL_EXT_multisampled_render_to_texture")){c->post_reason="multisampled-render-to-texture unavailable";return;}
 a.glRenderbufferStorageMultisampleEXT=reinterpret_cast<decltype(a.glRenderbufferStorageMultisampleEXT)>(dlsym(c->lib,"glRenderbufferStorageMultisampleEXT"));
 if(!a.glRenderbufferStorageMultisampleEXT)a.glRenderbufferStorageMultisampleEXT=reinterpret_cast<decltype(a.glRenderbufferStorageMultisampleEXT)>(a.eglGetProcAddress("glRenderbufferStorageMultisampleEXT"));
 a.glFramebufferTexture2DMultisampleEXT=reinterpret_cast<decltype(a.glFramebufferTexture2DMultisampleEXT)>(dlsym(c->lib,"glFramebufferTexture2DMultisampleEXT"));
 if(!a.glFramebufferTexture2DMultisampleEXT)a.glFramebufferTexture2DMultisampleEXT=reinterpret_cast<decltype(a.glFramebufferTexture2DMultisampleEXT)>(a.eglGetProcAddress("glFramebufferTexture2DMultisampleEXT"));
 if(!a.glRenderbufferStorageMultisampleEXT||!a.glFramebufferTexture2DMultisampleEXT){c->post_reason="multisample extension entry points unavailable";return;}
 c->post_supported=true;const std::string previous_error=c->error;
 const char*vs="attribute vec3 position; attribute vec4 color; varying vec2 uv; void main(){gl_Position=vec4(position,1.0);uv=vec2(color.x,1.0-color.y);}";
 // Authored five-tap edge filter, not canonical FXAA. Only the 3D image is
 // filtered. Local low-contrast areas are unchanged and mixing is <=35%.
 const char*fs="precision mediump float; varying vec2 uv; uniform sampler2D image; uniform vec2 pixel; uniform bool edge_filter; float lum(vec3 c){return dot(c,vec3(0.299,0.587,0.114));} void main(){vec4 c=texture2D(image,uv);if(!edge_filter){gl_FragColor=c;return;}vec3 n=texture2D(image,uv+vec2(0.0,pixel.y)).rgb;vec3 s=texture2D(image,uv-vec2(0.0,pixel.y)).rgb;vec3 e=texture2D(image,uv+vec2(pixel.x,0.0)).rgb;vec3 w=texture2D(image,uv-vec2(pixel.x,0.0)).rgb;float l=lum(c.rgb),ln=lum(n),ls=lum(s),le=lum(e),lw=lum(w);float hi=max(l,max(max(ln,ls),max(le,lw)));float lo=min(l,min(min(ln,ls),min(le,lw)));float range=hi-lo;float threshold=max(0.05,hi*0.14);float weight=0.35*clamp((range-threshold)/max(range,0.001),0.0,1.0);vec3 pair=abs(le-lw)>abs(ln-ls)?(e+w)*0.5:(n+s)*0.5;gl_FragColor=vec4(mix(c.rgb,pair,weight),c.a);}";
 c->post_program=program(c,vs,fs);
 if(c->post_program){c->post_sampler=a.glGetUniformLocation(c->post_program,"image");c->post_pixel=a.glGetUniformLocation(c->post_program,"pixel");c->post_enabled=a.glGetUniformLocation(c->post_program,"edge_filter");}
 if(!c->post_program||!allocate_post_targets(c,c->width,c->height)){
  c->post_reason=c->error.empty()?"post shader setup failed":c->error;if(c->post_program)a.glDeleteProgram(c->post_program);c->post_program=0;c->error=previous_error;return;
 }
 c->post_mode=c->post_requested;c->post_reason="active";
}
// ARM DUI0363D 2.6.10: an optional narrower geometry-to-fragment transport.
// Only color/fog varying declarations change; position arithmetic and UV do not.
static std::string mediump_geometry_varyings(const char*source){
 std::string result(source);const char*before[]={"varying vec4 v_color;","varying float v_fog;"};const char*after[]={"varying mediump vec4 v_color;","varying mediump float v_fog;"};
 for(int i=0;i<2;i++){size_t at=result.find(before[i]);if(at!=std::string::npos)result.replace(at,std::strlen(before[i]),after[i]);}return result;
}
// Optional actor-only ring. Slot zero is the exact legacy actor VBO/EBO;
// extra slots never alias it, the miscellaneous array stream, sky or HUD.
static void release_stream_ring_extras(Context*c){
 for(auto&slot:c->actor_ring_extra){
  if(slot.vertex)c->a.glDeleteBuffers(1,&slot.vertex);
  if(slot.index)c->a.glDeleteBuffers(1,&slot.index);
  slot=Context::ActorStreamSlot{};
 }
 c->stream_ring_size=1;c->stream_ring_next=c->stream_ring_selected=0;
}
static int configure_stream_ring(Context*c){
 const char*value=std::getenv("INK_GL_STREAM_RING");
 if(value&&std::strcmp(value,"1")&&std::strcmp(value,"3"))return fail(c,"INK_GL_STREAM_RING must be 1 or 3");
 if(value&&!std::strcmp(value,"1"))return 1;
 if(!c->split_streams){c->stream_ring_reason="ring3 requires split streams; using ring1";return 1;}
 auto&a=c->a;
 if(a.glGetError()!=GL_NO_ERROR)return fail(c,"base stream allocation failed before optional ring");
 bool okay=true;
 for(auto&slot:c->actor_ring_extra){
  a.glGenBuffers(1,&slot.vertex);
  if(!slot.vertex){okay=false;break;}
  a.glBindBuffer(GL_ARRAY_BUFFER,slot.vertex);
  a.glBufferData(GL_ARRAY_BUFFER,c->actor_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
  if(a.glGetError()!=GL_NO_ERROR){okay=false;break;}
  slot.vertex_capacity=c->actor_capacity;
  a.glGenBuffers(1,&slot.index);
  if(!slot.index){okay=false;break;}
  a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,slot.index);
  a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,c->stream_index_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
  if(a.glGetError()!=GL_NO_ERROR){okay=false;break;}
  slot.index_capacity=c->stream_index_capacity;
 }
 if(!okay){
  release_stream_ring_extras(c);
  for(int i=0;i<16&&a.glGetError()!=GL_NO_ERROR;++i){}
  c->stream_ring_reason="optional actor ring allocation failed; using ring1";
  return 1;
 }
 c->stream_ring_size=3;c->stream_ring_reason.clear();return 1;
}

static int init_pipeline(Context*c){
 auto&a=c->a;
 const char*vs="attribute vec3 position; attribute vec4 color; attribute vec2 texcoord; uniform mat4 mvp; uniform float opacity; uniform vec2 fog_affine; varying vec4 v_color; varying vec2 v_uv; varying float v_fog; void main(){gl_Position=mvp*vec4(position,1.0);v_color=vec4(color.rgb,color.a*opacity);v_uv=texcoord;v_fog=gl_Position.w*fog_affine.x+fog_affine.y;}";
 // Keep small material creases subordinate to the authored cartoon colors.
 // The white sentinel still produces exactly the legacy untextured color.
 const char*fs="precision mediump float; varying vec4 v_color; varying vec2 v_uv; varying float v_fog; uniform sampler2D material_atlas; uniform vec3 fog_rgb; void main(){float f=clamp(v_fog,0.0,1.0);vec3 paint=mix(vec3(1.0),texture2D(material_atlas,v_uv).rgb,0.86)*v_color.rgb;gl_FragColor=vec4(mix(paint,fog_rgb,f),v_color.a);}";
 // Legacy vertex-color program: exact white-sentinel output without a
 // texture fetch. Sky, distant painted silhouettes and pure-color FX use it.
 const char*fvs="attribute vec3 position; attribute vec4 color; uniform mat4 mvp; uniform float opacity; uniform vec2 fog_affine; varying vec4 v_color; varying float v_fog; void main(){gl_Position=mvp*vec4(position,1.0);v_color=vec4(color.rgb,color.a*opacity);v_fog=gl_Position.w*fog_affine.x+fog_affine.y;}";
 const char*ffs="precision mediump float; varying vec4 v_color; varying float v_fog; uniform vec3 fog_rgb; void main(){float f=clamp(v_fog,0.0,1.0);gl_FragColor=vec4(mix(v_color.rgb,fog_rgb,f),v_color.a);}";
 const char*hvs="attribute vec3 position; attribute vec4 color; varying vec2 uv; void main(){gl_Position=vec4(position,1.0);uv=color.xy;}";
 const char*hfs="precision mediump float; varying vec2 uv; uniform sampler2D image; uniform bool swap_rb; void main(){vec4 c=texture2D(image,uv);gl_FragColor=swap_rb?c.bgra:c;}";
 std::string medium_vs,medium_fs,medium_fvs,medium_ffs;
 if(c->mediump_varyings){medium_vs=mediump_geometry_varyings(vs);medium_fs=mediump_geometry_varyings(fs);medium_fvs=mediump_geometry_varyings(fvs);medium_ffs=mediump_geometry_varyings(ffs);vs=medium_vs.c_str();fs=medium_fs.c_str();fvs=medium_fvs.c_str();ffs=medium_ffs.c_str();}
 c->program=program(c,vs,fs);if(!c->program)return 0;c->flat_program=program(c,fvs,ffs);if(!c->flat_program)return 0;c->hud_program=program(c,hvs,hfs);if(!c->hud_program)return 0;
 c->mvp=a.glGetUniformLocation(c->program,"mvp");c->fog_affine=a.glGetUniformLocation(c->program,"fog_affine");c->fog_rgb=a.glGetUniformLocation(c->program,"fog_rgb");c->opacity_uniform=a.glGetUniformLocation(c->program,"opacity");c->material_sampler=a.glGetUniformLocation(c->program,"material_atlas");
 c->flat_mvp=a.glGetUniformLocation(c->flat_program,"mvp");c->flat_fog_affine=a.glGetUniformLocation(c->flat_program,"fog_affine");c->flat_fog_rgb=a.glGetUniformLocation(c->flat_program,"fog_rgb");c->flat_opacity=a.glGetUniformLocation(c->flat_program,"opacity");
 c->hud_sampler=a.glGetUniformLocation(c->hud_program,"image");c->hud_swap=a.glGetUniformLocation(c->hud_program,"swap_rb");
 a.glGenBuffers(1,&c->vbo);a.glGenBuffers(1,&c->hud_vbo);
 // Reserve the bounded actor/effect stream before gameplay. Rare bursts must
 // not trigger a fresh GPU allocation in the middle of a frame.
 const char*split=std::getenv("INK_GL_SPLIT_STREAMS");if(split&&std::strcmp(split,"0")&&std::strcmp(split,"1"))return fail(c,"INK_GL_SPLIT_STREAMS must be 0 or 1");c->split_streams=!split||std::strcmp(split,"1")==0;
 c->vbo_capacity=(c->split_streams?4096:65536)*sizeof(InkGlVertex);a.glBindBuffer(GL_ARRAY_BUFFER,c->vbo);
 a.glBufferData(GL_ARRAY_BUFFER,c->vbo_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
 a.glGenBuffers(1,&c->stream_ebo);a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,c->stream_ebo);
 c->stream_index_capacity=(c->split_streams?32768:65536)*sizeof(uint16_t);a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,c->stream_index_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
 if(c->split_streams){
  a.glGenBuffers(1,&c->actor_vbo);a.glBindBuffer(GL_ARRAY_BUFFER,c->actor_vbo);c->actor_capacity=8192*sizeof(InkGlVertex);
  a.glBufferData(GL_ARRAY_BUFFER,c->actor_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
  a.glGenBuffers(1,&c->sky_vbo);a.glBindBuffer(GL_ARRAY_BUFFER,c->sky_vbo);a.glBufferData(GL_ARRAY_BUFFER,6*sizeof(InkGlVertex),nullptr,GL_STATIC_DRAW);
 }
 if(!configure_stream_ring(c))return 0;
 const InkGlVertex quad[]={{-1,-1,0,0,1,0,0},{1,-1,0,1,1,0,0},{-1,1,0,0,0,0,0},{-1,1,0,0,0,0,0},{1,-1,0,1,1,0,0},{1,1,0,1,0,0,0}};
 a.glBindBuffer(GL_ARRAY_BUFFER,c->hud_vbo);a.glBufferData(GL_ARRAY_BUFFER,sizeof quad,quad,GL_STATIC_DRAW);
 a.glGenTextures(1,&c->texture);texture0(c,c->texture);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
 // Independent world texture starts white. Geometry without UV keeps its
 // existing vertex colors; loading an atlas never changes HUD texture state.
 a.glGenTextures(1,&c->material_texture);texture0(c,c->material_texture);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
 const unsigned char white[4]={255,255,255,255};a.glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,1,1,0,GL_RGBA,GL_UNSIGNED_BYTE,white);
 a.glDisable(GL_DITHER);a.glDisable(GL_CULL_FACE);a.glDepthFunc(GL_LEQUAL);a.glPixelStorei(GL_UNPACK_ALIGNMENT,1);a.glPixelStorei(GL_PACK_ALIGNMENT,1);
 return a.glGetError()==GL_NO_ERROR;
}
static void geometry_attribs_enabled(Context*c){
 if(!c->geometry_cache||!c->state_cache||!c->known_geometry_attribs){c->a.glEnableVertexAttribArray(0);c->a.glEnableVertexAttribArray(1);c->a.glEnableVertexAttribArray(2);}
 c->known_geometry_attribs=true;
}
static void attribs(Context*c){auto&a=c->a;geometry_attribs_enabled(c);a.glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,sizeof(InkGlVertex),(void*)0);a.glVertexAttribPointer(1,4,GL_FLOAT,GL_FALSE,sizeof(InkGlVertex),(void*)(3*sizeof(float)));a.glVertexAttribPointer(2,2,GL_FLOAT,GL_FALSE,sizeof(InkGlVertex),(void*)(7*sizeof(float)));}
// Used only for immutable meshes. Dynamic vertices, sky and HUD retain attribs().
static void packed_attribs(Context*c){
 auto&a=c->a;using V=inkgl_packing::StaticVertex;
 geometry_attribs_enabled(c);
 a.glVertexAttribPointer(0,3,GL_FLOAT,GL_FALSE,sizeof(V),(void*)offsetof(V,x));
 a.glVertexAttribPointer(1,4,GL_UNSIGNED_BYTE,GL_TRUE,sizeof(V),(void*)offsetof(V,r));
 a.glVertexAttribPointer(2,2,GL_UNSIGNED_SHORT,GL_TRUE,sizeof(V),(void*)offsetof(V,u));
}
static bool has_material_uv(const InkGlVertex*v,int n){
 // Immutable VBOs evaluate this once at upload. Dynamic batches stop at their
 // first real UV; white-only batches are small shadows/FX, not whole cities.
 for(int i=0;i<n;++i)if(v[i].u!=0.f||v[i].v!=0.f)return true;
 return false;
}
static void geometry_state(Context*c,const float*mvp,bool textured){
 auto&a=c->a;use_program(c,textured?c->program:c->flat_program);
 if(textured){texture0(c,c->material_texture);sampler0(c,c->material_sampler,c->material_sampler_zero);}
 a.glUniformMatrix4fv(textured?c->mvp:c->flat_mvp,1,GL_FALSE,mvp);
 auto&uniforms=textured?c->geometry_textured:c->geometry_flat;bool cache=c->geometry_cache&&c->state_cache;
 if(!cache||!uniforms.fog){a.glUniform2f(textured?c->fog_affine:c->flat_fog_affine,c->fog_scale,c->fog_bias);a.glUniform3fv(textured?c->fog_rgb:c->flat_fog_rgb,1,c->fog_color);uniforms.fog=true;}
 if(!cache||!uniforms.opacity||uniforms.cached_opacity!=c->opacity){a.glUniform1f(textured?c->opacity_uniform:c->flat_opacity,c->opacity);uniforms.opacity=true;uniforms.cached_opacity=c->opacity;}
 bool depth=c->opacity>=1,blend=c->blend||c->opacity<1;
 if(!cache||!c->known_geometry_depth||c->cached_geometry_depth!=depth)a.glDepthMask(depth?GL_TRUE:GL_FALSE);
 c->known_geometry_depth=true;c->cached_geometry_depth=depth;
 if(!cache||!c->known_geometry_blend||c->cached_geometry_blend!=blend){if(blend)a.glEnable(GL_BLEND);else a.glDisable(GL_BLEND);}
 c->known_geometry_blend=true;c->cached_geometry_blend=blend;
 if(blend&&(!cache||!c->known_geometry_blend_func)){a.glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA);c->known_geometry_blend_func=true;}
}
static Context*create(uint64_t xid,int width,int height,bool off,char*error,int error_size){
 auto*c=new Context;c->window=(Window)xid;c->offscreen=off;c->width=width;c->height=height;c->hud_viewport_width=width;c->hud_viewport_height=height;
 const char*mips_env=std::getenv("INK_GL_MATERIAL_MIPS");c->material_mips_requested=!mips_env||std::strcmp(mips_env,"1")==0;
 auto done=[&]()->Context*{if(error&&error_size>0)std::snprintf(error,error_size,"%s",c->error.c_str());inkgl_destroy(c);return nullptr;};
 if(width<1||height<1||width>4096||height>4096){fail(c,"invalid framebuffer dimensions");return done();}
 const char*state_env=std::getenv("INK_GL_STATE_CACHE");
 if(state_env&&std::strcmp(state_env,"0")&&std::strcmp(state_env,"1")){fail(c,"INK_GL_STATE_CACHE must be 0 or 1");return done();}
 c->state_cache=!state_env||std::strcmp(state_env,"0")!=0;
 const char*split_env=std::getenv("INK_GL_STATIC_SPLIT");if(split_env&&std::strcmp(split_env,"0")&&std::strcmp(split_env,"1")){fail(c,"INK_GL_STATIC_SPLIT must be 0 or 1");return done();}c->static_split=split_env&&!std::strcmp(split_env,"1");
 const char*medium_env=std::getenv("INK_GL_MEDIUMP_VARYINGS");if(medium_env&&std::strcmp(medium_env,"0")&&std::strcmp(medium_env,"1")){fail(c,"INK_GL_MEDIUMP_VARYINGS must be 0 or 1");return done();}c->mediump_varyings=medium_env&&!std::strcmp(medium_env,"1");
 const char*geometry_env=std::getenv("INK_GL_GEOMETRY_CACHE");if(geometry_env&&std::strcmp(geometry_env,"0")&&std::strcmp(geometry_env,"1")){fail(c,"INK_GL_GEOMETRY_CACHE must be 0 or 1");return done();}c->geometry_cache=!geometry_env||!std::strcmp(geometry_env,"1");
 const char*hsc_env=std::getenv("INK_GL_HUD_SCISSOR");c->hud_scissor=!hsc_env||std::strcmp(hsc_env,"0")!=0;
 const char*static_env=std::getenv("INK_GL_STATIC_FORMAT");
 if(static_env&&std::strcmp(static_env,"packed20")&&std::strcmp(static_env,"float36")){fail(c,"INK_GL_STATIC_FORMAT must be packed20 or float36");return done();}
 c->static_packed=!static_env||std::strcmp(static_env,"float36")!=0;
 const char*indexed=std::getenv("INK_GL_INDEXED");if(indexed&&std::strcmp(indexed,"0")&&std::strcmp(indexed,"1")){fail(c,"INK_GL_INDEXED must be 0 or 1");return done();}c->static_indexed=!indexed||std::strcmp(indexed,"1")==0;
 const char*material_env=std::getenv("INK_GL_MATERIAL_FORMAT");
 if(material_env&&std::strcmp(material_env,"rgb565")&&std::strcmp(material_env,"rgba8")&&std::strcmp(material_env,"auto")&&std::strcmp(material_env,"etc1")){fail(c,"INK_GL_MATERIAL_FORMAT must be auto, etc1, rgb565 or rgba8");return done();}
 c->material_rgba8=material_env&&std::strcmp(material_env,"rgba8")==0;
 c->material_allow_etc1=!material_env||!std::strcmp(material_env,"auto")||!std::strcmp(material_env,"etc1");
 const char*sample_env=std::getenv("INK_GL_SAMPLES");if(!sample_env||std::strcmp(sample_env,"4")==0)c->samples=4;
 const char*post_env=std::getenv("INK_GL_POST_AA");
 if(post_env&&std::strcmp(post_env,"off")&&std::strcmp(post_env,"resolve")&&std::strcmp(post_env,"edge5")){fail(c,"INK_GL_POST_AA must be off, resolve or edge5");return done();}
 c->post_requested=post_env&&!std::strcmp(post_env,"off")?0:(post_env&&!std::strcmp(post_env,"resolve")?1:(post_env?2:0));
 if(process_platform>=0&&process_platform!=(off?1:0)){fail(c,"ARM headless and X11 EGL require separate processes");return done();}
 process_platform=off?1:0;
 const char*vendor=off?"/usr/lib/aarch64-linux-gnu/mali/headless/libMali.so.9.0":"/usr/lib/aarch64-linux-gnu/mali/x11/libMali.so.9.0";
 c->lib=dlopen(vendor,RTLD_NOW|RTLD_LOCAL);if(!c->lib)c->lib=dlopen("libEGL.so.1",RTLD_NOW|RTLD_LOCAL);
 if(!c->lib){fail(c,dlerror());return done();}
#define LOAD(n) c->a.n=reinterpret_cast<decltype(c->a.n)>(dlsym(c->lib,#n));if(!c->a.n){fail(c,"missing " #n);return done();}
 EGL_FUNCS(LOAD) GL_FUNCS(LOAD)
#undef LOAD
 // Optional: absence must not break the existing PNG/RGB565 path.
 c->a.glCompressedTexImage2D=reinterpret_cast<decltype(c->a.glCompressedTexImage2D)>(dlsym(c->lib,"glCompressedTexImage2D"));
 if(!off){c->xlib=dlopen("libX11.so.6",RTLD_NOW|RTLD_LOCAL);if(!c->xlib){fail(c,"X11 runtime missing");return done();}
 c->XOpenDisplay=(decltype(c->XOpenDisplay))dlsym(c->xlib,"XOpenDisplay");c->XCloseDisplay=(decltype(c->XCloseDisplay))dlsym(c->xlib,"XCloseDisplay");c->XGetWindowAttributes=(decltype(c->XGetWindowAttributes))dlsym(c->xlib,"XGetWindowAttributes");
 c->xd=c->XOpenDisplay(nullptr);if(!c->xd){fail(c,"XOpenDisplay failed");return done();}}
 auto&a=c->a;c->display=a.eglGetDisplay((EGLNativeDisplayType)c->xd);EGLint major=0,minor=0;if(!a.eglInitialize(c->display,&major,&minor)){egl_fail(c,"initialize");return done();}
 EGLint visual_id=0;if(!off){XWindowAttributes wa={};if(!c->XGetWindowAttributes(c->xd,c->window,&wa)||!wa.visual){fail(c,"X window is unavailable");return done();}visual_id=wa.visual->visualid;}
 EGLint n=0;a.eglGetConfigs(c->display,nullptr,0,&n);std::vector<EGLConfig> configs(n);a.eglGetConfigs(c->display,configs.data(),n,&n);int best=-999;
 for(auto cfg:configs){EGLint bits=0,api=0,r=0,g=0,b=0,alpha=0,depth=0,samples=0,vis=0;a.eglGetConfigAttrib(c->display,cfg,EGL_SURFACE_TYPE,&bits);a.eglGetConfigAttrib(c->display,cfg,EGL_RENDERABLE_TYPE,&api);a.eglGetConfigAttrib(c->display,cfg,EGL_RED_SIZE,&r);a.eglGetConfigAttrib(c->display,cfg,EGL_GREEN_SIZE,&g);a.eglGetConfigAttrib(c->display,cfg,EGL_BLUE_SIZE,&b);a.eglGetConfigAttrib(c->display,cfg,EGL_ALPHA_SIZE,&alpha);a.eglGetConfigAttrib(c->display,cfg,EGL_DEPTH_SIZE,&depth);a.eglGetConfigAttrib(c->display,cfg,EGL_SAMPLES,&samples);a.eglGetConfigAttrib(c->display,cfg,EGL_NATIVE_VISUAL_ID,&vis);
 if(!(bits&(off?EGL_PBUFFER_BIT:EGL_WINDOW_BIT))||!(api&EGL_OPENGL_ES2_BIT)||depth<16||samples!=c->samples||(!off&&vis!=visual_id))continue;
 int score=(depth==16?20:0)+(alpha==0?5:0)+(off?(r+g+b):0);if(score>best){best=score;c->config=cfg;}}
 if(!c->config){fail(c,"no GLES2/depth16 EGL config matching native window visual");return done();}
 a.eglBindAPI(EGL_OPENGL_ES_API);EGLint ca[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};c->context=a.eglCreateContext(c->display,c->config,EGL_NO_CONTEXT,ca);if(!c->context){egl_fail(c,"create context");return done();}
 if(off){EGLint sa[]={EGL_WIDTH,width,EGL_HEIGHT,height,EGL_NONE};c->surface=a.eglCreatePbufferSurface(c->display,c->config,sa);}else c->surface=a.eglCreateWindowSurface(c->display,c->config,(EGLNativeWindowType)c->window,nullptr);
 if(!c->surface||!current(c)){egl_fail(c,"create surface/current");return done();}
 const char*renderer=(const char*)a.glGetString(GL_RENDERER);const char*version=(const char*)a.glGetString(GL_VERSION);const char*vendorname=(const char*)a.glGetString(GL_VENDOR);
 if(!renderer||std::strstr(renderer,"llvmpipe")||std::strstr(renderer,"softpipe")||std::strstr(renderer,"Software")||std::strstr(renderer,"swrast")){fail(c,"software GL renderer rejected");return done();}
 const char*extensions=reinterpret_cast<const char*>(a.glGetString(GL_EXTENSIONS));
 const char*etc1_name="GL_OES_compressed_ETC1_RGB8_texture";
 if(extensions&&a.glCompressedTexImage2D){const char*at=extensions;size_t len=std::strlen(etc1_name);
  while((at=std::strstr(at,etc1_name))){if((at==extensions||at[-1]==' ')&&(at[len]==0||at[len]==' ')){c->material_has_etc1=true;break;}at+=len;}}
 char info[1024];std::snprintf(info,sizeof info,"%s | %s | %s | EGL %d.%d | %s %dx%d | MSAA %d",vendorname?vendorname:"?",renderer,version?version:"?",major,minor,off?"pbuffer":"X11",width,height,c->samples);c->info=std::string(info)+(c->material_rgba8?" | world atlas preference RGBA8":(c->material_allow_etc1?" | world atlas preference ETC1/RGB565":" | world atlas preference RGB565"))+(c->material_has_etc1?" | ETC1 native capability yes":" | ETC1 native capability no")+" | white-UV flat program"+(c->static_packed?" | static VBO packed20 (range-safe fallback)":" | static VBO float36")+(c->state_cache?" | state cache on":" | state cache off")+(c->hud_scissor?" | HUD support scissor on":" | HUD support scissor off");
 c->info+=c->static_split?" | static flat/textured sub-EBOs on":" | static material split off";
 c->info+=c->mediump_varyings?" | color/fog varying mediump":" | legacy varying precision";
 c->info+=c->geometry_cache?" | geometry uniform/state cache on":" | geometry uniform/state cache off";
 c->info+=c->static_indexed?" | static exact-byte uint16 indexing on":" | static indexing off";
 a.eglSwapInterval(c->display,0);if(!init_pipeline(c)){if(c->error.empty())fail(c,"GL pipeline setup failed");return done();}
 init_post_pipeline(c,extensions);
 char post_info[500];std::snprintf(post_info,sizeof post_info," | world AA %s requested %s | post MSAA %d depth %d | HUD unfiltered | %s",c->post_mode==2?"edge5":(c->post_mode==1?"resolve":"off"),c->post_requested==2?"edge5":(c->post_requested==1?"resolve":"off"),c->post_samples,c->post_depth_bits,c->post_reason.c_str());c->info+=post_info;return c;
}
extern "C" void*inkgl_create(uint64_t xid,int w,int h,char*e,int n){return create(xid,w,h,false,e,n);}
extern "C" void*inkgl_create_offscreen(int w,int h,char*e,int n){return create(0,w,h,true,e,n);}
extern "C" int inkgl_resize(void*p,int w,int h){auto*c=(Context*)p;invalidate_state(c);if(!c||w<1||h<1||w>4096||h>4096)return 0;if(c->width==w&&c->height==h)return 1;if(c->offscreen){auto&a=c->a;a.eglMakeCurrent(c->display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);a.eglDestroySurface(c->display,c->surface);EGLint sa[]={EGL_WIDTH,w,EGL_HEIGHT,h,EGL_NONE};c->surface=a.eglCreatePbufferSurface(c->display,c->config,sa);if(!c->surface||!current(c))return egl_fail(c,"resize pbuffer");}else if(c->post_mode&&!current(c))return egl_fail(c,"resize current");if(c->post_mode&&!allocate_post_targets(c,w,h))return 0;c->width=w;c->height=h;return 1;}
extern "C" int inkgl_begin(void*p,const float top[3],const float bottom[3]){auto*c=(Context*)p;if(!c||!top||!bottom||!current(c))return 0;auto&a=c->a;c->blend=false;c->opacity=1;c->static_draw_calls=c->static_flat_vertices=c->static_textured_vertices=0;a.glBindFramebuffer(GL_FRAMEBUFFER,c->post_mode?c->post_fbo:0);a.glDisable(GL_SCISSOR_TEST);a.glViewport(0,0,c->width,c->height);c->hud_viewport_width=c->width;c->hud_viewport_height=c->height;a.glDepthMask(GL_TRUE);a.glClearColor(bottom[0],bottom[1],bottom[2],1);a.glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);a.glDisable(GL_DEPTH_TEST);a.glDisable(GL_BLEND);use_program(c,c->flat_program);a.glUniformMatrix4fv(c->flat_mvp,1,GL_FALSE,identity);a.glUniform2f(c->flat_fog_affine,0,0);a.glUniform3fv(c->flat_fog_rgb,1,c->fog_color);a.glUniform1f(c->flat_opacity,1);InkGlVertex q[6]={{-1,-1,.999f,bottom[0],bottom[1],bottom[2],1},{1,-1,.999f,bottom[0],bottom[1],bottom[2],1},{-1,1,.999f,top[0],top[1],top[2],1},{-1,1,.999f,top[0],top[1],top[2],1},{1,-1,.999f,bottom[0],bottom[1],bottom[2],1},{1,1,.999f,top[0],top[1],top[2],1}};if(c->split_streams){
 a.glBindBuffer(GL_ARRAY_BUFFER,c->sky_vbo);
 if(!c->sky_ready||std::memcmp(c->sky_colors,top,3*sizeof(float))||std::memcmp(c->sky_colors+3,bottom,3*sizeof(float))){
  a.glBufferSubData(GL_ARRAY_BUFFER,0,sizeof q,q);std::memcpy(c->sky_colors,top,3*sizeof(float));std::memcpy(c->sky_colors+3,bottom,3*sizeof(float));c->sky_ready=true;
 }
}else {a.glBindBuffer(GL_ARRAY_BUFFER,c->vbo);if(c->vbo_capacity<sizeof q){a.glBufferData(GL_ARRAY_BUFFER,sizeof q,q,GL_STREAM_DRAW);c->vbo_capacity=sizeof q;}else a.glBufferSubData(GL_ARRAY_BUFFER,0,sizeof q,q);}
attribs(c);a.glDrawArrays(GL_TRIANGLES,0,6);a.glEnable(GL_DEPTH_TEST);return 1;}
extern "C" int inkgl_draw(void*p,const InkGlVertex*v,int n,const float*mvp){auto*c=(Context*)p;if(!c||n<0||n>1500000||n%3)return fail(c,"invalid geometry batch");if(!n)return 1;if(!v||!mvp)return fail(c,"missing geometry batch");auto&a=c->a;geometry_state(c,mvp,has_material_uv(v,n));a.glBindBuffer(GL_ARRAY_BUFFER,c->vbo);size_t bytes=size_t(n)*sizeof(InkGlVertex);if(bytes>c->vbo_capacity){c->vbo_capacity=(bytes+65535)&~size_t(65535);a.glBufferData(GL_ARRAY_BUFFER,c->vbo_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;}a.glBufferSubData(GL_ARRAY_BUFFER,0,bytes,v);attribs(c);a.glDrawArrays(GL_TRIANGLES,0,n);return 1;}
static bool checked_actor_upload(Context*c,GLuint vertex,size_t&vertex_capacity,GLuint index,size_t&index_capacity,
 const InkGlVertex*v,int unique,const uint16_t*indices,int count){
 auto&a=c->a;size_t bytes=size_t(unique)*sizeof(InkGlVertex),index_bytes=size_t(count)*sizeof(uint16_t);
 // Grow only when needed, update capacity only after successful allocation.
 // A failure never records a capacity that the driver did not allocate.
 if(bytes>vertex_capacity){
  size_t capacity=(bytes+65535)&~size_t(65535);a.glBindBuffer(GL_ARRAY_BUFFER,vertex);
  a.glBufferData(GL_ARRAY_BUFFER,capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
  if(a.glGetError()!=GL_NO_ERROR)return false;
  vertex_capacity=capacity;
 }
 if(index_bytes>index_capacity){
  size_t capacity=(index_bytes+65535)&~size_t(65535);a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,index);
  a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;
  if(a.glGetError()!=GL_NO_ERROR)return false;
  index_capacity=capacity;
 }
 a.glBindBuffer(GL_ARRAY_BUFFER,vertex);a.glBufferSubData(GL_ARRAY_BUFFER,0,bytes,v);
 a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,index);a.glBufferSubData(GL_ELEMENT_ARRAY_BUFFER,0,index_bytes,indices);
 if(a.glGetError()!=GL_NO_ERROR)return false;
 attribs(c);a.glDrawElements(GL_TRIANGLES,count,GL_UNSIGNED_SHORT,nullptr);return true;
}
static int draw_actor_ring(Context*c,const InkGlVertex*v,int unique,const uint16_t*indices,int count,const float*mvp){
 int selected=c->stream_ring_next;bool okay=false;
 if(selected==0)okay=checked_actor_upload(c,c->actor_vbo,c->actor_capacity,c->stream_ebo,c->stream_index_capacity,v,unique,indices,count);
 else{
  auto&slot=c->actor_ring_extra[selected-1];
  okay=checked_actor_upload(c,slot.vertex,slot.vertex_capacity,slot.index,slot.index_capacity,v,unique,indices,count);
 }
 if(okay){c->stream_ring_selected=selected;c->stream_ring_next=(selected+1)%3;return 1;}
 // Optional storage/upload failure: retire extras and retry the complete batch
 // on the retained legacy slot. If that allocation/upload also fails, no draw
 // is issued and the normal explicit renderer-error route receives failure.
 release_stream_ring_extras(c);c->stream_ring_reason="actor ring growth/upload failed; using ring1";
 invalidate_state(c);geometry_state(c,mvp,has_material_uv(v,unique));
 if(checked_actor_upload(c,c->actor_vbo,c->actor_capacity,c->stream_ebo,c->stream_index_capacity,v,unique,indices,count))return 1;
 return fail(c,"actor stream upload failed after ring1 fallback");
}

extern "C" int inkgl_draw_indexed(void*p,const InkGlVertex*v,int unique,const uint16_t*indices,int count,const float*mvp){
 auto*c=(Context*)p;
 if(!c||unique<0||unique>65536||count<0||count>1500000||count%3)return fail(c,"invalid indexed stream dimensions");
 if(!count)return 1;
 if(!v||!indices||!mvp||!unique)return fail(c,"missing indexed geometry");
 for(int i=0;i<count;++i)if(indices[i]>=unique)return fail(c,"indexed stream exceeds unique vertices");
 auto&a=c->a;geometry_state(c,mvp,has_material_uv(v,unique));
 if(c->split_streams&&c->stream_ring_size==3)return draw_actor_ring(c,v,unique,indices,count,mvp);
 a.glBindBuffer(GL_ARRAY_BUFFER,c->split_streams?c->actor_vbo:c->vbo);size_t bytes=size_t(unique)*sizeof(InkGlVertex);
 size_t&capacity=c->split_streams?c->actor_capacity:c->vbo_capacity;
 if(bytes>capacity){capacity=(bytes+65535)&~size_t(65535);a.glBufferData(GL_ARRAY_BUFFER,capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;}
 a.glBufferSubData(GL_ARRAY_BUFFER,0,bytes,v);a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,c->stream_ebo);
 size_t index_bytes=size_t(count)*sizeof(uint16_t);
 if(index_bytes>c->stream_index_capacity){c->stream_index_capacity=(index_bytes+65535)&~size_t(65535);a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,c->stream_index_capacity,nullptr,GL_STREAM_DRAW);++c->stream_allocations;}
 a.glBufferSubData(GL_ELEMENT_ARRAY_BUFFER,0,index_bytes,indices);attribs(c);
 a.glDrawElements(GL_TRIANGLES,count,GL_UNSIGNED_SHORT,nullptr);return 1;
}
extern "C" int inkgl_stream_vertex_bytes(void*p){auto*c=(Context*)p;if(!c)return 0;if(c->stream_ring_size==3&&c->stream_ring_selected)return int(c->actor_ring_extra[c->stream_ring_selected-1].vertex_capacity);return int(c->split_streams?c->actor_capacity:c->vbo_capacity);}
extern "C" int inkgl_stream_index_bytes(void*p){auto*c=(Context*)p;if(!c)return 0;if(c->stream_ring_size==3&&c->stream_ring_selected)return int(c->actor_ring_extra[c->stream_ring_selected-1].index_capacity);return int(c->stream_index_capacity);}
extern "C" int inkgl_stream_ring_size(void*p){return p?((Context*)p)->stream_ring_size:0;}
extern "C" int inkgl_stream_ring_vertex_bytes(void*p){auto*c=(Context*)p;if(!c)return 0;size_t n=c->split_streams?c->actor_capacity:c->vbo_capacity;for(const auto&s:c->actor_ring_extra)n+=s.vertex_capacity;return int(n);}
extern "C" int inkgl_stream_ring_index_bytes(void*p){auto*c=(Context*)p;if(!c)return 0;size_t n=c->stream_index_capacity;for(const auto&s:c->actor_ring_extra)n+=s.index_capacity;return int(n);}
extern "C" uint64_t inkgl_stream_ring_bytes(void*p){return uint64_t(inkgl_stream_ring_vertex_bytes(p))+uint64_t(inkgl_stream_ring_index_bytes(p));}
extern "C" const char*inkgl_stream_ring_reason(void*p){return p?((Context*)p)->stream_ring_reason.c_str():"no context";}
extern "C" int inkgl_stream_allocations(void*p){return p?((Context*)p)->stream_allocations:0;}
extern "C" uint32_t inkgl_mesh_create(void*p,const InkGlVertex*v,int n){
 auto*c=(Context*)p;if(!c||!v||n<=0||n>1500000||n%3){fail(c,"invalid static mesh");return 0;}
 const auto eligibility=inkgl_packing::inspect(v,n);
 if(eligibility==inkgl_packing::Eligibility::nonfinite){fail(c,"non-finite static mesh");return 0;}
 const bool packed=c->static_packed&&eligibility==inkgl_packing::Eligibility::packable;
 // All transcoding occurs once at static mesh creation, never in mesh_draw.
 std::vector<inkgl_packing::StaticVertex> compact;
 if(packed){compact.resize(size_t(n));for(int i=0;i<n;++i)compact[i]=inkgl_packing::pack(v[i]);}
 const size_t stride=packed?sizeof(inkgl_packing::StaticVertex):sizeof(InkGlVertex);
 const void*input=packed?static_cast<const void*>(compact.data()):static_cast<const void*>(v);
 inkgl_indexing::Result indexed;if(c->static_indexed)indexed=inkgl_indexing::build(input,n,stride);
 if(!current(c)){egl_fail(c,"static mesh current");return 0;}
 GLuint id=0,indices=0;auto&a=c->a;a.glGenBuffers(1,&id);a.glBindBuffer(GL_ARRAY_BUFFER,id);
 a.glBufferData(GL_ARRAY_BUFFER,indexed.enabled?indexed.vertices.size():size_t(n)*stride,indexed.enabled?static_cast<const void*>(indexed.vertices.data()):input,GL_STATIC_DRAW);
 if(indexed.enabled){a.glGenBuffers(1,&indices);a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,indices);a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,indexed.indices.size()*sizeof(uint16_t),indexed.indices.data(),GL_STATIC_DRAW);}
 GLuint flat_indices=0,textured_indices=0;int flat_count=0,textured_count=0,skip=0;bool requested=false,groups_ok=true;
 if(c->static_split){bool opaque=true;for(int i=0;i<n;i++)if(v[i].a!=1.f)opaque=false;
  for(int i=0;i<n;i+=3){if(has_material_uv(v+i,3))textured_count+=3;else flat_count+=3;}
  if(flat_count&&textured_count){
   if(!opaque)skip=1;
   else if(!indexed.enabled&&n>65535)skip=2;
   else{requested=true;std::vector<uint16_t> flat,textured;flat.reserve(size_t(flat_count));textured.reserve(size_t(textured_count));
    for(int i=0;i<n;i+=3){auto&group=has_material_uv(v+i,3)?textured:flat;for(int j=0;j<3;j++)group.push_back(indexed.enabled?indexed.indices[size_t(i+j)]:static_cast<uint16_t>(i+j));}
    a.glGenBuffers(1,&flat_indices);a.glGenBuffers(1,&textured_indices);groups_ok=flat_indices&&textured_indices;
    if(groups_ok){a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,flat_indices);a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,flat.size()*sizeof(uint16_t),flat.data(),GL_STATIC_DRAW);a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,textured_indices);a.glBufferData(GL_ELEMENT_ARRAY_BUFFER,textured.size()*sizeof(uint16_t),textured.data(),GL_STATIC_DRAW);}
   }
  }
 }
 if(!id||(indexed.enabled&&!indices)||!groups_ok||a.glGetError()!=GL_NO_ERROR){if(id)a.glDeleteBuffers(1,&id);if(indices)a.glDeleteBuffers(1,&indices);if(flat_indices)a.glDeleteBuffers(1,&flat_indices);if(textured_indices)a.glDeleteBuffers(1,&textured_indices);fail(c,"static mesh upload failed");return 0;}
 c->meshes[id]={n,has_material_uv(v,n),packed,indices,indexed.enabled?indexed.unique:n};auto&record=c->meshes[id];record.flat_indices=flat_indices;record.textured_indices=textured_indices;record.flat_count=requested?flat_count:0;record.textured_count=requested?textured_count:0;record.split_skip=skip;record.vertex_bytes=indexed.enabled?indexed.vertices.size():size_t(n)*stride;record.index_bytes=(indexed.enabled?size_t(n)*2:0)+(requested?size_t(n)*2:0);return id;
}
static void draw_static_part(Context*c,uint32_t id,const Context::MeshRecord&record,const float*mvp,GLuint indices,int count,bool textured){
 geometry_state(c,mvp,textured);c->a.glBindBuffer(GL_ARRAY_BUFFER,id);if(record.packed)packed_attribs(c);else attribs(c);
 if(indices){c->a.glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,indices);c->a.glDrawElements(GL_TRIANGLES,count,GL_UNSIGNED_SHORT,nullptr);}else c->a.glDrawArrays(GL_TRIANGLES,0,count);
 ++c->static_draw_calls;if(textured)c->static_textured_vertices+=count;else c->static_flat_vertices+=count;
}
static void delete_static_record(Context*c,GLuint id,const Context::MeshRecord&record){
 c->a.glDeleteBuffers(1,&id);if(record.indices)c->a.glDeleteBuffers(1,&record.indices);if(record.flat_indices)c->a.glDeleteBuffers(1,&record.flat_indices);if(record.textured_indices)c->a.glDeleteBuffers(1,&record.textured_indices);
}
extern "C" int inkgl_mesh_draw(void*p,uint32_t id,const float*mvp){
 auto*c=(Context*)p;if(!c||!id||!mvp)return fail(c,"invalid static mesh draw");auto it=c->meshes.find(id);if(it==c->meshes.end())return fail(c,"unknown static mesh handle");const auto&record=it->second;
 // Original order is retained for any transparent/fading use of a static model.
 if(record.flat_indices&&record.textured_indices&&!c->blend&&c->opacity>=1){draw_static_part(c,id,record,mvp,record.flat_indices,record.flat_count,false);draw_static_part(c,id,record,mvp,record.textured_indices,record.textured_count,true);}
 else draw_static_part(c,id,record,mvp,record.indices,record.count,record.textured);
 return 1;
}
extern "C" void inkgl_mesh_delete(void*p,uint32_t id){auto*c=(Context*)p;if(!c||!id)return;auto it=c->meshes.find(id);if(it!=c->meshes.end()){if(current(c))delete_static_record(c,id,it->second);c->meshes.erase(it);}}
extern "C" int inkgl_static_draw_calls(void*p){return p?((Context*)p)->static_draw_calls:0;}
extern "C" int inkgl_static_flat_vertices(void*p){return p?((Context*)p)->static_flat_vertices:0;}
extern "C" int inkgl_static_textured_vertices(void*p){return p?((Context*)p)->static_textured_vertices:0;}
extern "C" int inkgl_static_grouped_meshes(void*p){auto*c=(Context*)p;if(!c)return 0;int n=0;for(const auto&v:c->meshes)n+=v.second.flat_indices&&v.second.textured_indices;return n;}
extern "C" int inkgl_static_split_fallback_meshes(void*p){auto*c=(Context*)p;if(!c)return 0;int n=0;for(const auto&v:c->meshes)n+=v.second.split_skip!=0;return n;}
extern "C" uint64_t inkgl_static_buffer_bytes(void*p){auto*c=(Context*)p;if(!c)return 0;uint64_t n=0;for(const auto&v:c->meshes)n+=v.second.vertex_bytes+v.second.index_bytes;return n;}
extern "C" void inkgl_set_blend(void*p,int enabled){auto*c=(Context*)p;if(c)c->blend=enabled!=0;}
extern "C" void inkgl_set_opacity(void*p,float opacity){auto*c=(Context*)p;if(!c)return;if(!std::isfinite(opacity)){fail(c,"non-finite opacity");return;}c->opacity=std::max(0.f,std::min(1.f,opacity));c->blend=c->opacity<1;}
extern "C" int inkgl_set_material_atlas(void*p,const unsigned char*pixels,int w,int h,int stride){
 auto*c=(Context*)p;
 // Asset sizes are bounded powers of two; 1x1 is reserved for diagnostic white.
 if(!c||!pixels||w!=h||(w!=1&&w!=512&&w!=1024)||stride<w*4||stride>8192)return fail(c,"invalid material atlas dimensions/stride");
 if(!current(c))return egl_fail(c,"material atlas current");
 std::vector<unsigned char> packed;const unsigned char*data=pixels;
 if(stride!=w*4){packed.resize(size_t(w)*h*4);for(int row=0;row<h;++row)std::memcpy(packed.data()+size_t(row)*w*4,pixels+size_t(row)*stride,size_t(w)*4);data=packed.data();}
 // White UV keeps legacy colors stable; non-opaque atlas cannot silently turn
 // textures into premultiplied shadows or introduce alpha sorting changes.
 if(data[0]!=255||data[1]!=255||data[2]!=255)return fail(c,"material atlas origin must be white");
 for(size_t i=3;i<size_t(w)*h*4;i+=4)if(data[i]!=255)return fail(c,"material atlas must be opaque RGBA");
 auto&a=c->a;GLuint candidate=0;a.glGenTextures(1,&candidate);
 if(!candidate)return fail(c,"material texture allocation failed");
 texture0(c,candidate);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);a.glPixelStorei(GL_UNPACK_ALIGNMENT,1);
 // One-time conversion of opaque artistic RGB. Normalized nearest 5/6/5
 // changes source channels by at most 4/2/4 in 8-bit units; the white sentinel
 // remains exactly one. No alpha, UV, filtering, resolution or geometry change.
 if(c->material_rgba8){a.glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,data);}
 else{
  std::vector<uint16_t> rgb565(size_t(w)*h);
  for(size_t i=0;i<rgb565.size();++i){const unsigned char*d=data+i*4;
   unsigned r=(unsigned(d[0])*31+127)/255,g=(unsigned(d[1])*63+127)/255,b=(unsigned(d[2])*31+127)/255;
   rgb565[i]=uint16_t((r<<11)|(g<<5)|b);
  }
  a.glTexImage2D(GL_TEXTURE_2D,0,GL_RGB,w,h,0,GL_RGB,GL_UNSIGNED_SHORT_5_6_5,rgb565.data());
 }
 if(a.glGetError()!=GL_NO_ERROR){a.glDeleteTextures(1,&candidate);texture0(c,c->material_texture);return fail(c,"material atlas upload failed");}
 GLuint previous=c->material_texture;c->material_texture=candidate;if(previous)a.glDeleteTextures(1,&previous);
 c->material_storage_bits=c->material_rgba8?32:16;c->material_width=w;c->material_height=h;
 c->material_mip_levels=c->material_mip_bytes=c->material_mip_upload_bytes=0;c->material_mip_reason="base is not ETC1";std::vector<unsigned char>().swap(c->material_etc1_base);return 1;
}
// ETC1 layout follows Khronos OES_compressed_ETC1_RGB8_texture. This
// validation decodes only the16 sentinel blocks, never the full image.
static uint32_t etc1_be32(const unsigned char*p){return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];}
static int etc1_signed3(unsigned n){return (n&4)?int(n&7)-8:int(n&7);}
static bool etc1_legal_block(uint32_t hi){
 if(!(hi&2))return true;
 for(int shift:{27,19,11}){int second=int((hi>>shift)&31)+etc1_signed3(hi>>(shift-3));if(second<0||second>31)return false;}
 return true;
}
static bool etc1_white_block(const unsigned char*p){
 const int modifiers[8][4]={{2,8,-2,-8},{5,17,-5,-17},{9,29,-9,-29},{13,42,-13,-42},{18,60,-18,-60},{24,80,-24,-80},{33,106,-33,-106},{47,183,-47,-183}};
 uint32_t hi=etc1_be32(p),lo=etc1_be32(p+4);if(!etc1_legal_block(hi))return false;
 int rgb[2][3];
 for(int channel=0;channel<3;++channel){
  if(hi&2){int shift=27-channel*8,first=(hi>>shift)&31,second=first+etc1_signed3(hi>>(shift-3));rgb[0][channel]=(first<<3)|(first>>2);rgb[1][channel]=(second<<3)|(second>>2);}
  else{int shift=28-channel*8,first=(hi>>shift)&15,second=(hi>>(shift-4))&15;rgb[0][channel]=first*17;rgb[1][channel]=second*17;}
 }
 for(int y=0;y<4;++y)for(int x=0;x<4;++x){int part=(hi&1)?(y>=2):(x>=2),k=x*4+y,index=((lo>>k)&1)|((lo>>(k+15))&2),table=(hi>>(part?2:5))&7;
  for(int channel=0;channel<3;++channel)if(rgb[part][channel]+modifiers[table][index]<255)return false;
 }
 return true;
}
static bool etc1_valid_atlas(const unsigned char*data,int bytes,int w,int h){
 if(!data||w!=h||(w!=512&&w!=1024)||bytes!=w*h/2)return false;
 // Differential endpoint overflow denotes ETC2 modes, not legal ETC1.
 for(int offset=0;offset<bytes;offset+=8)if(!etc1_legal_block(etc1_be32(data+offset)))return false;
 for(int by=0;by<4;++by)for(int bx=0;bx<4;++bx)if(!etc1_white_block(data+(by*(w/4)+bx)*8))return false;
 return true;
}
extern "C" int inkgl_material_etc1_supported(void*p){return p&&((Context*)p)->material_has_etc1&&((Context*)p)->material_allow_etc1?1:0;}
extern "C" int inkgl_set_material_atlas_etc1(void*p,const unsigned char*data,int bytes,int w,int h){
 auto*c=(Context*)p;if(!c||!etc1_valid_atlas(data,bytes,w,h))return fail(c,"invalid ETC1 atlas dimensions/blocks/white sentinel");
 if(!inkgl_material_etc1_supported(p))return fail(c,"native ETC1 upload unavailable or disabled");
 if(!current(c))return egl_fail(c,"ETC1 atlas current");
 auto&a=c->a;if(a.glGetError()!=GL_NO_ERROR)return fail(c,"pre-existing GL error before ETC1 upload");
 // Allocate separately: a failed compression upload cannot corrupt the
 // currently bound PNG atlas. Commit only after every GL operation passes.
 GLuint candidate=0;a.glGenTextures(1,&candidate);
 if(!candidate){a.glGetError();texture0(c,c->material_texture);return fail(c,"ETC1 texture allocation failed");}
 texture0(c,candidate);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
 a.glCompressedTexImage2D(GL_TEXTURE_2D,0,0x8D64,w,h,0,bytes,data);
 GLenum error=a.glGetError();if(error!=GL_NO_ERROR){a.glDeleteTextures(1,&candidate);texture0(c,c->material_texture);char message[100];std::snprintf(message,sizeof message,"ETC1 upload failed (GL0x%x)",error);return fail(c,message);}
 GLuint previous=c->material_texture;c->material_texture=candidate;c->material_storage_bits=4;if(previous)a.glDeleteTextures(1,&previous);
 c->material_width=w;c->material_height=h;c->material_mip_levels=c->material_mip_bytes=c->material_mip_upload_bytes=0;
 std::vector<unsigned char>().swap(c->material_etc1_base);c->material_mip_reason=c->material_mips_requested?"not enabled":"disabled by INK_GL_MATERIAL_MIPS";
 if(c->material_mips_requested){try{c->material_etc1_base.assign(data,data+bytes);}catch(...){c->material_mip_reason="ETC1 base mirror allocation failed";}}
 return 1;
}
// All levels are embedded and checked once at context setup, never per frame.
static bool valid_compiled_material_mips(){
 using namespace runner_material_mips;
 if(level_count!=10||sizeof(data)!=174776||sizeof(base_sha256)!=65||sizeof(source_png_sha256)!=65)return false;
 size_t end=0;
 for(int i=0;i<level_count;++i){const auto&level=levels[i];int dimension=1024>>(i+1),bytes=((dimension+3)/4)*((dimension+3)/4)*8;
  if(level.width!=dimension||level.height!=dimension||level.bytes!=bytes||level.offset<0||size_t(level.offset)!=end||end>sizeof(data)||size_t(bytes)>sizeof(data)-end)return false;
  for(int at=0;at<bytes;at+=8)if(!etc1_legal_block(etc1_be32(data+end+size_t(at))))return false;
  end+=size_t(bytes);
 }return end==sizeof(data);
}
extern "C" int inkgl_material_mips_api_version(){return 1;}
extern "C" const char*inkgl_material_mips_base_sha256(){return runner_material_mips::base_sha256;}
extern "C" const char*inkgl_material_mips_source_sha256(){return runner_material_mips::source_png_sha256;}
extern "C" int inkgl_material_mips_levels(void*p){return p?((Context*)p)->material_mip_levels:0;}
extern "C" int inkgl_material_mips_gpu_bytes(void*p){return p?((Context*)p)->material_mip_bytes:0;}
extern "C" int inkgl_material_mips_upload_bytes(void*p){return p?((Context*)p)->material_mip_upload_bytes:0;}
extern "C" const char*inkgl_material_mips_reason(void*p){return p?((Context*)p)->material_mip_reason.c_str():"no context";}
extern "C" int inkgl_set_material_mips(void*p,const char*expected_base_sha,const char*expected_png_sha){
 auto*c=(Context*)p;if(!c)return 0;
 auto rejected=[&](const char*reason){c->material_mip_reason=reason;return 0;};
 if(!c->material_mips_requested)return rejected("disabled by INK_GL_MATERIAL_MIPS");
 if(!expected_base_sha||std::strlen(expected_base_sha)!=64||std::strcmp(expected_base_sha,runner_material_mips::base_sha256)!=0||!expected_png_sha||std::strlen(expected_png_sha)!=64||std::strcmp(expected_png_sha,runner_material_mips::source_png_sha256)!=0)return rejected("compiled mip PNG/PKM SHA mismatch");
 if(c->material_storage_bits!=4||c->material_width!=1024||c->material_height!=1024||c->material_etc1_base.size()!=1024*1024/2)return rejected("mips require the current 1024-square ETC1 base mirror");
 if(!valid_compiled_material_mips())return rejected("compiled ETC1 mip dimensions/bytes/endpoints invalid");
 if(c->material_mip_levels==10){c->material_mip_reason.clear();return 1;}
 if(!current(c))return rejected("mip upload could not make context current");
 auto&a=c->a;if(a.glGetError()!=GL_NO_ERROR)return rejected("pre-existing GL error before mip upload");
 // Keep the validated base texture untouched until a complete candidate is
 // ready. Partial levels or filter errors are deleted, leaving GL_LINEAR.
 GLuint candidate=0;a.glGenTextures(1,&candidate);if(!candidate){a.glGetError();return rejected("mip candidate texture allocation failed");}
 texture0(c,candidate);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
 a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
 a.glCompressedTexImage2D(GL_TEXTURE_2D,0,0x8D64,1024,1024,0,int(c->material_etc1_base.size()),c->material_etc1_base.data());
 GLenum error=a.glGetError();if(error==GL_NO_ERROR)c->material_mip_upload_bytes+=int(c->material_etc1_base.size());
 for(int i=0;i<runner_material_mips::level_count&&error==GL_NO_ERROR;++i){const auto&level=runner_material_mips::levels[i];
  a.glCompressedTexImage2D(GL_TEXTURE_2D,i+1,0x8D64,level.width,level.height,0,level.bytes,runner_material_mips::data+level.offset);
  error=a.glGetError();if(error==GL_NO_ERROR)c->material_mip_upload_bytes+=level.bytes;
 }
 if(error==GL_NO_ERROR){a.glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR_MIPMAP_LINEAR);error=a.glGetError();}
 if(error!=GL_NO_ERROR){a.glDeleteTextures(1,&candidate);texture0(c,c->material_texture);char message[96];std::snprintf(message,sizeof message,"compiled ETC1 mip upload failed (GL0x%x); base retained",error);return rejected(message);}
 GLuint previous=c->material_texture;c->material_texture=candidate;if(previous)a.glDeleteTextures(1,&previous);
 c->material_mip_levels=runner_material_mips::level_count;c->material_mip_bytes=int(sizeof(runner_material_mips::data));c->material_mip_reason.clear();return 1;
}
static int upload_hud(Context*c,const unsigned char*pixels,int w,int h,int stride,bool bgra){
 if(!c)return 0;if(!pixels||w==0||h==0){c->hud_width=c->hud_height=0;c->hud_support.invalidate();return 1;}
 if(w<1||h<1||w>4096||h>4096||stride<w*4)return fail(c,"invalid HUD dimensions/stride");
 if(!current(c))return egl_fail(c,"HUD current");const unsigned char*data=pixels;
 if(stride!=w*4){c->packed_hud.resize(size_t(w)*h*4);for(int row=0;row<h;row++)std::memcpy(c->packed_hud.data()+size_t(row)*w*4,pixels+size_t(row)*stride,size_t(w)*4);data=c->packed_hud.data();}
 auto&a=c->a;texture0(c,c->texture);
 if(w!=c->texture_width||h!=c->texture_height){a.glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,data);c->texture_width=w;c->texture_height=h;c->hud_allocations++;}
 else a.glTexSubImage2D(GL_TEXTURE_2D,0,0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,data);
 if(a.glGetError()!=GL_NO_ERROR){c->hud_support.invalidate();return fail(c,"HUD texture upload failed");}c->hud_width=w;c->hud_height=h;c->hud_bgra=bgra;
 if(c->hud_scissor){c->hud_support.reset(w,h);c->hud_support.include(data,0,0,w,h,w*4);c->hud_support.plan(c->hud_viewport_width,c->hud_viewport_height);}else c->hud_support.invalidate();return 1;
}
static int patch_hud(Context*c,const unsigned char*pixels,int x,int y,int w,int h,int stride,bool bgra){
 if(!c||!pixels||w<1||h<1||stride<w*4||x<0||y<0||w>c->hud_width||h>c->hud_height||x>c->hud_width-w||y>c->hud_height-h)return fail(c,"invalid HUD patch bounds/stride");
 if(c->hud_bgra!=bgra)return fail(c,"HUD patch format must match full texture");
 if(!current(c))return egl_fail(c,"HUD patch current");const unsigned char*data=pixels;
 if(stride!=w*4){c->packed_hud.resize(size_t(w)*h*4);for(int row=0;row<h;row++)std::memcpy(c->packed_hud.data()+size_t(row)*w*4,pixels+size_t(row)*stride,size_t(w)*4);data=c->packed_hud.data();}
 auto&a=c->a;texture0(c,c->texture);a.glTexSubImage2D(GL_TEXTURE_2D,0,x,y,w,h,GL_RGBA,GL_UNSIGNED_BYTE,data);if(a.glGetError()!=GL_NO_ERROR){c->hud_support.invalidate();return 0;}
 if(c->hud_scissor&&c->hud_support.valid){c->hud_support.include(data,x,y,w,h,w*4);c->hud_support.plan(c->hud_viewport_width,c->hud_viewport_height);}return 1;
}
extern "C" int inkgl_set_hud(void*p,const unsigned char*rgba,int w,int h,int stride){return upload_hud((Context*)p,rgba,w,h,stride,false);}
extern "C" int inkgl_patch_hud(void*p,const unsigned char*rgba,int x,int y,int w,int h,int stride){return patch_hud((Context*)p,rgba,x,y,w,h,stride,false);}
extern "C" int inkgl_set_hud_bgra(void*p,const unsigned char*bgra,int w,int h,int stride){return upload_hud((Context*)p,bgra,w,h,stride,true);}
extern "C" int inkgl_patch_hud_bgra(void*p,const unsigned char*bgra,int x,int y,int w,int h,int stride){return patch_hud((Context*)p,bgra,x,y,w,h,stride,true);}
extern "C" int inkgl_hud_allocations(void*p){return p?((Context*)p)->hud_allocations:0;}
extern "C" int inkgl_present(void*p){auto*c=(Context*)p;if(!c)return 0;auto&a=c->a;
 if(c->post_mode){
  // Changing the framebuffer implicitly resolves the EXT multisample texture.
  // Sampling it happens only after unbinding; feedback loops are impossible.
  a.glBindFramebuffer(GL_FRAMEBUFFER,0);a.glViewport(0,0,c->width,c->height);a.glDisable(GL_SCISSOR_TEST);a.glDisable(GL_DEPTH_TEST);a.glDepthMask(GL_FALSE);a.glDisable(GL_BLEND);
  use_program(c,c->post_program);texture0(c,c->post_texture);a.glUniform1i(c->post_sampler,0);a.glUniform2f(c->post_pixel,1.f/c->width,1.f/c->height);a.glUniform1i(c->post_enabled,c->post_mode==2?1:0);a.glBindBuffer(GL_ARRAY_BUFFER,c->hud_vbo);attribs(c);a.glDrawArrays(GL_TRIANGLES,0,6);
 }
 if(c->hud_width&&c->hud_height){a.glDisable(GL_DEPTH_TEST);a.glDepthMask(GL_FALSE);a.glEnable(GL_BLEND);a.glBlendFunc(GL_ONE,GL_ONE_MINUS_SRC_ALPHA);use_program(c,c->hud_program);texture0(c,c->texture);sampler0(c,c->hud_sampler,c->hud_sampler_zero);a.glUniform1i(c->hud_swap,c->hud_bgra?1:0);a.glBindBuffer(GL_ARRAY_BUFFER,c->hud_vbo);attribs(c);
 if(c->hud_scissor&&c->hud_support.valid){c->hud_support.plan(c->hud_viewport_width,c->hud_viewport_height);a.glEnable(GL_SCISSOR_TEST);
  for(int i=0;i<c->hud_support.count;++i){const auto&r=c->hud_support.rectangles[i];a.glScissor(r.x,r.y,r.w,r.h);a.glDrawArrays(GL_TRIANGLES,0,6);}
 }else{a.glDisable(GL_SCISSOR_TEST);a.glDrawArrays(GL_TRIANGLES,0,6);}
 }a.glDisable(GL_SCISSOR_TEST);EGLBoolean swapped=a.eglSwapBuffers(c->display,c->surface);invalidate_state(c);if(!swapped)return egl_fail(c,"swap buffers");return 1;}
extern "C" int inkgl_finish(void*p){auto*c=(Context*)p;if(!c)return 0;c->a.glFinish();GLenum e=c->a.glGetError();if(e){char msg[80];std::snprintf(msg,sizeof msg,"GL error 0x%x",e);return fail(c,msg);}return 1;}
extern "C" int inkgl_read_rgba(void*p,unsigned char*data,int cap){auto*c=(Context*)p;if(!c||!data||cap<0||size_t(cap)<size_t(c->width)*c->height*4)return 0;c->a.glReadPixels(0,0,c->width,c->height,GL_RGBA,GL_UNSIGNED_BYTE,data);return c->a.glGetError()==GL_NO_ERROR;}
extern "C" void inkgl_set_fog(void*p,float s,float e,const float*rgb){
 auto*c=(Context*)p;if(!c)return;
 c->geometry_flat.fog=c->geometry_textured.fog=false;
 c->fog_start=s;c->fog_end=e;c->fog_scale=0;c->fog_bias=0;
 if(rgb)std::copy(rgb,rgb+3,c->fog_color);
 // Disabled ranges retain exactly zero fog. Use double host intermediates so
 // float end-start cannot overflow before the reciprocal is validated.
 if(!std::isfinite(s)||!std::isfinite(e)){fail(c,"non-finite fog range disabled");return;}
 if(e<=s)return;
 double span=double(e)-double(s),scale=1.0/span,bias=-double(s)*scale;
 float fs=float(scale),fb=float(bias);
 // Extreme coefficients cannot be represented reliably by an ES2 shader.
 if(!std::isfinite(fs)||!std::isfinite(fb)||!std::isnormal(fs)||fs<=0){fail(c,"unrepresentable fog affine disabled");return;}
 c->fog_scale=fs;c->fog_bias=fb;
}
extern "C" int inkgl_material_storage_bits(void*p){return p?((Context*)p)->material_storage_bits:0;}
extern "C" int inkgl_post_aa_mode(void*p){return p?((Context*)p)->post_mode:0;}
extern "C" int inkgl_post_aa_samples(void*p){return p?((Context*)p)->post_samples:0;}
extern "C" const char*inkgl_info(void*p){auto*c=(Context*)p;return c?c->info.c_str():"no context";}
extern "C" const char*inkgl_error(void*p){auto*c=(Context*)p;return c?c->error.c_str():"no context";}
extern "C" void inkgl_destroy(void*p){auto*c=(Context*)p;if(!c)return;if(c->display&&c->context&&c->surface&&c->a.eglMakeCurrent&&current(c)){auto&a=c->a;for(const auto&entry:c->meshes)delete_static_record(c,entry.first,entry.second);delete_post_targets(c,c->post_fbo,c->post_depth,c->post_texture);if(c->post_program)a.glDeleteProgram(c->post_program);if(c->program)a.glDeleteProgram(c->program);if(c->flat_program)a.glDeleteProgram(c->flat_program);if(c->hud_program)a.glDeleteProgram(c->hud_program);release_stream_ring_extras(c);if(c->vbo)a.glDeleteBuffers(1,&c->vbo);if(c->actor_vbo)a.glDeleteBuffers(1,&c->actor_vbo);if(c->sky_vbo)a.glDeleteBuffers(1,&c->sky_vbo);if(c->stream_ebo)a.glDeleteBuffers(1,&c->stream_ebo);if(c->hud_vbo)a.glDeleteBuffers(1,&c->hud_vbo);if(c->texture)a.glDeleteTextures(1,&c->texture);if(c->material_texture)a.glDeleteTextures(1,&c->material_texture);a.eglMakeCurrent(c->display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);}if(c->display&&c->a.eglDestroySurface&&c->surface)c->a.eglDestroySurface(c->display,c->surface);if(c->display&&c->a.eglDestroyContext&&c->context)c->a.eglDestroyContext(c->display,c->context);if(c->display&&c->a.eglTerminate)c->a.eglTerminate(c->display);if(c->xd&&c->XCloseDisplay)c->XCloseDisplay(c->xd);if(c->xlib)dlclose(c->xlib);if(c->lib)dlclose(c->lib);delete c;}



// BEGIN ADDITIVE NATIVE EFFECTIVE FLAGS API
// Read-only snapshot: no environment reads, GL calls or renderer state mutation.
// Returned storage belongs to this Context and survives until its next query.
extern "C" const char*inkgl_effective_flags(void*p){
 auto*c=(Context*)p;if(!c)return nullptr;
 std::string result="{\"schema\":1,\"effective\":{";bool first=true;
 auto add=[&](const char*key,const char*value){if(!first)result+=",";first=false;result+="\"";result+=key;result+="\":\"";result+=value;result+="\"";};
 add("INK_GL_STATE_CACHE",c->state_cache?"1":"0");
 add("INK_GL_GEOMETRY_CACHE",c->geometry_cache?"1":"0");
 add("INK_GL_HUD_SCISSOR",c->hud_scissor?"1":"0");
 add("INK_GL_INDEXED",c->static_indexed?"1":"0");
 add("INK_GL_MEDIUMP_VARYINGS",c->mediump_varyings?"1":"0");
 add("INK_GL_STATIC_FORMAT",c->static_packed?"packed20":"float36");
 add("INK_GL_MATERIAL_FORMAT",c->material_storage_bits==0?"white":(c->material_storage_bits==4?"etc1":(c->material_storage_bits==16?"rgb565":"rgba8")));
 add("INK_GL_STATIC_SPLIT",c->static_split?"1":"0");
 add("INK_GL_SPLIT_STREAMS",c->split_streams?"1":"0");
 add("INK_GL_STREAM_RING",c->stream_ring_size==3?"3":"1");
 add("INK_GL_MATERIAL_MIPS",c->material_mip_levels>0?"1":"0");
 add("INK_GL_POST_AA",c->post_mode==2?"edge5":(c->post_mode==1?"resolve":"off"));
 const std::string samples=std::to_string(c->samples);add("INK_GL_SAMPLES",samples.c_str());
 result+="},\"material_state\":{\"width\":"+std::to_string(c->material_width)+",\"height\":"+std::to_string(c->material_height)+",\"storage_bits\":"+std::to_string(c->material_storage_bits)+"}}";
 c->effective_flags_snapshot=std::move(result);return c->effective_flags_snapshot.c_str();
}
// END ADDITIVE NATIVE EFFECTIVE FLAGS API
