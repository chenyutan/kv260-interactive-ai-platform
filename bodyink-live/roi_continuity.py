"""Causal whole-pose ROI continuity. Never edits points or scores."""
import math
import numpy as np

class ContinuousRoi:
    def __init__(self,grace_seconds=.10,return_seconds=.25,agreement=.08,max_gap=.20,probe_seconds=.25):
        self.grace=grace_seconds;self.return_seconds=return_seconds
        self.agreement=agreement;self.max_gap=max_gap;self.probe_seconds=probe_seconds;self.reset()

    def reset(self):
        self.index=None;self.last_time=None;self.last_reliable=None;self.return_since=None
        self.last_probe=None;self.geometry=None

    def prepare(self,timestamp,width,height):
        stamp=float(timestamp)
        if not math.isfinite(stamp):raise ValueError('Invalid PTS')
        reset=(self.last_time is not None and (stamp<=self.last_time or stamp-self.last_time>self.max_gap))
        reset=reset or (self.geometry is not None and self.geometry!=(width,height))
        if reset:self.reset()
        self.geometry=(width,height)
        return reset

    def probe_due(self,timestamp):
        return self.last_probe is None or timestamp-self.last_probe>=self.probe_seconds

    @staticmethod
    def disagreement(a,b,width,height):
        if a is None or b is None:return math.inf
        scale=np.array([width,height],float)
        if not np.isfinite(a[5:13]).all() or not np.isfinite(b[5:13]).all():return math.inf
        if (a[5:13,:2]<0).any() or (b[5:13,:2]<0).any():return math.inf
        shoulder=min(np.linalg.norm((p[5,:2]-p[6,:2])*scale) for p in (a,b))
        if shoulder<18:return math.inf
        return float(np.linalg.norm((a[5:13,:2]-b[5:13,:2])*scale,axis=1).max()/shoulder)

    def single_arm_agreement(self,a,b,width,height):
        """Which A02 arms have same-frame observed agreement across both ROIs?

        A low-confidence inactive wrist must not erase a reliable active
        movement. This never grants class acceptance and never changes points.
        The complete original ROI disagreement flag remains unchanged.
        """
        if a is None or b is None:return []
        a=np.asarray(a,dtype=float);b=np.asarray(b,dtype=float)
        if a.shape!=(17,3) or b.shape!=(17,3):return []
        if not (math.isfinite(width) and math.isfinite(height) and width>0 and height>0):return []
        scale=np.array([width,height],float)
        torso=[5,6,11,12]
        for p in (a,b):
            core=p[torso]
            if (not np.isfinite(core).all() or (core[:,2]<.30).any() or
                    (core[:,:2]<0).any() or (core[:,:2]>1).any()):return []
        shoulders=[np.linalg.norm((p[5,:2]-p[6,:2])*scale) for p in (a,b)]
        shoulder=min(shoulders)
        if shoulder<18:return []
        sides=[]
        for side in (0,1):
            required=torso+[7+side,9+side]
            valid=True
            for p in (a,b):
                arm=p[[7+side,9+side]]
                if (not np.isfinite(arm).all() or (arm[:,2]<.20).any() or
                        (arm[:,:2]<0).any() or (arm[:,:2]>1).any()):valid=False
            if not valid:continue
            distance=np.linalg.norm((a[required,:2]-b[required,:2])*scale,axis=1)
            if float(distance.max())/shoulder<=self.agreement:sides.append(side)
        return sides

    def update(self,primary,secondary,qualities,timestamp,width,height,baseline_index):
        stamp=float(timestamp)
        reset=self.prepare(stamp,width,height)
        self.last_time=stamp
        valid=[q is not None and q['reliable'] for q in qualities]
        if qualities[0] is not None:self.last_probe=stamp
        gap=self.disagreement(primary,secondary,width,height)
        previous=self.index
        if previous is None:
            selected=baseline_index;reason='initial_selection'
        elif valid[previous]:
            selected=previous;reason='retain_reliable_roi'
            if previous==1 and valid[0] and gap<=self.agreement:
                if self.return_since is None:self.return_since=stamp
                if stamp-self.return_since>=self.return_seconds:
                    selected=0;reason='primary_agreement_confirmed'
            elif not (previous==1 and qualities[0] is None):self.return_since=None
        else:
            self.return_since=None;other=1-previous
            if valid[other]:
                if (self.last_reliable is not None and stamp-self.last_reliable<=self.grace
                        and gap>self.agreement):
                    selected=previous;reason='brief_disagreement_remains_unreliable'
                else:selected=other;reason='other_roi_required'
            else:
                selected=previous;reason='both_unreliable_keep_observation_source'
                counts=[q.get('visible_core_count',0) if q else 0 for q in qualities]
                # Severe partial-pose loss must not lock the preview to a ROI
                # indefinitely. Switch only for a large observed improvement;
                # the action engine still rejects this incomplete whole pose.
                if counts[previous]<=3 and counts[other]>=6:
                    selected=other;reason='recover_more_observed_joints_preview_only'
        changed=previous is not None and selected!=previous
        # Consumers must break motion history here, not treat a crop jump as speed.
        discontinuity=changed and gap>self.agreement
        retained=self.single_arm_agreement(primary,secondary,width,height) if discontinuity and not reset else []
        self.index=selected
        if valid[selected]:self.last_reliable=stamp
        return selected,dict(reason=reason,reliable=valid[selected],roi_changed=changed,
            preserve_a02_sides=retained,
            motion_history_discontinuity=discontinuity,disagreement_shoulders=gap if math.isfinite(gap) else None,
            stream_reset=reset,selected_index=selected)
