import unittest
from engine import Fight,Gesture
class Tests(unittest.TestCase):
    def game(self):
        f=Fight();f.start()
        for _ in range(31):f.advance(.1)
        return f
    def test_hit_once_and_cooldown(self):
        f=self.game();self.assertTrue(f.punch('left'));self.assertFalse(f.punch('right'))
        for _ in range(5):f.advance(.1)
        self.assertEqual(f.enemy_hp,92);self.assertEqual(f.hits,1)
    def test_telegraph_block(self):
        f=self.game();f.next_attack=0
        for _ in range(8):f.advance(.1,True)
        self.assertEqual(f.hp,100)
        for _ in range(3):f.advance(.1,True)
        self.assertEqual(f.hp,99);self.assertEqual(f.blocks,1)
    def test_pause_freezes(self):
        f=self.game();before=f.time;f.state='paused';f.advance(.1);self.assertEqual(f.time,before)
    def test_end_no_more_damage(self):
        f=self.game();f.enemy_hp=1;f.punch('left')
        for _ in range(5):f.advance(.1)
        self.assertEqual(f.result,'胜');self.assertFalse(f.punch('left'))
    def pose(self,t,reach):
        p=[[.5,.5,.9] for _ in range(17)]
        p[5]=[.4,.4,.9];p[6]=[.6,.4,.9]
        p[7]=[.4-reach*.1,.4,.9];p[9]=[.4-reach*.2,.4,.9]
        p[8]=[.61,.45,.9];p[10]=[.56,.38,.9]
        return dict(pid=1,sequence=round(t*100),timestamp_seconds=t,points17=p)
    def test_extend_retract(self):
        g=Gesture();self.assertEqual(g.consume(self.pose(1,.5)),[])
        self.assertEqual(g.consume(self.pose(1.1,1.1)),['left'])
        self.assertEqual(g.consume(self.pose(1.2,1.2)),[])
    def test_missing_never_punch(self):
        g=Gesture();g.consume(self.pose(1,.5));g.consume(None)
        self.assertEqual(g.consume(self.pose(1.1,1.1)),[])
    def test_reconnect_no_punch(self):
        g=Gesture();g.consume(self.pose(1,.5));s=self.pose(1.1,1.1);s['pid']=2
        self.assertEqual(g.consume(s),[])
    def test_static_guard(self):
        g=Gesture();g.consume(self.pose(1,.5));self.assertTrue(g.guard)
        self.assertEqual(g.consume(self.pose(1.1,.5)),[])
class CombatV2Tests(unittest.TestCase):
    game=Tests.game
    pose=Tests.pose
    def test_invalid_move_and_finite_time(self):
        f=self.game();before=(f.hp,f.enemy_hp,f.time)
        self.assertFalse(f.perform('unknown'))
        for dt in (-1,0,float('nan'),float('inf'),None):f.advance(dt)
        self.assertEqual((f.hp,f.enemy_hp,f.time),before)

    def test_every_move_has_single_damage_event_and_recovery(self):
        from engine import MOVES
        for move,spec in MOVES.items():
            with self.subTest(move=move):
                f=self.game();f.next_attack=100;f.energy=100
                self.assertTrue(f.perform(move));self.assertFalse(f.perform('left'))
                f.advance(spec['windup']-.02)
                self.assertEqual(f.hits,0)
                f.advance(.04)
                self.assertEqual(f.hits,1);self.assertEqual(f.enemy_hp,100-spec['damage'])
                self.assertFalse(f.perform('left'))
                f.advance(1.2)
                self.assertEqual(f.hits,1);self.assertTrue(f.perform('right'))

    def test_special_requires_and_spends_energy_once(self):
        f=self.game();f.next_attack=100
        self.assertFalse(f.perform('special'));self.assertIsNone(f.attack)
        f.energy=100;self.assertTrue(f.perform('special'));self.assertEqual(f.energy,0)
        self.assertFalse(f.perform('special'));f.advance(1.2)
        self.assertEqual(f.energy,0);self.assertFalse(f.perform('special'))

    def test_perfect_guard_counter_and_hold_not_repeated_parry(self):
        f=self.game();f.next_attack=0
        f.advance(.74,False);f.advance(.2,True)
        self.assertEqual(f.hp,100);self.assertEqual(f.perfect_blocks,1)
        self.assertEqual(f.energy,14);self.assertGreater(f.counter_until,f.time)
        self.assertTrue(f.perform('left'));f.advance(.2)
        self.assertEqual(f.enemy_hp,88)
        f.advance(6.,True)
        self.assertGreaterEqual(f.blocks,2);self.assertEqual(f.perfect_blocks,1)

    def test_heavy_interrupts_unlanded_enemy_attack(self):
        f=self.game();f.next_attack=0;f.advance(.1)
        self.assertIsNotNone(f.enemy_attack);self.assertTrue(f.perform('palm'))
        f.advance(.23);self.assertIsNone(f.enemy_attack);self.assertEqual(f.hp,100)
        self.assertGreater(f.next_attack,f.time)

    def test_enemy_cycles_three_readable_attacks(self):
        f=self.game();f.next_attack=0;seen=set()
        for _ in range(450):
            f.advance(.02,True)
            if f.enemy_attack:
                seen.add(f.enemy_attack['move'])
                self.assertGreaterEqual(f.enemy_attack['windup'],.8)
        self.assertEqual(seen,{'enemy_jab','enemy_heavy','enemy_sweep'})

    def test_fps_does_not_change_combat_result(self):
        states=[]
        for fps in (15,24,30,60):
            f=Fight();f.start()
            for _ in range(fps*23):f.advance(1/fps,True)
            states.append((f.hp,f.enemy_hp,f.blocks,f.energy,round(f.remaining,5)))
        self.assertTrue(all(s==states[0] for s in states),states)

    def test_dropped_frame_does_not_slow_clock_or_repeat_hit(self):
        f=self.game();f.next_attack=100;before=f.time;f.punch('left');f.advance(1.4)
        self.assertAlmostEqual(f.time-before,1.4);self.assertEqual(f.hits,1)
        self.assertEqual(f.enemy_hp,92);self.assertEqual(f.combo,0)

    def test_countdown_uses_remaining_elapsed_time(self):
        f=Fight();f.start();f.advance(3.5)
        self.assertEqual(f.state,'playing');self.assertAlmostEqual(f.time,.5)

    def test_victory_prevents_opponent_same_tick_retaliation(self):
        from engine import ENEMY_MOVES
        f=self.game();f.enemy_hp=1
        f.enemy_attack=dict(ENEMY_MOVES['enemy_jab'],move='enemy_jab',age=.759,hit=False)
        self.assertTrue(f.perform('left'));f.advance(.2)
        self.assertEqual(f.state,'over');self.assertEqual(f.hp,100)

    def pose_arms(self,t,left=None,right=None,pid=1):
        # arm=(elbow relative x/y, wrist relative x/y), shoulder-width units.
        p=self.pose(t,.5)['points17']
        for si,ei,wi,arm in ((5,7,9,left),(6,8,10,right)):
            if arm:
                sh=p[si];p[ei]=[sh[0]+arm[0]*.2,sh[1]+arm[1]*.2,.9]
                p[wi]=[sh[0]+arm[2]*.2,sh[1]+arm[3]*.2,.9]
        return dict(pid=pid,sequence=round(t*1000),timestamp_seconds=t,points17=p)

    def test_left_and_right_uppercuts(self):
        for hand in ('left','right'):
            g=Gesture();kwargs={hand:(-.25,.45,.12,.40)}
            g.consume(self.pose_arms(1,**kwargs))
            kwargs[hand]=(-.25,.35,.10,-.35)
            self.assertEqual(g.consume(self.pose_arms(1.1,**kwargs)),['uppercut_'+hand])
            for i in range(1,9):
                self.assertEqual(g.consume(self.pose_arms(1.1+i*.05,**kwargs)),[])

    def test_palm_same_direction_not_two_punches(self):
        g=Gesture();chamber=(.1,.45,.05,.35);extended=(.65,0.,1.15,-.02)
        g.consume(self.pose_arms(1,chamber,chamber))
        self.assertEqual(g.consume(self.pose_arms(1.1,extended,extended)),['palm'])
        self.assertEqual(g.consume(self.pose_arms(1.2,extended,extended)),[])

    def test_delayed_second_palm_combines_within_window(self):
        g=Gesture();chamber=(.1,.45,.05,.35);extended=(.65,0.,1.15,-.02)
        partial=(.45,.05,.85,.04)
        g.consume(self.pose_arms(1,chamber,chamber))
        self.assertEqual(g.consume(self.pose_arms(1.1,extended,partial)),[])
        self.assertEqual(g.consume(self.pose_arms(1.16,extended,extended)),['palm'])

    def test_opposing_extensions_are_not_palm(self):
        g=Gesture();chamber=(.1,.45,.05,.35)
        g.consume(self.pose_arms(1,chamber,chamber))
        cmds=g.consume(self.pose_arms(1.1,(-.65,0.,-1.15,-.02),(.65,0.,1.15,-.02)))
        self.assertNotIn('palm',cmds);self.assertIn(cmds[0],('left','right'))

    def test_special_raised_once_and_never_on_entry(self):
        g=Gesture();chamber=(.1,.45,.05,.35);overhead=(0.,-.65,.02,-1.2)
        self.assertEqual(g.consume(self.pose_arms(1,overhead,overhead)),[])
        self.assertEqual(g.consume(self.pose_arms(1.1,chamber,chamber)),[])
        self.assertEqual(g.consume(self.pose_arms(1.2,overhead,overhead)),['special'])
        for i in range(1,8):
            self.assertEqual(g.consume(self.pose_arms(1.2+i*.1,overhead,overhead)),[])
            self.assertFalse(g.guard)

    def test_slow_overhead_raise_is_not_palm_or_straight_punch(self):
        g=Gesture();out=[]
        for i in range(13):
            y=.35-i*.14
            arm=(0.,y*.5,.02,y)
            out.extend(g.consume(self.pose_arms(1+i*.05,arm,arm)))
        self.assertEqual(out,['special'])

    def test_guard_distinct_from_overhead_special(self):
        g=Gesture();arm=(.35,.1,.1,-.45)
        self.assertEqual(g.consume(self.pose_arms(1,arm,arm)),[]);self.assertTrue(g.guard)
        self.assertEqual(g.consume(self.pose_arms(1.1,arm,arm)),[]);self.assertTrue(g.guard)

    def test_partial_missing_resets_only_the_unobserved_hand(self):
        g=Gesture();g.consume(self.pose(1,.5));p=self.pose(1.05,.6)
        p['points17'][10][2]=.1
        self.assertEqual(g.consume(p),[]);self.assertTrue(g.valid);self.assertFalse(g.both_valid)
        self.assertNotIn('right',g.previous)
        self.assertEqual(g.consume(self.pose(1.1,1.1)),['left'])

    def test_stale_long_gap_reverse_time_and_duplicate_do_not_attack(self):
        for modification in ('stale','gap','reverse','pid'):
            g=Gesture();s=self.pose(1,.5);g.consume(s);self.assertEqual(g.consume(s),[])
            s=self.pose(1.1,1.1)
            if modification=='stale':s['stale']=True
            if modification=='gap':s['timestamp_seconds']=1.5
            if modification=='reverse':s['timestamp_seconds']=.9
            if modification=='pid':s['pid']=2
            self.assertEqual(g.consume(s),[])

    def test_whole_body_translation_does_not_attack(self):
        g=Gesture();g.consume(self.pose(1,.5));s=self.pose(1.1,.5)
        for p in s['points17']:p[0]+=.1;p[1]-=.1
        self.assertEqual(g.consume(s),[])

    def test_pending_punch_cancelled_when_retracted(self):
        g=Gesture();chamber=(.1,.45,.05,.35);extended=(.65,0.,1.15,-.02)
        partial=(.45,.05,.85,.04)
        g.consume(self.pose_arms(1,chamber,chamber))
        self.assertEqual(g.consume(self.pose_arms(1.1,extended,partial)),[])
        self.assertEqual(g.consume(self.pose_arms(1.2,chamber,chamber)),[])

    def test_static_guard_noise_never_punches(self):
        import random
        r=random.Random(27);g=Gesture()
        for i in range(120):
            s=self.pose_arms(1+i/30,(.35,.1,.1,-.45),(-.35,.1,-.1,-.45))
            for joint in s['points17'][5:11]:
                joint[0]+=r.uniform(-.002,.002);joint[1]+=r.uniform(-.002,.002)
            self.assertEqual(g.consume(s),[])

    def test_two_punches_require_real_retraction(self):
        g=Gesture();commands=[]
        for t,reach in ((1,.5),(1.1,1.1),(1.2,1.12),(1.3,.5),(1.4,1.1)):
            commands.extend(g.consume(self.pose(t,reach)))
        self.assertEqual(commands,['left','left'])

    def test_malformed_pose_does_not_raise(self):
        g=Gesture()
        for points in ({'oops':1},'bad',[None]*17,[[1,2]]*17):
            self.assertEqual(g.consume(dict(points17=points,pid=1,sequence=1,timestamp_seconds=1)),[])
            self.assertFalse(g.valid)

    def test_continuous_gestures_at_different_camera_rates(self):
        # Real video has intermediate samples; anchors must not walk along
        # with the moving wrist and erase the displacement at higher FPS.
        for fps in (15,24,30,60):
            for move in ('palm','uppercut_left','left'):
                with self.subTest(fps=fps,move=move):
                    g=Gesture();commands=[];steps=round(.4*fps)
                    for i in range(steps+1):
                        a=i/steps
                        if move=='palm':
                            arm=(.1+.55*a,.45*(1-a),.05+1.1*a,.35-.37*a)
                            s=self.pose_arms(1+i/fps,arm,arm)
                        elif move=='uppercut_left':
                            arm=(-.25,.45-.10*a,.12-.02*a,.40-.75*a)
                            s=self.pose_arms(1+i/fps,left=arm)
                        else:
                            s=self.pose(1+i/fps,.5+.7*a)
                        commands.extend(g.consume(s))
                    self.assertEqual(commands,[move])

if __name__=='__main__':
    unittest.main()
