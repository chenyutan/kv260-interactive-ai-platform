"""Offline animation candidate: ordered layer patches, never inference frames.

Final patches contain the exact cumulative event layer, so overlapping petals
and strokes preserve Pillow's original replacement/compositing semantics.
"""
from dataclasses import dataclass
import math
import numpy as np
from PIL import Image, ImageDraw


@dataclass
class Operation:
    before: Image.Image
    after: Image.Image
    xy: tuple
    path: object
    width: float
    kind: str
    weights: object

    def tile(self, progress):
        if progress >= 1:return self.after
        if progress <= 0:return self.before
        if self.kind == 'wash-v2':
            # Reveal precomputed pigment from left to right, with a soft edge.
            # No noise, blur or full-image floating-point texture work per tick.
            mask=Image.new('L',self.after.size)
            draw=ImageDraw.Draw(mask); feather=16
            edge=progress*(self.after.width+feather)
            if edge>=feather:draw.rectangle((0,0,int(edge-feather),self.after.height),fill=255)
            for k in range(feather):
                x=int(edge-feather+k)
                if 0<=x<self.after.width:draw.line((x,0,x,self.after.height),fill=int(255*(1-k/feather)))
            return Image.composite(self.after,self.before,mask)
        if self.path is None:return Image.blend(self.before,self.after,progress)
        q=self.path
        weights=np.asarray(self.weights,float)
        cumulative=np.r_[0.,np.cumsum(weights)]
        target=progress*cumulative[-1]
        index=min(len(q)-2,int(np.searchsorted(cumulative,target,side='right')-1))
        fraction=(target-cumulative[index])/max(weights[index],1e-9)
        points=[tuple(v) for v in q[:index+1]]
        points.append(tuple(q[index]+fraction*(q[index+1]-q[index])))
        mask=Image.new('L',self.after.size)
        draw=ImageDraw.Draw(mask);width=max(1,math.ceil(self.width))
        draw.line(points,fill=255,width=width)
        radius=width/2
        for x,y in (points[0],points[-1]):draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=255)
        return Image.composite(self.after,self.before,mask)


class Recorder:
    def __init__(self):self.operations=[]

    def record(self,before,after,xy,path,width,kind,brush_widths):
        if path is not None:
            path=np.asarray(path,float).copy()
            distance=np.linalg.norm(np.diff(path,axis=0),axis=1)
            # Width profile already derives from true speed. Larger widths
            # receive more animation time without modifying the ink profile.
            widths=np.asarray(brush_widths,float)
            weights=np.maximum(distance,1e-5)*(1+widths[:len(distance)]/max(float(widths.max()),1e-6))
        else:weights=None
        self.operations.append(Operation(before,after,tuple(xy),path,width,kind,weights))

    def wrap_draw(self,draw,layer):
        recorder=self
        class Draw:
            def __getattr__(self,name):
                fn=getattr(draw,name)
                if name not in ('ellipse','line'):raise AttributeError(name)
                def invoke(coords,**kwargs):
                    points=np.asarray(coords,float).reshape(-1,2)
                    pad=kwargs.get('width',1)+2
                    lo=np.floor(points.min(axis=0)-pad).astype(int)
                    hi=np.ceil(points.max(axis=0)+pad+1).astype(int)
                    bbox=(max(0,int(lo[0])),max(0,int(lo[1])),min(layer.width,int(hi[0])),min(layer.height,int(hi[1])))
                    if bbox[2]<=bbox[0] or bbox[3]<=bbox[1]:return fn(coords,**kwargs)
                    before=layer.crop(bbox);result=fn(coords,**kwargs)
                    recorder.record(before,layer.crop(bbox),bbox[:2],None,0,'detail',None)
                    return result
                return invoke
        return Draw()

    @property
    def raw_bytes(self):
        return sum(o.before.width*o.before.height*8 for o in self.operations)


class Player:
    """Bounded per-event player; caller drives advance() with monotonic time.

    No worker threads, sleeps or queued full-resolution video frames. Late ticks
    skip intermediate presentations but still apply all completed operations.
    """
    def __init__(self,base,recorder,duration=4.2):
        self.base=base.convert('RGBA');self.layer=Image.new('RGBA',base.size)
        self.frame=base.convert('RGB');self.previous_box=None
        self.operations=recorder.operations;self.index=0;self.progress=0.
        def timing_weight(o):
            if o.kind == 'wash-v2':return 3.5
            if o.kind == 'stroke-v2':
                length=float(np.linalg.norm(np.diff(o.path,axis=0),axis=1).sum())
                return min(8.,max(.25,length/(base.width/16.)))
            return .6 if o.kind=='wash' else .08 if o.kind=='detail' else 1.
        weight=np.array([timing_weight(o) for o in self.operations])
        self.ends=np.cumsum(weight)/max(float(weight.sum()),1e-9)*duration
        self.duration=duration;self.cancelled=False

    def advance(self,elapsed,copy_frame=True):
        if self.cancelled:return self.base.convert('RGB')
        elapsed=max(self.progress,float(elapsed));self.progress=elapsed
        boxes=[];self.last_dirty=None
        if self.previous_box:boxes.append(self.previous_box)
        def box(o):return (o.xy[0],o.xy[1],o.xy[0]+o.after.width,o.xy[1]+o.after.height)
        while self.index<len(self.operations) and elapsed>=self.ends[self.index]:
            o=self.operations[self.index];self.layer.paste(o.after,o.xy);self.index+=1;boxes.append(box(o))
        partial=None;self.previous_box=None
        if self.index<len(self.operations):
            o=self.operations[self.index];start=self.ends[self.index-1] if self.index else 0.
            fraction=(elapsed-start)/max(self.ends[self.index]-start,1e-9)
            partial=(o.tile(fraction),o.xy);self.previous_box=box(o);boxes.append(box(o))
        if boxes:
            dirty=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
            visible=self.layer.crop(dirty)
            if partial:
                tile,xy=partial;visible.paste(tile,(xy[0]-dirty[0],xy[1]-dirty[1]))
            composite=Image.alpha_composite(self.base.crop(dirty),visible).convert('RGB')
            self.frame.paste(composite,dirty[:2])
            self.last_dirty=dirty
        return self.frame.copy() if copy_frame else None

    def advance_patch(self,elapsed):
        if self.cancelled:return None
        self.advance(elapsed,copy_frame=False)
        if self.last_dirty:return self.last_dirty,self.frame.crop(self.last_dirty)
        return None

    def cancel(self):
        self.cancelled=True;self.operations=[];self.layer=Image.new('RGBA',(1,1))


def art_bounds(width,height):
    """16:9 art region beside a bounded control panel, across screen sizes."""
    available_w=max(240,width-424)
    available_h=max(180,height-160)
    scale=min(available_w/16,available_h/9)
    return int(16*scale),int(9*scale)
