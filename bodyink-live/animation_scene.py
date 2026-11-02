"""Rebuild exact ink from authoritative records, without pose inference.

One worker owns the follower. GUI owns the returned players/recorders.
On restart/undo/clear/late join show a complete authoritative snapshot;
ordinary appended actions are delivered in order for animation.
"""
import copy
from ink_renderer_progressive import InkRenderer
from progressive_ink import Recorder,Player

class SceneFollower:
 def __init__(self):
  self.epoch=None;self.records=[];self.renderer=None;self.revision=None

 def prepare(self,scene,barrier=0):
  if scene.get('schema')!=1:raise ValueError('Unsupported animation scene')
  width,height=int(scene['width']),int(scene['height'])
  if not (320<=width<=3840 and 240<=height<=2160):raise ValueError('Invalid canvas size')
  records=scene['events'];epoch=(scene['session'],scene['generation'],width,height,scene['seed'],barrier)
  if epoch==self.epoch and scene['revision']==self.revision:return None
  prefix=records[:len(self.records)]==self.records
  reset=epoch!=self.epoch or not prefix or len(records)-len(self.records)>8
  if reset:
   renderer=InkRenderer(width,height,seed=scene['seed'])
   for record in records:
    e=copy.deepcopy(record);renderer._paint(e);renderer._records.append(e)
   steps=[]
  else:
   renderer=self.renderer;steps=[]
   for record in records[len(self.records):]:
    base=renderer.image();recorder=Recorder();renderer.animation_observer=recorder
    e=copy.deepcopy(record);renderer._paint(e);renderer._records.append(e)
    renderer.animation_observer=None
    steps.append(dict(base=base,recorder=recorder,action=record['action'],player=Player(base,recorder,duration=4.2)))
  self.renderer=renderer;self.records=copy.deepcopy(records);self.epoch=epoch;self.revision=scene['revision']
  return dict(reset=reset,steps=steps,final=renderer.image(),barrier=barrier,epoch=epoch,revision=self.revision)
