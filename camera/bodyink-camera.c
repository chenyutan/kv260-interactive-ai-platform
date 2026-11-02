#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/memfd.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

/* A capture owner, not an encoder. No raw recording and no per-frame copy
 * unless a client requests a snapshot. Each socket response owns a sealed
 * memfd, so readers cannot observe a frame being overwritten. */
#define WIDTH 1920U
#define HEIGHT 1080U
#define FRAME_BYTES (WIDTH * HEIGHT * 2U)
#define PARAM_PATH "/sys/module/uvcvideo/parameters/bodyink_ss_bandwidth"
#define DEFAULT_DEVICE "/dev/v4l/by-id/usb-Generic_USB_Camera3___BODYINK_CAMERA_SERIAL__-video-index0"
#define MAX_BUFFERS 16U
#define RATE_CHECK_SECONDS 10.0
#define RATE_MIN_GOOD_FPS 55.0
#define RATE_STRICT_SLOW_WINDOWS 3U

static volatile sig_atomic_t stopping;
/* At the camera's native 60 Hz, DQBUF is the rate limiter: there is at
 * most one response for each newly dequeued frame. A second wall-clock
 * 1/60 deadline aliases with arrival jitter and can halve delivery rate.
 * Lower configured caps retain their original strict time limiter. */
static double next_snapshot_deadline(double started, unsigned fps)
{
    return fps == 60 ? 0.0 : started + 1.0 / fps;
}
struct mapping { void *data; size_t length; };
struct snapshot_request {
    int fd;
    double accepted_at, eligible_at;
};
struct rate_health {
    double last_check_time, next_check_time;
    double window_seconds, window_good_fps;
    uint64_t last_good_frames, checked_windows;
    unsigned slow_windows;
    bool degraded;
};
enum rate_check_result {
    RATE_NOT_CHECKED, RATE_HEALTHY, RATE_DEGRADED, RATE_STRICT_FAILED
};
/* This rate policy is separate from no-frame/usable-frame timeouts. A slow
 * stream can still serve valid frames; strict capture acceptance is opt-in. */
static void start_rate_health(struct rate_health *rate, double now, uint64_t good_frames)
{
    *rate = (struct rate_health){.last_check_time = now,
        .next_check_time = now + RATE_CHECK_SECONDS, .last_good_frames = good_frames};
}
static enum rate_check_result check_rate_health(struct rate_health *rate,
                                               double now, uint64_t good_frames,
                                               bool strict)
{
    if (now < rate->next_check_time) return RATE_NOT_CHECKED;
    rate->window_seconds = now - rate->last_check_time;
    rate->window_good_fps = (double)(good_frames - rate->last_good_frames) / rate->window_seconds;
    rate->degraded = rate->window_good_fps < RATE_MIN_GOOD_FPS;
    if (!rate->degraded) rate->slow_windows = 0;
    else if (rate->slow_windows < UINT_MAX) ++rate->slow_windows;
    rate->last_good_frames = good_frames;
    rate->last_check_time = now;
    rate->next_check_time = now + RATE_CHECK_SECONDS;
    ++rate->checked_windows;
    if (strict && rate->slow_windows >= RATE_STRICT_SLOW_WINDOWS) return RATE_STRICT_FAILED;
    return rate->degraded ? RATE_DEGRADED : RATE_HEALTHY;
}
struct stats {
    uint64_t frames, good_frames, sequence_gaps, sequence_resets;
    uint64_t error_frames, short_frames, poll_timeouts, timestamp_discontinuities;
    uint64_t snapshots, snapshot_failures, snapshot_rejected;
    uint64_t snapshot_deferred, snapshot_cancelled;
    double snapshot_wait_max_seconds;
    uint32_t last_sequence, last_flags, last_bytes;
    bool have_sequence, have_timestamp;
    double first_timestamp, last_timestamp, previous_timestamp;
    double started, first_arrival, last_arrival, first_good_timestamp, last_good_timestamp;
    double last_report_time, last_report_timestamp;
    uint64_t last_report_frames;
    struct rate_health rate;
};
struct config {
    const char *device, *directory;
    unsigned exposure, interval, timeout, buffers, snapshot_fps;
    double duration;
    bool strict_rate_check;
};

static void signal_stop(int sig) { (void)sig; stopping = 1; }
static double monotonic_seconds(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) { perror("clock_gettime"); exit(1); }
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static int xioctl(int fd, unsigned long request, void *argument)
{
    int r;
    do { r = ioctl(fd, request, argument); } while (r < 0 && errno == EINTR && !stopping);
    return r;
}
static int set_control(int fd, uint32_t id, int value, const char *name)
{
    struct v4l2_control c = {.id = id, .value = value};
    if (xioctl(fd, VIDIOC_S_CTRL, &c) < 0) { fprintf(stderr, "set %s: %s\n", name, strerror(errno)); return -1; }
    c.value = -1;
    if (xioctl(fd, VIDIOC_G_CTRL, &c) < 0 || c.value != value) {
        fprintf(stderr, "control %s readback mismatch: wanted %d, got %d\n", name, value, c.value); return -1;
    }
    return 0;
}
static int path_join(char *out, size_t capacity, const char *directory, const char *name)
{
    int n = snprintf(out, capacity, "%s/%s", directory, name);
    return n < 0 || (size_t)n >= capacity ? -1 : 0;
}
static int write_status(const struct config *c, struct stats *s, const char *state, const char *reason)
{
    char path[512], temporary[540];
    if (path_join(path, sizeof(path), c->directory, "status.json")) return -1;
    snprintf(temporary, sizeof(temporary), "%s.%ld.tmp", path, (long)getpid());
    FILE *f = fopen(temporary, "w");
    if (!f) { perror("status fopen"); return -1; }
    double now = monotonic_seconds();
    double elapsed = s->last_timestamp - s->first_timestamp;
    double arrival_elapsed = s->last_arrival - s->first_arrival;
    double window_time = s->last_timestamp - s->last_report_timestamp;
    double fps_ts = s->frames > 1 && elapsed > 0 ? (double)(s->frames - 1) / elapsed : 0;
    double fps_mono = s->frames > 1 && arrival_elapsed > 0 ? (double)(s->frames - 1) / arrival_elapsed : 0;
    double good_elapsed = s->last_good_timestamp - s->first_good_timestamp;
    double good_fps = s->good_frames > 1 && good_elapsed > 0 ? (double)(s->good_frames - 1) / good_elapsed : 0;
    double window_fps = s->last_report_frames && window_time > 0 ? (double)(s->frames - s->last_report_frames) / window_time : fps_ts;
    /* state/reason are program constants. The configurable path is not emitted
     * into JSON, avoiding an unescaped path invalidating machine-readable status. */
    fprintf(f,
        "{\n  \"schema_version\": 1, \"state\": \"%s\", \"reason\": \"%s\",\n"
        "  \"pid\": %ld, \"width\": %u, \"height\": %u, \"fourcc\": \"YUYV\", \"nominal_fps\": 60,\n"
        "  \"frame_bytes\": %u, \"exposure_100us\": %u, \"uptime_seconds\": %.6f, \"updated_monotonic_seconds\": %.6f,\n"
        "  \"frames\": %" PRIu64 ", \"good_frames\": %" PRIu64 ", \"fps_timestamp\": %.6f, \"fps_monotonic\": %.6f, \"window_fps_timestamp\": %.6f, \"good_fps_timestamp\": %.6f,\n"
        "  \"rate_health\": \"%s\", \"rate_degraded\": %s, \"strict_rate_check\": %s, \"rate_window_good_fps_monotonic\": %.6f,\n"
        "  \"rate_window_seconds\": %.6f, \"rate_checked_windows\": %" PRIu64 ", \"rate_slow_windows\": %u,\n"
        "  \"sequence_gaps\": %" PRIu64 ", \"sequence_resets\": %" PRIu64 ", \"error_frames\": %" PRIu64 ", \"short_frames\": %" PRIu64 ",\n"
        "  \"poll_timeouts\": %" PRIu64 ", \"timestamp_discontinuities\": %" PRIu64 ", \"last_sequence\": %u, \"last_flags\": %u, \"last_bytes\": %u,\n"
        "  \"last_frame_timestamp_seconds\": %.6f, \"snapshot_count\": %" PRIu64 ", \"snapshot_failures\": %" PRIu64 ", \"snapshot_rejected\": %" PRIu64 ",\n"
        "  \"snapshot_deferred\": %" PRIu64 ", \"snapshot_cancelled\": %" PRIu64 ", \"snapshot_wait_max_seconds\": %.6f\n}\n",
        state, reason, (long)getpid(), WIDTH, HEIGHT, FRAME_BYTES, c->exposure, now - s->started, now,
        s->frames, s->good_frames, fps_ts, fps_mono, window_fps, good_fps,
        s->rate.degraded ? "degraded" : "healthy", s->rate.degraded ? "true" : "false",
        c->strict_rate_check ? "true" : "false", s->rate.window_good_fps,
        s->rate.window_seconds, s->rate.checked_windows, s->rate.slow_windows,
        s->sequence_gaps, s->sequence_resets, s->error_frames, s->short_frames,
        s->poll_timeouts, s->timestamp_discontinuities, s->last_sequence, s->last_flags, s->last_bytes,
        s->last_timestamp, s->snapshots, s->snapshot_failures, s->snapshot_rejected,
        s->snapshot_deferred, s->snapshot_cancelled, s->snapshot_wait_max_seconds);
    int bad = ferror(f);
    if (fclose(f)) bad = 1;
    if (bad || rename(temporary, path)) { perror("status update"); unlink(temporary); return -1; }
    printf("state=%s frames=%" PRIu64 " fps=%.4f window_fps=%.4f rate_health=%s good_window_fps=%.4f slow_windows=%u gaps=%" PRIu64 " errors=%" PRIu64 " short=%" PRIu64 " snapshots=%" PRIu64 " uptime=%.1f\n",
           state, s->frames, fps_ts, window_fps, s->rate.degraded ? "degraded" : "healthy",
           s->rate.window_good_fps, s->rate.slow_windows, s->sequence_gaps,
           s->error_frames, s->short_frames, s->snapshots, now - s->started);
    fflush(stdout);
    s->last_report_time = now;
    s->last_report_timestamp = s->last_timestamp;
    s->last_report_frames = s->frames;
    return 0;
}
static int create_listener(const char *path)
{
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(address.sun_path)) { errno = ENAMETOOLONG; return -1; }
    strcpy(address.sun_path, path);
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    unlink(path); /* Protected by capture.lock; only our runtime-directory socket. */
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) || chmod(path, 0660) || listen(fd, 4)) {
        int saved = errno; close(fd); errno = saved; return -1;
    }
    return fd;
}
static int send_snapshot(int client, const void *frame, const struct v4l2_buffer *b)
{
    int fd = (int)syscall(SYS_memfd_create, "bodyink-yuyv-frame", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (fd < 0) return -1;
    size_t offset = 0;
    while (offset < FRAME_BYTES) {
        ssize_t n = write(fd, (const char *)frame + offset, FRAME_BYTES - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { close(fd); return -1; }
        offset += (size_t)n;
    }
    if (fcntl(fd, F_ADD_SEALS, F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE | F_SEAL_SEAL) < 0) { close(fd); return -1; }
    char metadata[384];
    int length = snprintf(metadata, sizeof(metadata),
        "{\"schema_version\":1,\"width\":1920,\"height\":1080,\"stride\":3840,\"fourcc\":\"YUYV\",\"bytes\":%u,\"sequence\":%u,\"flags\":%u,\"timestamp_seconds\":%.6f,\"sealed_memfd\":true}",
        b->bytesused, b->sequence, b->flags, (double)b->timestamp.tv_sec + (double)b->timestamp.tv_usec / 1e6);
    struct iovec iov = {.iov_base = metadata, .iov_len = (size_t)length};
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1, .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    struct cmsghdr *ancillary = CMSG_FIRSTHDR(&message);
    ancillary->cmsg_level = SOL_SOCKET;
    ancillary->cmsg_type = SCM_RIGHTS;
    ancillary->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(ancillary), &fd, sizeof(fd));
    ssize_t n = sendmsg(client, &message, MSG_NOSIGNAL | MSG_DONTWAIT);
    close(fd);
    return n == length ? 0 : -1;
}
static void begin_snapshot_request(struct snapshot_request *request, int fd,
                                   double accepted_at, double next_allowed,
                                   struct stats *s)
{
    request->fd = fd;
    request->accepted_at = accepted_at;
    request->eligible_at = accepted_at > next_allowed ? accepted_at : next_allowed;
    if (request->eligible_at > accepted_at) ++s->snapshot_deferred;
}
static bool snapshot_request_ready(const struct snapshot_request *request, double now)
{
    return request->fd >= 0 && now >= request->eligible_at;
}
static void close_snapshot_request(struct snapshot_request *request)
{
    if (request->fd >= 0) close(request->fd);
    request->fd = -1;
}
static unsigned parse_uint(const char *text, unsigned minimum, unsigned maximum)
{
    char *end;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !text[0] || *end || value < minimum || value > maximum) {
        fprintf(stderr, "invalid numeric option: %s\n", text); exit(64);
    }
    return (unsigned)value;
}
int main(int argc, char **argv)
{
    struct config c = {.device = DEFAULT_DEVICE, .directory = "/run/bodyink-camera", .exposure = 100, .interval = 10, .timeout = 3, .buffers = 8, .snapshot_fps = 10};
    const struct option options[] = {
        {"device", required_argument, NULL, 'd'}, {"runtime-dir", required_argument, NULL, 'r'},
        {"exposure", required_argument, NULL, 'e'}, {"stats-interval", required_argument, NULL, 'i'},
        {"timeout", required_argument, NULL, 't'}, {"buffers", required_argument, NULL, 'b'},
        {"snapshot-fps", required_argument, NULL, 's'}, {"duration", required_argument, NULL, 'D'},
        {"strict-rate-check", no_argument, NULL, 'S'},
        {"help", no_argument, NULL, 'h'}, {NULL, 0, NULL, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "d:r:e:i:t:b:s:D:Sh", options, NULL)) != -1) {
        switch (opt) {
        case 'd': c.device = optarg; break;
        case 'r': c.directory = optarg; break;
        case 'e': c.exposure = parse_uint(optarg, 1, 160); break;
        case 'i': c.interval = parse_uint(optarg, 1, 60); break;
        case 't': c.timeout = parse_uint(optarg, 1, 30); break;
        case 'b': c.buffers = parse_uint(optarg, 3, MAX_BUFFERS); break;
        case 's': c.snapshot_fps = parse_uint(optarg, 1, 60); break;
        case 'S': c.strict_rate_check = true; break;
        case 'D': c.duration = parse_uint(optarg, 1, 86400); break;
        case 'h': puts("bodyink-camera [--device /dev/v4l/by-id/...] [--runtime-dir /run/bodyink-camera]\n  [--exposure 100] [--stats-interval 10] [--timeout 3] [--buffers 8]\n  [--snapshot-fps 10] [--duration seconds] [--strict-rate-check]\nCapture is strictly 1920x1080 YUYV 60 Hz. Exposure is in 100-us units (100 = 10 ms).\nLow measured rate is reported as degraded; --strict-rate-check fails after three consecutive 10-second windows below 55 good fps.\nSnapshot clients receive one JSON message and a sealed memfd via SCM_RIGHTS."); return 0;
        default: return 64;
        }
    }
    if (optind != argc) { fprintf(stderr, "unexpected positional argument\n"); return 64; }
    signal(SIGINT, signal_stop); signal(SIGTERM, signal_stop); signal(SIGPIPE, SIG_IGN);
    struct stats s = {.started = monotonic_seconds()};
    s.last_report_time = s.started;
    struct mapping maps[MAX_BUFFERS] = {{0}};
    unsigned mapped = 0;
    int camera = -1, listener = -1, lock = -1, result = 1;
    struct snapshot_request snapshot = {.fd = -1};
    bool streaming = false, lock_owned = false;
    const char *reason = "initialization_failed";
    char socket_path[512] = {0}, lock_path[512];
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (mkdir(c.directory, 0770) && errno != EEXIST) { perror("runtime directory"); return 73; }
    if (path_join(lock_path, sizeof(lock_path), c.directory, "capture.lock") ||
        path_join(socket_path, sizeof(socket_path), c.directory, "snapshot.sock")) { fprintf(stderr, "runtime path too long\n"); return 64; }
    lock = open(lock_path, O_RDWR | O_CREAT | O_CLOEXEC, 0660);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) { fprintf(stderr, "capture owner already running or lock unavailable\n"); if (lock >= 0) close(lock); return 75; }
    lock_owned = true;
    FILE *parameter = fopen(PARAM_PATH, "r");
    int enabled = parameter ? fgetc(parameter) : 0;
    if (parameter) fclose(parameter);
    if (enabled != 'Y' && enabled != '1') {
        fprintf(stderr, "required opt-in UVC bandwidth correction is absent/disabled: %s\n", PARAM_PATH);
        reason = "bandwidth_patch_unavailable"; result = 78; goto cleanup;
    }
    camera = open(c.device, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (camera < 0) { perror(c.device); reason = "camera_open_failed"; result = 69; goto cleanup; }
    struct v4l2_capability capability = {0};
    if (xioctl(camera, VIDIOC_QUERYCAP, &capability) < 0) { perror("QUERYCAP"); goto cleanup; }
    uint32_t caps = capability.capabilities & V4L2_CAP_DEVICE_CAPS ? capability.device_caps : capability.capabilities;
    if ((caps & (V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING)) != (V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING)) {
        fprintf(stderr, "device does not support single-plane streaming capture\n"); result = 78; goto cleanup;
    }
    struct v4l2_format format = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    format.fmt.pix.width = WIDTH; format.fmt.pix.height = HEIGHT;
    format.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV; format.fmt.pix.field = V4L2_FIELD_NONE;
    if (xioctl(camera, VIDIOC_S_FMT, &format) < 0) { perror("S_FMT (another capture process may own the device)"); reason = "format_busy_or_failed"; goto cleanup; }
    if (format.fmt.pix.width != WIDTH || format.fmt.pix.height != HEIGHT || format.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV ||
        format.fmt.pix.bytesperline != WIDTH * 2U || format.fmt.pix.sizeimage != FRAME_BYTES) {
        fprintf(stderr, "driver changed requested 1920x1080 tightly packed YUYV format\n"); result = 78; reason = "format_mismatch"; goto cleanup;
    }
    struct v4l2_streamparm rate = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    rate.parm.capture.timeperframe.numerator = 1; rate.parm.capture.timeperframe.denominator = 60;
    if (xioctl(camera, VIDIOC_S_PARM, &rate) < 0) { perror("S_PARM"); goto cleanup; }
    memset(&rate, 0, sizeof(rate)); rate.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(camera, VIDIOC_G_PARM, &rate) < 0) { perror("G_PARM"); goto cleanup; }
    double nominal = rate.parm.capture.timeperframe.numerator ? (double)rate.parm.capture.timeperframe.denominator / rate.parm.capture.timeperframe.numerator : 0;
    if (nominal < 59.9 || nominal > 60.1) { fprintf(stderr, "driver did not accept 60 Hz: %.6f Hz\n", nominal); result = 78; reason = "nominal_fps_mismatch"; goto cleanup; }
    if (set_control(camera, V4L2_CID_EXPOSURE_AUTO, V4L2_EXPOSURE_MANUAL, "manual exposure") ||
        set_control(camera, V4L2_CID_EXPOSURE_AUTO_PRIORITY, 0, "dynamic framerate disabled") ||
        set_control(camera, V4L2_CID_EXPOSURE_ABSOLUTE, (int)c.exposure, "exposure duration")) { reason = "exposure_control_failed"; goto cleanup; }
    struct v4l2_requestbuffers request = {.count = c.buffers, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP};
    if (xioctl(camera, VIDIOC_REQBUFS, &request) < 0) { perror("REQBUFS (camera busy?)"); reason = "buffers_busy_or_failed"; goto cleanup; }
    if (request.count < 3 || request.count > MAX_BUFFERS) { fprintf(stderr, "unsupported allocated buffer count %u\n", request.count); goto cleanup; }
    for (unsigned i = 0; i < request.count; ++i) {
        struct v4l2_buffer buffer = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP, .index = i};
        if (xioctl(camera, VIDIOC_QUERYBUF, &buffer) < 0) { perror("QUERYBUF"); goto cleanup; }
        maps[i].length = buffer.length;
        maps[i].data = mmap(NULL, buffer.length, PROT_READ | PROT_WRITE, MAP_SHARED, camera, buffer.m.offset);
        if (maps[i].data == MAP_FAILED) { maps[i].data = NULL; perror("mmap"); goto cleanup; }
        ++mapped;
        if (xioctl(camera, VIDIOC_QBUF, &buffer) < 0) { perror("initial QBUF"); goto cleanup; }
    }
    listener = create_listener(socket_path);
    if (listener < 0) { perror("snapshot listener"); goto cleanup; }
    if (xioctl(camera, VIDIOC_STREAMON, &type) < 0) { perror("STREAMON"); reason = "streamon_failed"; goto cleanup; }
    streaming = true;
    printf("capture started device=%s nominal_fps=%.6f buffers=%u exposure_ms=%.1f\n", c.device, nominal, request.count, (double)c.exposure / 10);
    if (write_status(&c, &s, "starting", "awaiting_first_frame")) { reason = "status_write_failed"; goto cleanup; }
    double last_frame = monotonic_seconds(), last_good_frame = last_frame, next_snapshot_allowed = 0;
    start_rate_health(&s.rate, last_frame, s.good_frames);
    while (!stopping) {
        double now = monotonic_seconds();
        if (c.duration && now - s.started >= c.duration) { reason = "duration_complete"; result = 0; break; }
        if (now - last_frame >= c.timeout) { ++s.poll_timeouts; reason = "frame_timeout"; fprintf(stderr, "camera produced no frame for %u seconds\n", c.timeout); goto cleanup; }
        if (now - last_good_frame >= c.timeout) { reason = "usable_frame_timeout"; fprintf(stderr, "camera produced no complete error-free frame for %u seconds\n", c.timeout); goto cleanup; }
        struct pollfd pollfds[3] = {
            {.fd = camera, .events = POLLIN},
            {.fd = snapshot.fd < 0 ? listener : -1, .events = POLLIN},
            {.fd = snapshot.fd, .events = 0}
        };
        int ready = poll(pollfds, 3, 200);
        if (ready < 0) { if (errno == EINTR) continue; perror("poll"); reason = "poll_failed"; goto cleanup; }
        if (pollfds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) { reason = "camera_disconnected_or_poll_error"; goto cleanup; }
        if (pollfds[2].revents & (POLLHUP | POLLERR | POLLNVAL)) {
            close_snapshot_request(&snapshot); ++s.snapshot_cancelled;
        }
        if (pollfds[1].revents & POLLIN) {
            int client = accept4(listener, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
            if (client >= 0) {
                /* poll may have waited a frame period or longer. Acceptance
                 * must use a fresh clock, not the pre-poll loop timestamp.
                 * Keep one small pending request and continue requeueing
                 * camera frames until its delivery slot is available. */
                begin_snapshot_request(&snapshot, client, monotonic_seconds(), next_snapshot_allowed, &s);
            }
        }
        if (!(pollfds[0].revents & POLLIN)) continue;
        struct v4l2_buffer buffer = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP};
        if (xioctl(camera, VIDIOC_DQBUF, &buffer) < 0) {
            if (errno == EAGAIN || (errno == EINTR && stopping)) continue;
            perror("DQBUF"); reason = "dequeue_failed"; goto cleanup;
        }
        now = monotonic_seconds(); last_frame = now;
        if (buffer.index >= mapped) { reason = "invalid_buffer_index"; goto cleanup; }
        bool good = true;
        if (buffer.flags & V4L2_BUF_FLAG_ERROR) { ++s.error_frames; good = false; }
        if (buffer.bytesused != FRAME_BYTES || buffer.bytesused > maps[buffer.index].length) { ++s.short_frames; good = false; }
        if (s.have_sequence) {
            uint32_t delta = buffer.sequence - s.last_sequence;
            if (delta > 0 && delta < UINT32_C(0x80000000)) s.sequence_gaps += delta - 1U;
            else ++s.sequence_resets;
        }
        s.last_sequence = buffer.sequence; s.have_sequence = true;
        s.last_flags = buffer.flags; s.last_bytes = buffer.bytesused;
        double timestamp = (double)buffer.timestamp.tv_sec + (double)buffer.timestamp.tv_usec / 1e6;
        if (s.have_timestamp && timestamp <= s.previous_timestamp) ++s.timestamp_discontinuities;
        if (!s.have_timestamp) { s.first_timestamp = timestamp; s.first_arrival = now; s.have_timestamp = true; }
        s.last_timestamp = timestamp; s.previous_timestamp = timestamp; s.last_arrival = now;
        ++s.frames;
        if (good) {
            if (!s.good_frames) s.first_good_timestamp = timestamp;
            s.last_good_timestamp = timestamp;
            last_good_frame = now;
            ++s.good_frames;
        }
        double snapshot_started = monotonic_seconds();
        if (good && snapshot_request_ready(&snapshot, snapshot_started)) {
            double wait = snapshot_started - snapshot.accepted_at;
            if (wait > s.snapshot_wait_max_seconds) s.snapshot_wait_max_seconds = wait;
            if (send_snapshot(snapshot.fd, maps[buffer.index].data, &buffer)) ++s.snapshot_failures;
            else ++s.snapshots;
            next_snapshot_allowed = next_snapshot_deadline(snapshot_started, c.snapshot_fps);
            close_snapshot_request(&snapshot);
        }
        if (xioctl(camera, VIDIOC_QBUF, &buffer) < 0) { perror("QBUF"); reason = "requeue_failed"; goto cleanup; }
        bool was_degraded = s.rate.degraded;
        enum rate_check_result rate_result = check_rate_health(&s.rate, now, s.good_frames, c.strict_rate_check);
        if (rate_result == RATE_DEGRADED || rate_result == RATE_STRICT_FAILED) {
            fprintf(stderr, "capture rate degraded: good_fps=%.4f window_seconds=%.3f slow_windows=%u strict=%s\n",
                    s.rate.window_good_fps, s.rate.window_seconds, s.rate.slow_windows,
                    c.strict_rate_check ? "true" : "false");
        } else if (rate_result == RATE_HEALTHY && was_degraded) {
            fprintf(stderr, "capture rate recovered: good_fps=%.4f window_seconds=%.3f\n",
                    s.rate.window_good_fps, s.rate.window_seconds);
        }
        if (rate_result == RATE_STRICT_FAILED) {
            reason = "sustained_low_fps"; goto cleanup;
        }
        if (now - s.last_report_time >= c.interval && write_status(&c, &s, "running", "capturing")) { reason = "status_write_failed"; goto cleanup; }
    }
    if (stopping) { reason = "signal_stop"; result = 0; }
cleanup:
    if (streaming && xioctl(camera, VIDIOC_STREAMOFF, &type) < 0) perror("STREAMOFF");
    close_snapshot_request(&snapshot);
    if (listener >= 0) close(listener);
    for (unsigned i = 0; i < mapped; ++i) munmap(maps[i].data, maps[i].length);
    if (camera >= 0) close(camera);
    if (lock_owned) {
        if (listener >= 0) unlink(socket_path);
        (void)write_status(&c, &s, result == 0 ? "stopped" : "failed", reason);
    }
    if (lock >= 0) close(lock);
    return result;
}
