/* Limited-range BT.601 YUYV -> RGB, half-pixel bilinear ROI sampling.
 * The original exact OpenCV fixed-point color conversion is retained. Color
 * conversion clips each source pixel before interpolation, never after YUV
 * interpolation. Precomputed Q11 weights remove per-pixel double division;
 * the total weighted sum fits int32 (255 * 2048 * 2048 < INT_MAX).
 * No allocation or mutable global state; all scratch data belongs to this call.
 */
#include <stdint.h>
#include <stddef.h>

enum { COEF = 2048, COEF_BITS = 11, MAX_OUTPUT_WIDTH = 1920 };
typedef struct { uint16_t x0, x1, alpha; } Column;
typedef struct { int r, g, b; } Rgb;
typedef struct { int y[256], vr[256], ug[256], vg[256], ub[256]; } ColorLut;

static inline int clip(int v) { return v < 0 ? 0 : (v > 255 ? 255 : v); }

static void init_color_lut(ColorLut* lut) {
    for (int i = 0; i < 256; ++i) {
        lut->y[i] = (i > 16 ? i - 16 : 0) * 1220542 + (1 << 19);
        lut->vr[i] = (i - 128) * 1673527;
        lut->ug[i] = (i - 128) * 409993;
        lut->vg[i] = (i - 128) * 852492;
        lut->ub[i] = (i - 128) * 2116026;
    }
}

static inline Rgb from_yuv(const ColorLut* lut, int yy, int vr, int green, int ub) {
    const int y = lut->y[yy];
    const Rgb result = {clip((y + vr) >> 20), clip((y - green) >> 20), clip((y + ub) >> 20)};
    return result;
}

/* Horizontal Q11 RGB. Adjacent pixels in a YUYV pair share UV contributions;
 * alpha=0 needs only one pixel. */
static inline __attribute__((always_inline)) Rgb sample_row(const uint8_t* row, const Column* col, const ColorLut* lut) {
    const int x0 = col->x0, x1 = col->x1, alpha = col->alpha;
    const uint8_t* a = row + (x0 & ~1) * 2;
    const int vr = lut->vr[a[3]], green = lut->ug[a[1]] + lut->vg[a[3]], ub = lut->ub[a[1]];
    const Rgb p0 = from_yuv(lut, a[(x0 & 1) * 2], vr, green, ub);
    if (!alpha) {
        const Rgb result = {p0.r * COEF, p0.g * COEF, p0.b * COEF};
        return result;
    }
    Rgb p1;
    if ((x0 & ~1) == (x1 & ~1)) {
        p1 = from_yuv(lut, a[(x1 & 1) * 2], vr, green, ub);
    } else {
        const uint8_t* b = row + (x1 & ~1) * 2;
        p1 = from_yuv(lut, b[(x1 & 1) * 2], lut->vr[b[3]], lut->ug[b[1]] + lut->vg[b[3]], lut->ub[b[1]]);
    }
    const Rgb result = {p0.r * (COEF - alpha) + p1.r * alpha,
                       p0.g * (COEF - alpha) + p1.g * alpha,
                       p0.b * (COEF - alpha) + p1.b * alpha};
    return result;
}

int bodyink_yuyv_roi(const uint8_t* src, int width, int height, int stride,
                    uint8_t* dst, int ow, int oh, int rx, int ry, int rw, int rh) {
    if (!src || !dst || width < 2 || width > 4096 || width % 2 || height < 1 || height > 4096 ||
        stride < width * 2 || ow < 1 || oh < 1 || ow > MAX_OUTPUT_WIDTH || oh > 1080 ||
        rx < 0 || ry < 0 || rw < 1 || rh < 1 || rx > width - rw || ry > height - rh) return -1;
    Column columns[MAX_OUTPUT_WIDTH];
    ColorLut color;
    init_color_lut(&color);
    for (int i = 0; i < ow; ++i) {
        double fx = rx + (i + 0.5) * rw / ow - 0.5;
        if (fx < rx) fx = rx;
        if (fx > rx + rw - 1) fx = rx + rw - 1;
        int x0 = (int)fx, x1 = x0 + 1 < width ? x0 + 1 : x0;
        int alpha = (int)((fx - x0) * COEF + 0.5);
        if (alpha == COEF) { x0 = x1; alpha = 0; }
        columns[i] = (Column){(uint16_t)x0, (uint16_t)x1, (uint16_t)alpha};
    }
    for (int j = 0; j < oh; ++j) {
        double fy = ry + (j + 0.5) * rh / oh - 0.5;
        if (fy < ry) fy = ry;
        if (fy > ry + rh - 1) fy = ry + rh - 1;
        int y0 = (int)fy, y1 = y0 + 1 < height ? y0 + 1 : y0;
        int beta = (int)((fy - y0) * COEF + 0.5);
        if (beta == COEF) { y0 = y1; beta = 0; }
        const uint8_t* row0 = src + (size_t)y0 * stride;
        const uint8_t* row1 = src + (size_t)y1 * stride;
        uint8_t* output = dst + (size_t)j * ow * 3;
        if (!beta) {
            for (int i = 0; i < ow; ++i) {
                const Rgb a = sample_row(row0, &columns[i], &color);
                output[i * 3] = (uint8_t)((a.r + COEF / 2) >> COEF_BITS);
                output[i * 3 + 1] = (uint8_t)((a.g + COEF / 2) >> COEF_BITS);
                output[i * 3 + 2] = (uint8_t)((a.b + COEF / 2) >> COEF_BITS);
            }
        } else {
            for (int i = 0; i < ow; ++i) {
                const Rgb a = sample_row(row0, &columns[i], &color);
                const Rgb b = sample_row(row1, &columns[i], &color);
                output[i * 3] = (uint8_t)((a.r * (COEF - beta) + b.r * beta + (1 << 21)) >> 22);
                output[i * 3 + 1] = (uint8_t)((a.g * (COEF - beta) + b.g * beta + (1 << 21)) >> 22);
                output[i * 3 + 2] = (uint8_t)((a.b * (COEF - beta) + b.b * beta + (1 << 21)) >> 22);
            }
        }
    }
    return 0;
}

int bodyink_yuyv_rgb(const uint8_t* src, int width, int height, int stride,
                    uint8_t* dst, int ow, int oh) {
    return bodyink_yuyv_roi(src, width, height, stride, dst, ow, oh, 0, 0, width, height);
}
