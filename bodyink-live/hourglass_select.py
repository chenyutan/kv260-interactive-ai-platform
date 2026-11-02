"""One whole Hourglass pose, with quality-triggered ROI fallback.

No action, identity, file name or expected movement is an input. Both candidate
poses retain native heatmap peaks and their own ROI-normalized XY coordinates.
The caller maps the selected whole pose back to the original image afterwards.
"""
from __future__ import annotations

import math
import numpy as np

from action_engine import BODY_IDS, quality_thresholds

SELECTOR_VERSION = 'hourglass-fullheight-quality-fallback-v1'
SCORE_SEMANTICS = 'native_heatmap_peak'
QUALITY_CONFIG = {'min_score':.30,'min_distal_score':.20}


def rois(width,height):
    """Primary full-height square and smaller upper-body square, centered."""
    if (not isinstance(width,(int,np.integer)) or not isinstance(height,(int,np.integer))
            or min(width,height)<32):
        raise ValueError('Expected integer frame geometry of at least 32 pixels')
    full=min(int(width),int(height))
    upper=max(16,int(full*.8))
    return [((int(width)-full)//2,0,full,full),
            ((int(width)-upper)//2,0,upper,upper)]


def _quality(points):
    try:
        p=np.asarray(points,float)
    except (ValueError,TypeError):
        p=np.empty((0,))
    if p.shape!=(17,3):
        return {'reliable':False,'min_peak':None,'min_score_threshold_ratio':None,
                'valid_coordinate_count':0,'visible_core_count':0,'reason':'expected_17x3'}
    p=p[list(BODY_IDS)]
    finite=np.isfinite(p).all(axis=1)
    coordinates=finite & ((p[:,:2]>=0)&(p[:,:2]<=1)).all(axis=1)
    thresholds=quality_thresholds(QUALITY_CONFIG)
    peak=float(p[:,2].min()) if np.isfinite(p[:,2]).all() else None
    ratio=float((p[:,2]/thresholds).min()) if np.isfinite(p[:,2]).all() else None
    # A large heatmap peak outside the crop cannot rescue an invalid coordinate.
    reliable=bool(coordinates.all() and ratio is not None and ratio>=1.)
    reason='ok' if reliable else ('body_coordinates_unavailable' if not coordinates.all()
                                else 'native_peaks_below_profile')
    return {'reliable':reliable,'min_peak':peak,'min_score_threshold_ratio':ratio,
            'valid_coordinate_count':int(coordinates.sum()),
            'visible_core_count':int((coordinates & (p[:,2]>=thresholds)).sum()),'reason':reason}


def is_reliable(points):
    return _quality(points)['reliable']


def choose(primary,secondary=None):
    """Return (whole raw ROI pose, ROI index, explicit fallback diagnostics).

    A reliable primary always wins. The caller need not run the second DPU job
    then. Otherwise use a reliable secondary. If neither is reliable, retain a
    complete-coordinate pose with the larger weakest relative peak solely for
    preview/diagnostics; the action engine must still reject its low quality.
    """
    first=_quality(primary)
    if np.asarray(primary).shape!=(17,3):
        raise ValueError('Primary pose must be 17x3')
    second=None if secondary is None else _quality(secondary)
    if secondary is not None and np.asarray(secondary).shape!=(17,3):
        raise ValueError('Secondary pose must be 17x3')
    if first['reliable']:
        index,reason=0,'primary_reliable'
    elif second is None:
        index,reason=0,'secondary_inference_required'
    elif second['reliable']:
        index,reason=1,'secondary_reliable'
    else:
        def comparison(q):
            value=q['min_score_threshold_ratio']
            # Invalid XY is always worse than a complete low-score pose.
            return value if q['valid_coordinate_count']==8 and value is not None and math.isfinite(value) else -math.inf
        index=1 if comparison(second)>comparison(first) else 0
        reason='neither_reliable_preview_only'
    selected=np.asarray(primary if index==0 else secondary,np.float32).copy()
    diagnostics={'selector_version':SELECTOR_VERSION,'score_semantics':SCORE_SEMANTICS,
                 'thresholds':quality_thresholds(QUALITY_CONFIG).tolist(),
                 'primary':first,'secondary':second,'selected_index':index,
                 'reliable':bool((first if index==0 else second)['reliable']),
                 'fallback_requested':not first['reliable'],
                 'fallback_evaluated':secondary is not None,
                 'fallback_used':index==1,'reason':reason}
    return selected,index,diagnostics
