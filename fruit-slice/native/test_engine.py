import unittest
from engine import Game,PoseInput

class Tests(unittest.TestCase):
    def test_new_fruits_cut_and_score_once(self):
        for fruit in ('apple','strawberry','dragonfruit'):
            g=self.game();g.spawn('normal',fruit=fruit,x=500,y=350,vx=0,vy=0)
            g.input('left',400,350);g.advance(.05);g.input('left',600,350)
            self.assertEqual(g.score,10);self.assertEqual(len(g.events),1)
            self.assertEqual(g.events[0]['fruit'],fruit)
    def test_all_varieties_spawn(self):
        from engine import ORDINARY_FRUITS
        g=self.game();seen=set()
        for _ in range(300):
            seen.add(g.spawn('normal')['fruit']);g.objects.clear()
        self.assertEqual(seen,set(ORDINARY_FRUITS))
    def game(self):
        g=Game();g.start(1);g.advance(3.1);g.spawn_in=100;return g
    def test_sweep_once(self):
        g=self.game();g.spawn('normal',x=500,y=350,vx=0,vy=0)
        g.input('left',400,350);g.input('right',400,350);g.advance(.05)
        g.input('left',600,350);g.input('right',600,350);self.assertEqual(g.score,10)
    def test_stationary(self):
        g=self.game();g.spawn('normal',x=500,y=350,vx=0,vy=0)
        g.input('left',500,350);g.advance(.05);g.input('left',500,350);self.assertEqual(g.score,0)
    def test_gap(self):
        g=self.game();g.spawn('normal',x=500,y=350,vx=0,vy=0)
        g.input('left',400,350);g.advance(.2);g.input('left',600,350);self.assertEqual(g.score,0)
    def test_effects(self):
        g=self.game();g.cut(g.spawn('double'));g.cut(g.spawn('normal'));self.assertEqual(g.score,30)
        g.cut(g.spawn('bomb'));self.assertEqual(g.score,0)
        g.cut(g.spawn('ice'));until=g.freeze_until;g.advance(1);self.assertEqual(g.elapsed,1);self.assertEqual(until,5)
    def test_pause(self):
        g=self.game();g.pause();g.advance(10);self.assertEqual(g.elapsed,0);g.resume();g.advance(1);self.assertEqual(g.elapsed,1)
    def test_timeout(self):
        g=self.game();g.advance(60);self.assertEqual(g.state,'over');g.input('left',500,300);self.assertFalse(g.paths)
    def test_pose(self):
        g=self.game();p=PoseInput(g);points=[[.5,.5,.9] for _ in range(17)]
        s=dict(pid=1,sequence=1,timestamp_seconds=1,points17=points)
        self.assertEqual(len(p.consume(s)),2);self.assertFalse(p.consume(s));p.consume(None);self.assertFalse(g.paths)
    def test_bound(self):
        g=self.game()
        for _ in range(30):g.spawn()
        self.assertEqual(len(g.objects),16)
    def test_moving_fruit(self):
        g=self.game();g.spawn('normal',x=500,y=200,vx=0,vy=3000)
        g.input('left',400,350);g.advance(.1);g.input('left',600,350);self.assertEqual(g.score,10)
    def test_jump(self):
        g=self.game();g.spawn('normal',x=500,y=350,vx=0,vy=0)
        g.input('left',0,350);g.advance(.05);g.input('left',1000,350);self.assertEqual(g.score,0)
    def test_combo_bonus_and_cap(self):
        g=self.game()
        for _ in range(8):g.cut(g.spawn('normal'))
        self.assertEqual(g.score,170)
        self.assertEqual(g.best,8)
    def test_classic_scoring(self):
        g=self.game();g.mode='classic'
        for _ in range(8):g.cut(g.spawn('normal'))
        self.assertEqual(g.score,80);self.assertFalse(g.furious);self.assertEqual(g.charge,0)
    def test_combo_gap(self):
        g=self.game()
        for _ in range(2):g.cut(g.spawn('normal'))
        g.advance(.56);g.cut(g.spawn('normal'))
        self.assertEqual(g.score,30);self.assertEqual(g.combo,1)
    def test_fury_threshold_and_no_refresh(self):
        g=self.game()
        for _ in range(7):g.cut(g.spawn('normal'))
        self.assertTrue(g.furious);self.assertEqual(g.fury_until,6)
        g.advance(1);g.cut(g.spawn('normal'));self.assertEqual(g.fury_until,6)
        g.pause();g.advance(5);self.assertEqual(g.elapsed,1)
    def test_existing_bomb_interrupts_fury(self):
        g=self.game();g.fury_until=6;g.charge=8;g.score=90
        g.cut(g.spawn('bomb'));self.assertFalse(g.furious);self.assertEqual(g.charge,0)
        self.assertEqual(g.score,60);self.assertEqual(g.bombs_hit,1)
    def test_fury_waves_no_new_bombs(self):
        g=self.game();g.fury_until=6
        for _ in range(100):
            g.objects.clear();g.wave()
            self.assertTrue(all(o['kind']!='bomb' for o in g.objects))
    def test_stage_and_density_bound(self):
        g=self.game()
        self.assertEqual(g.stage,1);g.advance(20);self.assertEqual(g.stage,2)
        g.advance(20);self.assertEqual(g.stage,3)
        for _ in range(100):g.wave()
        self.assertLessEqual(len(g.objects),8)
    def test_missed_fruit_not_bomb(self):
        g=self.game();g.spawn('normal',y=900);g.spawn('bomb',y=900)
        g.advance(.001);self.assertEqual(g.missed,1);self.assertEqual(g.score,0)
    def test_restart_clears_arcade(self):
        g=self.game();g.fury_until=99;g.charge=9;g.bombs_hit=2;g.fruits_cut=15;g.start()
        self.assertEqual((g.fury_until,g.charge,g.bombs_hit,g.fruits_cut),(0,0,0,0))

if __name__=='__main__':unittest.main()
