"""No-dwell trajectory recognition with one submission per movement cycle.

The optional repeated-action hypothesis still competes against all action
classes in DTW; it is not permission to accept the previous class blindly.
"""
import importlib.util,sys
import numpy as np
from pathlib import Path
HERE=Path(__file__).resolve().parent

def _load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module);return module

base=_load('_bodyink_original_action_hypothesis',HERE/'flow_base_action_engine.py')
continuation=_load('_bodyink_continuing_action_hypothesis',HERE/'flow_continuing_action_engine.py')
speed_profile=_load('_bodyink_batched_speed_profile',HERE/'flow_speed_profile.py')
base.trajectory_speeds=speed_profile.trajectory_speeds
continuation.trajectory_speeds=speed_profile.trajectory_speeds
for _name in ('ACTIONS','BODY_IDS','descriptor','normalize_frame','trajectory_speeds','quality_thresholds'):
    globals()[_name]=getattr(base,_name)
ENGINE_VERSION='flow_no_dwell_v25_temporal_single_raise'

def compact_chest_lift(pose):
    """Both forearms point inward to a compact chest preparation."""
    pose=np.asarray(pose,dtype=float)
    wrists=pose[4:6]-pose[:2].mean(0)
    elbows=pose[2:4]-pose[:2].mean(0)
    inward_forearms=(elbows[:,0]-wrists[:,0])*np.array([1.,-1.])
    return bool(abs(wrists[0,0]-wrists[1,0])<1.65 and (inward_forearms>.18).all() and inward_forearms.mean()>.25)

def compact_lift_returning(event):
    # A compact lift shares its upward prefix with chest preparation. A
    # measured downward return resolves it without imposing a timed hold.
    poses=np.asarray(event['pose'],dtype=float)
    if len(poses)<3:return False
    wrists=poses[-3:,4:6]-poses[-3:,:2].mean(axis=1,keepdims=True)
    steps=np.diff(wrists,axis=0)
    return bool((steps[:,:,1]>.01).all() and
                (wrists[-1,:,1]-wrists[0,:,1]>.10).all() and
                np.max(np.abs(wrists[-1,:,0]-wrists[0,:,0]))<.05)

def protect_lift_endpoint(branch):
    if getattr(branch,'_compact_lift_guard',False):return
    original=branch._complete
    def complete_distinct(t,candidates):
        event,reason=original(t,candidates)
        if event is not None and event['action']=='A06' and compact_chest_lift(event['pose'][-1]) and not compact_lift_returning(event):
            return None,'compact_chest_preparation_not_palms_up'
        return event,reason
    branch._complete=complete_distinct
    branch._compact_lift_guard=True

def recover_lowered_single_raise_start(branch):
    """Reacquire only an A02 side hypothesis from measured lowered hands.

    Preserve every original start first. The extra region is preparation,
    never a class verdict; existing quality, endpoint and all-class DTW gates
    still judge the actual subsequent movement.
    """
    original=branch._start_matches
    def matches(pose):
        found=original(pose)
        if found or not branch._neutral(pose):return found
        previous=branch.config['start_distance']
        branch.config['start_distance']=max(previous,.48)
        try:return original(pose)
        finally:branch.config['start_distance']=previous
    branch._start_matches=matches

class ActionEngine:
    def __init__(self,templates,calibration,config=None):
        self._source=(templates,calibration,config)
        self._last_action=None
        self._clock_origin=None;self._clock_slot=None;self._decision_output=None;self._input_timestamp=None
        self._branches=[base.ActionEngine(templates,calibration,config=config),
                        continuation.ActionEngine(templates,calibration,config=config)]
        self._add_anatomical_branches()
        self._primary_pool=self._branches[:2]
        self._anatomical_pool=self._branches[2:]
        self._repeat_pool={}
        for action in self._branches[0].enabled_actions:
            branch=base.ActionEngine(templates,calibration,config=config)
            branch._start_filter=lambda t,action=action:t.action==action
            branch._cache_start_geometry();protect_lift_endpoint(branch)
            self._repeat_pool[action]=branch
        self._committed=None;self._display_index=0;self._cycle=0

    def _add_anatomical_branches(self):
        templates,calibration,config=self._source
        for action,settings in [("A01",dict(start_distance=.45,endpoint_distance=.45,min_progress=.50)),("A03",dict(endpoint_distance=.45)),("A04",dict())]:
            options=dict(config or {});options.update(settings)
            branch=base.ActionEngine(templates,calibration,config=options)
            branch._start_filter=lambda t,action=action:t.action==action
            branch._cache_start_geometry()
            if action=="A04":
                # Torso sway uses shoulders/hips; arm confidence must not
                # cancel observable torso motion. Arm geometry still gates posture.
                branch.score_thresholds[2:6]=0.
                branch._torso_motion=True
            self._branches.append(branch)

        # A low outward hand position can match several preparation classes.
        # Keep the lift hypothesis independent of the primary class lock.
        # Endpoint, bilateral motion and all-class DTW gates still apply.
        options=dict(config or {});options.update(start_distance=.30,endpoint_distance=.42)
        branch=base.ActionEngine(templates,calibration,config=options)
        branch._start_filter=lambda t:t.action=='A06'
        branch._cache_start_geometry()
        original=branch._start_matches
        def lifted_start(pose,original=original,branch=branch):
            primary=self._branches[0]
            if primary.phase not in ('ready','execute') or not primary.start_candidates:return []
            if any(v.action not in ('A04','A02') for v in primary.start_candidates):return []
            if primary.start_pose is None:return []
            a=pose[:2].mean(0);b=primary.start_pose[:2].mean(0)
            if abs(np.arctan2(a[0],-a[1])-np.arctan2(b[0],-b[1]))>.15:return []
            wrists=pose[4:6]-pose[:2].mean(0)
            # This fallback is for outward palms; compact chest preparation
            # belongs to A01 and must not be interpreted as an upward lift.
            if wrists[0,0]<.65 or wrists[1,0]>-.65:return []
            old_distance=branch.config['start_distance']
            # A wider reacquisition region is only valid while both measured
            # wrists are below the chest, not on a raised expansion prefix.
            if branch.last_rejection=='preparation_tracking_lost_reset' and (wrists[:,1]>.65).all():branch.config['start_distance']=.42
            try:matches=original(pose)
            finally:branch.config['start_distance']=old_distance
            if matches:branch._lift_from_single_prepare=any(v.action=='A02' for v in primary.start_candidates)
            return matches
        branch._start_matches=lifted_start
        lift_complete=branch._complete
        def disambiguated_lift(t,candidates,branch=branch,complete=lift_complete):
            if getattr(branch,'_lift_from_single_prepare',False):
                wrists=branch.last_pose[4:6]-branch.last_pose[:2].mean(0)
                if (wrists[:,1]>-.04).any():return None,'await_distinct_bilateral_lift_endpoint'
            return complete(t,candidates)
        branch._complete=disambiguated_lift
        self._branches.append(branch)

        for side in (0,1):
            branch=base.ActionEngine(templates,calibration,config=config)
            branch._active_side=side
            # The inactive arm cannot invalidate a one-arm action. Actual
            # moving-arm samples still retain the original confidence gates.
            branch.score_thresholds[[3-side,5-side]]=0.
            branch._start_filter=lambda t,side=side:t.action=="A02" and int(np.argmax(np.linalg.norm(t.delta[4:6],axis=1)))==side
            branch._cache_start_geometry()
            recover_lowered_single_raise_start(branch)
            self._branches.append(branch)

        # Experimental only: retain measured wrist/torso when active elbow
        # confidence briefly falls, requiring an unmistakable high/low pair.
        for side in (0,1):
            options=dict(config or {});options.update(max_tracking_gap=.20)
            branch=base.ActionEngine(templates,calibration,config=options)
            branch._active_side=side;branch._a02_elbow_optional=True
            branch.score_thresholds[2:4]=0.
            branch.score_thresholds[5-side]=0.
            branch._start_filter=lambda t,side=side:t.action=='A02' and int(np.argmax(np.linalg.norm(t.delta[4:6],axis=1)))==side
            branch._cache_start_geometry();recover_lowered_single_raise_start(branch)
            start_matches=branch._start_matches
            def observed_start(pose,branch=branch,side=side,start_matches=start_matches):
                # A fully unobserved elbow is never permission to arm a trial.
                if not branch.raw_scores:return []
                scores=branch.raw_scores[-1][1]
                if min(scores[2+side],scores[4+side],scores[5-side])<.20:return []
                return start_matches(pose)
            branch._start_matches=observed_start
            original=branch._complete
            def complete_wrist(stamp,candidates,branch=branch,side=side,original=original):
                pose=branch.last_pose;shoulder=pose[:2].mean(0)
                wrist=pose[4+side]-shoulder
                origin=branch.start_pose[4+side]-branch.start_pose[:2].mean(0)
                scores={t:v for t,v in branch.raw_scores if branch.trajectory[0][0]<=t<=stamp}
                observed=list(scores.items())
                if not observed or np.mean([v[2+side]>=.20 for _,v in observed])<.60:
                    return None,'optional_elbow_not_enough_observed_samples'
                last_elbow=observed[0][0]
                for t,v in observed:
                    if v[2+side]>=.20:last_elbow=t
                    elif t-last_elbow>.60:
                        return None,'optional_elbow_unobserved_too_long'
                # A weak inactive-wrist estimate cannot establish the low arm.
                # Use a genuinely observed sample in the unchanged speed window;
                # it is a guard only, never a filled coordinate or speed input.
                other_observed=[p for t,p in branch.trajectory
                    if t>=stamp-branch.config['max_sample_gap'] and t in scores and scores[t][5-side]>=.20]
                if not other_observed:return None,'optional_elbow_other_hand_unobserved'
                other_pose=other_observed[-1]
                other=other_pose[5-side]-other_pose[:2].mean(0)
                if wrist[1]>-.60 or other[1]<.45 or origin[1]-wrist[1]<1.0:
                    return None,'optional_elbow_requires_distinct_high_low_arms'
                if np.linalg.norm(shoulder-branch.start_pose[:2].mean(0))>.35:
                    return None,'optional_elbow_requires_observed_stable_torso'
                return original(stamp,candidates)
            branch._complete=complete_wrist
            self._branches.append(branch)

        # A01 can remain observable from both wrists and torso when an elbow
        # is occluded. This fallback has a narrower physical start gate; it
        # cannot arm from the lowered hands used to prepare a torso tilt.
        options=dict(config or {});options.update(start_distance=.65,endpoint_distance=.50)
        branch=base.ActionEngine(templates,calibration,config=options)
        branch.score_thresholds[2:4]=0.
        branch._scale_tolerance=1.7;branch._elbow_optional=True
        branch._start_filter=lambda t:t.action=="A01"
        branch._cache_start_geometry()
        original=branch._start_matches
        def wrist_start(pose,branch=branch,original=original):
            wrists=pose[4:6]-pose[:2].mean(0)
            if not branch.raw_scores or not (branch.raw_scores[-1][1][2:4]<.20).any():return []
            if (wrists[:,1]>.35).any() or (wrists[:,1]<-1.1).any():return []
            if abs(wrists[0,0]-wrists[1,0])>2.8:return []
            return original(pose)
        branch._start_matches=wrist_start
        self._branches.append(branch)

        # A bilateral raise remains observable with weak elbows when torso and
        # both wrists are measured. Keep its longer identity gap local to this
        # hypothesis; velocity gaps still use the unchanged 160 ms limit.
        options=dict(config or {});options.update(start_distance=.55,endpoint_distance=.55,max_tracking_gap=.55)
        branch=base.ActionEngine(templates,calibration,config=options)
        branch.score_thresholds[2:4]=0.
        branch._start_filter=lambda t:t.action=='A03'
        branch._cache_start_geometry()
        complete=branch._complete
        def raised_complete(t,candidates,branch=branch,complete=complete):
            wrists=branch.last_pose[4:6]-branch.last_pose[:2].mean(0)
            if (wrists[:,1]>-.60).any():return None,'bilateral_raise_not_above_shoulders'
            return complete(t,candidates)
        branch._complete=raised_complete
        self._branches.append(branch)

        # Bringing both hands together in front of the chest is also the
        # preparation for expansion. It is not a distinct palms-up endpoint.
        for branch in self._branches:protect_lift_endpoint(branch)

    def __getattr__(self,name):
        return getattr(self._branches[self._display_index],name)

    def reset(self, preserve_a02_sides=(), preserve_a02_optional_sides=()):
        # Preserve observation cadence across ROI resets; never replay an event.
        self._decision_output=None
        committed = self._committed is not None
        # Fixed templates/native geometry are immutable. Reset only temporal
        # state; rebuilding every hypothesis here stalls live inference.
        self._branches=list(self._primary_pool)
        if self._last_action is not None:self._branches.append(self._repeat_pool[self._last_action])
        self._branches.extend(self._anatomical_pool)
        # A ROI switch may disagree only on the irrelevant arm. Retain a
        # single-arm trial solely when the caller measured same-frame
        # agreement of BOTH torso anchors and that side's elbow/wrist.
        # No evidence, any active-side discrepancy, or a committed event
        # follows the established full-reset behaviour.
        retain={side for side in preserve_a02_sides if type(side) is int and side in (0,1)} if isinstance(preserve_a02_sides,(tuple,list)) else set()
        optional={side for side in preserve_a02_optional_sides if type(side) is int and side in (0,1)} if isinstance(preserve_a02_optional_sides,(tuple,list)) else set()
        for branch in self._branches:
            allowed=retain | (optional if getattr(branch,'_a02_elbow_optional',False) else set())
            safe=(not committed and getattr(branch,'_active_side',None) in allowed and
                  branch.phase in ('ready','execute') and branch.start_candidates and
                  all(t.action=='A02' for t in branch.start_candidates))
            if not safe:branch.reset()
        self._committed=None;self._display_index=0
        if committed:
            self._committed=0
            self._branches[0].phase="return"

    def _report(self,out):
        out=dict(out);out['engine']=ENGINE_VERSION
        out['hypotheses']=[dict(phase=e.phase,reason=e.last_rejection) for e in self._branches]
        out['committed_hypothesis']=self._committed
        return out

    def update(self,points17,timestamp,width,height):
        # Classification runs at a common observation cadence regardless of
        # capture rate. Select real points/PTS only; never invent coordinates
        # or velocity samples. Rendering and pose display remain independent.
        try:t=float(timestamp)
        except (ValueError,TypeError):
            self._clock_origin=None;self._clock_slot=None;self._input_timestamp=None
            return self._update_observation(points17,timestamp,width,height)
        monotonic=np.isfinite(t) and (self._input_timestamp is None or t>self._input_timestamp)
        self._input_timestamp=t if np.isfinite(t) else None
        if monotonic:
            if self._clock_origin is None:self._clock_origin=t
            # Fixed time bins, not a minimum inter-frame gap: a 35 Hz camera
            # must yield ~30 decisions/s rather than accidentally halving to 17.5.
            slot=int(np.floor((t-self._clock_origin)*30.+1e-6))
            if self._clock_slot==slot:
                if self._decision_output is not None:
                    out=dict(self._decision_output);out['event']=None
                    return out
                return self._report(self._branches[0]._result(dict(valid=False,min_score=0.,reason='awaiting_observation'), 'awaiting_observation'))
            self._clock_slot=slot
        else:self._clock_origin=None;self._clock_slot=None
        out=self._update_observation(points17,timestamp,width,height)
        self._decision_output=dict(out);self._decision_output['event']=None
        return out

    def _update_observation(self,points17,timestamp,width,height):
        # Cache identical normalization requests only within this input frame.
        # Hypotheses keep separate scales, thresholds and state; no history or
        # low-confidence coordinates are synthesized by this optimization.
        cache={}
        def normalize(points,w,h,scale=None,min_score=.30,min_distal_score=None):
            if np.isscalar(min_score):
                return base.normalize_frame(points,w,h,scale,min_score,min_distal_score)
            key=(scale,np.asarray(min_score,dtype=float).tobytes())
            if key not in cache:cache[key]=base.normalize_frame(points,w,h,scale,min_score,min_distal_score)
            p,measured,reason=cache[key]
            return (None if p is None else p.copy()),measured,reason
        for branch in self._branches:branch._normalizer=normalize
        if self._committed is not None:
            index=self._committed
            out=self._branches[index].update(points17,timestamp,width,height)
            if out['reason']=='returned_to_neutral':
                self._committed=None
                self.reset()
            elif out['reason'] in ('nonmonotonic_timestamp_reset','invalid_timestamp'):self.reset()
            return self._report(out)
        outputs=[e.update(points17,timestamp,width,height) for e in self._branches]
        events=[(i,o['event']) for i,o in enumerate(outputs) if o.get('event') is not None]
        if events:
            if len({e['action'] for _,e in events})>1:
                # Same-frame contradictory complete actions need a fresh cycle.
                # Neither branch is allowed to silently override the other.
                for branch in self._branches:branch._discard('competing_action_hypotheses')
                self._committed=0;self._display_index=0
                return self._report(self._branches[0]._result(outputs[0]['quality'],'competing_action_hypotheses'))
            # Both valid hypotheses can have different measured shoulder scales.
            # Their DTW costs are not a reason to replace an already complete
            # primary trajectory or change its real-time ink velocity scale.
            index,event=events[0]
            self._committed=index;self._display_index=index;self._cycle+=1
            out=dict(outputs[index]);event=dict(event)
            event.update(event_id=self._cycle,engine=ENGINE_VERSION,hypothesis=index)
            self._last_action=event['action']
            out['event']=event
            return self._report(out)
        self._display_index=next((i for i,o in enumerate(outputs) if o['phase']=='execute'),0)
        return self._report(outputs[self._display_index])
