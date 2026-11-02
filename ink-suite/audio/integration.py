"""Suite adapters: sound follows accepted game events, never drives gameplay."""
import json
import time
import threading
from pathlib import Path
from .runtime import AudioDirector
from .events import AudioEvents

AUDIO_ACTIONS=frozenset(('audio_low','audio_normal','audio_mute','audio_unmute',
    'audio_music_on','audio_music_off','audio_effects_on','audio_effects_off','audio_retry'))


def score_path(fallback):
    """Use the built 72-second arrangement with exactly the existing chart."""
    root=Path(__file__).resolve().parent
    try:
        data=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        path=(root/data['tracks']['bgm_taiko']['file']).resolve()
        if path.is_relative_to(root) and path.is_file():return path
    except (OSError,KeyError,ValueError,TypeError):pass
    return Path(fallback)


class ExperienceAudio:
    def __init__(self,mode,enabled=True,sink='autoaudiosink',director=None):
        self.mode=mode;self.enabled=bool(enabled);self.paused=True;self.app=None;self.serial=0
        self.director=director or AudioDirector(mode,enabled=self.enabled,sink=sink)
        self.events=AudioEvents(mode,self.director.play)
        self.event_lock=threading.RLock()
        self.levels=dict(master=.55,music=.48,effects=.78);self.muted=False
        self.pending_notice=None
        self.director.set_levels(**self.levels)

    def bind(self,app):
        self.app=app
        if self.mode=='fruit':
            original=app.item_fx.trigger
            def trigger(event):
                result=original(event)
                self.serial+=1
                self.events.fruit_cut(event,key=self.serial,enabled=not self.paused)
                return result
            app.item_fx.trigger=trigger
        self.baseline()

    def game(self):
        if self.app is None:return None
        return self.app.session.game if self.mode=='taiko' else getattr(self.app,'game',None)

    def baseline(self):
        game=self.game()
        if game is not None:self.observe(game,False)

    def observe(self,source,enabled):
        # Ink state arrives on its existing network worker, unlike game ticks.
        with self.event_lock:self.events.observe(source,enabled=enabled)

    def pause(self):
        self.paused=True;self.pending_notice=None;self.director.pause();self.baseline()

    def resume(self):
        self.baseline();self.paused=False
        if self.mode=='sword' and not self.sword_active():
            self.director.pause();return
        if self.mode=='taiko' and self.app is not None and getattr(self.app.audio,'mode',None)=='silent':
            self.director.pause();return
        self.director.resume()
        if self.pending_notice:
            name,key,at=self.pending_notice;self.pending_notice=None
            if time.monotonic()-at<5.:
                with self.event_lock:self.events.command(name,accepted=True,key=key,enabled=True)

    def restart(self):
        with self.event_lock:self.events.reset();self.baseline()
        self.director.stop()
        if not self.paused:self.resume()

    def tick(self):
        game=self.game()
        if game is None:return
        running=((self.app.session.running or game.finished) if self.mode=='taiko' else getattr(game,'state',None) in ('countdown','playing','finishing','over'))
        if self.mode=='sword':running=self.sword_active()
        if self.mode=='taiko' and getattr(self.app.audio,'mode',None)=='silent':running=False
        if self.paused or not running:
            self.director.pause();self.observe(game,False);return
        self.director.resume();self.observe(game,True)

    def sword_active(self):
        game=self.game()
        return game is not None and not game.paused and game.phase in ('countdown','playing','won','lost')

    def ink_state(self,state):
        self.observe(state,not self.paused)

    def command(self,name,accepted,key):
        # Only the just-confirmed save/clear may notify on immediate return;
        # gameplay sounds are never accumulated behind a pause menu.
        if self.paused and accepted and name in ('save','clear'):
            self.pending_notice=(name,key,time.monotonic());return
        with self.event_lock:self.events.command(name,accepted=accepted,key=key,enabled=not self.paused)

    def apply_music_level(self):
        if self.mode=='taiko' and self.app is not None:
            clock=self.app.audio
            setter=getattr(clock,'set_volume',None)
            if setter:setter(0. if self.muted else self.levels['master']*self.levels['music'])

    def handle(self,action):
        if action not in AUDIO_ACTIONS:return False
        if action=='audio_low':self.levels['master']=.28
        elif action=='audio_normal':self.levels['master']=.55
        elif action=='audio_mute':self.muted=True
        elif action=='audio_unmute':self.muted=False
        elif action=='audio_music_on':self.levels['music']=.48
        elif action=='audio_music_off':self.levels['music']=0.
        elif action=='audio_effects_on':self.levels['effects']=.78
        elif action=='audio_effects_off':self.levels['effects']=0.
        elif action=='audio_retry':self.director.retry()
        self.director.set_levels(**self.levels);self.director.mute(self.muted);self.apply_music_level()
        return True

    def close(self):
        self.paused=True;self.director.close()
