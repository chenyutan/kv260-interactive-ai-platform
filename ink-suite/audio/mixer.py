"""Bounded PCM mixer. Used only by the audio worker, never by GTK callbacks."""
from array import array
from dataclasses import dataclass
import math
import sys


@dataclass
class Clip:
    samples: object
    gain: float = 1.0
    loop: bool = False
    priority: int = 0
    cooldown_ms: int = 0

    @property
    def frames(self):
        return len(self.samples) // 2


@dataclass(eq=False)
class Voice:
    clip: Clip
    serial: int
    position: int = 0


class Mixer:
    """48 kHz stereo PCM16 with an optional NumPy fast path.

    Asset gains provide headroom; the final saturation prevents integer wrap.
    Effects never loop, even if an erroneous manifest asks them to.
    """

    def __init__(self, tracks, effects, music_id=None, max_voices=6, use_numpy=True):
        self.music = tracks.get(music_id)
        self.effects = effects
        self.music_position = 0
        self.voices = []
        self.max_voices = max_voices
        self.serial = 0
        self.stolen = 0
        self.rejected = 0
        self.np = None
        if use_numpy:
            try:
                import numpy as np
                self.np = np
            except ImportError:
                pass
        if self.np is not None:
            for clip in list(tracks.values()) + list(effects.values()):
                if isinstance(clip.samples, array):
                    clip.samples = self.np.frombuffer(clip.samples, dtype=self.np.int16)

    def play(self, effect_id):
        clip = self.effects.get(effect_id)
        if clip is None or clip.frames == 0:
            return False
        if len(self.voices) >= self.max_voices:
            victim_index = min(range(len(self.voices)),
                               key=lambda index: (self.voices[index].clip.priority, self.voices[index].serial))
            victim = self.voices[victim_index]
            if victim.clip.priority > clip.priority:
                self.rejected += 1
                return False
            self.voices.pop(victim_index)
            self.stolen += 1
        self.serial += 1
        self.voices.append(Voice(clip, self.serial))
        return True

    def clear_effects(self):
        self.voices.clear()

    def rewind_music(self, frames):
        if self.music is not None and self.music.frames:
            if self.music.loop:
                self.music_position = (self.music_position - frames) % self.music.frames
            else:
                self.music_position = max(0, self.music_position - frames)

    def render(self, frames, music_gain=1.0, effects_gain=1.0, master=1.0, muted=False):
        if frames < 1 or frames > 4800:
            raise ValueError("Mixer block must contain 1..4800 frames")
        master = 0.0 if muted else master
        if self.np is not None:
            mixed = self.np.zeros(frames * 2, dtype=self.np.float32)
        else:
            mixed = [0.0] * (frames * 2)

        def add(clip, position, gain, loop):
            written = 0
            while written < frames and position < clip.frames:
                count = min(frames - written, clip.frames - position)
                if gain:
                    src = clip.samples[position * 2:(position + count) * 2]
                    if self.np is not None:
                        mixed[written * 2:(written + count) * 2] += src * gain
                    else:
                        offset = written * 2
                        for index, value in enumerate(src):
                            mixed[offset + index] += value * gain
                position += count
                written += count
                if loop and position == clip.frames:
                    position = 0
            return position

        if self.music is not None:
            self.music_position = add(self.music, self.music_position,
                                      music_gain * self.music.gain * master, self.music.loop)
        alive = []
        for voice in self.voices:
            voice.position = add(voice.clip, voice.position,
                                 effects_gain * voice.clip.gain * master, False)
            if voice.position < voice.clip.frames:
                alive.append(voice)
        self.voices = alive
        if self.np is not None:
            return self.np.clip(mixed, -32768, 32767).astype('<i2').tobytes()
        output = array('h', (max(-32768, min(32767, int(value))) for value in mixed))
        if sys.byteorder != 'little':
            output.byteswap()
        return output.tobytes()


def valid_gain(value):
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("Audio gains must be finite values between 0 and 1")
    return value
