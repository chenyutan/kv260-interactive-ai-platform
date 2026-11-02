import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
import numpy as np
import pytest
from core import blank,save_json,annotation_path,queue_videos,resegment,store_analysis
from phases import body_phases
from test_inklabel import video

def pose_sequence(action,profile="fast",active="right"):
    t=np.arange(0,19,1/30); n=len(t)
    base=np.zeros((33,4)); base[:,3]=1
    base[11,:2]=[.6,.3]; base[12,:2]=[.4,.3]
    base[23,:2]=[.55,.6]; base[24,:2]=[.45,.6]
    base[15,:2]=[.62,.62]; base[16,:2]=[.38,.62]
    base[13,:2]=[.62,.45]; base[14,:2]=[.38,.45]
    ready=base.copy(); end=base.copy(); wrist=16 if active=="right" else 15
    if action=="A01":
        ready[[15,16],:2]=[[.55,.34],[.45,.34]]; end[[15,16],:2]=[[.9,.3],[.1,.3]]
    elif action=="A02":
        ready[wrist,:2]=[base[wrist,0],.5];end[wrist,:2]=[base[wrist,0],.04]
    elif action=="A03":
        ready[[15,16],:2]=[[.6,.3],[.4,.3]];end[[15,16],:2]=[[.75,.08],[.25,.08]]
    elif action=="A04":
        ready[[15,16],:2]=[[.9,.35],[.1,.35]];end=ready.copy()
        # Rotate shoulders and arms around hip center; use square coordinates in this fixture.
        theta=.35;rot=np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
        end[[11,12,13,14,15,16],:2]=(ready[[11,12,13,14,15,16],:2]-[.5,.6])@rot.T+[.5,.6]
    elif action=="A05":
        ready[wrist,:2]=[.1 if active=="right" else .9,.3]
        end[wrist,:2]=[.65 if active=="right" else .35,.3]
    else:
        ready[[15,16],:2]=[[.6,.47],[.4,.47]];end[[15,16],:2]=[[.6,.3],[.4,.3]]
    p=np.repeat(base[None],n,axis=0)
    endtime=9 if profile=="fast" else 11
    for i,sec in enumerate(t):
        if sec<2: row=base
        elif sec<3: row=base+(ready-base)*(sec-2)
        elif sec<8: row=ready
        elif sec<endtime:
            if profile=="pause":
                u=min((sec-8)/.4,.65) if sec<9.05 else .65+(sec-9.05)/(endtime-9.05)*.35
            else: u=(sec-8)/(endtime-8)
            row=ready+(end-ready)*u
        elif sec<14: row=end
        elif sec<17: row=end+(base-end)*(sec-14)/3
        else: row=base
        p[i]=row
    return t,p,endtime

@pytest.mark.parametrize("action",["A01","A02","A03","A04","A05","A06"])
@pytest.mark.parametrize("profile",["fast","slow"])
def test_direction_rejects_longer_return(action,profile):
    t,p,end=pose_sequence(action,profile)
    labels,d,w=body_phases(p,t,19,{"action_id":action,"active_hand":"right"},1000,1000)
    assert abs(d["execute_start"]-8)<.35,(action,d)
    assert abs(d["execute_end"]-end)<.35,(action,d)
    assert labels[np.argmin(abs(t-15))]=="return"
    assert labels[np.argmin(abs(t-5))]=="ready"

def test_fast_slow_pause_is_one_execution():
    t,p,end=pose_sequence("A06","pause")
    labels,d,w=body_phases(p,t,19,{"action_id":"A06","active_hand":"both"},1000,1000)
    assert d["execute_start"]<8.2 and d["execute_end"]>10.7
    assert labels[np.argmin(abs(t-8.8))]=="execute"
    assert any("停顿" in text for text in w)

def test_left_hand_sweep():
    t,p,end=pose_sequence("A05","fast","left")
    _,d,_=body_phases(p,t,19,{"action_id":"A05","active_hand":"left"},1000,1000)
    assert abs(d["execute_start"]-8)<.35 and abs(d["execute_end"]-9)<.35

def test_stationary_and_low_amplitude_rejected():
    t,p,_=pose_sequence("A02")
    p[:]=p[0]
    labels,d,w=body_phases(p,t,19,{"action_id":"A02"},1000,1000)
    assert set(labels)=={"unknown"}

def test_queue_missing_duplicate_and_review_protection(video,tmp_path):
    missing=tmp_path/"P002_S01_C01_BODY_A01_FAST_R01.mp4"
    queue,skip=queue_videos([str(video),str(video),str(missing)],tmp_path)
    assert queue==[str(video)] and len(skip)==1
    d=blank(video);d["status"]="reviewed";save_json(annotation_path(tmp_path,video),d)
    assert not queue_videos([str(video)],tmp_path)[0]

def test_cached_resegment_preserves_landmarks_and_metadata(video,tmp_path):
    d=blank(video);t,p,end=pose_sequence("A02");d["duration"]=19
    d["analysis"]={"width":1000,"height":1000,"kind":"pose"}
    d["metadata"]["active_hand"]="right";d["metadata"]["person_id"]="P002"
    d["events"]=[{"time":1,"type":"test"}];d["notes"]="keep notes";d["corrections"]={"1":{"15":[.62,.62,0,1]}}
    a=dict(points=p,times=t,frame_times=t,frame_indices=np.arange(len(t)),speed=np.zeros(len(t)))
    store_analysis(tmp_path,d,a);before=(tmp_path/d["keypoints_file"]).read_bytes()
    new,speed=resegment(d,a)
    assert new["metadata"]["person_id"]=="P002" and new["events"]==d["events"]
    assert new["notes"]==d["notes"] and new["corrections"]==d["corrections"]
    assert (tmp_path/d["keypoints_file"]).read_bytes()==before

def test_gui_person_filter_controls_queue(video,tmp_path):
    import shutil
    from PySide6.QtWidgets import QApplication
    from app import Window
    application=QApplication.instance() or QApplication([])
    p2=tmp_path/video.name.replace("P001","P002");shutil.copy2(video,p2)
    win=Window();win.open_project(tmp_path/"filter_project");win.add_files([str(video),str(p2)])
    win.person_filter.setCurrentIndex(win.person_filter.findData("P001"))
    assert win.visible_videos()==[str(video)]
    win.open_project(tmp_path/"filter_project")
    assert win.person_filter.currentData()=="P001" and win.visible_videos()==[str(video)]
    win.start_analysis(True)
    assert win.worker.videos==[str(video)]
    win.worker.requestInterruption();win.worker.wait(10000);application.processEvents()
    assert not win.worker.isRunning()
    win.close();application.processEvents();win.settings.remove("project")
