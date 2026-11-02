"""Causal display-only arm stabilization; never use results for counting.

raw_points preserves current accepted samples. points may have smoothed x/y,
but current scores never change. Missing samples remain missing by default.
Optional historical display continuation is always marked held with an age.
"""
import math

ARM_JOINTS=(7,8,9,10)
PARTNERS={7:8,8:7,9:10,10:9}
ASPECT=16/9


def _points(raw):
    result=[]
    for j in range(17):
        try:
            p=raw[j]
            if len(p)<3 or any(isinstance(v,bool) for v in p[:3]):raise ValueError()
            x,y,s=map(float,p[:3])
            if not all(math.isfinite(v) for v in (x,y,s)) or not (0<=x<=1 and 0<=y<=1 and 0<=s<=1):raise ValueError()
            result.append((x,y,s))
        except (TypeError,ValueError,IndexError,KeyError,OverflowError):result.append(None)
    return tuple(result)


def _core(points):
    if any(points[j] is None or points[j][2]<.15 for j in (5,6,11,12)):return None
    sx=(points[5][0]+points[6][0])*.5;sy=(points[5][1]+points[6][1])*.5
    hx=(points[11][0]+points[12][0])*.5;hy=(points[11][1]+points[12][1])*.5
    length=math.hypot((hx-sx)*ASPECT,hy-sy)
    if not .055<=length<=.75 or hy-sy<.025:return None
    return sx,sy,length


class DisplayFilter:
    """Bounded render history, not identity tracking or observation synthesis.

    Use one object per view. Reset on explicit source/visitor changes. frame_key
    may be (pid, sequence, timestamp); repeated draws cannot compound smoothing.
    hold_seconds defaults to zero; enabling it requires UI held/age styling.
    """
    def __init__(self,hold_seconds=0.,max_gap=.4):
        if not math.isfinite(hold_seconds) or not 0<=hold_seconds<=.2:raise ValueError('Hold must be within 0..0.2 seconds')
        if not math.isfinite(max_gap) or not 0<max_gap<=.8:raise ValueError('Invalid display gap')
        self.hold_seconds=float(hold_seconds);self.max_gap=float(max_gap);self.reset()

    def reset(self):
        self._shown=[None]*17;self._measured=[None]*17;self._seen=[None]*17
        self._last_now=None;self._frame_key=None;self._core=None;self._packet=None

    def sample(self,*,now,max_age=.65):
        """Read cached drawing data without advancing any filter/history clock.

        Return None for an expired/invalid whole packet. Held joints expire
        separately using their last real measurement time, even when no new
        receive callback arrives. The caller may redraw this at any frequency.
        """
        if (self._packet is None or self._last_now is None or isinstance(now,bool) or
                not isinstance(now,(int,float)) or not math.isfinite(now) or
                not isinstance(max_age,(int,float)) or not math.isfinite(max_age) or max_age<0 or
                now<self._last_now or now-self._last_now>max_age):return None
        result=dict(self._packet)
        points=list(result['points']);held=list(result['held']);ages=list(result['ages']);reasons=list(result['reasons'])
        for j in range(17):
            if self._seen[j] is not None:ages[j]=max(0.,now-self._seen[j])
            if held[j] and (ages[j] is None or ages[j]>self.hold_seconds):
                points[j]=None;held[j]=False;reasons[j]='hold_expired'
        result.update(points=tuple(points),held=tuple(held),ages=tuple(ages),reasons=tuple(reasons),packet_age=now-self._last_now)
        return result

    @staticmethod
    def _hard_key_change(previous,current):
        if not isinstance(previous,(tuple,list)) or not isinstance(current,(tuple,list)) or len(previous)<2 or len(current)<2:return False
        if previous[0]!=current[0]:return True
        return isinstance(previous[1],int) and isinstance(current[1],int) and current[1]<previous[1]

    def update(self,raw_display,*,now,frame_key=None,discontinuity=False):
        current=_points(raw_display)
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now):
            self.reset();return self._result(current,current,[False]*17,[0.]*17,[False]*17,['invalid_clock_passthrough']*17)
        dt=0. if self._last_now is None else now-self._last_now
        hard_reset=discontinuity or self._hard_key_change(self._frame_key,frame_key) or self._last_now is not None and dt<0
        if hard_reset:self.reset();dt=0.
        # Repeated GTK paints of one received sample must not invent samples or
        # gradually chase an outlier. Missing inputs are still handled below.
        duplicate=frame_key is not None and frame_key==self._frame_key and self._packet is not None
        if duplicate and current==self._packet['raw_points']:
            return self.sample(now=now)
        if self._last_now is not None and dt>self.max_gap:self.reset();dt=0.
        core=_core(current)
        if self._core and core:
            old=self._core
            jump=math.hypot((core[0]-old[0])*ASPECT,core[1]-old[1])/old[2]
            ratio=core[2]/old[2]
            if jump>.75 or not .65<=ratio<=1.5:
                # Show the newly observed body immediately, never lock to the
                # old one or reject a person. Only smoothing history is reset.
                self.reset();dt=0.
        measured_before=tuple(self._measured)
        output=[];held=[];ages=[];filtered=[];reasons=[]
        for j,p in enumerate(current):
            if p is None:
                age=math.inf if self._seen[j] is None else max(0.,now-self._seen[j])
                keep=self.hold_seconds>0 and self._measured[j] is not None and age<=self.hold_seconds
                output.append(self._measured[j] if keep else None)
                held.append(keep);ages.append(age if math.isfinite(age) else None);filtered.append(False)
                reasons.append('historical_measurement' if keep else 'missing')
                # Do not resume a smoothing trail after a missing current point.
                self._shown[j]=None
                continue
            previous=self._shown[j];result=p;why='current';changed=False
            if j in ARM_JOINTS and previous is not None and core is not None and dt>0 and p[2]<.45:
                dx=(p[0]-previous[0])*ASPECT;dy=p[1]-previous[1]
                relative=math.hypot(dx,dy)/core[2]
                partner=PARTNERS[j];q=current[partner];old=measured_before[j];oldq=measured_before[partner]
                coherent=False
                if old and q and oldq and min(p[2],q[2])>=.15:
                    a=((p[0]-old[0])*ASPECT,p[1]-old[1]);b=((q[0]-oldq[0])*ASPECT,q[1]-oldq[1])
                    la,lb=math.hypot(*a),math.hypot(*b)
                    coherent=(min(la,lb)>.12*core[2] and .4<=la/max(lb,1e-9)<=2.5 and
                              (a[0]*b[0]+a[1]*b[1])/max(la*lb,1e-9)>.75)
                isolated=relative>.45 and p[2]<.35 and not coherent
                tau=.50 if isolated else .035 if p[2]>=.35 or coherent else .055+.15*(.35-p[2])/.35
                alpha=-math.expm1(-dt/tau)
                if isolated and relative>0:alpha=min(alpha,1.75*dt/relative)
                result=(previous[0]+(p[0]-previous[0])*alpha,previous[1]+(p[1]-previous[1])*alpha,p[2])
                changed=abs(result[0]-p[0])+abs(result[1]-p[1])>1e-9
                why='isolated_low_score_step' if isolated else 'coherent_fast_motion' if coherent else 'confidence_smoothing'
            output.append(result);held.append(False);ages.append(0.);filtered.append(changed);reasons.append(why)
            self._shown[j]=result;self._measured[j]=p;self._seen[j]=now
        self._last_now=now;self._frame_key=frame_key;self._core=core
        self._packet=self._result(current,output,held,ages,filtered,reasons)
        return self._packet

    @staticmethod
    def _result(raw,points,held,ages,filtered,reasons):
        return dict(raw_points=tuple(raw),points=tuple(points),held=tuple(held),ages=tuple(ages),
                    filtered=tuple(filtered),reasons=tuple(reasons),display_only=True)
