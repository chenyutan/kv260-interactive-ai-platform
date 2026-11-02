"""Five circular entries around an observed-only, camera-free skeleton."""
import math
import time
from collections import deque
import cairo
from render_policy import RenderPolicy, present
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo','1.0')
from gi.repository import Gtk, GLib, Pango, PangoCairo

from radial_layout import MODES, VISITOR_RECTS, geometry, rectangles, hit_index
from gesture_menu import ZONES as RECTS  # rectangular secondary menus retain their shared geometry
from experience_names import TITLES as DISPLAY_NAMES
TITLES = tuple(DISPLAY_NAMES[mode] for mode in MODES)
VISITOR_CHOICES=('visitor_download','visitor_finish')


def text(cr, value, x, y, size, color=(.16,.22,.22), bold=False, width=None, centered=False):
    layout=PangoCairo.create_layout(cr)
    desc=Pango.FontDescription(('Noto Serif CJK SC Bold ' if bold else 'Noto Sans CJK SC ')+str(size))
    layout.set_font_description(desc);layout.set_text(value,-1)
    if width:layout.set_width(int(width*Pango.SCALE));layout.set_ellipsize(Pango.EllipsizeMode.END)
    if centered:layout.set_alignment(Pango.Alignment.CENTER)
    cr.set_source_rgb(*color);cr.move_to(x,y);PangoCairo.show_layout(cr,layout);cr.new_path()


class MenuView(Gtk.DrawingArea):
    radial=True
    def __init__(self, choose,visitor_controls=False):
        super().__init__();self.choose=choose;self.pointer=None;self.hover=None;self.progress=0.
        self.visitor_controls=visitor_controls
        self.mode_choices=MODES+('pose_view',)+VISITOR_CHOICES if visitor_controls else MODES+('pose_view',)
        self.message='右臂在身体侧面转两圈呼出菜单 · 右手移动，停留 2 秒确认'
        self.selection_enabled=True
        self.target=None;self.trail=deque(maxlen=24);self.trail_anchor=None;self.sample_key=None;self.sample_received=None
        self.last_tick=None;self.timer=None;self.static_surface=None;self.cache_key=None;self.cache_builds=0;self.backbuffer=None
        self.dirty_previous=[];self.selection_dirty=True;self.previous_hover=None;self.previous_progress=0.;self.visual_signature=None
        self.set_size_request(640,420);self.set_hexpand(True);self.set_vexpand(True)
        from gi.repository import Gdk
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect('button-press-event',self.click);self.connect('draw',self.draw)
        self.connect('map',self.mapped);self.connect('unmap',self.unmapped);self.connect('destroy',self.unmapped)
    @property
    def rectangles(self):
        return rectangles(max(1,self.get_allocated_width()),max(1,self.get_allocated_height()),self.visitor_controls)
    def hit_index(self,x,y):
        return hit_index(x,y,max(1,self.get_allocated_width()),max(1,self.get_allocated_height()),self.visitor_controls)
    def mapped(self,*_):
        self.last_tick=None
        if self.timer is None:self.timer=GLib.timeout_add(30,self.animate)
    def unmapped(self,*_):
        if self.timer is not None:GLib.source_remove(self.timer);self.timer=None
        self.clear_pointer();self.dirty_previous=[];self.visual_signature=None
    def clear_pointer(self):
        self.pointer=self.target=None;self.trail.clear();self.trail_anchor=None;self.sample_received=None
    def set_availability(self,ready,message):
        ready=bool(ready)
        if not ready:self.update_selection({})
        if self.selection_enabled!=ready or self.message!=message:
            self.selection_enabled=ready;self.message=message;self.queue_draw()
    def update_selection(self, state):
        # Only observed input refreshes freshness; drawing never advances dwell.
        pointer=state.get('cursor');key=state.get('sample_key')
        try:
            pointer=tuple(float(v) for v in pointer)
            if len(pointer)!=2 or not all(math.isfinite(v) for v in pointer):pointer=None
        except (TypeError,ValueError):pointer=None
        if pointer is None:self.clear_pointer()
        elif key!=self.sample_key:
            now=time.monotonic()
            if self.target is None or (self.sample_received is not None and now-self.sample_received>.24) or math.hypot(pointer[0]-self.target[0],pointer[1]-self.target[1])>.45:
                self.trail.clear();self.trail_anchor=None;self.pointer=pointer
            self.target=pointer;self.sample_key=key;self.sample_received=now
        hover=state.get('hover') if self.target is not None else None
        progress=max(0.,min(1.,float(state.get('progress',0.)))) if self.target is not None else 0.
        self.selection_dirty|=hover!=self.hover or progress!=self.progress
        self.hover=hover;self.progress=progress
    def pointer_bounds(self):
        points=list(self.trail)
        if self.pointer is not None:points.append((*self.pointer,0))
        if not points:return []
        w=self.get_allocated_width();h=self.get_allocated_height();pad=20*min(w/1024,h/550)+3
        xs=[p[0]*w for p in points];ys=[p[1]*h for p in points]
        return [(min(xs)-pad,min(ys)-pad,max(xs)-min(xs)+2*pad,max(ys)-min(ys)+2*pad)]
    def invalidate(self,rect):
        x,y,w,h=rect;self.queue_draw_area(math.floor(x),math.floor(y),max(1,math.ceil(w)+2),max(1,math.ceil(h)+2))
    def animate(self,now=None):
        if not self.get_mapped():return True
        now=time.monotonic() if now is None else now
        dt=.03 if self.last_tick is None else min(.08,max(0.,now-self.last_tick));self.last_tick=now
        if self.sample_received is not None and now-self.sample_received>.24:
            self.clear_pointer()
            self.selection_dirty|=self.hover is not None or self.progress!=0.
            self.hover=None;self.progress=0.
        if self.target is not None:
            alpha=1-math.exp(-dt/.035)
            x,y=self.pointer;tx,ty=self.target
            self.pointer=self.target if math.hypot(tx-x,ty-y)<.0002 else (x+(tx-x)*alpha,y+(ty-y)*alpha)
            if self.trail_anchor is None or math.hypot(self.pointer[0]-self.trail_anchor[0],self.pointer[1]-self.trail_anchor[1])>.0008:
                self.trail.append((*self.pointer,now));self.trail_anchor=self.pointer
        while self.trail and now-self.trail[0][2]>.34:self.trail.popleft()
        bounds=self.pointer_bounds()
        signature=(self.pointer,len(self.trail))
        if self.trail or signature!=self.visual_signature or self.selection_dirty:
            for rect in self.dirty_previous+bounds:self.invalidate(rect)
        self.dirty_previous=bounds
        self.visual_signature=signature
        if self.selection_dirty:
            w=self.get_allocated_width();h=self.get_allocated_height()
            for i in set((self.previous_hover,self.hover)):
                if i not in range(len(self.rectangles)):continue
                a,b,c,d=self.rectangles[i]
                if self.previous_hover!=self.hover or self.radial and i<=len(MODES):self.invalidate((a*w-8,b*h-8,(c-a)*w+16,(d-b)*h+16))
                else:self.invalidate((a*w,d*h-7,(c-a)*w,9))
            self.previous_hover=self.hover;self.previous_progress=self.progress;self.selection_dirty=False
        return True
    def click(self, _, event):
        if event.button!=1:return False
        x=event.x/max(1,self.get_allocated_width());y=event.y/max(1,self.get_allocated_height())
        i=self.hit_index(x,y)
        if i is not None and (self.selection_enabled or self.visitor_controls and i>len(MODES)):
            self.choose(self.mode_choices[i]);return True
        return False
    @staticmethod
    def motif(cr, i, x, y, w, h):
        cr.save();cr.translate(x,y);cr.scale(w,h)
        if i==0:
            for k,alpha in ((0,.08),(1,.13),(2,.19)):
                cr.set_source_rgba(.17,.28,.27,alpha);cr.move_to(0,1)
                cr.line_to(.24,.48+k*.12);cr.line_to(.43,.68);cr.line_to(.68,.12+k*.15);cr.line_to(1,.83);cr.line_to(1,1);cr.close_path();cr.fill()
        elif i==1:
            cr.set_source_rgba(.16,.44,.40,.15);cr.arc(.70,.31,.24,0,math.tau);cr.fill()
            for k in range(2):
                cr.set_source_rgba(.20,.42,.38,.12+k*.07);cr.move_to(0,1)
                cr.line_to(.21,.62-k*.15);cr.line_to(.43,.83);cr.line_to(.77,.40-k*.18);cr.line_to(1,1);cr.close_path();cr.fill()
            cr.set_source_rgba(.12,.31,.30,.72);cr.move_to(.24,.74);cr.line_to(.75,.23);cr.line_to(.68,.49);cr.close_path();cr.fill()
            cr.set_source_rgba(.75,.48,.19,.85);cr.set_line_width(.035)
            cr.move_to(.20,.66);cr.line_to(.36,.82);cr.move_to(.28,.74);cr.line_to(.16,.87);cr.stroke()
            cr.set_source_rgba(.39,.61,.55,.45);cr.set_line_width(.012)
            cr.move_to(.07,.94);cr.curve_to(.17,.63,.38,.67,.63,.31);cr.stroke()
        elif i==2:
            cr.set_source_rgba(.56,.18,.14,.13);cr.arc(.68,.43,.36,0,math.tau);cr.fill()
            cr.set_source_rgba(.18,.25,.25,.7);cr.set_line_width(.07)
            cr.move_to(.20,.77);cr.line_to(.78,.20);cr.move_to(.29,.23);cr.line_to(.81,.81);cr.stroke()
            cr.set_line_width(.025);cr.move_to(.13,.76);cr.line_to(.31,.94);cr.move_to(.71,.85);cr.line_to(.87,.69);cr.stroke()
        elif i==3:
            for x0,y0,r,col in ((.39,.62,.24,(.47,.65,.43)),(.67,.42,.19,(.81,.46,.30)),(.82,.74,.13,(.88,.70,.33))):
                cr.set_source_rgba(*col,.40);cr.arc(x0,y0,r,0,math.tau);cr.fill()
            cr.set_source_rgba(.13,.36,.29,.6);cr.set_line_width(.025);cr.move_to(.13,.85);cr.curve_to(.36,.40,.60,.31,.94,.08);cr.stroke()
        elif i==4:
            cr.set_source_rgba(.55,.17,.14,.65);cr.rectangle(.17,.40,.65,.35);cr.fill()
            cr.save();cr.translate(.495,.41);cr.scale(1,.45)
            cr.set_source_rgba(.91,.74,.42,.9);cr.arc(0,0,.33,0,math.tau);cr.fill();cr.restore()
            cr.set_source_rgba(.24,.27,.24,.8);cr.set_line_width(.045)
            cr.move_to(.13,.10);cr.line_to(.48,.40);cr.move_to(.90,.08);cr.line_to(.56,.41);cr.stroke()
        elif i==5:
            # Pixel adventurer and terrace, matching the new native experience.
            cr.set_antialias(cairo.ANTIALIAS_NONE)
            cr.set_source_rgb(.31,.51,.43)
            for xx,yy,ww,hh in ((.10,.80,.20,.13),(.36,.67,.23,.26),(.64,.52,.24,.41)):
                cr.rectangle(xx,yy,ww,hh);cr.fill()
            cr.set_source_rgb(.16,.29,.28);cr.rectangle(.43,.18,.18,.15);cr.fill()
            cr.set_source_rgb(.88,.72,.48);cr.rectangle(.45,.31,.14,.12);cr.fill()
            cr.set_source_rgb(.19,.51,.48);cr.rectangle(.42,.42,.18,.21);cr.fill()
            cr.set_source_rgb(.85,.55,.25);cr.rectangle(.36,.59,.12,.07);cr.fill();cr.rectangle(.56,.60,.14,.07);cr.fill()
            cr.set_source_rgb(.90,.74,.35);cr.rectangle(.71,.20,.08,.12);cr.fill()
        elif i==6:
            # Running traveller above converging rails: original seventh icon.
            cr.set_source_rgba(.18,.43,.46,.16)
            cr.arc(.66,.34,.30,0,math.tau);cr.fill()
            cr.set_source_rgba(.16,.36,.38,.55);cr.set_line_width(.028)
            for bottom in (.13,.50,.87):
                cr.move_to(.50,.16);cr.line_to(bottom,.96)
            cr.stroke()
            cr.set_source_rgb(.77,.39,.23);cr.arc(.54,.25,.085,0,math.tau);cr.fill()
            cr.set_line_width(.072);cr.set_line_cap(cairo.LINE_CAP_ROUND)
            for a,b in (((.51,.37),(.43,.58)),((.48,.44),(.30,.50)),((.48,.44),(.66,.45)),
                        ((.43,.58),(.26,.78)),((.43,.58),(.61,.68)),((.61,.68),(.69,.84))):
                cr.move_to(*a);cr.line_to(*b)
            cr.stroke()
            cr.set_source_rgb(.88,.68,.28);cr.set_line_width(.025)
            cr.arc(.80,.66,.065,0,math.tau);cr.stroke()
        elif i==7:
            # A sword and folded scroll distinguish the classic RPG entry.
            cr.set_source_rgba(.39,.57,.47,.2);cr.arc(.50,.50,.40,0,math.tau);cr.fill()
            cr.set_source_rgb(.85,.76,.53);cr.rectangle(.20,.24,.60,.54);cr.fill()
            cr.set_source_rgb(.34,.40,.32);cr.set_line_width(.035)
            for xx in (.20,.80):
                cr.move_to(xx,.20);cr.line_to(xx,.81);cr.stroke()
            cr.set_source_rgb(.19,.34,.37);cr.set_line_width(.045)
            cr.move_to(.30,.73);cr.line_to(.73,.20);cr.stroke()
            cr.set_source_rgb(.69,.31,.21);cr.set_line_width(.055)
            cr.move_to(.24,.61);cr.line_to(.43,.77);cr.stroke()
        else:
            # A curved racing line, four wheels and a compact open kart.
            cr.set_source_rgba(.28,.48,.40,.13);cr.arc(.50,.47,.40,0,math.tau);cr.fill()
            cr.set_source_rgba(.66,.49,.25,.5);cr.set_line_width(.025)
            cr.move_to(.12,.96);cr.curve_to(.80,.64,.08,.42,.82,.12);cr.stroke()
            cr.set_source_rgb(.13,.23,.24)
            for xx,yy in ((.20,.39),(.68,.39),(.18,.70),(.70,.70)):
                cr.rectangle(xx,yy,.13,.16);cr.fill()
            cr.set_source_rgb(.20,.50,.46);cr.move_to(.35,.27);cr.line_to(.65,.27);cr.line_to(.76,.78);cr.line_to(.24,.78);cr.close_path();cr.fill()
            cr.set_source_rgb(.88,.65,.27);cr.rectangle(.46,.29,.08,.45);cr.fill()
            cr.set_source_rgb(.17,.27,.28);cr.arc(.50,.50,.12,0,math.tau);cr.fill()
            cr.set_source_rgb(.89,.73,.52);cr.arc(.50,.44,.08,0,math.tau);cr.fill()
        cr.restore()
    def build_static(self,w,h):
        self.static_surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.static_surface);s=min(w/960,h/540)
        cr.set_source_rgb(.965,.952,.920);cr.paint()
        cr.set_source_rgba(.37,.35,.28,.035);cr.set_line_width(1)
        for yy in range(0,h,9):cr.move_to(0,yy);cr.line_to(w,yy);cr.stroke()
        text(cr,'墨境 · 主菜单',w*.045,h*.035,22*s,bold=True,width=w*.36)
        text(cr,'九种体验 · 体感 / 手柄',w*.047,h*.102,10*s,(.42,.46,.43))
        text(cr,'INK REALM',w*.82,h*.05,11*s,(.49,.29,.24),bold=True,width=w*.16)
        (cx,cy,r),circles=geometry(w,h)
        orbit=math.hypot(circles[0][0]-cx,circles[0][1]-cy)
        cr.set_source_rgba(.25,.42,.36,.13);cr.set_line_width(1*s);cr.set_dash((3*s,7*s))
        cr.arc(cx,cy,orbit,0,math.tau);cr.stroke();cr.set_dash(())
        cr.set_source_rgba(.15,.26,.21,.09);cr.arc(cx+2*s,cy+4*s,r+2*s,0,math.tau);cr.fill()
        cr.set_source_rgb(.991,.982,.95);cr.arc(cx,cy,r,0,math.tau);cr.fill()
        cr.set_source_rgb(.36,.46,.37);cr.set_line_width(1.5*s);cr.arc(cx,cy,r,0,math.tau);cr.stroke()
        # A static icon is an entry marker, not a fabricated inference result.
        cr.set_source_rgb(.19,.38,.31);cr.set_line_width(3*s);cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.arc(cx,cy-21*s,7*s,0,math.tau);cr.stroke()
        for a,b in (((0,-12),(0,13)),((-18,-5),(18,-5)),((-18,-5),(-26,9)),((18,-5),(26,9)),((0,13),(-16,30)),((0,13),(16,30))):
            cr.move_to(cx+a[0]*s,cy+a[1]*s);cr.line_to(cx+b[0]*s,cy+b[1]*s);cr.stroke()
        text(cr,'摄像头 · 骨架',cx-60*s,cy+r+6*s,13*s,bold=True,width=120*s,centered=True)
        for i,(x,y,ir) in enumerate(circles):
            cr.set_source_rgba(.15,.26,.21,.09);cr.arc(x+2*s,y+4*s,ir+2*s,0,math.tau);cr.fill()
            cr.set_source_rgb(.991,.982,.95);cr.arc(x,y,ir,0,math.tau);cr.fill()
            cr.set_source_rgb(.36,.46,.37);cr.set_line_width(1.5*s);cr.arc(x,y,ir,0,math.tau);cr.stroke()
            cr.save();cr.arc(x,y,ir-4*s,0,math.tau);cr.clip()
            self.motif(cr,i,x-ir*.76,y-ir*.86,ir*1.52,ir*1.20);cr.restore()
            # Keep all eight names inside their own circle, so labels cannot
            # be covered by the next entry around the orbit.
            text(cr,TITLES[i],x-ir*.87,y+ir*.38,12.5*s,bold=True,width=ir*1.74,centered=True)
        if self.visitor_controls:
            text(cr,self.message,w*.047,h*.143,9*s,(.39,.44,.42),width=w*.25)
            for i,(a,b,c,d) in enumerate(VISITOR_RECTS):
                x,y,cw,ch=a*w,b*h,(c-a)*w,(d-b)*h;rr=12*s
                cr.set_source_rgba(.99,.98,.94,.95)
                cr.new_sub_path();cr.arc(x+cw-rr,y+rr,rr,-math.pi/2,0);cr.arc(x+cw-rr,y+ch-rr,rr,0,math.pi/2)
                cr.arc(x+rr,y+ch-rr,rr,math.pi/2,math.pi);cr.arc(x+rr,y+rr,rr,math.pi,math.pi*1.5);cr.close_path();cr.fill_preserve()
                cr.set_source_rgba(.25,.41,.34,.35);cr.set_line_width(1*s);cr.stroke()
                mx=x+cw/2;my=y+ch*.26;cr.set_source_rgb(.25,.41,.34);cr.set_line_width(2.5*s)
                if i==0:
                    for qx,qy in ((-16,-13),(4,-13),(-16,7)):
                        cr.rectangle(mx+qx*s,my+qy*s,12*s,12*s);cr.stroke()
                    cr.rectangle(mx+6*s,my+9*s,5*s,5*s);cr.fill();cr.rectangle(mx+14*s,my+15*s,4*s,4*s);cr.fill()
                else:
                    cr.rectangle(mx-17*s,my-15*s,25*s,32*s);cr.stroke()
                    cr.move_to(mx-1*s,my+1*s);cr.line_to(mx+21*s,my+1*s);cr.move_to(mx+13*s,my-7*s);cr.line_to(mx+21*s,my+1*s);cr.line_to(mx+13*s,my+9*s);cr.stroke()
                label=('扫码下载','结束游玩')[i];subtitle=('画卷 / 成绩','归档本次游玩')[i]
                text(cr,label,mx-42.5*s,y+ch*.47,15*s,bold=True,width=85*s,centered=True)
                text(cr,subtitle,mx-49*s,y+ch*.72,9*s,(.43,.47,.41),width=98*s,centered=True)
        else:text(cr,self.message,w*.06,h*.965,10*s,(.39,.44,.42),width=w*.89)
        self.cache_key=(w,h,self.message);self.cache_builds+=1
    def draw(self, _, target):
        # Blend the brush into an image surface, as the fruit game does. Cairo
        # strokes and translucent layers sent straight to the X11 drawable can
        # require many slow server-side operations on the board. One opaque
        # blit also keeps the display path independent of the number of points.
        ow=self.get_allocated_width();oh=self.get_allocated_height()
        if ow<1 or oh<1:return False
        if not hasattr(self,'render_policy'):self.render_policy=RenderPolicy.from_environment()
        layout=self.render_policy.layout(ow,oh);w,h=layout.render_size
        if self.backbuffer is None or (self.backbuffer.get_width(),self.backbuffer.get_height())!=(w,h):
            self.backbuffer=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.backbuffer)
        # Always compose the complete bounded frame. Fractional card edges and
        # scaled translucent brush strokes must have the same coverage for full
        # and disjoint GTK damage. The static cache keeps this inexpensive; the
        # sole opaque presentation still respects GTK's target dirty clip.
        self.paint(cr,(w,h))
        present(target,self.backbuffer,layout)
        return False
    def paint(self, cr, size=None):
        w,h=size or (self.get_allocated_width(),self.get_allocated_height());s=min(w/960,h/540)
        if w<1 or h<1:return False
        if self.cache_key!=(w,h,self.message):self.build_static(w,h)
        cr.set_source_surface(self.static_surface,0,0);cr.paint()
        if self.hover in range(len(self.rectangles)):
            if self.radial and self.hover<=len(MODES):
                x,y,r=geometry(w,h)[0] if self.hover==len(MODES) else geometry(w,h)[1][self.hover]
                cr.set_source_rgba(.09,.47,.39,.10);cr.arc(x,y,r,0,math.tau);cr.fill()
                cr.set_source_rgb(.08,.46,.38);cr.set_line_width(3*s);cr.arc(x,y,r+4*s,0,math.tau);cr.stroke()
                cr.set_source_rgb(.70,.25,.14);cr.set_line_width(4*s)
                cr.arc(x,y,r+4*s,-math.pi/2,-math.pi/2+math.tau*self.progress);cr.stroke()
            else:
                a,b,c,d=self.rectangles[self.hover];x,y,cw,ch=a*w,b*h,(c-a)*w,(d-b)*h
                cr.set_source_rgba(.09,.47,.39,.075);cr.rectangle(x,y,cw,ch);cr.fill()
                cr.set_source_rgb(.08,.46,.38);cr.set_line_width(3);cr.rectangle(x+2,y+2,cw-4,ch-4);cr.stroke()
                cr.rectangle(x,y+ch-5,cw*self.progress,5);cr.fill()
        now=time.monotonic();p=[(x*w,y*h,t) for x,y,t in self.trail]
        cr.set_line_cap(cairo.LINE_CAP_ROUND);cr.set_line_join(cairo.LINE_JOIN_ROUND)
        # Fruit-slicing brush: midpoint curves, dark wet edge, warm thin core.
        for i in range(1,len(p)):
            a,b=p[i-1],p[i]
            start=((p[i-2][0]+a[0])/2,(p[i-2][1]+a[1])/2) if i>1 else a[:2]
            end=((a[0]+b[0])/2,(a[1]+b[1])/2) if i<len(p)-1 else b[:2]
            freshness=max(0.,1-(now-b[2])/.34);width=(2+10*i/(len(p)-1))*freshness*s
            if width<.2:continue
            for extra,color in ((2*s,(.08,.23,.21,.90)),(-.75*width,(1.,.93,.65,.86))):
                cr.set_source_rgba(*color[:3],color[3]*freshness);cr.set_line_width(max(.5*s,width+extra))
                cr.move_to(*start);cr.curve_to(start[0]+2*(a[0]-start[0])/3,start[1]+2*(a[1]-start[1])/3,end[0]+2*(a[0]-end[0])/3,end[1]+2*(a[1]-end[1])/3,*end);cr.stroke()
        if self.pointer is not None:
            try:cx,cy=self.pointer;cx*=w;cy*=h
            except (TypeError,ValueError):return False
            cr.set_source_rgba(.08,.23,.21,.20);cr.arc(cx,cy,11*s,0,math.tau);cr.fill()
            cr.set_source_rgba(.08,.23,.21,.94);cr.arc(cx,cy,6*s,0,math.tau);cr.fill()
            cr.set_source_rgb(1.,.93,.65);cr.arc(cx,cy,2.4*s,0,math.tau);cr.fill()
            if self.progress>0:
                cr.set_source_rgb(.66,.20,.14);cr.set_line_width(3*s);cr.arc(cx,cy,14*s,-math.pi/2,-math.pi/2+math.tau*self.progress);cr.stroke()
        return False
