"""Causal two-circle wrist controller; observed coordinates only, no image access."""
import math
from collections import deque

def _point(points,index,threshold=.12):
    try:
        x,y,c=map(float,points[index][:3])
        if c<threshold or not all(map(math.isfinite,(x,y,c))) or not(0<=x<=1 and 0<=y<=1):return None
        return (x,y)
    except (TypeError,ValueError,IndexError):return None

class _Track:
    def __init__(self):self.reset()
    def reset(self):
        self.samples=deque();self.last=None;self.anchor=None;self.moving=None;self.checked=-1e9;self.result=(0.,False);self.bend_start=None
        self.diagnostic={'reason':'collecting','turns':0.,'samples':0,'checks':0}
    def missing(self,t):
        if self.last is not None and t-self.last[0]>.20:self.reset()
    def update(self,t,p):
        if self.last is not None:
            dt=t-self.last[0];dist=math.hypot(p[0]-self.last[1][0],p[1]-self.last[1][1])
            if dt>.20:self.reset()
            elif dist>max(.30,dt*9):
                self.missing(t);self.diagnostic={'reason':'position_jump','turns':0.,'samples':len(self.samples),'checks':0};return (0.,False)
        self.last=(t,p)
        if self.anchor is None:self.anchor=p;self.moving=t
        if math.hypot(p[0]-self.anchor[0],p[1]-self.anchor[1])>.055:self.anchor=p;self.moving=t
        if t-self.moving>.85:
            self.reset();self.last=(t,p);self.anchor=p;self.moving=t
        if not self.samples or math.hypot(p[0]-self.samples[-1][1][0],p[1]-self.samples[-1][1][1])>.025:
            if len(self.samples)>=2:
                a,b=self.samples[-2][1],self.samples[-1][1]
                ux,uy=b[0]-a[0],b[1]-a[1];vx,vy=p[0]-b[0],p[1]-b[1]
                # A sharp change separates preceding ordinary motion from a
                # new smooth arc. It supplies one extra suffix, not a reset:
                # original candidates and every circle quality gate remain.
                if ux*vx+uy*vy<.22*math.hypot(ux,uy)*math.hypot(vx,vy):self.bend_start=self.samples[-1][0]
            self.samples.append((t,p))
        while self.samples and t-self.samples[0][0]>9:self.samples.popleft()
        if len(self.samples)<20:
            self.diagnostic={'reason':'collecting','turns':0.,'samples':len(self.samples),'checks':0}
            return (0.,False)
        if t-self.checked<.12:return self.result
        self.checked=t
        samples=list(self.samples);xy=[a[1] for a in samples];best=0.;best_window=0.;rejects={};checks=0
        def reject(reason):rejects[reason]=rejects.get(reason,0)+1
        # Fit a bounded set of suffixes; avoid NumPy dispatch and allocation in
        # the shared inference thread. Each candidate uses observed samples.
        offsets=set((0,6,18,30,len(xy)//8,len(xy)//4))
        # Ordinary gameplay can leave several seconds of non-circular history.
        # Point offsets above mostly trim its beginning; bend_start can also
        # move forward on harmless quantized wrist jitter. Independently test
        # a bounded set of recent time windows without relaxing any fit gate.
        for age in (1.5,2.,2.5,3.,3.5,4.,5.,6.5,8.):
            offset=next((i for i,(stamp,_) in enumerate(samples) if stamp>=t-age),len(samples))
            if len(samples)-offset>=20:offsets.add(offset)
        if self.bend_start is not None:
            offset=next((i for i,(stamp,_) in enumerate(samples) if stamp>=self.bend_start),len(samples))
            if len(samples)-offset>=20:offsets.add(offset)
        for offset in sorted(offsets):
            a=xy[offset:];n=len(a)
            if n<20 or t-samples[offset][0]<.9:continue
            checks+=1
            xs=sorted(p[0] for p in a);ys=sorted(p[1] for p in a)
            def quantile(values,fraction):
                k=(n-1)*fraction;i=int(k)
                return values[i]+(values[min(i+1,n-1)]-values[i])*(k-i)
            x0,x1=quantile(xs,.05),quantile(xs,.95)
            y0,y1=quantile(ys,.05),quantile(ys,.95)
            rx,ry=(x1-x0)*.5,(y1-y0)*.5
            if min(rx,ry)<.27 or max(rx,ry)>2.8 or min(rx,ry)/max(rx,ry)<.34:reject('ellipse_shape');continue
            cx,cy=(x0+x1)*.5,(y0+y1)*.5
            radii=[];angles=[]
            for x,y in a:
                zx,zy=(x-cx)/rx,(y-cy)/ry
                radii.append(math.hypot(zx,zy));angles.append(math.atan2(zy,zx))
            radii.sort()
            if quantile(radii,.1)<.55 or quantile(radii,.9)>1.5:reject('radial_scatter');continue
            delta=[(b-a+math.pi)%math.tau-math.pi for a,b in zip(angles,angles[1:])]
            if max(map(abs,delta))>1.15:reject('angular_jump');continue
            total=sum(delta);direction=1 if total>0 else -1
            reverse=sum(max(0,-direction*v) for v in delta)
            if reverse>1.6:reject('direction_reversal');continue
            turns=abs(total)/math.tau
            if turns<.6:reject('insufficient_turns');continue
            if turns>=1.88:
                progress=0.;k=0
                for k,v in enumerate(delta,1):
                    progress+=v*direction
                    if progress>=math.tau:break
                if k<8 or n-k<8:reject('insufficient_circle_samples');continue
                first=a[:k+1];second=a[k:]
                c1=((min(p[0] for p in first)+max(p[0] for p in first))*.5,(min(p[1] for p in first)+max(p[1] for p in first))*.5)
                c2=((min(p[0] for p in second)+max(p[0] for p in second))*.5,(min(p[1] for p in second)+max(p[1] for p in second))*.5)
                if math.hypot((c1[0]-c2[0])/rx,(c1[1]-c2[1])/ry)>.7:reject('circle_center_drift');continue
            if turns>best:best=turns;best_window=t-samples[offset][0]
        self.result=(best,best>=.88 and t-self.moving<.18)
        self.diagnostic={'reason':'two_circles' if best>=1.88 else 'turning' if best else 'fit_rejected',
            'turns':round(best,3),'samples':len(samples),'checks':checks,
            'window_seconds':round(best_window,3),'reject_reason':max(rejects,key=rejects.get) if rejects else None}
        return self.result

class CircleDetector:
    """Turn either wrist twice in one direction, without a pause or tracking gap."""
    def __init__(self,hands=('left','right')):
        if not hands or any(h not in ('left','right') for h in hands):raise ValueError('Unsupported control hand')
        self.tracks={h:_Track() for h in hands};self.last_t=None;self.cooldown_until=-1e9;self.reference=None;self.body_seen=-1e9
    def reset(self):
        for track in self.tracks.values():track.reset()
        self.last_t=None;self.reference=None;self.body_seen=-1e9
    def update(self,points,t,width=1920,height=1080):
        out={'open':False,'control_active':False,'hand':None,'turns':0.,'diagnostic':{'reason':'no_observation'}}
        try:t=float(t)
        except (TypeError,ValueError):self.reset();return out
        if not math.isfinite(t):self.reset();return out
        if self.last_t is not None and t<=self.last_t:out['diagnostic']={'reason':'duplicate_timestamp'};return out
        if self.last_t is not None and t-self.last_t>.4:self.reset()
        self.last_t=t
        if t<self.cooldown_until:out['diagnostic']={'reason':'cooldown'};return out
        a,b=_point(points,5),_point(points,6)
        aspect=float(width)/max(1,float(height))
        if a is not None and b is not None:
            a=(a[0]*aspect,a[1]);b=(b[0]*aspect,b[1]);observed_scale=math.hypot(a[0]-b[0],a[1]-b[1]);observed_center=((a[0]+b[0])*.5,(a[1]+b[1])*.5)
            if .035<observed_scale<.8:
                self.body_seen=t
                if self.reference is None:self.reference=(observed_center,observed_scale)
                elif math.hypot(observed_center[0]-self.reference[0][0],observed_center[1]-self.reference[0][1])>self.reference[1]*1.5:
                    self.reset();self.reference=(observed_center,observed_scale);self.body_seen=t
        if self.reference is None or t-self.body_seen>.20:
            for tr in self.tracks.values():tr.missing(t)
            out['diagnostic']={'reason':'shoulders_missing'}
            return out
        center,scale=self.reference
        for hand,index in [('left',9),('right',10)]:
            if hand not in self.tracks:continue
            p=_point(points,index,.1);track=self.tracks[hand]
            if p is None:track.missing(t);out['diagnostic']={'reason':'wrist_missing','hand':hand};continue
            turns,active=track.update(t,((p[0]*aspect-center[0])/scale,(p[1]-center[1])/scale))
            if out['diagnostic'].get('turns',-1)<=turns:out['diagnostic']=dict(track.diagnostic,hand=hand)
            if active and turns>out['turns']:out.update(control_active=True,hand=hand,turns=turns)
            if turns>=1.88:
                out.update(open=True,control_active=True,hand=hand,turns=turns)
                self.reset();self.cooldown_until=t+2.;break
        return out
