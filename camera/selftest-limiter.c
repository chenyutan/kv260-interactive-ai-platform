/* Real C snapshot producer with a 60 Hz timer in place of V4L2. Tests IPC and
 * rate scheduling under injected 25 ms work, not camera throughput. */
#define main bodyink_capture_main
#include "bodyink-camera.c"
#undef main
#include <assert.h>
#include <math.h>
#include <sys/timerfd.h>

static void test_poll_phase_regression(void)
{
    struct stats s = {0};
    struct snapshot_request request = {.fd = -1};
    unsigned old_rejected = 0;
    double previous_old_delivery = -1, next_allowed = 0;
    for (unsigned i = 0; i < 480; ++i) {
        double accepted = (double)i / 8.0;
        double stale_pre_poll = floor(accepted * 60.0) / 60.0;
        double work = i % 6U == 0 ? 0.025 : 0.002;
        if (stale_pre_poll - previous_old_delivery < 0.1) ++old_rejected;
        else previous_old_delivery = ceil((accepted + work) * 60.0) / 60.0;
        begin_snapshot_request(&request, 123, accepted, next_allowed, &s);
        double ready_at = request.eligible_at > accepted + work ? request.eligible_at : accepted + work;
        double delivery = ceil(ready_at * 60.0) / 60.0;
        /* A request is serviced on an eligible frame, even when rate limited;
         * no request is rejected because its pre-poll clock was stale. */
        assert(snapshot_request_ready(&request, delivery));
        assert(delivery - accepted < 0.2);
        next_allowed = delivery + 0.1;
    }
    assert(old_rejected > 0);
    begin_snapshot_request(&request, 123, 10.0, 10.09, &s);
    assert(!snapshot_request_ready(&request, 10.08));
    assert(snapshot_request_ready(&request, 10.1));
    printf("phase regression: 480 requests at 8 Hz; old_rejected=%u; new_delivered=480; all waits < 200 ms\n", old_rejected);
}

int main(int argc, char **argv)
{
    if (argc != 3) return 64;
    unsigned target = parse_uint(argv[2], 1, 1000);
    test_poll_phase_regression();
    char path[512];
    if (path_join(path, sizeof(path), argv[1], "snapshot.sock")) return 64;
    int listener = create_listener(path);
    if (listener < 0) return 1;
    int timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    if (timer < 0) return 1;
    struct itimerspec timing = {.it_interval = {.tv_nsec = 16666667}, .it_value = {.tv_nsec = 16666667}};
    if (timerfd_settime(timer, 0, &timing, NULL)) return 1;
    unsigned char *pixels = malloc(FRAME_BYTES);
    if (!pixels) return 1;
    for (size_t i = 0; i < FRAME_BYTES; ++i) pixels[i] = (unsigned char)(i % 251);
    struct stats s = {.started = monotonic_seconds()};
    struct snapshot_request request = {.fd = -1};
    double next_allowed = 0;
    uint32_t sequence = 0;
    while (s.snapshots < target && monotonic_seconds() - s.started < 60) {
        struct pollfd pending[2] = {{.fd = timer, .events = POLLIN}, {.fd = request.fd < 0 ? listener : -1, .events = POLLIN}};
        if (poll(pending, 2, 2000) < 0) return 1;
        if (pending[1].revents & POLLIN) {
            int client = accept4(listener, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
            if (client >= 0) begin_snapshot_request(&request, client, monotonic_seconds(), next_allowed, &s);
        }
        if (!(pending[0].revents & POLLIN)) continue;
        uint64_t expirations;
        if (read(timer, &expirations, sizeof(expirations)) != (ssize_t)sizeof(expirations)) return 1;
        sequence += (uint32_t)expirations;
        if (sequence % 7U == 0) {
            struct timespec delay = {.tv_nsec = 25000000};
            while (nanosleep(&delay, &delay) && errno == EINTR) { }
        }
        double now = monotonic_seconds();
        if (!snapshot_request_ready(&request, now)) continue;
        struct v4l2_buffer buffer = {.bytesused = FRAME_BYTES, .sequence = sequence, .flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC};
        buffer.timestamp.tv_sec = (long)now;
        buffer.timestamp.tv_usec = (long)((now - (double)buffer.timestamp.tv_sec) * 1e6);
        double wait = now - request.accepted_at;
        if (wait > s.snapshot_wait_max_seconds) s.snapshot_wait_max_seconds = wait;
        if (send_snapshot(request.fd, pixels, &buffer)) ++s.snapshot_failures;
        else ++s.snapshots;
        next_allowed = now + 0.1;
        close_snapshot_request(&request);
    }
    close_snapshot_request(&request);
    close(timer); close(listener); unlink(path); free(pixels);
    struct config c = {.directory = argv[1], .exposure = 100};
    if (write_status(&c, &s, "stopped", "limiter_fixture_complete")) return 1;
    return s.snapshots == target && !s.snapshot_failures ? 0 : 1;
}
