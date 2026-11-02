"""Deterministic original pentatonic chiptune; run only at build time."""
import hashlib
import json
from pathlib import Path
import wave

RATE=48000
ROOT=Path(__file__).resolve().parent/'audio_assets'
EVENTS=('jump','coin','power','stomp','hurt','die','pipe','checkpoint','clear','win')

def tone(notes, seconds=.1, gain=.25):
    import numpy as np
    pieces=[]
    for midi in notes:
        t=np.arange(round(RATE*seconds))/RATE
        freq=440*2**((midi-69)/12)
        # Band-limited pulse-like harmonics avoid high-frequency aliasing.
        y=sum(np.sin(2*np.pi*freq*k*t)/k for k in (1,3,5) if freq*k<RATE*.4)
        envelope=np.minimum(1,t/.004)*np.minimum(1,(seconds-t)/.02)
        pieces.append(y*envelope*gain)
    return np.concatenate(pieces)

def write(name, samples, loop=False, priority=0):
    import numpy as np
    samples=np.clip(samples,-.85,.85)
    stereo=np.repeat((samples*32767).astype('<i2')[:,None],2,axis=1)
    path=ROOT/(name+'.wav')
    with wave.open(str(path),'wb') as stream:
        stream.setparams((2,2,RATE,0,'NONE','not compressed'))
        stream.writeframes(stereo.tobytes())
    return dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                duration_seconds=len(samples)/RATE,gain=.72,loop=loop,
                cooldown_ms=0 if loop else 55,priority=priority)

def build():
    import numpy as np
    ROOT.mkdir(parents=True,exist_ok=True)
    beat=.25
    melody=(74,76,79,81,79,76,74,71,69,71,74,76,74,71,69,67,
            69,74,76,79,81,83,81,79,76,74,71,69,71,74,76,74)
    n=round(32*RATE)
    bgm=np.zeros(n)
    for i in range(128):
        note=melody[(i//2+i//32*4)%len(melody)]
        value=tone((note,),.20,.12)
        pos=round(i*beat*RATE)
        bgm[pos:pos+len(value)]+=value
        if i%2==0:
            bass=(50,55,57,55)[i//16%4]
            low=tone((bass,),.38,.07)
            bgm[pos:min(n,pos+len(low))]+=low[:min(len(low),n-pos)]
        click=tone((86 if i%4 else 62,),.032,.035)
        bgm[pos:pos+len(click)]+=click
    # Seam-safe composition: phrase ends before the loop boundary, no jump.
    tracks={'bgm_platformer':write('bgm_platformer',bgm,True)}
    cues={'jump':((69,76,81),.055),'coin':((83,90),.06),'power':((69,74,76,79,81,86),.07),
          'stomp':((55,43),.06),'hurt':((67,60,55),.06),'die':((76,72,69,64,57),.12),
          'pipe':((62,57,50),.08),'checkpoint':((74,79,83),.08),
          'clear':((74,76,79,81,86,81),.10),'win':((74,79,81,86,88,91,86),.13)}
    priorities={'hurt':3,'die':5,'clear':4,'win':6,'power':2,'checkpoint':2}
    effects={'platformer_'+name:write('platformer_'+name,tone(notes,duration,.27),priority=priorities.get(name,1))
             for name,(notes,duration) in cues.items()}
    manifest=dict(schema=1,sample_rate=RATE,channels=2,format='PCM S16LE',
        provenance='Original algorithmic pentatonic chiptune; no external samples or copied melodies.',
        tracks=tracks,effects=effects)
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(built=True,tracks=len(tracks),effects=len(effects),
                         bytes=sum(p.stat().st_size for p in ROOT.iterdir()))))

def verify_assets(root=ROOT):
    """Strict packaging check; no NumPy, GStreamer or speaker access."""
    root=Path(root).resolve()
    data=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if (data.get('schema'),data.get('sample_rate'),data.get('channels'),data.get('format'))!=(1,RATE,2,'PCM S16LE'):
        raise ValueError('Expected schema1 PCM16 stereo48000Hz manifest')
    if set(data.get('tracks',{}))!={'bgm_platformer'}:
        raise ValueError('Expected exactly bgm_platformer')
    if set(data.get('effects',{}))!={'platformer_'+name for name in EVENTS}:
        raise ValueError('Expected exactly the ten platformer effect IDs')
    files=set();pcm_bytes=0
    for group in ('tracks','effects'):
        for clip_id,entry in data[group].items():
            name=entry['file'];path=(root/name).resolve()
            if path.parent!=root or path.suffix.lower()!='.wav' or name in files:
                raise ValueError('Expected unique in-directory WAV file: '+clip_id)
            files.add(name)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
                raise ValueError('Checksum mismatch: '+clip_id)
            with wave.open(str(path),'rb') as source:
                if (source.getframerate(),source.getnchannels(),source.getsampwidth(),source.getcomptype())!=(RATE,2,2,'NONE'):
                    raise ValueError('Expected PCM16 stereo48000Hz: '+clip_id)
                frames=source.getnframes();payload=source.readframes(frames)
                if not frames or len(payload)!=frames*4:
                    raise ValueError('Empty or truncated PCM: '+clip_id)
                pcm_bytes+=len(payload)
                if abs(frames/RATE-entry['duration_seconds'])>1/RATE:
                    raise ValueError('Duration mismatch: '+clip_id)
            if entry.get('loop')!=(group=='tracks'):
                raise ValueError('Only background music loops: '+clip_id)
            if not 0<=entry.get('gain',-1)<=1 or not 0<=entry.get('cooldown_ms',-1)<=60000:
                raise ValueError('Invalid runtime gain/cooldown: '+clip_id)
    if {p.name for p in root.glob('*.wav')}!=files:
        raise ValueError('Unmanifested or missing WAV file')
    if pcm_bytes>96*1024*1024:
        raise ValueError('PCM cache exceeds existing director limit')
    return dict(passed=True,tracks=1,effects=10,pcm_bytes=pcm_bytes,
                cube_route_selected=False,physical_playback_verified=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if not args.verify_only:build()
    print(json.dumps(verify_assets()))
