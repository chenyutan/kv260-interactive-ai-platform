"""Single-instance native suite launcher; camera services stay shared."""
import argparse,fcntl,json,os,signal,subprocess,sys,time,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DISPLAY_SOURCE='/home/ubuntu/bodyink-live/native_display.py'


def notify(message):
    """Log launcher diagnostics without opening a blocking UI window."""
    print(message,file=sys.stderr,flush=True)


def find_displays(proc_root=Path('/proc'),uid=None):
    """Match the exact standalone script position, never runner --source args."""
    uid=os.getuid() if uid is None else uid;matches=[]
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():continue
        try:
            argv=[x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
            if len(argv)<2 or not Path(argv[0]).name.startswith('python') or argv[1]!=DISPLAY_SOURCE or proc.stat().st_uid!=uid:continue
            env=dict(chunk.decode().split('=',1) for chunk in (proc/'environ').read_bytes().split(b'\0') if b'=' in chunk)
            if not env.get('DISPLAY'):continue
            identity=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
            matches.append(dict(pid=int(proc.name),env=env,identity=identity))
        except (OSError,UnicodeError,ValueError,IndexError):continue
    return matches


def stop_display():
    matches=find_displays()
    if len(matches)>1:raise RuntimeError('检测到多个原水墨窗口，请先关闭重复窗口再打开墨境。')
    if not matches:return None
    old=matches[0];proc=Path('/proc')/str(old['pid'])
    # Recheck identity immediately before signaling an existing GUI process.
    current=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
    if current!=old['identity']:raise RuntimeError('原水墨窗口刚发生变化，请重新打开墨境。')
    os.kill(old['pid'],signal.SIGTERM)
    for _ in range(60):
        if not proc.exists():return old['env']
        try:
            fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]=='Z' or fields[19]!=old['identity']:return old['env']
        except OSError:return old['env']
        time.sleep(.1)
    raise RuntimeError('原水墨窗口未退出，未启动重复显示。')


def restore_display(env):
    if not env or find_displays():return
    with open('/home/ubuntu/bodyink-live/suite-display.log','ab') as log:
        subprocess.Popen(['/home/ubuntu/bodyink-live/start-display.sh'],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)


def active(unit):
    return subprocess.run(['systemctl','is-active','--quiet',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0


def service(op,unit,user=False):
    argv=(['systemctl','--user'] if user else ['sudo','-n','systemctl'])+[op,unit]
    subprocess.run(argv,capture_output=True,text=True,timeout=50,check=True)


def reset_backend():
    request=urllib.request.Request('http://127.0.0.1:8088/api/command',data=b'{"command":"suite","active":false,"mode":"ink","menu":false}',headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=3):pass
    except OSError:pass


def main():
    p=argparse.ArgumentParser();p.add_argument('--no-service-changes',action='store_true');args,extra=p.parse_known_args()
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'));lock=open(runtime/('ink-suite-'+str(os.getuid())+'.lock'),'w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    # Same lock as the previous individual game launchers: never allow a second
    # launcher to stop or replace the shared backend under this application.
    game_lock=open(runtime/('fruit-game-'+str(os.getuid())+'.lock'),'w')
    try:fcntl.flock(game_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        notify('请先退出已有切水果或格斗窗口，再打开墨境。');return 1
    child=None;display_env=None
    try:
        if not args.no_service_changes:
            if active('fruit-pose.service'):service('stop','fruit-pose.service')
            # bodyink-live depends on the single camera and DPU owner; no restart
            # when already active, so existing canvas and frame pipeline persist.
            if not active('bodyink-live.service'):
                try:service('start','bodyink-live.service')
                except (OSError,subprocess.SubprocessError) as exc:
                    # The native settings page must remain reachable even if
                    # a disconnected device prevents backend startup. The host
                    # still rejects stale/absent pose and exposes targeted retry.
                    print('识别服务暂未启动，仍可进入设备管理：'+str(exc),file=sys.stderr)
            display_env=stop_display()
        # Keep GTK double buffering while avoiding ARMSOC's repeated GEM
        # backing-pixmap allocation. The host propagates this image-backed
        # setting to embedded games; each game's visual/format guard still
        # chooses a matching native conversion or the correct Cairo fallback.
        child_env=dict(os.environ,GDK_RENDERING='image')
        child=subprocess.Popen([sys.executable,str(ROOT/'main.py')]+extra,cwd=ROOT,env=child_env)
        def stop(*_):
            if child and child.poll() is None:child.terminate()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        return child.wait()
    except (OSError,RuntimeError,subprocess.SubprocessError) as exc:
        notify('墨境启动失败：'+str(exc));return 1
    finally:
        if not args.no_service_changes:
            reset_backend()
            try:restore_display(display_env)
            except (OSError,subprocess.SubprocessError):pass

if __name__=='__main__':raise SystemExit(main())
