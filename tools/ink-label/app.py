from __future__ import annotations
import bisect
import copy
import json
import sys
import time
import traceback
from pathlib import Path
import av
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRectF, QSettings
from PySide6.QtGui import QColor, QPainter, QPen, QImage, QPixmap, QShortcut, QKeySequence, QFontDatabase
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QFileDialog,QListWidget,QSplitter,QComboBox,QLineEdit,QFormLayout,
    QDoubleSpinBox,QCheckBox,QTextEdit,QMessageBox,QProgressBar,QSlider,QGroupBox,QScrollArea,QTabWidget)
from core import *

ROOT=Path(__file__).resolve().parent
COLORS={"execute":"#20b6a3","down":"#20b6a3","prepare":"#7d8fbb","ready":"#a899d5",
        "hold":"#e8bc69","return":"#d88579","unknown":"#59667a","idle":"#38475c",
        "up":"#6883ae","negative":"#536175","circle1":"#ad8fe8","circle2":"#cba3ed"}

class Worker(QThread):
    progress=Signal(str,int); result=Signal(str,object,object); error=Signal(str,str); skipped=Signal(str,str)
    def __init__(self,videos,project,mode="analyze"):
        super().__init__(); self.videos=videos; self.project=project; self.mode=mode
    def run(self):
        for video in self.videos:
            if self.isInterruptionRequested(): break
            try:
                if not video_available(video):
                    self.skipped.emit(video,"视频已删除或移动，未启动分析"); continue
                self.progress.emit(video,0)
                previous=load_or_blank(self.project,video)
                if self.mode=="phases":
                    with np.load(Path(self.project)/previous["keypoints_file"]) as z: a={k:z[k] for k in z.files}
                    d,a["speed"]=resegment(previous,a)
                else:
                    d,a=analyze(video,ROOT/"models",lambda p:self.progress.emit(video,p),self.isInterruptionRequested,previous["metadata"])
                self.result.emit(video,d,a)
            except InterruptedError: break
            except Exception as exc:
                self.error.emit(video,str(exc)+"\n"+traceback.format_exc())

class Preview(QWidget):
    pointMoved=Signal(int,float,float); pointHidden=Signal(int)
    def __init__(self):
        super().__init__(); self.setMinimumSize(400,260)
        self.image=None; self.points=None; self.edges=[]; self.edit=False; self.drag=None; self.box=QRectF()
    def paintEvent(self,event):
        p=QPainter(self); p.fillRect(self.rect(),QColor("#090f18")); p.setRenderHint(QPainter.Antialiasing)
        if self.image is None:
            p.setPen(QColor("#8b9ab1")); p.drawText(self.rect(),Qt.AlignCenter,"导入拍摄视频\n\n自动建议 · 骨架复核 · 时间轴修正")
            return
        w,h=self.image.width(),self.image.height(); scale=min(self.width()/w,self.height()/h)
        self.box=QRectF((self.width()-w*scale)/2,(self.height()-h*scale)/2,w*scale,h*scale)
        p.drawImage(self.box,self.image)
        if self.points is not None:
            def pos(i): return (self.box.x()+self.points[i,0]*self.box.width(),self.box.y()+self.points[i,1]*self.box.height())
            p.setPen(QPen(QColor("#20e2bd"),2))
            for a,b in self.edges:
                if self.points[a,3]>.5 and self.points[b,3]>.5:
                    x,y=pos(a); xx,yy=pos(b); p.drawLine(int(x),int(y),int(xx),int(yy))
            for i,row in enumerate(self.points):
                if row[3]>.5:
                    x,y=pos(i); p.setBrush(QColor("#fff4cc")); p.drawEllipse(QRectF(x-3,y-3,6,6))
                    if self.edit: p.drawText(int(x+5),int(y-4),str(i))
    def mousePressEvent(self,event):
        if not self.edit or self.points is None: return
        candidates=[]
        for i,row in enumerate(self.points):
            if row[3]>.5:
                x=self.box.x()+row[0]*self.box.width(); y=self.box.y()+row[1]*self.box.height()
                candidates.append(((x-event.position().x())**2+(y-event.position().y())**2,i))
        if candidates:
            distance,i=min(candidates)
            if distance<400:
                if event.button()==Qt.RightButton: self.pointHidden.emit(i)
                else: self.drag=i
    def mouseMoveEvent(self,event):
        if self.drag is not None:
            x=np.clip((event.position().x()-self.box.x())/self.box.width(),0,1)
            y=np.clip((event.position().y()-self.box.y())/self.box.height(),0,1)
            self.points[self.drag,:2]=[x,y]; self.update()
    def mouseReleaseEvent(self,event):
        if self.drag is not None:
            self.pointMoved.emit(self.drag,float(self.points[self.drag,0]),float(self.points[self.drag,1])); self.drag=None

class Timeline(QWidget):
    seek=Signal(float); boundaryMoved=Signal(int,str,float); selected=Signal(int)
    def __init__(self):
        super().__init__(); self.setMinimumHeight(134); self.data=None; self.arrays=None; self.t=0; self.drag=None
    def tx(self,t): return 12+t/max(self.data["duration"],.001)*(self.width()-24)
    def time_at(self,x): return float(np.clip((x-12)/max(1,self.width()-24)*self.data["duration"],0,self.data["duration"]))
    def paintEvent(self,event):
        p=QPainter(self); p.fillRect(self.rect(),QColor("#101c2c")); p.setRenderHint(QPainter.Antialiasing)
        if not self.data or not self.data["duration"]: return
        if self.arrays is not None:
            ts=self.arrays["times"]; speeds=self.arrays["speed"]
            vals=speeds[np.isfinite(speeds)]; maximum=max(.001,float(np.percentile(vals,95))) if len(vals) else 1
            p.setPen(QPen(QColor("#56d6cb"),1.5)); last=None
            step=max(1,len(ts)//max(self.width(),1))
            for t,v in zip(ts[::step],speeds[::step]):
                if not np.isfinite(v): last=None; continue
                point=(self.tx(t),56-min(v/maximum,1)*40)
                if last: p.drawLine(int(last[0]),int(last[1]),int(point[0]),int(point[1]))
                last=point
        for i,s in enumerate(self.data["segments"]):
            x=self.tx(s["start"]); right=self.tx(s["end"])
            p.fillRect(QRectF(x,67,max(1,right-x-1),30),QColor(COLORS.get(s["phase"],"#6f83a7")))
            p.setPen(QColor("#ffffff"))
            if right-x>42: p.drawText(QRectF(x+3,67,right-x-4,30),Qt.AlignCenter,PHASES.get(s["phase"],s["phase"]))
        p.setPen(QPen(QColor("#ffe6ae"),2)); p.drawLine(int(self.tx(self.t)),3,int(self.tx(self.t)),103)
        p.setPen(QColor("#94a6be")); p.drawText(12,123,"点击定位 · 拖动色块左右边缘改时间 · 上方为运动速度（显示截顶）")
    def mousePressEvent(self,event):
        if not self.data: return
        x=event.position().x(); y=event.position().y()
        if 65<=y<=100:
            for i,s in enumerate(self.data["segments"]):
                for side in ("start","end"):
                    if abs(x-self.tx(s[side]))<6:
                        self.drag=(i,side); self.selected.emit(i); return
            for i,s in enumerate(self.data["segments"]):
                if s["start"]<=self.time_at(x)<=s["end"]: self.selected.emit(i); break
        self.seek.emit(self.time_at(x))
    def mouseMoveEvent(self,event):
        if self.drag:
            self.seek.emit(self.time_at(event.position().x()))
    def mouseReleaseEvent(self,event):
        if self.drag:
            i,side=self.drag; self.drag=None; self.boundaryMoved.emit(i,side,self.time_at(event.position().x()))

class Window(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(f"墨迹标注台 · InkLabel {VERSION}"); self.resize(1440,940)
        if Path("C:/Windows/Fonts/msyh.ttc").exists(): QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        self.project=None; self.videos=[]; self.data=None; self.arrays=None; self.container=None
        self.current_time=0.; self.current_sample=None; self.worker=None; self.history=[]; self.loading=False
        self.playing=False; self.anchor=0.; self.started=0.; self.frame_iterator=None
        self.settings=QSettings("InkLabel","Desktop")
        self.timer=QTimer(self); self.timer.setInterval(25); self.timer.timeout.connect(self.tick)
        self.autosave=QTimer(self); self.autosave.setSingleShot(True); self.autosave.setInterval(800); self.autosave.timeout.connect(self.save)
        self.build()
        for keys,fn in [("Space",self.toggle_play),("Left",lambda:self.step(-1)),("Right",lambda:self.step(1)),
                        ("Ctrl+S",self.save),("Ctrl+Z",self.undo),("I",lambda:self.set_boundary("start")),("O",lambda:self.set_boundary("end")),
                        ("Ctrl+Return",self.review),("Delete",self.delete_segment)]:
            shortcut=QShortcut(QKeySequence(keys),self); shortcut.activated.connect(fn)
        last=self.settings.value("project","")
        if last and (Path(last)/"project.json").exists():
            try: self.open_project(last)
            except Exception as e: self.statusBar().showMessage(str(e))

    def button(self,text,fn,layout):
        b=QPushButton(text); b.clicked.connect(fn); layout.addWidget(b); return b

    def build(self):
        root=QWidget(); outer=QVBoxLayout(root); outer.setContentsMargins(20,16,20,12); outer.setSpacing(12)
        title=QLabel("墨迹标注台  /  INKLABEL"); title.setObjectName("title"); outer.addWidget(title)
        self.subtitle=QLabel("本地自动预标注 → 回放修正 → 审核导出。原视频保持不动。"); outer.addWidget(self.subtitle)
        bar=QHBoxLayout(); outer.addLayout(bar)
        self.button("打开 / 新建工程",self.choose_project,bar)
        self.button("导入视频",self.import_files,bar); self.button("导入文件夹",self.import_folder,bar)
        self.auto_btn=self.button("自动分析当前",lambda:self.start_analysis(False),bar)
        self.batch_btn=self.button("分析筛选内未处理",lambda:self.start_analysis(True),bar)
        self.cancel_btn=self.button("取消分析",self.cancel_analysis,bar); self.cancel_btn.setEnabled(False)
        bar.addStretch(); self.button("导出筛选内已审核",self.export,bar)
        split=QSplitter(); outer.addWidget(split,1)
        left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,8,0)
        self.search=QLineEdit(); self.search.setPlaceholderText("搜索人员、动作或文件名…"); self.search.textChanged.connect(self.filter_files); ll.addWidget(self.search)
        self.person_filter=QComboBox(); self.person_filter.addItem("全部人员","all"); self.person_filter.currentIndexChanged.connect(lambda:self.filter_files(self.search.text())); ll.addWidget(self.person_filter)
        self.reshoot_filter=QComboBox()
        for label,value in [('全部重拍状态','all'),('仅看需要重拍','yes'),('排除需要重拍','no')]:self.reshoot_filter.addItem(label,value)
        self.reshoot_filter.currentIndexChanged.connect(lambda:self.filter_files(self.search.text())); ll.addWidget(self.reshoot_filter)
        self.scope_label=QLabel("批量操作范围：0 条"); self.scope_label.setWordWrap(True); ll.addWidget(self.scope_label)
        self.file_list=QListWidget(); self.file_list.currentRowChanged.connect(self.select_video); ll.addWidget(self.file_list)
        self.remove_btn=self.button("从列表移除当前视频",self.remove_video,ll)
        self.remove_btn.setToolTip("仅移出当前工程列表；原视频、标注和骨架缓存均保留，可重新导入。")
        self.stats=QLabel("尚未导入视频"); self.stats.setWordWrap(True); ll.addWidget(self.stats); split.addWidget(left)
        self.phase_btn=self.button("仅重算当前阶段",lambda:self.start_analysis(False,"phases"),ll)
        self.phase_batch_btn=self.button("重算筛选内阶段（复用骨架）",lambda:self.start_analysis(True,"phases"),ll)
        middle=QWidget(); ml=QVBoxLayout(middle); ml.setContentsMargins(2,0,2,0)
        self.video_title=QLabel("等待导入"); self.video_title.setWordWrap(True); ml.addWidget(self.video_title)
        self.phase_caption=QLabel("当前画面阶段：—"); self.phase_caption.setStyleSheet("color:#50dcc4;"); ml.addWidget(self.phase_caption)
        self.hand_caption=QLabel(); self.hand_caption.setWordWrap(True); self.hand_caption.hide(); ml.addWidget(self.hand_caption)
        self.preview=Preview(); ml.addWidget(self.preview,1)
        self.preview.pointMoved.connect(self.move_point); self.preview.pointHidden.connect(self.hide_point)
        playback=QHBoxLayout(); ml.addLayout(playback)
        self.button("上一帧",lambda:self.step(-1),playback); self.play_btn=self.button("播放  Space",self.toggle_play,playback)
        self.button("下一帧",lambda:self.step(1),playback)
        self.rate=QComboBox(); self.rate.addItems(["0.25×","0.5×","1×","1.5×","2×"]); self.rate.setCurrentIndex(2); playback.addWidget(self.rate)
        self.rate.currentIndexChanged.connect(lambda:self.pause())
        self.time_label=QLabel("0.000 / 0.000 s"); playback.addWidget(self.time_label); playback.addStretch()
        self.overlay=QCheckBox("显示骨架"); self.overlay.setChecked(True); self.overlay.toggled.connect(self.refresh_overlay); playback.addWidget(self.overlay)
        self.edit_points=QCheckBox("修正关键点"); self.edit_points.toggled.connect(self.point_mode); playback.addWidget(self.edit_points)
        self.slider=QSlider(Qt.Horizontal); self.slider.setRange(0,10000)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.sliderReleased.connect(lambda:self.seek_slider(self.slider.sliderPosition()))
        self.slider.valueChanged.connect(lambda v:self.seek_slider(v) if not self.slider.isSliderDown() else None)
        self.slider.setToolTip("拖动圆点，松开后定位到对应画面；也可点击轨道或用方向键调整。")
        ml.addWidget(self.slider)
        self.timeline=Timeline(); self.timeline.seek.connect(self.seek); self.timeline.boundaryMoved.connect(self.drag_boundary)
        self.timeline.selected.connect(lambda i:self.segment_list.setCurrentRow(i)); ml.addWidget(self.timeline)
        self.warning=QLabel("自动分析使用本机 CPU；不上传拍摄视频。"); self.warning.setWordWrap(True); self.warning.setObjectName("warning"); ml.addWidget(self.warning)
        split.addWidget(middle)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(8,0,0,0)
        self.review_label=QLabel("待处理"); self.review_label.setObjectName("badge"); rl.addWidget(self.review_label)
        self.reshoot=QCheckBox('需要重拍'); self.reshoot.setToolTip('人工标记视频质量问题；可取消，不修改已有动作时间段。'); rl.addWidget(self.reshoot)
        self.reshoot_reason=QLineEdit(); self.reshoot_reason.setPlaceholderText('重拍原因（可选）：出框、遮挡、动作错误…'); rl.addWidget(self.reshoot_reason)
        self.reshoot.toggled.connect(self.reshoot_changed); self.reshoot_reason.editingFinished.connect(self.reshoot_changed)
        tabs=QTabWidget(); rl.addWidget(tabs,1)
        metadata_panel=QWidget(); metadata_layout=QVBoxLayout(metadata_panel)
        segments_panel=QWidget(); segments_layout=QVBoxLayout(segments_panel)
        event_panel=QWidget(); event_layout=QVBoxLayout(event_panel)
        tabs.addTab(segments_panel,"时间段"); tabs.addTab(metadata_panel,"动作标签"); tabs.addTab(event_panel,"事件 / 备注")
        form=QFormLayout(); metadata_layout.addLayout(form); self.fields={}
        for field,label,choices in [
            ("person_id","人员",None),("session_id","场次",None),("clothing_id","服装",None),
            ("category","类别",["","BODY","HAND","CHAR","CTRL","MIX","NEG"]),
            ("action_id","动作 / 字 / 流程",None),
            ("speed_instruction","速度指令",["","SLOW","MID","FAST","SF","FS","NAT"]),
            ("active_hand","活动手",["unknown","left","right","both"]),
            ("direction","动作方向",["unknown","left","right","CW","CCW"]),
            ("control_validity","切换有效性",["","POS","NEG"]),
            ("control_direction","画圈方向",["","CW","CCW"]),
            ("negative_type","负例类型",None)]:
            if choices:
                widget=QComboBox(); widget.addItems(choices); widget.currentTextChanged.connect(self.meta_changed)
            else:
                widget=QLineEdit(); widget.editingFinished.connect(self.meta_changed)
            self.fields[field]=widget; form.addRow(label,widget)
        self.split_label=QLabel("划分：—"); self.split_label.setWordWrap(True); metadata_layout.addWidget(self.split_label); metadata_layout.addStretch()
        self.verified=QCheckBox("已核对实际动作与标签一致"); self.verified.toggled.connect(self.meta_changed); rl.addWidget(self.verified)
        segments_layout.addWidget(QLabel("时间段  /  点击条目定位"))
        self.segment_list=QListWidget(); self.segment_list.setMinimumHeight(115); self.segment_list.currentRowChanged.connect(self.segment_selected); segments_layout.addWidget(self.segment_list,1)
        edit=QHBoxLayout(); segments_layout.addLayout(edit)
        self.phase=QComboBox()
        for k,v in PHASES.items(): self.phase.addItem(v+" / "+k,k)
        edit.addWidget(self.phase)
        self.start=QDoubleSpinBox(); self.end=QDoubleSpinBox()
        for box in (self.start,self.end): box.setRange(0,100000); box.setDecimals(3); box.setSingleStep(.033)
        times=QHBoxLayout(); segments_layout.addLayout(times); times.addWidget(self.start); times.addWidget(QLabel("→")); times.addWidget(self.end)
        buttons=QHBoxLayout(); segments_layout.addLayout(buttons)
        self.button("应用修改",self.apply_segment,buttons); self.button("新增",self.add_segment,buttons); self.button("删除",self.delete_segment,buttons)
        quick=QHBoxLayout(); segments_layout.addLayout(quick)
        self.button("当前设起点 I",lambda:self.set_boundary("start"),quick); self.button("当前设终点 O",lambda:self.set_boundary("end"),quick)
        events=QHBoxLayout(); event_layout.addLayout(events)
        self.event_type=QComboBox(); self.event_type.addItems(["menu_open","confirm","cancel","occlusion","reacquired","pen_down","pen_up","speed_change"])
        events.addWidget(self.event_type); self.button("添加事件",self.add_event,events)
        self.event_list=QListWidget(); self.event_list.itemClicked.connect(lambda item:self.seek(item.data(Qt.UserRole))); event_layout.addWidget(self.event_list,1)
        self.button("删除所选事件",self.delete_event,event_layout)
        event_layout.addWidget(QLabel("自动手部事件建议（未审核，点击定位）"))
        self.suggested_event_list=QListWidget(); self.suggested_event_list.setMaximumHeight(150)
        self.suggested_event_list.itemClicked.connect(lambda item:self.seek(item.data(Qt.UserRole)))
        event_layout.addWidget(self.suggested_event_list)
        self.notes=QTextEdit(); self.notes.setPlaceholderText("复核备注、主题选项、异常原因…"); self.notes.setMaximumHeight(160); self.notes.textChanged.connect(self.notes_changed); event_layout.addWidget(self.notes)
        finish=QHBoxLayout(); rl.addLayout(finish)
        self.button("保存 Ctrl+S",self.save,finish); self.button("撤销 Ctrl+Z",self.undo,finish)
        self.review_btn=self.button("审核通过并下一条",self.review,rl); self.review_btn.setObjectName("primary")
        right.setMinimumWidth(365); split.addWidget(right)
        split.setSizes([240,820,370])
        bottom=QHBoxLayout(); outer.addLayout(bottom); self.progress=QProgressBar(); self.progress.setMaximumWidth(240); bottom.addWidget(self.progress)
        self.task=QLabel("就绪 · 空格播放，← →逐帧，I / O设边界，Ctrl+Z撤销。视频播放不含音频。"); bottom.addWidget(self.task,1)
        self.setCentralWidget(root)
        self.setStyleSheet('''QMainWindow,QWidget{background:#142033;color:#e4ebf5;font:13px "Microsoft YaHei UI";}
            QLabel#title{font-size:24px;font-weight:700;color:#f4ead5;} QLabel#warning{color:#e5c48a;padding:8px;background:#243043;border-radius:6px;}
            QLabel#badge{color:#50dcc4;font-weight:700;padding:7px;background:#20384a;} QPushButton{background:#2a3c54;border:1px solid #3a506b;border-radius:6px;padding:8px;}
            QPushButton:hover{background:#38526e;} QPushButton:disabled{color:#617188;} QPushButton#primary{background:#137f78;border-color:#1fa295;font-weight:700;}
            QLineEdit,QComboBox,QDoubleSpinBox,QTextEdit,QListWidget{background:#0f1a2a;border:1px solid #314259;border-radius:5px;padding:5px;}
            QListWidget::item{padding:7px;} QListWidget::item:selected{background:#24505d;color:#fff;} QSlider::groove:horizontal{height:5px;background:#33455b;} QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#45c5b5;border-radius:6px;}
            QProgressBar{border:0;background:#26384c;border-radius:4px;text-align:center;} QProgressBar::chunk{background:#168f83;} QScrollArea{border:0;}
        ''')

    def choose_project(self):
        folder=QFileDialog.getExistingDirectory(self,"选择工程文件夹（可新建；不会移动视频）",str(ROOT/"projects"))
        if folder: self.open_project(folder)

    def open_project(self,folder):
        if self.worker and self.worker.isRunning(): return self.message("请先取消或等待分析结束。")
        self.save(); self.pause(); self.project=Path(folder); self.project.mkdir(parents=True,exist_ok=True)
        p=self.project/"project.json"
        self.videos=json.loads(p.read_text(encoding="utf-8"))["videos"] if p.exists() else []
        self.data=None; self.arrays=None; self.file_list.blockSignals(True); self.file_list.clear(); self.file_list.blockSignals(False)
        self.settings.setValue("project",str(self.project))
        saved_filter=self.settings.value("person_filter/"+key(self.project),"all")
        self.restoring_project=True
        self.subtitle.setText("工程："+str(self.project)); self.save_project(); self.refresh_files()
        self.person_filter.setCurrentIndex(max(0,self.person_filter.findData(saved_filter)))
        self.restoring_project=False; self.filter_files(self.search.text())
        if self.videos:
            first=next((i for i in range(len(self.videos)) if not self.file_list.item(i).isHidden()),-1)
            self.file_list.setCurrentRow(first)

    def save_project(self):
        if self.project: save_json(self.project/"project.json",{"schema":"inklabel.project.v1","videos":self.videos},False)

    def import_files(self):
        if self.worker and self.worker.isRunning(): return
        if not self.project: self.choose_project()
        if not self.project: return
        paths,_=QFileDialog.getOpenFileNames(self,"导入视频","","视频 (*.mp4 *.mkv *.avi *.mov *.webm *.m4v)")
        self.add_files(paths)

    def import_folder(self):
        if self.worker and self.worker.isRunning(): return
        if not self.project: self.choose_project()
        if not self.project: return
        folder=QFileDialog.getExistingDirectory(self,"选择拍摄视频文件夹")
        if folder: self.add_files([str(p) for p in Path(folder).rglob("*") if p.suffix.lower() in (".mp4",".mkv",".avi",".mov",".webm",".m4v") and "rejected" not in p.parts and "_BAD" not in p.stem.upper()])

    def add_files(self,paths):
        if self.worker and self.worker.isRunning():return
        old={str(Path(v).resolve()).casefold() for v in self.videos}
        refreshed=[];failures=[]
        for p in sorted(set(paths)):
            identity=str(Path(p).resolve()).casefold()
            if not video_available(p):continue
            try:
                replaced=prepare_video_import(self.project,p)
            except Exception as exc:
                failures.append(Path(p).name+"："+str(exc));continue
            if identity not in old:
                self.videos.append(str(Path(p).resolve())); old.add(identity)
            if replaced:refreshed.append(identity)
        self.save_project(); self.refresh_files()
        if refreshed:
            self.search.clear();self.person_filter.setCurrentIndex(0);self.reshoot_filter.setCurrentIndex(0)
            row=next(i for i,v in enumerate(self.videos) if str(Path(v).resolve()).casefold()==refreshed[-1])
            self.file_list.blockSignals(True);self.file_list.setCurrentRow(row);self.file_list.blockSignals(False)
            self.select_video(row)
            self.statusBar().showMessage(f"已更新 {len(refreshed)} 条同名重拍视频；旧标注已归档，请重新自动分析。",8000)
        if self.videos and self.file_list.currentRow()<0: self.file_list.setCurrentRow(0)
        if failures:self.message("以下视频未导入：\n"+"\n".join(failures))

    def remove_video(self):
        if self.worker and self.worker.isRunning():return
        row=self.file_list.currentRow()
        if not self.project or not 0<=row<len(self.videos):return
        video=self.videos[row]
        if not self.save():return
        remaining=[v for v in self.videos if v!=video]
        try:
            save_json(self.project/"project.json",{"schema":"inklabel.project.v1","videos":remaining},False)
        except Exception as exc:
            self.message("移除失败："+str(exc));return
        self.pause();self.autosave.stop()
        if self.container:self.container.close()
        self.container=None;self.stream=None;self.frame_iterator=None
        self.data=None;self.arrays=None;self.history=[];self.current_sample=None;self.current_time=0
        self.videos=remaining
        self.preview.image=None;self.preview.points=None;self.preview.update()
        self.timeline.data=None;self.timeline.arrays=None;self.timeline.t=0;self.timeline.update()
        self.loading=True
        try:
            self.segment_list.clear();self.event_list.clear();self.suggested_event_list.clear()
            self.notes.clear();self.verified.setChecked(False);self.reshoot.setChecked(False);self.reshoot_reason.clear()
            for widget in self.fields.values():
                if isinstance(widget,QComboBox):widget.setCurrentIndex(-1)
                else:widget.clear()
        finally:self.loading=False
        self.video_title.setText("请选择或导入视频");self.phase_caption.setText("当前画面阶段：—")
        self.hand_caption.clear();self.hand_caption.hide();self.review_label.setText("未选择视频");self.split_label.clear()
        self.time_label.setText("0.000 / 0.000 s")
        self.slider.blockSignals(True);self.slider.setValue(0);self.slider.blockSignals(False)
        self.warning.setText("视频已移出工程列表。原视频、标注及骨架缓存保留，可通过导入视频恢复。")
        self.file_list.blockSignals(True);self.file_list.setCurrentRow(-1);self.file_list.blockSignals(False)
        self.refresh_files()
        visible=[i for i in range(len(self.videos)) if not self.file_list.item(i).isHidden()]
        if visible:self.file_list.setCurrentRow(next((i for i in visible if i>=row),visible[-1]))
        self.statusBar().showMessage("已从列表移除："+Path(video).name+"；文件和标注已保留",6000)

    def refresh_files(self):
        row=self.file_list.currentRow(); self.file_list.blockSignals(True); self.file_list.clear(); counts={}; persons={}; self.file_people=[]; self.file_reshoots=[]
        for v in self.videos:
            p=annotation_path(self.project,v); status="new"; metadata=parse_name(v); person=metadata["person_id"] or "unknown"
            if p.exists():
                try:
                    stored=json.loads(p.read_text(encoding="utf-8")); status=stored["status"]; metadata=stored["metadata"]; person=metadata.get("person_id") or "unknown"
                except Exception: status="error"
            if not video_available(v): status="missing"
            persons[person]=persons.get(person,0)+1; self.file_people.append(person)
            needs_reshoot=bool(metadata.get('needs_reshoot',False)); self.file_reshoots.append(needs_reshoot)
            counts[status]=counts.get(status,0)+1
            prefix={"new":"[新]","draft":"[待审]","reviewed":"[已审]","error":"[错误]","missing":"[文件缺失]"}.get(status,"[新]")
            if needs_reshoot:prefix='[需重拍]'+prefix
            summary=" ".join(str(metadata.get(k) or "") for k in ("action_id","speed_instruction","repeat")).strip()
            if metadata.get('category')=='CTRL' and metadata.get('control_validity')=='NEG':
                subtype=metadata.get('negative_type') or metadata.get('action_id') or '未分类'
                summary=f"控制负例 · {subtype} {metadata.get('repeat','')}"
            if not summary: summary=Path(v).stem
            self.file_list.addItem(prefix+" "+summary+"\n"+" · ".join([person,metadata.get("session_id", ""),metadata.get("clothing_id", "")]))
            self.file_list.item(self.file_list.count()-1).setToolTip(v+('\n重拍原因：'+metadata.get('reshoot_reason','') if needs_reshoot else ''))
        self.file_list.setCurrentRow(row); self.file_list.blockSignals(False)
        selection=self.person_filter.currentData(); self.person_filter.blockSignals(True); self.person_filter.clear(); self.person_filter.addItem("全部人员", "all")
        for person,count in sorted(persons.items()): self.person_filter.addItem(f"{person}（{count} 条）",person)
        self.person_filter.setCurrentIndex(max(0,self.person_filter.findData(selection))); self.person_filter.blockSignals(False)
        self.stats.setText(f"共 {len(self.videos)} 条 · 已审核 {counts.get('reviewed',0)} 条 · 需重拍 {sum(self.file_reshoots)} 条 · 缺失 {counts.get('missing',0)} 条\n人员编号来自实际文件标签")
        self.filter_files(self.search.text())

    def filter_files(self,text):
        person=self.person_filter.currentData()
        if self.project and person is not None and not getattr(self,"restoring_project",False): self.settings.setValue("person_filter/"+key(self.project),person)
        for i,v in enumerate(self.videos):
            hidden=text.casefold() not in Path(v).name.casefold() or (person not in (None,"all") and self.file_people[i]!=person)
            mode=self.reshoot_filter.currentData()
            hidden=hidden or (mode=='yes' and not self.file_reshoots[i]) or (mode=='no' and self.file_reshoots[i])
            self.file_list.item(i).setHidden(hidden)
        visible=self.visible_videos(); self.scope_label.setText(f"批量范围：{person if person and person!='all' else '全部人员'} · {len(visible)} 条\n下方重算复用已有骨架")
        self.remove_btn.setEnabled(bool(visible) and not (self.worker and self.worker.isRunning()))
        row=self.file_list.currentRow()
        if row>=0 and self.file_list.item(row).isHidden():
            next_row=next((i for i in range(len(self.videos)) if not self.file_list.item(i).isHidden()),-1)
            self.file_list.setCurrentRow(next_row)

    def visible_videos(self):
        return [v for i,v in enumerate(self.videos) if not self.file_list.item(i).isHidden()]

    def select_video(self,row):
        if row<0 or row>=len(self.videos): return
        self.save(); self.pause(); self.history=[]
        if not video_available(self.videos[row]):
            self.data=None; self.arrays=None; self.preview.image=None; self.preview.points=None; self.preview.update()
            self.timeline.data=None; self.timeline.arrays=None; self.timeline.update()
            self.video_title.setText("文件缺失："+Path(self.videos[row]).name)
            self.warning.setText("文件已删除或移动。该条将自动跳过，不会进入分析。原标注保留。")
            return
        try:
            data=load_or_blank(self.project,self.videos[row])
            if self.container: self.container.close()
            self.container=av.open(data["video"]); self.stream=self.container.streams.video[0]
            self.frame_iterator=None
            first=next(self.container.decode(self.stream)); self.origin=float(first.pts*first.time_base) if first.pts is not None else 0
            if not data["duration"]: data["duration"]=float(self.container.duration/av.time_base) if self.container.duration else float(self.stream.duration*self.stream.time_base) if self.stream.duration else 1
            self.data=data; self.arrays=None
            if data.get("keypoints_file"):
                with np.load(self.project/data["keypoints_file"]) as z: self.arrays={k:z[k] for k in z.files}
            self.refresh_document(); self.seek(0)
        except Exception as e:
            self.data=None; self.arrays=None; self.message("无法打开视频："+str(e))

    def refresh_document(self):
        if not self.data: return
        self.loading=True; d=self.data
        self.video_title.setText(Path(d["video"]).name)
        for k,w in self.fields.items():
            value=d["metadata"].get(k,"")
            if isinstance(w,QComboBox): w.setCurrentText(value)
            else: w.setText(value)
        self.verified.setChecked(d["metadata"].get("filename_label_verified",False)); self.notes.setPlainText(d.get("notes",""))
        self.reshoot.setChecked(bool(d['metadata'].get('needs_reshoot',False)))
        self.reshoot_reason.setText(d['metadata'].get('reshoot_reason',''))
        self.split_label.setText("数据划分："+d["metadata"]["split"]+"（按人员 / 场次固定）")
        self.review_label.setText("已审核时间标签（骨架仍是预标注）" if d["status"]=="reviewed" else "待人工审核")
        self.warning.setText("\n".join(d.get("warnings",[])) or "文件名标签尚未独立验证。点击自动分析提取关键点并建议时间段。")
        row=self.segment_list.currentRow(); self.segment_list.clear()
        for s in d["segments"]: self.segment_list.addItem(f"{s['start']:.3f} – {s['end']:.3f}   {PHASES.get(s['phase'],s['phase'])}")
        self.segment_list.setCurrentRow(min(max(row,0),len(d["segments"])-1))
        self.event_list.clear()
        for e in d["events"]:
            self.event_list.addItem(f"{e['time']:.3f}  {e['type']}"); self.event_list.item(self.event_list.count()-1).setData(Qt.UserRole,e["time"])
        self.suggested_event_list.clear()
        for e in d.get("suggested_events",[]):
            self.suggested_event_list.addItem(f"{e['time']:.3f}  {e['type']}（自动）")
            self.suggested_event_list.item(self.suggested_event_list.count()-1).setData(Qt.UserRole,e["time"])
        self.timeline.data=d; self.timeline.arrays=self.arrays; self.timeline.update(); self.loading=False
        self.update_phase_caption()
        self.segment_selected(self.segment_list.currentRow(),False)

    def update_phase_caption(self):
        if not self.data: self.phase_caption.setText("当前画面阶段：—"); return
        current=next((s for s in self.data["segments"] if s["start"]<=self.current_time<s["end"]),None)
        name=PHASES.get(current["phase"],current["phase"]) if current else "未标注"
        self.phase_caption.setText("当前画面阶段："+name+"  ·  "+("已审核" if self.data["status"]=="reviewed" else "自动 / 人工草稿，待审核"))

    def reshoot_changed(self,*args):
        if self.loading or not self.data:return
        self.data['metadata']['needs_reshoot']=self.reshoot.isChecked()
        self.data['metadata']['reshoot_reason']=self.reshoot_reason.text().strip()
        self.data['metadata']['reshoot_updated_at']=now()
        self.save()

    def meta_changed(self,*args):
        if self.loading or not self.data: return
        self.checkpoint()
        for k,w in self.fields.items(): self.data["metadata"][k]=w.currentText() if isinstance(w,QComboBox) else w.text().strip()
        m=self.data["metadata"]; m["split"]=split_for(m["person_id"],m["session_id"]); m["filename_label_verified"]=self.verified.isChecked()
        if self.arrays is not None:
            a=self.data["analysis"]
            self.arrays["speed"],_,_=velocity(effective_points(self.data,self.arrays),self.arrays["times"],a["width"],a["height"],m,a["kind"]=="hand")
        self.changed()

    def notes_changed(self):
        if self.loading or not self.data: return
        self.data["notes"]=self.notes.toPlainText(); self.data["status"]="draft"; self.review_label.setText("待人工审核"); self.autosave.start()

    def checkpoint(self):
        self.history.append(copy.deepcopy(self.data)); self.history=self.history[-40:]

    def changed(self):
        self.data["status"]="draft"; self.data["updated_at"]=now(); self.refresh_document(); self.refresh_overlay(); self.autosave.start()

    def undo(self):
        if self.history:
            self.data=self.history.pop(); self.data["status"]="draft"; self.recompute_speed(); self.refresh_document(); self.refresh_overlay(); self.autosave.start()

    def save(self):
        self.autosave.stop()
        if self.data and self.project:
            if video_available(self.data['video']) and self.data.get('fingerprint')!=fingerprint(self.data['video']):
                self.statusBar().showMessage("视频已被替换，旧窗口内容未覆盖新记录；请重新导入该视频。",6000)
                return False
            errors=validate(self.data)
            if errors: self.statusBar().showMessage("未保存："+"；".join(errors)); return False
            try:
                self.data["updated_at"]=now(); save_json(annotation_path(self.project,self.data["video"]),self.data)
                self.statusBar().showMessage("已保存，旧版保留在 annotations/history",2500); self.refresh_files()
                return True
            except Exception as e: self.message("保存失败："+str(e)); return False
        return True

    def segment_selected(self,row,jump=True):
        if not self.data or not 0<=row<len(self.data["segments"]): return
        s=self.data["segments"][row]; self.phase.setCurrentIndex(max(0,self.phase.findData(s["phase"])))
        self.start.setValue(s["start"]); self.end.setValue(s["end"])
        if jump and not self.loading: self.seek(s["start"])

    def apply_segment(self):
        i=self.segment_list.currentRow()
        if not self.data or i<0: return
        self.checkpoint(); old=copy.deepcopy(self.data["segments"])
        self.data["segments"][i]=dict(phase=self.phase.currentData(),start=self.start.value(),end=self.end.value(),source="human",verified=True)
        self.data["segments"].sort(key=lambda s:s["start"])
        errors=validate(self.data)
        if errors:
            self.data["segments"]=old; self.history.pop(); return self.message("；".join(errors))
        self.changed()

    def add_segment(self):
        if not self.data: return
        self.checkpoint()
        # Split an existing interval at cursor; otherwise fill the gap up to next segment.
        t=self.current_time
        for i,s in enumerate(self.data["segments"]):
            if s["start"]<t<s["end"]:
                new=copy.deepcopy(s); new["start"]=t; new["phase"]=self.phase.currentData(); new["source"]="human"
                s["end"]=t; self.data["segments"].insert(i+1,new); self.changed(); return
        end=min([s["start"] for s in self.data["segments"] if s["start"]>t]+[self.data["duration"]])
        if any(s["start"]<=t<s["end"] for s in self.data["segments"]) or end<=t:
            self.history.pop(); return self.message("请把播放位置移到段内再拆分，或移到未标注的空白位置。")
        self.data["segments"].append(dict(start=t,end=end,phase=self.phase.currentData(),source="human",verified=True))
        self.data["segments"].sort(key=lambda s:s["start"]); self.changed()

    def delete_segment(self):
        i=self.segment_list.currentRow()
        if self.data and i>=0:
            self.checkpoint(); self.data["segments"].pop(i); self.changed()

    def set_boundary(self,side):
        i=self.segment_list.currentRow()
        if self.data and i>=0: self.drag_boundary(i,side,self.current_time)

    def drag_boundary(self,i,side,t):
        self.checkpoint(); seg=self.data["segments"]; s=seg[i]; old=s[side]
        if side=="start":
            t=max(0,min(t,s["end"]-.001)); lower=seg[i-1]["start"]+.001 if i else 0; t=max(t,lower)
            if i and abs(seg[i-1]["end"]-old)<.002: seg[i-1]["end"]=t
            elif i: t=max(t,seg[i-1]["end"])
        else:
            t=min(self.data["duration"],max(t,s["start"]+.001)); upper=seg[i+1]["end"]-.001 if i+1<len(seg) else self.data["duration"]; t=min(t,upper)
            if i+1<len(seg) and abs(seg[i+1]["start"]-old)<.002: seg[i+1]["start"]=t
            elif i+1<len(seg): t=min(t,seg[i+1]["start"])
        s[side]=round(t,6); s["source"]="human"; self.changed()

    def add_event(self):
        if self.data:
            self.checkpoint(); self.data["events"].append(dict(time=self.current_time,type=self.event_type.currentText(),source="human"))
            self.data["events"].sort(key=lambda e:e["time"]); self.changed()

    def delete_event(self):
        i=self.event_list.currentRow()
        if self.data and i>=0:
            self.checkpoint(); self.data["events"].pop(i); self.changed()

    def point_mode(self,on):
        self.pause(); self.preview.edit=on
        if on and self.arrays is not None and self.current_sample is not None:
            self.seek(float(self.arrays["times"][self.current_sample]))
        self.preview.update()
        self.statusBar().showMessage("拖动圆点修正当前采样帧；右键标记不可见；Ctrl+Z撤销。不自动传播到其他帧。" if on else "骨架查看模式")

    def move_point(self,j,x,y):
        if self.current_sample is None: return
        self.checkpoint(); row=self.preview.points[j].tolist(); row[:2]=[x,y]; row[3]=1
        self.data["corrections"].setdefault(str(self.current_sample),{})[str(j)]=row; self.recompute_speed(); self.changed()

    def hide_point(self,j):
        if self.current_sample is None: return
        self.checkpoint(); row=self.preview.points[j].tolist(); row[3]=0
        self.data["corrections"].setdefault(str(self.current_sample),{})[str(j)]=row; self.recompute_speed(); self.changed()

    def recompute_speed(self):
        if self.arrays is not None:
            a=self.data["analysis"]
            self.arrays["speed"],_,_=velocity(effective_points(self.data,self.arrays),self.arrays["times"],a["width"],a["height"],self.data["metadata"],a["kind"]=="hand")

    def refresh_overlay(self,*args):
        self.preview.points=None; self.current_sample=None
        is_hand=bool(self.data and self.arrays is not None and self.data.get("analysis",{}).get("kind")=="hand")
        self.hand_caption.setVisible(is_hand)
        if is_hand:
            from hand_phases import hand_features
            ts=self.arrays["times"]; index=int(np.argmin(abs(ts-self.current_time)))
            pts=effective_points(self.data,self.arrays)[index:index+1]
            a=self.data["analysis"]; f=hand_features(pts,ts[index:index+1],a["width"],a["height"])
            ratio=f["pinch_ratio"][0]
            detail=f"捏合比 {ratio:.2f}（结合手掌转向与前后帧判定）" if np.isfinite(ratio) else "未获得有效手部关键点；不代表画面中没有手"
            count=self.data.get("segmentation",{}).get("stroke_count",0)
            inferred=next((b for b in self.data.get("segmentation",{}).get("inferred_state_intervals",[]) if b['start']<=self.current_time<b['end']),None)
            if inferred:
                if inferred.get('source')=='hand_shape_inference':
                    detail=("局部遮挡 / 侧掌：由前后捏合推断连续落笔，待复核" if inferred.get('evidence')=='ambiguous_release_between_confirmed_pinches'
                             else "手背遮住拇指：由手指闭合及后续捏合推断落笔，接触时刻待复核")
                    if inferred.get('evidence')=='sustained_closed_shape_followed_by_confirmed_pinch':
                        detail="持续闭合姿态结合后续捏合推断落笔；接触时刻待复核，坐标未插值"
                else:detail=f"短暂丢点，{PHASES[inferred['phase']]}状态由前后帧推断；坐标和速度保持缺失"
            self.hand_caption.setText(f"手部21点 · {detail} · 落笔片段 {count} 段")
        if self.arrays is not None and self.overlay.isChecked():
            times=self.arrays["times"]; i=int(np.argmin(abs(times-self.current_time)))
            if abs(times[i]-self.current_time)<.05:
                self.current_sample=i; pts=self.arrays["points"][i].copy()
                for j,value in self.data.get("corrections",{}).get(str(i),{}).items(): pts[int(j)]=value
                self.preview.points=pts; self.preview.edges=HAND_EDGES if self.data["analysis"]["kind"]=="hand" else POSE_EDGES
                # Editing is only allowed on the matching inference frame.
                self.preview.edit=self.edit_points.isChecked() and abs(times[i]-self.current_time)<.001
        self.preview.update()

    def seek_slider(self,value):
        if not self.data: return
        self.pause()
        self.seek(value/10000*self.data["duration"])

    def seek(self,t):
        if not self.data or not self.container: return
        try:
            t=float(np.clip(t,0,max(0,self.data["duration"]-.001)))
            if self.arrays is not None:
                times=self.arrays["frame_times"]; t=float(times[min(bisect.bisect_left(times,t-1e-6),len(times)-1)])
            # Seek to a preceding keyframe and decode forward using actual presentation timestamps.
            self.container.seek(int((t+self.origin)/float(self.stream.time_base)),stream=self.stream,backward=True,any_frame=False)
            self.frame_iterator=self.container.decode(self.stream)
            chosen=None
            for frame in self.frame_iterator:
                chosen=frame
                ft=float(frame.pts*frame.time_base)-self.origin if frame.pts is not None else t
                if ft>=t-1e-6: break
            if chosen is not None:
                self.display_frame(chosen,t)
                if self.playing: self.anchor=self.current_time; self.started=time.monotonic()
        except Exception as e: self.statusBar().showMessage("定位失败："+str(e))

    def display_frame(self,frame,fallback):
        rgb=np.ascontiguousarray(frame.to_ndarray(format="rgb24")); h,w,_=rgb.shape
        self.preview.image=QImage(rgb.data,w,h,rgb.strides[0],QImage.Format_RGB888).copy()
        self.current_time=max(0,float(frame.pts*frame.time_base)-self.origin) if frame.pts is not None else fallback
        self.time_label.setText(f"{self.current_time:.3f} / {self.data['duration']:.3f} s")
        self.update_phase_caption()
        # Decoding must not move the handle underneath an active mouse drag.
        if not self.slider.isSliderDown():
            blocked=self.slider.blockSignals(True)
            self.slider.setValue(round(self.current_time/max(self.data["duration"],.001)*10000))
            self.slider.blockSignals(blocked)
        self.timeline.t=self.current_time; self.timeline.update(); self.refresh_overlay()

    def step(self,direction):
        self.pause()
        if not self.data: return
        if self.arrays is not None:
            ts=self.arrays["times"] if self.edit_points.isChecked() else self.arrays["frame_times"]
            i=int(np.argmin(abs(ts-self.current_time))); self.seek(float(ts[np.clip(i+direction,0,len(ts)-1)]))
        else: self.seek(self.current_time+direction/float(self.stream.average_rate or 30))

    def toggle_play(self):
        if not self.data: return
        if self.playing: self.pause(); return
        self.edit_points.setChecked(False); self.playing=True; self.anchor=self.current_time; self.started=time.monotonic()
        self.play_btn.setText("暂停  Space"); self.timer.start()

    def pause(self):
        self.playing=False; self.timer.stop(); self.play_btn.setText("播放  Space")

    def tick(self):
        if not self.playing or not self.data: return
        target=self.anchor+(time.monotonic()-self.started)*float(self.rate.currentText().replace("×",""))
        if target>=self.data["duration"]: self.pause(); return
        try:
            if self.frame_iterator is None: self.seek(target); return
            chosen=None
            while self.current_time<target:
                chosen=next(self.frame_iterator)
                ft=float(chosen.pts*chosen.time_base)-self.origin if chosen.pts is not None else target
                if ft>=target: break
            if chosen is not None: self.display_frame(chosen,target)
        except StopIteration: self.pause()
        except Exception as e: self.pause(); self.message(str(e))

    def start_analysis(self,batch,mode="analyze"):
        if not self.project or not self.videos: return self.message("请先建立工程并导入视频。")
        if self.worker and self.worker.isRunning(): return
        self.save(); self.pause()
        if batch:
            videos,skipped=queue_videos(self.visible_videos(),self.project,mode)
        else:
            if not self.data or not video_available(self.data["video"]): return self.message("当前视频不存在，未启动分析。")
            if self.data["video"] not in self.visible_videos(): return self.message("请先在当前人员筛选中选择一个视频。")
            if mode=="phases" and not self.data.get("keypoints_file"): return self.message("尚无骨架缓存，请先自动分析一次。")
            protected=self.data["status"]=="reviewed" or any(s.get("source")=="human" or s.get("verified") for s in self.data["segments"])
            if protected or (mode=="analyze" and (self.data.get("segments") or self.data.get("corrections"))):
                prompt="将替换人工时间段，骨架修正保留。" if mode=="phases" else "将重新提取骨架并替换时间段及关键点修正。"
                if QMessageBox.question(self,"重新分析",prompt+"旧版保留在 history。继续？")!=QMessageBox.Yes: return
            videos=[self.data["video"]]
            skipped=[]
        save_json(self.project/"last_queue.json",{"created_at":now(),"mode":mode,"person_filter":self.person_filter.currentData(),"videos":videos,"skipped":skipped},False)
        if not videos: return self.message("当前筛选范围内没有可处理的视频。已审核、人工时间段、缺失视频会被跳过；已有骨架请使用“重算阶段”。")
        self.queue_total=len(videos); self.queue_completed=0; self.queue_failed=0; self.queue_skipped=0
        self.worker=Worker(videos,self.project,mode); self.worker.progress.connect(self.on_progress); self.worker.result.connect(self.on_result)
        self.worker.skipped.connect(self.on_skipped)
        self.worker.error.connect(self.on_error); self.worker.finished.connect(self.on_finished)
        self.auto_btn.setEnabled(False); self.batch_btn.setEnabled(False); self.phase_btn.setEnabled(False); self.phase_batch_btn.setEnabled(False); self.cancel_btn.setEnabled(True)
        self.centralWidget().findChild(QSplitter).setEnabled(False)
        self.worker.start()

    def cancel_analysis(self):
        if self.worker: self.worker.requestInterruption(); self.task.setText("正在取消，等待当前帧处理结束…")

    def on_progress(self,video,p):
        self.progress.setValue(p)
        position=self.worker.videos.index(video)+1
        self.task.setText(f"{'重算阶段' if self.worker.mode=='phases' else '分析'} {position}/{self.queue_total}："+Path(video).name)

    def on_skipped(self,video,reason):
        self.queue_skipped+=1; self.task.setText("已跳过："+Path(video).name+" · "+reason)

    def on_result(self,video,d,a):
        try:
            previous=load_or_blank(self.project,video)
            if self.worker.mode=="phases":
                # Human quality flags may change while a worker is running.
                for field in ('needs_reshoot','reshoot_reason','reshoot_updated_at'):
                    if field in previous['metadata']:d['metadata'][field]=previous['metadata'][field]
                # No NPZ rewrite or detector call: keep existing skeleton and edits exactly.
                save_json(annotation_path(self.project,video),d)
            else:
                d["metadata"]=previous["metadata"]; d["notes"]=previous.get("notes",""); d["events"]=previous.get("events",[])
                d,a["speed"]=resegment(d,a); store_analysis(self.project,d,a)
            self.queue_completed+=1
            if self.data and self.data["video"]==video:
                self.data=d; self.arrays=a; self.history=[]; self.refresh_document(); self.seek(0)
            self.refresh_files()
        except Exception as e: self.on_error(video,str(e))

    def on_error(self,video,error):
        self.queue_failed+=1
        log=self.project/"analysis_errors.log"
        with log.open("a",encoding="utf-8") as f: f.write(now()+" "+video+"\n"+error+"\n")
        self.warning.setText("分析失败："+Path(video).name+"\n"+error.splitlines()[0]+"\n详细原因见工程内 analysis_errors.log")

    def on_finished(self):
        self.remove_btn.setEnabled(bool(self.visible_videos()))
        self.auto_btn.setEnabled(True); self.batch_btn.setEnabled(True); self.phase_btn.setEnabled(True); self.phase_batch_btn.setEnabled(True); self.cancel_btn.setEnabled(False)
        self.centralWidget().findChild(QSplitter).setEnabled(True)
        self.task.setText(f"完成 {self.queue_completed}/{self.queue_total} · 失败 {self.queue_failed} · 跳过 {self.queue_skipped}。阶段仍需复核。")
        self.progress.setValue(100 if self.queue_completed==self.queue_total else 0)

    def review(self):
        if not self.data: return
        errors=validate(self.data,True)
        if errors: return self.message("暂时不能审核：\n"+"\n".join(errors))
        self.data["status"]="reviewed"; self.data["reviewed_at"]=now()
        for s in self.data["segments"]: s["verified"]=True
        if not self.save(): self.data["status"]="draft"; return
        self.refresh_document()
        row=self.file_list.currentRow()
        next_row=next((i for i in range(row+1,len(self.videos)) if not self.file_list.item(i).isHidden()),None)
        if next_row is not None: self.file_list.setCurrentRow(next_row)

    def export(self):
        if not self.project: return
        self.save()
        folder=QFileDialog.getExistingDirectory(self,"选择导出父文件夹；将新建带时间戳的导出目录",str(self.project))
        if not folder: return
        try:
            dest=Path(folder)/("export_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            n,skip=export_reviewed(self.project,self.visible_videos(),dest)
            self.message(f"已导出 {n} 条，跳过 {skip} 条未审核或划分无效的数据。\n{dest}\n包含 JSON、NPZ 和 train / validation / test.jsonl。")
        except Exception as e: self.message("导出失败："+str(e))

    def message(self,text): QMessageBox.information(self,"墨迹标注台",text)

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.cancel_analysis(); event.ignore(); self.task.setText("已请求取消，请等分析停止后再次关闭。"); return
        self.save()
        if self.container: self.container.close()
        event.accept()

def main():
    app=QApplication(sys.argv); window=Window(); window.show(); sys.exit(app.exec())

if __name__=="__main__": main()
