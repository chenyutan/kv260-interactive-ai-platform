"""Experimental two-hypothesis action state machine; not deployed.

One hypothesis permits preparation changes, the other preserves genuine ongoing
motion. Both require the original readiness, trajectory and terminal evidence.
No model output is altered. Only one event may win each neutral-return cycle.
"""
import importlib.util,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent

def _load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module);return module

base=_load('_bodyink_original_action_hypothesis',HERE/'base_action_engine.py')
continuation=_load('_bodyink_continuing_action_hypothesis',HERE/'continuing_action_engine.py')
speed_profile=_load('_bodyink_batched_speed_profile',HERE/'speed_profile.py')
base.trajectory_speeds=speed_profile.trajectory_speeds
continuation.trajectory_speeds=speed_profile.trajectory_speeds
for _name in ('ACTIONS','BODY_IDS','descriptor','normalize_frame','trajectory_speeds','quality_thresholds'):
    globals()[_name]=getattr(base,_name)
ENGINE_VERSION='experimental_v52_batched_causal_speed_profile'

class ActionEngine:
    def __init__(self,templates,calibration,config=None):
        self._branches=[base.ActionEngine(templates,calibration,config=config),
                        continuation.ActionEngine(templates,calibration,config=config)]
        self._committed=None;self._display_index=0;self._cycle=0

    def __getattr__(self,name):
        return getattr(self._branches[self._display_index],name)

    def reset(self):
        for branch in self._branches:branch.reset()
        self._committed=None;self._display_index=0

    def _report(self,out):
        out=dict(out);out['engine']=ENGINE_VERSION
        out['hypotheses']=[dict(phase=e.phase,reason=e.last_rejection) for e in self._branches]
        out['committed_hypothesis']=self._committed
        return out

    def update(self,points17,timestamp,width,height):
        if self._committed is not None:
            index=self._committed
            out=self._branches[index].update(points17,timestamp,width,height)
            if out['reason'] in ('returned_to_neutral','nonmonotonic_timestamp_reset','invalid_timestamp'):self.reset()
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
            out['event']=event
            return self._report(out)
        self._display_index=1 if outputs[1]['phase']=='execute' and outputs[0]['phase'] in ('idle','prepare','ready') else 0
        return self._report(outputs[self._display_index])
