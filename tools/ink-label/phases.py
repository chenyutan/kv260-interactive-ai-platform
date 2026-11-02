"""Action-conditioned offline segmentation. Uses signed pose progress, not longest motion."""
from __future__ import annotations
import numpy as np

ENGINE="pose_completion_v3"

def smooth(x, window=5):
    x=np.asarray(x,dtype=float); pad=window//2
    padded=np.pad(x,((pad,pad),)+((0,0),)*(x.ndim-1),mode="edge")
    return np.stack([np.median(padded[i:i+window],axis=0) for i in range(len(x))])

def spans(mask):
    boundaries=np.diff(np.r_[False,mask,False].astype(int))
    return list(zip(np.flatnonzero(boundaries==1),np.flatnonzero(boundaries==-1)))

def merge_bouts(bouts,times,progress,valid,gap=.55,backtrack=.05):
    result=[]
    for a,b in bouts:
        if result:
            p,q=result[-1]
            if times[a]-times[q-1]<=gap and valid[q:a+1].all() and np.min(progress[q-1:a+1])>=progress[q-1]-backtrack:
                result[-1]=(p,b); continue
        result.append((a,b))
    return result

def features(points,times,width,height,meta):
    xy=np.asarray(points[:,:,:2],float)*[width,height]
    valid=(points[:,:,3]>.5)&np.isfinite(points[:,:,:2]).all(axis=2)&(points[:,:,:2]>=0).all(axis=2)&(points[:,:,:2]<=1).all(axis=2)
    base=valid[:,[11,12,23,24]].all(axis=1)
    sh=(xy[:,11]+xy[:,12])/2; hip=(xy[:,23]+xy[:,24])/2
    widths=np.linalg.norm(xy[:,11]-xy[:,12],axis=1)
    early=base&(times<times[0]+3)
    scale=float(np.median(widths[early])) if early.any() else 0
    if scale<1: return None
    rel=(xy-sh[:,None,:])/scale
    body=smooth(rel[:,[13,14,15,16,23,24]])
    # A robust movement range avoids selecting the inactive hand because of jitter.
    side=meta.get("active_hand","unknown")
    if side not in ("left","right"):
        ranges=[]
        for j in (15,16):
            samples=rel[valid[:,j]&base,j]
            ranges.append(float(np.linalg.norm(np.percentile(samples,95,axis=0)-np.percentile(samples,5,axis=0))) if len(samples)>5 else 0)
        side="left" if ranges[0]>ranges[1] else "right"
    wrist=15 if side=="left" else 16
    act=meta.get("action_id")
    if act=="A01":
        progress=np.linalg.norm(xy[:,15]-xy[:,16],axis=1)/scale
        good=base&valid[:,15]&valid[:,16]; minimum=.6
    elif act in ("A02","A03","A06"):
        ids=[wrist] if act=="A02" else [15,16]
        progress=-rel[:,ids,1].mean(axis=1)
        good=base&valid[:,ids].all(axis=1); minimum=.25
    elif act=="A04":
        tilt=np.arctan2((sh-hip)[:,0],-(sh-hip)[:,1])
        baseline=float(np.median(tilt[early])); offset=tilt-baseline
        direction=1 if np.percentile(offset[base],95)>abs(np.percentile(offset[base],5)) else -1
        progress=direction*offset; good=base; minimum=.08
    elif act=="A05":
        shoulder=11 if side=="left" else 12; other=12 if side=="left" else 11
        axis=xy[:,other]-xy[:,shoulder]; axis/=np.maximum(np.linalg.norm(axis,axis=1,keepdims=True),1)
        progress=np.sum((xy[:,wrist]-xy[:,shoulder])*axis,axis=1)/scale
        good=base&valid[:,wrist]; minimum=.45
    else: return None
    # Interpolation is used only to stabilize segmentation around short holes; labels retain unknown.
    if good.sum()<10: return None
    filled=np.interp(times,times[good],progress[good]); progress=smooth(filled)
    return dict(progress=progress,valid=good,body=body,relative=rel,side=side,scale=scale,minimum=minimum)

def elbow_angles(points,width,height):
    xy=points[:,:,:2]*[width,height]; result=[]
    for shoulder,elbow,wrist in ((11,13,15),(12,14,16)):
        u=xy[:,shoulder]-xy[:,elbow]; v=xy[:,wrist]-xy[:,elbow]
        cosine=np.sum(u*v,axis=1)/np.maximum(np.linalg.norm(u,axis=1)*np.linalg.norm(v,axis=1),1)
        result.append(np.degrees(np.arccos(np.clip(cosine,-1,1))))
    return smooth(np.stack(result,axis=1))

def completed_end(points,times,a,b,f,width,height,meta):
    """Height/width only locates the main excursion. Completion needs the full moving limb.

    An offline plateau provides a per-clip geometric endpoint, NOT a speed normalization.
    This does not change the speed signal exported for ink rendering.
    """
    n=len(times); dt=float(np.median(np.diff(times))); q=f["progress"]; good=f["valid"]
    info={"initial_end":float(times[b]) if b<n else float(times[-1]),"completion_refined":False}
    if b>=n-2: return b,info
    span=float(np.percentile(q[good],97)-np.percentile(q[good],3))
    # Stop looking before a substantial return toward the start. Intermediate pauses are allowed.
    level=float(np.median(q[max(a,b-4):b]))
    returning=spans((q[b:]<level-.35*span)&good[b:])
    limit=next((b+p for p,e in returning if (e-p)*dt>=.25),n)
    if limit-b<round(.4/dt): return b,info
    act=meta.get("action_id"); angles=elbow_angles(points,width,height)
    selected=[0,1]
    if act=="A02": selected=[0 if f["side"]=="left" else 1]
    extension=angles[:,selected].mean(axis=1)
    elbow_good=(points[:,[13,14],3]>.5).all(axis=1) if len(selected)==2 else points[:,13 if selected[0]==0 else 14,3]>.5
    pool=(np.arange(n)>=b)&(np.arange(n)<limit)&good&elbow_good
    # A01/A02/A03 end with an extended arm. Do not impose 180 degrees: use the observed endpoint.
    need_extension=act in ("A01","A02","A03") and pool.sum()>=8
    # Avoid chasing progressively straighter elbows during a long held pose. In image space
    # a comfortably extended arm need not project to 180 degrees.
    target=min(155.,float(np.percentile(extension[pool],80))) if need_extension else 0
    info["terminal_elbow_degrees"]=round(target,2) if need_extension else None
    body=f["body"]
    if act in ("A02","A05"):
        body=body[:,[0,2] if f["side"]=="left" else [1,3]]
    elif act=="A04":
        # For leaning, angular progress is the primary signal; wrist pose can legitimately vary.
        body=q[:,None,None]
    window=max(5,round(.45/dt))
    for i in range(b,min(limit-window+1,n-window)):
        if not good[i:i+window].all(): continue
        if need_extension and (not elbow_good[i:i+window].all() or np.median(extension[i:i+3])<target-3): continue
        movement=np.sqrt(np.mean(np.sum((body[i:i+window]-body[i])**2,axis=-1),axis=-1))
        tolerance=.018 if act=="A04" else .055
        if np.percentile(movement,90)>tolerance: continue
        if need_extension and np.percentile(abs(extension[i:i+window]-extension[i]),90)>4: continue
        info.update(completion_refined=bool(i>b),completion_end=round(float(times[i]),3))
        return i,info
    info["completion_uncertain"]=True
    return b,info

def body_phases(points,times,duration,meta,width,height):
    labels=np.full(len(times),"unknown",dtype=object)
    f=features(points,times,width,height,meta)
    details={"engine":ENGINE,"action_condition":meta.get("action_id"),"confidence":"low"}
    if f is None or f["valid"].mean()<.55:
        return labels,details,["有效骨架不足，阶段保留未知。"]
    q=f["progress"]; good=f["valid"]; dt=float(np.median(np.diff(times))) if len(times)>1 else .033
    span=float(np.percentile(q[good],97)-np.percentile(q[good],3))
    details["suggested_active_hand"]=f["side"]; details["progress_range"]=round(span,4)
    if span<f["minimum"]:
        return labels,details,["没有找到足够幅度的目标动作，未强行标出执行段。"]
    # A central difference covers roughly 0.13 s and filters per-frame landmark jitter.
    k=max(1,round(.067/dt)); deriv=np.zeros(len(times))
    for i in range(k,len(times)-k): deriv[i]=(q[i+k]-q[i-k])/(times[i+k]-times[i-k])
    threshold=max(.008 if meta["action_id"]=="A04" else .04,span*.022)
    bouts=spans((deriv>threshold)&good)
    bouts=merge_bouts(bouts,times,q,good)
    top=float(np.percentile(q[good],95))
    # Join a fast/slow pair across a brief pause, but stop once the terminal pose is reached.
    # This prevents endpoint fidgets from chaining into a multi-second execution.
    joined=[]
    for a,b in bouts:
        if q[b-1]-q[a]<span*.025: continue
        if joined:
            p,e=joined[-1]
            if times[a]-times[e-1]<=1.1 and q[e-1]<top-span*.10 and good[e:a+1].all() and np.min(q[e-1:a+1])>=q[e-1]-span*.30:
                joined[-1]=(p,b); continue
        joined.append((a,b))
    bouts=joined
    candidates=[]
    for a,b in bouts:
        if b-a<2: continue
        amp=q[b-1]-q[a]
        if amp<max(f["minimum"]*.45,span*.14): continue
        # Preparations finish below the terminal pose; returns have opposite signed progress.
        if q[b-1]<top-.23*span: continue
        after=min(len(q),b+max(2,round(.4/dt)))
        stable_end=float(np.median(q[b:after])) if after>b else q[b-1]
        if stable_end<q[b-1]-.2*span: continue
        candidates.append((float(amp),a,b))
    if not candidates:
        return labels,details,["无法可靠区分准备、执行和回位。请手动标出执行起止。"]
    _,a,b=max(candidates)
    # Recover low-speed tails around the directed excursion, without crossing a long hold.
    low_threshold=threshold*.25
    for _ in range(max(1,round(.25/dt))):
        if a>0 and good[a-1] and deriv[a-1]>low_threshold: a-=1
        else: break
    for _ in range(max(1,round(.25/dt))):
        if b<len(times) and good[b] and deriv[b]>low_threshold: b+=1
        else: break
    # Refine to plateau-relative 2% onset/offset crossings; never use instruction speed as truth.
    pre=max(0,a-round(.5/dt)); post=min(len(q),b+round(.5/dt))
    lo=float(np.median(q[pre:a])) if a>pre else q[a]
    hi=float(np.median(q[b:post])) if post>b else q[b-1]
    excursion=hi-lo
    if excursion>f["minimum"]*.5:
        start_candidates=np.flatnonzero(q[pre:b]>lo+excursion*.02)
        if len(start_candidates): a=pre+int(start_candidates[0])
        end_candidates=np.flatnonzero(q[a:post]>=hi-excursion*.02)
        if len(end_candidates): b=a+int(end_candidates[0])+1
    if b<=a or times[b-1]-times[a]<.12:
        return labels,details,["候选执行段过短，请人工复核。"]
    b,completion=completed_end(points,times,a,b,f,width,height,meta)
    details.update(completion)
    body=f["body"]; rel=f["relative"]
    # Neutral requires both wrists near the sides and below the pelvis level.
    hips=rel[:,[23,24],1].mean(axis=1)
    neutral=(rel[:,15,1]>hips-.35)&(rel[:,16,1]>hips-.35)&(abs(rel[:,15,0])<1.15)&(abs(rel[:,16,0])<1.15)&good
    labels[:a]="ready"; labels[a:b]="execute"; labels[b:]="hold"
    # Preparation: identify the last neutral block and arrival at the actual pre-action pose.
    neutral_blocks=[(p,e) for p,e in spans(neutral[:a]) if (e-p)*dt>=.25]
    if neutral_blocks:
        p,e=neutral_blocks[-1]; prep_start=e
        ref=np.median(body[max(pre,a-round(.4/dt)):a],axis=0) if a>pre else body[a]
        distance=np.sqrt(np.mean(np.sum((body-ref)**2,axis=2),axis=1))
        ready_blocks=[(u+e,v+e) for u,v in spans(distance[e:a]<.11) if (v-u)*dt>=.2]
        ready_start=ready_blocks[-1][0] if ready_blocks else a
        labels[:prep_start]="idle"; labels[prep_start:ready_start]="prepare"; labels[ready_start:a]="ready"
    elif a>round(.4/dt):
        # A clip can start after preparation already began: do not label the whole intro ready.
        ref=np.median(body[max(0,a-round(.4/dt)):a],axis=0)
        distance=np.sqrt(np.mean(np.sum((body-ref)**2,axis=2),axis=1))
        ready_blocks=[(p,e) for p,e in spans(distance[:a]<.11) if (e-p)*dt>=.2]
        if ready_blocks and float(np.median(distance[:max(1,round(.15/dt))]))>.2:
            ready_start=ready_blocks[-1][0]
            labels[:ready_start]="prepare";labels[ready_start:a]="ready"
            details["initial_neutral_not_observed"]=True
    # Find a substantial directed return, not arbitrary drift away from the first end pose.
    end_level=float(np.median(q[b:min(len(q),b+max(2,round(.4/dt)))])) if b<len(q) else q[-1]
    execution_change=max(f["minimum"],end_level-float(np.median(q[max(0,a-round(.4/dt)):a])) if a else span)
    peak_reverse=max(0,float(np.max(-deriv[b:]))) if b<len(q) else 0
    reverse_threshold=max(threshold*.75,peak_reverse*.20)
    reversal=spans((deriv<-reverse_threshold)&good&(np.arange(len(q))>=b))
    reversal=merge_bouts(reversal,times,-q,good,gap=.25,backtrack=span*.03)
    departures=[(p,e) for p,e in reversal if (e-p)*dt>=.1 and q[p]-q[e-1]>.30*execution_change and q[e-1]<end_level-.35*execution_change]
    if departures:
        ret=max(departures,key=lambda pe:q[pe[0]]-q[pe[1]-1])[0]
        # Recover the beginning of the final reversal, with a bounded look-back.
        for _ in range(max(1,round(.8/dt))):
            if ret>b and good[ret-1] and deriv[ret-1]<-max(threshold*.25,peak_reverse*.04): ret-=1
            else: break
        returns=[(u+ret,v+ret) for u,v in spans(neutral[ret:]) if (v-u)*dt>=.35]
        stop=returns[0][0] if returns else len(times)
        labels[ret:stop]="return"; labels[stop:]="idle"
    labels[~good]="unknown"
    details.update(confidence="medium",execute_start=round(float(times[a]),3),execute_end=round(float(times[b]) if b<len(times) else duration,3),candidate_count=len(candidates))
    warnings=["阶段按动作姿态与方向推测，区分执行与反向回位；起止边界仍需人工复核。"]
    if a==0: warnings.append("视频从动作中途开始，缺少起点保持。")
    if len(candidates)>1: warnings.append("检测到多个可能的执行片段，已选幅度最大的候选，请确认是否含重复动作。")
    pauses=[(p,e) for p,e in spans(abs(deriv[a:b])<threshold) if (e-p)*dt>.45]
    if pauses: warnings.append("执行途中检测到停顿，已保留为同一候选执行段；请核对是否需要拆开。")
    if completion.get("completion_uncertain"): warnings.append("没有找到可靠的终点稳定窗口，结束时间请重点复核。")
    if details.get("initial_neutral_not_observed"): warnings.append("开头没有足够长的自然站立，起点姿态到位前暂标为准备，请复核。")
    return labels,details,warnings
