/* Host-only transport/metrics fixture. It does not simulate a camera or claim
 * hardware validation. This compiles the actual implementation being shipped. */
#define main bodyink_capture_main
#include "bodyink-camera.c"
#undef main

int main(int argc, char **argv)
{
    if (argc != 2 && argc != 3) return 64;
    if (argc == 3) {
        if (!strcmp(argv[2], "720")) { capture_width=1280; capture_height=720; }
        else if (!strcmp(argv[2], "540")) { capture_width=960; capture_height=540; }
        else return 64;
    }
    char path[512];
    if (path_join(path, sizeof(path), argv[1], "snapshot.sock")) return 64;
    int listener = create_listener(path);
    if (listener < 0) { perror("fixture listener"); return 1; }
    struct pollfd pending = {.fd = listener, .events = POLLIN};
    if (poll(&pending, 1, 5000) <= 0) return 1;
    int client = accept4(listener, NULL, NULL, SOCK_CLOEXEC);
    if (client < 0) return 1;
    unsigned char *pixels = malloc(FRAME_BYTES);
    if (!pixels) return 1;
    for (size_t i = 0; i < FRAME_BYTES; ++i) pixels[i] = (unsigned char)(i % 251);
    struct v4l2_buffer buffer = {.bytesused = FRAME_BYTES, .sequence = 42, .flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC};
    buffer.timestamp.tv_sec = 123; buffer.timestamp.tv_usec = 456789;
    int rc = send_snapshot(client, pixels, &buffer);
    memset(pixels, 0, FRAME_BYTES); /* Received sealed frame must remain unchanged. */
    free(pixels); close(client); close(listener); unlink(path);
    struct config config = {.directory = argv[1], .exposure = 100};
    struct stats stats = {.started = monotonic_seconds() - 12, .frames = 601, .good_frames = 599,
        .first_timestamp = 100, .last_timestamp = 110, .first_arrival = 200, .last_arrival = 210,
        .sequence_gaps = 3, .error_frames = 1, .short_frames = 1, .last_sequence = 600};
    if (write_status(&config, &stats, "running", "selftest")) rc = -1;
    return rc ? 1 : 0;
}
