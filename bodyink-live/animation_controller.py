"""GTK-only playback; scene preparation and HTTP run on a separate worker."""
import time
from collections import deque
from gi.repository import GLib
from progressive_ink import Player

class AnimationController:
 def __init__(self,widget):
  self.widget=widget;self.pending=deque();self.player=None;self.timer=None
  self.barrier=0;self.final=None;self.epoch=None

 def cancel(self):
  self.barrier+=1;self.pending.clear()
  if self.player:self.player.cancel()
  self.player=None
  if self.timer is not None:GLib.source_remove(self.timer);self.timer=None

 def receive(self,batch):
  if batch['barrier']!=self.barrier:return
  if batch['reset'] or self.epoch!=batch['epoch']:
   self.pending.clear();self.player=None
   self.widget.set_image(batch['final']);self.epoch=batch['epoch']
   if self.timer is not None:GLib.source_remove(self.timer);self.timer=None
  else:
   self.pending.extend(batch['steps'])
  self.final=batch['final']
  # Bound presentation lag under an artificial action burst. Preserve the
  # authoritative final picture rather than retaining unlimited image patches.
  if len(self.pending)>8:
   self.pending.clear();self.player=None;self.widget.set_image(self.final)
  if self.pending and self.timer is None:self.timer=GLib.timeout_add(16,self.tick)

 def tick(self):
  if self.player is None:
   if not self.pending:self.timer=None;return False
   step=self.pending.popleft();self.player=step.get('player')
   if self.player is None:self.player=Player(step['base'],step['recorder'],duration=4.2)
   # The previous step ended at this exact base; reset batches already
   # installed it. Avoid a redundant full-canvas copy on the GUI thread.
   self.started=time.monotonic()
  elapsed=time.monotonic()-self.started
  patch=self.player.advance_patch(elapsed)
  if patch:self.widget.apply_patch(*patch)
  if elapsed>=self.player.duration:self.player=None
  return True
