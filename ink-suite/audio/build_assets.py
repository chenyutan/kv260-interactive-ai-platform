"""Deterministically compose InkSuite's original music and sound palette.

Build-time dependency: NumPy. Runtime needs only the generated PCM16 WAV files.
No samples, recordings, downloaded material, MIDI files, or commercial melodies.
Run `python audio/build_assets.py` from any directory; nothing is played.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import wave

import numpy as np

ROOT = Path(__file__).resolve().parent
RATE = 48000
SEED = 260929
PROVENANCE = ('Original algorithmic composition and physical-inspired synthesis; '
              'no third-party music, recordings, samples, or copied melodies.')


def hz(midi):
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def rng_for(*key):
    raw = ':'.join(map(str, (SEED,) + key)).encode()
    return np.random.default_rng(int.from_bytes(hashlib.sha256(raw).digest()[:8], 'little'))


def edges(x, attack=.003, release=.025):
    """Zero-valued endpoints, smooth short attack and release; preserve duration."""
    x = np.asarray(x, dtype=np.float64).copy()
    a, r = min(len(x), max(2, round(attack * RATE))), min(len(x), max(2, round(release * RATE)))
    env = np.ones(len(x))
    env[:a] *= np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    env[-r:] *= np.sin(np.linspace(np.pi / 2, 0, r)) ** 2
    return x * (env[:, None] if x.ndim == 2 else env)


def noise(n, key, smoothing=1):
    x = rng_for('noise', key).standard_normal(n)
    if smoothing > 1:
        x = np.convolve(x, np.ones(smoothing) / math.sqrt(smoothing), mode='same')
    return x


def pluck(note, duration=1.6, kind='qin'):
    t = np.arange(round(duration * RATE)) / RATE
    f = hz(note)
    brightness = {'qin': 1.58, 'pipa': 1.15, 'bell': 1.9, 'bass': 1.8}[kind]
    decay = {'qin': 1.45, 'pipa': 3.2, 'bell': 1.1, 'bass': 1.8}[kind]
    y = np.zeros_like(t)
    for k in range(1, 11 if kind == 'pipa' else 8):
        # Small dispersion gives strings a natural, slightly inharmonic decay.
        partial = f * k * (1.0 + .00019 * k * k)
        if partial > RATE * .4:
            continue
        phase = 2 * np.pi * partial * (t + .00007 * (1 - np.exp(-t / .021)))
        y += (np.sin(phase) / k ** brightness) * np.exp(-t * decay * (.72 + k * .27))
    y += .018 * noise(len(t), ('pluck', note, kind), 6) * np.exp(-t / .012)
    return edges(y * (1 - np.exp(-t / .0015)), .0018, .055)


def flute(note, duration=1.1):
    t = np.arange(round(duration * RATE)) / RATE
    # A breath-controlled attack, gentle pitch approach, and delayed vibrato.
    f = hz(note)
    vibrato = .0043 * f * np.sin(2 * np.pi * 5.25 * t) * np.minimum(1, t / .35)
    freq = f * (1 - .0025 * np.exp(-t / .045)) + vibrato
    phase = np.cumsum(freq) * (2 * np.pi / RATE)
    y = .83 * np.sin(phase) + .12 * np.sin(phase * 2) + .035 * np.sin(phase * 3)
    y += .019 * noise(len(t), ('flute', note, duration), 8)
    y *= .92 + .08 * np.sin(np.pi * np.minimum(1, t / max(duration, .1)))
    return edges(y, min(.075, duration * .18), min(.15, duration * .25))


def bowed(note, duration=2.0):
    t = np.arange(round(duration * RATE)) / RATE
    f = hz(note)
    phase = 2 * np.pi * f * t + .047 * np.sin(2 * np.pi * 5.8 * t)
    y = sum(np.sin(phase * k + .05 * k) / k ** 1.6 for k in range(1, 10))
    y += .32 * np.sin(2 * np.pi * f * 1.0015 * t)
    return edges(y * (.94 + .06 * np.sin(2 * np.pi * 2.3 * t)), .12, min(.22, duration * .3))


def drum(high=False, duration=.65, power=1.0):
    t = np.arange(round(duration * RATE)) / RATE
    f = 143 if high else 79
    phase = 2 * np.pi * (f * t + (42 if high else 48) * .027 * (1 - np.exp(-t / .027)))
    y = np.sin(phase) * np.exp(-t / (.12 if high else .225))
    y += .22 * np.sin(phase * 1.57) * np.exp(-t / .064)
    if not high:
        # Keep a recognizable low drum body on small speakers. The stable
        # 158/237 Hz partials carry the attack and decay above a 120 Hz cutoff.
        y += .68 * np.sin(phase * 2) * np.exp(-t / .20)
        y += .24 * np.sin(phase * 3) * np.exp(-t / .14)
    y += .16 * noise(len(t), ('drum', high), 4) * np.exp(-t / .011)
    return edges(y * power, .0008, .035)


def wood(duration=.11, pitch=1450):
    t = np.arange(round(duration * RATE)) / RATE
    y = np.sin(2 * np.pi * pitch * t) * np.exp(-t / .012)
    y += .43 * np.sin(2 * np.pi * pitch * 1.48 * t) * np.exp(-t / .008)
    return edges(y, .0006, .016)


def shaker(duration=.12, seed=0):
    t = np.arange(round(duration * RATE)) / RATE
    n = noise(len(t), ('shaker', seed))
    y = (n - np.convolve(n, np.ones(11) / 11, mode='same')) * np.exp(-t / .025)
    return edges(y, .002, .02)


def swoosh(duration=.22, seed=0, descending=False):
    t = np.arange(round(duration * RATE)) / RATE
    f = 1800 - 1400 * t / duration if descending else 650 + 1800 * t / duration
    carrier = np.sin(np.cumsum(f) * (2 * np.pi / RATE))
    n = noise(len(t), ('swoosh', seed), 5)
    envelope = np.sin(np.pi * t / duration) ** 1.8
    return edges((.72 * n + .11 * carrier) * envelope, .003, .012)


class Score:
    def __init__(self, duration, loop=False):
        self.data = np.zeros((round(duration * RATE), 2), dtype=np.float64)
        self.loop = loop
        self.events = []

    def add(self, at, sound, gain=1., pan=0., label=None):
        start = round(at * RATE)
        y = np.asarray(sound)
        if y.ndim == 1:
            angle = (max(-1., min(1., pan)) + 1) * np.pi / 4
            y = y[:, None] * np.array([np.cos(angle), np.sin(angle)])[None, :]
        y = y * gain
        if self.loop:
            # Fold complete instrument/reverb tails into the next repetition.
            for offset in range(0, len(y), len(self.data)):
                part = y[offset:offset + len(self.data)]
                pos = (start + offset) % len(self.data)
                count = min(len(part), len(self.data) - pos)
                self.data[pos:pos + count] += part[:count]
                if count < len(part):
                    self.data[:len(part) - count] += part[count:]
        else:
            lo, hi = max(start, 0), min(start + len(y), len(self.data))
            if hi > lo:
                self.data[lo:hi] += y[lo - start:hi - start]
        if label:
            self.events.append({'time': round(at, 6), 'sample': start, 'label': label})

    def room(self, wet=.16):
        dry = self.data.copy()
        taps = [(.037, .72), (.061, .59), (.103, .47), (.151, .35),
                (.211, .27), (.307, .19), (.419, .13), (.557, .08)]
        for i, (delay, gain) in enumerate(taps):
            shift = round(delay * RATE)
            source = dry[:, ::-1] if i % 2 == 0 else dry
            if self.loop:
                self.data += np.roll(source, shift, axis=0) * gain * wet
            else:
                self.data[shift:] += source[:-shift] * gain * wet
        return self

    def finish(self, peak=.78):
        x = self.data - self.data.mean(axis=0)
        # 3 ms equal-slope edge windows avoid quantized wrap clicks without
        # padding silence; all musical tails have already been circularly mixed.
        attack, release = (.003, .003) if self.loop else (.008, .065)
        x = edges(x, attack, release)
        # Windowing an asymmetric drum transient can reintroduce DC. Remove
        # that residual through the same window so zero endpoints stay intact.
        correction = edges(np.ones(len(x)), attack, release)
        x -= correction[:, None] * (x.sum(axis=0) / correction.sum())
        maximum = float(np.max(np.abs(x)))
        return x * (peak / max(maximum, 1e-12))


def ink():
    """24 bars / 80 BPM / 72 seconds; three related eight-bar phrases."""
    beat, bars = .75, 24
    s = Score(bars * 4 * beat, True)
    melodies = [[74, 77, 79, 77], [72, 74, 69, 72], [77, 79, 81, 79], [77, 74, 72, 69],
                [74, 79, 81, 84], [81, 79, 77, 74], [72, 77, 74, 69], [72, 69, 74, 74]]
    roots = [38, 38, 36, 36, 34, 34, 33, 38]
    for bar in range(bars):
        at, phrase = bar * 4 * beat, melodies[bar % 8]
        root = roots[bar % 8]
        s.add(at, pluck(root, 3.9, 'bass'), .18, -.08)
        for j, step in enumerate([0, 7, 12, 19]):
            s.add(at + j * .75 * beat, pluck(root + 12 + step, 2.5), .12 - j * .009, -.48 + j * .3)
        # A sparse question, an answering flute, then an ornamented return.
        if bar < 8:
            for j in (0, 2):
                s.add(at + (j + .25) * beat, flute(phrase[j], beat * 1.5), .072, .28)
        elif bar < 16:
            for j, note in enumerate(phrase):
                s.add(at + (j + .08) * beat, flute(note, beat * .88), .066, .25)
        else:
            for j in (0, 2):
                s.add(at + j * beat, flute(phrase[j], beat * 1.65), .072, .25)
            s.add(at + 2.7 * beat, pluck(phrase[1], 1.8), .078, -.48)
            s.add(at + 3.35 * beat, pluck(phrase[2], 1.5), .063, .42)
        if bar % 4 == 0:
            s.add(at, pluck(86 if bar % 8 == 0 else 81, 3.0, 'bell'), .026, .55)
    return s.room(.22), {'title': '溪山听雨', 'bpm': 80, 'bars': 24, 'mode': 'D minor pentatonic',
                         'instruments': ['synthesized guqin', 'breathy bamboo flute', 'low plucked strings', 'bell harmonics']}


def fruit():
    """32 bars / 120 BPM / 64 seconds; nimble pipa/flute call and response."""
    beat, bars = .5, 32
    s = Score(bars * 4 * beat, True)
    motifs = [[74, 78, 81, 78, 76, 74], [76, 78, 83, 81, 78, 76],
              [78, 81, 86, 83, 81, 78], [76, 74, 71, 73, 76, 74],
              [81, 83, 86, 83, 81, 78], [78, 76, 74, 76, 78, 81],
              [83, 81, 78, 76, 73, 71], [76, 78, 81, 76, 74, 74]]
    roots = [38, 38, 47, 45, 43, 43, 45, 38]
    for bar in range(bars):
        at, root, notes = bar * 4 * beat, roots[bar % 8], motifs[bar % 8]
        section = bar // 8
        for j, pos in enumerate([0, .5, 1.5, 2, 2.75, 3.5]):
            n = notes[j] + (12 if section == 2 and j in (1, 4) else 0)
            s.add(at + pos * beat, pluck(n, 1.0, 'pipa'), .122 if j % 3 == 0 else .087, -.4 + .16 * j)
        s.add(at, pluck(root, 1.45, 'bass'), .23, -.08)
        s.add(at + 2 * beat, pluck(root + 7, 1.2, 'bass'), .14, .1)
        for j in range(4):
            s.add(at + j * beat, drum(j % 2 == 1, .4), .049 if j % 2 else .085, 0)
            s.add(at + (j + .5) * beat, shaker(seed=bar * 4 + j), .010, (-1) ** j * .5)
        if section in (1, 2) and bar % 2 == 1:
            s.add(at + .75 * beat, flute(notes[1], 1.2 * beat), .057, -.26)
            s.add(at + 2.5 * beat, flute(notes[4], 1.1 * beat), .046, .24)
        if section != 0:
            s.add(at + 1 * beat, wood(pitch=1200), .037, .35)
            s.add(at + 3 * beat, wood(pitch=1550), .031, -.35)
        if bar % 8 == 7:
            for j, n in enumerate([74, 76, 78, 81]):
                s.add(at + (3 + j / 4) * beat, pluck(n, .6, 'pipa'), .056, -.45 + .3 * j)
    return s.room(.13), {'title': '青竹拾果', 'bpm': 120, 'bars': 32, 'mode': 'D major pentatonic with passing tones',
                         'instruments': ['synthesized pipa', 'bamboo flute', 'wood blocks', 'hand drums', 'shakers']}


def fight():
    """32 bars / 112 BPM; energetic but controlled drums and bowed strings."""
    beat, bars = 60 / 112, 32
    s = Score(bars * 4 * beat, True)
    roots = [38, 38, 36, 36, 34, 34, 33, 33]
    leads = [[74, 77, 79, 81], [79, 77, 74, 72], [72, 77, 79, 84], [81, 79, 77, 74],
             [77, 79, 81, 84], [86, 84, 81, 79], [77, 74, 72, 69], [72, 69, 72, 74]]
    for bar in range(bars):
        at, root, section = bar * 4 * beat, roots[bar % 8], bar // 8
        for j, step in enumerate([0, 12, 7, 12, 0, 12, 10, 7]):
            s.add(at + j * .5 * beat, pluck(root + 12 + step, .8, 'pipa'), .088 if j % 2 == 0 else .047, (-1) ** j * .36)
        s.add(at, bowed(root + 12, beat * 3.8), .060, -.4)
        s.add(at, bowed(root + 19, beat * 3.8), .034, .4)
        for pos, hi, strength in [(0, False, .23), (1.5, True, .11), (2, False, .18), (3, True, .14), (3.5, True, .078)]:
            s.add(at + pos * beat, drum(hi), strength, .18 if hi else -.08)
        for j in range(8):
            s.add(at + (j + .5) * .5 * beat, shaker(.085, bar * 8 + j), .011, (-1) ** j * .55)
        if section != 0 or bar % 2:
            for j, note in enumerate(leads[bar % 8]):
                s.add(at + j * beat, bowed(note, beat * .84), .041 if section < 2 else .056, .1)
        if section in (1, 3):
            s.add(at + 1 * beat, wood(pitch=1150), .044, .3)
            s.add(at + 3 * beat, wood(pitch=980), .044, -.3)
        if bar % 8 == 7:
            for j in range(4):
                s.add(at + (3 + j / 4) * beat, drum(j % 2 == 1, .35), .059 + .014 * j, (-1) ** j * .3)
    return s.room(.12), {'title': '破阵山河', 'bpm': 112, 'bars': 32, 'mode': 'D minor pentatonic',
                         'instruments': ['synthesized low war drums', 'bowed string ensemble', 'pipa ostinato', 'wood blocks']}


def sword():
    """32 bars / 108 BPM: flowing E-pentatonic arpeggios and soaring flute.

    Four eight-bar sections alternate flight, pursuit, cloud passage and return.
    This is a new score, not a tempo/pitch variation of the fighting track.
    """
    beat, bars = 60 / 108, 32
    s = Score(bars * 4 * beat, True)
    roots = [40, 40, 36, 38, 40, 36, 35, 40]
    motifs = [[76, 79, 83, 81], [79, 76, 74, 71], [76, 79, 81, 86], [83, 81, 79, 74],
              [83, 86, 88, 86], [83, 81, 79, 76], [74, 71, 74, 79], [81, 79, 76, 76]]
    sections = ['踏云', '逐风', '穿岚', '归峰']
    for bar in range(bars):
        at, section = bar * 4 * beat, bar // 8
        root, notes = roots[bar % 8], motifs[bar % 8]
        # Rolling guqin figures use an asymmetric 3+3+2 contour; counter-plucks
        # answer from the opposite side rather than doubling the same pitches.
        steps = [0, 7, 12, 19, 14, 12, 7, 12]
        if bar % 2:
            steps = [12, 7, 0, 7, 19, 12, 14, 7]
        for j, step in enumerate(steps):
            offset = (j * .5 + (.035 if j % 2 else 0)) * beat
            accent = 1.0 if j in (0, 3, 6) else .70
            s.add(at + offset, pluck(root + 12 + step, 1.5), .097 * accent,
                  -.58 + .16 * j)
        s.add(at, pluck(root, 1.95, 'bass'), .20, -.05)
        s.add(at + 2.5 * beat, pluck(root + 7, 1.0, 'bass'), .10, .08)
        # Breath-length melody: long rising phrases, short answering gestures.
        if section in (0, 2):
            s.add(at + .25 * beat, flute(notes[0], 1.6 * beat), .059, .25)
            s.add(at + 2.2 * beat, flute(notes[2] if section == 0 else notes[3], 1.5 * beat), .055, -.16)
        else:
            for j, pos in enumerate([.125, 1.25, 2.0, 3.125]):
                pitch = notes[j] + (12 if section == 3 and bar % 8 == 4 and j == 2 else 0)
                s.add(at + pos * beat, flute(pitch, (.90 if j != 1 else .62) * beat), .067, .18)
        if section != 2:
            for pos, high, gain in [(0, False, .18), (1.5, True, .105), (2.5, False, .14), (3.5, True, .09)]:
                s.add(at + pos * beat, drum(high, .6), gain, .12 if high else -.10)
        else:
            # A genuine contrasting passage: half-time drum bed and airy chimes.
            s.add(at, drum(False, .75), .145, -.12)
            s.add(at + 2 * beat, drum(True, .5), .080, .12)
            s.add(at + 1.5 * beat, pluck(notes[1] + 12, 1.7, 'bell'), .041, .5)
        for j in range(8):
            s.add(at + (j + .5) * .5 * beat, shaker(.09, 1000 + bar * 8 + j),
                  .010 if section == 2 else .013, (-1) ** j * .55)
        if section in (1, 3):
            s.add(at + beat, wood(.10, 1330), .039, -.35)
            s.add(at + 3 * beat, wood(.10, 1660), .033, .35)
            for j, pitch in enumerate([notes[0] - 12, notes[1] - 12, notes[2] - 12]):
                s.add(at + (.75 + j) * beat, pluck(pitch, .7, 'pipa'), .046, -.28)
        if bar % 8 == 7:
            for j, pitch in enumerate([71, 74, 76, 79]):
                s.add(at + (3 + j / 4) * beat, pluck(pitch, 1.0), .055, -.45 + .3 * j)
        if bar % 8 == 0:
            s.add(at, pluck(95, 2.3, 'bell'), .029, .6)
    return s.room(.18), {'title': '御风行云', 'bpm': 108, 'bars': 32,
                         'mode': 'E minor pentatonic',
                         'sections': [{'title': name, 'start_seconds': i * 8 * 4 * beat,
                                       'bars': 8} for i, name in enumerate(sections)],
                         'instruments': ['synthesized flowing guqin', 'soaring bamboo flute',
                                         'pipa counter-phrases', 'hand drums', 'bell harmonics', 'shakers']}


def load_chart():
    source = ROOT.parent / 'taiko' / 'chart.py'
    spec = importlib.util.spec_from_file_location('inksuite_original_taiko_chart', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, source


def taiko():
    chart_module, chart_path = load_chart()
    beat = chart_module.BEAT
    s = Score(chart_module.DURATION, False)
    phrases = [[62, 65, 69, 67], [65, 62, 60, 62], [67, 69, 72, 69], [67, 65, 62, 60],
               [62, 67, 69, 74], [72, 69, 67, 65], [67, 65, 60, 62], [65, 67, 62, 62]]
    for bar in range(28):
        at, notes = 2.4 + bar * 4 * beat, phrases[bar % 8]
        for j, note in enumerate(notes):
            s.add(at + j * beat, pluck(note, 1.65), .13 if bar < 4 else .17, (-1) ** j * .45)
        s.add(at, pluck(38 if bar % 2 == 0 else 45, 2.5, 'bass'), .20)
        if bar >= 4:
            s.add(at, flute(notes[0] + 12, beat * 1.8), .052, -.22)
            s.add(at + 2 * beat, flute(notes[2] + 12, beat * 1.8), .047, .22)
        for j in range(4):
            s.add(at + j * beat, drum(j % 2 == 1), .088)
            if bar >= 8:
                s.add(at + (j + .5) * beat, shaker(seed=bar * 4 + j), .016, (-1) ** j * .45)
    # The exact union of the original easy and normal chart is accented.
    union = sorted({(n['time'], n['lane']) for diff in ('easy', 'normal') for n in chart_module.chart(diff)})
    for at, lane in union:
        s.add(at, drum(lane == 'R', .8), .39 if lane == 'B' else .31,
              -.3 if lane == 'L' else .3 if lane == 'R' else 0., label='chart:' + lane)
    for at in (0., .6, 1.2, 1.8):
        s.add(at, pluck(74, .9, 'bell'), .087, label='count_in')
    for note in (38, 50, 62, 69, 74):
        s.add(69.6, pluck(note, 2.4), .15)
    s.room(.15)
    # The final chord decays naturally into EOS; do not alter the 72 s clock.
    tail = round(1.1 * RATE)
    s.data[-tail:] *= np.sin(np.linspace(np.pi / 2, 0, tail))[:, None] ** 2
    details = {'title': chart_module.TITLE + ' · 琴笛重编', 'bpm': chart_module.BPM,
               'bars': 30, 'mode': 'D minor pentatonic', 'chart_source': '../taiko/chart.py',
               'chart_sha256': hashlib.sha256(chart_path.read_bytes()).hexdigest(),
               'chart_difficulties': ['easy', 'normal'],
               'chart_note_counts': {d: len(chart_module.chart(d)) for d in ('easy', 'normal')},
               'instruments': ['synthesized taiko', 'guqin', 'bamboo flute', 'shakers'],
               'preserves_original_timeline': True, 'count_in_seconds': 2.4, 'final_hit_seconds': 69.6}
    return s, details


def effect_score(duration, notes=(), drums=(), swishes=()):
    s = Score(duration)
    for at, note, length, gain, pan in notes:
        s.add(at, pluck(note, length, 'pipa' if length < .5 else 'qin'), gain, pan)
    for at, high, gain, pan in drums:
        s.add(at, drum(high, min(.75, duration - at)), gain, pan)
    for at, length, gain, pan, down in swishes:
        s.add(at, swoosh(length, round(at * 1000), down), gain, pan)
    return s


def effects():
    """Distinct short event signatures; restrained gains and meaningful priorities."""
    specs = {}
    def add(key, title, score, priority=40, cooldown=80, gain=.80, wet=.10):
        specs[key] = (score.room(wet), {'title': title, 'priority': priority, 'cooldown_ms': cooldown, 'gain': gain})
    def notes(values, spacing=.06, duration=.65):
        return [(i * spacing, n, max(.15, duration - i * spacing - .08), .7, (i % 2 * 2 - 1) * .2) for i, n in enumerate(values)]
    add('ui_open', '开卷', effect_score(.78, notes([69, 74, 81], .07, .72)), 35, 250, .67)
    add('ui_select', '移签', effect_score(.17, notes([81], duration=.16)), 20, 65, .58, .03)
    add('ui_confirm', '落印', effect_score(.42, notes([74, 81, 86], .045, .39)), 45, 160, .72)
    add('ui_back', '收卷', effect_score(.34, notes([81, 74], .055, .31)), 40, 160, .65)
    add('ink_stroke', '笔触', effect_score(.22, swishes=[(0, .14, .5, -.15, True)]), 8, 160, .24, .015)
    add('ink_save', '留墨', effect_score(1.1, notes([74, 79, 81, 86], .11, 1.02)), 65, 450, .76)
    add('ink_clear', '拂纸', effect_score(.52, notes=[(.1, 62, .35, .25, 0)], swishes=[(0, .29, .6, 0, True)]), 45, 350, .53)
    add('fruit_slice', '破果', effect_score(.21, notes=[(.025, 86, .13, .24, .2)], swishes=[(0, .125, .75, -.1, False)]), 40, 42, .79, .025)
    add('fruit_combo', '连斩', effect_score(.48, notes([81, 86, 90], .045, .41)), 62, 180, .77)
    add('fruit_double', '双倍', effect_score(.85, notes([74, 78, 81, 86], .095, .77)), 68, 380, .76)
    freeze = effect_score(1.0, notes([98, 93, 86, 81], .10, .94))
    freeze.add(0, swoosh(.7, 44, True), .18, .2)
    add('fruit_freeze', '凝霜', freeze, 68, 500, .69, .22)
    bomb = effect_score(.72, drums=[(0, False, .8, 0), (.055, True, .22, .3)], swishes=[(.025, .33, .75, 0, True)])
    bomb.add(0, pluck(33, .68, 'bass'), .7)
    add('fruit_bomb', '惊雷', bomb, 90, 380, .79, .08)
    add('fruit_miss', '落果', effect_score(.26, notes=[(0, 57, .22, .6, -.15)]), 28, 200, .55, .02)
    add('fight_swing', '掠风', effect_score(.20, swishes=[(0, .16, .8, -.2, False)]), 28, 80, .66, .02)
    hit = effect_score(.31, drums=[(0, True, .9, 0)], swishes=[(0, .085, .26, .15, True)])
    hit.add(0, wood(.08, 1250), .23)
    add('fight_hit', '拳震', hit, 70, 55, .82, .04)
    add('fight_kick', '踏岳', effect_score(.43, drums=[(.012, False, 1., 0)], swishes=[(0, .15, .52, -.15, False)]), 72, 100, .82, .05)
    block = effect_score(.28, notes=[(0, 93, .20, .21, .18)], drums=[(0, True, .28, 0)])
    block.add(0, wood(.13, 2050), .8)
    add('fight_block', '格挡', block, 66, 80, .73, .03)
    special = effect_score(1.08, notes([62, 69, 74, 81, 86], .085, .95),
                           drums=[(.31, False, .7, 0)], swishes=[(0, .42, .63, 0, False)])
    add('fight_special', '破阵', special, 94, 650, .82, .13)
    add('fight_win', '凯旋', effect_score(1.55, notes([62, 67, 69, 74, 81, 86], .115, 1.45),
                                        drums=[(0, False, .27, 0), (.46, False, .38, 0)]), 100, 1200, .81, .20)
    add('fight_lose', '惜败', effect_score(1.24, notes([74, 72, 69, 62], .14, 1.13)), 100, 1200, .71, .17)
    add('taiko_left', '左鼓', effect_score(.36, drums=[(0, False, 1., -.26)]), 78, 35, .80, .025)
    add('taiko_right', '右鼓', effect_score(.28, drums=[(0, True, 1., .26)]), 78, 35, .80, .025)
    add('taiko_both', '合鼓', effect_score(.44, drums=[(0, False, .83, -.3), (0, True, .67, .3)]), 83, 35, .83, .035)
    miss = Score(.13)
    miss.add(0, wood(.11, 700), .5, -.1)
    add('taiko_miss', '轻枯', miss, 32, 100, .42, .015)
    add('taiko_finish', '曲终', effect_score(1.7, notes([62, 69, 74, 81, 86], .105, 1.58),
                                           drums=[(0, False, .35, 0), (.42, False, .48, 0)]), 100, 1000, .80, .20)
    return specs


def sword_effects():
    """Nine gameplay cues with soft repeated shots and more prominent outcomes."""
    specs = {}
    def add(key, title, score, priority, cooldown, gain, wet=.08):
        specs[key] = (score.room(wet), {'title': title, 'priority': priority,
                                      'cooldown_ms': cooldown, 'gain': gain})
    shot = Score(.115)
    t = np.arange(round(.088 * RATE)) / RATE
    phase = 2 * np.pi * (1920 * t - 650 * t * t / .088)
    shot.add(0, edges((np.sin(phase) + .19 * np.sin(phase * 2)) * np.exp(-t / .025), .0015, .02), .55, -.1)
    shot.add(.008, pluck(88, .09, 'pipa'), .16, .1)
    add('sword_shot', '飞剑', shot, 20, 65, .43, .015)
    hit = Score(.21)
    hit.add(0, wood(.13, 1730), .42, .12)
    hit.add(0, drum(True, .19), .35, -.1)
    hit.add(.009, pluck(83, .16, 'pipa'), .28, .1)
    add('sword_hit', '剑鸣', hit, 42, 70, .59, .045)
    kill = effect_score(.47, notes=[(0, 79, .36, .38, -.25), (.055, 83, .33, .48, 0),
                                  (.105, 88, .32, .42, .3)])
    kill.add(0, swoosh(.14, 7201), .20, .1)
    add('sword_kill', '破邪', kill, 53, 90, .66)
    hurt = effect_score(.52, notes=[(.02, 52, .40, .38, 0), (.07, 51, .31, .18, -.2)],
                        drums=[(0, False, .62, 0)], swishes=[(0, .16, .27, .2, True)])
    add('sword_hurt', '受创', hurt, 78, 220, .78, .06)
    collect = effect_score(.42, notes=[(0, 88, .28, .36, -.2), (.065, 95, .30, .42, .2)])
    add('sword_collect', '纳灵', collect, 35, 90, .55, .13)
    special = effect_score(1.20,
        notes=[(i * .055, pitch, .85 - i * .055, .32, -.5 + i * .2)
               for i, pitch in enumerate([64, 71, 76, 79, 83, 88])],
        drums=[(.26, False, .60, 0), (.38, True, .25, .2)],
        swishes=[(0, .42, .46, -.12, False)])
    special.add(.3, flute(88, .65), .095, .28)
    add('sword_special', '万剑归宗', special, 92, 650, .82, .18)
    boss = effect_score(1.46,
        notes=[(0, 40, 1.2, .41, -.1), (.20, 47, 1.05, .38, .15), (.45, 52, .88, .34, -.1)],
        drums=[(0, False, .72, -.12), (.24, False, .52, .12), (.52, True, .39, 0)])
    boss.add(.38, bowed(67, .88), .14, .15)
    boss.add(.38, bowed(71, .88), .085, -.15)
    add('sword_boss', '劫云临阵', boss, 96, 1400, .81, .14)
    win = effect_score(1.82,
        notes=[(i * .105, pitch, 1.65 - i * .105, .39, (-1) ** i * .25)
               for i, pitch in enumerate([64, 71, 76, 79, 83, 88])],
        drums=[(0, False, .24, 0), (.42, False, .35, 0)])
    win.add(.58, flute(88, .9), .11, .22)
    add('sword_win', '凌云凯归', win, 100, 1600, .79, .21)
    lose = effect_score(1.42,
        notes=[(0, 83, 1.25, .39, -.22), (.14, 79, 1.08, .37, .15),
               (.29, 74, .92, .39, -.1), (.46, 64, .86, .40, .1)])
    add('sword_lose', '剑意未尽', lose, 100, 1400, .71, .19)
    return specs


def write_wave(path, audio):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.rint(np.clip(audio, -1., 1.) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm.tobytes())
    return pcm


def read_wave(path):
    with wave.open(str(path), 'rb') as f:
        parameters = {'sample_rate': f.getframerate(), 'channels': f.getnchannels(),
                      'sample_width_bytes': f.getsampwidth(), 'frame_count': f.getnframes(),
                      'compression': f.getcomptype()}
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype='<i2').reshape(-1, 2)
    return pcm, parameters


def db(value):
    return round(20 * math.log10(max(float(value), 1e-12)), 3)


def waveform_metrics(pcm, loop=False):
    y = pcm.astype(np.float64) / 32768
    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(y * y)))
    end_jump = float(np.max(np.abs(y[0] - y[-1])))
    width = float(np.sqrt(np.mean((y[:, 0] - y[:, 1]) ** 2)))
    # Reconstruct 4x oversampled, windowed high-peak neighborhoods using FFT.
    # This is an objective intersample-peak estimate, not a BS.1770 LUFS meter.
    interp_peak = peak
    for channel in range(2):
        strongest = np.argpartition(np.abs(y[:, channel]), -min(10, len(y)))[-10:]
        for index in set((strongest // 4096).tolist()):
            start = max(0, min(index * 4096, len(y) - 8192))
            block = y[start:start + 8192, channel]
            if len(block) < 32:
                continue
            windowed = block * np.hanning(len(block))
            up = np.fft.irfft(np.fft.rfft(windowed), n=len(block) * 4) * 4
            interp_peak = max(interp_peak, float(np.max(np.abs(up))))
    return {'peak_dbfs': db(peak), 'rms_dbfs': db(rms), 'crest_factor_db': db(peak / max(rms, 1e-12)),
            'estimated_intersample_peak_dbfs': db(interp_peak),
            'clipped_sample_count': int(np.count_nonzero(np.abs(pcm.astype(np.int32)) >= 32767)),
            'dc_offset': [round(float(n), 8) for n in y.mean(axis=0)],
            'stereo_side_rms_dbfs': db(width),
            'first_sample': pcm[0].tolist(), 'last_sample': pcm[-1].tolist(),
            'boundary_jump': end_jump, 'loop': loop,
            'silence_ratio_below_minus60_db': round(float(np.mean(np.max(np.abs(y), axis=1) < .001)), 6)}


AUTHORIZED_CLAP_REMAPS = {
    'easy': ((7, 10.8, 'R'), (11, 15.6, 'B'), (25, 32.4, 'B'), (49, 61.2, 'L')),
    'normal': ((7, 10.8, 'R'), (11, 15.6, 'B'), (49, 51.6, 'L'), (59, 61.2, 'L')),
    'hard': ((7, 10.8, 'R'), (57, 42., 'R'), (64, 50.4, 'B'), (85, 68.4, 'R')),
}
# Hard was added after this WAV was composed. Pin the full pre-clap release
# event table, not a new claim about the immutable composition-time manifest.
HARD_CHART_BASELINE = dict(
    note_count=87,
    source_chart_sha256='05263e7ed2cfc0f161a328244f84f1f6d85e4887e73930af97a2875416e800d6',
    events_sha256='8196ab886ef089887a6f93fd89e73ab90cb7fa6d38f64fc453679e9169d54798',
    source_reference='diagnostics/taiko-gameplay-20260930/bundle/'
                     'c1365d3363c93f59b25751d287bcb262c83152756b08ce5585d4f5c68c33dde2/'
                     'payload/taiko/chart.py',
)


def _clap_declaration_matches(chart_module):
    declared = getattr(chart_module, 'CLAP_TIMES', None)
    if not isinstance(declared, dict) or set(declared) != set(AUTHORIZED_CLAP_REMAPS):
        return False
    try:
        return all(tuple(declared[level]) == tuple(at for _, at, _ in entries)
                   for level, entries in AUTHORIZED_CLAP_REMAPS.items())
    except TypeError:
        return False


def _authorized_clap_notes(difficulty, notes, emitted, reference=None):
    """Restore only the pinned same-ID/time C substitutions for comparison."""
    approved = {(ident, at): lane for ident, at, lane in AUTHORIZED_CLAP_REMAPS[difficulty]}
    archived = {(n['id'], n['time']): n['lane'] for n in reference} if reference is not None else None
    normalized, remaps, unauthorized = [], [], []
    for note in notes:
        restored = dict(note)
        if note['lane'] == 'C':
            key = (note['id'], note['time'])
            original = approved.get(key)
            if original is None or (archived is not None and archived.get(key) != original):
                unauthorized.append(dict(note))
            else:
                restored['lane'] = original
                accent = emitted.get((note['time'], original))
                if accent is not None:
                    source = dict(kind='archived_chart_accent', label='chart:' + original,
                                  time=note['time'], sample=accent,
                                  reference='audio/qa/taiko-accent-schedule.json')
                else:
                    # Only hard may rely on the pre-existing quiet beat bed.
                    source = dict(kind='existing_100bpm_beat_accompaniment' if difficulty == 'hard'
                                  else 'missing_archived_chart_accent',
                                  time=note['time'], sample=round(note['time'] * RATE),
                                  reference='audio/build_assets.py:taiko 28-bar drum/pluck accompaniment')
                remaps.append(dict(difficulty=difficulty, id=note['id'], time=note['time'],
                                   original_lane=original, new_lane='C', accent_source=source))
        normalized.append(restored)
    found = [(r['id'], r['time']) for r in remaps]
    complete = len(found) == len(approved) and set(found) == set(approved) and not unauthorized
    return normalized, remaps, unauthorized, complete


def chart_compatibility(chart_module, chart_path, manifest, schedule, baseline=None):
    """Compare musical events, while retaining the actual build-time source hash.

    Adding a level or practice constants changes source bytes without changing
    the music that was composed. Only the explicitly pinned clap substitutions
    may reuse their original accents. Every other event remains exact, including
    the later hard release whose complete event digest is independent of this
    caller's chart module and CLAP_TIMES declaration.
    """
    track = manifest['tracks']['bgm_taiko']
    current_hash = hashlib.sha256(Path(chart_path).read_bytes()).hexdigest()
    built_hash = track['chart_sha256']
    if baseline is None:
        path = ROOT / 'qa' / 'taiko-chart-baseline-20260929.json'
        baseline = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    have_baseline = baseline.get('source_chart_sha256') == built_hash
    emitted = {(e['time'], e['label'].split(':', 1)[1]): e['sample']
               for e in schedule if e['label'].startswith('chart:')}
    declaration_ok = _clap_declaration_matches(chart_module)
    all_remaps = []
    levels = {}
    for difficulty in ('easy', 'normal'):
        notes = chart_module.chart(difficulty)
        reference = (baseline.get('charts') or {}).get(difficulty) if have_baseline else None
        # A same-source fresh composition may validate without a historical
        # table. A changed source requires the archive-backed event baseline.
        same_events = notes == reference if reference is not None else current_hash == built_hash
        normalized, remaps, unauthorized, complete = _authorized_clap_notes(difficulty, notes, emitted, reference)
        all_remaps.extend(remaps)
        compatible_events = normalized == reference if reference is not None else same_events
        missing = [(n['time'], n['lane']) for n in normalized if (n['time'], n['lane']) not in emitted]
        sample_errors = [abs(emitted[(n['time'], n['lane'])] - n['time'] * RATE)
                         for n in normalized if (n['time'], n['lane']) in emitted]
        count_ok = len(notes) == track['chart_note_counts'][difficulty]
        levels[difficulty] = dict(note_count=len(notes), expected_note_count=track['chart_note_counts'][difficulty],
                                  events_equal_to_build_time=same_events,
                                  events_equal_after_authorized_clap_remaps=compatible_events,
                                  authorized_clap_remaps=remaps, unauthorized_clap_notes=unauthorized,
                                  authorized_claps_complete=complete,
                                  missing_accents=missing,
                                  maximum_rounding_error_samples=max(sample_errors, default=0.),
                                  passed=declaration_ok and complete and compatible_events and count_ok and not missing and bool(sample_errors)
                                         and max(sample_errors) <= .5)
    hard = dict(present='hard' in getattr(chart_module, 'DIFFICULTIES', {}), passed=False,
                release_event_baseline=dict(HARD_CHART_BASELINE), build_time_hard_chart_present=False)
    if hard['present']:
        # taiko() writes quiet drum + plucked-string accompaniment on every
        # 100-BPM beat for all 28 bars, in addition to the original accents.
        grid = {round((2.4 + (bar * 4 + beat) * .6) * RATE)
                for bar in range(28) for beat in range(4)}
        hard_notes = chart_module.chart('hard')
        normalized, remaps, unauthorized, complete = _authorized_clap_notes('hard', hard_notes, emitted)
        all_remaps.extend(remaps)
        event_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
        same_events = event_hash == HARD_CHART_BASELINE['events_sha256']
        missing = [n for n in normalized if (n['time'], n['lane']) not in emitted
                   and round(n['time'] * RATE) not in grid]
        aligned = all(abs(round(n['time'] * RATE) - n['time'] * RATE) <= .5 for n in normalized)
        hard.update(note_count=len(hard_notes), missing_background_beats=missing,
                    inherited_heavy_accents=sum((n['time'], n['lane']) in emitted for n in normalized),
                    normalized_events_sha256=event_hash, events_equal_to_release_baseline=same_events,
                    authorized_clap_remaps=remaps, unauthorized_clap_notes=unauthorized,
                    authorized_claps_complete=complete,
                    beat_accompaniment_bpm=100,
                    passed=declaration_ok and complete and same_events and not missing and aligned
                           and len(hard_notes) == HARD_CHART_BASELINE['note_count'],
                    note='Hard retains its pinned later release events and existing beat accompaniment; '
                         'this does not claim a composition-time hard chart or a heavy accent on every hard note.')
    timeline_ok = (chart_module.BPM == track['bpm'] == 100 and chart_module.DURATION == track['duration_seconds'] == 72.
                   and abs(chart_module.BEAT - .6) < 1e-12)
    return dict(passed=timeline_ok and all(n['passed'] for n in levels.values()) and hard.get('passed', True),
                build_time_chart_sha256=built_hash, current_chart_sha256=current_hash,
                source_changed=current_hash != built_hash, archive_event_baseline_used=have_baseline,
                clap_declaration_matches_authorization=declaration_ok,
                authorized_clap_remaps=all_remaps,
                timeline_compatible=timeline_ok, legacy_levels=levels, hard=hard,
                audio_manifest_mutated=False, waveform_regenerated=False)


def chart_accent_alignment(compatibility):
    """Expose the same strict authorized-remap result to the PCM validator."""
    return {level: dict(note_count=details['note_count'],
                        missing_accents=sorted(details['missing_accents']),
                        maximum_rounding_error_samples=details['maximum_rounding_error_samples'],
                        authorized_clap_remaps=details['authorized_clap_remaps'],
                        authorized_claps_complete=details['authorized_claps_complete'],
                        events_equal_after_authorized_clap_remaps=details['events_equal_after_authorized_clap_remaps'],
                        passed=details['passed'])
            for level, details in compatibility['legacy_levels'].items()}


def validate(destination, manifest, report_path=None):
    checks, results = [], {}
    expect_tracks = {'bgm_ink', 'bgm_fruit', 'bgm_fight', 'bgm_taiko', 'bgm_sword'}
    expect_fx = {'ui_open', 'ui_select', 'ui_confirm', 'ui_back', 'ink_stroke', 'ink_save', 'ink_clear',
                 'fruit_slice', 'fruit_combo', 'fruit_double', 'fruit_freeze', 'fruit_bomb', 'fruit_miss',
                 'fight_swing', 'fight_hit', 'fight_kick', 'fight_block', 'fight_special', 'fight_win', 'fight_lose',
                 'taiko_left', 'taiko_right', 'taiko_both', 'taiko_miss', 'taiko_finish',
                 'sword_shot', 'sword_hit', 'sword_kill', 'sword_hurt', 'sword_collect',
                 'sword_special', 'sword_boss', 'sword_win', 'sword_lose'}
    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})
    check('exact_track_ids', set(manifest['tracks']) == expect_tracks)
    check('exact_effect_ids', set(manifest['effects']) == expect_fx)
    check('manifest_contract', manifest['schema'] == 1 and manifest['sample_rate'] == RATE and manifest['channels'] == 2)
    for key, entry in {**manifest['tracks'], **manifest['effects']}.items():
        path = destination / entry['file']
        pcm, header = read_wave(path)
        metric = waveform_metrics(pcm, entry['loop'])
        results[key] = {**header, **metric, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        check(key + ':pcm16_stereo_48000', header['sample_rate'] == RATE and header['channels'] == 2 and header['sample_width_bytes'] == 2 and header['compression'] == 'NONE')
        check(key + ':hash', results[key]['sha256'] == entry['sha256'])
        check(key + ':duration', abs(header['frame_count'] / RATE - entry['duration_seconds']) < 1 / RATE)
        check(key + ':headroom', metric['peak_dbfs'] < -1.5 and metric['estimated_intersample_peak_dbfs'] < -1.)
        check(key + ':no_clipping', metric['clipped_sample_count'] == 0)
        check(key + ':dc', max(abs(n) for n in metric['dc_offset']) < .001)
        check(key + ':zero_boundary', metric['boundary_jump'] == 0 and pcm[0].tolist() == [0, 0])
        check(key + ':audible_energy', metric['rms_dbfs'] > -35)
        if entry['loop']:
            check(key + ':loop_60_to_90_seconds', 60 <= len(pcm) / RATE <= 90)
            check(key + ':no_padded_silence', metric['silence_ratio_below_minus60_db'] < .02)
    chart_module, chart_path = load_chart()
    schedule = json.loads((destination / 'qa' / 'taiko-accent-schedule.json').read_text(encoding='utf-8'))
    emitted = {(e['time'], e['label'].split(':', 1)[1]) for e in schedule if e['label'].startswith('chart:')}
    compatibility = chart_compatibility(chart_module, chart_path, manifest, schedule)
    alignment = chart_accent_alignment(compatibility)
    for difficulty, details in alignment.items():
        check('taiko:' + difficulty + ':every_note_has_accent', not details['missing_accents'])
        check('taiko:' + difficulty + ':sample_alignment', details['maximum_rounding_error_samples'] <= .5)
    check('taiko:build_time_events_preserved', all(n['passed'] for n in compatibility['legacy_levels'].values()))
    check('taiko:timeline_compatible', compatibility['timeline_compatible'])
    check('taiko:new_level_has_existing_beat_accompaniment', compatibility['hard'].get('passed', True))
    check('taiko:exact_72_seconds', results['bgm_taiko']['frame_count'] == 72 * RATE)
    check('taiko:eos_not_loop', manifest['tracks']['bgm_taiko']['loop'] is False)
    check('taiko:final_hit', (69.6, 'B') in emitted)
    # At each real chart accent, measure a forward window in the rendered WAV.
    taiko_pcm, _ = read_wave(destination / manifest['tracks']['bgm_taiko']['file'])
    accent_levels = []
    for at, _ in emitted:
        offset = round(at * RATE)
        data = taiko_pcm[offset:offset + round(.06 * RATE)].astype(float) / 32768
        accent_levels.append(db(np.sqrt(np.mean(data * data))))
    check('taiko:all_rendered_accents_have_energy', min(accent_levels) > -35)
    baseline_path = destination / 'qa' / 'legacy-audio-baseline-20260929.json'
    legacy = {'baseline_available': baseline_path.is_file(), 'preserved_assets': 0}
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        entries = {**manifest['tracks'], **manifest['effects']}
        for key, previous in baseline['assets'].items():
            preserved = results[key]['sha256'] == previous['sha256']
            check('legacy:' + key + ':unchanged_wav', preserved)
            check('legacy:' + key + ':unchanged_metadata', entries[key] == previous['manifest_entry'])
            legacy['preserved_assets'] += int(preserved)
        check('legacy:all_29_preserved', legacy['preserved_assets'] == 29)
        legacy['previous_manifest_sha256'] = baseline['manifest_sha256']
        for filename, field in [('review.wav', 'preview_sha256'), ('chapters.json', 'preview_chapters_sha256')]:
            path = destination / 'preview' / filename
            check('legacy:' + filename + ':unchanged', path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == baseline[field])
    report = {'passed': all(c['passed'] for c in checks), 'passed_count': sum(c['passed'] for c in checks),
              'check_count': len(checks), 'checks': checks, 'assets': results, 'chart_alignment': alignment,
              'legacy_preservation': legacy, 'chart_compatibility': compatibility,
              'quietest_taiko_accent_window_rms_dbfs': min(accent_levels),
              'method': 'Offline PCM/header/hash/energy/zero-boundary/sample-schedule checks; no playback, board, camera, or speaker access.',
              'limitations': ['No subjective listening performed.', 'No 8BitDo Cube RF/latency/output-level measurement.',
                              'Intersample peak is a windowed 4x FFT estimate, not certified true-peak/LUFS.']}
    target = Path(report_path) if report_path is not None else destination / 'qa' / 'report.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['passed']:
        raise AssertionError([c['name'] for c in checks if not c['passed']])
    return report


def preview(destination, manifest):
    pieces, chapters, cursor = [], [], 0.0
    # Interior excerpts show fully developed phrases, then complete effect cues.
    for key, start in [('bgm_ink', 24), ('bgm_fruit', 20), ('bgm_fight', 24), ('bgm_taiko', 28.8)]:
        entry = manifest['tracks'][key]
        pcm, _ = read_wave(destination / entry['file'])
        clip = pcm[round(start * RATE):round((start + 16) * RATE)].astype(float) / 32768
        clip = edges(clip * .86, .15, .30)
        chapters.append({'id': key, 'title': entry['title'], 'start_seconds': cursor,
                         'duration_seconds': len(clip) / RATE, 'source_offset_seconds': start})
        pieces += [clip, np.zeros((round(.55 * RATE), 2))]
        cursor += len(clip) / RATE + .55
    for key, entry in manifest['effects'].items():
        if key.startswith('sword_'):
            continue
        pcm, _ = read_wave(destination / entry['file'])
        clip = pcm.astype(float) / 32768 * .65
        chapters.append({'id': key, 'title': entry['title'], 'start_seconds': round(cursor, 5), 'duration_seconds': len(clip) / RATE})
        pieces += [clip, np.zeros((round(.30 * RATE), 2))]
        cursor += len(clip) / RATE + .30
    write_wave(destination / 'preview' / 'review.wav', np.concatenate(pieces))
    (destination / 'preview' / 'chapters.json').write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 音频试听索引', '', '本文件只提供索引，生成脚本不调用任何播放设备。', '',
             '| 起点 | ID | 曲目／音效 |', '|---:|---|---|']
    for item in chapters:
        seconds = item['start_seconds']
        lines.append(f"| {int(seconds // 60):02d}:{seconds % 60:05.2f} | `{item['id']}` | {item['title']} |")
    (destination / 'preview' / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def sword_preview(destination, manifest):
    """Separate review keeps the original four-mode review byte-for-byte intact."""
    pieces, chapters, cursor = [], [], 0.0
    entry = manifest['tracks']['bgm_sword']
    pcm, _ = read_wave(destination / entry['file'])
    # Sample each arrangement, then demonstrate the actual end/start loop join.
    for section in entry['sections']:
        start = section['start_seconds']
        clip = pcm[round(start * RATE):round((start + 8) * RATE)].astype(float) / 32768
        clip = edges(clip * .86, .06, .18)
        chapters.append({'id': 'bgm_sword', 'title': entry['title'] + ' · ' + section['title'],
                         'start_seconds': round(cursor, 6), 'duration_seconds': len(clip) / RATE,
                         'source_offset_seconds': start})
        pieces += [clip, np.zeros((round(.4 * RATE), 2))]
        cursor += len(clip) / RATE + .4
    join = np.concatenate([pcm[-4 * RATE:], pcm[:4 * RATE]]).astype(float) / 32768
    join = edges(join * .86, .06, .18)
    chapters.append({'id': 'bgm_sword_loop_join', 'title': '原始循环接缝（片段中间）',
                     'start_seconds': round(cursor, 6), 'duration_seconds': 8.,
                     'loop_join_seconds': round(cursor + 4, 6)})
    pieces += [join, np.zeros((round(.5 * RATE), 2))]
    cursor += 8.5
    for key, entry in manifest['effects'].items():
        if not key.startswith('sword_'):
            continue
        pcm, _ = read_wave(destination / entry['file'])
        clip = pcm.astype(float) / 32768 * .65
        chapters.append({'id': key, 'title': entry['title'], 'start_seconds': round(cursor, 6),
                         'duration_seconds': len(clip) / RATE})
        pieces += [clip, np.zeros((round(.32 * RATE), 2))]
        cursor += len(clip) / RATE + .32
    write_wave(destination / 'preview' / 'sword-review.wav', np.concatenate(pieces))
    (destination / 'preview' / 'sword-chapters.json').write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 御剑音频试听索引', '', '独立新增合集，原 review.wav 与 chapters.json 保持原哈希。生成脚本未播放声音。', '',
             '| 起点 | ID | 乐段／音效 |', '|---:|---|---|']
    for item in chapters:
        seconds = item['start_seconds']
        lines.append(f"| {int(seconds // 60):02d}:{seconds % 60:05.2f} | `{item['id']}` | {item['title']} |")
    lines += ['', '前四段为 8 秒乐段；33.60 秒起的第 5 段拼接原曲末 4 秒和开头 4 秒，37.60 秒为循环接缝。之后为 9 个完整效果。']
    (destination / 'preview' / 'SWORD_REVIEW.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def documentation(destination, manifest, report):
    lines = ['# 国风原创音频包 · 2026-09-29', '',
             '5 首原创程序编曲、34 个事件音效；全部 48 kHz、双声道、16-bit PCM WAV。使用 NumPy 与 Python 标准库生成，不含第三方采样、录音或商业旋律。', '',
             '| ID | 曲目 | 秒数 | BPM | 循环 | 峰值 dBFS | RMS dBFS |', '|---|---|---:|---:|---|---:|---:|']
    for key, entry in manifest['tracks'].items():
        m = report['assets'][key]
        lines.append(f"| `{key}` | {entry['title']} | {entry['duration_seconds']:.3f} | {entry['bpm']} | {'是' if entry['loop'] else '否'} | {m['peak_dbfs']} | {m['rms_dbfs']} |")
    lines += ['', '水墨以古琴式泛音、气息笛声与留白构成三段问答；水果以琵琶式短弹拨、竹笛与木鱼式打击形成轻快呼应；格斗使用低鼓、弓弦与弹拨固定音型。全部乐器由振荡器、衰减包络和确定性噪声合成，未声称真实民族乐器录音。', '',
              '太鼓《青岚鼓引》沿用项目原八小节旋律并重新配器，保留 100 BPM、72 秒、2.4 秒引子和 69.6 秒终击。easy/normal 的每个谱面事件都在原采样位置写入鼓点；不改谱面、不以循环替代曲终。', '',
              '新增御剑《御风行云》为 E 小调五声音阶、108 BPM、32 小节（71.111 秒），由踏云／逐风／穿岚／归峰四个 8 小节段落构成。流动琴声与长短笛句相答，中段半拍鼓点形成对比，最后回归较密的追逐节奏；并非格斗曲的变速或移调版本。', '',
              '4 首循环曲使用完整乐句闭合、圆周折回乐器与混响尾音、3 ms 零端点平滑，文件无补零尾段。所有瞬态保留余量并有短淡入淡出；房间反射已预烘焙，运行时无需卷积。', '',
              f"离线客观 QA：**{report['passed_count']}/{report['check_count']} 通过**。检查 WAV 头、SHA-256、时长、峰值、估计插值峰值、削波、直流、首尾边界、有效能量，以及两种太鼓谱面逐事件对齐。具体数据见 `qa/report.json`，谱面事件与采样索引见 `qa/taiko-accent-schedule.json`。", '',
              '补充复核：`python audio/qa/verify_reproducibility.py` 在内存中重新合成全部 39 个资源并逐采样比较，同时复核两个试听合集章节、峰值与鼓声频段；结果见 `qa/reproducibility.json`。该测试也不打开播放设备。', '',
              '完整生成命令：`python audio/build_assets.py`（仅构建环境需要 NumPy）；御剑增量：`python audio/build_assets.py --sword-only`；复核命令：`python audio/build_assets.py --verify-only`。增量只写新增 10 个 WAV 和新版 manifest／报告，不重写旧 WAV 或旧试听合集。manifest 中 file 相对于 audio/，运行时只读取 WAV；gain 是建议相对增益，最终混音仍须保留多音重叠余量。', '',
              '版本对照：原 4 首 BGM、25 音效（29 文件）与原 review.wav／chapters.json 均保留原 SHA-256，基线见 `qa/legacy-audio-baseline-20260929.json`；新增为 bgm_sword + sword_shot / sword_hit / sword_kill / sword_hurt / sword_collect / sword_special / sword_boss / sword_win / sword_lose。旧 manifest SHA-256 为 `7431f2247f9863740ecb594edc58859565c27626d24a354478e8b036640c342f`，新版 manifest 扩展条目但旧条目不变。原发布包未改动。', '',
              '`preview/review.wav` 保留原 4 曲片段和 25 个音效；新增 `preview/sword-review.wav` 提供四个乐段、一次原始循环接缝和 9 个完整音效，索引为 `preview/sword-chapters.json`。两个合集都仅生成、未播放。', '',
              '4 个 UI 音效（ui_open / ui_select / ui_confirm / ui_back）作为资源预备，本次未接入主菜单发声。其余 21 个音效用于水墨、水果、格斗、太鼓的已接受事件。低鼓另含 158 / 237 Hz 持续泛音，保留小型扬声器可重放的鼓身。', '',
              '未完成项目：主观试听、8BitDo Cube 2.4G 设备枚举、端到端延迟与扬声器音量验收。以上需要设备在手后单独执行；本报告不把离线测试视为硬件验收。', '',
              '本次未连接板卡、摄像头、扬声器，未启动任何音频输出设备。']
    (destination / 'AUDIO_ASSETS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def build(destination=ROOT, verify_only=False, sword_only=False, report_path=None):
    destination = Path(destination).resolve()
    (destination / 'qa').mkdir(parents=True, exist_ok=True)
    if verify_only:
        manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
        result = validate(destination, manifest, report_path)
        print(json.dumps({'passed': result['passed'], 'checks': result['check_count']}, ensure_ascii=False))
        return
    if sword_only:
        manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    else:
        manifest = {'schema': 1, 'sample_rate': RATE, 'channels': 2, 'format': 'PCM S16LE',
                'seed': SEED, 'provenance': PROVENANCE,
                'license': 'Project-original generated source and waveforms; may be used and modified with InkSuite.',
                'tracks': {}, 'effects': {}}
    track_builders = [('bgm_sword', sword)] if sword_only else [('bgm_ink', ink), ('bgm_fruit', fruit), ('bgm_fight', fight), ('bgm_taiko', taiko), ('bgm_sword', sword)]
    for key, maker in track_builders:
        print('Composing', key, flush=True)
        score, details = maker()
        pcm = write_wave(destination / 'assets' / (key + '.wav'), score.finish(.76))
        path = destination / 'assets' / (key + '.wav')
        manifest['tracks'][key] = {'file': 'assets/' + path.name, 'loop': score.loop, 'gain': .72, **details,
                                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'duration_seconds': len(pcm) / RATE}
        if key == 'bgm_taiko':
            (destination / 'qa' / 'taiko-accent-schedule.json').write_text(json.dumps(score.events, ensure_ascii=False, indent=2), encoding='utf-8')
    effect_builders = sword_effects() if sword_only else {**effects(), **sword_effects()}
    for key, (score, details) in effect_builders.items():
        path = destination / 'assets' / (key + '.wav')
        pcm = write_wave(path, score.finish(.78))
        manifest['effects'][key] = {'file': 'assets/' + path.name, 'loop': False, **details,
                                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                    'duration_seconds': len(pcm) / RATE}
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not sword_only:
        preview(destination, manifest)
    sword_preview(destination, manifest)
    report = validate(destination, manifest, report_path)
    documentation(destination, manifest, report)
    print(json.dumps({'passed': report['passed'], 'checks': report['check_count'], 'tracks': len(manifest['tracks']),
                      'effects': len(manifest['effects']), 'manifest': str(destination / 'manifest.json'),
                      'preview': str(destination / 'preview' / 'review.wav')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--sword-only', action='store_true', help='Append/rebuild only the new sword assets, preserving the earlier WAV files and review.')
    parser.add_argument('--report', type=Path, help='Write QA to a new report path, preserving earlier validation records.')
    args = parser.parse_args()
    build(args.output, args.verify_only, args.sword_only, args.report)
