"""Observed 2-D body controls; no camera, interpolation, or model dependency.

Shoulder motion controls lateral velocity. A small unguarded dip/rise maps to
a virtual jump; guarded dips stay crouched. Visible knee lifts map to kicks,
without claiming to reconstruct depth or infer an unseen ankle.
"""
import math
import statistics
from collections import deque


class BodyControl:
    # At 15 Hz this permits at most two missing observations between good
    # samples. Nothing is interpolated and no event is emitted while hidden.
    BRIEF_GAP=.20
    DIP_MAX_SECONDS=2.5
    DIP_HIP_GAP=.30

    def __init__(self, mirrored=True, aspect=16/9):
        self.mirrored=bool(mirrored);self.aspect=float(aspect)
        self.reset()

    def reset(self):
        self.last=None;self.previous=None;self.baseline=None
        self.travel=deque();self.travel_key=None;self.travel_active=False
        self.travel_diagnostic="need_motion_window"
        self.calibration=deque(maxlen=90);self.legs={};self.axis=0.
        self.vertical=None;self.vertical_calibration=deque(maxlen=90)
        self.body_width=None;self.scale_calibration=deque(maxlen=90)
        self.scale_reference=None;self.scale_samples=deque(maxlen=30)
        self.scale_candidate=None;self.scale_confirmed=False
        self.body_anchor=None;self.body_gap=None;self.body_gap_hips_only=False;self.guarded_return=False
        self.projection_anchor=None;self.menu_reference_paused=False
        self.scale_vertical_reference=None;self.scale_return_since=None
        self.dip=None;self.crouched=False;self.jump_until=-1.;self.jump_armed=False
        self.result=self._result('calibrating','need_neutral')

    @staticmethod
    def blocked(pose, menu_blocked=False, enabled=True):
        suite=pose.get('suite',{}) if isinstance(pose,dict) else {}
        return bool(menu_blocked or not enabled or
                    (isinstance(suite,dict) and suite.get('active') and
                     (suite.get('menu') or suite.get('control_active') or suite.get('mode')!='fight')))

    def _result(self,status,reason='',**extra):
        data=dict(move_axis=0.,crouch_guard=False,events=[],status=status,reason=reason,
                  capabilities=dict(move=False,jump=False,crouch=False,kick_left=False,kick_right=False))
        data.update(extra);return data

    @staticmethod
    def _point(points,index):
        p=points[index] if isinstance(points,(tuple,list)) and len(points)>index else None
        if (not isinstance(p,(tuple,list)) or len(p)<3 or
                not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in p[:3]) or
                not 0<=p[0]<=1 or not 0<=p[1]<=1 or p[2]<.20):return None
        return p

    def _distance(self,a,b):return math.hypot(a[0]-b[0],(a[1]-b[1])/self.aspect)

    def _knee_angle(self,h,k,a):
        # Apparent image-plane bend, not reconstructed 3-D anatomy.
        u=(h[0]-k[0],(h[1]-k[1])/self.aspect)
        v=(a[0]-k[0],(a[1]-k[1])/self.aspect)
        length=math.hypot(*u)*math.hypot(*v)
        if length<=1e-12:return 180.
        return math.degrees(math.acos(max(-1.,min(1.,sum(x*y for x,y in zip(u,v))/length))))

    def _lose(self,status,reason):
        self.reset();self.result=self._result(status,reason);return self.result

    def _clear_vertical(self):
        self.scale_vertical_reference=None;self.scale_return_since=None
        self.guarded_return|=self.crouched
        self.vertical=None;self.vertical_calibration.clear();self.legs.clear()
        self.dip=None;self.crouched=False;self.jump_armed=False

    def _dip_gap_limit(self):
        # Only an already observed descent may wait slightly longer for hips.
        # A lost shoulder, unconfirmed one-frame dip or guard retains the
        # ordinary limit. No coordinates or hidden movement are reconstructed.
        confirmed=bool(self.dip and self.dip['last_down']-self.dip['start']>=.045)
        return self.DIP_HIP_GAP if self.body_gap_hips_only and confirmed else self.BRIEF_GAP

    def _brief_missing(self,t,guard,hips_only=False):
        if guard:
            self.guarded_return|=bool(self.dip or self.crouched)
            self.dip=None
        # Keep a recently confirmed standing anchor through a brief gap, but
        # require a new visible two-sample lift on recovery. Never advance a
        # pending strike while the foot/body is hidden.
        for name,leg in list(self.legs.items()):
            leg['candidate']=None;leg['neutral_since']=None
            if t-leg['seen_at']>self.BRIEF_GAP+1e-9:self.legs.pop(name,None)
        if self.body_gap is None:
            self.body_gap=self.body_anchor;self.body_gap_hips_only=hips_only
        else:self.body_gap_hips_only&=hips_only
        if self.baseline is None or self.body_gap is None or t-self.body_gap[0]>self._dip_gap_limit()+1e-9:
            self._clear_vertical();self.body_anchor=None;self.body_gap=None;self.body_gap_hips_only=False
            return False
        return True

    def _resume_body(self,t,center,width,hip):
        if self.body_gap is not None:
            old_t,old_center,old_width,old_hip=self.body_gap
            torso=(self.vertical or self.baseline or {}).get('torso') or old_width
            coherent=(t-old_t<=(.25 if self.menu_reference_paused else self._dip_gap_limit())+1e-9 and abs(width/old_width-1)<=.30 and
                self._distance(center,old_center)<=.70*min(width,old_width) and
                self._distance(hip,old_hip)<=.70*min(width,old_width) and
                abs((center[1]-old_center[1])-(hip[1]-old_hip[1]))<=.24*torso)
            if not coherent:self._clear_vertical()
            self.body_gap=None;self.body_gap_hips_only=False
        self.menu_reference_paused=False
        self.body_anchor=(t,center,width,hip)

    def _scale_state(self,t,width,metrics,center,neutral,guard):
        """A shoulder-width outlier is not evidence of whole-body depth change.

        Confirm common shoulder/hip/left-torso/right-torso scaling for >=100 ms.
        Window medians are measurement statistics, never invented joint points.
        """
        if metrics is None:return self.scale_confirmed
        median=lambda values:sorted(values)[len(values)//2]
        if self.scale_reference is None:
            self.scale_reference=dict(metrics);self.body_width=width
        self.scale_samples.append((t,metrics))
        while self.scale_samples and t-self.scale_samples[0][0]>.16:self.scale_samples.popleft()
        measured={key:median([r[1][key] for r in self.scale_samples]) for key in metrics}
        ratios=[measured[key]/self.scale_reference[key] for key in metrics]
        common_scale=median(ratios)
        common=(max(ratios)/min(ratios)<=1.12 and abs(common_scale-1)>.08 and
                all(abs(r-1)>.08 and (r-1)*(common_scale-1)>0 for r in ratios))
        if common:
            self.scale_return_since=None
            direction=1 if common_scale>1 else -1
            if self.scale_candidate is None or self.scale_candidate[1]!=direction:
                self.scale_candidate=(t,direction)
            if t-self.scale_candidate[0]>=.10 and not self.scale_confirmed:
                old_vertical=dict(self.vertical) if self.vertical else None
                self.scale_confirmed=True;self._clear_vertical()
                self.scale_vertical_reference=old_vertical
        else:
            self.scale_candidate=None
            # Reject the former depth hypothesis only after independent
            # torso lengths and actual shoulders/hips return to its trusted
            # standing reference. An unseen/unfinished dip is never resumed.
            old=self.scale_vertical_reference
            hip=self.body_anchor[3] if self.body_anchor else None
            returned=bool(old and hip and neutral and
                all(abs(measured[k]/self.scale_reference[k]-1)<.06
                    for k in ('left_torso','right_torso')) and
                abs(center[1]-old['center'][1])<.08*old['torso'] and
                abs(hip[1]-old['hip'][1])<.08*old['torso'])
            if self.scale_confirmed and returned:
                if self.scale_return_since is None:self.scale_return_since=t
                if t-self.scale_return_since>=.10:
                    self._clear_vertical()
                    self.vertical=dict(old);self.jump_armed=False
                    self.scale_confirmed=False;self.scale_candidate=None
                    self.scale_calibration.clear();self.scale_samples.clear()
            else:
                self.scale_return_since=None
                if self.scale_confirmed and old is None and all(abs(r-1)<.06 for r in ratios):
                    self.scale_confirmed=False;self.scale_calibration.clear()
        unsafe=common or self.scale_confirmed
        if self.scale_confirmed and neutral and not guard and not self.dip and not self.crouched:
            self.scale_calibration.append((t,measured,center))
            # Shoulder projection can jitter while the new body distance is
            # stationary. Require stable hip/torso size and body location.
            while self.scale_calibration and (
                any(max(r[1][k] for r in self.scale_calibration)/min(r[1][k] for r in self.scale_calibration)>1.10
                    for k in ('hip','left_torso','right_torso')) or
                any(self._distance(r[2],center)>.12*width for r in self.scale_calibration)):
                self.scale_calibration.popleft()
            if self.scale_calibration and t-self.scale_calibration[0][0]>=.60:
                self.scale_reference={key:median([r[1][key] for r in self.scale_calibration]) for key in metrics}
                self.body_width=self.scale_reference['shoulder']
                self.scale_calibration.clear();self.scale_samples.clear()
                self.scale_candidate=None;self.scale_confirmed=False;unsafe=False
        else:self.scale_calibration.clear()
        return unsafe

    def _pause_menu_candidate(self,pose,menu_blocked,enabled):
        # A briefly suspected circle is not an actual menu transition. Disarm
        # all movement edges immediately, but keep a recent neutral reference.
        # The timestamp of the last accepted sample is never advanced here,
        # so repeated packets cannot extend this bounded pause.
        suite=pose.get('suite',{}) if isinstance(pose,dict) else {}
        t=pose.get('timestamp_seconds') if isinstance(pose,dict) else None
        pid=(pose.get('pid'),pose.get('person_id'),pose.get('track_id')) if isinstance(pose,dict) else None
        seq=pose.get('sequence') if isinstance(pose,dict) else None
        rewind=bool(self.last and isinstance(seq,(int,float)) and
                    isinstance(self.last[1],(int,float)) and seq<self.last[1])
        candidate=(enabled and not menu_blocked and isinstance(suite,dict) and
            suite.get('active') and suite.get('mode')=='fight' and
            suite.get('control_active') and not suite.get('menu'))
        if not (candidate and self.baseline and self.last and pid==self.last[0] and
                isinstance(t,(int,float)) and math.isfinite(t) and
                0<=t-self.last[2]<=self.BRIEF_GAP+1e-9 and not rewind and
                not pose.get('stale') and pose.get('available') is not False and
                not pose.get('motion_history_discontinuity')):
            return self._lose('blocked','menu_or_disabled')
        self.guarded_return|=self.crouched
        self.dip=None;self.crouched=False;self.jump_armed=False;self.legs.clear()
        self.travel.clear();self.travel_key=None;self.travel_active=False;self.axis=0.
        if self.body_gap is None:self.body_gap=self.body_anchor
        self.body_gap_hips_only=False
        self.menu_reference_paused=True
        self.result=self._result('blocked','brief_menu_candidate')
        return self.result

    def _continuous_torso(self,t,ls,rs,lh,rh):
        # Shoulder projection changes during punches/guard. Only independent
        # visible hips and both torso sides may justify retaining calibration.
        old=self.projection_anchor
        if old is None or any(p is None for p in (ls,rs,lh,rh)) or t-old[0]>self.BRIEF_GAP:
            return False
        _,ols,ors,olh,orh=old
        width=self._distance(ols,ors)
        if width<=0:return False
        for a,b in ((lh,olh),(rh,orh)):
            if self._distance(a,b)>.30*width:return False
        for a,b,oa,ob in ((lh,rh,olh,orh),(ls,lh,ols,olh),(rs,rh,ors,orh)):
            previous=self._distance(oa,ob)
            if previous<=0 or not .80<=self._distance(a,b)/previous<=1.20:return False
        torso=(olh[1]+orh[1]-ols[1]-ors[1])/2
        shoulder_dy=(ls[1]+rs[1]-ols[1]-ors[1])/2
        hip_dy=(lh[1]+rh[1]-olh[1]-orh[1])/2
        return torso>0 and abs(shoulder_dy-hip_dy)<.16*torso

    def _consume_body(self,pose,*,guard=False,menu_blocked=False,enabled=True):
        if self.blocked(pose,menu_blocked,enabled):return self._pause_menu_candidate(pose,menu_blocked,enabled)
        if not isinstance(pose,dict) or pose.get('stale') or pose.get('available') is False:
            return self._lose('lost','missing_or_stale_pose')
        if pose.get('motion_history_discontinuity'):
            return self._lose('lost','pose_history_discontinuity')
        t=pose.get('timestamp_seconds');seq=pose.get('sequence')
        pid=(pose.get('pid'),pose.get('person_id'),pose.get('track_id'))
        if not isinstance(t,(int,float)) or not math.isfinite(t):return self._lose('lost','invalid_timestamp')
        key=(pid,seq,t)
        if key==self.last:
            return dict(self.result,events=[])
        if self.last:
            rewind=(isinstance(seq,(int,float)) and isinstance(self.last[1],(int,float)) and seq<self.last[1])
            if pid!=self.last[0] or rewind or not 0<t-self.last[2]<=.25:self.reset()
        self.last=key
        points=pose.get('points17')
        ls,rs=self._point(points,5),self._point(points,6)
        if ls is None or rs is None:
            if self._brief_missing(t,guard):
                self.result=self._result('uncertain','brief_shoulders_missing');return self.result
            return self._lose('lost','shoulders_missing')
        width=self._distance(ls,rs);center=((ls[0]+rs[0])/2,(ls[1]+rs[1])/2)
        lh,rh=self._point(points,11),self._point(points,12)
        if not .055<=width<=.60:
            if self._continuous_torso(t,ls,rs,lh,rh) and self._brief_missing(t,guard):
                self.result=self._result('uncertain','brief_shoulder_geometry');return self.result
            return self._lose('lost','invalid_shoulder_geometry')
        if self.previous:
            old_t,old_center,old_width=self.previous
            displaced=self._distance(center,old_center)>.70*min(width,old_width)
            width_changed=abs(width/old_width-1)>.30
            if width_changed and not displaced and self._continuous_torso(t,ls,rs,lh,rh):
                self.previous=(t,center,width)
                # These are visible measurements with a verified torso, not
                # a lost-body gap. Use their new projection for the next
                # continuity check without changing the neutral reference.
                self.body_anchor=(t,center,width,((lh[0]+rh[0])/2,(lh[1]+rh[1])/2))
                self.projection_anchor=(t,tuple(ls),tuple(rs),tuple(lh),tuple(rh))
                self.body_gap=None;self.body_gap_hips_only=False
                for leg in self.legs.values():leg['candidate']=None;leg['neutral_since']=None
                if guard:
                    self.guarded_return|=bool(self.dip or self.crouched)
                    self.dip=None;self.jump_armed=False
                self.result=self._result('uncertain','brief_shoulder_projection');return self.result
            if displaced or width_changed:
                self.reset();self.last=key
        self.previous=(t,center,width)
        hip=None
        if lh is not None and rh is not None:
            candidate=((lh[0]+rh[0])/2,(lh[1]+rh[1])/2)
            if .12*width<self._distance(lh,rh)<1.4*width and .09<candidate[1]-center[1]<.60:
                hip=candidate
        metrics=None;brief_hip=False
        if hip is not None:
            self._resume_body(t,center,width,hip)
            self.projection_anchor=(t,tuple(ls),tuple(rs),tuple(lh),tuple(rh))
            metrics=dict(shoulder=width,hip=self._distance(lh,rh),
                         left_torso=self._distance(ls,lh),right_torso=self._distance(rs,rh))
        else:brief_hip=self._brief_missing(t,guard,hips_only=True)
        observed={}
        neutral=True
        for name,hi,ki,ai in (('left',11,13,15),('right',12,14,16)):
            h,k,a=(self._point(points,j) for j in (hi,ki,ai))
            if hip is None or any(p is None for p in (h,k,a)):continue
            if not (.065*width<self._distance(h,k)<2.0*width and
                    .065*width<self._distance(k,a)<2.0*width):continue
            old=self.legs.get(name)
            if old:
                if (t-old['seen_at']>self.BRIEF_GAP+1e-9 or
                        abs(width/old['width']-1)>.30 or
                        self._distance(h,old['hip'])>.70*min(width,old['width'])):
                    self.legs.pop(name,None)
                elif self._distance(a,old['ankle'])>1.0*width:
                    self.legs.pop(name,None);continue
            observed[name]=(h,k,a)
            if k[1]-h[1]<width*.43 or a[1]-k[1]<width*.25:neutral=False
        if self.baseline is None:
            if self.calibration and self._distance(center,self.calibration[-1][1])>.09*width:
                self.calibration.clear()
            # Lateral movement is calibrated from shoulders independently of
            # legs/guard. Cropped knees or a held guard must not disable it.
            self.calibration.append((t,center,width,hip,observed,neutral and not guard and hip is not None,metrics))
            # Slow travel must not become a nominally stationary calibration
            # just because adjacent frames are close together.
            while self.calibration and (max(r[2] for r in self.calibration)/min(r[2] for r in self.calibration)>1.30 or
                    any(self._distance(r[1],center)>.25*width for r in self.calibration)):
                self.calibration.popleft()
            if t-self.calibration[0][0]<.60:
                self.result=self._result('calibrating','hold_neutral_briefly');return self.result
            rows=list(self.calibration)
            median=lambda values:sorted(values)[len(values)//2]
            self.baseline=dict(center=(median([r[1][0] for r in rows]),median([r[1][1] for r in rows])),
                               width=median([r[2] for r in rows]),hip=hip,
                               torso=hip[1]-center[1] if hip else None)
            if all(r[5] for r in rows):
                self.vertical=dict(self.baseline)
            metric_rows=[r[6] for r in rows if r[6] is not None]
            if metric_rows:
                self.scale_reference={key:median([r[key] for r in metric_rows]) for key in metric_rows[0]}
                self.body_width=self.scale_reference['shoulder']
            self.calibration.clear()
        b=self.baseline
        # Scale ambiguity concerns vertical/leg edges, not the independent
        # shoulder-center movement channel. Preserve its neutral reference.
        if self.body_width is None:self.body_width=b['width']
        scale=width/b['width']
        scale_unsafe=self._scale_state(t,width,metrics,center,neutral,guard)
        if self.vertical is None:
            if scale_unsafe or not neutral or guard or hip is None:self.vertical_calibration.clear()
            else:
                self.vertical_calibration.append((t,center,hip))
                while self.vertical_calibration and any(self._distance(r[1],center)>.12*width for r in self.vertical_calibration):
                    self.vertical_calibration.popleft()
                if t-self.vertical_calibration[0][0]>=.60:
                    centers=[r[1] for r in self.vertical_calibration]
                    hips=[r[2] for r in self.vertical_calibration]
                    neutral_center=tuple(statistics.median(p[i] for p in centers) for i in (0,1))
                    neutral_hip=tuple(statistics.median(p[i] for p in hips) for i in (0,1))
                    self.vertical=dict(center=neutral_center,hip=neutral_hip,
                        torso=statistics.median(h[1]-s[1] for s,h in zip(centers,hips)))
                    self.guarded_return=False;self.crouched=False
                    self.vertical_calibration.clear()
        # The public consume wrapper derives lateral velocity from recent
        # observed travel; a held offset from this baseline must not walk.
        self.axis=0.
        caps=dict(move=True,jump=bool(hip is not None and self.vertical and not scale_unsafe),crouch=bool(hip is not None and self.vertical and not scale_unsafe),
                  kick_left=not scale_unsafe and 'left' in observed,kick_right=not scale_unsafe and 'right' in observed)
        events=[];leg_lifted=False
        if guard and (self.dip or self.crouched):self.guarded_return=True;self.dip=None
        for name in ('left','right'):
            if scale_unsafe or name not in observed:
                old=self.legs.get(name)
                if old:
                    old['candidate']=None;old['neutral_since']=None
                    if t-old['seen_at']>self.BRIEF_GAP+1e-9:self.legs.pop(name,None)
                continue
            h,k,a=observed[name];old=self.legs.get(name)
            is_neutral=k[1]-h[1]>width*.43 and a[1]-k[1]>width*.25
            if old is None:
                old=dict(ankle=a,hip=h,width=width,seen_at=t,armed=False,neutral_since=t if is_neutral else None,
                         knee_gap=k[1]-h[1] if is_neutral else None,
                         ankle_gap=a[1]-h[1] if is_neutral else None,
                         ankle_y=a[1] if is_neutral else None,candidate=None)
                self.legs[name]=old
            old.update(ankle=a,hip=h,width=width,seen_at=t)
            if is_neutral and (old['knee_gap'] is None or k[1]-h[1]>=old['knee_gap']-.025):
                if old['neutral_since'] is None:old['neutral_since']=t
                if not old['armed'] and t-old['neutral_since']>=.10:
                    old.update(armed=True,knee_gap=k[1]-h[1],ankle_gap=a[1]-h[1],ankle_y=a[1],candidate=None)
            else:old['neutral_since']=None
            torso=(b['torso'] or b['width'])*scale
            rise=(old['knee_gap']-(k[1]-h[1]))/torso if old['knee_gap'] is not None else 0.
            ankle_rise=(old['ankle_gap']-(a[1]-h[1]))/torso if old['ankle_gap'] is not None else 0.
            # A deep squat shortens both gaps with planted feet. A kick also
            # needs an observed foot rising in the camera plane, including
            # when the arms remain in guard.
            foot_rise=(old['ankle_y']-a[1])/torso if old['ankle_y'] is not None else 0.
            # Knee flexion plus an actually rising foot are the two positive
            # observations. Hip travel can cancel much of ankle_gap during a
            # natural knee lift; use it only to reject opposite relative motion.
            # Once both knee and foot have started rising, modest hip travel
            # need not cancel the same lift. Keep positive relative travel,
            # visible evidence and capture-time confirmation throughout.
            ankle_threshold=0. if old['candidate'] is not None else .04
            lifted=rise>.27 and ankle_rise>ankle_threshold and foot_rise>.13
            support=observed.get('right' if name=='left' else 'left')
            anchor=old.get('candidate_support')
            # Continue an already observed positive knee/foot lift when hip
            # travel makes the relative foot term slightly negative. Require
            # independent visible support-foot and shoulder stability, plus
            # a bent target knee, within the original short capture window.
            # This cannot initiate a lift or infer an unseen foot.
            if (not lifted and old['candidate'] is not None and anchor and support
                    and 0<=t-old['candidate']<=self.BRIEF_GAP
                    and rise>.27 and foot_rise>.13 and ankle_rise>-.10
                    and self._knee_angle(h,k,a)<=160.
                    and self._distance(support[2],anchor['ankle'])*self.aspect<=.08*anchor['torso']
                    and self._distance(center,anchor['shoulder'])*self.aspect<=.08*anchor['torso']):
                lifted=True
            leg_lifted|=lifted
            if old['armed'] and lifted:
                if old['candidate'] is None:
                    old['candidate']=t
                    old['candidate_support']=(dict(ankle=tuple(support[2]),shoulder=tuple(center),torso=torso)
                                              if support else None)
                elif t-old['candidate']>=.045:
                    events.append('kick_'+name);old['armed']=False;old['candidate']=None
            elif not lifted:old['candidate']=None
        if caps['jump']:
            v=self.vertical
            sy=(center[1]-v['center'][1])/v['torso'];hy=(hip[1]-v['hip'][1])/v['torso']
            down=min(sy,hy)
            # Both channels must rise together. A single displaced shoulder
            # or hip must not complete a dip (nor unlock guarded return).
            returned=max(sy,hy)<.08 and abs(sy-hy)<.16
            if guard:
                self.dip=None;self.jump_armed=False
                self.crouched=down>(.08 if self.crouched else .16)
                if self.crouched:self.guarded_return=True
            else:
                if self.crouched or self.guarded_return:
                    # Rising from guarded crouch remains a return, not a jump.
                    self.dip=None;self.jump_armed=False
                    if returned:
                        self.crouched=False;self.guarded_return=False;self.jump_armed=True
                elif leg_lifted:self.dip=None;self.jump_armed=False
                elif self.dip is not None:
                    # One descent owns one candidate until observed return or
                    # expiry. A held squat must never restart a periodic timer.
                    if t-self.dip['start']>self.DIP_MAX_SECONDS:
                        self.dip=None;self.jump_armed=False
                    else:
                        if down>.16:self.dip.update(last_down=t,peak=max(down,self.dip['peak']))
                        if returned:
                            if (self.dip['last_down']-self.dip['start']>=.045 and
                                    t-self.dip['last_down']<.75 and t>self.jump_until):
                                events.append('jump');self.jump_until=t+.50
                            self.dip=None;self.jump_armed=True
                elif returned:self.jump_armed=True
                elif down>.16 and self.jump_armed and t>self.jump_until:
                    self.dip=dict(start=t,last_down=t,peak=down);self.jump_armed=False
        elif not (brief_hip or (scale_unsafe and not self.scale_confirmed)):
            self.dip=None;self.crouched=False;self.jump_armed=False
        self.result=self._result('tracking',events=events,move_axis=self.axis,
            crouch_guard=bool(guard and self.crouched and caps['crouch']),capabilities=caps,
            reason='brief_hip_missing' if brief_hip else 'scale_unstable_lateral_active' if scale_unsafe else
                   '' if caps['kick_left'] or caps['kick_right'] else 'legs_unavailable_upper_body_active')
        return self.result

    @staticmethod
    def _travel_slope(rows,key):
        entries=[(r['t'],r[key]) for r in rows if r.get(key) is not None]
        if len(entries)<3:return None
        mt=sum(t for t,_ in entries)/len(entries);mx=sum(x for _,x in entries)/len(entries)
        denominator=sum((t-mt)**2 for t,_ in entries)
        return sum((t-mt)*(x-mx) for t,x in entries)/denominator if denominator>0 else None

    def consume(self,pose,**kwargs):
        prior_key=self.travel_key
        result=self._consume_body(pose,**kwargs)
        if result['status']!='tracking' or not result['capabilities']['move']:
            self.travel.clear();self.travel_active=False;self.travel_key=None
            return result
        key=(pose.get('pid'),pose.get('person_id'),pose.get('track_id'),pose.get('sequence'),pose.get('timestamp_seconds'))
        if key==prior_key:return result
        self.travel_key=key
        t=pose['timestamp_seconds'];p=pose['points17']
        ls,rs,lh,rh,la,ra=[self._point(p,j) for j in (5,6,11,12,15,16)]
        if not(ls and rs):return result
        width=self._distance(ls,rs)
        hip=(lh[0]+rh[0])/2 if lh and rh else None
        torso=(lh[1]+rh[1]-ls[1]-rs[1])/2 if lh and rh else None
        row=dict(t=t,left=ls[0],right=rs[0],shoulder=(ls[0]+rs[0])/2,hip=hip,
                 width=width,torso=torso,
                 metrics=[width,self._distance(lh,rh),self._distance(ls,lh),self._distance(rs,rh)] if lh and rh else None,
                 left_foot=la[0] if la else None,right_foot=ra[0] if ra else None,
                 left_foot_y=la[1] if la else None,right_foot_y=ra[1] if ra else None)
        self.travel.append(row)
        while len(self.travel)>1 and t-self.travel[1]['t']>.28:self.travel.popleft()
        rows=[dict(r) for r in self.travel];axis=0.;why='stationary'
        # Compensate only a common body-size change across independent torso
        # measurements. Width alone also changes when a person turns.
        reference=rows[0]['metrics']
        if reference and min(reference)>0:
            for r in rows:
                if not r['metrics']:continue
                ratios=[x/y for x,y in zip(r['metrics'],reference)]
                scale=statistics.median(ratios)
                if min(ratios)>0 and max(ratios)/min(ratios)<=1.12:
                    for k in ('left','right','shoulder','hip','left_foot','right_foot'):
                        if r[k] is not None:r[k]=.5+(r[k]-.5)/scale
        if len(rows)>=3 and t-rows[0]['t']>=.12:
            sl,sr=(self._travel_slope(rows,k) for k in ('left','right'))
            ss=self._travel_slope(rows,'shoulder');hs=self._travel_slope(rows,'hip')
            coherent=sl is not None and sr is not None and sl*sr>0 and min(abs(sl),abs(sr))>=.25*max(abs(sl),abs(sr))
            if hs is not None:coherent &= ss*hs>0 and abs(hs)>=.35*abs(ss)
            speed=(.65*hs+.35*ss)/width if hs is not None else ss/width
            start=.35 if hs is not None else .45
            enough=abs(speed)>(.18 if self.travel_active else start)
            support=True
            feet_complete=all(r['left_foot'] is not None and r['right_foot'] is not None for r in rows)
            if feet_complete:
                foot_slopes=[self._travel_slope(rows,k) for k in ('left_foot','right_foot')]
                foot_speed=sum(foot_slopes)/2
                # A planted stance plus shifting shoulders/hips is a lean or
                # weight transfer, not a horizontal step. During a visible
                # foot lift, require the remaining foot to actually travel.
                lift=[]
                for k in ('left_foot_y','right_foot_y'):
                    ys=[r[k] for r in rows]
                    lift.append(max(ys)-min(ys)>.10*(torso or width))
                if any(lift):
                    support=any(not raised and s*speed>0 and abs(s)/width>.12
                                for raised,s in zip(lift,foot_slopes))
                    # In a real side-step one support foot stays planted while
                    # the other travels horizontally in the air. Accept that
                    # observed lateral stride, not an almost-vertical knee lift.
                    for raised,s,xk,yk in zip(lift,foot_slopes,
                            ('left_foot','right_foot'),('left_foot_y','right_foot_y')):
                        span=max(r[xk] for r in rows)-min(r[xk] for r in rows)
                        vertical=self._travel_slope(rows,yk)/self.aspect
                        support |= (raised and s*speed>0 and span>.16*width and
                                    abs(s)>1.25*abs(vertical) and abs(s)/width>.20)
                else:
                    support=foot_speed*speed>0 and abs(foot_speed)/width>.12
            if coherent and enough and support:
                axis=max(-1.,min(1.,speed))*(-1 if self.mirrored else 1)
                why='coherent_visible_travel'
            elif not support:why='planted_stance_or_lift'
            elif not coherent:why='incoherent_centers'
        else:why='need_motion_window'
        self.travel_active=axis!=0.;self.axis=axis
        self.result=dict(result,move_axis=axis)
        self.travel_diagnostic=why
        return self.result
