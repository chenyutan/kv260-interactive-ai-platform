"""Infer the retained ROI first; probe recovery without doubling every frame."""
import time
import numpy as np
from hourglass_select import _quality,choose

SELECTOR_VERSION='hourglass-causal-roi-continuity-v2-partial-recovery'

def infer_continuous(net,provider,rois,width,height,policy,timestamp):
    if len(rois)!=2:raise ValueError('Continuity pipeline expects two candidate ROIs')
    stream_reset=policy.prepare(timestamp,width,height)
    preferred=policy.index if policy.index is not None else 0
    raw=[None,None];mapped=[None,None];qualities=[None,None];timings=[]
    def run(index):
        start=time.monotonic();rgb=provider(index,rois[index]);prepared=time.monotonic()
        p,t=net.predict(rgb);raw[index]=p;qualities[index]=_quality(p)
        timings.append(dict(t,roi_index=index,preprocess_ms=(prepared-start)*1000))
        p=p.copy();valid=(p[:,:2]>=0).all(axis=1);x,y,w,h=rois[index]
        p[valid,:2]=(p[valid,:2]*[w,h]+[x,y])/[width,height];mapped[index]=p
    run(preferred)
    if not qualities[preferred]['reliable'] or (preferred==1 and policy.probe_due(timestamp)):
        run(1-preferred)
    if raw[0] is not None and raw[1] is not None:
        _,baseline,_=choose(*raw)
    else:baseline=preferred
    selected,detail=policy.update(*mapped,qualities,timestamp,width,height,baseline)
    if mapped[selected] is None:raise RuntimeError('Selected an unevaluated ROI')
    indices=[i for i in (0,1) if raw[i] is not None]
    diagnostic=dict(selector_version=SELECTOR_VERSION,primary=qualities[0],secondary=qualities[1],
        selected_index=selected,reliable=detail['reliable'],evaluated_roi_indices=indices,
        inference_roi_order=[t['roi_index'] for t in timings],continuity=detail,
        motion_history_discontinuity=detail['motion_history_discontinuity'],stream_reset=stream_reset,
        fallback_requested=preferred==1 or not qualities[0]['reliable'],
        fallback_evaluated=raw[1] is not None,fallback_used=selected==1,reason=detail['reason'])
    # Preserve ROI-index order for diagnostics; timings carry explicit indices.
    timings.sort(key=lambda t:t['roi_index'])
    return mapped[selected],selected,[raw[i] for i in indices],timings,diagnostic
