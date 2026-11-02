"""Deterministic combat and conservative shoulder-relative 2-D gestures."""
import math
import random
from front_gesture import FrontGesture

MOVES = {
    'left': dict(name='青锋直拳', hand='left', damage=8, windup=.12, duration=.34, cooldown=.34, energy=10),
    'right': dict(name='破竹直拳', hand='right', damage=8, windup=.12, duration=.34, cooldown=.34, energy=10),
    'uppercut_left': dict(name='左·升龙拳', hand='left', damage=13, windup=.17, duration=.48, cooldown=.50, energy=13),
    'uppercut_right': dict(name='右·升龙拳', hand='right', damage=13, windup=.17, duration=.48, cooldown=.50, energy=13),
    'palm': dict(name='双掌·排云', hand='both', damage=17, windup=.21, duration=.62, cooldown=.68, energy=15),
    'special': dict(name='奥义·墨龙破', hand='both', damage=30, windup=.30, duration=1.02, cooldown=1.10, energy=0),
    'kick_left': dict(name='左·穿云腿',hand='left',damage=12,windup=.18,duration=.52,cooldown=.55,energy=12),
    'kick_right': dict(name='右·追风腿',hand='right',damage=12,windup=.18,duration=.52,cooldown=.55,energy=12),
}
for _name,_move in MOVES.items():
    _move.update(reach=680 if _name=='special' else 560 if _name=='palm' else 510 if _name.startswith('kick') else 445,
                 level='all' if _name=='special' else 'mid' if _name=='palm' or _name.startswith('kick') else 'high')
ENEMY_MOVES = {
    'enemy_jab': dict(name='探云拳', hand='right', damage=12, chip=1, windup=.88, duration=1.18),
    'enemy_heavy': dict(name='震山掌', hand='both', damage=18, chip=4, windup=1.23, duration=1.60),
    'enemy_sweep': dict(name='回风扫腿', hand='left', damage=14, chip=2, windup=1.03, duration=1.37),
}
for _name,_move in ENEMY_MOVES.items():
    _move.update(reach=520 if _name=='enemy_heavy' else 470,
                 level='low' if _name=='enemy_sweep' else 'high' if _name=='enemy_jab' else 'mid')

class Fight:
    def __init__(self):
        self.state='menu'; self.time=0.; self.hp=100; self.enemy_hp=100
        self.guard=False; self.attack=None; self.enemy_attack=None; self.effects=[]
        self.combo=0; self.hits=0; self.blocks=0; self.remaining=60.; self.result=''
        self.last_hit=-10.; self.next_attack=1.7; self.cooldown=0.; self.rng=random.Random(19)
        self.energy=0; self.max_energy=100; self.perfect_blocks=0
        self.guard_since=-100.; self.counter_until=0.; self.last_move=''; self.move_counts={}
        self.enemy_move_index=0
        self.player_x=435.;self.enemy_x=845.;self.player_y=0.;self.enemy_y=0.
        self.player_vy=0.;self.enemy_vy=0.;self.move_axis=0.;self.enemy_axis=0.
        self.crouch=False;self.enemy_crouch=False;self.facing=1;self.enemy_facing=-1
        self.walk_phase=0.;self.enemy_walk_phase=0.;self.misses=0;self.enemy_misses=0
        self.jumps=0;self.kicks=0;self.ai_enabled=True
        self.finish_remaining=0.;self.finish_rate=1.

    def start(self):
        self.__init__(); self.state='countdown'; self.countdown=3.

    def punch(self, hand):
        """Compatibility entry point; accepts every key in MOVES."""
        return self.perform(hand)

    def perform(self, move):
        if move not in MOVES or self.state!='playing' or self.attack or self.time+1e-9<self.cooldown:
            return False
        if move=='special' and self.energy<self.max_energy:
            return False
        self.attack=dict(MOVES[move], move=move, age=0., hit=False,
                         counter=self.time<self.counter_until,facing=self.facing)
        if move=='special':
            self.energy=0
        self.counter_until=0.; self.guard=False;self.crouch=False
        self.cooldown=self.time+MOVES[move]['cooldown']
        self.last_move=move; self.move_counts[move]=self.move_counts.get(move,0)+1
        if move.startswith('kick_'):self.kicks+=1
        return True

    def jump(self):
        if self.state!='playing' or self.player_y>1e-6 or self.crouch:return False
        self.player_vy=690.;self.player_y=.001;self.jumps+=1;return True

    def _space(self,dt):
        speed=265.*(.40 if self.guard else 1.)*(.20 if self.attack else 1.)
        if self.crouch:speed=0.
        self.player_x+=self.move_axis*speed*dt
        self.walk_phase+=abs(self.move_axis)*dt*7
        gap=self.enemy_x-self.player_x
        self.enemy_axis=0.
        if self.ai_enabled and not self.enemy_attack:
            if abs(gap)>385:self.enemy_axis=-1. if gap>0 else 1.
            elif abs(gap)<205:self.enemy_axis=1. if gap>0 else -1.
        self.enemy_x+=self.enemy_axis*115.*dt
        self.enemy_walk_phase+=abs(self.enemy_axis)*dt*5
        for prefix in ('player','enemy'):
            y=getattr(self,prefix+'_y');vy=getattr(self,prefix+'_vy')
            if y>0 or vy>0:
                y+=vy*dt;vy-=1800.*dt
                if y<=0:y=0.;vy=0.
                setattr(self,prefix+'_y',y);setattr(self,prefix+'_vy',vy)
        self.player_x=max(150.,min(1130.,self.player_x));self.enemy_x=max(150.,min(1130.,self.enemy_x))
        gap=self.enemy_x-self.player_x
        if abs(gap)<145 and abs(self.player_y-self.enemy_y)<90:
            sign=1 if gap>0 else -1 if gap<0 else self.facing
            middle=(self.player_x+self.enemy_x)/2
            middle=max(222.5,min(1057.5,middle))
            self.player_x=middle-sign*72.5;self.enemy_x=middle+sign*72.5
        self.facing=1 if self.enemy_x>=self.player_x else -1
        self.enemy_facing=-self.facing

    def _connects(self,attack,enemy=False):
        x,y=(self.enemy_x,self.enemy_y) if enemy else (self.player_x,self.player_y)
        tx,ty=(self.player_x,self.player_y) if enemy else (self.enemy_x,self.enemy_y)
        facing=attack.get('facing',self.enemy_facing if enemy else self.facing)
        if (tx-x)*facing<0 or abs(tx-x)>attack.get('reach',445):return False
        crouch=self.crouch if enemy else self.enemy_crouch
        bottom,top=ty+(20 if crouch else 35),ty+(205 if crouch else 340)
        level=attack.get('level','mid')
        if level=='all':return y+440>=bottom and y+45<=top
        contact=y+dict(low=60,mid=170,high=250)[level]
        return bottom<=contact<=top

    def _knockback(self,enemy,amount,direction):
        field='player_x' if enemy else 'enemy_x'
        setattr(self,field,max(150.,min(1130.,getattr(self,field)+amount*direction)))

    def _enemy_hit(self,a):
        a['hit']=True
        if not self._connects(a,True):
            a['miss']=True;self.enemy_misses+=1;return
        level=a.get('level','mid')
        blocked=self.guard and (level=='mid' or (level=='low' and self.crouch) or (level=='high' and not self.crouch))
        perfect=blocked and self.time-self.guard_since<=.20
        damage=0 if perfect else a['chip'] if blocked else a['damage']
        self.hp=max(0,self.hp-damage)
        if blocked:
            self.blocks+=1;self.perfect_blocks+=int(perfect)
            self.energy=min(self.max_energy,self.energy+(14 if perfect else 7))
            if perfect:self.counter_until=self.time+1.25
        else:self.combo=0;self.counter_until=0.
        self._effect('player','block' if blocked else 'hit',a,damage,perfect=perfect)
        if not blocked:self._knockback(True,28,a.get('facing',self.enemy_facing))

    def _effect(self, side, kind, attack, damage, **extra):
        # Freeze the impact at contact, before knockback or a later jump moves
        # the actor. Coordinates are in the renderer's 1280x720 arena space.
        player=side=='player';target_x=self.player_x if player else self.enemy_x
        attacker_y=self.enemy_y if player else self.player_y
        facing=attack.get('facing',self.enemy_facing if player else self.facing)
        level=attack.get('level','mid')
        contact=attacker_y+dict(low=60,mid=170,high=250,all=210).get(level,170)
        self.effects.append(dict(side=side, kind=kind, age=0., damage=damage,
                                 move=attack['move'], name=attack['name'],
                                 combo=self.combo, lifetime=.65,
                                 x=target_x-facing*59,y=590-contact,face=-facing,level=level,**extra))
        self.effects=self.effects[-6:]

    def _player_hit(self, a):
        a['hit']=True
        if not self._connects(a):
            a['miss']=True;self.misses+=1;return
        self.combo=self.combo+1 if self.time-self.last_hit<1.15 else 1
        self.last_hit=self.time
        damage=a['damage']+(3 if self.combo%3==0 else 0)+(4 if a['counter'] else 0)
        self.enemy_hp=max(0,self.enemy_hp-damage); self.hits+=1
        self.energy=min(self.max_energy,self.energy+a['energy'])
        self._effect('enemy','hit',a,damage,counter=a['counter'])
        self._knockback(False,42 if a['move'] in ('palm','special') else 24,a.get('facing',self.facing))
        # A heavy blow staggers an opponent still winding up. It cannot erase
        # a hit which already landed.
        if a['move'] in ('palm','special') and self.enemy_attack and not self.enemy_attack['hit']:
            self.enemy_attack=None
            self.next_attack=max(self.next_attack,self.time+.85)

    def _finish(self):
        if self.state!='playing':return
        self.result='胜' if self.enemy_hp<self.hp else '负' if self.hp<self.enemy_hp else '平'
        self.guard=False;self.crouch=False;self.move_axis=0.;self.enemy_axis=0.
        if self.hp>0 and self.enemy_hp>0:
            # A time limit has no lethal impact waiting to be shown.
            self._end_round();return
        # Decide the match at contact, then finish only the visible motion.
        # No AI, collision, damage or gameplay clock advances after this point.
        if self.enemy_hp<=0:self.enemy_attack=None
        if self.hp<=0:self.attack=None
        attacks=[a for a in (self.attack,self.enemy_attack) if a]
        self.finish_rate=.65 if self.attack and self.attack['move']=='special' else 1.
        tail=max([.30]+[max(0.,a['duration']-a['age']) for a in attacks]+
                 [max(0.,e['lifetime']-e['age']) for e in self.effects])
        self.finish_remaining=tail/self.finish_rate+.15
        self.state='finishing'

    def _end_round(self):
        self.state='over';self.attack=None;self.enemy_attack=None
        self.finish_remaining=0.

    def _finish_step(self,dt):
        used=min(dt,self.finish_remaining);animation_dt=used*self.finish_rate
        for name in ('attack','enemy_attack'):
            a=getattr(self,name)
            if a:
                a['age']+=animation_dt
                if a['age']+1e-9>=a['duration']:setattr(self,name,None)
        for effect in self.effects:effect['age']+=animation_dt
        self.effects=[e for e in self.effects if e['age']<e['lifetime']]
        self.finish_remaining=max(0.,self.finish_remaining-used)
        if self.finish_remaining<=1e-9:self._end_round()

    def _step(self, dt, guard):
        self.time+=dt; self.remaining=max(0.,60.-self.time)
        if self.combo and self.time-self.last_hit>1.15:
            self.combo=0
        guarding=bool(guard) and self.attack is None
        if guarding and not self.guard:
            self.guard_since=self.time-dt
        self.guard=guarding
        self._space(dt)
        for effect in self.effects:
            effect['age']+=dt
        self.effects=[effect for effect in self.effects if effect['age']<effect['lifetime']]
        if self.attack:
            a=self.attack; a['age']+=dt
            if a['age']+1e-9>=a['windup'] and not a['hit']:
                self._player_hit(a)
            if a['age']+1e-9>=a['duration']:
                self.attack=None
        if self.enemy_hp<=0:
            self._finish(); return
        if (self.ai_enabled and not self.enemy_attack and self.time+1e-9>=self.next_attack
                and abs(self.enemy_x-self.player_x)<540):
            move=('enemy_jab','enemy_heavy','enemy_sweep')[self.enemy_move_index%3]
            self.enemy_move_index+=1
            self.enemy_attack=dict(ENEMY_MOVES[move],move=move,age=0.,hit=False,facing=self.enemy_facing)
            self.next_attack=self.time+self.rng.uniform(2.15,2.90)
        if self.enemy_attack:
            a=self.enemy_attack; a['age']+=dt
            if a['age']+1e-9>=a['windup'] and not a['hit']:
                self._enemy_hit(a)
            if a['age']+1e-9>=a['duration']:
                self.enemy_attack=None
        if self.hp<=0 or self.remaining<=1e-8:
            self._finish()

    def advance(self, dt, guard=False, control=None):
        if not isinstance(dt,(int,float)) or not math.isfinite(dt) or dt<=0:
            return
        if self.state=='countdown':
            used=min(dt,max(0.,self.countdown)); self.countdown-=used; dt-=used
            if self.countdown>1e-9:
                return
            self.countdown=0.; self.state='playing'
        if self.state=='finishing':
            self._finish_step(dt);return
        if self.state!='playing':
            return
        control=control if isinstance(control,dict) else {}
        axis=control.get('move_axis',0.)
        self.move_axis=max(-1.,min(1.,axis)) if isinstance(axis,(float,int)) and math.isfinite(axis) else 0.
        self.crouch=bool(control.get('crouch_guard') and guard and self.player_y<=0 and self.attack is None)
        if control.get('jump'):self.jump()
        # Short substeps preserve hit order on a dropped rendering frame;
        # elapsed time is never silently clamped to one display frame.
        while dt>1e-9 and self.state=='playing':
            if self.remaining<=1e-9:self._finish();break
            step=min(dt,1./120.,self.remaining); self._step(step,guard); dt-=step
        # Preserve the same presentation timing across dropped frames and
        # fine-grained updates when a lethal contact occurs inside this call.
        if dt>1e-9 and self.state=='finishing':self._finish_step(dt)

class Gesture:
    """Return one-shot commands from observed shoulder-relative wrist motion.

    Either trusted arm can punch; guard and two-hand moves need both arms.
    Retraction seeds a motion, so holding or entering already extended cannot
    attack. Recent observed motion lets a strike naturally decelerate at its
    end. Missing joints are never interpolated. FrontGesture supplies observed
    projection evidence for camera-facing strikes; no 3-D depth is inferred.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.front=FrontGesture()
        self.previous={}; self.armed={}; self.upper_ready={}; self.pending={}
        self.motion={}; self.special_anchors={}; self.upper_spent={}
        self.last=None; self.guard=False; self.valid=False; self.both_valid=False
        self.special_ready=False; self.high_previous=False; self.last_command=-100.
        self.hint='请让肩、肘、腕入镜'

    def _clear_motion(self, hand=None):
        for mapping in (self.previous,self.armed,self.upper_ready,self.pending,self.motion):
            if hand is None:
                mapping.clear()
            else:
                mapping.pop(hand,None)
        self.special_ready=False; self.special_anchors.clear(); self.high_previous=False
        self.both_valid=False; self.guard=False
        if hand is None:
            self.valid=False; self.upper_spent.clear()

    def _recent(self, hand, after, metric):
        """Only measured velocities in the latest 300 ms of this motion."""
        return max((m[metric] for m in self.motion.get(hand,[]) if m['t']>after),default=0.)

    def consume(self, s):
        if not isinstance(s,dict) or s.get('stale') or s.get('available') is False:
            self.reset(); return []
        points=s.get('points17') or []; t=s.get('timestamp_seconds'); pid=s.get('pid')
        if not isinstance(points,(list,tuple)):
            self.reset(); return []
        if not isinstance(t,(float,int)) or not math.isfinite(t):
            self.reset(); return []
        key=(pid,s.get('sequence'),t)
        if key==self.last:
            return []
        if self.last and (pid!=self.last[0] or not 0<t-self.last[2]<=.20):
            self.reset()
        self.last=key
        previously_front_locked=set(self.front.locked)
        front_out=self.front.consume(s)
        front_blocked=self.front.blocking_hands
        for hand in previously_front_locked-set(self.front.locked):
            # A front punch has completed its observed return. Do not reuse
            # a legacy anchor from before that punch to attack on retraction.
            self.armed.pop(hand,None);self.upper_ready.pop(hand,None);self.pending.pop(hand,None)
        def point(i):
            p=points[i] if i<len(points) else None
            if not isinstance(p,(list,tuple)) or len(p)<3:
                return None
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p[:3]):
                return None
            supported=i in getattr(self.front,'supported_shoulders',set())
            minimum=.12 if supported else .20
            return p if p[2]>=minimum and 0<=p[0]<=1 and 0<=p[1]<=1 else None
        joints={i:point(i) for i in (5,6,7,8,9,10)}
        if not joints[5] or not joints[6]:
            self._clear_motion(); self.hint='双肩未识别，请面向摄像头'; return []
        ls,rs=joints[5],joints[6]
        width=math.hypot(ls[0]-rs[0],ls[1]-rs[1])
        if width<.05:
            self._clear_motion(); self.hint='请面向摄像头并适当靠近'; return []
        current={}
        for hand,si,ei,wi in (('left',5,7,9),('right',6,8,10)):
            sh,el,wr=joints[si],joints[ei],joints[wi]
            if not el or not wr:
                # An unrelated hand disappearing must not cancel a measured
                # strike on the other arm. The missing arm must seed again.
                self._clear_motion(hand); continue
            x,y=(wr[0]-sh[0])/width,(wr[1]-sh[1])/width
            reach=math.hypot(x,y)
            length=math.hypot(el[0]-sh[0],el[1]-sh[1])+math.hypot(wr[0]-el[0],wr[1]-el[1])
            straight=reach*width/(length+1e-8)
            old=self.previous.get(hand)
            vx=(x-old['x'])/(t-old['t']) if old else 0.
            vy=(y-old['y'])/(t-old['t']) if old else 0.
            radial=(reach-old['reach'])/(t-old['t']) if old else 0.
            speed=math.hypot(vx,vy)
            # The live camera is 16:9. Normalized y has 16/9 times the
            # pixel displacement of normalized x; compare jumps in pixels.
            # Keep legacy gesture coordinates separate from this sanity gate.
            physical_speed=math.hypot(vx,vy*9./16.)
            if old and physical_speed>=24.:
                # A discontinuous landmark jump is not usable motion evidence.
                self._clear_motion(hand); old=None; vx=vy=radial=speed=physical_speed=0.
            history=[m for m in self.motion.get(hand,[]) if t-m['t']<=.30+1e-9]
            history.append(dict(t=t,speed=speed if physical_speed<14. else 0.,
                                outward=max(0.,radial) if physical_speed<14. else 0.,
                                up=max(0.,-vy) if physical_speed<14. else 0.))
            self.motion[hand]=history
            current[hand]=dict(t=t,x=x,y=y,reach=reach,straight=straight,vx=vx,vy=vy,
                               old=old,elbow_y=(el[1]-sh[1])/width,
                               center_x=(wr[0]-(ls[0]+rs[0])*.5)/width)
            stationary=speed<.55
            anchor=self.armed.get(hand)
            if hand not in front_blocked and reach<.80 and y>-.75 and (not anchor or stationary
                    or reach<math.hypot(anchor['x'],anchor['y'])-.02):
                self.armed[hand]=dict(t=t,x=x,y=y)
            anchor=self.upper_ready.get(hand)
            if hand in self.upper_spent:
                self.upper_spent[hand]=min(self.upper_spent[hand],y)
                if y-self.upper_spent[hand]>.25 and y>-.55:
                    self.upper_spent.pop(hand,None)
            if (hand not in front_blocked and hand not in self.upper_spent and -.55<y<1.5 and straight<.92 and
                    (not anchor or stationary or y>anchor['y']+.02)):
                self.upper_ready[hand]=dict(t=t,x=x,y=y)
        self.valid=bool(current); self.both_valid=len(current)==2
        # Face-level fists can also lie a shoulder width above the shoulders.
        # An overhead raise needs the elbows to rise as well; wrists alone
        # confused a normal high guard with the energy-consuming special.
        high=self.both_valid and all(m['y']<-.95 and m['elbow_y']<-.10 for m in current.values())
        low=self.both_valid and all(m['y']>-.55 for m in current.values())
        seeded=self.both_valid and all(m['old'] is not None for m in current.values())
        if low:
            self.special_ready=True
            self.special_anchors={hand:m['y'] for hand,m in current.items()}
        out=[]
        if (seeded and high and self.special_ready and
                all(self.special_anchors.get(hand,m['y'])-m['y']>.35 for hand,m in current.items())):
            # A slow final crossing must not swallow the full observed raise.
            # Ready is restored only after BOTH hands actually lower again.
            out=['special']; self.special_ready=False; self.special_anchors.clear()
        self.high_previous=high

        def travel(m,a):
            return (m['x']-a['x'],m['y']-a['y'])
        def aligned(v1,v2):
            n1=math.hypot(*v1); n2=math.hypot(*v2)
            return n1>1e-8 and n2>1e-8 and (v1[0]*v2[0]+v1[1]*v2[1])/(n1*n2)>.72
        if not out and front_out:out=front_out
        candidates=[]
        for hand,m in current.items():
            if hand in front_blocked:continue
            a=self.armed.get(hand); old=m['old']
            if a and old and t-a['t']<1.1:
                d=travel(m,a)
                if (m['reach']>.90 and m['straight']>.82
                        and m['reach']-math.hypot(a['x'],a['y'])>.28
                        and self._recent(hand,a['t'],'outward')>.85
                        and abs(d[0])>.38 and abs(d[1])<abs(d[0])*1.5):
                    candidates.append(hand)
        if not out and not front_blocked and self.both_valid and len(self.armed)==2 and seeded:
            a,b=current['left'],current['right']; ca,cb=self.armed['left'],self.armed['right']
            da,db=travel(a,ca),travel(b,cb)
            if (max(t-ca['t'],t-cb['t'])<1.1 and min(a['reach'],b['reach'])>.90
                    and min(a['straight'],b['straight'])>.82
                    and min(math.hypot(*da),math.hypot(*db))>.50 and aligned(da,db)
                    and abs(da[0])>abs(da[1])*1.15 and abs(db[0])>abs(db[1])*1.15
                    and all(self._recent(hand,self.armed[hand]['t'],'speed')>.70 for hand in current)):
                out=['palm']
        if not out:
            for hand,m in current.items():
                if hand in front_blocked:continue
                a=self.upper_ready.get(hand)
                if not a:
                    continue
                rise=a['y']-m['y']
                other_hand='right' if hand=='left' else 'left'
                other=current.get(other_hand); other_anchor=self.upper_ready.get(other_hand)
                other_rise=other_anchor['y']-other['y'] if other and other_anchor else 0.
                # Allow a natural counterbalancing hand, but do not turn a
                # coordinated two-arm overhead raise into one uppercut.
                bilateral=other_rise>.24 and other_rise>=rise*.65
                # Natural guard raises need not start on the very same frame.
                # A lagging arm already rising at a comparable speed is
                # coordinated evidence, before its travel catches up. Small
                # counterbalancing motion remains allowed.
                main_up=self._recent(hand,a['t'],'up')
                other_up=self._recent(other_hand,other_anchor['t'],'up') if other_anchor else 0.
                bilateral=bilateral or (other is not None and other_rise>.10
                    and main_up>.85 and other_up>main_up*.60 and other['vy']<-.5)
                if (m['old'] and t-a['t']<1.1 and rise>.44 and rise>abs(m['x']-a['x'])*1.05
                        and m['y']<-.18 and m['straight']<.90 and m['elbow_y']>-.35
                        and self._recent(hand,a['t'],'up')>.85 and not bilateral):
                    out=['uppercut_'+hand]; break
        if not out:
            for hand in candidates:
                m=current[hand]; other=current.get('right' if hand=='left' else 'left')
                if other and aligned((m['vx'],m['vy']),(other['vx'],other['vy'])) and math.hypot(other['vx'],other['vy'])>.65:
                    # Give the second arm 100 ms to complete a double palm.
                    self.pending.setdefault(hand,t+.10)
                else:
                    out=[hand]; break
            if not out:
                for hand,deadline in list(self.pending.items()):
                    if hand in front_blocked or hand not in current or current[hand]['reach']<.85 or current[hand]['straight']<.80:
                        self.pending.pop(hand,None); continue
                    if t+1e-9>=deadline:
                        out=[hand]; break
        if out:
            self.last_command=t
            if out[0] in ('palm','special'):
                self.armed.clear(); self.upper_ready.clear(); self.pending.clear()
            else:
                hand=out[0].split('_')[-1]
                self.armed.pop(hand,None); self.upper_ready.pop(hand,None); self.pending.pop(hand,None)
                if out[0].startswith('uppercut_'):
                    # A long continuing rise is still one uppercut. Only an
                    # observed downward retraction can prepare the next one.
                    self.upper_spent[hand]=current[hand]['y']
        # A small release margin avoids flickering a held bent-arm guard at
        # the shoulder-height boundary. Entry still requires the higher pose;
        # missing joints or an attack immediately clear it as before.
        guard_height=.36 if self.guard else .16
        self.guard=(self.both_valid and not high and not out and all(m['y']<guard_height and abs(m['center_x'])<1.10
                    and (m['straight']<.92 or m['reach']<.78) for m in current.values()))
        self.previous={hand:{k:m[k] for k in ('t','x','y','reach')} for hand,m in current.items()}
        if out:
            self.hint='已识别：'+MOVES[out[0]]['name']
        elif not self.valid:
            self.hint='肘腕未识别，请让手臂完整入镜'
        elif not self.both_valid:
            self.hint='单臂可出拳；双手招式需要两臂入镜'
        elif high:
            self.hint='奥义后请放下双手再举起'
        elif self.guard:
            self.hint='防御中 · 可直接出拳'
        else:
            self.hint='出拳后收手；双掌可向镜头前推或同侧横推'
        return out
