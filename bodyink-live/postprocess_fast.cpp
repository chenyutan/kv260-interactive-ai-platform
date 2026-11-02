/* SPDX-License-Identifier: Apache-2.0
 * BodyInk fixed-shape MoveNet CPU postprocessor.
 * Algorithm derived from Copyright 2019 Xilinx Inc. movenet_imp.cpp (Vitis AI 2.5):
 * https://github.com/Xilinx/Vitis-AI/blob/v2.5/src/Vitis-AI-Library/movenet/src/movenet_imp.cpp
 *
 * Compile without -ffast-math: official integer truncation and floating-point
 * score tie behavior are part of the numerical contract.
 * All tensors are contiguous NHWC with batch=1. Heat:48x48x17,
 * center:48x48x1, reg/offset:48x48x34. out:17x3 [normalized x,y,score].
 * Return 0 on success, -1 for invalid arguments. No allocation or global state.
 */
#include <math.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
#define BODYINK_LINKAGE extern "C"
#else
#define BODYINK_LINKAGE
#endif

#if defined(_WIN32)
#define BODYINK_API BODYINK_LINKAGE __declspec(dllexport)
#else
#define BODYINK_API BODYINK_LINKAGE __attribute__((visibility("default")))
#endif

enum { WIDTH = 48, POINTS = 17, CELLS = WIDTH * WIDTH };

static void sigmoid_lut(float* lut, int fix_point) {
    const float scale = ldexpf(1.0f, -fix_point);
    for (int i = 0; i < 256; ++i) {
        const float val = (i - 128) * scale;
        // Matches upstream sigmoid(float): double exp/divide, returned as float.
        lut[i] = (float)(1.0 / (1.0 + exp(-val * 1.0)));
    }
}

BODYINK_API int bodyink_movenet_decode(
    const int8_t* heat, const int8_t* center, const int8_t* reg,
    const int8_t* offset, int heat_fix, int center_fix, int reg_fix,
    int offset_fix, const float* weights, float threshold, float* out) {
    if (!heat || !center || !reg || !offset || !weights || !out ||
        heat_fix < 0 || heat_fix > 24 || center_fix < 0 || center_fix > 24 ||
        reg_fix < 0 || reg_fix > 24 || offset_fix < 0 || offset_fix > 24 ||
        !isfinite(threshold) || threshold < 0.0f || threshold > 1.0f) {
        return -1;
    }
    float heat_lut[256], center_lut_storage[256];
    sigmoid_lut(heat_lut, heat_fix);
    const float* center_lut = heat_lut;
    if (heat_fix != center_fix) {
        sigmoid_lut(center_lut_storage, center_fix);
        center_lut = center_lut_storage;
    }
    int best_center = -1;
    float max_center = -100.0f;
    for (int i = 0; i < CELLS; ++i) {
        if (!isfinite(weights[i]) || weights[i] < 0.0f) return -1;
        const float val = center_lut[(int)center[i] + 128] * weights[i];
        if (val > max_center) {
            max_center = val;
            best_center = i;
        }
    }
    const int cy = best_center / WIDTH, cx = best_center % WIDTH;
    const float reg_scale = ldexpf(1.0f, -reg_fix);
    const float offset_scale = ldexpf(1.0f, -offset_fix);
    for (int joint = 0; joint < POINTS; ++joint) {
        const int pair = joint * 2;
        // Upstream +0.5 promotes to double, then assigns each squared distance
        // into vector<int>. Keep both steps explicitly.
        const double reg_x = reg[best_center * POINTS * 2 + pair] * reg_scale + 0.5 + cx;
        const double reg_y = reg[best_center * POINTS * 2 + pair + 1] * reg_scale + 0.5 + cy;
        int dx2[WIDTH], dy2[WIDTH];
        for (int i = 0; i < WIDTH; ++i) {
            dx2[i] = (int)((i - reg_x) * (i - reg_x));
            dy2[i] = (int)((i - reg_y) * (i - reg_y));
        }
        float maximum = -100.0f;
        int best = 0;
        for (int y = 0; y < WIDTH; ++y) {
            for (int x = 0; x < WIDTH; ++x) {
                const int cell = y * WIDTH + x;
                const float denominator = (float)(sqrt((double)(dx2[x] + dy2[y])) + 1.8);
                const float candidate = heat_lut[(int)heat[cell * POINTS + joint] + 128]
                    / denominator;
                if (candidate > maximum) {
                    maximum = candidate;
                    best = cell;
                }
            }
        }
        const float score = heat_lut[(int)heat[best * POINTS + joint] + 128];
        const float ox = offset[best * POINTS * 2 + pair] * offset_scale;
        const float oy = offset[best * POINTS * 2 + pair + 1] * offset_scale;
        out[joint * 3] = score < threshold ? -1.0f : ((best % WIDTH) + ox) / WIDTH;
        out[joint * 3 + 1] = score < threshold ? -1.0f : ((best / WIDTH) + oy) / WIDTH;
        out[joint * 3 + 2] = score;
    }
    return 0;
}

// LUT is [256,3] INT8, row-major: lut[value*3 + channel]. RGB stays RGB.
// For MoveNet defaults precompute trunc((value-mean[channel])*scale[channel]*2^fix).
// Pixels is the number of RGB pixels, not the byte count; input/output may not overlap.
BODYINK_API int bodyink_quantize_rgb_lut(
    const uint8_t* rgb, int8_t* out, size_t pixels, const int8_t* lut) {
    if (!rgb || !out || !lut || pixels > 4096u * 4096u) return -1;
    for (size_t i = 0; i < pixels; ++i) {
        out[i * 3] = lut[(size_t)rgb[i * 3] * 3];
        out[i * 3 + 1] = lut[(size_t)rgb[i * 3 + 1] * 3 + 1];
        out[i * 3 + 2] = lut[(size_t)rgb[i * 3 + 2] * 3 + 2];
    }
    return 0;
}
