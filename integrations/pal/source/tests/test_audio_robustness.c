/* Test the real game audio source against controlled SDL/device/player stubs.
 * Compile -std=gnu99 -Iinclude $(sdl-config --cflags). No device or game data. */
#define SDL_OpenAudio test_OpenAudio
#define SDL_CloseAudio test_CloseAudio
#define SDL_PauseAudio test_PauseAudio
#define SDL_LockAudio test_LockAudio
#define SDL_UnlockAudio test_UnlockAudio
#define SDL_GetError test_GetError
#include "../src/sound/audio.c"
CONFIGURATION gConfig;
static int open_mode, opens, closes, resumes, blocks, played_bytes;
static void sound_fill(void *self, LPBYTE dst, int len)
{
   (void)self;
   assert(len <= 8192 && len % 4 == 0);
   for (int i = 0; i < len / 2; i++) ((short *)dst)[i] = 1000;
   blocks++; played_bytes += len;
}
static void sound_shutdown(void *self) { (void)self; }
static AUDIOPLAYER sound_player = { .FillBuffer = sound_fill, .Shutdown = sound_shutdown };
int test_OpenAudio(SDL_AudioSpec *desired, SDL_AudioSpec *obtained)
{
   assert(obtained == NULL); /* SDL must convert hardware format internally. */
   opens++;
   if (open_mode == 1) return -1;
   desired->samples = 2048; desired->size = 8192;
   if (open_mode == 2) desired->size = 8193; /* Invalid negotiated buffer. */
   if (open_mode == 3) desired->format = AUDIO_U8;
   return 0;
}
void test_CloseAudio(void) { closes++; }
void test_PauseAudio(int paused) { if (!paused) resumes++; }
void test_LockAudio(void) {}
void test_UnlockAudio(void) {}
char *test_GetError(void) { return "synthetic device failure"; }
void resampler_init(void) {}
void MIDI_SetVolume(int n) { (void)n; }
void MIDI_Play(int n, BOOL loop) { (void)n; (void)loop; }
void UTIL_LogOutput(LOGLEVEL l, const char *s, ...) { (void)l; (void)s; }
char *UTIL_GlobalBuffer(int n) { static char b[1024]; (void)n; return b; }
const char *UTIL_GetFullPathName(char *b, size_t n, const char *p, const char *s)
{ (void)b; (void)n; (void)p; (void)s; return NULL; }
LPAUDIOPLAYER SOUND_Init(void) { return &sound_player; }
LPAUDIOPLAYER RIX_Init(LPCSTR file) { (void)file; return NULL; }
LPAUDIOPLAYER MP3_Init(void) { return NULL; }
LPAUDIOPLAYER OGG_Init(void) { return NULL; }
LPAUDIOPLAYER OPUS_Init(void) { return NULL; }
void *AVI_GetPlayState(void) { return NULL; }
void AVI_FillAudioBuffer(void *s, uint8_t *p, int n) { (void)s; (void)p; (void)n; }
int main(void)
{
   unsigned char *stream = malloc(20000); assert(stream);
   gConfig.iSampleRate = 44100; gConfig.iAudioChannels = 2;
   gConfig.wAudioBufferSize = 1024; gConfig.iSoundVolume = PAL_MAX_VOLUME;
   gConfig.iMusicVolume = PAL_MAX_VOLUME; gConfig.eMusicType = MUSIC_RIX;
   gConfig.eCDType = CD_NONE;
   assert(AUDIO_OpenDevice() == 0 && opens == 1 && resumes == 1);
   assert(gAudioDevice.soundBufferSize == 8192 && gAudioDevice.spec.samples == 2048);
   assert(gAudioDevice.spec.freq == 44100 && gConfig.iSampleRate == 44100);
   assert(AUDIO_OpenDevice() == -1 && opens == 1);
   AUDIO_FillBuffer(NULL, stream, 20000);
   assert(blocks == 3 && played_bytes == 20000);
   for (int i = 0; i < 10000; i++) assert(((short *)stream)[i] == 1000);
   memset(stream, 255, 5); AUDIO_FillBuffer(NULL, stream, 5);
   for (int i = 0; i < 5; i++) assert(stream[i] == 0);
   assert(blocks == 3);
   assert(AUDIO_PlayCDTrack(-2) == FALSE); /* Optional CD player absent. */
   AUDIO_CloseDevice(); assert(closes == 1 && !gAudioDevice.fOpened);
   assert(gAudioDevice.pSoundBuffer == NULL && gAudioDevice.soundBufferSize == 0);
   open_mode = 1; assert(AUDIO_OpenDevice() == -3 && !gAudioDevice.fOpened);
   open_mode = 2; assert(AUDIO_OpenDevice() == -4 && !gAudioDevice.fOpened);
   open_mode = 3; assert(AUDIO_OpenDevice() == -4 && !gAudioDevice.fOpened);
   assert(resumes == 1 && closes == 3);
   gConfig.iSampleRate = 0; assert(AUDIO_OpenDevice() == -2);
   free(stream);
   puts("PASS audio: fixed callback format, negotiated size, bounded mixing, failure/close lifecycle");
   return 0;
}
