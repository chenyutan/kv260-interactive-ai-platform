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

#include "palcommon.h"
#include "global.h"
#include "palcfg.h"
#include "audio.h"
#include "players.h"
#include "util.h"
#include "resampler.h"
#include "midi.h"
#include "aviplay.h"
#include <math.h>

/* WASAPI need fewer samples for less gapping */
#define PAL_AUDIO_FORCE_BUFFER_SIZE_WASAPI   512

typedef void(*ResampleMixFunction)(void *, const void *, int, void *, int, int, uint8_t);

typedef struct tagAUDIODEVICE
{
   SDL_AudioSpec             spec;		/* Actual-used sound specification */
   AUDIOPLAYER              *pMusPlayer;
   AUDIOPLAYER              *pCDPlayer;
   AUDIOPLAYER              *pSoundPlayer;
   void                     *pSoundBuffer;	/* The output buffer for sound */
   size_t                   soundBufferSize;
   INT                       iMusicVolume;	/* The BGM volume ranged in [0, 128] for better performance */
   INT                       iSoundVolume;	/* The sound effect volume ranged in [0, 128] for better performance */
   BOOL                      fMusicEnabled; /* Is BGM enabled? */
   BOOL                      fSoundEnabled; /* Is sound effect enabled? */
   BOOL                      fOpened;       /* Is the audio device opened? */
} AUDIODEVICE;

static AUDIODEVICE gAudioDevice;


PAL_FORCE_INLINE
void
AUDIO_MixNative(
	short     *dst,
	short     *src,
	int        samples
)
{
	while (samples > 0)
	{
		int val = *src++ + *dst;
		if (val > SHRT_MAX)
			*dst++ = SHRT_MAX;
		else if (val < SHRT_MIN)
			*dst++ = SHRT_MIN;
		else
			*dst++ = (short)val;
		samples--;
	}
}

PAL_FORCE_INLINE
void
AUDIO_AdjustVolume(
	short     *srcdst,
	int        iVolume,
	int        samples
)
{
	if (iVolume == SDL_MIX_MAXVOLUME) return;
	if (iVolume == 0) { memset(srcdst, 0, samples << 1); return; }
	while (samples > 0)
	{
		*srcdst = *srcdst * iVolume / SDL_MIX_MAXVOLUME;
		samples--; srcdst++;
	}
}

static VOID SDLCALL
AUDIO_FillBuffer(
   LPVOID          udata,
   LPBYTE          stream,
   INT             len
)
/*++
  Purpose:

    SDL sound callback function.

  Parameters:

    [IN]  udata - pointer to user-defined parameters (Not used).

    [OUT] stream - pointer to the stream buffer.

    [IN]  len - Length of the buffer.

  Return value:

    None.

--*/
{
   if (stream == NULL || len <= 0) return;
   memset(stream, 0, (size_t)len);
   if (gAudioDevice.spec.channels == 0 ||
       len % (gAudioDevice.spec.channels * (int)sizeof(short)) != 0) return;

   //
   // Play music
   //
   if (gAudioDevice.fMusicEnabled && gAudioDevice.iMusicVolume > 0)
   {
      if (gAudioDevice.pMusPlayer)
      {
         gAudioDevice.pMusPlayer->FillBuffer(gAudioDevice.pMusPlayer, stream, len);
      }

      if (gAudioDevice.pCDPlayer)
      {
         gAudioDevice.pCDPlayer->FillBuffer(gAudioDevice.pCDPlayer, stream, len);
      }

      //
      // Adjust volume for music
      //
      AUDIO_AdjustVolume((short *)stream, gAudioDevice.iMusicVolume, len >> 1);
   }

   //
   // Play sound
   //
   if (gAudioDevice.fSoundEnabled && gAudioDevice.pSoundPlayer &&
       gAudioDevice.iSoundVolume > 0 && gAudioDevice.pSoundBuffer != NULL &&
       gAudioDevice.soundBufferSize > 0)
   {
      int offset = 0;
      /* Never allocate in the callback. A larger callback is mixed in bounded blocks. */
      while (offset < len)
      {
         int block = (size_t)(len - offset) < gAudioDevice.soundBufferSize ?
            len - offset : (int)gAudioDevice.soundBufferSize;
         memset(gAudioDevice.pSoundBuffer, 0, (size_t)block);
         gAudioDevice.pSoundPlayer->FillBuffer(gAudioDevice.pSoundPlayer,
                                               gAudioDevice.pSoundBuffer, block);
         AUDIO_AdjustVolume((short *)gAudioDevice.pSoundBuffer,
                            gAudioDevice.iSoundVolume, block >> 1);
         AUDIO_MixNative((short *)(stream + offset),
                         (short *)gAudioDevice.pSoundBuffer, block >> 1);
         offset += block;
      }
   }

#ifndef __NAVY__
   //
   // Play sound for AVI
   //
   AVI_FillAudioBuffer(AVI_GetPlayState(), (LPBYTE)stream, len);
#endif
}

BOOL
AUDIO_CD_Available(
   VOID
)
{
   return gConfig.eCDType != CD_NONE;
}

INT
AUDIO_OpenDevice(
   VOID
)
/*++
  Purpose:

    Initialize the audio subsystem.

  Parameters:

    None.

  Return value:

    0 if succeed, others if failed.

--*/
{
   if (gAudioDevice.fOpened)
   {
      //
      // Already opened
      //
      return -1;
   }

   if (gConfig.iSampleRate <= 0 ||
       (gConfig.iAudioChannels != 1 && gConfig.iAudioChannels != 2) ||
       gConfig.wAudioBufferSize == 0) return -2;

   memset(&gAudioDevice.spec, 0, sizeof(gAudioDevice.spec));
   gAudioDevice.soundBufferSize = 0;
   gAudioDevice.fOpened = FALSE;
   gAudioDevice.fMusicEnabled = TRUE;
   gAudioDevice.fSoundEnabled = TRUE;
   gAudioDevice.iMusicVolume = gConfig.iMusicVolume * SDL_MIX_MAXVOLUME / PAL_MAX_VOLUME;
   gAudioDevice.iSoundVolume = gConfig.iSoundVolume * SDL_MIX_MAXVOLUME / PAL_MAX_VOLUME;
   MIDI_SetVolume(gConfig.iMusicVolume);

   //
   // Initialize the resampler module
   //
   resampler_init();


   //
   // Open the audio device.
   //
   gAudioDevice.spec.freq = gConfig.iSampleRate;
   gAudioDevice.spec.format = AUDIO_S16SYS;
   gAudioDevice.spec.channels = gConfig.iAudioChannels;
   gAudioDevice.spec.samples = gConfig.wAudioBufferSize;
   gAudioDevice.spec.callback = AUDIO_FillBuffer;

   UTIL_LogOutput(LOGLEVEL_VERBOSE, "OpenAudio: requesting audio spec:freq %d, format %d, channels %d, samples %d\n", gAudioDevice.spec.freq, gAudioDevice.spec.format,  gAudioDevice.spec.channels, gAudioDevice.spec.samples);

   /* SDL 1.2 guarantees the requested callback format when obtained is NULL.
    * Hardware conversion stays inside SDL; game players retain their configured rate. */
   if (SDL_OpenAudio(&gAudioDevice.spec, NULL) < 0)
   {
      UTIL_LogOutput(LOGLEVEL_ERROR, "OpenAudio ERROR: %s\n", SDL_GetError());
      return -3;
   }
   if (gAudioDevice.spec.format != AUDIO_S16SYS ||
       gAudioDevice.spec.freq != gConfig.iSampleRate ||
       gAudioDevice.spec.channels != gConfig.iAudioChannels ||
       gAudioDevice.spec.size == 0 || gAudioDevice.spec.size > INT_MAX ||
       gAudioDevice.spec.size % (gAudioDevice.spec.channels * sizeof(short)) != 0)
   {
      SDL_CloseAudio();
      UTIL_LogOutput(LOGLEVEL_ERROR, "OpenAudio returned an invalid callback specification\n");
      return -4;
   }
   /* SDL may adjust the sample count; size is valid only after OpenAudio. */
   gAudioDevice.pSoundBuffer = malloc(gAudioDevice.spec.size);
   if (gAudioDevice.pSoundBuffer == NULL)
   {
      SDL_CloseAudio();
      return -4;
   }
   gAudioDevice.soundBufferSize = gAudioDevice.spec.size;
   UTIL_LogOutput(LOGLEVEL_VERBOSE,
      "OpenAudio callback: freq %d, format %d, channels %d, samples %d, bytes %u\n",
      gAudioDevice.spec.freq, gAudioDevice.spec.format, gAudioDevice.spec.channels,
      gAudioDevice.spec.samples, gAudioDevice.spec.size);

   gAudioDevice.fOpened = TRUE;

   //
   // Initialize the sound subsystem.
   //
   gAudioDevice.pSoundPlayer = SOUND_Init();

   //
   // Initialize the music subsystem.
   //
   switch (gConfig.eMusicType)
   {
   case MUSIC_RIX:
       gAudioDevice.pMusPlayer = RIX_Init(UTIL_GetFullPathName(PAL_BUFFER_SIZE_ARGS(0), gConfig.pszGamePath, "mus.mkf"));
	   break;
   case MUSIC_MP3:
	   gAudioDevice.pMusPlayer = MP3_Init();
	   break;
   case MUSIC_OGG:
	   gAudioDevice.pMusPlayer = OGG_Init();
	   break;
   case MUSIC_OPUS:
	   gAudioDevice.pMusPlayer = OPUS_Init();
	   break;
   case MUSIC_MIDI:
	   gAudioDevice.pMusPlayer = NULL;
	   break;
   default:
	   break;
   }

   //
   // Initialize the CD audio.
   //
   switch (gConfig.eCDType)
   {
   case CD_SDLCD:
   {
	   gAudioDevice.pCDPlayer = NULL;
	   break;
   }
   case CD_MP3:
	   gAudioDevice.pCDPlayer = MP3_Init();
	   break;
   case CD_OGG:
	   gAudioDevice.pCDPlayer = OGG_Init();
	   break;
   case CD_OPUS:
	   gAudioDevice.pCDPlayer = OPUS_Init();
	   break;
   default:
      gAudioDevice.pCDPlayer = NULL;
	   break;
   }

   //
   // Let the callback function run so that musics will be played.
   //
   SDL_PauseAudio(0);

   return 0;
}

VOID
AUDIO_CloseDevice(
   VOID
)
/*++
  Purpose:

    Close the audio subsystem.

  Parameters:

    None.

  Return value:

    None.

--*/
{
   SDL_CloseAudio();

   if (gAudioDevice.pSoundPlayer != NULL)
   {
      gAudioDevice.pSoundPlayer->Shutdown(gAudioDevice.pSoundPlayer);
      gAudioDevice.pSoundPlayer = NULL;
   }

   if (gAudioDevice.pMusPlayer)
   {
	   gAudioDevice.pMusPlayer->Shutdown(gAudioDevice.pMusPlayer);
	   gAudioDevice.pMusPlayer = NULL;
   }

   if (gAudioDevice.pCDPlayer)
   {
	   gAudioDevice.pCDPlayer->Shutdown(gAudioDevice.pCDPlayer);
	   gAudioDevice.pCDPlayer = NULL;
   }


   if (gAudioDevice.pSoundBuffer != NULL)
   {
      free(gAudioDevice.pSoundBuffer);
	  gAudioDevice.pSoundBuffer = NULL;
   }

   gAudioDevice.soundBufferSize = 0;

   if (gConfig.eMusicType == MUSIC_MIDI)
   {
      MIDI_Play(0, FALSE);
   }

   gAudioDevice.fOpened = FALSE;
}

SDL_AudioSpec*
AUDIO_GetDeviceSpec(
	VOID
)
{
	return &gAudioDevice.spec;
}

VOID
AUDIO_PlaySound(
   INT    iSoundNum
)
/*++
  Purpose:

    Play a sound in voc.mkf/sounds.mkf file.

  Parameters:

    [IN]  iSoundNum - number of the sound; the absolute value is used.

  Return value:

    None.

--*/
{
   // Unlike musics that use the 'load as required' strategy, sound player
   // load the entire sound file at once, which may cause about 0.5s or longer
   // latency for large sound files. To prevent this latency affects audio playing,
   // the mutex lock is obtained inside the SOUND_Play function rather than here.
   if (gAudioDevice.pSoundPlayer)
   {
      gAudioDevice.pSoundPlayer->Play(gAudioDevice.pSoundPlayer, abs(iSoundNum), FALSE, 0);
   }
}

VOID
AUDIO_PlayMusic(
   INT       iNumRIX,
   BOOL      fLoop,
   INT       flFadeTime
)
{
	if (iNumRIX > 0)
	{
		//
		// Stop the current CD music.
		//
		AUDIO_PlayCDTrack(-1);
	}

   if (gConfig.eMusicType == MUSIC_MIDI)
   {
      MIDI_Play(iNumRIX, fLoop);
      return;
   }

   AUDIO_Lock();
   if (gAudioDevice.pMusPlayer)
   {
      gAudioDevice.pMusPlayer->Play(gAudioDevice.pMusPlayer, iNumRIX, fLoop, flFadeTime);
   }
   AUDIO_Unlock();
}

BOOL
AUDIO_PlayCDTrack(
   INT    iNumTrack
)
/*++
  Purpose:

    Play a CD Audio Track.

  Parameters:

    [IN]  iNumTrack - number of the CD Audio Track.
                      special case: -2: do NOTHING

  Return value:

    TRUE if the track can be played, FALSE if not.

--*/
{
	BOOL ret = FALSE;
	if (iNumTrack > 0)
	{
		AUDIO_PlayMusic(-1, FALSE, 0);
	}
   if (iNumTrack == -2 && gAudioDevice.pCDPlayer != NULL &&
       gAudioDevice.pCDPlayer->iMusic > PAL_CDTRACK_BASE )
   {
       return TRUE;
   }
   AUDIO_Lock();
   if (gAudioDevice.pCDPlayer)
   {
	   if (iNumTrack != -1)
	   {
		   ret = gAudioDevice.pCDPlayer->Play(gAudioDevice.pCDPlayer, PAL_CDTRACK_BASE + iNumTrack, TRUE, 0);
	   }
	   else
	   {
		   ret = gAudioDevice.pCDPlayer->Play(gAudioDevice.pCDPlayer, -1, FALSE, 0);
	   }
   }
   AUDIO_Unlock();

   return ret;
}

VOID
AUDIO_EnableMusic(
   BOOL   fEnable
)
{
   gAudioDevice.fMusicEnabled = fEnable;
}

BOOL
AUDIO_MusicEnabled(
   VOID
)
{
   return gAudioDevice.fMusicEnabled;
}

VOID
AUDIO_EnableSound(
   BOOL   fEnable
)
{
	gAudioDevice.fSoundEnabled = fEnable;
}

BOOL
AUDIO_SoundEnabled(
   VOID
)
{
   return gAudioDevice.fSoundEnabled;
}

void
AUDIO_Lock(
	void
)
{
	SDL_LockAudio();
}

void
AUDIO_Unlock(
	void
)
{
	SDL_UnlockAudio();
}
