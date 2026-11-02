"""Public static image -> short video. Verifies real pose inference and UI, NOT gesture accuracy."""
import os
os.environ["QT_QPA_PLATFORM"]="offscreen"
import time
from pathlib import Path
import av
import cv2
import numpy as np
from PySide6.QtWidgets import QApplication
from core import analyze,store_analysis
from app import Window

root=Path(__file__).resolve().parent
assets=root/"test_assets"
source=assets/"pose.jpg"
image=cv2.imread(str(source))
assert image is not None
rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
h,w=rgb.shape[:2]; h=h//2*2; w=w//2*2; rgb=rgb[:h,:w]
video=assets/"DEMO_POSE_STATIC.mp4"
with av.open(str(video),"w") as c:
    s=c.add_stream("mpeg4",rate=30); s.width=w; s.height=h; s.pix_fmt="yuv420p"
    for i in range(60):
        f=av.VideoFrame.from_ndarray(rgb,format="rgb24")
        for p in s.encode(f): c.mux(p)
    for p in s.encode(): c.mux(p)
d,a=analyze(video,root/"models")
assert (a["points"][:,:,3]>.5).any(), "Public sample did not produce visible landmarks"
d["notes"]="公开静态人体样图组成的测试视频，仅验证模型与界面，不是项目训练样本。"
project=assets/"demo_project"; store_analysis(project,d,a)
app=QApplication.instance() or QApplication([])
win=Window(); win.open_project(project); win.add_files([str(video.resolve())]); win.show(); app.processEvents()
win.seek(.5); win.edit_points.setChecked(True); app.processEvents()
assert win.preview.points is not None
before=win.preview.points[16,0]
win.move_point(16,float(before+.01),float(win.preview.points[16,1])); assert win.data["corrections"]
win.undo(); assert not win.data["corrections"]
win.edit_points.setChecked(False); win.toggle_play()
until=time.monotonic()+.4
while time.monotonic()<until: app.processEvents(); time.sleep(.01)
win.pause(); assert win.current_time>.5
win.subtitle.setText("公开样图演示 · 仅验证骨架提取，不用于动作训练")
win.video_title.setText("DEMO_POSE_STATIC.mp4  |  Google 官方公开人体样图")
win.warning.setText("这是静态样图测试。真实拍摄视频的动作时间段仍需人工复核。")
app.processEvents(); win.grab().save(str(root/"界面预览.png"))
win.close(); app.processEvents(); win.settings.remove("project")
print("Positive pose inference, landmark edit/undo, playback, and screenshot PASS.")
