/* Native presentation helper: little-endian Cairo RGB24 BGRX -> RGB565 16LE.
 * Caller supplies separate, non-overlapping buffers and positive row strides.
 * The caller selects this only for a matching 16-bit RGB565 display visual.
 * Build: cc -O3 -std=c99 -shared -fPIC rgb565.c -o libink_rgb565.so
 */
#include <stddef.h>
#include <stdint.h>

#if defined(__aarch64__)
#include <arm_neon.h>
#endif

#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

static int valid_buffers(uint8_t *dst, ptrdiff_t ds, const uint8_t *src,
                         ptrdiff_t ss, int width, int height) {
    return dst && src && width >= 0 && height >= 0 &&
           ds >= (ptrdiff_t)width * 2 && ss >= (ptrdiff_t)width * 4;
}

static inline void convert_row_scalar(uint8_t *dst, const uint8_t *src, int width) {
    int x;
    for (x = 0; x < width; ++x, dst += 2, src += 4) {
        uint16_t value = (uint16_t)(((uint16_t)(src[2] & 0xf8) << 8) |
                                    ((uint16_t)(src[1] & 0xfc) << 3) |
                                    ((uint16_t)src[0] >> 3));
        dst[0] = (uint8_t)value;
        dst[1] = (uint8_t)(value >> 8);
    }
}

EXPORT int sword_rgb24_to_565_scalar(uint8_t *dst, ptrdiff_t dst_stride,
                                     const uint8_t *src, ptrdiff_t src_stride,
                                     int width, int height) {
    int y;
    if (!valid_buffers(dst, dst_stride, src, src_stride, width, height)) return -1;
    for (y = 0; y < height; ++y)
        convert_row_scalar(dst + y * dst_stride, src + y * src_stride, width);
    return 0;
}

#if defined(__aarch64__)
static inline uint16x8_t pack8_565(uint8x8_t b, uint8x8_t g, uint8x8_t r) {
    uint16x8_t red = vshlq_n_u16(vmovl_u8(vand_u8(r, vdup_n_u8(0xf8))), 8);
    uint16x8_t green = vshll_n_u8(vand_u8(g, vdup_n_u8(0xfc)), 3);
    uint16x8_t blue = vmovl_u8(vshr_n_u8(b, 3));
    return vorrq_u16(vorrq_u16(red, green), blue);
}
#endif

EXPORT int sword_rgb24_to_565(uint8_t *dst, ptrdiff_t dst_stride,
                              const uint8_t *src, ptrdiff_t src_stride,
                              int width, int height) {
#if defined(__aarch64__) && (!defined(__BYTE_ORDER__) || __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__)
    int x, y;
    if (!valid_buffers(dst, dst_stride, src, src_stride, width, height)) return -1;
    for (y = 0; y < height; ++y) {
        uint8_t *d = dst + y * dst_stride;
        const uint8_t *s = src + y * src_stride;
        for (x = 0; x + 16 <= width; x += 16, s += 64, d += 32) {
            uint8x16x4_t channels = vld4q_u8(s);
            uint16x8_t low = pack8_565(vget_low_u8(channels.val[0]),
                                      vget_low_u8(channels.val[1]),
                                      vget_low_u8(channels.val[2]));
            uint16x8_t high = pack8_565(vget_high_u8(channels.val[0]),
                                       vget_high_u8(channels.val[1]),
                                       vget_high_u8(channels.val[2]));
            vst1q_u16((uint16_t *)(void *)d, low);
            vst1q_u16((uint16_t *)(void *)(d + 16), high);
        }
        convert_row_scalar(d, s, width - x);
    }
    return 0;
#else
    return sword_rgb24_to_565_scalar(dst, dst_stride, src, src_stride, width, height);
#endif
}

EXPORT const char *sword_rgb565_backend(void) {
#if defined(__aarch64__) && (!defined(__BYTE_ORDER__) || __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__)
    return "aarch64-neon";
#else
    return "scalar-fallback";
#endif
}
