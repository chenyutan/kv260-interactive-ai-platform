"""Offline hand prelabels. Geometry is evidence, never a reviewed stroke class."""
import numpy as np

ENGINE = "hand_pinch_v9"

def recover_sustained_closed_shape(labels,features,times):
    """Offline weak label: sustained curled fingers before a confirmed pinch.

    This does not prove hidden thumb contact. Keep explicit provenance and never
    fill coordinates, out-of-frame observations, or long tracking gaps.
    """
    result=list(labels);n=len(times);compact=np.zeros(n,bool);curled=np.zeros(n,bool)
    for i,t in enumerate(times):
        a=np.searchsorted(times,t-.1);b=np.searchsorted(times,t+.1,side='right')
        good=features['valid'][a:b]
        if good.sum()<3 or good.mean()<.6:continue
        ratio=np.median(features['pinch_ratio'][a:b][good])
        spread=np.median(features['fingertip_spread'][a:b][good])
        angles=np.median(features['finger_angles'][a:b][good],axis=0)
        curled[i]=ratio<1.05 and spread<.85 and angles[0]<165 and angles[1]<160 and max(angles[2:4])<155
        compact[i]=curled[i] or (ratio<.45 and spread<.85)
    ids=np.flatnonzero(compact)
    for a,b in zip(ids[:-1],ids[1:]):
        if (times[b]-times[a]<=.20+1e-6 and features['in_frame'][a:b+1].all()
                and not (features['valid'][a:b+1]&(features['fingertip_spread'][a:b+1]>1.3)).any()
                and np.all(np.diff(times[a:b+1])<=.06)):
            compact[a:b+1]=True
    inferred=[]
    for end in range(1,n):
        if labels[end]!='down' or labels[end-1]=='down':continue
        stop=np.searchsorted(times,times[end]+.15,side='right')
        if stop-end<4 or any(x!='down' for x in labels[end:stop]):continue
        start=end
        while start>0 and times[end]-times[start-1]<=8:
            k=start-1
            if times[start]-times[k]>.06 or labels[k]=='down':break
            if not features['in_frame'][k] or not compact[k]:break
            if not features['valid'][k]:
                left=k
                while left>0 and not features['valid'][left]:left-=1
                right=k
                while right<n-1 and not features['valid'][right]:right+=1
                if times[right]-times[left]>.20:break
            start=k
        if times[end]-times[start]<1 or np.mean(curled[start:end])<.6:continue
        pre=np.searchsorted(times,times[start]-.35)
        ang=features['finger_angles'][pre:start]
        opened=features['valid'][pre:start]&(ang[:,1]>160)&(ang[:,2:4]>155).all(1)
        if not opened.any():continue
        result[start:end]=['down']*(end-start)
        inferred.append(dict(start=float(times[start]),end=float(times[end]),phase='down',source='hand_shape_inference',
            evidence='sustained_closed_shape_followed_by_confirmed_pinch',verified=False,coordinates_interpolated=False))
    return result,inferred

def recover_ambiguous_release(labels,features,times):
    """Offline continuity inference, never a cloth detector or direct contact proof.

    Require visible, compact side-hand geometry and confirmed down on BOTH sides.
    Strong release, tracking loss, and timestamp gaps must remain unmerged.
    """
    labels=list(labels);inferred=[];i=0
    while i<len(labels):
        if labels[i]!='up':i+=1;continue
        j=i+1
        while j<len(labels) and labels[j]=='up':j+=1
        if i>0 and j<len(labels) and labels[i-1]==labels[j]=='down':
            ratio=features['pinch_ratio'][i:j]
            angles=features['finger_angles'][i:j,1:]
            strong_open=(ratio>.8)&((angles>172).sum(axis=1)>=3)
            if 'spread_open' in features:strong_open|=features['spread_open'][i:j]
            a=np.searchsorted(times,times[i]-.10)
            b=np.searchsorted(times,times[j]+.10,side='right')
            anchored=i-a>=2 and b-j>=2 and all(x=='down' for x in labels[a:i]+labels[j:b])
            evidence=(features['valid'][i-1:j+1].all()
                and np.all(np.diff(times[i-1:j+1])<=.06)
                and np.max(ratio)<1.05
                and np.median(features['fingertip_spread'][i:j])<.8
                and np.mean(features['palm_foreshortened'][i:j])>=(.4 if times[j]-times[i]<=.50 else .7)
                and not strong_open.any())
            if anchored and times[j]-times[i]<=4 and evidence:
                labels[i:j]=['down']*(j-i)
                inferred.append(dict(start=float(times[i]),end=float(times[j]),phase='down',
                    source='hand_shape_inference',evidence='ambiguous_release_between_confirmed_pinches',
                    verified=False,coordinates_interpolated=False))
        i=j
    return labels,inferred

def recover_hidden_pinch_onset(labels, features, times):
    """Offline shape evidence backed by subsequent visible pinch; never velocity.

    A dorsal thumb can be hidden while the four fingers visibly close. This is
    an inferred state, not proof of fingertip contact or recovered coordinates.
    Do not extend across missing landmarks, an open hand, or an unconfirmed fist.
    """
    labels=list(labels); intervals=[]
    angles=features['finger_angles']; ratio=features['pinch_ratio']
    curled=(features['valid'] & (ratio>=.30) & (ratio<.70)
            & (angles[:,0]<155) & (angles[:,1]<150)
            & ((angles[:,2:]<145).sum(axis=1)>=2))
    # Fingertips can converge without bent projected finger joints in a side
    # view. Only backfill this weaker shape when a visible pinch follows.
    if 'fingertip_spread' in features:
        curled|=(features['valid'] & (ratio<.65) & (angles[:,0]<165)
                 & (features['fingertip_spread']<1.0))
        ids=np.flatnonzero(curled)
        for a,b in zip(ids[:-1],ids[1:]):
            if (times[b]-times[a]<=.15 and features['valid'][a:b+1].all()
                    and np.max(ratio[a:b+1])<.75
                    and np.max(features['fingertip_spread'][a:b+1])<1.2
                    and np.all(np.diff(times[a:b+1])<=.06)):
                curled[a:b+1]=True
    for end in range(1,len(labels)):
        if labels[end]!='down' or labels[end-1]=='down':continue
        support=np.searchsorted(times,times[end]+.10,side='right')
        if support-end<3 or any(x!='down' for x in labels[end:support]):continue
        start=end
        # Brief turnover can straighten the projected thumb before the strict
        # pinch gate confirms. The other fingers must still be visibly curled.
        while start>0 and times[end]-times[start-1]<=.15:
            k=start-1
            if not (labels[k]=='up' and features['valid'][k] and ratio[k]<.45
                    and angles[k,1]<150 and (angles[k,2:]<145).sum()>=2
                    and times[start]-times[k]<=.06):break
            start-=1
        transition_start=start
        while start>0 and curled[start-1] and labels[start-1]=='up':
            if times[start]-times[start-1]>.06:break
            start-=1
        duration=times[end]-times[start]
        if not .15<=duration<=8 or start==0 or times[transition_start]-times[start]<.15:continue
        # Require a preceding visibly open posture, rather than a clip starting
        # in an unobservable fist. Transition frames may occupy up to 0.3 s.
        pre=np.searchsorted(times,times[start]-.30)
        opened=(features['valid'][pre:start] & (angles[pre:start,1]>155)
                & ((angles[pre:start,2:]>150).sum(axis=1)>=2))
        if not opened.any():continue
        labels[start:end]=['down']*(end-start)
        intervals.append(dict(start=float(times[start]),end=float(times[end]),phase='down',
            source='hand_shape_inference',verified=False,coordinates_interpolated=False,
            evidence='visible_finger_closure_followed_by_confirmed_pinch'))
    return labels,intervals

def bridge_short_gaps(labels,features,times):
    """Infer pen state only across brief gaps, preserving missing coordinates/speed."""
    labels=list(labels);bridges=[];i=0
    while i<len(labels):
        if labels[i]!='unknown': i+=1;continue
        j=i+1
        while j<len(labels) and labels[j]=='unknown': j+=1
        if i>0 and j<len(labels) and labels[i-1]==labels[j] and labels[j] in ('up','down'):
            duration=float(times[j]-times[i])
            left=features['tip_xy'][i-1];right=features['tip_xy'][j]
            # Large displacement is allowed; a whole-frame swap is not.
            displacement=float(np.linalg.norm(left-right))
            pre=np.searchsorted(times,times[i]-.25)
            post=np.searchsorted(times,times[j]+.25,side='right')
            left_labels=labels[pre:i];right_labels=labels[j:post]
            period=float(np.median(np.diff(times))) if len(times)>1 else 0
            stable=(left_labels.count(labels[j])*period>=.08 and right_labels.count(labels[j])*period>=.08
                    and all(x in (labels[j],'unknown') for x in left_labels+right_labels))
            # Only detector gaps, not observed ambiguous half-pinches or timestamp gaps.
            missing=not features['valid'][i:j].any()
            time_ok=np.all(np.diff(times[i-1:j+1])<=.06)
            limit=.20 if features['palm_foreshortened'][i-1] or features['palm_foreshortened'][j] else .15
            if duration<=limit+1e-6 and stable and missing and time_ok and displacement<=.60:
                labels[i:j]=[labels[j]]*(j-i)
                bridges.append(dict(start=float(times[i]),end=float(times[j]),phase=labels[j],
                    source='temporal_inference',verified=False,coordinates_interpolated=False))
        i=j
    return labels,bridges

def select_writing_hand(candidates,width,height,meta):
    anchored,anchor_detail=_select_anchored_hand(candidates,width,height,meta)
    if np.mean((candidates[:,:,0,3]>.5).all(1))<.02:return anchored,anchor_detail
    tracks=np.zeros_like(candidates);previous=[None,None];last_seen=[-100000,-100000]
    for i,frame in enumerate(candidates):
        ids=[j for j in range(2) if frame[j,0,3]>.5]
        if not ids:continue
        if all(p is None for p in previous):assignments=[(j,j) for j in ids]
        else:
            def cost(slot,j):
                if previous[slot] is None or i-last_seen[slot]>15:return 1e6
                return float(np.linalg.norm((frame[j,0,:2]-previous[slot][0,:2])*[width,height]))
            if len(ids)==2:
                assignments=[(0,0),(1,1)] if cost(0,0)+cost(1,1)<=cost(0,1)+cost(1,0) else [(0,1),(1,0)]
            else:
                j=ids[0];assignments=[(min(range(2),key=lambda k:cost(k,j)),j)]
        for slot,j in assignments:
            tracks[i,slot]=frame[j];previous[slot]=frame[j].copy();last_seen[slot]=i
    scores=[]
    for slot in range(2):
        p=tracks[:,slot];good=p[:,0,3]>.5
        if good.sum()<3:scores.append(0.);continue
        xy=p[good,0,:2]*[width,height]
        scores.append(float(np.linalg.norm(np.percentile(xy,95,axis=0)-np.percentile(xy,5,axis=0))*np.sqrt(good.mean())))
    # A confident continuous track wins. Re-anchor only ambiguous identities;
    # a far-apart frame can itself contain a false hand in a long recording.
    if max(scores)<1.35*max(min(scores),1):
        other=anchor_detail['candidate_scores']
        if max(other)>1.35*max(min(other),1):return anchored,anchor_detail
    chosen=int(np.argmax(scores))
    return tracks[:,chosen].copy(),dict(model_side=['Left','Right'][chosen],method='spatial_tracks_then_whole_clip_wrist_excursion',
        candidate_scores=scores,verified=False,warning='按整段轨迹选择书写手；两手轮流书写需拆分或人工复核。')

def _select_anchored_hand(candidates,width,height,meta):
    """Stitch detections spatially before choosing a single whole-clip hand track."""
    candidates=candidates.copy();tracks=np.zeros_like(candidates)
    frequency=(candidates[:,:,0,3]>.5).sum(axis=0)
    sparse_dual=np.mean((candidates[:,:,0,3]>.5).all(axis=1))<.02
    # Remove sparse duplicate observations before anchoring either direction.
    for i,frame in enumerate(candidates):
        ids=[j for j in range(2) if frame[j,0,3]>.5]
        if not ids: continue
        if len(ids)==2:
            # Occasional duplicate detections of ONE hand can seed a ghost track.
            xy=frame[:,:,:2]*[width,height]
            lo=xy.min(axis=1);hi=xy.max(axis=1)
            intersection=np.maximum(np.minimum(hi[0],hi[1])-np.maximum(lo[0],lo[1]),0).prod()
            union=np.prod(hi[0]-lo[0])+np.prod(hi[1]-lo[1])-intersection
            palm=max(np.linalg.norm(xy[k,0]-xy[k,9]) for k in range(2))
            if sparse_dual and intersection/max(union,1)>.30 and np.linalg.norm(xy[0,0]-xy[1,0])<.75*palm:
                candidates[i,1-int(np.argmax(frequency))]=0
    both=(candidates[:,:,0,3]>.5).all(axis=1)
    separation=np.linalg.norm((candidates[:,0,0,:2]-candidates[:,1,0,:2])*[width,height],axis=1)
    anchor=int(np.argmax(np.where(both,separation,-1))) if both.any() else 0
    tracks[anchor]=candidates[anchor]
    for direction in (1,-1):
      previous=[candidates[anchor,j].copy() if candidates[anchor,j,0,3]>.5 else None for j in range(2)]
      for i in range(anchor+direction,len(candidates) if direction==1 else -1,direction):
        frame=candidates[i];ids=[j for j in range(2) if frame[j,0,3]>.5]
        if not ids:continue
        if all(p is None for p in previous):
            assignments=[(j,j) for j in ids]
        else:
            def cost(slot,j):
                # Anchoring both directions avoids assigning the first visible
                # idle hand to a new identity when the writing hand reappears.
                if previous[slot] is None: return 1e6
                return float(np.linalg.norm((frame[j,0,:2]-previous[slot][0,:2])*[width,height]))
            if len(ids)==2:
                assignments=[(0,0),(1,1)] if cost(0,0)+cost(1,1)<=cost(0,1)+cost(1,0) else [(0,1),(1,0)]
            else:
                j=ids[0];slot=min(range(2),key=lambda k:cost(k,j));assignments=[(slot,j)]
        for slot,j in assignments:
            tracks[i,slot]=frame[j];previous[slot]=frame[j].copy()
    candidates=tracks
    scores=[]
    for slot in range(2):
        p=candidates[:,slot];good=p[:,0,3]>.5
        if good.sum()<3: scores.append(0.);continue
        xy=p[good,0,:2]*[width,height]
        extent=np.percentile(xy,95,axis=0)-np.percentile(xy,5,axis=0)
        scores.append(float(np.linalg.norm(extent)*np.sqrt(good.mean())))
    chosen=int(np.argmax(scores))
    # These are model labels on the saved image, not an assumption about mirroring.
    return candidates[:,chosen].copy(),dict(model_side=['Left','Right'][chosen],
        method='bidirectional_separated_hand_anchor',candidate_scores=scores,
        verified=False,warning='按整段运动范围选择书写手；左右称谓受镜像影响，两手轮流书写需拆分或人工复核。')

def hand_features(points, times, width, height):
    xy=points[:,:,:2]*[width,height]
    visible=(points[:,:,3]>.5)&np.isfinite(xy).all(axis=2)
    visible&=((points[:,:,:2]>=0)&(points[:,:,:2]<=1)).all(axis=2)
    # Wrist-to-MCP length collapses when the palm turns edge-on. MCP width and
    # a local scale reference keep this projection change from imitating release.
    palm=np.maximum(np.linalg.norm(xy[:,0]-xy[:,9],axis=1),np.linalg.norm(xy[:,5]-xy[:,17],axis=1))
    valid=visible[:,[0,4,5,8,9,17]].all(axis=1)&(palm>=8)
    projected=palm.copy()
    for i,t in enumerate(times):
        if not valid[i]:continue
        a=np.searchsorted(times,t-.4);b=np.searchsorted(times,t+.4,side='right')
        local=projected[a:b][valid[a:b]]
        if len(local)>=3:palm[i]=max(palm[i],.75*float(np.percentile(local,75)))
    pinch=np.linalg.norm(xy[:,4]-xy[:,8],axis=1)/np.maximum(palm,1)
    # Translation alone is not evidence of occlusion. Keep it as a diagnostic;
    # actual missing landmarks and timestamps still invalidate speed samples.
    tip=(xy[:,4]+xy[:,8])/2
    motion_jump=np.zeros(len(times),dtype=bool)
    if len(times)>1:
        dt=np.diff(times)
        jump=np.linalg.norm(np.diff(xy[:,0],axis=0),axis=1)/np.maximum(palm[:-1],1)
        motion_jump[1:]=valid[:-1]&valid[1:]&(dt>0)&(dt<.1)&(jump>2.5)
        valid[1:]&=~((dt<=0)|(dt>.25))
    angles=[]
    for a,b,c in [(1,2,4),(5,6,8),(9,10,12),(13,14,16),(17,18,20)]:
        u=xy[:,a]-xy[:,b]; v=xy[:,c]-xy[:,b]
        cosine=np.sum(u*v,axis=1)/np.maximum(np.linalg.norm(u,axis=1)*np.linalg.norm(v,axis=1),1e-6)
        angles.append(np.degrees(np.arccos(np.clip(cosine,-1,1))))
    index_length=sum(np.linalg.norm(xy[:,j]-xy[:,j+1],axis=1) for j in (5,6,7))
    side_projection=visible[:,[5,6,7,8]].all(axis=1)&(index_length>1.3*palm)
    # A palm-only denominator collapses in oblique views. Use the observed
    # articulated index length as a second scale, capped against bad zigzags.
    reference=np.maximum(palm,np.minimum(index_length,2*palm))
    normalized=pinch*palm/np.maximum(reference,1)
    fingertip_spread=np.max(np.stack([np.linalg.norm(xy[:,j]-xy[:,k],axis=1)
        for j in (8,12,16,20) for k in (8,12,16,20)]),axis=0)/np.maximum(palm,1)
    finger_angles=np.stack(angles,axis=1)
    # Learned relative z is corroborating evidence, not measured metric depth.
    xyz=points[:,:,:3]*[width,height,width]
    estimated_gap=np.linalg.norm(xyz[:,4]-xyz[:,8],axis=1)/np.maximum(reference,1)
    spread_open=valid&(fingertip_spread>1.3)&((finger_angles[:,1:]>165).sum(1)>=3)&(estimated_gap>.4)
    return dict(pinch_ratio=np.where(valid,normalized,np.nan),raw_pinch_ratio=np.where(valid,pinch,np.nan),palm_pixels=palm,
                spread_open=spread_open,
                in_frame=((points[:,:,:2]>=0)&(points[:,:,:2]<=1)).all(axis=(1,2)),
                fingertip_spread=fingertip_spread,
                side_pinch=valid&side_projection&(pinch<.70)&(pinch*palm/np.maximum(index_length,1)<.35),
                finger_angles=finger_angles,tip_xy=tip/[width,height],valid=valid,motion_jump=motion_jump,
                palm_foreshortened=(np.linalg.norm(xy[:,0]-xy[:,9],axis=1)<.90*palm)|side_projection)

def hand_phases(points,times,duration,meta,width,height):
    f=hand_features(points,times,width,height)
    ratio=f['pinch_ratio']; valid=f['valid']; labels=['unknown']*len(times)
    # Hysteresis plus time-based confirmation; slow motion never ends a stroke.
    state='unknown'; candidate=None; start=0;last_valid=None
    for i,t in enumerate(times):
        if not valid[i]:
            if last_valid is None or t-last_valid>.20:state='unknown'
            candidate=None;continue
        if last_valid is not None and t-last_valid>.25:state='unknown'
        last_valid=t
        release_threshold=.60 if f['palm_foreshortened'][i] else .45
        release_value=ratio[i]
        target='up' if f['spread_open'][i] else 'down' if ratio[i]<.30 or f['side_pinch'][i] else 'up' if release_value>release_threshold else state
        if target==state:
            candidate=None
        elif target!=candidate:
            candidate=target; start=i
        # Modest separation during palm rotation needs stronger temporal support;
        # clearly open fingers can still release in 50ms.
        confirmation=.20 if candidate=='up' and f['palm_foreshortened'][start:i+1].any() and np.max(ratio[start:i+1])<.90 else .05
        if candidate is not None and t-times[start]>=confirmation-1e-6:
            state=candidate
            labels[start:i+1]=[state]*(i-start+1)
            candidate=None
        labels[i]=state
    labels,release_intervals=recover_ambiguous_release(labels,f,times)
    labels,shape_intervals=recover_hidden_pinch_onset(labels,f,times)
    labels,sustained_intervals=recover_sustained_closed_shape(labels,f,times)
    # Re-evaluate the remaining short release after onset recovery has restored
    # its right boundary; preserve provenance for both kinds of weak inference.
    labels,remaining_releases=recover_ambiguous_release(labels,f,times)
    release_intervals+=remaining_releases
    labels,bridges=bridge_short_gaps(labels,f,times)
    shape_intervals+=sustained_intervals
    bridges=release_intervals+shape_intervals+bridges
    events=[]; strokes=[]
    for i in range(1,len(labels)):
        if labels[i]==labels[i-1]: continue
        kind='occlusion' if labels[i]=='unknown' else 'reacquired' if labels[i-1]=='unknown' else 'pen_down' if labels[i]=='down' else 'pen_up'
        events.append(dict(time=float(times[i]),type=kind,source='auto',verified=False))
    i=0
    while i<len(labels):
        if labels[i]!='down': i+=1; continue
        j=i+1
        while j<len(labels) and labels[j]=='down': j+=1
        tip=f['tip_xy'][i:j]*[width,height];good=valid[i:j]
        delta=tip[-1]-tip[0]
        palm=float(np.median(f['palm_pixels'][i:j][good]))
        links=good[:-1]&good[1:]
        length=float(np.linalg.norm(np.diff(tip,axis=0),axis=1)[links].sum()/max(palm,1))
        strokes.append(dict(start=float(times[i]),end=float(times[j]) if j<len(times) else float(duration),
            path_length_palms=length,displacement_palms=(delta/max(palm,1)).tolist(),
            action_hint=meta.get('action_id',''),class_source='filename_unverified',
            incomplete_start=i==0 or labels[i-1]=='unknown',
            incomplete_end=j==len(times) or labels[j]=='unknown'))
        i=j
    details=dict(engine=ENGINE,enter_ratio=.30,exit_ratio=.45,confirmation_seconds=.05,
                 gap_bridge_max_seconds=.15,side_palm_gap_max_seconds=.20,foreshortened_weak_release_seconds=.20,
                 side_exit_ratio=.60,normalization='max_palm_or_capped_index_length',
                 spread_open_guard='spread_and_extended_fingers_and_estimated_relative_depth',
                 valid_fraction=float(valid.mean()) if len(valid) else 0,
                 large_motion_frames=int(f['motion_jump'].sum()),
                 inferred_state_intervals=bridges,
                 stroke_count=len(strokes),strokes=strokes,events=events,
                 features=['pinch_ratio','palm_pixels','finger_angles','tip_xy','valid','motion_jump'])
    warnings=['手部21点预标注：捏合为落笔、松开为抬笔；停顿与变速不会自动断笔。笔画类别来自文件名，尚需人工核对。',
              '短丢点前后状态一致且稳定时可推断连续状态：通常150毫秒，侧掌转向最多200毫秒；推断区间单独记录，原坐标与速度不插值。较长丢失及真实抬笔不合并。']
    if not strokes: warnings.append('未检测到稳定捏合落笔，请检查手势、手部清晰度及时间段。')
    if shape_intervals: warnings.append('手背遮住拇指时，结合可见手指闭合与随后确认的捏合推断起笔；推断区间需复核接触时刻，不代表直接看见指尖接触。')
    if sustained_intervals: warnings.append('持续闭合姿态结合随后明确捏合推断静止起笔；这是离线弱标注，需人工复核，未补造关键点或速度。')
    if release_intervals: warnings.append('局部遮挡或侧掌下的弱抬笔证据，结合前后已确认捏合推断连续落笔；已记录推断区间，需人工复核。未插值坐标，不代表直接检测到衣物遮挡。')
    return labels,details,warnings
