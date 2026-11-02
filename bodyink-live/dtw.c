/* BodyInk bounded dynamic-time-warping recurrence.
 * Compile: gcc -std=c11 -O2 -fno-fast-math -fPIC -shared dtw.c -o libbodyink_dtw.so -lm
 * cost is contiguous n*m row-major local RMS distance; no cost preprocessing.
 * Allowed steps: (1,0), (0,1), (1,1), with |i-j| <= band.
 * Returns cumulative endpoint cost / (n+m), or -1 for invalid input/no finite path.
 * No allocation or mutable global state. Caller must provide n*m doubles.
 */
#include <math.h>
#include <stddef.h>

#if defined(_WIN32)
#define BODYINK_API __declspec(dllexport)
#else
#define BODYINK_API __attribute__((visibility("default")))
#endif

BODYINK_API double bodyink_dtw_cost(const double* cost, int n, int m, int band) {
    if (!cost || n < 1 || n > 256 || m < 1 || m > 256 || band < 0 || band > 256) return -1.0;
    const int difference = n > m ? n - m : m - n;
    if (difference > band) return -1.0;
    for (size_t i = 0; i < (size_t)n * (size_t)m; ++i) {
        if (!isfinite(cost[i]) || cost[i] < 0.0) return -1.0;
    }
    double row_a[257], row_b[257];
    double* previous = row_a;
    double* current = row_b;
    previous[0] = 0.0;
    for (int j = 1; j <= m; ++j) previous[j] = INFINITY;
    for (int i = 1; i <= n; ++i) {
        for (int j = 0; j <= m; ++j) current[j] = INFINITY;
        const int first = i - band > 1 ? i - band : 1;
        const int last = i + band < m ? i + band : m;
        for (int j = first; j <= last; ++j) {
            double best = previous[j - 1];
            if (previous[j] < best) best = previous[j];
            if (current[j - 1] < best) best = current[j - 1];
            current[j] = cost[(size_t)(i - 1) * (size_t)m + (size_t)(j - 1)] + best;
        }
        double* swap = previous;
        previous = current;
        current = swap;
    }
    if (!isfinite(previous[m])) return -1.0;
    return previous[m] / (n + m);
}
