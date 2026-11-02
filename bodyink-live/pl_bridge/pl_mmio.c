#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stddef.h>
#include <time.h>
#include <errno.h>
static void barrier(void) {
#if defined(__aarch64__)
  __asm__ volatile("dsb sy" ::: "memory");
#else
  __sync_synchronize();
#endif
}
uint32_t bodyink_read32(void *base, size_t offset) {
  barrier();
  uint32_t value = *(volatile uint32_t *)((uint8_t *)base + offset);
  barrier();
  return value;
}
void bodyink_write32(void *base, size_t offset, uint32_t value) {
  barrier();
  *(volatile uint32_t *)((uint8_t *)base + offset) = value;
  barrier();
}

// ctypes releases the GIL across this call. Keep the same 100 us polling
// interval and barriers, without repeatedly scheduling Python during DMA.
// Return 0 for drained completion, 1 for timeout, -1 for invalid/system error.
// No command writes or buffer release: Python retains cancellation ownership.
int bodyink_wait_done(void *base, uint64_t timeout_ns, uint32_t *status) {
  if (!base || !status || timeout_ns > 10000000000ULL) return -1;
  struct timespec now;
  if (clock_gettime(CLOCK_MONOTONIC,&now)) return -1;
  uint64_t deadline=(uint64_t)now.tv_sec*1000000000ULL+now.tv_nsec+timeout_ns;
  for (;;) {
    *status=bodyink_read32(base,8);
    if ((*status & 3U)==2U) return 0;
    if (clock_gettime(CLOCK_MONOTONIC,&now)) return -1;
    if ((uint64_t)now.tv_sec*1000000000ULL+now.tv_nsec>=deadline) return 1;
    struct timespec pause={0,100000};
    if (nanosleep(&pause,NULL) && errno!=EINTR) return -1;
  }
}
