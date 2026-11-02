"""Pure, observed-only skeleton projection with a torso-anchored viewport."""
import math
import time


def finite_points(raw, minimum=.08):
    result=[]
    for index in range(17):
        try:
            item=raw[index]
            if any(isinstance(v,bool) for v in item[:3]):raise ValueError('boolean point')
            x,y,score=map(float,item[:3])
            valid=(all(math.isfinite(v) for v in (x,y,score)) and
                   0<=x<=1 and 0<=y<=1 and score>=minimum)
            result.append((x,y,score) if valid else None)
        except (TypeError,ValueError,IndexError,KeyError,OverflowError):result.append(None)
    return tuple(result)


def _anchor_transform(points,box,aspect=16/9):
    """Return a qualified torso transform or None; original gates unchanged."""
    a,b,c,d=box
    core=(5,6,11,12)
    if any(points[i] is None or points[i][2]<.15 for i in core):return None
    ls,rs,lh,rh=(points[i] for i in core)
    sx,sy=(ls[0]+rs[0])*aspect/2,(ls[1]+rs[1])/2
    hx,hy=(lh[0]+rh[0])*aspect/2,(lh[1]+rh[1])/2
    torso=math.hypot(sx-hx,sy-hy)
    shoulder_width=math.hypot((ls[0]-rs[0])*aspect,ls[1]-rs[1])
    if torso<.055 or torso>.75 or hy-sy<.025 or shoulder_width<.025 or shoulder_width>.9:return None
    center_x=(sx+hx)/2
    center_y=sy+.70*torso
    # Keep headroom for raised wrists and full marker radius. The envelope is
    # based on the torso, not changing limb visibility or current reach.
    scale=min((c-a)*.88/max(.36,torso*3.6),(d-b)*.88/max(.66,torso*3.5))
    return center_x,center_y,scale


def _fallback_transform(box,aspect):
    a,b,c,d=box
    return aspect/2,.5,min((c-a)*.94/aspect,(d-b)*.94)


def observed_transform(points,box,aspect=16/9):
    """Stateless compatibility projection; missing anchors use camera bounds."""
    return _anchor_transform(points,box,aspect) or _fallback_transform(box,aspect)


def _project(points,box,transform,mirror,aspect):
    cx,cy,scale=transform;a,b,c,d=box
    return tuple(None if p is None else
                 ((a+c)/2+(-1 if mirror else 1)*(p[0]*aspect-cx)*scale,
                  (b+d)/2+(p[1]-cy)*scale,p[2]) for p in points)


def project_observed(raw,box,mirror=True,aspect=16/9):
    points=finite_points(raw)
    return _project(points,box,observed_transform(points,box,aspect),mirror,aspect)


class ObservedViewport:
    """Display-only bounded camera transform; stores no joint or score history.

    A missing current joint always yields None, even while its old viewport is
    retained. This class must never feed scoring, counting or pose inference.
    Call reset on person/source/lesson changes; discontinuity does the same.
    """
    def __init__(self,hold_seconds=.4,time_constant=.30,max_log_scale_rate=.45,
                 max_pan_fraction_per_second=.30):
        if not (0<=hold_seconds<=.4 and time_constant>0 and max_log_scale_rate>0 and max_pan_fraction_per_second>0):
            raise ValueError('Invalid bounded viewport parameters')
        if not all(math.isfinite(float(v)) for v in (hold_seconds,time_constant,max_log_scale_rate,max_pan_fraction_per_second)):
            raise ValueError('Finite viewport parameters required')
        self.hold_seconds=float(hold_seconds);self.time_constant=float(time_constant)
        self.max_log_scale_rate=float(max_log_scale_rate)
        self.max_pan_fraction_per_second=float(max_pan_fraction_per_second)
        self.reset()

    def reset(self):
        self._transform=None;self._last_update=None;self._last_anchor=None;self._missing_since=None;self._key=None
        self.status='reset'

    @property
    def transform(self):
        """Current display transform for diagnostics, not a measured pose."""
        return self._transform

    def project(self,raw,box,mirror=True,aspect=16/9,*,now=None,discontinuity=False):
        now=time.monotonic() if now is None else now
        points=finite_points(raw)
        if isinstance(now,bool) or not isinstance(now,(float,int)) or not math.isfinite(now):
            self.reset()
            return _project(points,box,observed_transform(points,box,aspect),mirror,aspect)
        key=(tuple(box),float(aspect))
        elapsed=0. if self._last_update is None else now-self._last_update
        gap=(self._last_update is not None and (elapsed<0 or elapsed>self.hold_seconds+1e-9))
        # Hold duration starts at the first observed missing-anchor frame, not
        # the preceding good sample (important for sparse 5 Hz QA replays).
        anchor_expired=(self._missing_since is not None and now-self._missing_since>self.hold_seconds+1e-9)
        if discontinuity or key!=self._key or gap or anchor_expired:self.reset()
        self._key=key
        target=_anchor_transform(points,box,aspect)
        previous=self._transform
        if target is None:
            if self._last_anchor is not None and self._missing_since is None:self._missing_since=now
            if previous is not None and self._last_anchor is not None:
                self.status='held_missing_anchor'
            else:
                self._transform=_fallback_transform(box,aspect)
                self.status='fallback'
        else:
            if previous is None or self._last_anchor is None:
                self._transform=target;self.status='anchored'
            else:
                dt=max(0.,now-self._last_update) if self._last_update is not None else 0.
                alpha=-math.expm1(-dt/self.time_constant)
                dx=(target[0]-previous[0])*alpha;dy=(target[1]-previous[1])*alpha
                distance=math.hypot(dx,dy)
                width,height=box[2]-box[0],box[3]-box[1]
                budget=min(width,height)*self.max_pan_fraction_per_second*dt/previous[2]
                if distance>budget and distance>0:dx*=budget/distance;dy*=budget/distance
                change=(math.log(target[2])-math.log(previous[2]))*alpha
                bound=self.max_log_scale_rate*dt;change=max(-bound,min(bound,change))
                self._transform=(previous[0]+dx,previous[1]+dy,previous[2]*math.exp(change))
                self.status='smoothed_anchor'
            self._last_anchor=now
            self._missing_since=None
        self._last_update=now
        return _project(points,box,self._transform,mirror,aspect)


# Descriptive alias for callers which name the policy rather than its subject.
BoundedViewport=ObservedViewport
