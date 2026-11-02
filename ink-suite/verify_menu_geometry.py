"""Read only the suite's X11 window geometry; no screen or camera capture."""
import json,os,subprocess
from pathlib import Path
matches=[]
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():continue
    try:
        args=[a.decode() for a in (proc/'cmdline').read_bytes().split(b'\0') if a]
        if len(args)>1 and args[1]=='/home/ubuntu/ink-suite/main.py' and proc.stat().st_uid==os.getuid():
            env=dict(c.decode().split('=',1) for c in (proc/'environ').read_bytes().split(b'\0') if b'=' in c)
            matches.append((proc.name,env))
    except (OSError,ValueError,UnicodeError):pass
if len(matches)!=1:raise RuntimeError('Expected one suite host')
pid,env=matches[0]
r=subprocess.run(['xwininfo','-name','墨境 · 国风体感工坊'],env=env,capture_output=True,text=True,timeout=5,check=True)
print(r.stdout)
assert 'Width: 1024' in r.stdout and 'Height: 600' in r.stdout
assert 'Map State: IsViewable' in r.stdout
print(json.dumps(dict(pid=pid,width=1024,height=600,visible=True,image_capture=False)))
