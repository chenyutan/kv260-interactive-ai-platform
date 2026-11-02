"""Actual single-shot API/YUYV/filter/GTK integration; synthetic input, no camera."""
import importlib.util,json,sys,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,GLib
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'environment'))
spec=importlib.util.spec_from_file_location('tested_photo_page',ROOT/'environment/main.py')
page=importlib.util.module_from_spec(spec);sys.modules[spec.name]=page;spec.loader.exec_module(page)
sys.path.insert(0,'/home/ubuntu/bodyink-live')
from movenet import YuyvPreprocessor
sys.path.insert(0,str(ROOT))
from environment_capture import EnvironmentCapture
from suite_control import SuiteControl
spec=importlib.util.spec_from_file_location('photo_web',ROOT/'runtime/web_ui.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
raw=np.empty((1080,1920,2),np.uint8)
raw[:,:,0]=np.linspace(45,230,1920).astype(np.uint8);raw[:,::2,1]=95;raw[:,1::2,1]=160;raw=raw.tobytes()
stats=dict(previews=0,shots=0,snapshot_calls=0,filter_calls=0)
def snapshot(**kwargs):
    stats['snapshot_calls']+=1
    return dict(width=1920,height=1080,stride=3840,timestamp_seconds=time.monotonic(),sequence=stats['snapshot_calls']),raw
stop=threading.Event();suite=SuiteControl();suite.configure(dict(active=True,mode='environment',menu=False))
capture=EnvironmentCapture(snapshot,YuyvPreprocessor().resize,suite.snapshot,lambda:(None,None),stop)
def preview(after):stats['previews']+=1;return capture.preview(after)
def photo():stats['shots']+=1;stats['post_started']=time.monotonic();return capture.photograph()
original=page.InkFilter.render
def render(self,*args,**kwargs):
    stats['filter_calls']+=1;started=time.monotonic()
    art=original(self,*args,**kwargs);stats['filter_ms']=(time.monotonic()-started)*1000;return art
page.InkFilter.render=render
server=web.ViewerServer('127.0.0.1',0,lambda:{},lambda:b'',lambda:b'',lambda _:None,get_environment_frame=preview,get_environment_photo=photo).start()
temporary=tempfile.TemporaryDirectory()
args=SimpleNamespace(embed_socket=None,offline_preview=False,offline_image=None,base='http://127.0.0.1:'+str(server.address[1]),fps=4.,works=Path(temporary.name))
window=Gtk.Window();panel=page.EnvironmentPanel(args);window.add(panel);window.set_default_size(1024,600);window.show_all();panel.refresh_ui()
started=time.monotonic();phase='warmup';result_at=None;errors=[];numbers=set();result_previews=None
def finish():
    stop.set();panel.operation('shutdown');window.destroy();Gtk.main_quit()
def tick():
    global phase,result_at,result_previews
    try:
        now=time.monotonic()
        if now-started>18:raise TimeoutError('Single-photo pipeline did not finish')
        if panel.session.phase=='countdown':numbers.add(panel.session.remaining(now))
        if phase=='warmup' and now-started>1.:
            assert stats['previews']>=1 and stats['filter_calls']==stats['shots']==0
            stats['countdown_started']=now;assert panel.begin_capture();assert not panel.begin_capture();phase='photo'
        elif phase=='photo' and panel.session.phase=='result':
            result_at=now;result_previews=stats['previews'];phase='static'
            assert stats['shots']==stats['filter_calls']==1
            assert stats['post_started']-stats['countdown_started']>=3.
            assert {1,2,3}<=numbers
            assert panel.current.shape==(1080,1920,3)
            assert panel.current_metadata['capture_monotonic']>=stats['post_started']
            stats['after_shutter_to_result_ms']=(now-stats['post_started'])*1000
        elif phase=='static' and now-result_at>1.:
            assert stats['previews']==result_previews and stats['shots']==stats['filter_calls']==1
            photo_id=panel.artifact_id;panel.operation('pause');panel.operation('resume')
            assert panel.session.phase=='result' and panel.artifact_id==photo_id
            path=page.save_art(panel.current,args.works,panel.current_metadata)
            assert np.array_equal(np.array(Image.open(path)),panel.current)
            result=dict(passed=True,camera_used=False,source='synthetic_YUYV_1920x1080',statistics=stats,
                countdown_numbers=sorted(numbers),full_size_saved=[1920,1080],static_result_no_work=True)
            out=ROOT/'diagnostics/photo-pipeline.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
            finish();return False
        return True
    except Exception as exc:
        errors.append(repr(exc));finish();return False
GLib.timeout_add(40,tick);Gtk.main();server.stop();temporary.cleanup()
if errors:raise RuntimeError(errors)
