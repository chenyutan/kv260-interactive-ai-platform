"""Fresh take-off supersedes a preparatory slide, without airborne exploits."""
import unittest

from .engine import RunnerGame


class JumpSlideTests(unittest.TestCase):
    def game(self):
        game=RunnerGame(10);game.start();game._next_wave_distance=1e9
        game.drain_events()
        return game

    def test_grounded_takeoff_cancels_preparatory_slide(self):
        game=self.game();self.assertTrue(game.slide());game.update(.2)
        game.update(0,dict(valid=True,jump=True))
        self.assertTrue(game.jumping);self.assertFalse(game.sliding)
        self.assertEqual([e['kind'] for e in game.drain_events()],['slide','jump'])

    def test_roof_takeoff_keeps_its_real_height(self):
        game=self.game();game.player_y=game.ROOF_HEIGHT
        game.spawn('cart',1,-2.);game.update(.01)
        self.assertTrue(game._grounded)
        self.assertTrue(game.slide());self.assertTrue(game.jump())
        self.assertFalse(game.sliding);self.assertEqual(game._jump_origin,game.ROOF_HEIGHT)
        game.update(.05);self.assertGreater(game.player_y,game.ROOF_HEIGHT)

    def test_valid_coyote_takeoff_can_cancel_slide(self):
        game=self.game();game.slide();game._grounded=False
        game.elapsed=.05;game._last_grounded=.01
        self.assertTrue(game.jump());self.assertFalse(game.sliding)

    def test_fall_outside_coyote_buffers_without_cancelling_slide(self):
        game=self.game();game.slide();before=game.slide_remaining
        game._grounded=False;game.elapsed=1.;game._last_grounded=.1
        self.assertFalse(game.jump());self.assertEqual(game.slide_remaining,before)
        self.assertEqual(game._jump_buffer,game.JUMP_BUFFER_SECONDS)
        self.assertFalse(game.jumping)

    def test_pause_over_title_and_jetpack_still_reject_without_cancelling(self):
        for state in ('paused','over','title','running'):
            with self.subTest(state=state):
                game=self.game();game.slide();before=game.slide_remaining;game.state=state
                if state=='running':game.jetpack_time=1.
                self.assertFalse(game.jump());self.assertEqual(game.slide_remaining,before)
                self.assertFalse(game.jumping);self.assertEqual(game._jump_buffer,0)

    def test_airborne_edge_is_buffered_without_a_midair_double_jump(self):
        game=self.game();game.jump();game.update(.2);remaining=game.jump_remaining
        self.assertFalse(game.jump());self.assertEqual(game.jump_remaining,remaining)
        self.assertEqual(game._jump_buffer,game.JUMP_BUFFER_SECONDS)
        game.update(.2)
        self.assertEqual(game.counts['jump'],1)

    def test_late_airborne_edge_keeps_the_existing_landing_buffer(self):
        game=self.game();game.jump();game.update(.25);game.update(.25);game.update(.25)
        self.assertFalse(game.jump())
        for _ in range(20):game.update(.01)
        self.assertEqual(game.counts['jump'],2)

    def landing_during_slide(self,duration):
        game=self.game();game.elapsed=1.;game._last_grounded=.1
        game._grounded=False;game.player_y=.015;game._fall_velocity=-1.
        game.slide_remaining=duration
        entries=[];start=game._start_jump
        def takeoff():
            # Observe the actual buffered call, not only frame-end flags.
            self.assertFalse(game.sliding)
            entries.append(game.elapsed)
            return start()
        game._start_jump=takeoff
        self.assertFalse(game.jump());self.assertEqual(game.slide_remaining,duration)
        game.update(.02)
        self.assertTrue(game._grounded);self.assertTrue(game.sliding)
        self.assertFalse(game.jumping)
        for _ in range(100):
            game.update(.01)
            self.assertFalse(game.jumping and game.sliding)
        return game,entries

    def test_landing_buffer_waits_until_short_slide_has_ended(self):
        game,entries=self.landing_during_slide(.04)
        self.assertEqual(game.counts.get('jump'),1)
        self.assertEqual(len(entries),1);self.assertGreaterEqual(entries[0],1.04-1e-9)

    def test_landing_during_long_slide_expires_buffer_without_stale_jump(self):
        game,entries=self.landing_during_slide(.75)
        self.assertEqual(game.counts.get('jump',0),0)
        self.assertFalse(game.sliding);self.assertEqual(game._jump_buffer,0)
        self.assertEqual(entries,[])

    def test_clear_crouch_then_takeoff_executes_both_actions_once(self):
        from .controls import PoseControls
        from .test_core import pose
        tracker=PoseControls();game=self.game();stamp=10.;sequence=0
        # A confirmed deep crouch must still slide, and a subsequent fresh
        # take-off must cancel that real slide. A brief preparatory dip is
        # covered separately and must no longer produce a slide.
        for y in [0.]*8+[.04,.08,.12]+[.16]*5+[.12,.08,.03,0.,-.02,-.05,-.065]+[-.065]*5:
            stamp+=.05;sequence+=1
            control=tracker.update(pose(stamp,sequence,body_down=y),stamp,phase='running')
            game.update(.05,control)
        self.assertEqual(game.counts.get('slide'),1)
        self.assertEqual(game.counts.get('jump'),1)


if __name__=='__main__':unittest.main()
