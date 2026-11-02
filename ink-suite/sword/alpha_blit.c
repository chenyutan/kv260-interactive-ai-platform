/* Cached-sprite helper: integer-aligned premultiplied BGRA OVER.
 * The Python wrapper validates format/CTM/clips and owns all buffers.
 * Build: cc -O3 -std=c99 -shared -fPIC alpha_blit.c -o libink_alpha.so
 */
#include <stddef.h>
#include <stdint.h>
#include <limits.h>

#if defined(__aarch64__)
#include <arm_neon.h>
#endif
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

static inline uint8_t mul_un8(uint8_t a, uint8_t b) {
    unsigned int t = (unsigned int)a * b + 0x80;
    return (uint8_t)((t + (t >> 8)) >> 8);
}

/* Invalid shapes/strides return -1 without accessing the buffers. Buffers
 * must not alias. RGB24's high byte is padding, and is canonicalized to 255.
 * Cairo flush() is required before access, mark_dirty() after access.
 */
static int valid_args(uint8_t *dst, ptrdiff_t ds, const uint8_t *src,
                      ptrdiff_t ss, int w, int h, unsigned alpha) {
    return dst && src && w >= 0 && h >= 0 && alpha <= 255 &&
           ds >= (ptrdiff_t)w * 4 && ss >= (ptrdiff_t)w * 4;
}

static void row_scalar(uint8_t *d, const uint8_t *s, int w,
                       uint8_t alpha, int rgb24) {
    int x, ch;
    for (x = 0; x < w; ++x, d += 4, s += 4) {
        uint8_t a = alpha == 255 ? s[3] : mul_un8(s[3], alpha);
        uint8_t inv_a = (uint8_t)(255 - a);
        for (ch = 0; ch < 4; ++ch) {
            unsigned sc = alpha == 255 ? s[ch] : mul_un8(s[ch], alpha);
            unsigned v = sc + mul_un8(d[ch], inv_a);
            d[ch] = (uint8_t)(v > 255 ? 255 : v);
        }
        if (rgb24) d[3] = 255;
    }
}

EXPORT int sword_alpha_over_scalar(uint8_t *dst, ptrdiff_t dst_stride,
                                    const uint8_t *src, ptrdiff_t src_stride,
                                    int width, int height, unsigned alpha,
                                    int dst_rgb24) {
    int y;
    if (!valid_args(dst, dst_stride, src, src_stride, width, height, alpha)) return -1;
    if (alpha == 0 || width == 0 || height == 0) return 0;
    for (y = 0; y < height; ++y)
        row_scalar(dst + y * dst_stride, src + y * src_stride,
                   width, (uint8_t)alpha, dst_rgb24);
    return 0;
}

#if defined(__aarch64__)
static inline uint8x16_t mul_un8x16(uint8x16_t a, uint8x16_t b) {
    uint16x8_t lo = vmull_u8(vget_low_u8(a), vget_low_u8(b));
    uint16x8_t hi = vmull_u8(vget_high_u8(a), vget_high_u8(b));
    lo = vaddq_u16(lo, vdupq_n_u16(128));
    hi = vaddq_u16(hi, vdupq_n_u16(128));
    lo = vaddq_u16(lo, vshrq_n_u16(lo, 8));
    hi = vaddq_u16(hi, vshrq_n_u16(hi, 8));
    return vcombine_u8(vshrn_n_u16(lo, 8), vshrn_n_u16(hi, 8));
}
#endif

EXPORT int sword_alpha_over(uint8_t *dst, ptrdiff_t dst_stride,
                            const uint8_t *src, ptrdiff_t src_stride,
                            int width, int height, unsigned alpha,
                            int dst_rgb24) {
#if defined(__aarch64__)
    const uint8_t alpha_indices_bytes[16] = {
        3,3,3,3, 7,7,7,7, 11,11,11,11, 15,15,15,15
    };
    const uint8_t alpha_mask_bytes[16] = {
        0,0,0,255, 0,0,0,255, 0,0,0,255, 0,0,0,255
    };
    uint8x16_t indices = vld1q_u8(alpha_indices_bytes);
    uint8x16_t alpha_mask = vld1q_u8(alpha_mask_bytes);
    uint8x16_t global = vdupq_n_u8((uint8_t)alpha);
    uint8x16_t full = vdupq_n_u8(255);
    int x, y;
    if (!valid_args(dst, dst_stride, src, src_stride, width, height, alpha)) return -1;
    if (alpha == 0 || width == 0 || height == 0) return 0;
    for (y = 0; y < height; ++y) {
        uint8_t *d = dst + y * dst_stride;
        const uint8_t *s = src + y * src_stride;
        for (x = 0; x + 4 <= width; x += 4, s += 16, d += 16) {
            uint8x16_t sv = vld1q_u8(s);
            uint8x16_t dv = vld1q_u8(d);
            uint8x16_t inv, out;
            if (alpha != 255) sv = mul_un8x16(sv, global);
            inv = vsubq_u8(full, vqtbl1q_u8(sv, indices));
            out = vqaddq_u8(sv, mul_un8x16(dv, inv));
            if (dst_rgb24) out = vorrq_u8(out, alpha_mask);
            vst1q_u8(d, out);
        }
        row_scalar(d, s, width - x, (uint8_t)alpha, dst_rgb24);
    }
    return 0;
#else
    return sword_alpha_over_scalar(dst, dst_stride, src, src_stride,
                                    width, height, alpha, dst_rgb24);
#endif
}

/* Full-frame RGB24 batch ABI. Buffer lengths belong to the actual pinned
 * Python views, not just the visible clipped regions. Every descriptor is
 * validated before the first write; an invalid later tile cannot partially
 * composite the batch. Sources may share immutable storage with each other,
 * but neither sources nor descriptors may overlap the destination. */
#define SWORD_ALPHA_BATCH_LIMIT 512
typedef struct sword_alpha_tile {
    const uint8_t *src;
    size_t src_bytes;
    ptrdiff_t src_stride;
    int width;
    int height;
    int left;
    int top;
} sword_alpha_tile;

static int valid_span(const uint8_t *p, size_t bytes, ptrdiff_t stride,
                       int width, int height) {
    size_t row_bytes, required;
    uintptr_t address = (uintptr_t)p;
    if (!p || width < 0 || height < 0 || stride < 0) return 0;
    if ((size_t)width > SIZE_MAX / 4) return 0;
    row_bytes = (size_t)width * 4;
    if ((size_t)stride < row_bytes || bytes > UINTPTR_MAX - address) return 0;
    if (!width || !height) return 1;
    if ((size_t)(height - 1) > (SIZE_MAX - row_bytes) / (size_t)stride) return 0;
    required = (size_t)(height - 1) * (size_t)stride + row_bytes;
    return required <= bytes && required <= (size_t)PTRDIFF_MAX;
}

static int spans_overlap(uintptr_t a, size_t an, uintptr_t b, size_t bn) {
    return an && bn && a < b + bn && b < a + an;
}

EXPORT int sword_alpha_batch(uint8_t *dst, size_t dst_bytes,
                             ptrdiff_t dst_stride, int dst_width, int dst_height,
                             const sword_alpha_tile *tiles, size_t count) {
    size_t i, descriptor_bytes;
    uintptr_t dst_address = (uintptr_t)dst;
    uintptr_t descriptor_address = (uintptr_t)tiles;
    if (count > SWORD_ALPHA_BATCH_LIMIT || (count && !tiles) ||
            !valid_span(dst, dst_bytes, dst_stride, dst_width, dst_height)) return -1;
    descriptor_bytes = count * sizeof(sword_alpha_tile);
    if (descriptor_bytes > UINTPTR_MAX - descriptor_address ||
            spans_overlap(dst_address, dst_bytes, descriptor_address, descriptor_bytes)) return -1;
    for (i = 0; i < count; ++i) {
        const sword_alpha_tile *t = tiles + i;
        if (!valid_span(t->src, t->src_bytes, t->src_stride, t->width, t->height) ||
                spans_overlap(dst_address, dst_bytes, (uintptr_t)t->src, t->src_bytes)) return -1;
    }
    for (i = 0; i < count; ++i) {
        const sword_alpha_tile *t = tiles + i;
        int64_t left = t->left, top = t->top;
        int64_t x0 = left > 0 ? left : 0, y0 = top > 0 ? top : 0;
        int64_t x1 = left + (int64_t)t->width, y1 = top + (int64_t)t->height;
        if (x1 > dst_width) x1 = dst_width;
        if (y1 > dst_height) y1 = dst_height;
        if (x1 <= x0 || y1 <= y0) continue;
        /* Previous validation and clipping prove these offsets are in range. */
        sword_alpha_over(dst + (ptrdiff_t)y0 * dst_stride + (ptrdiff_t)x0 * 4,
                         dst_stride,
                         t->src + (ptrdiff_t)(y0 - top) * t->src_stride +
                                  (ptrdiff_t)(x0 - left) * 4,
                         t->src_stride, (int)(x1 - x0), (int)(y1 - y0), 255, 1);
    }
    return 0;
}

EXPORT const char *sword_alpha_backend(void) {
#if defined(__aarch64__)
    return "aarch64-neon";
#else
    return "scalar-fallback";
#endif
}
