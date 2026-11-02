#ifndef INK_GL_BACKEND_H
#define INK_GL_BACKEND_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* UV (0,0) is reserved for the atlas white tile. Existing seven-component
 * aggregate initializers remain untextured. This is internal geometry only;
 * the runner Frame/Entity C ABI is unchanged. */
typedef struct InkGlVertex { float x,y,z,r,g,b,a; float u=0.f,v=0.f; } InkGlVertex;
/* X11 window must already exist and belong to this user/display. No window is created. */
const char *inkgl_effective_flags(void *context);
void *inkgl_create(uint64_t window_xid,int width,int height,char *error,int error_size);
/* Uses ARM headless EGL when installed, otherwise regular EGL. Never maps a window. */
void *inkgl_create_offscreen(int width,int height,char *error,int error_size);
int inkgl_resize(void *context,int width,int height);
int inkgl_begin(void *context,const float top_rgb[3],const float bottom_rgb[3]);
int inkgl_draw(void *context,const InkGlVertex *vertices,int count,const float column_major_mvp[16]);
/* Optional bounded dynamic stream. Static meshes and public scene ABI unchanged. */
int inkgl_draw_indexed(void *context,const InkGlVertex *vertices,int unique_count,
                      const uint16_t *indices,int index_count,const float *mvp);
/* Immutable VBO, uploaded once during scene warmup. Handle 0 means failure. */
uint32_t inkgl_mesh_create(void *context,const InkGlVertex *vertices,int count);
int inkgl_mesh_draw(void *context,uint32_t handle,const float column_major_mvp[16]);
void inkgl_mesh_delete(void *context,uint32_t handle);
/* Geometry defaults opaque at begin; enable only for explicit shadows/FX. */
void inkgl_set_blend(void *context,int enabled);
/* Fade consumed obstacles without hiding the hero behind their long geometry. */
void inkgl_set_opacity(void *context,float opacity);
/* Opaque, straight RGBA8, PNG top row first. One persistent world texture,
 * independent from the premultiplied HUD. Upload only at context creation. */
int inkgl_set_material_atlas(void *context,const unsigned char *rgba,int width,int height,int stride);
/* Optional additive ETC1 capability; Frame/Entity/material RGBA API unchanged.
 * Raw ETC1 RGB8 blocks, PNG top row first, exactly width*height/2 bytes.
 * 512/1024 square only, opaque RGB and decoded top-left16x16 pure white.
 * Unsupported/invalid/upload failure preserves the previous texture intact. */
int inkgl_material_etc1_supported(void *context);
int inkgl_set_material_atlas_etc1(void *context,const unsigned char *blocks,int byte_count,int width,int height);
/* Uploaded world texture storage, 0 before upload, otherwise 4, 16 or 32.
 * INK_GL_MATERIAL_FORMAT=rgba8 keeps the diagnostic original storage path. */
int inkgl_material_storage_bits(void *context);
/* RGBA8 PREMULTIPLIED alpha. Copies/uploads only when called; texture persists across frames. */
int inkgl_set_hud(void *context,const unsigned char *rgba,int width,int height,int stride);
int inkgl_patch_hud(void *context,const unsigned char *rgba,int x,int y,int width,int height,int stride);
/* Same premultiplication, native Cairo little-endian BGRA order; no channel copy. */
int inkgl_set_hud_bgra(void *context,const unsigned char *bgra,int width,int height,int stride);
int inkgl_patch_hud_bgra(void *context,const unsigned char *bgra,int x,int y,int width,int height,int stride);
/* Diagnostics: actual texture allocations, excluding same-size updates. */
int inkgl_hud_allocations(void *context);
int inkgl_present(void *context);
int inkgl_finish(void *context);
/* Bottom row first, tight RGBA8; caller supplies at least width*height*4 bytes. */
int inkgl_read_rgba(void *context,unsigned char *pixels,int byte_capacity);
/* Fog uses eye distance approximated by clip w; disabled until end > start >= 0. */
void inkgl_set_fog(void *context,float start,float end,const float rgb[3]);
const char *inkgl_info(void *context);
const char *inkgl_error(void *context);
void inkgl_destroy(void *context);
#ifdef __cplusplus
}
#endif
#endif
