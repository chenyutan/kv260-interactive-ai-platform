"""Small preparation-only spatial tolerance; terminal and velocity unchanged."""
import types
import numpy as np
from action_engine import ActionEngine as Original

class ActionEngine(Original):
 def __init__(self,templates,calibration,config=None):
  super().__init__(templates,calibration,config=config)
  for branch in self._branches:
   original=branch._stable
   def stable(self,seconds,radius=None,_original=original):
    if _original(seconds,radius):return True
    if radius is not None or self.phase not in ('idle','prepare'):return False
    if not _original(seconds,.030):return False
    poses=np.array([p for _,p in self.window])
    candidates=[{v.action for v in self._start_matches(p)} for p in poses]
    if not candidates or len(candidates[0])!=1 or any(c!=candidates[0] for c in candidates):return False
    # Torso tilt is itself the A04 signal; do not relax its stability gate.
    if 'A04' in candidates[0]:return False
    torso=poses[:,[0,1,6,7]]
    spread=float(np.percentile(np.sqrt(np.mean((torso-np.median(torso,axis=0))**2,axis=(1,2))),90))
    return spread<=self.config['stable_radius']
   branch._stable=types.MethodType(stable,branch)
