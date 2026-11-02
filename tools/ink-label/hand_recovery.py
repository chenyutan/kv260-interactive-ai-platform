"""Re-detect short hand tracking gaps in original RGB crops. No interpolation."""
import numpy as np

def unrotate_xy(x,y,turns):
    return [(x,y),(1-y,x),(1-x,1-y),(y,1-x)][turns%4]

def refine_visible_track(video,arrays,width,height,detector,cancelled=lambda:False):
    """Re-estimate visible hands in original-resolution ROIs, not just lost frames.

    Select by wrist proximity, never by the desired pinch state. Keep original
    observations if the crop detector has no matching hand.
    """
    import av
    import mediapipe as mp
    original=arrays['points'];p=original.copy();mask=np.zeros(len(p),bool)
    wanted={int(f):i for i,f in enumerate(arrays['frame_indices'])}
    with av.open(str(video)) as c:
        for fi,frame in enumerate(c.decode(video=0)):
            if cancelled():raise InterruptedError('分析已取消，原标注未修改。')
            if fi not in wanted:continue
            i=wanted[fi];q=original[i]
            if not (q[:,3]>.5).all() or not np.isfinite(q[:,:2]).all():continue
            xy=q[:,:2]*[width,height];span=np.maximum(np.ptp(xy,axis=0),80)
            lo=np.maximum(xy.min(0)-span*.4,0).astype(int)
            hi=np.minimum(xy.max(0)+span*.4,[width,height]).astype(int)
            crop=np.ascontiguousarray(frame.to_ndarray(format='rgb24')[lo[1]:hi[1],lo[0]:hi[0]])
            if min(crop.shape[:2])<32:continue
            result=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=crop));choices=[]
            for hand in result.hand_landmarks:
                row=np.array([[(lm.x*crop.shape[1]+lo[0])/width,(lm.y*crop.shape[0]+lo[1])/height,lm.z*crop.shape[1]/width,1.] for lm in hand],np.float32)
                if np.linalg.norm(row[0,:2]-q[0,:2])<.12:choices.append(row)
            if choices:p[i]=min(choices,key=lambda row:np.linalg.norm(row[0,:2]-q[0,:2]));mask[i]=True
    return p,mask

def recover_track_gaps(video,arrays,width,height,detector,cancelled=lambda:False):
    """Image-based recovery anchored to both ends, irrespective of pinch state."""
    import av
    import mediapipe as mp
    from hand_phases import hand_features
    p=arrays['points'].copy();times=arrays['times'];missing=~hand_features(p,times,width,height)['valid']
    recovered=np.zeros(len(times),bool);requests={};i=0
    while i<len(times):
        if not missing[i]:i+=1;continue
        j=i+1
        while j<len(times) and missing[j]:j+=1
        if i>0 and j<len(times) and times[j]-times[i]<=.65 and np.all(np.diff(times[i-1:j+1])<=.06):
            endpoints=np.concatenate([p[i-1,:,:2],p[j,:,:2]])*[width,height]
            span=np.maximum(np.ptp(endpoints,axis=0),80)
            lo=np.maximum(endpoints.min(0)-.5*span,0).astype(int)
            hi=np.minimum(endpoints.max(0)+.5*span,[width,height]).astype(int)
            if np.linalg.norm(p[j,0,:2]-p[i-1,0,:2])<.7:
                for k in range(i,j):requests[int(arrays['frame_indices'][k])]=(k,lo,hi,i-1,j)
        i=j
    if not requests:return p,recovered
    with av.open(str(video)) as c:
        for index,frame in enumerate(c.decode(video=0)):
            if cancelled():raise InterruptedError('分析已取消，原标注未修改。')
            if index>max(requests):break
            if index not in requests:continue
            k,lo,hi,left,right=requests[index]
            crop=frame.to_ndarray(format='rgb24')[lo[1]:hi[1],lo[0]:hi[0]]
            if min(crop.shape[:2])<32:continue
            ref=(p[left,0,:2]+p[right,0,:2])/2;choices=[]
            for turn in (0,1,3,2):
                view=np.ascontiguousarray(np.rot90(crop,turn))
                result=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=view))
                for hand in result.hand_landmarks:
                    row=[]
                    for lm in hand:
                        x,y=unrotate_xy(lm.x,lm.y,turn)
                        row.append([(x*crop.shape[1]+lo[0])/width,(y*crop.shape[0]+lo[1])/height,lm.z*view.shape[1]/width,1.])
                    row=np.asarray(row,dtype=np.float32)
                    if ((row[:,:2]>=0)&(row[:,:2]<=1)).all() and np.linalg.norm(row[0,:2]-ref)<.4:
                        choices.append(row)
                if choices:break
            if choices:p[k]=min(choices,key=lambda x:np.linalg.norm(x[0,:2]-ref));recovered[k]=True
    return p,recovered

def recover_with_pose(video,arrays,width,height,detector,pose_model,cancelled=lambda:False,seed_missing=None):
    """Use the body's observed wrist to recover dorsal hands, including long gaps."""
    import av
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from pathlib import Path
    p=arrays['points'].copy();times=arrays['times']
    missing=~(p[:,[0,4,8,9],3]>.5).all(axis=1)
    recovered=np.zeros(len(times),dtype=bool)
    if missing.all():return p,recovered
    source=missing.copy() if seed_missing is None else missing|seed_missing
    if not source.any():return p,recovered
    targets=source.copy()
    # Recheck the transition around the gap as well: bad landmarks often precede
    # full detector loss. Keep the original suspect region on later passes.
    for offset in range(1,6):
        targets[offset:]|=source[:-offset];targets[:-offset]|=source[offset:]
    wanted={int(frame):i for i,frame in enumerate(arrays['frame_indices'])}
    side=None
    options=vision.PoseLandmarkerOptions(base_options=python.BaseOptions(model_asset_buffer=Path(pose_model).read_bytes()),running_mode=vision.RunningMode.IMAGE,num_poses=1)
    with vision.PoseLandmarker.create_from_options(options) as pose, av.open(str(video)) as c:
        for frame_index,frame in enumerate(c.decode(video=0)):
            if cancelled():raise InterruptedError('分析已取消，原标注未修改。')
            if frame_index not in wanted:continue
            i=wanted[frame_index]
            if side is not None and not targets[i]:continue
            rgb=frame.to_ndarray(format='rgb24')
            result=pose.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)))
            if not result.pose_landmarks:continue
            body=result.pose_landmarks[0]
            if side is None:
                if missing[i]:continue
                visible=[j for j in (15,16) if body[j].visibility>.6]
                if not visible:continue
                side=min(visible,key=lambda j:np.linalg.norm(np.array([body[j].x,body[j].y])-p[i,0,:2]))
                if np.linalg.norm(np.array([body[side].x,body[side].y])-p[i,0,:2])>.15:
                    side=None;continue
            if not targets[i] or body[side].visibility<.6:continue
            wrist=np.array([body[side].x*width,body[side].y*height])
            elbow=np.array([body[side-2].x*width,body[side-2].y*height])
            length=np.linalg.norm(wrist-elbow)
            center=wrist+.3*(wrist-elbow);radius=max(90.,length*.85)
            lo=np.maximum(center-radius,0).astype(int);hi=np.minimum(center+radius,[width,height]).astype(int)
            crop=rgb[lo[1]:hi[1],lo[0]:hi[0]]
            if min(crop.shape[:2])<32:continue
            choices=[]
            for flipped,turn in [(False,k) for k in (0,1,3,2)]+[(True,k) for k in (0,1,3,2)]:
                view=crop[:,::-1] if flipped else crop
                rotated=np.ascontiguousarray(np.rot90(view,turn))
                hands=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=rotated))
                for hand in hands.hand_landmarks:
                    row=[]
                    for lm in hand:
                        x,y=unrotate_xy(lm.x,lm.y,turn)
                        if flipped:x=1-x
                        row.append([(x*crop.shape[1]+lo[0])/width,(y*crop.shape[0]+lo[1])/height,lm.z*rotated.shape[1]/width,1.])
                    row=np.asarray(row,dtype=np.float32)
                    distance=np.linalg.norm(row[0,:2]*[width,height]-wrist)
                    if distance<max(45.,length*.55) and ((row[:,:2]>=0)&(row[:,:2]<=1)).all():
                        choices.append((distance,row))
                if choices:break
            if choices:p[i]=min(choices,key=lambda pair:pair[0])[1];recovered[i]=True
    return p,recovered

def recover_gaps(video,arrays,width,height,detector):
    import av
    import mediapipe as mp
    from hand_phases import hand_features
    p=arrays['points'].copy();times=arrays['times'];f=hand_features(p,times,width,height)
    missing=~(p[:,[0,4,8,9],3]>.5).all(axis=1)
    recovered=np.zeros(len(times),dtype=bool);requests=[];i=0
    while i<len(times):
        if not missing[i]:i+=1;continue
        j=i+1
        while j<len(times) and missing[j]:j+=1
        end=j
        # First reacquired prediction can be weak. Look a few frames ahead for
        # clear pinch evidence, but never search through a clearly open hand.
        while end<len(times)-1 and times[end]-times[j]<.15 and f['valid'][end] and .30<=f['pinch_ratio'][end]<.75:end+=1
        endpoint_closed=end<len(times) and (f['pinch_ratio'][end]<.30 or (f['palm_foreshortened'][end] and f['pinch_ratio'][end]<.75))
        if i>0 and end<len(times) and times[end]-times[i]<=.5 and f['pinch_ratio'][i-1]<.75 and endpoint_closed:
            xy=np.concatenate([p[i-1,:,:2],p[end,:,:2]])*[width,height]
            span=np.maximum(xy.max(0)-xy.min(0),64)
            lo=np.maximum(xy.min(0)-span*.6,0).astype(int)
            hi=np.minimum(xy.max(0)+span*.6,[width,height]).astype(int)
            for k in range(i,end):requests.append((k,lo,hi,(p[i-1,0,:2]+p[end,0,:2])/2))
        i=j
    if not requests:return p,recovered
    with av.open(str(video)) as c:
        s=c.streams.video[0];origin=float(s.start_time*s.time_base) if s.start_time is not None else 0
        for k,lo,hi,reference in requests:
            c.seek(int((times[k]+origin)/float(s.time_base)),stream=s,backward=True)
            frame=None
            for candidate in c.decode(s):
                if candidate.pts is not None and float(candidate.pts*candidate.time_base)-origin>=times[k]-1e-5:
                    frame=candidate;break
            if frame is None or abs(float(frame.pts*frame.time_base)-origin-times[k])>.005:continue
            rgb=frame.to_ndarray(format='rgb24')[lo[1]:hi[1],lo[0]:hi[0]]
            if min(rgb.shape[:2])<32:continue
            result=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)))
            candidates=[]
            for hand in result.hand_landmarks:
                row=np.array([[(lm.x*rgb.shape[1]+lo[0])/width,(lm.y*rgb.shape[0]+lo[1])/height,lm.z*rgb.shape[1]/width,1.] for lm in hand],dtype=np.float32)
                if np.linalg.norm(row[0,:2]-reference)<.25:candidates.append(row)
            if candidates:
                p[k]=min(candidates,key=lambda row:np.linalg.norm(row[0,:2]-reference));recovered[k]=True
    return p,recovered
