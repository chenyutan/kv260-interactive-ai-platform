"""Audio follows game outcomes and menus; no device, camera or network access."""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from audio.integration import ExperienceAudio, AUDIO_ACTIONS, score_path
from action_catalog import page, actions
from action_dispatch import SUPPORTED_ACTIONS
from test_action_dispatch import load_class
import test_action_host
from taiko.engine import Game
from sword.engine import Game as SwordGame

ROOT=Path(__file__).resolve().parent

class Director:
    def __init__(self):
        self.played=[];self.phase='stopped';self.levels={};self.muted=False
    def set_levels(self,**levels):self.levels.update(levels)
    def mute(self,value):self.muted=value
    def resume(self):self.phase='playing'
    def pause(self):self.phase='paused';self.played.clear()
    def stop(self):self.phase='stopped';self.played.clear()
    def retry(self):pass
    def close(self):self.phase='closed';self.played.clear()
    def status(self):return dict(state=self.phase)
    def play(self,name,key=None):
        if self.phase!='playing':return False
        self.played.append((name,key));return True


class IntegrationTests(unittest.TestCase):
    def audio(self,mode,app=None):
        director=Director();a=ExperienceAudio(mode,director=director)
        if app is not None:a.bind(app)
        return a,director

    def test_fruit_visual_callback_preserved_and_old_events_discarded(self):
        visual=[];app=NS(game=NS(elapsed=0,missed=0,state='playing'),item_fx=NS(trigger=visual.append))
        a,d=self.audio('fruit',app);a.resume()
        event=dict(kind='ice',combo=0)
        app.item_fx.trigger(event)
        self.assertIs(visual[0],event)
        self.assertEqual([n for n,_ in d.played],['fruit_freeze'])
        a.pause();app.item_fx.trigger(dict(kind='bomb'));a.resume();a.tick()
        self.assertEqual(d.played,[])
        self.assertEqual(len(visual),2)

    def test_fight_wins_only_after_finale_and_pause_has_no_backlog(self):
        game=NS(time=0,state='playing',move_counts={},effects=[],result='')
        a,d=self.audio('fight',NS(game=game));a.resume()
        game.move_counts['kick_left']=1;game.effects=[dict(kind='hit')];a.tick()
        self.assertEqual({n for n,_ in d.played},{'fight_kick','fight_hit'})
        a.pause();game.move_counts['kick_left']=2;a.resume();a.tick()
        self.assertFalse(d.played)
        game.state='finishing';game.result='胜';a.tick();self.assertFalse(d.played)
        game.state='over';a.tick();a.tick()
        self.assertEqual([n for n,_ in d.played],['fight_win'])

    def test_ink_save_notice_only_current_success_and_no_stale_replay(self):
        a,d=self.audio('ink');a.command('save',False,'bad');a.resume()
        self.assertFalse(d.played)
        a.pause();a.command('save',True,'saved');a.resume()
        self.assertEqual([n for n,_ in d.played],['ink_save'])
        a.pause();a.command('clear',True,'clear');a.pause();a.resume()
        self.assertFalse(d.played)

    def test_ink_count_does_not_replay_existing_or_reconnected_art(self):
        a,d=self.audio('ink');a.resume()
        a.ink_state(dict(pid=1,event_count=12,art_revision=0))
        self.assertFalse(d.played)
        a.ink_state(dict(pid=1,event_count=13,art_revision=1))
        self.assertEqual([n for n,_ in d.played],['ink_stroke'])
        d.played.clear();a.ink_state(dict(pid=2,event_count=18,art_revision=2))
        self.assertFalse(d.played)

    def test_taiko_volume_does_not_advance_clock_or_change_judgements(self):
        game=Game(notes=[dict(id=1,time=1.,lane='L')],duration=2.)
        clock=NS(mode='gstreamer-fakesink',set_volume=Mock(),position=Mock(return_value=0))
        a,d=self.audio('taiko',NS(session=NS(game=game,running=True),audio=clock))
        before=game.snapshot();a.handle('audio_mute');a.handle('audio_music_off')
        clock.set_volume.assert_called_with(0.)
        a.handle('audio_unmute');clock.set_volume.assert_called_with(0.)
        self.assertEqual(game.snapshot(),before);clock.position.assert_not_called()
        a.handle('audio_music_on');clock.set_volume.assert_called_with(.55*.48)

    def test_taiko_finish_emits_once_after_clock_session_stops(self):
        game=Game(notes=[],duration=1.);game.pause(False)
        session=NS(game=game,running=True)
        a,d=self.audio('taiko',NS(session=session,audio=NS(mode='gstreamer-fakesink')));a.resume()
        game.advance(1.);game.pause();session.running=False
        a.tick();a.tick()
        self.assertEqual([n for n,_ in d.played],['taiko_finish'])

    def test_taiko_explicit_silent_practice_suppresses_effects(self):
        game=Game(notes=[],duration=1.);game.pause(False)
        a,d=self.audio('taiko',NS(session=NS(game=game,running=True),audio=NS(mode='silent')))
        a.resume();a.tick();self.assertEqual(d.phase,'paused')
        game.advance(1.);a.tick();self.assertFalse(d.played)

    def test_disabled_preview_never_creates_output_worker(self):
        a=ExperienceAudio('fight',enabled=False)
        a.bind(NS(game=NS(state='playing',time=0,move_counts={},effects=[])))
        a.resume();a.tick();a.handle('audio_retry')
        self.assertFalse(a.director.status()['worker_alive']);a.close()

    def test_audio_menu_navigation_and_fixed_child_commands(self):
        for mode in ('ink','fruit','fight','taiko','sword'):
            self.assertTrue(AUDIO_ACTIONS<=SUPPORTED_ACTIONS[mode])
            self.assertTrue(AUDIO_ACTIONS<=actions(mode))
            for name in ('audio','audio_options'):
                self.assertLessEqual(len(page(mode,name)['items']),6)
        self.assertNotIn('koi',SUPPORTED_ACTIONS)

    def test_sword_ready_tracking_loss_restart_and_resume(self):
        game=SwordGame();a,d=self.audio('sword',NS(game=game))
        a.resume();a.tick();self.assertEqual(d.phase,'paused')
        game.start(0);a.restart();a.tick();self.assertEqual(d.phase,'playing')
        game.update(.05,dict(x=.5,y=.78,valid=True,status='tracking',special=False));a.tick()
        self.assertEqual([name for name,_ in d.played],['sword_shot'])
        game.pause(True);a.tick();self.assertEqual(d.phase,'paused');self.assertFalse(d.played)
        game.pause(False);a.tick();self.assertFalse(d.played)
        a.pause();game.start(0);a.restart();self.assertNotEqual(d.phase,'playing')
        a.resume();a.tick();self.assertFalse(d.played)

    def test_sword_menu_sound_action_does_not_resume_physics(self):
        cls,_=load_class(ROOT/'sword/main.py','SwordPanel');panel=cls.__new__(cls)
        panel.game=SwordGame();panel.game.start(0);panel.game.pause(True)
        panel.sound,_=self.audio('sword',panel);done=Mock();before=panel.game.snapshot()
        panel.execute_action('audio_music_off',done)
        self.assertEqual(panel.game.snapshot(),before);self.assertTrue(done.call_args.args[0])
        self.assertEqual(panel.sound.levels['music'],0.)

    def test_host_routes_audio_pages_locally_before_sending_selection(self):
        h=test_action_host.ActionHostTests();h.setUp();s=h.s
        s.choose_action('system');s.choose_action('audio')
        self.assertEqual(s.action_page,'audio');self.assertFalse(h.action_messages())
        s.choose_action('audio_options');self.assertEqual(s.action_page,'audio_options')
        s.choose_action('audio_music_off')
        self.assertEqual(h.action_messages()[0]['action'],'audio_music_off')

    def test_audio_clock_retry_error_is_not_reported_as_success(self):
        cls,_=load_class(ROOT/'taiko/main.py','TaikoPanel');p=cls.__new__(cls)
        p.sound=Mock();p.audio=NS(error='no sink');p.session=NS(error=None)
        p.retry_audio=Mock();done=Mock();p.execute_action('audio_retry',done)
        done.assert_called_once_with(False,'no sink')

    def test_built_chart_audio_is_used(self):
        self.assertEqual(score_path('/missing/fallback.wav'),ROOT/'audio/assets/bgm_taiko.wav')

if __name__=='__main__':unittest.main()
