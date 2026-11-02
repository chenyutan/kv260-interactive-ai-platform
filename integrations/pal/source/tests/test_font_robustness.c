/* Synthetic fixtures only; no game data. Compile from the source root:
 * cc -std=gnu99 -g -fsanitize=address,undefined -Iinclude $(sdl-config --cflags)
 *    tests/test_font_robustness.c -o BUILD/test_font_robustness
 * Run from a writable scratch directory (all fixtures are owned and removed).
 */
#include <unistd.h>
#include "../src/text/font.c"

TEXTLIB g_TextLib;
FILE *UTIL_OpenFile(LPCSTR name) { return fopen(name, "rb"); }
CODEPAGE PAL_DetectCodePageForString(const char *text, int len, CODEPAGE cp, int *p)
{ (void)text; (void)len; *p = 100; return cp; }
INT PAL_MultiByteToWideCharCP(CODEPAGE cp, LPCSTR text, int len, LPWSTR out, int cap)
{
   int i; (void)cp;
   if (out == NULL) return len;
   if (cap < len) return 0;
   for (i = 0; i < len; i++)
      out[i] = (unsigned char)text[i] == 255 ? 0xffff :
               (unsigned char)text[i] == 254 ? 0xfffd : (unsigned char)text[i];
   return len;
}
static void fixture(unsigned char code, int bytes)
{
   FILE *f = fopen("wor16.asc", "wb"); assert(f); fputc(code, f); fclose(f);
   remove("wor16.fon");
   if (bytes >= 0)
   {
      int i; f = fopen("wor16.fon", "wb"); assert(f);
      for (i = 0; i < bytes; i++) fputc(i < 0x682 ? 0 : 255, f);
      fclose(f);
   }
}
static PAL_POS pos(int x, int y)
{ return (uint32_t)(uint16_t)x | ((uint32_t)(uint16_t)y << 16); }
int main(void)
{
   CONFIGURATION cfg = {0};
   unsigned char pixels[32 + 24 * 18 + 32], before[sizeof(pixels)];
   SDL_PixelFormat format = {0}; SDL_Surface surface = {0};
   char tmp[] = "./font-fixture-XXXXXX"; char *dir = mkdtemp(tmp); assert(dir);
   assert(chdir(dir) == 0);
   assert(PAL_InitFont(NULL) == -1);
   assert(PAL_InitFont(&cfg) == -1); /* Both files missing. */
   fixture('A', -1); assert(PAL_InitFont(&cfg) == -1); /* Missing pair. */
   fixture('A', 0x682 + 29); assert(PAL_InitFont(&cfg) == -1); /* Short glyph. */
   fixture('A', 0x682 + 30); assert(PAL_InitFont(&cfg) == 0);
   assert(PAL_FontHeight() == 15 && PAL_CharWidth('A') == 16);
   assert(PAL_CharWidth('X') == 0 && PAL_CharWidth(0xffff) == 0);
   format.BytesPerPixel = 1; surface.format = &format;
   surface.w = 20; surface.h = 18; surface.pitch = 24; surface.pixels = pixels + 32;
   memset(pixels, 0xa5, sizeof(pixels));
   PAL_DrawCharOnSurface('A', &surface, pos(-5, -2), 7, FALSE);
   for (int i = 0; i < 32; i++) assert(pixels[i] == 0xa5);
   for (int y = 0; y < 18; y++)
      for (int x = 0; x < 24; x++)
         assert(pixels[32 + y * 24 + x] == (y < 13 && x < 11 ? 7 : 0xa5));
   for (int i = 32 + 24 * 18; i < sizeof(pixels); i++) assert(pixels[i] == 0xa5);
   memcpy(before, pixels, sizeof(pixels));
   PAL_DrawCharOnSurface(0x4e00, &surface, pos(0, 0), 3, TRUE); /* 8x8 table bound. */
   PAL_DrawCharOnSurface('A', &surface, pos(30, 30), 3, FALSE);
   assert(memcmp(before, pixels, sizeof(pixels)) == 0);
   fixture(255, 0x682 + 30); assert(PAL_InitFont(&cfg) == -1); /* Invalid scalar. */
   fixture(254, 0x682 + 30); assert(PAL_InitFont(&cfg) == 0); /* Last valid glyph. */
   assert(PAL_CharWidth(0xfffd) == 16);
   PAL_FreeFont(); assert(PAL_CharWidth('A') == 0);
   PAL_DrawCharOnSurface('A', &surface, pos(0, 0), 3, FALSE);
   assert(remove("wor16.asc") == 0 && remove("wor16.fon") == 0);
   assert(chdir("..") == 0 && rmdir(dir) == 0);
   puts("PASS font: missing/truncated pair, zero initialization, scalar/8x8 bounds, clipped pixels, cleanup");
   return 0;
}
