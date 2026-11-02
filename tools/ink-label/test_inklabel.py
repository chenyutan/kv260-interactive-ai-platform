import json
import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import av
import numpy as np
import pytest
from core import *

@pytest.fixture
def video(tmp_path):
    path=tmp_path/"P001_S01_C01_BODY_A02_SLOW_R01.mp4"
    with av.open(str(path),"w") as c:
        s=c.add_stream("mpeg4",rate=30); s.width=320; s.height=240; s.pix_fmt="yuv420p"
        for i in range(45):
            im=np.zeros((240,320,3),np.uint8); im[60:100,20+i:50+i]=[120,210,100]
            frame=av.VideoFrame.from_ndarray(im,format="rgb24")
            for p in s.encode(frame): c.mux(p)
        for p in s.encode(): c.mux(p)
    return path

def test_filename_categories():
    for cat,action in [("BODY","A06"),("HAND","B08"),("CHAR","YUE"),("MIX","M03")]:
        m=parse_name(f"P003_S02_C03_{cat}_{action}_FAST_R02.mp4")
        assert m["action_id"]==action and m["split"]=="test"
    m=parse_name("P001_S01_C01_CTRL_NEG_A05_R01.mkv")
    assert m["control_validity"]=="NEG" and m["negative_type"]=="A05"
    assert split_for("P001","S02")=="validation"
    assert split_for("P004","S01")=="unassigned"

def test_persistence_and_collision(video,tmp_path):
    d=blank(video); p=annotation_path(tmp_path,video); save_json(p,d); save_json(p,d)
    assert load_or_blank(tmp_path,video)["video"]==str(video)
    assert len(list((p.parent/"history").glob("*.json")))==1
    assert annotation_path(tmp_path,tmp_path/"other"/video.name)!=p
    with video.open("ab") as f: f.write(b"modified")
    with pytest.raises(ValueError): load_or_blank(tmp_path,video)

def test_validation(video):
    d=blank(video); d["duration"]=2
    d["segments"]=[dict(phase="execute",start=.1,end=1),dict(phase="return",start=.9,end=1.5)]
    assert validate(d)
    d["segments"][1]["start"]=1
    assert not validate(d)
    assert validate(d,True)
    d["metadata"]["filename_label_verified"]=True
    assert not validate(d,True)

def skeleton(n):
    p=np.zeros((n,33,4)); p[:,:,3]=1
    p[:,11,:2]=[.4,.3]; p[:,12,:2]=[.6,.3]
    p[:,23,:2]=[.45,.6]; p[:,24,:2]=[.55,.6]
    p[:,15,:2]=[.3,.4]; p[:,16,:2]=[.7,.4]
    return p

def test_speed_symmetric_does_not_cancel():
    t=np.arange(30)/30; p=skeleton(30)
    p[:,15,0]-=t*.1; p[:,16,0]+=t*.1
    speed,unit,valid=velocity(p,t,1920,1080,{"action_id":"A01"})
    assert np.allclose(speed[2:-2],.5) and unit=="shoulder_widths/s"

def test_speed_gap_not_connected():
    t=np.array([0,.03,.06,.6,.63]); p=skeleton(5); p[:,16,0]+=t*.1
    p[1,16,3]=0
    speed,_,_=velocity(p,t,1920,1080,{"action_id":"A02","active_hand":"right"})
    assert np.isnan(speed[:4]).all() and np.isfinite(speed[4])

def test_missing_never_creates_execute():
    times=np.arange(60)/30; p=np.zeros((60,33,4))
    segments,_,_,_=propose(p,times,2,parse_name("P001_S01_C01_BODY_A01_SLOW_R01.mp4"),640,480)
    assert all(s["phase"]=="unknown" for s in segments)

def test_control_does_not_fabricate_events():
    t=np.arange(60)/30; p=skeleton(60)
    seg,_,_,_=propose(p,t,2,parse_name("P001_S01_C01_CTRL_G01_POS_CW_R01.mp4"),640,480)
    assert all(s["phase"]=="unknown" for s in seg)

def test_export_applies_corrections_and_excludes_unreviewed(video,tmp_path):
    project=tmp_path/"project"; d=blank(video); d["duration"]=1.5
    d["metadata"]["filename_label_verified"]=True; d["status"]="reviewed"
    d["segments"]=[dict(phase="execute",start=.2,end=.8)]
    d["analysis"]=dict(width=320,height=240,kind="pose",landmarks_verified=False)
    p=skeleton(45); t=np.arange(45)/30
    a=dict(points=p,times=t,frame_times=t,frame_indices=np.arange(45),speed=np.zeros(45))
    d["corrections"]={"10":{"16":[.8,.2,0,1]}}
    store_analysis(project,d,a)
    dest=tmp_path/"export"; assert export_reviewed(project,[str(video)],dest)==(1,0)
    with np.load(next(dest.glob("*.npz"))) as z:
        assert z["points"][10,16,0]==.8
        assert np.isnan(z["ink_speed"][:6]).all()
    assert len((dest/"train.jsonl").read_text().splitlines())==1
    assert not (dest/"test.jsonl").read_text()
    d["status"]="draft"; save_json(annotation_path(project,video),d)
    assert export_reviewed(project,[str(video)],tmp_path/"out2")== (0,1)

def test_real_model_blank_video(video,tmp_path):
    d,a=analyze(video,Path(__file__).parent/"models")
    assert d["analysis"]["timestamp_source"]=="video_pts"
    assert len(a["frame_times"])==45 and np.all(np.diff(a["times"])>0)
    assert all(s["phase"]=="unknown" for s in d["segments"])
    hand=parse_name("P001_S01_C01_HAND_B02_SLOW_R01.mp4")
    d,a=analyze(video,Path(__file__).parent/"models",metadata=hand)
    assert a["points"].shape[1]==21
    assert all(s["phase"]=="unknown" for s in d["segments"])

def test_gui_roundtrip(video,tmp_path):
    from PySide6.QtWidgets import QApplication
    from app import Window
    application=QApplication.instance() or QApplication([])
    win=Window(); win.message=lambda text: (_ for _ in ()).throw(AssertionError(text))
    win.open_project(tmp_path/"gui_project"); win.add_files([str(video)])
    win.show(); application.processEvents()
    assert win.preview.image is not None
    win.seek(.4); assert abs(win.current_time-.4)<.04
    win.phase.setCurrentIndex(win.phase.findData("execute")); win.add_segment()
    assert win.data["segments"][0]["phase"]=="execute"
    win.drag_boundary(0,"start",.2); assert win.data["segments"][0]["start"]==.2
    win.undo(); assert win.data["segments"][0]["start"]>.2
    win.verified.setChecked(True); win.review(); assert win.data["status"]=="reviewed"
    win.save(); saved=load_or_blank(tmp_path/"gui_project",video); assert saved["status"]=="reviewed"
    win.grab().save(str(Path(__file__).parent/"界面预览.png"))
    win.close(); application.processEvents(); win.settings.remove("project")

def test_slider_mouse_drag_and_keyboard(video,tmp_path):
    from PySide6.QtWidgets import QApplication, QStyleOptionSlider, QStyle
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    from app import Window
    application=QApplication.instance() or QApplication([])
    win=Window(); win.open_project(tmp_path/"slider_project"); win.add_files([str(video)])
    win.show(); application.processEvents()
    slider=win.slider
    def drag(fraction):
        option=QStyleOptionSlider(); slider.initStyleOption(option)
        handle=slider.style().subControlRect(QStyle.CC_Slider,option,QStyle.SC_SliderHandle,slider)
        target=QPoint(int(slider.width()*fraction),handle.center().y())
        QTest.mousePress(slider,Qt.LeftButton,pos=handle.center())
        assert slider.isSliderDown() and not win.playing
        QTest.mouseMove(slider,target)
        QTest.mouseRelease(slider,Qt.LeftButton,pos=target)
        application.processEvents()
        assert abs(win.current_time-win.data["duration"]*fraction)<.08
    try:
        win.toggle_play(); drag(.75)
        drag(.25)
        before=win.current_time
        QTest.keyClick(slider,Qt.Key_End)
        assert win.current_time>before and win.current_time>1.4
        # Programmatic frame refresh must not recursively seek.
        original=win.seek; calls=[]
        def counted(t):
            calls.append(t); original(t)
        win.seek=counted; win.seek(.5)
        assert len(calls)==1
    finally:
        win.close(); application.processEvents()

def test_background_worker_and_reopen(video,tmp_path):
    import time
    from PySide6.QtWidgets import QApplication
    from app import Window
    application=QApplication.instance() or QApplication([])
    win=Window(); win.message=lambda text: (_ for _ in ()).throw(AssertionError(text))
    project=tmp_path/"worker_project"
    win.open_project(project); win.add_files([str(video)]); win.start_analysis(True)
    deadline=time.monotonic()+30
    while win.worker.isRunning() and time.monotonic()<deadline:
        application.processEvents(); time.sleep(.01)
    application.processEvents()
    assert not win.worker.isRunning()
    assert win.data.get("analysis") and win.arrays is not None
    win.open_project(project)
    assert win.data is not None and win.preview.image is not None and win.arrays is not None
    win.close(); application.processEvents(); win.settings.remove("project")
