import math
import json
import random
import unittest
from circle_detector import CircleDetector

def pose(angle,hand='right',rx=.13,ry=.22):
    p=[[.5,.6,.95] for _ in range(17)]
    p[5]=[.44,.38,.95];p[6]=[.56,.38,.95]
    p[9]=[.43,.72,.95];p[10]=[.57,.72,.95]
    sign=1 if hand=='right' else -1
    p[10 if hand=='right' else 9]=[.5+sign*.18+rx*math.cos(angle),.46+ry*math.sin(angle),.95]
    return p

class Circles(unittest.TestCase):
    def gameplay_then_circle(self,seed,turns=2,direction=1):
        # Continuous horizontal gameplay leaves a long non-circular prefix.
        # Small image-coordinate jitter models a quantized wrist without
        # inventing missing observations or relaxing tracking confidence.
        rng=random.Random(seed);fps=24
        for i in range(7*fps):
            t=i/fps;p=pose(0)
            p[10]=[.68+.12*math.cos(t*math.tau),.46+rng.gauss(0,.003),.9]
            yield t,p
        for i in range(turns*2*fps+1):
            p=pose(direction*math.tau*i/(2*fps))
            for axis in (0,1):p[10][axis]=round((p[10][axis]+rng.gauss(0,.006))*256)/256
            yield 7+i/fps,p

    def test_two_circles_survive_quantized_wrist_after_gameplay(self):
        for seed in range(12):
            for direction in (-1,1):
                with self.subTest(seed=seed,direction=direction):
                    detector=CircleDetector(hands=('right',));hits=[]
                    for t,p in self.gameplay_then_circle(seed,direction=direction):
                        result=detector.update(p,t)
                        self.assertLessEqual(result['diagnostic'].get('checks',0),16)
                        if result['open']:hits.append((t,result))
                    self.assertEqual(len(hits),1)
                    # It must finish nearly two actual circles, not add the
                    # preceding horizontal swings to a single circle.
                    self.assertGreaterEqual(hits[0][0],7+1.88*2)
                    self.assertEqual(hits[0][1]['diagnostic']['reason'],'two_circles')

    def test_one_noisy_circle_after_gameplay_does_not_open(self):
        for seed in range(12):
            for direction in (-1,1):
                with self.subTest(seed=seed,direction=direction):
                    detector=CircleDetector(hands=('right',))
                    for t,p in self.gameplay_then_circle(seed,turns=1,direction=direction):
                        self.assertFalse(detector.update(p,t)['open'])

    def test_ordinary_gameplay_does_not_open(self):
        # Punches, straight slashes, overhead arm swings, and figure-eights
        # are distinct from the reserved two same-direction circles.
        paths=(lambda t:(.68+.13*math.sin(t*5),.46),
               lambda t:(.68,.46+.22*math.sin(t*4)),
               lambda t:(.68+.12*math.sin(t*4),.46+.21*math.sin(t*4)),
               lambda t:(.68+.11*math.cos(t*3),.46+.18*math.sin(t*6)),
               lambda t:(.68+.13*math.cos(math.pi*math.sin(t*2)),
                         .46+.22*math.sin(math.pi*math.sin(t*2))))
        for n,path in enumerate(paths):
            with self.subTest(path=n):
                rng=random.Random(90+n);detector=CircleDetector(hands=('right',))
                for i in range(16*24):
                    t=i/24;x,y=path(t);p=pose(0)
                    p[10]=[x+rng.gauss(0,.003),y+rng.gauss(0,.003),.9]
                    self.assertFalse(detector.update(p,t)['open'])

    def test_diagnostics_are_bounded_and_explain_rejection(self):
        detector=CircleDetector(hands=('right',))
        for i in range(120):
            p=pose(0);p[10]=[.68+.12*math.sin(i/7),.46,.9]
            result=detector.update(p,i/24)
        diagnostic=result['diagnostic']
        self.assertEqual(diagnostic['reason'],'fit_rejected')
        self.assertEqual(diagnostic['reject_reason'],'ellipse_shape')
        self.assertLessEqual(diagnostic['checks'],16)
        self.assertLess(len(json.dumps(diagnostic)),256)
        self.assertTrue(all(isinstance(v,(str,int,float,type(None))) for v in diagnostic.values()))
        self.assertEqual(detector.update(p,119/24)['diagnostic']['reason'],'duplicate_timestamp')
        self.assertEqual(detector.update([],6)['diagnostic']['reason'],'shoulders_missing')
        p=pose(0);p[10][2]=0
        self.assertEqual(detector.update(p,6.1)['diagnostic']['reason'],'wrist_missing')

    def test_production_only_accepts_right_arm(self):
        from suite_control import SuiteControl
        for hand in ('left','right'):
            suite=SuiteControl();suite.configure(dict(active=True,mode='fight',menu=False))
            for i in range(121):
                suite.observe(pose(math.tau*2*i/120,hand),i/30,1920,1080)
            self.assertEqual(suite.snapshot()['open_serial'],1 if hand=='right' else 0)
            self.assertEqual(suite.snapshot()['menu'],hand=='right')

    def replay(self,angles,fps=30,hand='right',missing=(),pause=None):
        d=CircleDetector();hits=[];active=[]
        for i,a in enumerate(angles):
            p=pose(a,hand)
            if i in missing:p[10 if hand=='right' else 9][2]=0
            o=d.update(p,i/fps)
            if o['open']:hits.append(i)
            if o['control_active']:active.append(i)
        return hits,active
    def test_two_turns_both_directions_hands_and_rates(self):
        for fps in [15,24,30,60]:
            for sign in [-1,1]:
                for hand in ['left','right']:
                    with self.subTest(fps=fps,sign=sign,hand=hand):
                        angles=[sign*math.tau*2*i/(fps*4) for i in range(fps*4+1)]
                        hits,active=self.replay(angles,fps,hand)
                        self.assertEqual(len(hits),1)
                        self.assertTrue(active and active[0]<fps*2)
    def test_single_turn_and_backtracking_do_not_open(self):
        for angles in [[math.tau*i/120 for i in range(121)],
                       [math.pi*math.sin(i*.055) for i in range(300)]]:
            self.assertEqual(self.replay(angles)[0],[])
    def test_two_turns_after_continuous_ordinary_motion(self):
        # The same detector sees ordinary motion and then the command, with
        # no pause or artificial reset between them.
        for fps in (15,30):
            for seconds in (3,5,7):
                for hand in ('left','right'):
                    for direction in (-1,1):
                        with self.subTest(fps=fps,seconds=seconds,hand=hand,direction=direction):
                            d=CircleDetector();index=9 if hand=='left' else 10
                            center=.32 if hand=='left' else .68
                            for i in range(seconds*fps):
                                p=pose(0,hand);p[index]=[center+.13*math.cos(math.tau*i/fps),.46,.95]
                                self.assertFalse(d.update(p,i/fps)['open'])
                            hits=[]
                            for i in range(fps*4+1):
                                r=d.update(pose(direction*math.tau*2*i/(fps*4),hand),seconds+i/fps)
                                if r['open']:hits.append(i)
                            self.assertEqual(len(hits),1)
    def test_pause_between_circles_cannot_add_them(self):
        one=[math.tau*i/60 for i in range(61)]
        angles=one+[math.tau]*45+[math.tau+v for v in one]
        self.assertEqual(self.replay(angles)[0],[])
    def test_missing_arc_cannot_complete_two_circles(self):
        angles=[math.tau*2*i/120 for i in range(121)]
        self.assertEqual(self.replay(angles,missing=range(55,67))[0],[])
    def test_still_noise_cannot_open(self):
        d=CircleDetector()
        for i in range(400):
            p=pose(0);p[10][0]+=.002*math.sin(i*.37);p[10][1]+=.002*math.cos(i*.41)
            self.assertFalse(d.update(p,i/30)['open'])
    def test_duplicate_or_backward_time_does_not_open(self):
        d=CircleDetector()
        for i in range(400):self.assertFalse(d.update(pose(i*.12),5.)['open'])
        self.assertFalse(d.update(pose(0),float('nan'))['open'])
    def test_invalid_input_does_not_crash(self):
        for points in [None,[],[None]*17,[[float('nan'),.2,1]]*17]:
            self.assertFalse(CircleDetector().update(points,1.)['open'])

if __name__=='__main__':unittest.main()
