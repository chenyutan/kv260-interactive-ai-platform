"""Deterministic closed-course kart racing. No GTK, camera, GPU or filesystem."""
from dataclasses import dataclass
import bisect
import math
import random

TAU = math.tau
N = 384
ROAD_HALF = 7.0
LAPS = 3
ITEM_NAMES = {'boost': '加速', 'shield': '护盾', 'pulse': '追踪弹'}
DRIFT_THRESHOLDS = (.65, 1.25, 1.95)

def drift_tier(charge):
    """Readable blue, gold and violet tiers shared by physics and the HUD."""
    return sum(charge >= threshold for threshold in DRIFT_THRESHOLDS)

def clamp(x, a, b): return max(a, min(b, x))

class Track:
    """Arc-length sampling; the C++ renderer uses the same 384-point table."""
    def __init__(self):
        self.points = []
        for i in range(N + 1):
            t = TAU * i / N
            height=4*math.sin((t-3.9)*math.pi/1.35)**2 if 3.9<t<5.25 else 0.
            self.points.append((112*math.sin(t)+22*math.sin(2*t),
                                height,
                                128*math.cos(t)-22*math.cos(3*t)))
        self.lengths = [0.]
        for a,b in zip(self.points,self.points[1:]):
            self.lengths.append(self.lengths[-1]+math.dist(a,b))
        self.length = self.lengths[-1]
    def sample(self, distance):
        d = distance % self.length
        i = clamp(bisect.bisect_right(self.lengths,d)-1,0,N-1)
        a,b = self.points[i:i+2]
        f = (d-self.lengths[i])/(self.lengths[i+1]-self.lengths[i])
        p = tuple(x+(y-x)*f for x,y in zip(a,b))
        prev=self.points[(i-1)%N];after=self.points[(i+2)%N]
        dx=(b[0]-prev[0])*(1-f)+(after[0]-a[0])*f
        dz=(b[2]-prev[2])*(1-f)+(after[2]-a[2])*f
        angle = math.atan2(dx,dz)
        return p, angle
    def curvature(self, distance):
        _,a=self.sample(distance-4);_,b=self.sample(distance+4)
        return ((b-a+math.pi)%TAU-math.pi)/8

TRACK = Track()

@dataclass
class Racer:
    id: int
    name: str
    s: float
    x: float
    speed: float = 0.
    steer: float = 0.
    shield: float = 0.
    boost: float = 0.
    slow: float = 0.
    item: str = ''
    drift: float = 0.
    drift_charge: float = 0.
    finish_time: float = 0.
    skill: float = 1.
    next_item: float = 0.
    hit_cooldown: float = 0.

class KartGame:
    def __init__(self,seed=20261018):
        self.seed=seed;self.rng=random.Random(seed)
        self.round_serial=0;self.events=[];self.state='title';self.paused_state='title'
        self.elapsed=0.;self.clock=0.;self.countdown=3.;self.score=0;self.coins=0
        self.lives=1;self.distance=0.;self.lap=1;self.rank=6;self.best_lap=0.
        self.lap_times=[];self.lap_started=0.;self.result='';self.karts=[]
        self.drifts=0;self.items_used=0;self.boosts=0;self.collisions=0
        self.projectiles=[];self.pickups=[];self.notice='';self.notice_time=0.
        self._setup()
    def _setup(self):
        self.karts=[Racer(0,'你',0.,0.,skill=1.)]
        names=('青竹','飞燕','赤霞','流云','小满')
        for i,name in enumerate(names,1):
            self.karts.append(Racer(i,name,9.+(i//2)*5.,(-1 if i%2 else 1)*2.5,
                                    skill=.89+i*.019,next_item=4.+i))
        self.pickups=[dict(id=i,s=TRACK.length*((i//3)+.55)/6,x=(-3.6,0.,3.6)[i%3],
                           timer=0.,kind=('boost','shield','pulse')[i%3]) for i in range(18)]
    @property
    def player(self):return self.karts[0]
    @property
    def time(self):return self.elapsed
    @property
    def finished(self):return self.state=='finished'
    def start(self):self.restart()
    def restart(self):
        serial=self.round_serial+1;seed=self.seed
        self.__init__(seed);self.round_serial=serial;self.state='countdown'
        self.events.append(dict(kind='start'))
    def pause(self):
        if self.state in ('countdown','running'):
            self.paused_state=self.state;self.state='paused'
    def resume(self):
        if self.state=='paused':self.state=self.paused_state
    def _event(self,kind,**fields):self.events.append(dict(kind=kind,**fields))
    def _notice(self,text):self.notice=text;self.notice_time=1.8
    def drain_events(self):events=self.events;self.events=[];return events
    def use_item(self,kart):
        if not kart.item:return
        item=kart.item;kart.item='';self._event('item',item=item,racer=kart.id)
        if kart.id==0:
            self.items_used+=1
            if item=='boost':self.boosts+=1
        if item=='boost':kart.boost=max(kart.boost,2.5)
        elif item=='shield':kart.shield=6.
        else:
            ahead=[r for r in self.karts if r.id!=kart.id and 0<r.s-kart.s<140 and not r.finish_time]
            target=min(ahead,key=lambda r:r.s).id if ahead else -1
            self.projectiles.append(dict(s=kart.s+3,x=kart.x,owner=kart.id,target=target,life=4.))
        if kart.id==0:self._notice(ITEM_NAMES[item]+'！')
    def hit(self,kart,owner=-1):
        if kart.hit_cooldown>0:return
        kart.hit_cooldown=1.0
        if kart.id==0:self.collisions+=1
        if kart.shield>0:kart.shield=0;self._event('shield_break',racer=kart.id)
        else:
            kart.slow=1.65;kart.speed*=.57;self._event('hit',racer=kart.id)
            if kart.id==0:self._notice('被击中 · 稍后恢复')
        if owner==0:self.score+=100
    def _step_racer(self,r,dt,action):
        for field in ('boost','shield','slow','hit_cooldown'):
            setattr(r,field,max(0.,getattr(r,field)-dt))
        if r.finish_time:
            # Classification freezes at the line, while AI drive onward so
            # later finishers never meet a stationary pile of ghost cars.
            if r.id:
                r.s+=max(22.,r.speed)*dt
                r.x+=clamp(-r.x,-dt*1.2,dt*1.2)
            return
        ai=r.id!=0;curve=TRACK.curvature(r.s)
        if ai:
            target=math.sin(r.s*.022+r.id*1.4)*3.8
            for other in self.karts:
                if other.id!=r.id and 0<other.s-r.s<13 and abs(other.x-target)<1.8:
                    target=clamp(target+(3.5 if target<0 else -3.5),-5.,5.)
            steer=clamp((target-r.x)*.28+curve*r.speed*.36,-1.,1.)
            drifting=abs(curve)>.013 and r.speed>21
            brake=False;accelerate=1.
            if r.item and self.elapsed>r.next_item:
                self.use_item(r);r.next_item=self.elapsed+self.rng.uniform(4,8)
        else:
            steer=clamp(float(action.get('steer',0.)),-1,1)
            drifting=bool(action.get('drift')) and abs(steer)>.17 and r.speed>14
            brake=bool(action.get('brake'));accelerate=clamp(float(action.get('accelerate',1.)),0,1)
            if action.get('item') or action.get('use_item'):self.use_item(r)
        r.steer+=(steer-r.steer)*min(1,dt*10)
        if drifting:
            r.drift=math.copysign(1.,steer);r.drift_charge=min(2.4,r.drift_charge+dt)
        else:
            tier=drift_tier(r.drift_charge)
            if tier:
                r.boost=max(r.boost,(0.,.80,1.40,2.05)[tier])
                self._event('drift_boost',racer=r.id,tier=tier)
                if not ai:
                    self.score+=30*tier;self.drifts+=1;self.boosts+=1
                    self._notice(('','蓝焰漂移！','金焰漂移！','紫焰超级漂移！')[tier])
            r.drift=0.;r.drift_charge=0.
        max_speed=(35.5 if not ai else 35.5*r.skill)*(1-min(.23,abs(curve)*5.5))
        if r.boost>0:max_speed+=15.
        if r.slow>0:max_speed*=.52
        if abs(r.x)>6.4:max_speed*=.63
        if brake:max_speed*=.40
        max_speed*=accelerate
        acceleration=11. if r.speed<max_speed else 18.
        r.speed+=clamp(max_speed-r.speed,-acceleration*dt,acceleration*dt)
        # Lateral steering and mild centrifugal slip, with forgiving boundaries.
        r.x+=(r.steer*(4.6+r.speed*.075)*(1.23 if drifting else 1.)
              -curve*r.speed*r.speed*.078*(.50 if drifting else 1.))*dt
        if abs(r.x)>7.0:
            r.x=math.copysign(7.,r.x);r.speed=max(5.,r.speed-dt*20)
            if not ai and r.hit_cooldown<=0:
                self._event('rail',racer=0);r.hit_cooldown=.6;self.collisions+=1
        r.s+=r.speed*dt
        if r.s>=TRACK.length*LAPS:
            r.s=TRACK.length*LAPS;r.finish_time=self.elapsed
    def _step(self,dt,action):
        self.clock+=dt
        if self.notice_time>0:self.notice_time=max(0,self.notice_time-dt)
        if self.state=='countdown':
            before=math.ceil(self.countdown);self.countdown=max(0,self.countdown-dt)
            if math.ceil(self.countdown)!=before:self._event('count',number=math.ceil(self.countdown))
            if self.countdown<=0:self.state='running';self._notice('出发！');self._event('go')
            return
        if self.state!='running':return
        self.elapsed+=dt
        oldlap=int(self.player.s/TRACK.length)
        for r in self.karts:self._step_racer(r,dt,action)
        # Resolve every pair, including AI/AI. Dimensions cover the exposed
        # tyres as well as the body; cars cannot simply drive through a pack.
        # Later pairs can push a car back into a pair already corrected in
        # this substep. Four bounded passes resolve the six-car pack without
        # changing collision dimensions or allowing order-dependent overlap.
        for contact_pass in range(4):
            corrected=False
            for i,a in enumerate(self.karts):
                for b in self.karts[i+1:]:
                    if a.finish_time or b.finish_time:continue
                    if abs(a.s-b.s)>=3.1 or abs(a.x-b.x)>=2.15:continue
                    corrected=True
                    side=-1 if a.x<b.x or a.x==b.x and a.id<b.id else 1
                    overlap=2.15-abs(a.x-b.x)
                    a.x=clamp(a.x+side*overlap*.51,-7,7)
                    b.x=clamp(b.x-side*overlap*.51,-7,7)
                    rear,front=(a,b) if a.s<b.s else (b,a)
                    rear.speed=min(rear.speed,max(6.,front.speed-.5))
                    if abs(a.x-b.x)<2.1:
                        # The rail can prevent a lateral solution. Keep the
                        # rear kart behind the front tyres in that case.
                        rear.s=min(rear.s,front.s-3.12)
            if not corrected:break
        for pickup in self.pickups:
            pickup['timer']=max(0,pickup['timer']-dt)
            if pickup['timer']>0:continue
            for r in self.karts:
                ds=(pickup['s']-r.s+TRACK.length/2)%TRACK.length-TRACK.length/2
                if abs(ds)<2.2 and abs(r.x-pickup['x'])<1.45 and not r.item and not r.finish_time:
                    r.item=pickup['kind'];pickup['timer']=7.
                    self._event('pickup',racer=r.id,item=r.item)
                    if r.id==0:self.coins+=1;self.score+=25;self._notice('获得'+ITEM_NAMES[r.item])
                    break
        alive=[]
        for p in self.projectiles:
            p['life']-=dt;p['s']+=57*dt
            if p['target']>=0:
                target=self.karts[p['target']]
                p['x']+=clamp(target.x-p['x'],-dt*9,dt*9)
            collided=False
            for r in self.karts:
                if r.id!=p['owner'] and abs(r.s-p['s'])<3 and abs(r.x-p['x'])<1.7:
                    self.hit(r,p['owner']);collided=True;break
            if not collided and p['life']>0:alive.append(p)
        self.projectiles=alive
        self.distance=self.player.s
        p=self.player
        self.rank=1+sum((r.finish_time<p.finish_time if r.finish_time and p.finish_time else
                         bool(r.finish_time) if not p.finish_time else False)
                        or (not r.finish_time and not p.finish_time and r.s>p.s)
                        for r in self.karts[1:])
        self.lap=min(LAPS,1+int(self.player.s/TRACK.length))
        if int(self.player.s/TRACK.length)>oldlap:
            lap_time=self.elapsed-self.lap_started;self.lap_times.append(lap_time)
            self.lap_started=self.elapsed;self.best_lap=min(self.lap_times)
            self._event('lap',lap=len(self.lap_times))
            if not self.player.finish_time:self._notice('最后一圈！' if self.lap==3 else '第2圈')
        if self.player.finish_time:
            self.state='finished';self.result='第%d名'%self.rank
            self.score+=(6-self.rank)*100000+max(0,60000-int(self.elapsed*100))
            self._event('finished',report=self.report())
    def update(self,dt,action=None):
        if self.state in ('title','paused','finished'):return
        action=action or {};dt=clamp(float(dt),0,.10)
        # Fixed-size substeps make collisions and drift release stable at 30 Hz.
        while dt>1e-8:
            step=min(dt,1/60);self._step(step,action);dt-=step
            action=dict(action,item=False,use_item=False)
    def report(self):
        return dict(score=int(self.score),elapsed=round(self.elapsed,3),time=round(self.elapsed,3),
                    distance=round(self.player.s,2),lap=self.lap,laps=LAPS,rank=int(self.rank),
                    result='finished' if self.finished else self.state,best_lap=round(self.best_lap,3),
                    lap_times=[round(x,3) for x in self.lap_times],coins=self.coins,
                    progress=clamp(self.player.s/(TRACK.length*LAPS),0,1),
                    position=int(self.rank),total_laps=LAPS,finish_time=round(self.player.finish_time,3),
                    drifts=self.drifts,items_used=self.items_used,boosts=self.boosts,collisions=self.collisions,
                    track='山水环线',round_serial=self.round_serial)
    def snapshot(self):
        return dict(state=self.state,elapsed=self.elapsed,clock=self.clock,countdown=self.countdown,
                    rank=self.rank,lap=self.lap,laps=LAPS,speed=self.player.speed*3.6,
                    score=self.score,coins=self.coins,distance=self.player.s,track_length=TRACK.length,
                    karts=[r.__dict__.copy() for r in self.karts],pickups=self.pickups,
                    projectiles=self.projectiles,progress=clamp(self.player.s/(TRACK.length*LAPS),0,1),
                    item=self.player.item,notice=self.notice if self.notice_time>0 else '',
                    lap_times=self.lap_times,best_lap=self.best_lap,result=self.result)
