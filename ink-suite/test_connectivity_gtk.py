"""Connection dialogs/keyboard in isolated Xvfb, synthetic HTTP and no devices."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,Gdk,GLib
from main import Suite
from connectivity.component_policy import ComponentPolicy

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'test-evidence/connectivity';OUT.mkdir(parents=True,exist_ok=True)
state=dict(pid=12345678,pose_count=0,sequence=0,phase='tracking',camera_link=dict(present=True),
           suite=dict(active=True,mode='ink',menu=True,control_active=False,open_serial=0,hand=None))
requests=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        assert self.path=='/api/state'
        state['pose_count']+=1;state['sequence']+=1;state['timestamp_seconds']=time.monotonic()
        payload=json.dumps(state).encode();self.send_response(200);self.end_headers();self.wfile.write(payload)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(data);state['suite'].update({k:v for k,v in data.items() if k!='command'})
        self.send_response(200);self.end_headers();self.wfile.write(b'{"accepted":true}')
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
args=SimpleNamespace(base='http://127.0.0.1:'+str(server.server_port),fullscreen=False,offline_preview=True)
app=Suite(args);app.resize(1024,600)
app.device_policy=ComponentPolicy(initial_delay=0,loss_debounce=0)
snapshot=dict(camera=dict(connected=True,name='摄像头',detail='已连接'),audio=dict(connected=True,name='音响',detail='保活中'),wifi=dict(connected=False,name='Wi‑Fi 网络',detail='尚未连接网络'))
network=dict(ssid='测试:热点\\国风',signal=92,security='WPA2',interface='wlx-test',connected=False)
operations=[]
class Devices:
    def request(self,op,**data):
        token=len(operations)+1;operations.append((op,data))
        result=dict(ok=True,message='搜索完成',networks=[network]) if op=='scan' else dict(ok=True,message='已连接测试网络')
        GLib.idle_add(app.device_result,token,op,result);return token
    def close(self):pass
app.devices=Devices()
errors=[];records=[];stage=[0];started=time.monotonic()
def shot(name):
    for w in (app,app.device_view):
        if w.get_visible():
            x,y=w.get_size();assert x<=1024 and y<=600,(name,w.get_title(),x,y)
    w=app.device_view if app.device_view.get_visible() else app
    x,y=w.get_size();Gdk.pixbuf_get_from_window(w.get_window(),0,0,x,y).savev(str(OUT/(name+'.png')),'png',[],[])
def tick():
    try:
        if time.monotonic()-started>20:raise RuntimeError('Connection UI test timed out')
        n=stage[0]
        if n==0:
            if not app.interaction_ready():return True
            app.devices_updated(snapshot);app.check_devices();assert app.device_dialog is not None
            records.append('WiFi absent popup');stage[0]+=1
        elif n==1:
            app.device_dialog.response(Gtk.ResponseType.CANCEL);app.check_devices()
            assert app.device_dialog is None;assert not app.device_policy.ready('wifi')
            app.open_connections(wifi=True);stage[0]+=1
        elif n==2:
            v=app.device_view
            if v.pending is not None:return True
            assert len(v.rows)==1;v.list.select_row(v.rows[0]);assert v.ssid.get_text()==network['ssid']
            v.target(v.password);v.type_char('a');v.toggle_shift();v.type_char('b');v.type_char('1');assert v.password.get_text()=='aB1'
            v.backspace();assert v.password.get_text()=='aB';v.toggle_keyboard();stage[0]+=1
        elif n==3:
            shot('wifi-keyboard');v=app.device_view;v.password.set_text('testPass42');v.connect_wifi();stage[0]+=1
        elif n==4:
            v=app.device_view
            if v.pending is not None:return True
            assert operations[-1]==('connect',dict(network=network,password='testPass42'))
            assert v.password.get_text()=='';assert app.menu_open
            snapshot['wifi']['connected']=True;app.devices_updated(snapshot);app.check_devices()
            snapshot['wifi']['connected']=False;app.devices_updated(snapshot);v.hide_center();app.check_devices()
            assert app.device_dialog is not None;app.device_dialog.response(Gtk.ResponseType.CANCEL)
            # Camera unavailable permits native reconnect/dismiss without enabling gestures.
            snapshot['camera']['connected']=False;app.devices_updated(snapshot);v.hide_center();app.check_devices()
            assert app.device_dialog is not None;app.device_dialog.response(Gtk.ResponseType.OK);stage[0]+=1
        elif n==5:
            v=app.device_view
            if v.pending is not None:return True
            assert operations[-1][0]=='camera_retry';assert v.get_visible();assert not app.device_policy.ready('camera')
            shot('device-status');records.append('dismiss, reconnect, episode recovery, onscreen keyboard, 1024x600 fit')
            app.destroy();return False
        return True
    except Exception as exc:
        import traceback
        errors.append(traceback.format_exc());app.destroy();return False
GLib.timeout_add(180,tick);Gtk.main();server.shutdown()
result=dict(passed=not errors,errors=errors,records=records,operations=[op for op,_ in operations],
            camera_used=False,real_network_modified=False,audio_played=False)
(OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False));raise SystemExit(bool(errors))
