"""Evidence-only A02 continuity across a crop switch; never edits an observation.

Temporal motion is different from same-frame crop agreement. Use a bounded
actual-PTS window, torso identity anchors and observed one-arm upward motion.
Only the caller's existing A02 branch can retain state; this cannot create an
action, synthesize a pose, relax confidence or extend a velocity gap.
"""
import math
import numpy as np


class TemporalA02Continuity:
    def __init__(self):
        self.reset()

    def reset(self):
        self.history=[];self.geometry=None

    @staticmethod
    def _valid(points, ids, score):
        q=points[:,ids]
        return bool(np.isfinite(q).all() and (q[:,:,2]>=score).all() and
                    ((q[:,:,:2]>=0)&(q[:,:,:2]<=1)).all())

    def observe(self,points,timestamp,width,height,*,discontinuity=False,stream_reset=False):
        result=dict(normal_sides=[],optional_sides=[],reason='no_crop_discontinuity')
        stamp=float(timestamp);geometry=(width,height)
        p=np.asarray(points,dtype=float)
        if (p.shape!=(17,3) or not math.isfinite(stamp) or
                not math.isfinite(width) or not math.isfinite(height) or min(width,height)<=0):
            self.reset();return dict(result,reason='invalid_observation')
        broken=(stream_reset or (self.geometry is not None and self.geometry!=geometry) or
                (self.history and not 0<stamp-self.history[-1][0]<=.16))
        if broken:self.reset()
        self.geometry=geometry;self.history.append((stamp,p.copy()))
        self.history=[entry for entry in self.history if entry[0]>=stamp-.30][-32:]
        if broken:return dict(result,reason='stream_gap_or_geometry_reset')
        if not discontinuity:return result
        # Pick actual representatives in a fixed 120--250 ms PTS window rather
        # than a frame count, so 15/20/30 Hz inputs use the same time scale.
        anchors=[i for i,(t,_) in enumerate(self.history) if stamp-.25<=t<=stamp-.12]
        if not anchors:return dict(result,reason='need_observed_time_span')
        start=anchors[0];window=self.history[start:]
        if len(window)<3:return dict(result,reason='need_three_observations')
        times=np.array([t for t,_ in window]);q=np.stack([pose for _,pose in window]);dt=np.diff(times)
        if not ((dt>0)&(dt<=.16)).all():return dict(result,reason='unmeasurable_interval')
        middle=min(range(1,len(window)-1),key=lambda i:abs(times[i]-(times[0]+stamp)*.5))
        chosen=[0,middle,len(window)-1]
        if not self._valid(q,[5,6,11,12],.30):return dict(result,reason='torso_unobserved')
        xy=q[:,:,:2]*[width,height]
        widths=np.linalg.norm(xy[:,5]-xy[:,6],axis=1);scale=float(widths.min())
        if scale<18 or float(widths.max()/scale)>1.18:return dict(result,reason='shoulder_scale_discontinuity')
        torso=xy[:,[5,6,11,12]];center=torso.mean(axis=1)
        translation=np.linalg.norm(np.diff(center,axis=0),axis=1)/scale
        shape=np.linalg.norm(np.diff(torso-center[:,None],axis=0),axis=2).max(axis=1)/scale
        translation_envelope=float(np.linalg.norm(center-center[0],axis=1).max()/scale)
        shape_envelope=float(np.linalg.norm((torso-center[:,None])-(torso[0]-center[0]),axis=2).max()/scale)
        if (translation>.08).any() or (shape>.15).any() or translation_envelope>.08 or shape_envelope>.15:
            return dict(result,reason='torso_identity_discontinuity',translation_envelope=translation_envelope,shape_envelope=shape_envelope)
        result.update(reason='observed_temporal_evidence',observed_span_s=stamp-times[0],
                      torso_translation_max=float(translation.max()),torso_shape_max=float(shape.max()),
                      torso_translation_envelope=translation_envelope,torso_shape_envelope=shape_envelope)
        shoulder_center=xy[:,[5,6]].mean(axis=1)
        for side in (0,1):
            wrist=9+side;elbow=7+side;other=10-side
            if not self._valid(q,[wrist],.20):continue
            relative=(xy[:,wrist]-shoulder_center)/scale
            steps=np.diff(relative,axis=0)
            # One noise allowance per fixed PTS window, not per frame. A short
            # measured velocity peak may reach 12 W/s, while the window's mean
            # displacement stays below 8 W/s. No velocity is exported as ink.
            if (np.linalg.norm(steps,axis=1)>np.maximum(.12,12.*dt)).any():continue
            if np.linalg.norm(relative[-1]-relative[0])>8.*(stamp-times[0])+.08:continue
            directed=np.diff(relative[chosen,1])
            if (directed>.01).any() or -float(directed.sum())<.12:continue
            if relative[0,1]-relative[-2,1]<.05:continue
            if float(np.max(relative[:,1]-np.minimum.accumulate(relative[:,1])))>.08:continue
            recent=[]
            opposite_observed=[]
            for i,t in enumerate(times):
                if self._valid(q[i:i+1],[other],.20):
                    other_y=(xy[i,other,1]-xy[i,[5,6],1].mean())/widths[i]
                    opposite_observed.append((t,other_y))
                    if stamp-t<=.16:recent.append(other_y>.45)
            if not recent or not recent[-1]:continue
            # A genuinely observed bilateral upward prefix is not a single-arm
            # continuity claim, even while the second wrist is still low.
            if (len(opposite_observed)>=2 and opposite_observed[-1][0]-opposite_observed[0][0]>=.06 and
                    opposite_observed[0][1]-opposite_observed[-1][1]>.08):continue
            result['optional_sides'].append(side)
            if self._valid(q,[elbow],.20):
                relative_elbow=(xy[:,elbow]-shoulder_center)/scale
                elbow_steps=np.linalg.norm(np.diff(relative_elbow,axis=0),axis=1)
                if (elbow_steps<=np.maximum(.12,8.*dt)).all():result['normal_sides'].append(side)
        return result
