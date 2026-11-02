"""Offline control-motion envelopes, independent of filename positivity/direction.

These stages identify movement, not verified circles or successful menu events.
"""
import numpy as np

ENGINE='control_envelope_v2'

def control_phases(points,times,duration,meta,width,height):
    n=len(times);labels=np.full(n,'unknown',dtype=object)
    detail=dict(engine=ENGINE,events=[],verified=False)
    warnings=['控制动作按主手连续轨迹分段；正式执行仅表示样本动作区间，不代表有效控制指令；圈数、方向和正负标签仍需复核。']
    if meta.get('negative_type')=='BODYFREE' and meta.get('control_validity')=='NEG':
        detail.update(label_source='filename_unverified',protocol='free_activity')
        warnings.append('按文件名中的自由活动采集类型预标为无关活动，尚需人工核对；不推断准备、转圈或菜单事件。')
        return ['negative']*n,detail,warnings
    if n<5:return labels.tolist(),detail,warnings
    xy=points[:,:,:2]*[width,height]
    vis=(points[:,:,3]>.5)&np.isfinite(xy).all(axis=2)&((points[:,:,:2]>=0)&(points[:,:,:2]<=1)).all(axis=2)
    base=vis[:,11]&vis[:,12]&vis[:,23]&vis[:,24]
    scale=np.median(np.linalg.norm(xy[base,11]-xy[base,12],axis=1)) if base.any() else 0
    if scale<8:return labels.tolist(),detail,warnings
    center=(xy[:,23]+xy[:,24])/2
    scores=[]
    for wrist in [15,16]:
        good=base&vis[:,wrist];q=(xy[:,wrist]-center)/scale
        scores.append(float(np.linalg.norm(np.percentile(q[good],95,axis=0)-np.percentile(q[good],5,axis=0))) if good.sum()>4 else 0.)
    wrist=[15,16][int(np.argmax(scores))]
    good=base&vis[:,wrist];q=(xy[:,wrist]-center)/scale
    smooth=q.copy()
    for i,t in enumerate(times):
        a=np.searchsorted(times,t-.08);b=np.searchsorted(times,t+.08,side='right')
        if good[a:b].all():smooth[i]=np.median(q[a:b],axis=0)
    speed=np.full(n,np.nan)
    for i,t in enumerate(times):
        a=max(0,np.searchsorted(times,t-.10));b=min(n-1,np.searchsorted(times,t+.10))
        if b>a and good[a:b+1].all() and np.all(np.diff(times[a:b+1])<=.06):
            speed[i]=np.linalg.norm(smooth[b]-smooth[a])/(times[b]-times[a])
    moving=np.isfinite(speed)&(speed>.30)
    # Join brief speed dips, never a missing-track interval or timestamp break.
    ids=np.flatnonzero(moving)
    for a,b in zip(ids[:-1],ids[1:]):
        if times[b]-times[a]<=.65 and good[a:b+1].all() and np.all(np.diff(times[a:b+1])<=.06):moving[a:b+1]=True
    bouts=[];i=0
    while i<n:
        if not moving[i]:i+=1;continue
        j=i+1
        while j<n and moving[j] and times[j]-times[j-1]<=.06:j+=1
        path=float(np.linalg.norm(np.diff(smooth[i:j],axis=0),axis=1).sum())
        if times[j-1]-times[i]>=.25 and path>=.25:bouts.append((i,j,path))
        i=j
    detail.update(active_wrist=wrist,selection_scores=scores,
                  motion_bouts=[dict(start=float(times[a]),end=float(times[b]) if b<n else float(duration),path_shoulders=p) for a,b,p in bouts])
    # Ordinary jitter or a lone short lift is insufficient for a rotation draft.
    candidates=[x for k,x in enumerate(bouts)
                if x[2]>=(.65 if 0<k<len(bouts)-1 else 1.5)
                and times[x[1]-1]-times[x[0]]>=.8]
    if not candidates:return labels.tolist(),detail,warnings
    def action_score(bout):
        a,b,path=bout
        score=path
        # A large lowering movement can exceed a short sweep's path length.
        # Penalize descending into a relaxed wrist-near-hip posture. Likewise,
        # the first lift has no preceding prepared posture in a multi-bout clip.
        directness=np.linalg.norm(smooth[b-1]-smooth[a])/max(path,1e-6)
        if smooth[b-1,1]>-.4 and smooth[b-1,1]-smooth[a,1]>.75 and directness>.65:score*=.25
        if len(bouts)>=3 and bout==bouts[0]:score*=.25
        return score
    main=max(candidates,key=action_score);a,b,_=main
    # Some recordings pause between revolutions. A second large excursion is
    # part of the candidate, not the short lowering movement after it.
    members=[main]
    for x,y,path in bouts:
        if x<b:continue
        if (times[x]-times[b-1]<=1.5 and path>=max(3.,main[2]*.4)
                and times[y-1]-times[x]>=.8 and good[b:x+1].all()
                and np.all(np.diff(times[b-1:x+1])<=.06)):
            b=y;members.append((x,y,path))
        else:break
    for x,y,path in reversed(bouts):
        if y>a:continue
        if (times[a]-times[y-1]<=1.5 and path>=max(3.,main[2]*.4)
                and times[y-1]-times[x]>=.8 and good[y:a+1].all()
                and np.all(np.diff(times[y-1:a+1])<=.06)):
            a=x;members.append((x,y,path))
        else:break
    labels[good]='ready';labels[a:b]='execute'
    before=[x for x in bouts if x[1]<=a];after=[x for x in bouts if x[0]>=b]
    if before:
        p0,p1,_=before[-1];labels[:p0]='idle';labels[p0:p1]='prepare'
    labels[b:]='hold'
    if after:
        r0,r1,_=after[0];labels[r0:r1]='return';labels[r1:]='idle'
    # Other significant movement remains visible for review, not silently hidden
    # inside a guessed hold. Do not join separate bouts through long occlusion.
    for x,y,_ in bouts:
        if any((x,y)==m[:2] for m in members):continue
        if before and (x,y)==before[-1][:2]:continue
        if after and (x,y)==after[0][:2]:continue
        labels[x:y]='unknown'
    labels[~good]='unknown'
    detail['candidate_start']=float(times[a]);detail['candidate_end']=float(times[b]) if b<n else float(duration)
    return labels.tolist(),detail,warnings
