"""Shared offline/live ROI policy; raw joint scores are never replaced."""
import numpy as np
import time
from hourglass_select import SELECTOR_VERSION,choose,is_reliable

def description(net,rois):
    hourglass=net.info.get('score_semantics')=='native_heatmap_peak'
    size=256 if hourglass else 192
    return {'protocol_version':'bodyink-pose-v3',
        'selector_version':SELECTOR_VERSION if hourglass else 'movenet-dual-v1',
        'preprocess_version':'opencv-rgb-bilinear-v1/'+net.info.get('preprocess_version','movenet-mean-v1')+'/'+net.info.get('decoder','movenet-v1'),
        'rois':rois,'input_shape':[len(rois),size,size,3],'color_order':'RGB'}

def identity(pipeline):
    return {key:pipeline[key] for key in ['protocol_version','selector_version','preprocess_version','color_order']} | {'input_image_shape':pipeline['input_shape'][1:]}

def infer_hourglass(net,provider,rois,width,height,*,force_all=False,on_prediction=None):
    if not 1<=len(rois)<=2:raise ValueError('Hourglass uses one or two ROIs')
    poses=[];timings=[]
    for index,roi in enumerate(rois):
        if index and not force_all and is_reliable(poses[0]):break
        start=time.monotonic();rgb=provider(index,roi);prepared=time.monotonic()
        points,timing=net.predict(rgb)
        timing=dict(timing,preprocess_ms=(prepared-start)*1000)
        poses.append(points);timings.append(timing)
        if on_prediction:on_prediction(index)
    points,index,diagnostic=choose(poses[0],poses[1] if len(poses)>1 else None)
    x,y,w,h=rois[index];valid=(points[:,:2]>=0).all(axis=1)
    points[valid,:2]=(points[valid,:2]*[w,h]+[x,y])/[width,height]
    return points,index,poses,timings,diagnostic
