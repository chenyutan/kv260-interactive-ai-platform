"""Offline candidate: do not commit a stable window with no matching median.

Previously that failed commitment cleared all preparation history, delaying a
valid start approaching the template boundary. Keep waiting with the existing
causal rolling window instead. All numerical gates remain unchanged.
"""
import types
import numpy as np
from preparation_action_engine import ActionEngine as Original

class ActionEngine(Original):
 def __init__(self,templates,calibration,config=None):
  super().__init__(templates,calibration,config=config)
  for branch in self._branches:
   original=branch._stable
   def stable(self,seconds,radius=None,_original=original):
    result=_original(seconds,radius)
    if result and radius is None and self.phase in ('idle','prepare'):
     median=np.median(np.asarray([p for _,p in self.window]),axis=0)
     return bool(self._start_matches(median))
    return result
   branch._stable=types.MethodType(stable,branch)
