"""Renderer-independent fruit game. Coordinates are logical 1280 x 720."""
import math
import random
from collections import deque

W, H = 1280, 720
ORDINARY_FRUITS = ('peach', 'mandarin', 'watermelon', 'apple', 'strawberry', 'dragonfruit')

def intersects(a, b, radius):
    dx, dy = b[0]-a[0], b[1]-a[1]
    den = dx*dx+dy*dy
    u = max(0, min(1, -(a[0]*dx+a[1]*dy)/den)) if den else 0
    return (a[0]+dx*u)**2+(a[1]+dy*u)**2 <= radius**2

class Game:
    def __init__(self, mode='arcade'):
        self.mode=mode
        self.charge=self.fury_until=self.fruits_cut=self.bombs_hit=self.missed=0
        self.state = 'menu'
        self.objects = []
        self.paths = {}
        self.events = deque(maxlen=64)
        self.elapsed = self.score = self.double_until = self.freeze_until = 0
        self.combo = self.best = 0
        self.random = random.Random()

    def start(self, seed=None):
        self.random.seed(seed)
        self.state = 'countdown'
        self.countdown = 3
        self.elapsed = self.score = self.double_until = self.freeze_until = 0
        self.combo = self.best = 0
        self.charge=self.fury_until=self.fruits_cut=self.bombs_hit=self.missed=0
        self.last_cut = -10
        self.spawn_in = .3
        self.objects.clear(); self.paths.clear(); self.events.clear()

    def pause(self):
        if self.state in ('playing', 'countdown'):
            self.previous = self.state; self.state = 'paused'; self.paths.clear()

    def resume(self):
        if self.state == 'paused':
            self.state = self.previous; self.paths.clear()

    def spawn(self, kind=None, **kw):
        if len(self.objects) >= 16: return
        r = self.random
        kind = kind or r.choices(['normal','double','ice','bomb'], [72,10,10,8])[0]
        x = r.uniform(150,1130)
        o = dict(kind=kind, fruit=r.choice(ORDINARY_FRUITS), x=x, y=805,
                 vx=(640-x)*.38+r.uniform(-80,80), vy=r.uniform(-1130,-950),
                 angle=r.uniform(0,6.28), spin=r.uniform(-1.3,1.3), radius=44 if kind=='bomb' else 48)
        o.update(kw); o['history'] = deque([(self.elapsed,o['x'],o['y'])])
        self.objects.append(o)
        return o

    @property
    def stage(self):
        return min(3,1+int(self.elapsed//20))

    @property
    def furious(self):
        return self.elapsed<self.fury_until

    def wave(self):
        r=self.random
        if self.mode=='classic':
            self.spawn_in=r.uniform(.78,1.33)
            for _ in range(r.randint(1,2)):self.spawn()
            return
        # Small readable formations, with a strict shared object budget.
        self.spawn_in=r.uniform(.95,1.2)/(1+.18*(self.stage-1))*(.70 if self.furious else 1)
        count=min(2+(1 if self.furious or (self.stage>=2 and r.random()<.5) else 0),max(0,8-len(self.objects)))
        center=r.uniform(280,1000); vy=r.uniform(-1100,-1000)
        formation=r.random()<.65
        for i in range(count):
            kind=r.choices(['normal','double','ice','bomb'],[76,9,9,0 if self.furious else 6+self.stage])[0]
            if formation:
                # Bombs are outside the combo fruit formation, not hidden inside it.
                x=center+(i-(count-1)/2)*100
                if kind=='bomb':x=150 if center>640 else 1130
                self.spawn(kind,x=x,vx=(640-center)*.3,vy=vy+(25*i if kind!='bomb' else -60))
            else:self.spawn(kind)

    def advance(self, dt):
        if not math.isfinite(dt) or dt <= 0: return
        if self.state == 'countdown':
            self.countdown -= dt
            if self.countdown <= 0: self.state = 'playing'; self.paths.clear()
            return
        if self.state != 'playing': return
        self.elapsed += dt
        if self.elapsed >= 60:
            self.state = 'over'; self.paths.clear(); return
        if dt > .25: self.paths.clear()
        step = min(dt,.25) * (.45 if self.elapsed < self.freeze_until else 1)
        for o in self.objects:
            o['x'] += o['vx']*step
            o['y'] += o['vy']*step+480*step*step
            o['vy'] += 960*step; o['angle'] += o['spin']*step
            h = o['history']; h.append((self.elapsed,o['x'],o['y']))
            while len(h)>2 and h[1][0]<self.elapsed-.35: h.popleft()
        remaining=[o for o in self.objects if o['y']<880 and -180<o['x']<1460]
        self.missed+=sum(o['kind']!='bomb' for o in self.objects if o not in remaining)
        self.objects=remaining
        self.spawn_in -= step
        if self.spawn_in <= 0:
            self.wave()

    @staticmethod
    def position(o,t):
        h = o['history']
        if t < h[0][0]-1e-6: return None
        for a,b in zip(h,list(h)[1:]):
            if t <= b[0]:
                u=(t-a[0])/(b[0]-a[0] or 1)
                return (a[1]+(b[1]-a[1])*u,a[2]+(b[2]-a[2])*u)
        return o['x'],o['y']

    def input(self, hand, x, y):
        if self.state != 'playing' or not all(map(math.isfinite,(x,y))) or not (0<=x<=W and 0<=y<=H):
            self.paths.pop(hand,None); return
        t=self.elapsed; prev=self.paths.get(hand)
        if prev and t<=prev[2]: return
        self.paths[hand]=(x,y,t)
        if not prev: return
        dt=t-prev[2]; speed=math.hypot(x-prev[0],y-prev[1])/dt
        if dt>.18 or not 220<=speed<=7500: return
        for o in self.objects[:]:
            times=[prev[2]]+[v[0] for v in o['history'] if prev[2]<v[0]<t]+[t]
            for t0,t1 in zip(times,times[1:]):
                c0,c1=self.position(o,t0),self.position(o,t1)
                if c0 is None or c1 is None: continue
                def relative(at,c):
                    u=(at-prev[2])/dt
                    return prev[0]+(x-prev[0])*u-c[0],prev[1]+(y-prev[1])*u-c[1]
                if intersects(relative(t0,c0),relative(t1,c1),o['radius']+5):
                    o['cut_angle']=math.atan2(y-prev[1],x-prev[0])
                    self.cut(o); self.objects.remove(o); break

    def cut(self,o):
        if o['kind']=='bomb':
            delta=-min(self.score,30); self.score+=delta; self.combo=0; self.last_cut=-10
            self.bombs_hit+=1; self.charge=0; self.fury_until=self.elapsed
        else:
            delta=20 if self.elapsed<self.double_until else 10
            self.fruits_cut+=1
            self.combo=self.combo+1 if self.elapsed-self.last_cut<=.55 else 1
            self.best=max(self.best,self.combo); self.last_cut=self.elapsed
            bonus=min(20,max(0,self.combo-2)*5) if self.mode=='arcade' else 0
            delta+=bonus; self.score+=delta
            if self.mode=='arcade' and not self.furious:
                self.charge+=1+(1 if self.combo>=3 else 0)
                if self.charge>=12:self.charge=0; self.fury_until=self.elapsed+6
            if o['kind']=='double': self.double_until=self.elapsed+8
            if o['kind']=='ice': self.freeze_until=self.elapsed+5
        self.events.append(dict(o,delta=delta,combo=self.combo))

class PoseInput:
    def __init__(self,game):
        self.game=game; self.last=None; self.mirror=True

    def reset(self):
        self.last=None
        for hand in ('left','right'): self.game.paths.pop(hand,None)

    def consume(self,s):
        if not isinstance(s,dict) or s.get('stale') or s.get('available') is False:
            self.reset(); return []
        t=s.get('timestamp_seconds'); key=(s.get('pid'),s.get('sequence'),t,s.get('action_reset_count'))
        if not isinstance(t,(int,float)) or not math.isfinite(t): self.reset(); return []
        if self.last==key: return []
        if self.last and (not 0<t-self.last[2]<=.18 or self.last[0]!=key[0] or self.last[3]!=key[3]): self.reset()
        self.last=key; out=[]; points=s.get('points17') or []
        for hand,i in [('left',9),('right',10)]:
            p=points[i] if len(points)>i else None
            if not p or len(p)<3 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p[:3]) or p[2]<.2 or not (0<=p[0]<=1 and 0<=p[1]<=1):
                self.game.paths.pop(hand,None); continue
            x=(1-p[0] if self.mirror else p[0])*W; y=p[1]*H
            self.game.input(hand,x,y); out.append((hand,x,y))
        return out
