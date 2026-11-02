"""Accepted engine outcomes feed the live Cube output; audio never drives physics."""
from pathlib import Path

AUDIO_ACTIONS = frozenset(('audio_low','audio_normal','audio_mute','audio_unmute',
    'audio_music_on','audio_music_off','audio_effects_on','audio_effects_off','audio_retry'))

EVENTS = ('jump','coin','power','stomp','hurt','die','pipe','checkpoint','clear','win')

class PlatformerSound:
    def __init__(self, enabled=True, *, director=None):
        self.enabled = bool(enabled)
        if director is None:
            # Importing the pure adapter in a mock test never discovers audio
            # hardware. Normal scenes still use the existing verified Cube route.
            from audio.game_output import CubeGameAudioDirector
            director = CubeGameAudioDirector('platformer', enabled=self.enabled,
                asset_dir=Path(__file__).resolve().parent/'audio_assets')
        self.director = director
        self.levels = dict(master=.55, music=.48, effects=.78)
        self.muted = False
        self.paused = True
        self.previous = None
        self.closed = False
        self.director.set_levels(**self.levels)

    def baseline(self, game):
        if self.closed:return
        self.previous = (game.round_id, dict(game.audio_counts), getattr(game,'state','playing'))

    def pause(self, game):
        if self.closed:return
        self.paused = True
        self.director.pause()
        self.baseline(game)

    def resume(self, game):
        if self.closed:return
        self.baseline(game)
        self.paused = False
        if self.enabled:
            if getattr(game,'state',None)=='paused':self.director.pause()
            else:self.director.resume()

    def restart(self, game):
        if self.closed:return
        self.director.stop()
        self.baseline(game)

    def tick(self, game):
        if self.closed:return
        counts = dict(game.audio_counts)
        old = self.previous
        phase = getattr(game,'state','playing')
        self.previous = (game.round_id, counts, phase)
        if self.paused or not self.enabled:
            return
        if phase=='paused':
            self.director.pause()
            return
        if old is not None and old[0]!=game.round_id:
            # Even an external engine restart drops the old mixer/event queue.
            self.director.stop()
            self.director.resume()
            return
        if old is not None and any(counts.get(name,0)<old[1].get(name,0) for name in EVENTS):
            # Counter rewinds are a new baseline, never a historical replay.
            self.director.pause()
            self.director.resume()
            return
        if old is not None and phase in ('won','over') and old[2]!=phase:
            # Settlement cues follow the fresh outcome; queued jump/coin tails
            # from gameplay must not accompany the conclusion plaque.
            self.director.pause()
        self.director.resume()
        if old is None or old[0] != game.round_id:
            return
        for name in EVENTS:
            if counts.get(name,0) > old[1].get(name,0):
                self.director.play('platformer_'+name,
                    key=(game.round_id,name,counts[name]))

    def handle(self, action):
        if self.closed or action not in AUDIO_ACTIONS:
            return False
        if action=='audio_low': self.levels['master']=.28
        elif action=='audio_normal': self.levels['master']=.55
        elif action=='audio_mute': self.muted=True
        elif action=='audio_unmute': self.muted=False
        elif action=='audio_music_on': self.levels['music']=.48
        elif action=='audio_music_off': self.levels['music']=0.
        elif action=='audio_effects_on': self.levels['effects']=.78
        elif action=='audio_effects_off': self.levels['effects']=0.
        elif action=='audio_retry' and self.enabled: self.director.retry()
        self.director.set_levels(**self.levels)
        self.director.mute(self.muted)
        return True

    def close(self):
        if self.closed:return
        self.closed=True
        self.paused=True
        self.previous=None
        self.director.close()
