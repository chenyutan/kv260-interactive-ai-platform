"""Exercise replay delivery/PTS invariants without importing VART or cameras."""
import ast,queue,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
source=Path(__file__).with_name('test_full_recorded_app.py').read_text(encoding='utf-8-sig')
node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='Replay')
class Base:
 def __init__(self,args):
  self.stop=threading.Event();self.latest=queue.Queue(1);self.preprocess_queue=queue.Queue(1);self.calls=[]
  def update(points,stamp,w,h):self.calls.append(stamp);return {'phase':'idle','reason':'test'}
  self.engine=SimpleNamespace(update=update);self.net=SimpleNamespace(predict=lambda rgb:(rgb,{}))
 def replace_latest(self,q,value):return 'preview-policy'
env=dict(a=SimpleNamespace(lz4=False,pack=Path('.')),m={},NpzSource=lambda *args:None,app=SimpleNamespace(LiveApp=Base),queue=queue,threading=threading,time=time,np=np)
exec(compile(ast.Module(body=[node],type_ignores=[]),'<replay-class>','exec'),env)
Replay=env['Replay']
class DeliveryTests(unittest.TestCase):
 def test_fast_producer_never_replaces_slow_consumer_inputs(self):
  r=Replay(None);seen=[]
  def feed():
   for i in range(80):r.replace_latest(r.latest,i)
  t=threading.Thread(target=feed);t.start()
  for i in range(80):seen.append(r.latest.get(timeout=2));time.sleep(.001)
  t.join(2);self.assertFalse(t.is_alive());self.assertEqual(seen,list(range(80)))
 def test_shutdown_unblocks_full_queue(self):
  r=Replay(None);r.latest.put(0);t=threading.Thread(target=lambda:r.replace_latest(r.latest,1));t.start();r.stop.set();t.join(1)
  self.assertFalse(t.is_alive());self.assertEqual(r.latest.get(),0)
 def test_preview_keeps_production_policy(self):
  r=Replay(None);self.assertEqual(r.replace_latest(queue.Queue(),1),'preview-policy')
 def test_source_pts_drives_engine_not_replay_wall_clock(self):
  r=Replay(None);r.mapping[999]=(0,1.25);r.engine.update(np.zeros((17,3)),999,1920,1080)
  self.assertEqual(r.calls,[1.25]);self.assertEqual(r.seen,[0]);self.assertFalse(r.mapping)
 def test_out_of_order_or_dropped_frame_fails(self):
  r=Replay(None);r.mapping[999]=(1,1.25)
  with self.assertRaises(AssertionError):r.engine.update(np.zeros((17,3)),999,1920,1080)
  self.assertFalse(r.calls)
if __name__=='__main__':unittest.main()
