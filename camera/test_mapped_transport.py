"""Check mapped lifetime using the production 1080p sender and smaller synthetic replays."""
import tempfile,subprocess,time,sys,gc,os,json
from pathlib import Path
import numpy as np
from snapshot import capture_snapshot
from transport_fixture import producer_command
root=Path(__file__).resolve().parent;results=[]
def descriptors():return len(os.listdir('/proc/self/fd'))
gc.collect();before=descriptors()
for repeat in range(4):
 for mode,w,h in [('',1920,1080),('720',1280,720),('540',960,540)]:
  with tempfile.TemporaryDirectory() as d:
   p=subprocess.Popen(producer_command(d,mode),stdout=subprocess.DEVNULL)
   try:
    sock=Path(d)/'snapshot.sock'
    for _ in range(100):
     if sock.exists():break
     time.sleep(.01)
    m,b=capture_snapshot(str(sock),copy=False)
    assert m['buffer_mode']=='readonly_mmap' and (m['width'],m['height'],len(b))==(w,h,w*h*2)
    assert p.wait(5)==0
    try:b[0]=1
    except TypeError:pass
    else:raise AssertionError('Mapping must reject writes')
    view=np.frombuffer(b,dtype=np.uint8);assert not view.flags.writeable
    del b;gc.collect()
    # Producer exited, received fd closed, original Python mapping variable gone.
    expected=np.tile(np.arange(251,dtype=np.uint8),(len(view)+250)//251)[:len(view)]
    assert np.array_equal(view,expected)
    del view,expected;gc.collect()
    assert descriptors()==before,(before,descriptors())
    results.append({'repeat':repeat,'width':w,'height':h,'passed':True})
   finally:
    if p.poll() is None:p.kill();p.wait()
(root/'mapped-tests.json').write_text(json.dumps({'tests':results,'fd_before':before,'fd_after':descriptors()},indent=2));print('12 mapped transport/lifetime checks passed; descriptors:',descriptors())
