"""Same causal three-sample medians/derivatives, batched by window length."""
import numpy as np

def trajectory_speeds(poses,times,action,max_gap=.16,gap_times=()):
    p,t=np.asarray(poses,float),np.asarray(times,float)
    if action=='A04':c=p[:,:2].mean(1)[:,None,:];active='torso'
    elif action in ('A02','A05'):
        excursions=np.linalg.norm(np.percentile(p[:,4:6],95,axis=0)-np.percentile(p[:,4:6],5,axis=0),axis=1)
        side=int(np.argmax(excursions));c=p[:,4+side:5+side];active='left' if side==0 else 'right'
    else:c,active=p[:,4:6],'both'
    if not len(t):return [None],active
    gaps=np.asarray(gap_times,float);dt=np.diff(t)
    broken=np.array([False]+[bool(((gaps>t[i-1])&(gaps<=t[i])).any()) for i in range(1,len(t))])
    starts=np.r_[True,(dt>max_gap)|broken[1:]]
    indices=np.arange(len(t));origin=np.maximum.accumulate(np.where(starts,indices,0))
    lengths=np.minimum(indices-origin+1,3);smooth=np.empty_like(c)
    one=indices[lengths==1];smooth[one]=c[one]
    for length in (2,3):
        end=indices[lengths==length]
        if len(end):
            windows=c[end[:,None]+np.arange(1-length,1)]
            smooth[end]=np.median(windows,axis=1)
    valid=(dt>0)&(dt<=max_gap)&~broken[1:]
    speeds=[None]*len(t)
    indices=np.flatnonzero(valid)+1
    values=np.linalg.norm(smooth[indices]-smooth[indices-1],axis=-1).mean(axis=-1)/dt[indices-1]
    for i,value in zip(indices,values):speeds[int(i)]=float(value)
    return speeds,active
