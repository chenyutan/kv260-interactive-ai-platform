/* Host-only rate-policy and status fixture. Uses the shipped C implementation;
 * no camera, capture ioctl, live frame or hardware validation is involved. */
#define main bodyink_capture_main
#include "bodyink-camera.c"
#undef main
#include <assert.h>
#include <math.h>

static void near(double actual, double expected)
{
    assert(fabs(actual - expected) < 1e-9);
}

static void test_live_degraded(void)
{
    struct rate_health rate;
    start_rate_health(&rate, 100.0, 37);
    assert(check_rate_health(&rate, 109.999, 536, false) == RATE_NOT_CHECKED);
    assert(rate.checked_windows == 0 && rate.slow_windows == 0);
    /* Ten minutes of complete but slow frames must keep default live service
     * running, with the measured rate and consecutive windows still visible. */
    for (unsigned window = 1; window <= 60; ++window) {
        assert(check_rate_health(&rate, 100.0 + 10.0 * window,
                                 37 + UINT64_C(500) * window, false) == RATE_DEGRADED);
        assert(rate.degraded && rate.slow_windows == window);
        assert(rate.checked_windows == window);
        near(rate.window_good_fps, 50.0);
        near(rate.window_seconds, 10.0);
    }
    assert(check_rate_health(&rate, 710.0, 37 + 30000 + 550, false) == RATE_HEALTHY);
    assert(!rate.degraded && rate.slow_windows == 0);
    near(rate.window_good_fps, 55.0); /* The original threshold is exclusive. */
    assert(check_rate_health(&rate, 720.0, 37 + 30000 + 550 + 549, false) == RATE_DEGRADED);
    assert(rate.slow_windows == 1);
    near(rate.window_good_fps, 54.9);
    rate.slow_windows = UINT_MAX;
    assert(check_rate_health(&rate, 730.0, 37 + 30000 + 550 + 549 + 500, false) == RATE_DEGRADED);
    assert(rate.slow_windows == UINT_MAX); /* Never wrap back to healthy. */
}

static void test_strict_consecutive_windows(void)
{
    struct rate_health rate;
    start_rate_health(&rate, 0.0, 0);
    assert(check_rate_health(&rate, 10.0, 500, true) == RATE_DEGRADED);
    assert(check_rate_health(&rate, 20.0, 1000, true) == RATE_DEGRADED);
    assert(check_rate_health(&rate, 30.0, 1600, true) == RATE_HEALTHY);
    assert(rate.slow_windows == 0 && !rate.degraded);
    assert(check_rate_health(&rate, 40.0, 2100, true) == RATE_DEGRADED);
    assert(rate.slow_windows == 1);
    assert(check_rate_health(&rate, 50.0, 2600, true) == RATE_DEGRADED);
    assert(check_rate_health(&rate, 60.0, 3100, true) == RATE_STRICT_FAILED);
    assert(rate.degraded && rate.slow_windows == 3 && rate.checked_windows == 6);
    near(rate.window_good_fps, 50.0);
}

static void test_elapsed_window(void)
{
    struct rate_health rate;
    start_rate_health(&rate, 5.0, 100);
    assert(check_rate_health(&rate, 17.0, 760, true) == RATE_HEALTHY);
    near(rate.window_seconds, 12.0);
    near(rate.window_good_fps, 55.0);
    assert(check_rate_health(&rate, 26.999, 1260, true) == RATE_NOT_CHECKED);
    assert(rate.checked_windows == 1);
    assert(check_rate_health(&rate, 27.5, 1260, true) == RATE_DEGRADED);
    near(rate.window_seconds, 10.5);
    near(rate.window_good_fps, 500.0 / 10.5);
    assert(rate.slow_windows == 1 && rate.checked_windows == 2);
}

static void test_status_metadata(void)
{
    char directory[] = "/tmp/bodyink-rate-health-XXXXXX";
    assert(mkdtemp(directory));
    struct config config = {.directory = directory, .exposure = 160};
    assert(!config.strict_rate_check);
    struct stats stats = {.started = monotonic_seconds() - 30.0,
        .frames = 601, .good_frames = 501, .first_timestamp = 100.0,
        .last_timestamp = 110.0, .first_arrival = 200.0, .last_arrival = 210.0,
        .first_good_timestamp = 100.0, .last_good_timestamp = 110.0,
        .rate = {.window_seconds = 10.0, .window_good_fps = 50.0,
            .checked_windows = 3, .slow_windows = 3, .degraded = true}};
    assert(write_status(&config, &stats, "running", "capturing") == 0);
    char path[512], json[4096];
    assert(path_join(path, sizeof(path), directory, "status.json") == 0);
    FILE *file = fopen(path, "r");
    assert(file);
    size_t length = fread(json, 1, sizeof(json) - 1, file);
    assert(!ferror(file) && feof(file));
    json[length] = '\0';
    assert(fclose(file) == 0);
    assert(strstr(json, "\"state\": \"running\", \"reason\": \"capturing\""));
    assert(strstr(json, "\"fps_timestamp\": 60.000000, \"fps_monotonic\": 60.000000"));
    assert(strstr(json, "\"good_fps_timestamp\": 50.000000"));
    assert(strstr(json, "\"rate_health\": \"degraded\", \"rate_degraded\": true, \"strict_rate_check\": false"));
    assert(strstr(json, "\"rate_window_good_fps_monotonic\": 50.000000"));
    assert(strstr(json, "\"rate_window_seconds\": 10.000000, \"rate_checked_windows\": 3, \"rate_slow_windows\": 3"));
    config.strict_rate_check = true;
    assert(write_status(&config, &stats, "failed", "sustained_low_fps") == 0);
    file = fopen(path, "r");
    assert(file);
    length = fread(json, 1, sizeof(json) - 1, file);
    assert(!ferror(file) && feof(file));
    json[length] = '\0';
    assert(fclose(file) == 0);
    assert(strstr(json, "\"state\": \"failed\", \"reason\": \"sustained_low_fps\""));
    assert(strstr(json, "\"strict_rate_check\": true"));
    assert(unlink(path) == 0 && rmdir(directory) == 0);
}

int main(void)
{
    test_live_degraded();
    test_strict_consecutive_windows();
    test_elapsed_window();
    test_status_metadata();
    puts("PASS: live low-rate continuity, strict third-window failure, recovery/55 fps boundary, real elapsed windows, saturation and additive status metadata");
    return 0;
}
