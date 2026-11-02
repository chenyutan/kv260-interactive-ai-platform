"""Causal torso-centered square crops; coordinates and scores remain untouched."""
import math,threading
import numpy as np

class BodyCrop:
    def __init__(self):
        self.lock=threading.RLock();self.reset()

    def reset(self):
        with self.lock:
            self.center=None;self.stamp=None;self.geometry=None

    def snapshot(self,width,height,timestamp,defaults,hold=False):
        # Return immutable per-frame geometry, never a shared mutable list.
        with self.lock:
            if (self.geometry!=(width,height) or self.stamp is None or
                    not math.isfinite(timestamp) or timestamp<self.stamp or (timestamp-self.stamp>1. and not hold)):
                self.center=None;self.stamp=None;self.geometry=(width,height)
            center=width/2 if self.center is None else self.center
            return tuple((int(np.clip(round(center-rw/2),0,width-rw)),y,rw,rh)
                         for _,y,rw,rh in defaults)

    def observe(self,points,timestamp,width,height):
        p=np.asarray(points,float)
        if p.shape!=(17,3) or not math.isfinite(timestamp):return False
        q=p[[5,6,11,12]]
        if not np.isfinite(q).all() or (q[:,2]<.3).any() or (q[:,:2]<0).any() or (q[:,:2]>1).any():return False
        shoulder=q[:2,:2].mean(0);hip=q[2:,:2].mean(0)
        if hip[1]-shoulder[1]<.08 or abs(hip[0]-shoulder[0])>.15:return False
        span=np.linalg.norm((q[0,:2]-q[1,:2])*[width,height])
        if span<18 or span>.5*width:return False
        target=float((shoulder[0]+hip[0])*.5*width)
        # Leave the original centered crop exactly unchanged for centered people.
        if abs(target-width/2)<=.025*width:target=width/2
        with self.lock:
            if self.geometry!=(width,height) or self.stamp is None or timestamp-self.stamp>1.:
                self.center=width/2;self.stamp=timestamp;self.geometry=(width,height)
            elif timestamp<=self.stamp:return False
            dt=max(0.,timestamp-self.stamp)
            delta=target-self.center
            if abs(delta)>8:self.center+=float(np.clip(delta,-width*.5*dt,width*.5*dt))
            self.stamp=timestamp
        return True
