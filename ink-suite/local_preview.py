"""Explicit synthetic local preview; never opens a camera or contacts a board.

Run on a Linux GTK3 desktop: python3 -B local_preview.py --synthetic
Existing games show their entry instructions; this preview does not execute
ARM game libraries. The three-section menu and health lesson run natively.
"""
import argparse,json,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from health.catalog import reference_pose
from suite_control import SuiteControl

class PreviewBackend:
    def __init__(self):
        self.lock=threading.RLock();self.owner=SuiteControl();self.owner.configure(dict(active=True,mode='ink',menu=True))
        self.sequence=0;self.posts=[];self.paths=[];self.errors=[];self.cursor=None
        self.state_provider=None;self.stamp=time.monotonic;self.loss=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def reply(self,payload):
                blob=json.dumps(payload,allow_nan=False).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(blob)));self.end_headers()
                try:self.wfile.write(blob)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                with owner.lock:
                    owner.paths.append(self.path)
                    if self.path!='/api/state':owner.errors.append(self.path);self.send_error(404);return
                    owner.sequence+=1;stamp=owner.stamp()
                    points=owner.state_provider() if owner.state_provider else reference_pose('prepare',0.)
                    if owner.cursor is not None:
                        # The inverse of the existing observed right-hand menu
                        # projection is used only by deterministic local QA.
                        points=[[.5,.5,0.] for _ in range(17)]
                        points[5]=[.6,.4,1.];points[6]=[.4,.4,1.]
                        points[7]=[.65,.55,1.];points[8]=[.35,.55,1.]
                        points[9]=[.65,.65,1.];scale=.2*16/9
                        x,y=owner.cursor
                        points[10]=[.5-(x-.5)*scale*3.2/(16/9),.4+(y-.35)*scale*2.6,1.]
                        for i,p in ((11,(.55,.64)),(12,(.45,.64)),(13,(.55,.8)),(14,(.45,.8)),(15,(.55,.95)),(16,(.45,.95))):points[i]=[*p,1.]
                    if owner.loss:points=None
                    result=dict(pid=7654321,sequence=owner.sequence,timestamp_seconds=stamp,pose_count=owner.sequence,
                        phase='unknown',available=True,stale=False,points17=points,pose_fps=None,latency_ms=None,
                        synthetic=True,camera_link=dict(present=True),suite=dict(owner.owner.snapshot(),input_mode='body'))
                self.reply(result)
            def do_POST(self):
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if self.path!='/api/command' or not 0<length<8192:raise ValueError('Only bounded local suite commands are accepted')
                    command=json.loads(self.rfile.read(length))
                    if command.get('command')!='suite':raise ValueError('Preview does not save, clear or modify real artwork')
                    with owner.lock:owner.posts.append(dict(command));owner.owner.configure(command)
                    self.reply(dict(accepted=True,synthetic=True))
                except (ValueError,TypeError) as exc:
                    owner.errors.append(str(exc));self.send_error(400)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,name='synthetic-local-api',daemon=True)
        self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=2.)

def arguments(base):
    return SimpleNamespace(base=base,offline_preview=True,fullscreen=False,preserve_display=True,entry_delay=0.,
        visitor_data=None,visitor_port=8091,ink_source='',fight_source='',fruit_source='')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--synthetic',action='store_true',required=True)
    parser.parse_args()
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk
    from main import Suite
    backend=PreviewBackend();window=Suite(arguments(backend.base))
    window.set_title('本地功能预览 · 合成动作 · 不连接摄像头或板卡')
    banner=Gtk.Label(label='本地预览：关节输入为合成数据；游戏仅预览入口说明，不代表真人识别效果。')
    banner.set_margin_top(4);banner.set_margin_bottom(4)
    container=window.get_child();container.pack_start(banner,False,False,0);container.reorder_child(banner,0);banner.show()
    def instruction_only(mode):
        window.actions.set_availability(True,'本地预览仅查看游戏操作说明；八段锦可完整运行，现有游戏请在板端游玩。')
        return False
    window.launch_selected=instruction_only
    backend.state_provider=lambda:list(window.health_view.current.get('reference_pose') or reference_pose('prepare',0.))
    try:Gtk.main()
    finally:backend.close()

if __name__=='__main__':main()
