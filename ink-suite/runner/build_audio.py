"""Build original pentatonic chase music and runner cues once, never at runtime.

All oscillators, envelopes and percussion are synthesized locally from a fixed
seed. No recorded samples or existing melodies are used. Verification uses only
the Python standard library and does not open a playback device.
"""
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys
import wave

RATE = 48000
ROOT = Path(__file__).resolve().parent / 'audio_assets'
EVENTS = ('jump', 'slide', 'lane', 'coin', 'power', 'hit', 'over')
MAX_PCM_BYTES = 12 * 1024 * 1024
LOOP_SECONDS = 32


def frequency(midi):
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def pluck(midi, duration=.30, gain=.16):
    import numpy as np
    t = np.arange(round(RATE * duration), dtype=np.float64) / RATE
    f = frequency(midi)
    attack = np.minimum(1.0, t / .0025)
    release = np.minimum(1.0, (duration - t) / .025)
    envelope = attack * release * np.exp(-t * (5.5 / max(.18, duration)))
    # A bright struck string with rapidly fading upper partials.
    signal = np.sin(2 * np.pi * f * t)
    for harmonic, level in ((2, .37), (3, .21), (4, .09), (5, .05)):
        if harmonic * f < RATE * .4:
            signal += level * np.sin(2 * np.pi * f * harmonic * t) * np.exp(-t * harmonic * 3)
    return signal * envelope * gain


def flute(midi, duration=.65, gain=.10, seed=0):
    import numpy as np
    t = np.arange(round(RATE * duration), dtype=np.float64) / RATE
    f = frequency(midi)
    # Vibrato develops after the onset; very light breath keeps the tone soft.
    vibrato = .0022 * np.sin(2 * np.pi * 5.1 * t) * np.minimum(1.0, t / .14)
    phase = 2 * np.pi * f * np.cumsum(1.0 + vibrato) / RATE
    rng = np.random.default_rng(seed)
    breath = rng.normal(0, .015, len(t))
    envelope = np.minimum(1.0, t / .045) * np.minimum(1.0, (duration - t) / .10)
    envelope *= .87 + .13 * np.sin(np.pi * t / duration)
    return (np.sin(phase) + .16 * np.sin(2 * phase) + .05 * np.sin(3 * phase) + breath) * envelope * gain


def drum(kind='low', gain=.09, seed=0):
    import numpy as np
    duration = .16 if kind == 'low' else .085
    t = np.arange(round(RATE * duration), dtype=np.float64) / RATE
    rng = np.random.default_rng(seed)
    if kind == 'low':
        phase = 2 * np.pi * (70 * t + 54 * .022 * (1 - np.exp(-t / .022)))
        signal = np.sin(phase) * np.exp(-t / .042) + .15 * rng.normal(0, 1, len(t)) * np.exp(-t / .012)
    else:
        signal = .65 * np.sin(2 * np.pi * 1180 * t) + .35 * rng.normal(0, 1, len(t))
        signal *= np.exp(-t / .013)
    return signal * np.minimum(1.0, t / .0015) * np.minimum(1.0, (duration - t) / .010) * gain


def add(target, signal, seconds, pan=0.0):
    import numpy as np
    start = round(seconds * RATE)
    count = min(len(signal), len(target) - start)
    if start < 0 or count <= 0:
        return
    # Gentle equal-power panning; mono output remains intact on one speaker.
    left = math.cos((pan + 1) * math.pi / 4)
    right = math.sin((pan + 1) * math.pi / 4)
    target[start:start + count, 0] += signal[:count] * left
    target[start:start + count, 1] += signal[:count] * right


def music():
    import numpy as np
    target = np.zeros((RATE * LOOP_SECONDS, 2), dtype=np.float64)
    # D major pentatonic: D E F# A B. Sixteen bars at 120 BPM.
    # None marks a rest; the second phrase changes direction and register.
    phrases = (
        (74, None, 78, 81, 83, 81, 78, 76),
        (74, 78, 76, None, 69, 74, 76, 78),
        (81, None, 83, 86, 83, 81, 78, None),
        (76, 78, 81, 78, 76, None, 74, None),
        (78, 81, 83, None, 81, 78, 76, 74),
        (76, None, 78, 81, 78, 76, 69, None),
        (74, 76, 78, 81, 83, None, 86, 83),
        (81, 78, 76, None, 74, None, None, None),
        (74, 78, 81, None, 86, 83, 81, 78),
        (76, 78, 81, 78, 74, None, 69, 74),
        (83, None, 86, 90, 86, 83, 81, None),
        (78, 81, 83, 81, 78, None, 76, 74),
        (78, None, 81, 83, 86, 83, 81, 78),
        (76, 78, 81, None, 78, 76, 74, 69),
        (74, 78, 81, 83, 86, None, 83, 81),
        (78, 76, 74, None, 69, None, 74, None),
    )
    roots = (50, 57, 59, 57, 50, 54, 57, 50) * 2
    for bar, phrase in enumerate(phrases):
        for step, note in enumerate(phrase):
            if note is None:
                continue
            at = bar * 2 + step * .25
            duration = .30 if step % 2 else .40
            add(target, pluck(note, duration, .15 if step % 2 else .18), at, -.18)
        # Light driving bass and percussion, with unaccented syncopation.
        for beat in range(4):
            at = bar * 2 + beat * .5
            add(target, pluck(roots[bar] + (12 if beat % 2 else 0), .35, .13), at, .05)
            add(target, drum('low' if beat in (0, 2) else 'high', .095 if beat in (0, 2) else .045, bar * 4 + beat), at)
            if beat in (1, 3):
                add(target, drum('high', .020, 100 + bar * 4 + beat), at + .25, .25)
        # Short flute responses leave the plucked melody clearly articulated.
        if bar % 4 in (1, 3):
            notes = (78, 76) if bar % 4 == 1 else (81, 74)
            add(target, flute(notes[0], .65, .065, bar), bar * 2 + .20, .22)
            add(target, flute(notes[1], .65, .060, bar + 50), bar * 2 + 1.05, .18)
    # Seam ends at zero; no residual release tail creates a loop click.
    fade = round(.04 * RATE)
    target[:fade] *= np.linspace(0, 1, fade)[:, None]
    target[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return target


def cue(event):
    import numpy as np
    patterns = {
        'jump': ((74, 81, 86), .055, .23),
        'slide': ((81, 76, 69), .060, .21),
        'lane': ((74, 78), .035, .17),
        'coin': ((86, 90), .070, .25),
        'power': ((74, 78, 81, 86, 90, 93), .065, .22),
        'hit': ((57, 50, 45), .075, .24),
        'over': ((81, 78, 76, 69, 62), .145, .22),
    }
    notes, interval, gain = patterns[event]
    duration = len(notes) * interval + .13
    signal = np.zeros((round(RATE * duration), 2), dtype=np.float64)
    for i, note in enumerate(notes):
        add(signal, pluck(note, interval + .11, gain), i * interval)
    if event in ('slide', 'hit'):
        t = np.arange(len(signal)) / RATE
        rng = np.random.default_rng(512 if event == 'slide' else 777)
        rush = rng.normal(0, 1, len(t))
        # Convolve a small box once offline to soften the noise's harsh edge.
        rush = np.convolve(rush, np.ones(11) / 11, mode='same')
        envelope = np.minimum(1, t / .009) * np.exp(-t / (.10 if event == 'slide' else .06))
        signal[:, 0] += rush * envelope * .16
        signal[:, 1] += rush * envelope * .16
    if event == 'power':
        add(signal, flute(86, .31, .08, 42), .12, .1)
    signal[-round(.015 * RATE):] *= np.linspace(1, 0, round(.015 * RATE))[:, None]
    return signal


def write(root, name, samples, loop=False, priority=1, gain=.72):
    import numpy as np
    root.mkdir(parents=True, exist_ok=True)
    # The mix has headroom already; reject rather than silently clip a build.
    if samples.ndim != 2 or samples.shape[1] != 2 or not np.isfinite(samples).all():
        raise ValueError('Expected finite stereo samples: ' + name)
    peak = float(np.max(np.abs(samples)))
    if peak > .94:
        raise ValueError('Mix exceeds headroom: ' + name)
    pcm = np.rint(samples * 32767).astype('<i2')
    path = root / (name + '.wav')
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm.tobytes())
    return dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                duration_seconds=len(samples) / RATE, gain=gain, loop=loop,
                cooldown_ms=0 if loop else (35 if name.endswith('_coin') else 55),
                priority=priority)


def build(root=ROOT):
    root = Path(root)
    tracks = {'bgm_runner': write(root, 'bgm_runner', music(), True, 0, .76)}
    priorities = {'hit': 4, 'over': 6, 'power': 3}
    effects = {'sfx_runner_' + event: write(root, 'sfx_runner_' + event, cue(event),
               priority=priorities.get(event, 1), gain=.82 if event == 'over' else .76)
               for event in EVENTS}
    manifest = dict(schema=1, sample_rate=RATE, channels=2, format='PCM S16LE',
        provenance='Original deterministic pentatonic plucked-string/flute chase composition and synthesized effects; no external samples or copied melodies.',
        tracks=tracks, effects=effects)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return dict(built=True, tracks=1, effects=len(EVENTS), runtime_synthesis=False)


def verify_assets(root=ROOT):
    """Validate shipped bytes and mix headroom, using no synthesizer or device."""
    root = Path(root).resolve()
    data = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if (data.get('schema'), data.get('sample_rate'), data.get('channels'), data.get('format')) != (1, RATE, 2, 'PCM S16LE'):
        raise ValueError('Expected schema1 PCM16 stereo48000Hz manifest')
    if set(data.get('tracks', {})) != {'bgm_runner'}:
        raise ValueError('Expected exactly bgm_runner')
    if set(data.get('effects', {})) != {'sfx_runner_' + name for name in EVENTS}:
        raise ValueError('Expected the seven runner effect IDs')
    files = set()
    pcm_bytes = 0
    statistics = {}
    for group in ('tracks', 'effects'):
        for clip_id, entry in data[group].items():
            name = entry['file']
            original = root / name
            path = original.resolve()
            if (original.is_symlink() or path.parent != root or Path(name).name != name
                    or path.suffix.lower() != '.wav' or name in files):
                raise ValueError('Expected unique in-directory WAV: ' + clip_id)
            files.add(name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('Checksum mismatch: ' + clip_id)
            with wave.open(str(path), 'rb') as source:
                if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (RATE, 2, 2, 'NONE'):
                    raise ValueError('Expected PCM16 stereo48000Hz: ' + clip_id)
                frames = source.getnframes()
                payload = source.readframes(frames)
            if not frames or len(payload) != frames * 4:
                raise ValueError('Empty or truncated PCM: ' + clip_id)
            pcm_bytes += len(payload)
            if abs(frames / RATE - entry['duration_seconds']) > 1 / RATE:
                raise ValueError('Duration mismatch: ' + clip_id)
            if entry.get('loop') != (group == 'tracks'):
                raise ValueError('Only background music loops: ' + clip_id)
            if (not isinstance(entry.get('gain'), (int, float)) or not math.isfinite(entry['gain'])
                    or not 0 <= entry['gain'] <= 1
                    or not isinstance(entry.get('cooldown_ms'), int) or not 0 <= entry['cooldown_ms'] <= 60000
                    or not isinstance(entry.get('priority'), int) or not 0 <= entry['priority'] <= 10):
                raise ValueError('Invalid gain/cooldown/priority: ' + clip_id)
            samples = array('h')
            samples.frombytes(payload)
            if sys.byteorder != 'little':
                samples.byteswap()
            peak = max(abs(value) for value in samples)
            rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
            if not 200 <= peak <= round(.95 * 32768) or rms < .001:
                raise ValueError('Silent or clipped audio: ' + clip_id)
            channel_rms = [math.sqrt(sum(value * value for value in samples[c::2]) / frames) / 32768 for c in (0, 1)]
            if min(channel_rms) < .001:
                raise ValueError('Silent channel: ' + clip_id)
            if group == 'tracks':
                if frames != RATE * LOOP_SECONDS:
                    raise ValueError('Expected exactly 32-second loop')
                if max(abs(samples[c] - samples[-2 + c]) for c in (0, 1)) > 32:
                    raise ValueError('Loop seam discontinuity')
                # Reject a mistakenly empty musical bar despite a nonzero file.
                for start in range(0, len(samples), RATE * 4):
                    block = samples[start:start + RATE * 4]
                    if math.sqrt(sum(value * value for value in block) / len(block)) < 80:
                        raise ValueError('Silent musical bar')
            statistics[clip_id] = dict(seconds=frames / RATE, peak=peak,
                                      rms=round(rms, 6), channel_rms=[round(v, 6) for v in channel_rms])
    if {p.name for p in root.glob('*.wav')} != files:
        raise ValueError('Unmanifested or missing WAV')
    if pcm_bytes >= MAX_PCM_BYTES:
        raise ValueError('Runner audio cache must remain below 12MiB')
    return dict(passed=True, tracks=1, effects=len(EVENTS), pcm_bytes=pcm_bytes,
                cache_limit_bytes=MAX_PCM_BYTES, clips=statistics,
                runtime_synthesis=False, cube_route_selected=False,
                physical_playback_verified=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT)
    args = parser.parse_args()
    if not args.verify_only:
        print(json.dumps(build(args.output)))
    print(json.dumps(verify_assets(args.output), indent=2))
