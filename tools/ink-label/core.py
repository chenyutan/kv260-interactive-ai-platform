"""InkLabel: local annotation data, PTS decoding and conservative proposals."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

VERSION = "1.6.5"
VIDEO_EXTENSIONS={".mp4",".mkv",".avi",".mov",".webm",".m4v"}
PHASES = {
    "idle": "自然站立", "prepare": "准备", "ready": "起点保持",
    "execute": "正式执行", "hold": "终点保持", "return": "回位",
    "up": "抬笔", "down": "落笔", "unknown": "未知 / 待确认",
    "circle1": "第一圈", "circle2": "第二圈", "negative": "无关活动",
    "menu_open": "菜单打开", "confirm": "确认", "cancel": "取消",
    "landscape": "山水", "bamboo": "竹", "plum": "梅"}
ACTIONS = {"A01": "双臂展开", "A02": "单臂上举", "A03": "双臂V形",
           "A04": "身体侧倾", "A05": "单臂横扫", "A06": "双手上托",
           "B01": "点", "B02": "横", "B03": "竖", "B04": "撇",
           "B05": "捺", "B06": "提", "B07": "横折", "B08": "竖钩"}
POSE_EDGES = [(11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),
              (23,24),(23,25),(25,27),(24,26),(26,28)]
HAND_EDGES = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
              (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),
              (15,16),(13,17),(0,17),(17,18),(18,19),(19,20)]

def now():
    return datetime.now(timezone.utc).isoformat()

def split_for(person, session):
    if person == "P003": return "test"
    if person in ("P001", "P002"):
        return {"S01": "train", "S02": "validation"}.get(session, "unassigned")
    return "unassigned"

def parse_name(path):
    tokens = Path(path).stem.upper().split("_")
    def token(pattern):
        return next((s for s in tokens if re.fullmatch(pattern, s)), "")
    person, session = token(r"P\d{3}"), token(r"S\d{2}")
    category = next((s for s in tokens if s in ("BODY","HAND","CHAR","CTRL","MIX","NEG")), "")
    tail = tokens[tokens.index(category)+1:] if category else []
    return dict(person_id=person, session_id=session, clothing_id=token(r"C\d{2}"),
                category=category, action_id=token(r"[AB]\d{2}|G01|M0[123]") or
                (tail[0] if tail else ""),
                speed_instruction=token(r"SLOW|MID|FAST|SF|FS|NAT"),
                repeat=token(r"R\d+"), active_hand="unknown", direction="unknown",
                control_validity="NEG" if "NEG" in tail else ("POS" if "POS" in tail else ""),
                control_direction=token(r"CW|CCW"), negative_type=tail[1] if tail and tail[0]=="NEG" and len(tail)>1 else "",
                split=split_for(person,session), filename_label_verified=False)

def fingerprint(path):
    p = Path(path)
    st = p.stat()
    return {"size":st.st_size, "mtime_ns":st.st_mtime_ns}

def blank(path):
    return {"schema":"inklabel.v1", "version":VERSION, "video":str(Path(path).resolve()),
            "fingerprint":fingerprint(path), "metadata":parse_name(path), "duration":0,
            "status":"draft", "segments":[], "events":[], "corrections":{},
            "notes":"", "warnings":[], "created_at":now(), "updated_at":now()}

def key(path):
    return hashlib.sha256(str(Path(path).resolve()).casefold().encode()).hexdigest()[:16]

def annotation_path(project, video):
    return Path(project)/"annotations"/(Path(video).stem+"_"+key(video)+".json")

def save_json(path, data, history=True):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if history and path.exists():
        hist = path.parent/"history"; hist.mkdir(exist_ok=True)
        shutil.copy2(path, hist/(path.stem+"_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f")+".json"))
    temp = path.with_suffix(path.suffix+".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
    os.replace(temp,path)

def load_or_blank(project, video):
    p = annotation_path(project, video)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("fingerprint") != fingerprint(video):
            raise ValueError("原视频已被替换或重拍。请点击“导入视频”重新选择该文件，程序会归档旧标注并建立新记录。")
        return data
    return blank(video)

def prepare_video_import(project,video):
    """Explicit reimport accepts a changed recording, retaining old JSON/cache."""
    path=annotation_path(project,video)
    if not path.exists():return False
    old=json.loads(path.read_text(encoding='utf-8'))
    current=fingerprint(video)
    if old.get('fingerprint')==current:return False
    # Do not archive usable annotations for an incomplete/corrupt replacement.
    import av
    with av.open(str(video)) as container:
        next(container.decode(video=0))
    if fingerprint(video)!=current:raise ValueError('视频仍在写入，请录制或复制完成后再导入。')
    fresh=blank(video)
    fresh['replacement']={'previous_fingerprint':old.get('fingerprint'),'imported_at':now()}
    fresh['warnings']=['已导入同名重拍视频；旧标注保留在 annotations/history，旧骨架缓存保留。请重新自动分析并审核。']
    save_json(path,fresh)
    return True

def video_available(video):
    p=Path(video)
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def queue_videos(videos,project,mode="analyze"):
    """Validate actual files at queue construction; callers supply the currently visible scope."""
    queued=[]; skipped=[]; seen=set()
    for video in videos:
        identity=str(Path(video).resolve()).casefold()
        if identity in seen: continue
        seen.add(identity)
        if not video_available(video):
            skipped.append({"video":video,"reason":"视频文件不存在或类型不支持"}); continue
        try: data=load_or_blank(project,video)
        except Exception as exc:
            skipped.append({"video":video,"reason":str(exc)}); continue
        if data["status"]=="reviewed" or any(s.get("source")=="human" or s.get("verified") for s in data["segments"]):
            skipped.append({"video":video,"reason":"保留已审核或人工时间段"}); continue
        if mode=="phases":
            cache=data.get("keypoints_file")
            if data["metadata"].get("category") not in ("BODY","HAND","CHAR","CTRL") or not cache or not (Path(project)/cache).is_file():
                skipped.append({"video":video,"reason":"没有可复用的人体或手部关键点缓存"}); continue
        elif data.get("analysis") or data["segments"]:
            skipped.append({"video":video,"reason":"已有分析或时间段；可使用仅重算阶段"}); continue
        queued.append(video)
    return queued,skipped

def resegment(data,arrays):
    """Preserve video, keypoint cache, corrections, metadata and events; replace phase proposals."""
    from phases import body_phases,ENGINE
    result=copy.deepcopy(data); a=result["analysis"]
    points=effective_points(result,arrays)
    seg,speed,unit,warnings=propose(points,arrays["times"],result["duration"],result["metadata"],a["width"],a["height"],a["kind"]=="hand")
    result["segments"]=seg; result["status"]="draft"; result["version"]=VERSION
    result["updated_at"]=now(); result.pop("reviewed_at",None)
    result["warnings"]=[w for w in result.get("warnings",[]) if "PTS" in w or "时间戳" in w]+warnings
    if result["metadata"].get("category")=="BODY":
        _,detail,_=body_phases(points,arrays["times"],result["duration"],result["metadata"],a["width"],a["height"])
        result["segmentation"]=detail
    elif result['metadata'].get('category')=='CTRL':
        from control_phases import control_phases
        _,detail,_=control_phases(points,arrays['times'],result['duration'],result['metadata'],a['width'],a['height'])
        result['segmentation']=detail
        result['suggested_events']=[]
    elif a["kind"]=="hand":
        from hand_phases import hand_phases
        _,detail,_=hand_phases(points,arrays["times"],result["duration"],result["metadata"],a["width"],a["height"])
        result["segmentation"]=detail
        # Keep manual events intact; machine suggestions are separately inspectable.
        result["suggested_events"]=detail["events"]
    result["analysis"]["speed_unit"]=unit
    return result,speed

def validate(data, reviewed=False):
    errors=[]; end=0
    for i,s in enumerate(data["segments"]):
        a,b=s["start"],s["end"]
        if not (np.isfinite(a) and np.isfinite(b) and 0<=a<b<=data["duration"]+0.001):
            errors.append(f"第{i+1}段时间越界或起止颠倒")
        if a<end-0.001: errors.append(f"第{i+1}段与上一段重叠")
        end=b
    for e in data.get("events",[]):
        if not 0<=e["time"]<=data["duration"]: errors.append("事件时间越界")
    if reviewed:
        m=data["metadata"]
        if not m.get("filename_label_verified"): errors.append("尚未确认文件名标签与实际动作一致")
        if not m.get("person_id") or not m.get("category"): errors.append("人员与类别不能为空")
        if not data["segments"]: errors.append("请至少标注一个时间段")
        if m.get("category")=="BODY" and not any(s["phase"]=="execute" for s in data["segments"]):
            errors.append("人体动作缺少 execute 执行段")
    return errors

def effective_points(data, arrays):
    pts=arrays["points"].copy()
    for frame, edits in data.get("corrections",{}).items():
        for joint, value in edits.items(): pts[int(frame),int(joint)] = value
    return pts

def runs(labels, times, duration):
    result=[]
    for i,label in enumerate(labels):
        if i==0 or label!=labels[i-1]:
            if result: result[-1]["end"]=float(times[i])
            result.append(dict(phase=str(label),start=float(times[i]),end=duration,source="auto",verified=False))
    return result

def velocity(points, times, width, height, meta, hand=False):
    """Use PTS, aspect-correct coordinates and fixed scale; never bridge invalid points."""
    n=len(points); out=np.full(n,np.nan); valid=np.zeros(n,dtype=bool)
    if not n: return out, "unknown", valid
    xy=points[:,:,:2]*[width,height]
    vis=(points[:,:,3]>.5)&np.isfinite(points[:,:,:2]).all(axis=2)&(points[:,:,:2]>=0).all(axis=2)&(points[:,:,:2]<=1).all(axis=2)
    if hand:
        distances=np.linalg.norm(xy[:,0]-xy[:,9],axis=1)
        scale_valid=vis[:,0]&vis[:,9]
        scale=np.median(distances[scale_valid][:90]) if scale_valid.any() else 0
        coords=((xy[:,4]+xy[:,8])/2)[:,None,:]
        valid=vis[:,4]&vis[:,8]&scale_valid
        unit="palm_lengths/s"
    else:
        base_valid=vis[:,11]&vis[:,12]&vis[:,23]&vis[:,24]
        widths=np.linalg.norm(xy[:,11]-xy[:,12],axis=1)
        initial=(times<=times[0]+3)&base_valid
        scale=np.median(widths[initial]) if initial.any() else 0
        hip=(xy[:,23]+xy[:,24])/2
        if meta.get("action_id")=="A04":
            shoulder=(xy[:,11]+xy[:,12])/2
            coords=np.arctan2((shoulder-hip)[:,0],-(shoulder-hip)[:,1])[:,None,None]
            scale=1; valid=base_valid; unit="radians/s"
        else:
            ids=[15,16]
            if meta.get("action_id") in ("A02","A05"):
                selected={"left":15,"right":16}.get(meta.get("active_hand"))
                if selected is None:
                    # Conservative automatic side selection; recorded as a suggestion only.
                    energy=[]
                    for j in ids:
                        d=np.linalg.norm(np.diff(xy[:,j]-hip,axis=0),axis=1)
                        good=vis[1:,j]&vis[:-1,j]&base_valid[1:]&base_valid[:-1]
                        energy.append(float(np.median(d[good])) if good.any() else 0)
                    selected=ids[int(np.argmax(energy))]
                ids=[selected]
            coords=xy[:,ids]-hip[:,None,:]
            valid=base_valid&vis[:,ids].all(axis=1); unit="shoulder_widths/s"
    if scale<1e-6: return out,unit,np.zeros(n,dtype=bool)
    coords=coords/scale
    smooth=coords.copy()
    for i in range(1,n-1):
        if valid[i-1:i+2].all(): smooth[i]=np.median(coords[i-1:i+2],axis=0)
    dt=np.diff(times)
    good=valid[1:]&valid[:-1]&(dt>0)&(dt<=.25)
    delta=smooth[1:]-smooth[:-1]
    if unit=="radians/s": delta=(delta+np.pi)%(2*np.pi)-np.pi
    values=np.mean(np.linalg.norm(delta,axis=-1),axis=-1)/np.maximum(dt,1e-9)
    out[1:]=np.where(good,values,np.nan)
    return out,unit,valid

def propose(points,times,duration,meta,width,height,hand=False):
    warnings=[]
    speed,unit,valid=velocity(points,times,width,height,meta,hand)
    if hand:
        from hand_phases import hand_phases,hand_features
        labels,_,warnings=hand_phases(points,times,duration,meta,width,height)
        valid=hand_features(points,times,width,height)["valid"]
        speed[~valid]=np.nan
        if len(speed)>1: speed[1:][~valid[:-1]]=np.nan
        segments=runs(labels,times,duration)
    elif meta["category"]=="NEG":
        segments=runs(["negative"]*len(times),times,duration)
    elif meta["category"]=="BODY":
        from phases import body_phases
        labels,details,phase_warnings=body_phases(points,times,duration,meta,width,height)
        segments=runs(labels,times,duration)
        warnings.extend(phase_warnings)
    elif meta["category"]=="CTRL":
        from control_phases import control_phases
        labels,details,control_warnings=control_phases(points,times,duration,meta,width,height)
        segments=runs(labels,times,duration)
        warnings.extend(control_warnings)
    else:
        segments=runs(["unknown"]*len(times),times,duration)
        warnings.append("控制圈数、混合主题及菜单事件请人工标注；不会根据文件名伪造成功事件。")
    return segments,speed,unit,warnings

def analyze(video, model_dir, progress=lambda x:None, cancelled=lambda:False, metadata=None):
    import av
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    data=blank(video)
    if metadata is not None: data["metadata"]=copy.deepcopy(metadata)
    meta=data["metadata"]
    hand=meta["category"] in ("HAND","CHAR")
    model=Path(model_dir)/("hand_landmarker.task" if hand else "pose_landmarker_lite.task")
    if not model.exists(): raise FileNotFoundError("模型未安装，请先运行安装脚本。")
    options=dict(base_options=python.BaseOptions(model_asset_buffer=model.read_bytes()),running_mode=vision.RunningMode.VIDEO)
    detector=(vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(**options,num_hands=2)) if hand else
              vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(**options,num_poses=1)))
    all_times=[]; sample_times=[]; indexes=[]; points=[]; hand_candidates=[]; origin=None; last_ms=-1; fallback=0
    with av.open(str(video)) as container, detector:
        stream=container.streams.video[0]
        fps=float(stream.average_rate or 30); width=stream.codec_context.width; height=stream.codec_context.height
        estimated=float(container.duration/av.time_base) if container.duration else 0
        for i,frame in enumerate(container.decode(stream)):
            if cancelled(): raise InterruptedError("分析已取消，原标注未修改。")
            if frame.pts is not None:
                raw=float(frame.pts*frame.time_base)
                if origin is None: origin=raw
                t=raw-origin
            else:
                fallback+=1; t=all_times[-1]+1/fps if all_times else 0
            if all_times and t<=all_times[-1]:
                raise ValueError("视频存在重复或倒退的帧时间戳，不能可靠计算速度；请检查源文件。")
            all_times.append(t)
            # Keep every presentation timestamp; infer at <=30 Hz without inventing frames.
            if sample_times and t-sample_times[-1]<1/30-.001: continue
            rgb=frame.to_ndarray(format="rgb24")
            limit=1440 if hand else 960
            if width>limit: rgb=cv2.resize(rgb,(limit,round(height*limit/width)))
            img=mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb))
            ms=max(last_ms+1,round(t*1000)); last_ms=ms
            result=detector.detect_for_video(img,ms)
            landmarks=result.hand_landmarks if hand else result.pose_landmarks
            row=np.zeros((21 if hand else 33,4),dtype=np.float32)
            if hand:
                candidates=np.zeros((2,21,4),dtype=np.float32)
                scores=[-1.,-1.]
                for lm,side in zip(landmarks,result.handedness):
                    slot=0 if side[0].category_name.lower()=="left" else 1
                    if side[0].score<=scores[slot]: continue
                    scores[slot]=side[0].score
                    for j,l in enumerate(lm): candidates[slot,j]=[l.x,l.y,l.z,1.]
                hand_candidates.append(candidates)
            elif landmarks:
                for j,l in enumerate(landmarks[0]): row[j]=[l.x,l.y,l.z,1.0 if hand else (l.visibility or 0)]
            points.append(row); sample_times.append(t); indexes.append(i)
            if i%15==0: progress(min(99,int(t/max(estimated,t+.1)*100)))
    if not all_times: raise ValueError("视频没有可解码画面。")
    duration=all_times[-1]+(np.median(np.diff(all_times)) if len(all_times)>1 else 1/fps)
    pts=np.asarray(points); ts=np.asarray(sample_times)
    hand_selection=None
    if hand:
        from hand_phases import select_writing_hand
        candidates=np.asarray(hand_candidates)
        pts,hand_selection=select_writing_hand(candidates,width,height,meta)
    seg,speed,unit,warnings=propose(pts,ts,float(duration),meta,width,height,hand)
    if fallback: warnings.append(f"{fallback}帧无PTS，使用帧率估算；不适合精确速度验收。")
    data.update(duration=float(duration),segments=seg,warnings=warnings,analysis={
        "engine":"MediaPipe Tasks / CPU", "model":model.name,"model_sha256":hashlib.sha256(model.read_bytes()).hexdigest(),
        "kind":"hand" if hand else "pose", "timestamp_source":"mixed_fallback" if fallback else "video_pts",
        "width":width,"height":height,"fps_nominal":fps,"decoded_frames":len(all_times),
        "samples":len(ts),"sample_rate_limit":30,"speed_unit":unit,
        "landmarks_verified":False,"label_source":"filename", "created_at":now()})
    arrays=dict(points=pts,times=ts,frame_indices=np.asarray(indexes),frame_times=np.asarray(all_times),speed=speed)
    if hand:
        arrays["hand_candidates"]=candidates
        data["analysis"]["hand_selection"]=hand_selection
        from hand_recovery import recover_gaps,recover_with_pose,recover_track_gaps,refine_visible_track
        recovery_options=vision.HandLandmarkerOptions(base_options=python.BaseOptions(model_asset_buffer=model.read_bytes()),running_mode=vision.RunningMode.IMAGE,num_hands=2)
        with vision.HandLandmarker.create_from_options(recovery_options) as recovery_detector:
            arrays['hand_pre_refinement_points']=arrays['points'].copy()
            arrays['points'],refined=refine_visible_track(video,arrays,width,height,recovery_detector,cancelled)
            arrays['hand_roi_refined']=refined
            data['analysis']['visible_roi_refinement_version']='1'
            data['analysis']['visible_roi_refined_samples']=int(refined.sum())
            arrays['points'],track_recovered=recover_track_gaps(video,arrays,width,height,recovery_detector,cancelled)
            arrays['points'],recovered=recover_gaps(video,arrays,width,height,recovery_detector)
            seed_missing=~(arrays['points'][:,[0,4,8,9],3]>.5).all(axis=1)
            arrays['points'],pose_recovered=recover_with_pose(video,arrays,width,height,recovery_detector,Path(model_dir)/'pose_landmarker_lite.task',cancelled,seed_missing)
        # Lower thresholds are confined to the body-wrist-constrained fallback.
        # All resulting keypoints remain unreviewed detector proposals.
        remaining=~(arrays['points'][:,[0,4,8,9],3]>.5).all(axis=1)
        if seed_missing.any() and not remaining.all():
            fallback_options=vision.HandLandmarkerOptions(base_options=python.BaseOptions(model_asset_buffer=model.read_bytes()),running_mode=vision.RunningMode.IMAGE,num_hands=2,min_hand_detection_confidence=.25,min_hand_presence_confidence=.3)
            with vision.HandLandmarker.create_from_options(fallback_options) as recovery_detector:
                for _ in range(2):
                    arrays['points'],extra=recover_with_pose(video,arrays,width,height,recovery_detector,Path(model_dir)/'pose_landmarker_lite.task',cancelled,seed_missing)
                    pose_recovered|=extra
                    after=~(arrays['points'][:,[0,4,8,9],3]>.5).all(axis=1)
                    if after.sum()>=remaining.sum():break
                    remaining=after
        arrays['hand_pose_roi_recovered']=pose_recovered
        arrays['hand_track_roi_recovered']=track_recovered
        data['analysis']['track_roi_recovered_samples']=int(track_recovered.sum())
        data['analysis']['backhand_recovery_version']='1'
        data['analysis']['pose_roi_recovered_samples']=int(pose_recovered.sum())
        arrays['hand_roi_recovered']=recovered
        data['analysis']['roi_recovery_version']='1'
        data['analysis']['roi_recovered_samples']=int(recovered.sum())
    data,arrays["speed"]=resegment(data,arrays)
    return data,arrays

def store_analysis(project,data,arrays):
    folder=Path(project)/"keypoints"; folder.mkdir(parents=True,exist_ok=True)
    # New immutable cache per analysis avoids destroying old reviewed landmark data.
    name=key(data["video"])+"_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f")+".npz"
    np.savez_compressed(folder/name,**arrays)
    data["keypoints_file"]="keypoints/"+name
    save_json(annotation_path(project,data["video"]),data)

def export_reviewed(project, videos, destination):
    dest=Path(destination); dest.mkdir(parents=True,exist_ok=True)
    records=[]; skipped=[]
    for video in videos:
        if not video_available(video): skipped.append(video); continue
        try: data=load_or_blank(project,video)
        except (OSError,ValueError): skipped.append(video); continue
        if data["status"]!="reviewed" or validate(data,True): skipped.append(video); continue
        m=data["metadata"]
        if m["split"]!=split_for(m["person_id"],m["session_id"]) or m["split"]=="unassigned":
            skipped.append(video); continue
        item=copy.deepcopy(data); tag=Path(video).stem+"_"+key(video)
        if data.get("keypoints_file"):
            with np.load(Path(project)/data["keypoints_file"]) as z: arrays={k:z[k] for k in z.files}
            arrays["points"]=effective_points(data,arrays)
            a=data["analysis"]
            arrays["speed"],_,_=velocity(arrays["points"],arrays["times"],a["width"],a["height"],m,a["kind"]=="hand")
            if a["kind"]=="hand":
                from hand_phases import hand_features
                features=hand_features(arrays["points"],arrays["times"],a["width"],a["height"])
                arrays.update({"hand_"+k:v for k,v in features.items()})
                inferred=np.zeros(len(arrays['times']),dtype=bool)
                for interval in data.get('segmentation',{}).get('inferred_state_intervals',[]):
                    inferred|=(arrays['times']>=interval['start'])&(arrays['times']<interval['end'])
                arrays['hand_state_inferred']=inferred
                arrays["speed"][~features["valid"]]=np.nan
                if len(arrays["speed"])>1: arrays["speed"][1:][~features["valid"][:-1]]=np.nan
            mask=np.zeros(len(arrays["times"]),dtype=bool)
            for s in data["segments"]:
                if s["phase"] in ("execute","down"):
                    mask|=(arrays["times"]>=s["start"])&(arrays["times"]<s["end"])
            arrays["ink_speed"]=np.where(mask,arrays["speed"],np.nan)
            np.savez_compressed(dest/(tag+".npz"),**arrays)
            item["keypoints_file"]=tag+".npz"
            item["corrections_applied"]=True
        save_json(dest/(tag+".json"),item,False); records.append(item)
    for split in ("train","validation","test"):
        with (dest/(split+".jsonl")).open("w",encoding="utf-8") as f:
            for r in records:
                if r["metadata"]["split"]==split: f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+"\n")
    save_json(dest/"export_report.json",{"exported":len(records),"skipped":skipped,"created_at":now()},False)
    return len(records),len(skipped)
