#include <stdint.h>
#include <stddef.h>
#include <string.h>
/* Validate every span before any write. No access to uncopied frame bytes. */
int bodyink_copy_rows(uint8_t *dst, const uint8_t *src, size_t size,
                     size_t stride, const uint32_t *rows, size_t count,
                     size_t begin, size_t end) {
    if (!dst || !src || !rows || !stride || begin>=end || end>stride) return -1;
    for (size_t i=0;i<count;i++)
        if (size<end || rows[i]>(size-end)/stride) return -2;
    for (size_t i=0;i<count;i++) {
        size_t offset=(size_t)rows[i]*stride+begin;
        memcpy(dst+offset,src+offset,end-begin);
    }
    return 0;
}
