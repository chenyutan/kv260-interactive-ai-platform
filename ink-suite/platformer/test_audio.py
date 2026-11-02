"""Pure mock lifecycle tests and offline packaging checks; no audio output."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from .audio import PlatformerSound, EVENTS, AUDIO_ACTIONS
from .build_audio import verify_assets


class MockDirector:
    def __init__(self, *args, **kwargs):
        self.args,self.kwargs=args,kwargs
        self.calls=[]
        self.queue=[]
        self.keys=set()
        self.played=[]
        self.desired='stopped'
        self.closed=False
        self.enabled=kwargs.get('enabled',True)

    def set_levels(self, **levels):self.calls.append(('levels',dict(levels)))
    def mute(self,value):self.calls.append(('mute',bool(value)))
    def resume(self):
        self.calls.append(('resume',))
        if not self.closed and self.enabled:self.desired='playing'
    def pause(self):
        self.calls.append(('pause',));self.desired='paused';self.queue.clear();self.keys.clear()
    def stop(self):
        self.calls.append(('stop',));self.desired='stopped';self.queue.clear();self.keys.clear()
    def play(self,name,key=None):
        self.calls.append(('play',name,key))
        if self.closed or not self.enabled or self.desired!='playing' or key in self.keys:return False
        self.keys.add(key);self.queue.append((name,key));self.played.append((name,key));return True
    def retry(self):
        self.calls.append(('retry',));self.queue.clear();self.keys.clear()
    def close(self):
        self.calls.append(('close',));self.closed=True;self.desired='closed';self.queue.clear()


class MockGame:
    def __init__(self,round_id=1,state='playing'):
        self.round_id,self.state=round_id,state
        self.audio_counts=dict.fromkeys(EVENTS,0)
    def event(self,name,count=1):self.audio_counts[name]+=count


class AudioLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.game=MockGame();self.director=MockDirector()
        self.sound=PlatformerSound(director=self.director)

    def activate(self):self.sound.resume(self.game)

    def test_constructs_existing_cube_director_for_platformer_with_own_assets(self):
        module=types.ModuleType('audio.game_output');module.CubeGameAudioDirector=MockDirector
        package=types.ModuleType('audio');package.__path__=[]
        with patch.dict(sys.modules,{'audio':package,'audio.game_output':module}):
            sound=PlatformerSound(enabled=False)
        self.assertEqual(sound.director.args,('platformer',))
        self.assertFalse(sound.director.kwargs['enabled'])
        self.assertEqual(sound.director.kwargs['asset_dir'],Path(__file__).resolve().parent/'audio_assets')
        self.assertEqual(sound.director.desired,'stopped')

    def test_all_ten_fresh_outcomes_play_once_with_round_and_count_key(self):
        self.activate()
        for name in EVENTS:self.game.event(name)
        self.sound.tick(self.game);self.sound.tick(self.game)
        self.assertEqual([name for name,key in self.director.played],['platformer_'+name for name in EVENTS])
        self.assertEqual(len(self.director.played),10)
        self.assertTrue(all(key==(1,name,1) for (_,key),name in zip(self.director.played,EVENTS)))

    def test_multiple_outcomes_coalesce_instead_of_replaying_backlog(self):
        self.activate();self.game.event('coin',7);self.sound.tick(self.game)
        self.assertEqual(self.director.played,[('platformer_coin',(1,'coin',7))])

    def test_embedded_pause_resume_flushes_and_baselines_hidden_outcomes(self):
        self.activate();self.game.event('jump');self.sound.tick(self.game)
        self.assertTrue(self.director.queue)
        self.sound.pause(self.game);self.assertFalse(self.director.queue)
        self.game.event('coin');self.game.event('hurt');self.sound.tick(self.game)
        self.sound.resume(self.game);self.sound.tick(self.game)
        self.assertEqual(len(self.director.played),1)
        self.game.event('jump');self.sound.tick(self.game)
        self.assertEqual(self.director.played[-1],('platformer_jump',(1,'jump',2)))

    def test_engine_paused_state_does_not_authorize_music(self):
        self.activate();self.game.state='paused';self.game.event('coin');self.sound.tick(self.game)
        self.assertEqual(self.director.desired,'paused');self.assertFalse(self.director.played)
        self.game.state='playing';self.sound.tick(self.game)
        self.assertEqual(self.director.desired,'playing');self.assertFalse(self.director.played)

    def test_explicit_restart_flushes_old_queue_preserving_pause_owner(self):
        self.activate();self.game.event('coin');self.sound.tick(self.game)
        self.game.round_id=2;self.game.audio_counts=dict.fromkeys(EVENTS,0)
        self.sound.restart(self.game);self.assertFalse(self.director.queue)
        self.sound.tick(self.game);self.assertEqual(len(self.director.played),1)
        self.game.event('jump');self.sound.tick(self.game)
        self.assertEqual(self.director.played[-1],('platformer_jump',(2,'jump',1)))
        self.sound.pause(self.game);self.game.round_id=3;self.sound.restart(self.game)
        self.sound.tick(self.game);self.assertEqual(self.director.desired,'stopped')
        self.assertTrue(self.sound.paused)

    def test_external_round_change_also_flushes_and_drops_first_observation(self):
        self.activate();self.game.event('jump');self.sound.tick(self.game)
        self.game.round_id=7;self.game.audio_counts=dict.fromkeys(EVENTS,4)
        self.sound.tick(self.game)
        self.assertFalse(self.director.queue);self.assertEqual(len(self.director.played),1)
        self.game.event('jump');self.sound.tick(self.game)
        self.assertEqual(self.director.played[-1],('platformer_jump',(7,'jump',5)))

    def test_counter_rewind_is_new_baseline_not_replay(self):
        self.activate();self.game.event('coin',8);self.sound.tick(self.game)
        self.game.audio_counts['coin']=2;self.game.event('jump');self.sound.tick(self.game)
        self.assertFalse(self.director.queue);self.assertEqual(len(self.director.played),1)
        self.game.event('coin');self.sound.tick(self.game)
        self.assertEqual(self.director.played[-1],('platformer_coin',(1,'coin',3)))

    def test_settlement_clears_gameplay_tails_and_emits_only_fresh_conclusion(self):
        for phase,cue in (('won','win'),('over','die')):
            with self.subTest(phase=phase):
                game=MockGame();director=MockDirector();sound=PlatformerSound(director=director)
                sound.resume(game);game.event('coin');sound.tick(game)
                game.state=phase;game.event(cue);sound.tick(game)
                self.assertEqual(director.queue,[('platformer_'+cue,(1,cue,1))])
                sound.tick(game);sound.pause(game);sound.resume(game);sound.tick(game)
                self.assertFalse(director.queue);self.assertEqual(len(director.played),2)

    def test_offline_disabled_never_starts_or_retries_output_or_effects(self):
        director=MockDirector(enabled=False);sound=PlatformerSound(False,director=director)
        game=MockGame();sound.resume(game);game.event('jump');sound.tick(game);sound.handle('audio_retry')
        self.assertFalse(any(call[0] in ('resume','retry','play') for call in director.calls))
        self.assertEqual(director.desired,'stopped')

    def test_controls_share_settings_without_queued_historical_replay(self):
        self.activate()
        actions=('audio_low','audio_normal','audio_mute','audio_unmute','audio_music_off',
                 'audio_music_on','audio_effects_off','audio_effects_on','audio_retry')
        self.assertEqual(set(actions),set(AUDIO_ACTIONS))
        for action in actions:self.assertTrue(self.sound.handle(action))
        self.assertFalse(self.sound.handle('not_audio'))
        self.assertEqual(self.sound.levels,dict(master=.55,music=.48,effects=.78))
        self.assertFalse(self.sound.muted)
        self.assertEqual(self.director.desired,'playing')
        self.game.event('coin');self.sound.tick(self.game)
        self.sound.handle('audio_mute');self.sound.tick(self.game)
        self.sound.handle('audio_unmute');self.sound.tick(self.game)
        self.assertEqual(len(self.director.played),1)
        self.assertEqual(len(self.director.played),1)

    def test_retry_during_embedded_pause_does_not_authorize_playback(self):
        self.activate();self.sound.pause(self.game)
        resumes=sum(call[0]=='resume' for call in self.director.calls)
        self.assertTrue(self.sound.handle('audio_retry'))
        self.sound.tick(self.game)
        self.assertEqual(self.director.desired,'paused')
        self.assertEqual(sum(call[0]=='resume' for call in self.director.calls),resumes)

    def test_close_is_idempotent_and_terminal_for_every_public_control(self):
        self.activate();self.sound.close();self.sound.close()
        before=list(self.director.calls)
        self.sound.resume(self.game);self.sound.pause(self.game);self.sound.restart(self.game)
        self.sound.tick(self.game);self.sound.baseline(self.game);self.assertFalse(self.sound.handle('audio_retry'))
        self.assertEqual(self.director.calls,before);self.assertIsNone(self.sound.previous)


class AudioAssetTests(unittest.TestCase):
    def test_manifest_all_hashes_pcm_contract_and_ten_effects(self):
        result=verify_assets()
        self.assertTrue(result['passed']);self.assertEqual(result['effects'],10)
        self.assertEqual(result['tracks'],1);self.assertFalse(result['physical_playback_verified'])


if __name__=='__main__':unittest.main()
