/* -*- mode: c; tab-width: 4; c-basic-offset: 4; c-file-style: "linux" -*- */
//
// Copyright (c) 2009-2011, Wei Mingzhi <whistler_wmz@users.sf.net>.
// Copyright (c) 2011-2020, SDLPAL development team.
// All rights reserved.
//
// This file is part of SDLPAL.
//
// SDLPAL is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.
//

#include "font.h"
#include "util.h"
#include "text.h"

#define _FONT_C

#include "ascii.h"

#define NR_UNICODE_FONT 57086
static unsigned char (*unicode_font)[32] = NULL;
static unsigned char *font_width = NULL;

static const int unicode_lower_top  = 0xd800;
static const int unicode_upper_base = 0xf900;
static const int unicode_upper_top  = 65534;

static int _font_height = 16;

static uint8_t reverseBits(uint8_t x) {
    uint8_t y = 0;
    for (int i = 0 ; i < 8; i++){
        y <<= 1;
        y |= (x & 1);
        x >>= 1;
    }
    return y;
}

#ifdef DEBUG
void dump_font(BYTE *buf,int rows, int cols)
{
   for(int row=0;row<rows;row++)
   {
      for( int bit=0;bit<cols;bit++)
         if( buf[row] & (uint8_t)pow(2,bit) )
            printf("*");
         else
            printf(" ");
      printf("\n");
   }
}

static uint16_t reverseBits16(uint16_t x)
{
   //specified to unifont structure; not common means
   uint8_t l = reverseBits(x);
   uint8_t h = reverseBits(x >> 8);
   return h << 8 | l;
}

void dump_font16(WORD *buf,int rows, int cols)
{
   for(int row=0;row<rows;row++)
   {
      for( int bit=0;bit<cols;bit++)
         if( reverseBits16(buf[row]) & (uint16_t)pow(2,bit) )
            printf("*");
         else
            printf(" ");
      printf("\n");
   }
}
#endif

static void PAL_LoadISOFont(void)
{
    int         i, j;

    for (i = 0; i < sizeof(iso_font) / 15; i++)
    {
        for (j = 0; j < 15; j++)
        {
            unicode_font[i][j] = reverseBits(iso_font[i * 15 + j]);
        }

        unicode_font[i][15] = 0;
        font_width[i] = 16;
    }
}

/* Invalid Unicode values must never become indices in the compact font table. */
static int PAL_FontIndex(wchar_t character)
{
   if (character < 0 || character >= unicode_upper_top ||
       (character >= unicode_lower_top && character < unicode_upper_base))
      return -1;
   if (character >= unicode_upper_base)
      character -= unicode_upper_base - unicode_lower_top;
   return character < NR_UNICODE_FONT ? (int)character : -1;
}

static BOOL PAL_LoadEmbeddedFont(void)
{
   FILE *fp = NULL;
   char *char_buf = NULL;
   wchar_t *wchar_buf = NULL;
   long length;
   int nChars, i, probability;
   BOOL loaded = FALSE;

   fp = UTIL_OpenFile("wor16.asc");
   if (fp == NULL || fseek(fp, 0, SEEK_END) != 0) goto cleanup;
   length = ftell(fp);
   if (length <= 0 || length > INT_MAX || fseek(fp, 0, SEEK_SET) != 0)
      goto cleanup;
   char_buf = (char *)malloc((size_t)length);
   if (char_buf == NULL || fread(char_buf, 1, (size_t)length, fp) != (size_t)length)
      goto cleanup;
   fclose(fp);
   fp = NULL;

   if (PAL_DetectCodePageForString(char_buf, (int)length, CP_BIG5, &probability) != CP_BIG5 ||
       probability < 99) goto cleanup;
   nChars = PAL_MultiByteToWideCharCP(CP_BIG5, char_buf, (int)length, NULL, 0);
   if (nChars <= 0 || nChars > NR_UNICODE_FONT) goto cleanup;
   wchar_buf = (wchar_t *)malloc((size_t)nChars * sizeof(wchar_t));
   if (wchar_buf == NULL ||
       PAL_MultiByteToWideCharCP(CP_BIG5, char_buf, (int)length, wchar_buf, nChars) != nChars)
      goto cleanup;

   fp = UTIL_OpenFile("wor16.fon");
   if (fp == NULL || fseek(fp, 0, SEEK_END) != 0) goto cleanup;
   length = ftell(fp);
   /* Each character consumes one 30-byte glyph after the original header. */
   if (length < 0x682L + (long)nChars * 30 || fseek(fp, 0x682, SEEK_SET) != 0)
      goto cleanup;
   for (i = 0; i < nChars; i++)
   {
      int index = PAL_FontIndex(wchar_buf[i]);
      if (index < 0 || fread(unicode_font[index], 30, 1, fp) != 1)
         goto cleanup;
      font_width[index] = 32;
   }
   _font_height = 15;
   loaded = TRUE;

cleanup:
   if (fp != NULL) fclose(fp);
   free(char_buf);
   free(wchar_buf);
   return loaded;
}

int
PAL_InitFont(
   const CONFIGURATION* cfg
)
{
   PAL_FreeFont();
   _font_height = 16;
   if (cfg == NULL) return -1;
   unicode_font = calloc(NR_UNICODE_FONT, sizeof(*unicode_font));
   font_width = calloc(NR_UNICODE_FONT, sizeof(*font_width));
   if (unicode_font == NULL || font_width == NULL ||
       (!cfg->pszMsgFile && !PAL_LoadEmbeddedFont()))
   {
      PAL_FreeFont();
      return -1;
   }
   if (g_TextLib.fUseISOFont) PAL_LoadISOFont();
   return 0;
}

void
PAL_FreeFont(
	void
)
{
   if (unicode_font) {
     free(unicode_font);
     unicode_font = NULL;
   }
   if (font_width) {
     free(font_width);
     font_width = NULL;
   }
}

void
PAL_DrawCharOnSurface(
	uint16_t                 wChar,
	SDL_Surface             *lpSurface,
	PAL_POS                  pos,
	uint8_t                  bColor,
	BOOL                     fUse8x8Font
)
{
   int row, column, index, width, height;
   int x = PAL_X(pos), y = PAL_Y(pos);
   if (lpSurface == NULL || lpSurface->pixels == NULL || lpSurface->format == NULL ||
       lpSurface->format->BytesPerPixel != 1 || unicode_font == NULL || font_width == NULL)
      return;
   index = PAL_FontIndex((wchar_t)wChar);
   if (index < 0) return;
   if (fUse8x8Font)
   {
      if (wChar >= sizeof(iso_font_8x8) / sizeof(iso_font_8x8[0])) return;
      width = height = 8;
   }
   else
   {
      width = font_width[index] == 32 ? 16 : 8;
      height = _font_height;
   }
   /* Clip before computing pixel addresses, including negative x/y positions. */
   for (row = 0; row < height; row++)
   {
      int sy = y + row;
      if (sy < 0 || sy >= lpSurface->h) continue;
      LPBYTE dest = (LPBYTE)lpSurface->pixels + sy * lpSurface->pitch;
      for (column = 0; column < width; column++)
      {
         int sx = x + column;
         unsigned char bits;
         if (sx < 0 || sx >= lpSurface->w) continue;
         if (fUse8x8Font)
            bits = iso_font_8x8[wChar][row] & (1u << column);
         else
            bits = unicode_font[index][row * (width / 8) + column / 8] &
                   (1u << (7 - column % 8));
         if (bits) dest[sx] = bColor;
      }
   }
}

int
PAL_CharWidth(
   uint16_t                 wChar
)
{
   int index = PAL_FontIndex((wchar_t)wChar);
   return font_width != NULL && index >= 0 ? font_width[index] >> 1 : 0;
}

int
PAL_FontHeight(
	void
)
{
	return _font_height;
}
