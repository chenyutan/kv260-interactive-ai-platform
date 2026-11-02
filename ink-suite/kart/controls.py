"""Fresh body input and the shared gamepad ownership/reconnect contract."""
import math
from runner.controls import PoseControls as SourceValidator

CONTROL_HINT='身体左右倾转向 · 左手抬起漂移 · 右手抬起用道具 · 双手举高开始'

def gamepad_action(sample):
    pressed=sample.get('pressed',set());buttons=sample.get('buttons',set())
    return dict(sample,steer=sample.get('lx',0.),accelerate=1.,
                brake=sample.get('lt',0.)>.3 or 'DOWN' in buttons,
                drift=bool(buttons & {'B','LB'}),item=bool(pressed & {'X','RB'}),
                confirm='A' in pressed)

class PoseControls:
    def __init__(self,**kwargs):
        self.validator=SourceValidator(**kwargs);self.sequence=None;self.steer=0.
        self.right_armed=False;self.confirm_since=None;self.confirm_fired=False;self.drift=False
    def reset(self):
        self.validator.reset();self.steer=0.;self.right_armed=False
        self.confirm_since=None;self.confirm_fired=False;self.drift=False
    def update(self,packet,now):
        result=self.validator.update(packet,now)
        out=dict(valid=result['valid'],reason=result['reason'],steer=0.,accelerate=1.,
                 brake=False,drift=False,item=False,confirm=False)
        if not result['valid']:
            self.steer=0.;self.right_armed=False;self.confirm_since=None;self.confirm_fired=False;self.drift=False
            return out
        if result['reason']=='duplicate_pose':
            out['steer']=self.steer
            out['drift']=self.drift
            return out
        points=(packet or {}).get('points17',())
        point=self.validator._point
        try:
            ls,rs=point(points,5),point(points,6)
            lw,rw=point(points,9),point(points,10)
            lh,rh=point(points,11),point(points,12)
            width=math.dist(ls,rs)
        except (TypeError,IndexError):return out
        if width<.055:return out
        # The torso line needs no neutral calibration and survives one lost wrist.
        lean=0.
        if lh and rh:
            lean=((ls[0]+rs[0])-(lh[0]+rh[0]))/(2*width)*3.3
        wheel=None
        if lw and rw and abs(lw[0]-rw[0])>width*.25:
            if max(lw[1]-ls[1],rw[1]-rs[1])<width*1.8:
                screen_left,screen_right=sorted((lw,rw),key=lambda p:p[0])
                # A clockwise wheel raises its screen-left grip and lowers
                # its screen-right grip, so positive slope means right turn.
                wheel=(screen_right[1]-screen_left[1])/width*1.4
        target=wheel if wheel is not None and abs(wheel)>.18 else lean
        target=max(-1.,min(1.,target));target=0. if abs(target)<.10 else target
        self.steer=self.steer*.52+target*.48
        left_up=lw is not None and lw[1]<=ls[1]+width*.10
        right_up=rw is not None and rw[1]<=rs[1]+width*.10
        both=left_up and right_up
        stamp=packet['timestamp_seconds']
        if both:
            if self.confirm_since is None:self.confirm_since=stamp
            if not self.confirm_fired and stamp-self.confirm_since>=.30:
                out['confirm']=True;self.confirm_fired=True
        else:self.confirm_since=None;self.confirm_fired=False
        if rw and rw[1]>rs[1]+width*.30:self.right_armed=True
        if right_up and not both and self.right_armed:
            out['item']=True;self.right_armed=False
        self.drift=left_up and not both
        out.update(steer=self.steer,drift=self.drift)
        return out
