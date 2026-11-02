"""CPU-only regression checks; no camera, sound device or GPU context."""
import math
import unittest
from kart.engine import KartGame,TRACK,clamp,LAPS,drift_tier
from kart.controls import PoseControls,gamepad_action

def autopilot(game):
    r=game.player;curve=TRACK.curvature(r.s)
    return dict(valid=True,steer=clamp(-r.x*.3+curve*r.speed*r.speed*.078/(4.6+r.speed*.075),-1,1),
                item=bool(r.item),drift=abs(curve)>.014 and int(game.elapsed*1.3)%5<4)

def packet(seq,t,left_up=False,right_up=False,lean=0.):
    points=[[.5,.5,.8] for _ in range(17)]
    points[5]=[.40+lean,.38,.7];points[6]=[.60+lean,.38,.7]
    points[11]=[.44,.68,.7];points[12]=[.56,.68,.7]
    points[9]=[.36+lean,.35 if left_up else .57,.7]
    points[10]=[.64+lean,.35 if right_up else .57,.7]
    return dict(pid=1,sequence=seq,timestamp_seconds=t,latency_ms=12,available=True,points17=points)

class EngineTests(unittest.TestCase):
    def test_closed_continuous_course(self):
        self.assertGreater(TRACK.length,700)
        self.assertLess(math.dist(TRACK.sample(0)[0],TRACK.sample(TRACK.length)[0]),1e-8)
        self.assertLess(math.dist(TRACK.sample(-.01)[0],TRACK.sample(.01)[0]),.03)
    def test_bridge_height_and_grade_are_continuous(self):
        heights=[TRACK.sample(TRACK.length*i/1000)[0][1] for i in range(1001)]
        self.assertGreater(max(heights),3.99);self.assertEqual(heights[0],0);self.assertEqual(heights[-1],0)
        self.assertLess(max(abs(b-a)/(TRACK.length/1000) for a,b in zip(heights,heights[1:])),.10)
        self.assertGreater(TRACK.length,790);self.assertLess(TRACK.length,795)
    def test_multicar_contacts_do_not_reenter_during_full_race(self):
        g=KartGame();g.start()
        for _ in range(6000):
            g.update(1/60,autopilot(g))
            for i,a in enumerate(g.karts):
                for b in g.karts[i+1:]:
                    if a.finish_time or b.finish_time:continue
                    self.assertFalse(abs(a.s-b.s)<3.09 and abs(a.x-b.x)<2.09)
            if g.finished:break
        self.assertTrue(g.finished);self.assertEqual(len(g.lap_times),3)
    def test_three_lap_complete_race_and_report(self):
        g=KartGame();g.start()
        for _ in range(30*180):
            g.update(1/30,autopilot(g))
            if g.finished:break
        self.assertTrue(g.finished,g.report());self.assertEqual(len(g.lap_times),3)
        self.assertGreater(g.elapsed,60);self.assertLess(g.elapsed,160)
        self.assertEqual(g.report()['result'],'finished');self.assertEqual(g.report()['progress'],1)
        self.assertEqual(g.report()['total_laps'],3);self.assertGreater(g.score,0)
        self.assertTrue(all(abs(r.x)<=7.00001 for r in g.karts))
    def test_pause_freezes_everything_and_resume_preserves_countdown(self):
        g=KartGame();g.start();g.update(.1,{});g.pause();before=g.snapshot()
        g.update(5,{'item':True});self.assertEqual(g.snapshot(),before)
        g.resume();self.assertEqual(g.state,'countdown');g.update(.1,{})
        self.assertLess(g.countdown,before['countdown'])
    def test_shield_blocks_one_projectile_and_boost_is_visible(self):
        g=KartGame();r=g.player;r.item='shield';g.use_item(r)
        g.hit(r,1);self.assertEqual(r.slow,0);self.assertEqual(r.shield,0)
        r.hit_cooldown=0;g.hit(r,1);self.assertGreater(r.slow,0)
        r.item='boost';g.use_item(r);self.assertGreater(r.boost,0)
    def test_three_drift_tiers_release_distinct_boost_and_score(self):
        durations=[];scores=[]
        for charge in (.70,1.30,2.05):
            g=KartGame();g.state='running';r=g.player;r.speed=27.;r.drift=1.;r.drift_charge=charge
            g.update(1/60,{'steer':0,'drift':False})
            durations.append(r.boost);scores.append(g.score)
            event=next(e for e in g.drain_events() if e['kind']=='drift_boost' and e['racer']==0)
            self.assertEqual(event['tier'],drift_tier(charge));self.assertEqual(r.drift_charge,0)
        self.assertEqual(scores,[30,60,90]);self.assertTrue(durations[0]<durations[1]<durations[2])
    def test_item_rows_offer_three_separate_lanes(self):
        g=KartGame()
        for start in range(0,18,3):
            row=g.pickups[start:start+3]
            self.assertEqual(len({p['s'] for p in row}),1)
            self.assertEqual({p['kind'] for p in row},{'boost','shield','pulse'})
            self.assertEqual(len({p['x'] for p in row}),3)
    def test_visible_projectile_hits_ai(self):
        g=KartGame();g.state='running';g.player.item='pulse';g.karts[1].s=18;g.karts[1].x=0
        for r in g.karts[2:]:r.s=-20-r.id*5
        g.use_item(g.player);self.assertEqual(len(g.projectiles),1)
        for _ in range(60):g.update(1/60,{'steer':0})
        self.assertTrue(any(e['kind']=='hit' and e.get('racer')==1 for e in g.drain_events()))
    def test_finish_order_is_preserved_after_all_reach_finish(self):
        g=KartGame();g.state='running';g.elapsed=100
        for r in g.karts[1:]:r.s=TRACK.length*3;r.finish_time=90+r.id
        g.player.s=TRACK.length*3-.01;g.player.speed=20
        g.update(.02,{'steer':0});self.assertEqual(g.rank,6)
    def test_finished_ai_drive_clear_without_changing_classification(self):
        g=KartGame();g.state='running';g.elapsed=100
        ai=g.karts[1];ai.s=TRACK.length*3;ai.finish_time=91.;ai.speed=30.
        old=ai.s;g.update(.10,{'steer':0})
        self.assertGreater(ai.s,old+2);self.assertEqual(ai.finish_time,91.)

class ControlsTests(unittest.TestCase):
    def test_gamepad_edges_and_held_drift(self):
        out=gamepad_action(dict(valid=True,pressed={'A','X'},buttons={'B'},lx=.6,lt=.8))
        self.assertTrue(out['confirm']);self.assertTrue(out['item']);self.assertTrue(out['drift'])
        self.assertTrue(out['brake']);self.assertEqual(out['steer'],.6)
    def test_low_nonzero_confidence_is_accepted_no_calibration(self):
        c=PoseControls();p=packet(1,10.)
        for point in p['points17']:point[2]=.04
        out=c.update(p,10.);self.assertTrue(out['valid'])
    def test_stale_pose_never_acts_and_recovery_does_not_require_centre(self):
        c=PoseControls();self.assertTrue(c.update(packet(1,10,lean=.03),10)['valid'])
        out=c.update(packet(1,10,right_up=True),10.6)
        self.assertFalse(out['valid']);self.assertFalse(out['item'])
        self.assertTrue(c.update(packet(2,10.65,lean=.03),10.65)['valid'])
    def test_item_is_single_edge_and_duplicate_keeps_drift(self):
        c=PoseControls();c.update(packet(1,10),10)
        self.assertTrue(c.update(packet(2,10.05,right_up=True),10.05)['item'])
        self.assertFalse(c.update(packet(2,10.05,right_up=True),10.08)['item'])
        c.update(packet(3,10.10,left_up=True),10.10)
        self.assertTrue(c.update(packet(3,10.10,left_up=True),10.15)['drift'])
    def test_wide_shoulders_and_hands_at_shoulder_line_confirm(self):
        c=PoseControls();confirmed=False
        for i in range(10):confirmed |= c.update(packet(i,10+i*.05,True,True),10+i*.05)['confirm']
        self.assertTrue(confirmed)

if __name__=='__main__':unittest.main()
