/* SPDX-License-Identifier: GPL-3.0-or-later
 * Independent Linux/KV260 preparation. No game assets are embedded here. */
#include "main.h"
#include "pal_port.h"
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *data_dir, *save_dir, *test_bmp;
static int check_only, self_test, no_audio, fullscreen = -1;
static unsigned width, height;
static unsigned audio_callbacks;

typedef char port_word_must_be_16_bit[sizeof(WORD) == 2 ? 1 : -1];
typedef char port_dword_must_be_32_bit[sizeof(DWORD) == 4 ? 1 : -1];
typedef char port_position_must_be_32_bit[sizeof(PAL_POS) == 4 ? 1 : -1];

static const char *required[] = {
   "abc.mkf", "ball.mkf", "data.mkf", "f.mkf", "fbp.mkf",
   "fire.mkf", "gop.mkf", "map.mkf", "mgo.mkf", "pat.mkf",
   "rgm.mkf", "rng.mkf", "sss.mkf", "m.msg", "word.dat",
   "wor16.asc", "wor16.fon", NULL
};

static void usage(void)
{
   puts("PAL KV260 preparation 0.1 (pal-navy 32e0a2f)\n"
        "Usage: sdlpal [options]\n"
        "  --data-dir DIR    DOS resource directory (lowercase names)\n"
        "  --save-dir DIR    Existing writable save directory\n"
        "  --check-data      Check inventory/MKF structure without SDL\n"
        "  --self-test       Generated video/event/audio test; no game data\n"
        "  --self-test-bmp P Save generated test card to BMP\n"
        "  --no-audio        Explicit silent mode (also permits no music/SFX data)\n"
        "  --size WxH        Window size, default 640x400\n"
        "  --windowed        Window mode (recommended for first board run)\n"
        "  --fullscreen      Fullscreen (mode support needs board validation)\n"
        "  --help, --version\n"
        "Original game data is separate. See docs/resources.md.\n"
        "Self-test is not gameplay, physical-device, or frame-rate acceptance.");
}

int PAL_PortParse(int argc, char **argv)
{
   int i;
   for (i = 1; i < argc; ++i)
   {
      const char *arg = argv[i];
      if (!strcmp(arg, "--help") || !strcmp(arg, "-h")) { usage(); return 1; }
      if (!strcmp(arg, "--version")) { puts("pal-kv260 0.1 / pal-navy 32e0a2f / SDL 1.2"); return 1; }
      if (!strcmp(arg, "--check-data")) check_only = 1;
      else if (!strcmp(arg, "--self-test")) self_test = 1;
      else if (!strcmp(arg, "--no-audio")) no_audio = 1;
      else if (!strcmp(arg, "--fullscreen")) fullscreen = 1;
      else if (!strcmp(arg, "--windowed")) fullscreen = 0;
      else if (!strcmp(arg, "--data-dir") || !strcmp(arg, "--save-dir") ||
               !strcmp(arg, "--size") || !strcmp(arg, "--self-test-bmp"))
      {
         if (++i >= argc || !argv[i][0]) { fprintf(stderr, "%s requires a value\n", arg); return -1; }
         if (!strcmp(arg, "--data-dir")) data_dir = argv[i];
         else if (!strcmp(arg, "--save-dir")) save_dir = argv[i];
         else if (!strcmp(arg, "--self-test-bmp")) test_bmp = argv[i];
         else
         {
            char tail;
            if (sscanf(argv[i], "%ux%u%c", &width, &height, &tail) != 2 ||
                width < 320 || height < 200 || width > 3840 || height > 2160)
            { fputs("Invalid --size; allowed 320x200 through 3840x2160\n", stderr); return -1; }
         }
      }
      else { fprintf(stderr, "Unknown option: %s\n", arg); return -1; }
   }
   if ((self_test && check_only) || (test_bmp && !self_test))
   { fputs("Conflicting test options\n", stderr); return -1; }
   return 0;
}

const char *PAL_PortConfigDir(void) { return data_dir ? data_dir : PAL_CONFIG_PREFIX; }
int PAL_PortAudioEnabled(void) { return !no_audio; }
int PAL_PortSelfTestRequested(void) { return self_test; }

static int replace_path(char **destination, const char *value)
{
   char *copy = strdup(value);
   if (!copy) return -1;
   free(*destination);
   *destination = copy;
   return 0;
}

static int join_path(char *out, size_t size, const char *dir, const char *name)
{
   int n = snprintf(out, size, "%s/%s", dir, name);
   return n < 0 || (size_t)n >= size ? -1 : 0;
}

static int read_le32(FILE *file, unsigned *value)
{
   unsigned char b[4];
   if (fread(b, 1, 4, file) != 4) return -1;
   *value = b[0] | ((unsigned)b[1] << 8) | ((unsigned)b[2] << 16) | ((unsigned)b[3] << 24);
   return 0;
}

static int check_file(const char *name, int report)
{
   char path[4096];
   struct stat st;
   FILE *file;
   int valid = 1;
   if (join_path(path, sizeof(path), gConfig.pszGamePath, name) < 0) return -1;
   if (stat(path, &st) || !S_ISREG(st.st_mode) || st.st_size <= 0 || !(file = fopen(path, "rb")))
   {
      if (report) fprintf(stderr, "Missing/empty/unreadable resource: %s\n", path);
      return -1;
   }
   if (strstr(name, ".mkf"))
   {
      unsigned first, prev, next, i;
      int nonempty = 0;
      if (read_le32(file, &first) || first < 8 || first % 4 ||
          first > 4 * 1024 * 1024 || first >= (unsigned long long)st.st_size) valid = 0;
      if (valid)
      {
         prev = first;
         for (i = 1; i < first / 4; ++i)
         {
            if (read_le32(file, &next) || next < prev || next > (unsigned long long)st.st_size)
            { valid = 0; break; }
            if (next > prev) nonempty = 1;
            prev = next;
         }
         if (!nonempty) valid = 0;
      }
   }
   fclose(file);
   if (!valid && report) fprintf(stderr, "Invalid MKF offset table: %s\n", path);
   return valid ? 0 : -1;
}

int PAL_PortPrepare(void)
{
   int i, missing = 0;
   if ((data_dir && replace_path(&gConfig.pszGamePath, data_dir)) ||
       (save_dir && replace_path(&gConfig.pszSavePath, save_dir))) return -1;
   if (fullscreen >= 0) gConfig.fFullScreen = fullscreen;
   if (width) { gConfig.dwScreenWidth = width; gConfig.dwScreenHeight = height; }
   if (self_test) return 0;
   for (i = 0; required[i]; ++i) if (check_file(required[i], 1)) ++missing;
   if (!no_audio)
   {
      if (check_file("mus.mkf", 1)) ++missing;
      if (check_file("voc.mkf", 0) && check_file("sounds.mkf", 0))
      { fputs("Missing or invalid SFX: voc.mkf or sounds.mkf required\n", stderr); ++missing; }
   }
   if (missing)
   {
      fprintf(stderr, "Resource preflight failed (%d). Use tools/pal_resources.py check/import.\n", missing);
      fputs("No original assets are bundled. Official source: https://store.steampowered.com/app/1546570/\n", stderr);
      return -1;
   }
   puts("RESOURCE_STRUCTURE_OK: inventory/MKF only; content/version/font compatibility still needs validation.");
   if (check_only) return 1;
   if (data_dir && !save_dir && !strcmp(gConfig.pszSavePath, PAL_SAVE_PREFIX))
   {
      fputs("Specify --save-dir with an existing writable directory, or use run-pal.sh.\n", stderr);
      return -1;
   }
   {
      char probe[4096];
      int fd;
      if (join_path(probe, sizeof(probe), gConfig.pszSavePath, ".pal-write-XXXXXX")) return -1;
      fd = mkstemp(probe);
      if (fd < 0)
      { fprintf(stderr, "Save directory must exist and be writable: %s (%s)\n", gConfig.pszSavePath, strerror(errno)); return -1; }
      close(fd);
      unlink(probe);
   }
   return 0;
}

static void test_audio(void *unused, Uint8 *stream, int len)
{
   /* A quiet generated tone; no original music is included. */
   static unsigned phase;
   Sint16 *samples = (Sint16 *)stream;
   int i;
   (void)unused;
   memset(stream, 0, (size_t)len);
   for (i = 0; i < len / (int)sizeof(Sint16); ++i)
      samples[i] = ((phase++ / 100) % 2) ? 256 : -256;
   ++audio_callbacks;
}

int PAL_PortSelfTest(void)
{
   SDL_Color palette[256];
   SDL_AudioSpec spec;
   SDL_Event sent, got;
   Uint32 start;
   int i, frame, received = 0, audio_open = 0, result = 0;
   char driver[64] = {0};
   if (VIDEO_Startup() != 0) { fprintf(stderr, "SELFTEST video failed: %s\n", SDL_GetError()); return 3; }
   VIDEO_SetWindowTitle("PAL port self-test - generated test card, not original game");
   for (i = 0; i < 256; ++i)
   { palette[i].r = (Uint8)i; palette[i].g = (Uint8)(255-i); palette[i].b = (Uint8)((i*3)%256); palette[i].unused = 0; }
   VIDEO_SetPalette(palette);
   memset(&sent, 0, sizeof(sent));
   sent.type = SDL_USEREVENT;
   sent.user.code = 260;
   if (SDL_PushEvent(&sent) < 0) result = 4;
   if (!no_audio)
   {
      memset(&spec, 0, sizeof(spec));
      spec.freq = 44100; spec.format = AUDIO_S16SYS; spec.channels = 1;
      spec.samples = 1024; spec.callback = test_audio;
      if (SDL_OpenAudio(&spec, NULL) != 0)
      { fprintf(stderr, "SELFTEST audio failed: %s\n", SDL_GetError()); result = 5; }
      else { audio_open = 1; SDL_PauseAudio(0); }
   }
   start = SDL_GetTicks();
   for (frame = 0; frame < 45 && !result; ++frame)
   {
      SDL_Rect block = {(Sint16)(frame*5), 70, 32, 32};
      SDL_FillRect(gpScreen, NULL, 16);
      for (i = 0; i < 8; ++i)
      {
         SDL_Rect strip = {(Sint16)(i*40), 0, 40, 50};
         SDL_FillRect(gpScreen, &strip, (Uint32)(32+i*28));
      }
      SDL_FillRect(gpScreen, &block, 250);
      VIDEO_UpdateScreen(NULL);
      while (SDL_PollEvent(&got))
      {
         if (got.type == SDL_USEREVENT && got.user.code == 260) received = 1;
         if (got.type == SDL_QUIT) result = 6;
      }
      SDL_Delay(16);
   }
   if (audio_open) SDL_CloseAudio(); /* Joins callback thread before reading counter. */
   if (!received || (audio_open && !audio_callbacks)) result = 7;
   if (test_bmp && SDL_SaveBMP(gpScreen, test_bmp) < 0) result = 8;
   SDL_VideoDriverName(driver, sizeof(driver));
   printf("PAL_SELFTEST_%s video=%s frames=%d event_queue=%d audio_callbacks=%u elapsed_ms=%u original_assets=0\n",
          result ? "FAIL" : "PASS", driver, frame, received, audio_callbacks, SDL_GetTicks()-start);
   VIDEO_Shutdown();
   return result;
}
