"""Desktop entry: own a single game session and switch its pose services."""
import argparse,fcntl,json,os,subprocess,sys,threading,time,urllib.request
from pathlib import Path
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,GLib
from session_record import CONTROL

ROOT=Path(__file__).resolve().parent

def active(unit):
    return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0

def service(operation,unit):
    r=subprocess.run(['sudo','-n','systemctl',operation,unit],capture_output=True,text=True,timeout=45)
    if r.returncode:raise RuntimeError(f'{unit}: {r.stderr.strip()}')

def wait_ready(timeout=35):
    deadline=time.monotonic()+timeout;previous=None
    while time.monotonic()<deadline:
        try:
            pid=int(subprocess.check_output(['systemctl','show','fruit-pose.service','-p','MainPID','--value'],text=True).strip())
            with urllib.request.urlopen('http://127.0.0.1:8088/api/state',timeout=1) as response:s=json.load(response)
            key=(s.get('sequence'),s.get('timestamp_seconds'))
            if pid>0 and int(s.get('pid',-1))==pid:
                if previous is not None and key!=previous:return dict(pid=pid,sequence=s.get('sequence'),ready=True)
                previous=key
        except (OSError,ValueError,TypeError):pass
        time.sleep(.25)
    raise RuntimeError('摄像头/腕点服务未产生新的帧，请检查 USB 摄像头连接。')

class Launcher:
    def __init__(self,extra_args=()):
        self.extra_args=list(extra_args)
        self.window=Gtk.Window(title='墨武格斗');self.window.set_default_size(430,120)
        self.label=Gtk.Label(label='正在启动摄像头与肩肘腕识别，请稍候…');self.label.set_line_wrap(True);self.label.set_margin_start(16);self.label.set_margin_end(16);self.window.add(self.label)
        self.window.connect('delete-event',lambda *_:True);self.window.show_all()
        threading.Thread(target=self.run,daemon=True).start()
    def error(self,message):
        self.window.hide()
        dialog=Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR,buttons=Gtk.ButtonsType.CLOSE,text='墨武格斗启动失败')
        dialog.format_secondary_text(message);dialog.run();dialog.destroy();Gtk.main_quit();return False
    def run(self):
        restore_ink=active('bodyink-live.service');was_fruit=active('fruit-pose.service');changed=False
        error=None
        try:
            service('stop','bodyink-live.service');changed=True
            service('start','fruit-pose.service')
            status=wait_ready();status['default_input']='pose';status['restore_ink_on_exit']=restore_ink
            (ROOT/'launcher-status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
            GLib.idle_add(self.window.hide)
            result=subprocess.run([sys.executable,str(ROOT/'main.py'),'--pose']+self.extra_args,cwd=ROOT)
            if result.returncode:raise RuntimeError(f'游戏进程退出码 {result.returncode}；详见 launcher.log。')
        except Exception as exc:error=str(exc)
        finally:
            if '--record-session' in self.extra_args:
                directory=str(Path(self.extra_args[self.extra_args.index('--record-session')+1]).resolve())
                try:
                    if json.loads(CONTROL.read_text(encoding='utf-8')).get('directory')==directory:CONTROL.unlink(missing_ok=True)
                except (OSError,ValueError):pass
            try:
                if changed and (restore_ink or not was_fruit):service('stop','fruit-pose.service')
                if changed and restore_ink:service('start','bodyink-live.service')
            except Exception as exc:error=(error+'\n' if error else '')+'恢复服务失败：'+str(exc)
        if error:GLib.idle_add(self.error,error)
        else:GLib.idle_add(Gtk.main_quit)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--record-session');args=parser.parse_args()
    extra_args=['--live-test','--record-session',args.record_session] if args.record_session else []
    lock=open(Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'))/f'fruit-game-{os.getuid()}.lock','w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        dialog=Gtk.MessageDialog(message_type=Gtk.MessageType.INFO,buttons=Gtk.ButtonsType.OK,text='墨武格斗已打开，请先退出切西瓜或已有格斗窗口。');dialog.run();dialog.destroy();sys.exit(0)
    log=open(ROOT/'launcher.log','a',buffering=1);os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
    launcher=Launcher(extra_args);Gtk.main()
