"""Generate an original pentatonic ensemble; no samples or commercial melody.

Only the build tool needs NumPy. Runtime plays the committed PCM WAV.
"""
import hashlib
import json
from pathlib import Path
import wave
import numpy as np
from .chart import BEAT, DURATION, TITLE, COMPOSER, chart

RATE = 22050


def create(destination=None):
    root = Path(destination) if destination else Path(__file__).with_name('audio')
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(260927)
    mix = np.zeros((int((DURATION + .15) * RATE), 2), np.float64)

    def add(at, sound, gain=1., pan=0.):
        begin = int(at * RATE)
        end = min(len(mix), begin + len(sound))
        if begin < 0 or end <= begin:
            return
        v = sound[:end-begin] * gain
        mix[begin:end, 0] += v * (1-pan*.35)
        mix[begin:end, 1] += v * (1+pan*.35)

    def drum(strength=1, high=False):
        t = np.arange(int(RATE*.9))/RATE
        f0 = 120 if high else 72
        # Falling membrane pitch, woody attack, darker long body.
        phase = 2*np.pi*(f0*t + (45 if high else 30)*.024*(1-np.exp(-t/.024)))
        v = np.sin(phase)*np.exp(-t/(.17 if high else .29))
        v += .18*np.sin(phase*1.59)*np.exp(-t/.10)
        noise = rng.standard_normal(len(t))
        v += .10*noise*np.exp(-t/.012)
        return v * (1-np.exp(-t/.0007)) * strength

    def pluck(midi, duration=1.8):
        t = np.arange(int(RATE*duration))/RATE
        f = 440*2**((midi-69)/12)
        v = sum((1/k**1.45)*np.sin(2*np.pi*f*k*t)*np.exp(-t*(1.9+.7*k)) for k in range(1, 7))
        return v * (1-np.exp(-t/.0015))

    def flute(midi, duration):
        t = np.arange(int(RATE*duration))/RATE
        f = 440*2**((midi-69)/12)
        phase = 2*np.pi*f*t + .035*np.sin(2*np.pi*5.2*t)
        env = np.minimum(1,t/.08)*np.minimum(1,np.maximum(0,duration-t)/.16)
        return (.8*np.sin(phase)+.15*np.sin(phase*2)+.05*np.sin(phase*3))*env

    # An original eight-bar phrase in D minor pentatonic, varied on repeat.
    phrases = [[62,65,69,67], [65,62,60,62], [67,69,72,69], [67,65,62,60],
               [62,67,69,74], [72,69,67,65], [67,65,60,62], [65,67,62,62]]
    for bar in range(29):
        at = 2.4 + bar*4*BEAT
        if at >= 70:
            break
        notes = phrases[bar % 8]
        for beat, note in enumerate(notes):
            add(at+beat*BEAT, pluck(note), .115 if bar<4 else .16, (-1)**beat*.65)
        if bar >= 4:
            add(at, flute(notes[0]+12, BEAT*1.85), .042, -.25)
            add(at+2*BEAT, flute(notes[2]+12, BEAT*1.85), .038, .25)
        add(at, pluck(38 if bar%2==0 else 45, 2.7), .18)
        for beat in range(4):
            add(at+beat*BEAT, drum(.44, high=beat%2==1), .27)
            if bar>=8:
                t=np.arange(int(RATE*.065))/RATE
                shake=rng.standard_normal(len(t))*np.exp(-t/.018)
                add(at+(beat+.5)*BEAT,shake,.024,(-1)**beat*.8)
    # Every playable note has an audible accent, including the easier chart.
    accents={(n['time'],n['lane']) for diff in ('easy','normal') for n in chart(diff)}
    for at,lane in sorted(accents):
        add(at,drum(1.,high=lane=='R'), .45 if lane=='B' else .31, -.3 if lane=='L' else .3 if lane=='R' else 0.)
    for at in (.0,.6,1.2,1.8):
        add(at,pluck(74,1.2),.07)
    for note in (38,50,62,69,74):
        add(69.6,pluck(note,2.5),.12)
    # A restrained short stereo room, pre-baked to avoid runtime convolution.
    dry=mix.copy()
    for delay,gain in ((.069,.13),(.131,.08),(.223,.04)):
        shift=int(delay*RATE)
        mix[shift:] += dry[:-shift,::-1]*gain
    fade=np.minimum(1,np.arange(len(mix))/RATE/.025)
    fade*=np.minimum(1,np.maximum(0,DURATION-np.arange(len(mix))/RATE)/1.2)
    mix*=fade[:,None]
    peak=float(np.max(np.abs(mix)))
    pcm=np.round(mix*(.88/max(peak,1e-9))*32767).astype('<i2')
    path=root/'qinglan-guyin.wav'
    with wave.open(str(path),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(RATE);w.writeframes(pcm[:int(DURATION*RATE)].tobytes())
    result=dict(title=TITLE,composer=COMPOSER,duration_seconds=DURATION,bpm=100,
        sample_rate=RATE,channels=2,format='PCM S16LE',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance='Original algorithmic composition and synthesis; no third-party music, recordings or samples.',
        license='Project original; generated source and waveform may be used and modified with InkSuite.',
        peak_normalized=.88,chart_difficulties=['easy','normal'])
    (root/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return result


if __name__=='__main__':
    create()
