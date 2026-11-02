# Native 3D railway renderer and ARM GLES2 backend

The runner uses an adapted CC0 Quaternius character with original courier accessories, original city/train meshes, a chase camera,
distance-based animation, per-face lighting, fog and a cached Chinese HUD.
Official Subway Surfers screenshots were studied for spatial composition;
no original game's code or art is bundled. Python retains simulation, controls,
audio, reports and suite lifecycle. The 7.2 m train body has a matching collision
span, contacted-train dissolve and natural tail retention; roofs are not playable.

The renderer uses the existing
Mali-400 vendor EGL/GLES2 driver directly, avoiding the Xorg GLX software path.
No driver, display, device permission or system package is changed.

Build on KV260:

```
python3 -B build_native.py --output librunner3d.so
```

The backend only needs the existing Khronos headers under `/usr/include/mali`.
X11 declarations are the public ABI and symbols are loaded at runtime, so it
does not require installing X11 development packages. Only the EGL and GLES2
runtime library is used; there are no desktop OpenGL calls. The build receipt
pins the two C++ files, two headers and output binary by SHA256.

The installed driver rejects GTK's cloned default visual despite identical
RGB565 pixel masks. `runner/main.py` therefore selects the actual X11 root
visual before the canvas realizes and synchronizes creation before EGL opens
its independent X connection. It does not change the desktop resolution or
other games' GTK windows. Scene and HUD use a single complete EGL swap.

Default scene anti-aliasing is 4x MSAA; INK_GL_SAMPLES=0 enables a profiling
comparison. INK_RUNNER_RENDERER=cairo selects the unchanged fallback explicitly.
Native failures are recorded and fall back without claiming acceleration.

`InkGlVertex` is seven tightly packed floats: world `xyz` followed by straight
`rgba`. Matrices are column major. Batch geometry uses depth test/write and is
opaque by default; `inkgl_set_blend(context, 1)` enables straight alpha for explicit
shadows/FX until changed or the next `inkgl_begin`. The optional cached HUD is RGBA8 **premultiplied** alpha,
top row first; convert Cairo little-endian BGRA when the HUD changes. HUD
composition has no depth test and uses premultiplied blending.

Typical frame:

1. `inkgl_begin(context, sky_top_rgb, sky_bottom_rgb)`
2. `inkgl_draw(context, vertices, count, matrix)`
3. `inkgl_present(context)`

HUD upload is separate and only needed when changed. `inkgl_finish` synchronizes
GPU execution for benchmarks, not normal play. Readback is RGBA8 bottom row first.
Use the headless pbuffer for saved frame QA; readback after swapping a native
double-buffered window is not generally guaranteed to preserve its back buffer.

Use `inkgl_mesh_create` once during scene warmup for immutable world/train/coin
geometry, then `inkgl_mesh_draw` with a per-object matrix each frame. This retains
the VBO on the GPU rather than regenerating/uploading the city in every frame.
All meshes are released by context teardown; `inkgl_mesh_delete` is available for
explicit removal. `inkgl_patch_hud` updates a bounded premultiplied RGBA rectangle
inside an existing HUD texture, including padded row stride. The score card can
therefore update without transferring the complete 960x540 HUD.

`INK_GL_SAMPLES=4` selects a four-sample EGL config, when available. The complete
runner scene defaults to four samples for smoother silhouettes; explicitly set
`INK_GL_SAMPLES=0` for the measured faster alternative. The low-level backend
itself uses zero samples unless configured by its caller. The renderer string
records the actual selected sample count.

The native X11 path checks the actual window's visual ID against EGL configs,
including the current board's RGB565 (16-bit X11 color) display. Its actual EGL
depth/stencil buffer is 24/8 bits. It creates no windows,
does not change focus and does not alter screen resolution. The GTK caller
owns its already-realized drawing-area XID, suppresses ordinary Cairo drawing,
and destroys the GL context before destroying that window.

Actual GTK integration exposed two constraints that simple Xlib windows did not:
GDK can select visual 942 with the same RGB565 masks while the Mali EGL configs
only advertise original visual 33 (and RGBA visual 99), and a newly realized GDK
window may still be buffered on GDK's X connection. The runner now dynamically
looks up the current X server's root visual and assigns it to its drawing area
before realization, then ensures the window is native and synchronizes GDK
before EGL's separate X connection queries it. Exact EGL visual matching remains
enabled. The initial failed GTK probes are retained in the audit directory.

ARM headless and X11 vendor libraries must be exercised in **separate processes**.
Sequentially loading the two window-system builds crashed the proprietary driver
in the initial bounded probe. The backend now rejects mixing platforms in the
same process, and independent headless/native lifecycles both pass four recreate
cycles. No production process or camera capture was stopped for this audit.

Measured on the live KV260 on 2026-10-15 (synthetic tests, not game acceptance):

- Real vendor/renderer: ARM / Mali-400 MP / OpenGL ES 2.0.
- Headless 960x540 background + depth-overlapping quads: 166.59 FPS, P95 7.51 ms,
  maximum 8.61 ms, process CPU 16.30% of one core.
- Native X11 unmapped depth16 window: 62.97 FPS, P95 20.36 ms, max 23.32 ms,
  process CPU 13.54% of one core; each sample includes swap and `glFinish`.
- Depth order, premultiplied HUD, resize, cleanup/recreate and mixed-platform
  rejection passed. The native test window was never mapped onto the screen.

Evidence: `../audit/egl-probe-result.json`,
`../audit/backend-smoke-result.json`, `../audit/backend_smoke.py`.
The production game must still be profiled with its complete scene and HUD,
then visually reviewed and tested through the real embedded native window.

The original complete scene initially measured 26.63 FPS typical and 22.74 FPS
with 60 entities; this failed the intended performance direction. After static
VBO caching and distance-based world LOD, a 40-second headless real-GPU regression
measured 56.63 FPS typical (1% low 36.43), and 43.01 FPS with 60 entities (1% low
26.37), with no frame over 50 ms. Evidence is `../audit/static-result.json`.
These are deterministic GPU scene benchmarks with the production DPU service
still active; they are not presented-window gameplay or human acceptance.
