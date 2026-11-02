"""Observed-frame dwell selection for the native menu. No camera IO."""
import math

from radial_layout import MODES
ZONES=((.04,.20,.33,.52),(.355,.20,.645,.52),(.67,.20,.96,.52),
       (.04,.57,.33,.89),(.355,.57,.645,.89),(.67,.57,.96,.89))

def point(points,index,score=.12):
    try:
        p=points[index]
        if len(p)<3 or not all(math.isfinite(float(v)) for v in p[:3]):return None
        if float(p[2])<score or not all(0<=float(v)<=1 for v in p[:2]):return None
        return float(p[0]),float(p[1])
    except (TypeError,ValueError,IndexError):return None

def body(points,aspect=16/9):
    a,b=point(points,5),point(points,6)
    if a is None or b is None:return None
    width=math.hypot((a[0]-b[0])*aspect,a[1]-b[1])
    if width<.035 or width>.8:return None
    return ((a[0]+b[0])*.5,(a[1]+b[1])*.5,width)

class DwellSelector:
    """Two-second dwell uses new, timely pose observations, never stale time."""
    def __init__(self,seconds=2.,choices=MODES[:len(ZONES)]):
        # The generic selector starts with the six rectangular secondary-menu
        # slots. The host explicitly supplies every radial main-menu choice,
        # its circle geometry and hit test before enabling selection.
        self.seconds=seconds;self.set_choices(choices)
    def set_choices(self,choices,zones=None,hit_test=None):
        values=tuple(choices)
        rectangles=tuple(zones) if zones is not None else ZONES
        if not 1<=len(values)<=len(rectangles) or len(set(values))!=len(values) or any(not isinstance(v,str) or not v for v in values):
            raise ValueError('Menu needs unique action identifiers with matching hit zones')
        if any(len(r)!=4 or not all(math.isfinite(v) and 0<=v<=1 for v in r) or r[0]>=r[2] or r[1]>=r[3] for r in rectangles):
            raise ValueError('Invalid menu rectangles')
        if hit_test is not None and not callable(hit_test):raise ValueError('Invalid menu hit test')
        self.choices=values;self.zones=rectangles;self.hit_test=hit_test;self.reset()
    def reset(self):
        self.hand='right';self.opened=None;self.last_key=None;self.last_update=None
        self.hover=None;self.since=None;self.origin=None;self.armed=False;self.down_since=None;self.last_cursor=None;self.done=False
        self.cached=None;self.allow_cancel=False;self.person=None
    def open(self,hand,now,points=None):
        self.reset();self.hand='right';self.opened=now;self.allow_cancel=hand is not None
        if points is not None:
            self.origin=point(points,10,.15)
    def update(self,state,now):
        out=dict(cursor=None,hover=None,progress=0.,selected=None,cancel=False)
        if self.done or self.opened is None:return out
        if (not isinstance(state,dict) or state.get('stale') or state.get('available') is False
                or state.get('motion_history_discontinuity')):
            self.hover=self.since=self.cached=None;self.armed=False;self.origin=None;self.down_since=None
            return out
        person=(state.get('pid'),state.get('person_id'),state.get('track_id'))
        if self.person is not None and person!=self.person:
            self.hover=self.since=self.cached=None;self.armed=False;self.origin=None;self.down_since=None
        self.person=person
        key=(state.get('pid'),state.get('sequence'),state.get('timestamp_seconds')) if state else None
        if key is None or key==self.last_key:
            if self.last_update is None or now-self.last_update>.20:
                self.hover=self.since=self.cached=None
                return out
            return dict(self.cached) if self.cached else out
        if self.last_key is not None and key[0]!=self.last_key[0]:self.hover=self.since=None
        self.last_key=key
        if self.last_update is not None and now-self.last_update>.22:self.hover=self.since=None
        self.last_update=now
        points=state.get('points17');torso=body(points)
        if torso is None:self.hover=self.since=self.cached=None;self.down_since=None;return out
        wp=point(points,10,.15)
        if wp is None:self.hover=self.since=self.cached=None;self.down_since=None;return out
        # Camera is not mirrored, while on-screen gesture navigation is mirrored.
        cx,cy,scale=torso
        lowered=wp[1]>cy+1.6*scale
        if self.allow_cancel and lowered:
            if self.down_since is None:self.down_since=now
            if now-self.down_since>=.6:
                self.done=True;out['cancel']=True;return out
        else:self.down_since=None
        cursor=(max(0.,min(1.,.5-(wp[0]-cx)*(16/9)/scale/3.2)),max(0.,min(1.,.35+(wp[1]-cy)/scale/2.6)))
        out['cursor']=cursor;self.last_cursor=cursor
        if not self.armed:
            if self.origin is None:self.origin=wp
            moved= math.hypot((wp[0]-self.origin[0])*(16/9),wp[1]-self.origin[1])>.12*scale
            if now-self.opened>=.4 and moved:self.armed=True
            else:self.cached=out;return out
        index=(self.hit_test(*cursor) if self.hit_test is not None else
               next((i for i,(x0,y0,x1,y1) in enumerate(self.zones[:len(self.choices)]) if x0<=cursor[0]<=x1 and y0<=cursor[1]<=y1),None))
        if index is not None and index not in range(len(self.choices)):index=None
        if index is None:self.hover=self.since=None;self.cached=out;return out
        if index!=self.hover:self.hover=index;self.since=now
        out.update(hover=index,progress=min(1.,(now-self.since)/self.seconds))
        if out['progress']>=1:
            out['selected']=self.choices[index];self.done=True
        self.cached=out
        return out
