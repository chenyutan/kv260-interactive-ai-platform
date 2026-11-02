import json
import shutil
from PySide6.QtWidgets import QApplication
from core import load_or_blank,annotation_path,queue_videos
from test_inklabel import video

def test_remove_preserves_files_annotations_and_reimport(video,tmp_path):
    from app import Window
    application=QApplication.instance() or QApplication([])
    win=Window();project=tmp_path/'project';win.open_project(project)
    other=tmp_path/'P003_S01_C01_BODY_A01_MID_R01.mp4';shutil.copy2(video,other)
    win.add_files([str(video),str(other)])
    win.file_list.setCurrentRow(win.videos.index(str(video)))
    win.notes.setPlainText('keep my note')
    original=video.read_bytes()
    win.remove_btn.click()
    assert str(video) not in win.videos and win.data['video']==str(other)
    assert video.read_bytes()==original and annotation_path(project,video).exists()
    assert load_or_blank(project,video)['notes']=='keep my note'
    assert json.loads((project/'project.json').read_text('utf8'))['videos']==[str(other)]
    queued,_=queue_videos(win.visible_videos(),project,'analyze')
    assert str(video) not in queued
    win.remove_btn.click()
    assert not win.videos and win.data is None and win.container is None
    assert win.preview.image is None and win.timeline.data is None and win.segment_list.count()==0
    win.open_project(project)
    assert not win.videos
    win.add_files([str(video)])
    assert win.data['notes']=='keep my note'
    win.close();application.processEvents();win.settings.remove('project')

def test_remove_filtered_missing_entry_and_running_guard(video,tmp_path):
    from app import Window
    application=QApplication.instance() or QApplication([])
    win=Window();project=tmp_path/'project';win.open_project(project)
    other=tmp_path/'P003_S01_C01_BODY_A01_MID_R01.mp4';shutil.copy2(video,other)
    win.add_files([str(video),str(other)]);win.search.setText('P003')
    class Running:
        def isRunning(self):return True
    win.worker=Running();win.remove_video()
    assert len(win.videos)==2
    win.worker=None;win.container.close();win.container=None;other.unlink()
    win.remove_video()
    assert win.videos==[str(video)] and win.data is None
    assert win.file_list.currentRow()==-1 and not win.visible_videos()
    win.search.clear();win.file_list.setCurrentRow(0)
    assert win.data['video']==str(video)
    win.close();application.processEvents();win.settings.remove('project')
