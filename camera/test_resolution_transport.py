"""Exercise actual C memfd producer and Python reader without camera access."""
import tempfile,subprocess,time,json,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from snapshot import capture_snapshot
root=Path(__file__).resolve().parent
results=[]
for mode,w,h in [('',1920,1080),('720',1280,720),('540',960,540)]:
    with tempfile.TemporaryDirectory() as d:
        p=subprocess.Popen([str(root/'selftest-resolution'),d]+([mode] if mode else []))
        try:
            sock=Path(d)/'snapshot.sock'
            for _ in range(100):
                if sock.exists():break
                time.sleep(.01)
            m,b=capture_snapshot(str(sock))
            assert (m['width'],m['height'],m['stride'],len(b))==(w,h,w*2,w*h*2)
            expected=(bytes(range(251))*((len(b)+250)//251))[:len(b)]
            assert b==expected
            assert m['sequence']==42 and m['sealed_memfd'] is True
            assert p.wait(5)==0
            results.append({'width':w,'height':h,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'passed':True})
        finally:
            if p.poll() is None:p.kill();p.wait()
(root/'resolution-tests.json').write_text(json.dumps(results,indent=2));print(json.dumps(results))
