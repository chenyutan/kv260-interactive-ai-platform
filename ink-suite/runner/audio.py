"""Accepted runner events feed the shared worker-driven Cube output."""
from pathlib import Path

class RunnerSound:
    def __init__(self,enabled=True,director=None):
        if director is None:
            from audio.game_output import CubeGameAudioDirector
            director=CubeGameAudioDirector('runner',enabled=enabled,
                asset_dir=Path(__file__).resolve().parent/'audio_assets')
        self.director=director;self.enabled=enabled;self.playing=False;self.closed=False
        self.levels=dict(master=.55,music=.48,effects=.78);self.muted=False
        director.set_levels(**self.levels)
    def set_playing(self,playing):
        playing=bool(playing and self.enabled and not self.closed)
        # resume() is a cached, asynchronous request and also owns the shared
        # director's >=3s reconnect retry. Keep authorizing playback while the
        # run is active, even if the adapter already marked itself playing.
        if not playing and not self.playing:return
        self.playing=playing
        if playing:self.director.resume()
        else:self.director.pause()
    def events(self,events):
        if not self.playing:return
        names={'jump':'jump','slide':'slide','lane':'lane','hit':'hit','over':'over','shield_break':'hit'}
        for e in events:
            kind=e.get('kind');name=names.get(kind)
            if kind=='collect':name='coin' if e.get('item_kind')=='coin' else 'power'
            if name:self.director.play('sfx_runner_'+name)
    def restart(self):
        self.director.stop();self.playing=False
    def handle(self,action):
        if action=='audio_low':self.levels['master']=.28
        elif action=='audio_normal':self.levels['master']=.55
        elif action=='audio_mute':self.muted=True
        elif action=='audio_unmute':self.muted=False
        elif action=='audio_music_on':self.levels['music']=.48
        elif action=='audio_music_off':self.levels['music']=0.
        elif action=='audio_effects_on':self.levels['effects']=.78
        elif action=='audio_effects_off':self.levels['effects']=0.
        elif action=='audio_retry':self.director.retry()
        else:return False
        self.director.set_levels(**self.levels);self.director.mute(self.muted);return True
    def close(self):
        if self.closed:return
        self.set_playing(False);self.closed=True;self.director.close()
