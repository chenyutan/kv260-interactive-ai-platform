"""Bounded native photo waiting/reveal animation; no processing or IO here.

Stage names come from the worker, never from elapsed-time guesses. The small
vector motion is independent of the photograph; Pango labels are cached.
"""
import math
import time

STAGES = {'queued':'等待处理', 'loading_model':'正在准备画笔',
          'preparing_image':'正在整理画面', 'painting':'正在绘制笔墨',
          'finishing':'正在设色和整理细节', 'finished':'作品已完成',
          'processing':'正在处理照片', 'importing':'正在导入照片'}


class PhotoWaitState:
    """Only a genuine waiting->completed transition earns a brief reveal."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.key=None;self.waiting=False;self.job_waiting=False;self.stage=None;self.elapsed=0
        self.completed_pending=False;self.reveal_at=None;self.origin=0.

    def update(self, job, busy=False, preview_ready=False, now=None):
        now=time.monotonic() if now is None else now
        key=job.get('id') if job else None
        state=job.get('status') if job else None
        # Importing a selected file is a new activity, even if a previous
        # completed image is still selected while submission IO finishes.
        waiting=bool(busy or state in ('queued','processing'))
        if key!=self.key:
            self.reset();self.key=key
        if waiting:
            if not self.waiting:self.origin=now
            self.reveal_at=None;self.completed_pending=False
            self.stage='importing' if busy else job.get('stage',state)
            self.elapsed=max(0,int(job.get('elapsed_s',0))) if job and not busy else 0
        elif state=='completed' and self.job_waiting:
            self.completed_pending=True
        elif state!='completed':
            self.completed_pending=False;self.reveal_at=None
        self.waiting=waiting
        self.job_waiting=state in ('queued','processing') and not busy
        if self.completed_pending and preview_ready:
            self.completed_pending=False;self.reveal_at=now
        return self.animated(now)

    def animated(self, now=None):
        now=time.monotonic() if now is None else now
        return self.waiting or (self.reveal_at is not None and 0<=now-self.reveal_at<1.6)

    def reveal_age(self,now):
        return now-self.reveal_at if self.reveal_at is not None else None


class PhotoWaitPainter:
    """One cached label surface plus at most 52 short animated vector paths."""
    def __init__(self):
        self.labels_key=None;self.labels=None;self.cache_builds=0
        # Sampled once, never allocated from a photo or randomness per frame.
        self.ink=tuple((i/35.,math.sin(i*.35)*.035+math.sin(i*.13)*.023,
                        .6+2.7*math.sin(math.pi*i/35.)**.65) for i in range(36))

    def _labels(self,w,h,state):
        key=(w,h,state.stage,state.elapsed)
        if key==self.labels_key:return
        import cairo
        from menu_view import text
        self.labels=cairo.ImageSurface(cairo.FORMAT_ARGB32,w,h)
        cr=cairo.Context(self.labels)
        text(cr,STAGES.get(state.stage,'正在处理照片'),w*.06,h*.40,19,(.15,.27,.23),bold=True,width=w*.88,centered=True)
        subtitle=('正在读取原图，请稍候' if state.stage=='importing' else
                  '排队中，制作开始后会自动更新' if state.stage=='queued' else
                  '已用 '+str(state.elapsed)+' 秒 · 清晰制作通常需要约 2–3 分钟')
        text(cr,subtitle,w*.04,h*.59,11,(.42,.49,.41),width=w*.92,centered=True)
        text(cr,'完成后自动展示 · 返回创作不会中断制作',w*.03,h*.80,10,(.48,.54,.46),width=w*.94,centered=True)
        self.labels_key=key;self.cache_builds+=1

    def draw(self,cr,w,h,state,now=None):
        now=time.monotonic() if now is None else now
        if state.waiting:
            # The real source remains visible as a quiet underpainting. This is
            # an indeterminate visual, deliberately not a progress percentage.
            cr.set_source_rgba(.975,.963,.929,.93);cr.paint()
            t=now-state.origin
            cx,cy=w*.5,h*.27;span=min(260,w*.57)
            cr.set_line_cap(1)
            for sign,points in ((1,self.ink),(-1,reversed(self.ink))):
                for index,(u,y,width) in enumerate(points):
                    point=(cx-span*.5+u*span,cy+y*span+sign*width)
                    if sign==1 and index==0:cr.move_to(*point)
                    else:cr.line_to(*point)
            cr.close_path();cr.set_source_rgba(.13,.31,.27,.47+.07*math.sin(t*1.3));cr.fill()
            cr.set_source_rgba(.23,.46,.37,.24);cr.set_line_width(.7)
            for index,(u,y,width) in enumerate(self.ink):
                point=(cx-span*.5+u*span,cy+y*span-width*.4)
                if index==0:cr.move_to(*point)
                else:cr.line_to(*point)
            cr.stroke()
            # A bright, tiny ink bead travels along the existing brush line.
            u=(t*.18)%1.;point=self.ink[min(35,int(u*35))]
            cr.set_source_rgba(.12,.41,.34,.85);cr.arc(cx-span*.5+u*span,cy+point[1]*span,2.8,0,math.tau);cr.fill()
            for i in range(3):
                phase=(t*.23+i/3.)%1.
                cr.set_source_rgba(.35,.56,.47,(1.-phase)*.20)
                cr.set_line_width(.8);cr.save();cr.translate(cx,cy+8);cr.scale(1.,.23)
                cr.arc(0,0,18+phase*span*.55,0,math.tau);cr.stroke();cr.restore()
            self._labels(w,h,state);cr.set_source_surface(self.labels,0,0);cr.paint()
        else:
            age=state.reveal_age(now)
            if age is None or not 0<=age<1.6:return
            # Result is visible immediately. Only the small seal fades in/out;
            # generation and image display are never held for the animation.
            alpha=min(1.,age/.16)*min(1.,(1.6-age)/.55)
            x,y=w-57,18
            cr.save();cr.set_source_rgba(.65,.22,.16,.88*alpha);cr.set_line_width(2.)
            cr.rectangle(x,y,35,38);cr.stroke();cr.rectangle(x+4,y+4,27,30);cr.stroke()
            # A clear completion check inside the seal; no decorative wording,
            # raster asset or per-frame font layout is needed.
            cr.move_to(x+8,y+19);cr.line_to(x+15,y+26);cr.line_to(x+28,y+11)
            cr.stroke();cr.restore()
