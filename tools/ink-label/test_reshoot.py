import shutil
from core import load_or_blank
from test_inklabel import video

def test_mark_reason_filter_clear_and_reload(video,tmp_path):
    from PySide6.QtWidgets import QApplication
    from app import Window
    application=QApplication.instance() or QApplication([])
    other=tmp_path/'P003_S01_C01_BODY_A01_MID_R01.mp4';shutil.copy2(video,other)
    project=tmp_path/'reshoot_project';win=Window()
    win.open_project(project);win.add_files([str(video),str(other)])
    win.file_list.setCurrentRow(win.videos.index(str(video)))
    original_status=win.data['status'];original_segments=list(win.data['segments'])
    win.reshoot_reason.setText('手部出框');win.reshoot.setChecked(True)
    saved=load_or_blank(project,video)
    assert saved['metadata']['needs_reshoot'] and saved['metadata']['reshoot_reason']=='手部出框'
    assert saved['status']==original_status and saved['segments']==original_segments
    win.reshoot_filter.setCurrentIndex(win.reshoot_filter.findData('yes'))
    assert win.visible_videos()==[str(video)]
    win.search.setText('P003');assert not win.visible_videos()
    win.search.clear();assert win.visible_videos()==[str(video)]
    win.reshoot_filter.setCurrentIndex(win.reshoot_filter.findData('no'))
    assert win.visible_videos()==[str(other)]
    win.reshoot_filter.setCurrentIndex(0);win.file_list.setCurrentRow(win.videos.index(str(video)))
    assert win.reshoot.isChecked() and win.reshoot_reason.text()=='手部出框'
    win.reshoot.setChecked(False)
    assert not load_or_blank(project,video)['metadata']['needs_reshoot']
    win.reshoot_filter.setCurrentIndex(win.reshoot_filter.findData('yes'));assert not win.visible_videos()
    win.close();application.processEvents();win.settings.remove('project')
