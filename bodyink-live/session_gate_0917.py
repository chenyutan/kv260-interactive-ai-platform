"""Permit high-bandwidth BodyInk startup after a local desktop settles."""
import json,subprocess,time
from pathlib import Path

def eligible(p):
    return (p.get('Name')=='ubuntu' and p.get('Class')=='user' and
            p.get('Type') in ('x11','wayland') and p.get('Active')=='yes' and
            p.get('Remote')=='no' and p.get('Seat')=='seat0')

def settled(previous,session,now):
    if not session:return {},False
    if previous.get('session')!=session or not 0<=now-previous.get('since',now)<=86400:
        previous={'session':session,'since':now}
    return previous,now-previous['since']>=60

def command(*args):return subprocess.check_output(args,text=True,timeout=8)

def main():
    ready=Path('/run/bodyink-session-ready');state=Path('/run/bodyink-session-gate.json')
    if ready.exists():return
    ids=command('loginctl','list-sessions','--no-legend','--no-pager').splitlines();session=None
    for line in ids:
        sid=line.split()[0]
        try:
            data=command('loginctl','show-session',sid,'-p','Name','-p','Class','-p','Type','-p','Active','-p','Remote','-p','Seat')
        except subprocess.CalledProcessError:continue
        if eligible(dict(x.split('=',1) for x in data.splitlines() if '=' in x)):
            session=sid;break
    try:previous=json.loads(state.read_text())
    except (OSError,ValueError):previous={}
    updated,allow=settled(previous,session,time.monotonic())
    state.write_text(json.dumps(updated))
    if allow:
        ready.touch()
        try:
            subprocess.run(['systemctl','start','--no-block','bodyink-live.service'],check=True,timeout=8)
        except (OSError, subprocess.SubprocessError):
            # A failed enqueue must not prevent the next timer tick from trying.
            ready.unlink(missing_ok=True)
            raise
        print('Local desktop stable for 60 seconds; normal BodyInk startup permitted.')

if __name__=='__main__':main()
