import json
from core import blank,save_json,annotation_path,prepare_video_import,load_or_blank
from test_inklabel import video

def test_retake_archives_old_record_and_same_file_is_unchanged(video,tmp_path):
    old=blank(video);old['notes']='old recording';old['keypoints_file']='keypoints/old.npz'
    path=annotation_path(tmp_path,video);save_json(path,old)
    assert not prepare_video_import(tmp_path,video)
    assert load_or_blank(tmp_path,video)==old
    with video.open('ab') as stream:stream.write(b'retake')
    assert prepare_video_import(tmp_path,video)
    fresh=load_or_blank(tmp_path,video)
    assert fresh['notes']=='' and fresh['segments']==[] and 'keypoints_file' not in fresh
    history=list((path.parent/'history').glob('*.json'))
    assert any(json.loads(p.read_text('utf8'))==old for p in history)

def test_gui_reimport_existing_path_reloads_new_record(video,tmp_path):
    from PySide6.QtWidgets import QApplication
    from app import Window
    application=QApplication.instance() or QApplication([])
    win=Window();win.open_project(tmp_path/'project');win.add_files([str(video)])
    win.notes.setPlainText('old recording');win.save()
    win.container.close();win.container=None
    with video.open('ab') as stream:stream.write(b'retake')
    assert win.save() is False
    win.add_files([str(video)])
    assert win.videos==[str(video)]
    assert win.data['notes']=='' and win.data['segments']==[] and win.arrays is None
    assert win.data['fingerprint']==load_or_blank(win.project,video)['fingerprint']
    win.close();application.processEvents();win.settings.remove('project')
