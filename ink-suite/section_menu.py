"""Cached native three-section menu using the proven bounded ink cursor.

Only this view and its pure catalog are new. The original experience menu is
left intact so existing callers and regression checks remain available.
"""
import math
import time
from pathlib import Path
import cairo
from menu_view import MenuView,text
import section_catalog as catalog


def rounded(cr,x,y,width,height,radius):
    radius=min(radius,width/2,height/2)
    cr.new_sub_path()
    for cx,cy,a,b in ((x+width-radius,y+radius,-math.pi/2,0),
                      (x+width-radius,y+height-radius,0,math.pi/2),
                      (x+radius,y+height-radius,math.pi/2,math.pi),
                      (x+radius,y+radius,math.pi,math.pi*1.5)):
        cr.arc(cx,cy,radius,a,b)
    cr.close_path()


class SectionMenuView(MenuView):
    radial=False
    def __init__(self,choose,visitor_controls=False):
        self.section='home'
        super().__init__(choose,visitor_controls)
        self.mode_choices=catalog.choices('home',visitor_controls)
        self.set_size_request(640,420)

    @property
    def rectangles(self):return catalog.rectangles(self.section,self.visitor_controls)

    def hit_index(self,x,y):return catalog.hit_index(x,y,self.section,self.visitor_controls)

    def set_section(self,section='home'):
        catalog.choices(section,self.visitor_controls)  # Reject invalid input before mutation.
        changed=section!=self.section
        self.section=section;self.mode_choices=catalog.choices(section,self.visitor_controls)
        self.clear_pointer();self.sample_key=None;self.hover=None;self.progress=0.
        self.previous_hover=None;self.previous_progress=0.;self.selection_dirty=True
        self.dirty_previous=[];self.visual_signature=None;self.cache_key=None
        self.static_surface=None;self.queue_draw()
        return changed

    def click(self,_,event):
        if event.button!=1:return False
        index=self.hit_index(event.x/max(1,self.get_allocated_width()),event.y/max(1,self.get_allocated_height()))
        if index is not None:
            choice=self.mode_choices[index]
            if self.selection_enabled or catalog.available_without_pose(choice):
                self.choose(choice);return True
        return False

    def set_selection(self,state):self.update_selection(state)

    def animate(self,now=None):
        # Progress strips scale with the bounded framebuffer. Invalidate the
        # whole selected card before delegating to the original cursor timer,
        # rather than relying on its fixed-size radial-menu progress damage.
        if not self.get_mapped():return True
        if self.selection_dirty:
            w=self.get_allocated_width();h=self.get_allocated_height()
            for index in set((self.previous_hover,self.hover)):
                if index in range(len(self.rectangles)):
                    a,b,c,d=self.rectangles[index]
                    self.invalidate((a*w-4,b*h-4,(c-a)*w+8,(d-b)*h+8))
        return super().animate(now)

    def artwork(self,cr,name,x,y,w,h,anchor=.5,opacity=1.):
        # Decode each bundled painting once; composite only when static cache changes.
        if not hasattr(self,'_paintings'):self._paintings={}
        if name not in self._paintings:
            path=Path(__file__).resolve().parent/'avatar'/'assets'/(name+'.png')
            try:self._paintings[name]=cairo.ImageSurface.create_from_png(str(path))
            except (OSError,cairo.Error):self._paintings[name]=None
        image=self._paintings[name]
        if image is None:return
        if not hasattr(self,'_scaled_paintings'):self._scaled_paintings={}
        key=(name,w,h,anchor,opacity)
        target=self._scaled_paintings.get(key)
        if target is None:
            # Keep expensive high-quality resampling out of status-only rebuilds.
            # Transparent edges preserve the fractional card clipping geometry.
            target=cairo.ImageSurface(cairo.FORMAT_ARGB32,max(1,math.ceil(w)),max(1,math.ceil(h)))
            cached=cairo.Context(target)
            iw,ih=image.get_width(),image.get_height();scale=max(w/iw,h/ih)
            cached.rectangle(0,0,w,h);cached.clip()
            cached.translate(-(iw*scale-w)*anchor,-(ih*scale-h)*.48);cached.scale(scale,scale)
            cached.set_source_surface(image);cached.get_source().set_filter(cairo.FILTER_BEST)
            cached.paint_with_alpha(opacity)
            # There are seven distinct painting crops across all menu sections.
            # The cap also bounds use if a future caller adds arbitrary crops.
            if len(self._scaled_paintings)>=16:self._scaled_paintings.clear()
            self._scaled_paintings[key]=target
        cr.save();cr.rectangle(x,y,w,h);cr.clip()
        cr.set_source_surface(target,x,y);cr.paint();cr.restore()

    @staticmethod
    def arrow(cr,x,y,s,color=(.42,.48,.43)):
        cr.set_source_rgb(*color);cr.set_line_width(1.25*s)
        cr.move_to(x-12*s,y);cr.line_to(x,y);cr.line_to(x-4*s,y-4*s)
        cr.move_to(x,y);cr.line_to(x-4*s,y+4*s);cr.stroke()

    def header_brands(self,cr,w,h):
        """Original marks, competition first. Decode once; only paint in cache.

        The supplied competition PNG has a transparent trailing margin. Its
        content viewport avoids counting that padding as part of the logo.
        The bundled PNG is byte-for-byte the user's source image.
        """
        if not hasattr(self,'_brand_images'):self._brand_images={}
        x,y,area_w,area_h=.600*w,.049*h,.345*w,.112*h
        competition_w=area_w*.46
        amd_x=x+area_w*.52
        amd_w=area_w*.48
        amd_h=min(area_h*.91,amd_w*.35)
        cy=y+area_h*.5
        def mark(name,viewport,left,top,width,height):
            if name not in self._brand_images:
                path=Path(__file__).resolve().parent/'assets'/'branding'/name
                try:self._brand_images[name]=cairo.ImageSurface.create_from_png(str(path))
                except (OSError,cairo.Error):self._brand_images[name]=None
            image=self._brand_images[name]
            if image is None:return
            vx,vy,vw,vh=viewport or (0,0,image.get_width(),image.get_height())
            scale=min(width/vw,height/vh)
            cr.save();cr.rectangle(left,top,width,height);cr.clip()
            cr.translate(left+(width-vw*scale)/2,top+(height-vh*scale)/2)
            cr.scale(scale,scale);cr.set_source_surface(image,-vx,-vy)
            cr.get_source().set_filter(cairo.FILTER_BEST);cr.paint();cr.restore()
        # Temporary showcase mark; original competition/AMD assets stay intact.
        temporary='temporary-showcase-competition.png'
        if (Path(__file__).resolve().parent/'assets'/'branding'/temporary).is_file():
            mark(temporary,None,x,y,area_w,area_h)
            if self._brand_images.get(temporary) is not None:return
        mark('fpga-competition-10th.png',(0,0,229,80),x,y,competition_w,area_h)
        # AMD's official reversed white mark retains its original shape/color.
        # A neutral dark plate keeps it legible against the paper background.
        rounded(cr,amd_x,cy-amd_h/2,amd_w,amd_h,2*min(w/960,h/540))
        cr.set_source_rgb(.055,.060,.065);cr.fill()
        mark('amd-logo-white.png',None,amd_x+amd_w*.04,cy-amd_h*.38,
             amd_w*.92,amd_h*.76)

    @staticmethod
    def deepseek_mark(cr,x,y,w,h):
        """Official whale asset, decoded once; called only for cached chrome."""
        if not hasattr(SectionMenuView,'_deepseek_image'):
            path=Path(__file__).resolve().parent/'assets'/'branding'/'deepseek-whale.png'
            try:SectionMenuView._deepseek_image=cairo.ImageSurface.create_from_png(str(path))
            except (OSError,cairo.Error):SectionMenuView._deepseek_image=None
        image=SectionMenuView._deepseek_image
        if image is None:return False
        scale=min(w/image.get_width(),h/image.get_height())
        cr.save();cr.translate(x+(w-image.get_width()*scale)/2,y+(h-image.get_height()*scale)/2)
        cr.scale(scale,scale);cr.set_source_surface(image)
        cr.get_source().set_filter(cairo.FILTER_BEST);cr.paint();cr.restore()
        return True

    @staticmethod
    def ai_entry(cr,x,y,w,h,s):
        """Static, bounded technology accent; rendered only in the menu cache."""
        cr.save()
        rounded(cr,x,y,w,h,8*s);cr.clip()
        gradient=cairo.LinearGradient(x,y,x+w,y+h)
        gradient.add_color_stop_rgb(0,.045,.105,.175)
        gradient.add_color_stop_rgb(.62,.065,.17,.245)
        gradient.add_color_stop_rgb(1,.09,.22,.29)
        cr.set_source(gradient);cr.paint()
        # A quiet inset rail carries the blue accent without a glow/filter pass.
        cr.set_source_rgba(.34,.82,.88,.22);cr.set_line_width(.8*s)
        cr.move_to(x+57*s,y+8*s);cr.line_to(x+w-13*s,y+8*s);cr.stroke()
        cx,cy=x+29*s,y+h*.5
        cr.set_source_rgba(.28,.72,.80,.12)
        rounded(cr,cx-18*s,cy-18*s,36*s,36*s,8*s);cr.fill()
        SectionMenuView.deepseek_mark(cr,cx-18*s,cy-14*s,36*s,28*s)
        # Normal UI text stays sharp at the native bounded framebuffer size.
        text(cr,'AI 助手',x+57*s,y+7*s,17*s,(.94,.98,.99),bold=False,width=w-69*s,centered=True)
        text(cr,'DeepSeek',x+57*s,y+32*s,9*s,(.48,.80,.85),width=w-69*s,centered=True)
        cr.restore()
        rounded(cr,x+.7*s,y+.7*s,w-1.4*s,h-1.4*s,7.5*s)
        cr.set_source_rgba(.34,.72,.79,.8);cr.set_line_width(1.1*s);cr.stroke()

    def build_static(self,w,h):
        if getattr(self,'_artwork_size',None)!=(w,h):
            self._scaled_paintings={};self._artwork_size=(w,h)
        self.static_surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.static_surface);s=min(w/960,h/540)
        cr.set_source_rgb(.973,.966,.946);cr.paint()
        header_width=w*(.285 if self.section=='home' else .435)
        text(cr,catalog.HEADINGS[self.section],w*.055,h*.037,23*s,bold=True,width=header_width)
        text(cr,catalog.SUBHEADINGS[self.section],w*.057,h*.120,10*s,(.42,.47,.43),width=header_width)
        # The home header's unused right slot holds both marks. Submenus keep
        # their full-size back button in the same position and hit geometry.
        if self.section=='home':
            self.header_brands(cr,w,h)
        cr.set_source_rgba(.30,.39,.34,.17);cr.set_line_width(.75*s)
        cr.move_to(w*.055,h*.207);cr.line_to(w*.945,h*.207);cr.stroke()
        count=len(catalog.CONTENT[self.section])
        for index,choice in enumerate(self.mode_choices):
            a,b,c,d=self.rectangles[index];x,y,cw,ch=a*w,b*h,(c-a)*w,(d-b)*h
            if choice=='section_home':
                rounded(cr,x,y,cw,ch,8*s);cr.set_source_rgb(.989,.983,.965);cr.fill_preserve()
                cr.set_source_rgb(.53,.63,.56);cr.set_line_width(1.4*s);cr.stroke()
                cr.set_source_rgb(.25,.39,.32);cr.set_line_width(2*s)
                cr.move_to(x+30*s,y+ch*.5);cr.line_to(x+16*s,y+ch*.5)
                cr.move_to(x+22*s,y+ch*.5-6*s);cr.line_to(x+16*s,y+ch*.5);cr.line_to(x+22*s,y+ch*.5+6*s);cr.stroke()
                text(cr,catalog.TITLES[choice],x+37*s,y+ch*.5-15*s,16*s,(.25,.39,.32),bold=True,width=cw-49*s,centered=True)
                continue
            if choice=='ai_assistant':
                self.ai_entry(cr,x,y,cw,ch,s)
                continue
            if index>=count:
                rounded(cr,x,y,cw,ch,10*s);cr.set_source_rgb(.963,.974,.946);cr.fill_preserve()
                cr.set_source_rgba(.22,.39,.33,.30);cr.set_line_width(1.1*s);cr.stroke()
                rounded(cr,x+12*s,y+ch*.5-26*s,52*s,52*s,9*s)
                cr.set_source_rgb(.994,.992,.976);cr.fill()
                self.utility_icon(cr,choice,x+38*s,y+ch*.5,20*s)
                text(cr,catalog.TITLES[choice],x+76*s,y+5*s,20*s,bold=True,width=cw-88*s)
                text(cr,catalog.DESCRIPTIONS[choice],x+77*s,y+45*s,12.5*s,(.36,.44,.37),width=cw-89*s)
                continue
            cr.set_source_rgba(.14,.24,.19,.045);rounded(cr,x,y+3*s,cw,ch,10*s);cr.fill()
            rounded(cr,x,y,cw,ch,10*s);cr.set_source_rgb(.996,.992,.978);cr.fill_preserve()
            cr.set_source_rgba(.23,.40,.33,.18);cr.set_line_width(.8*s);cr.stroke()
            cr.save();rounded(cr,x+1*s,y+1*s,cw-2*s,ch-2*s,9*s);cr.clip()
            if self.section=='home':
                name=('mountains','bamboo','plum')[index]
                self.artwork(cr,name,x,y,cw,ch*.61,anchor=(.08,.12,.85)[index])
                gradient=cairo.LinearGradient(0,y+ch*.42,0,y+ch*.63)
                gradient.add_color_stop_rgba(0,.996,.992,.978,0)
                gradient.add_color_stop_rgba(1,.996,.992,.978,1)
                cr.set_source(gradient);cr.rectangle(x,y+ch*.42,cw,ch*.22);cr.fill()
                text(cr,catalog.TITLES[choice],x+22*s,y+ch*.66,27*s,bold=True,width=cw-44*s)
                text(cr,catalog.DESCRIPTIONS[choice],x+23*s,y+ch*.83,10*s,(.40,.46,.41),width=cw-48*s)
                self.arrow(cr,x+cw-23*s,y+ch*.76,s)
            elif self.section=='entertainment':
                # Restrained line illustrations on a clear ground, never pixel sprites.
                self.game_mark(cr,choice,x+cw*.5,y+ch*.34,min(cw*.29,ch*.29))
                text(cr,catalog.TITLES[choice],x+8*s,y+ch*.70,14*s,bold=True,width=cw-16*s,centered=True)
            else:
                names={'baduanjin':'bamboo','taichi':'mountains','ink':'plum','photo_ink':'mountains'}
                self.artwork(cr,names[choice],x,y,cw,ch*.47,anchor=.25 if index==0 else .78)
                gradient=cairo.LinearGradient(0,y+ch*.30,0,y+ch*.49)
                gradient.add_color_stop_rgba(0,.996,.992,.978,0)
                gradient.add_color_stop_rgba(1,.996,.992,.978,1)
                cr.set_source(gradient);cr.rectangle(x,y+ch*.30,cw,ch*.20);cr.fill()
                text(cr,catalog.TITLES[choice],x+25*s,y+ch*.50,23*s,bold=True,width=cw-50*s)
                lines={'baduanjin':('分解学习 · 完整跟练 · 自主练习','先看示范，再看骨架与动作建议'),
                       'taichi':('二十四式 · 示范与基础跟练','慢慢练习，按自己的舒适幅度完成'),
                       'ink':('动作控制笔墨，逐笔完成画卷','保存作品，扫码下载'),
                       'photo_ink':('手机扫码上传 / 板卡或 U盘选图','青绿淡彩与水墨两种画风')}[choice]
                for row,line in enumerate(lines):
                    text(cr,line,x+26*s,y+ch*(.72+.11*row),10*s,(.39,.46,.40),width=cw-52*s)
                self.arrow(cr,x+cw-26*s,y+ch*.60,s)
            cr.restore()
        text(cr,self.message,w*.057,h*catalog.STATUS_TOP,9.5*s,(.43,.48,.43),width=w*.886)
        self.cache_key=(w,h,self.message,self.section);self.cache_builds+=1

    @staticmethod
    def game_mark(cr,choice,x,y,r):
        cr.save();cr.translate(x,y);cr.scale(r,r)
        cr.set_line_cap(cairo.LINE_CAP_ROUND);cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(.27,.43,.37,.055);cr.arc(0,0,1.06,0,math.tau);cr.fill()
        cr.set_source_rgb(.20,.35,.31);cr.set_line_width(.055)
        def path(points):
            cr.move_to(*points[0])
            for point in points[1:]:cr.line_to(*point)
            cr.stroke()
        if choice in ('sword','pal','fight'):
            path([(-.58,.65),(.55,-.62),(.43,-.27),(-.32,.48)])
            path([(-.63,.22),(-.19,.63)]);path([(-.45,.45),(-.70,.73)])
            if choice=='fight':path([(-.57,-.62),(.62,.59)]);path([(.33,.60),(.61,.31)])
            if choice=='pal':
                cr.set_source_rgba(.65,.48,.25,.65);cr.set_line_width(.035)
                path([(-.75,-.5),(-.75,.5),(.67,.5),(.67,-.5)])
                path([(-.8,-.5),(-.1,-.5)]);path([(.22,-.5),(.72,-.5)])
        elif choice=='fruit':
            cr.arc(-.25,.22,.45,0,math.tau);cr.stroke();cr.arc(.38,-.10,.35,0,math.tau);cr.stroke()
            path([(-.22,-.25),(-.32,-.55),(-.04,-.47)])
            cr.set_source_rgb(.64,.30,.21);path([(-.72,.7),(.75,-.68)])
        elif choice=='taiko':
            cr.save();cr.scale(1,.55);cr.arc(0,-.1,.69,0,math.tau);cr.stroke();cr.restore()
            path([(-.69,0),(-.69,.48)]);path([(.69,0),(.69,.48)])
            cr.save();cr.translate(0,.48);cr.scale(1,.35);cr.arc(0,0,.69,0,math.pi);cr.stroke();cr.restore()
            cr.set_source_rgb(.64,.30,.21);path([(-.65,-.72),(-.1,-.20)]);path([(.65,-.72),(.1,-.20)])
        elif choice=='platformer':
            path([(-.85,.68),(-.85,.4),(-.27,.4),(-.27,.12),(.30,.12),(.30,-.17),(.85,-.17)])
            cr.set_source_rgb(.64,.30,.21);cr.arc(-.36,-.55,.18,0,math.tau);cr.stroke()
            path([(-.36,-.37),(-.36,-.04),(-.57,.10)]);path([(-.36,-.15),(-.13,.04)])
        elif choice=='runner':
            cr.arc(.10,-.61,.15,0,math.tau);cr.stroke()
            path([(.06,-.42),(-.15,.02),(-.53,.47)]);path([(-.15,.02),(.29,.27),(.44,.65)])
            path([(-.04,-.22),(-.42,-.1),(-.55,-.33)]);path([(-.04,-.22),(.36,-.06),(.54,-.25)])
            cr.set_source_rgba(.65,.48,.25,.7);path([(-.78,.65),(-.30,.65)]);path([(-.8,.36),(-.58,.36)])
        else:
            cr.arc(0,0,.73,0,math.tau);cr.stroke();cr.arc(0,0,.19,0,math.tau);cr.stroke()
            path([(-.68,-.18),(-.18,0)]);path([(.68,-.18),(.18,0)]);path([(0,.19),(0,.72)])
            cr.set_source_rgb(.64,.30,.21);cr.arc(0,0,.84,-2.1,-1.0);cr.stroke()
        cr.restore()

    @staticmethod
    def utility_icon(cr,choice,x,y,r):
        cr.save();cr.translate(x,y);cr.set_source_rgb(.27,.43,.35);cr.set_line_width(max(1.5,r*.085))
        if choice=='pose_view':
            cr.arc(0,-r*.50,r*.22,0,math.tau);cr.stroke()
            for a,b in (((0,-r*.2),(0,r*.35)),((-r*.5,0),(r*.5,0)),((0,r*.35),(-r*.4,r*.8)),((0,r*.35),(r*.4,r*.8))):cr.move_to(*a);cr.line_to(*b)
            cr.stroke()
        elif choice=='visitor_download':
            for px,py in ((-r,-r),(r*.15,-r),(-r,r*.15)):
                cr.rectangle(px,py,r*.75,r*.75);cr.stroke()
            cr.rectangle(r*.25,r*.25,r*.40,r*.40);cr.fill()
        else:
            cr.rectangle(-r*.7,-r,r,2*r);cr.stroke()
            cr.move_to(0,0);cr.line_to(r,0);cr.move_to(r*.5,-r*.5);cr.line_to(r,0);cr.line_to(r*.5,r*.5);cr.stroke()
        cr.restore()

    def paint(self,cr,size=None):
        w,h=size or (self.get_allocated_width(),self.get_allocated_height());s=min(w/960,h/540)
        if w<1 or h<1:return False
        if self.cache_key!=(w,h,self.message,self.section):self.build_static(w,h)
        cr.set_source_surface(self.static_surface,0,0);cr.paint()
        if self.hover in range(len(self.rectangles)):
            a,b,c,d=self.rectangles[self.hover];x,y,cw,ch=a*w,b*h,(c-a)*w,(d-b)*h
            ai_focus=self.mode_choices[self.hover]=='ai_assistant'
            rounded(cr,x+1*s,y+1*s,cw-2*s,ch-2*s,(8 if ai_focus else 12)*s)
            cr.set_source_rgba(*((.28,.78,.88,.10) if ai_focus else (.10,.45,.35,.08)));cr.fill_preserve()
            cr.set_source_rgb(*((.37,.91,.96) if ai_focus else (.13,.43,.33)));cr.set_line_width(2.5*s);cr.stroke()
            cr.set_source_rgb(.67,.29,.19);cr.rectangle(x+8*s,y+ch-5*s,max(0,cw-16*s)*self.progress,3*s);cr.fill()
        self.paint_cursor(cr,w,h,s)
        return False

    def paint_cursor(self,cr,w,h,s):
        now=time.monotonic();points=[(x*w,y*h,t) for x,y,t in self.trail]
        cr.set_line_cap(cairo.LINE_CAP_ROUND);cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for index in range(1,len(points)):
            a,b=points[index-1],points[index]
            start=((points[index-2][0]+a[0])/2,(points[index-2][1]+a[1])/2) if index>1 else a[:2]
            end=((a[0]+b[0])/2,(a[1]+b[1])/2) if index<len(points)-1 else b[:2]
            freshness=max(0.,1-(now-b[2])/.34);width=(2+10*index/(len(points)-1))*freshness*s
            if width<.2:continue
            for extra,color in ((2*s,(.08,.23,.21,.90)),(-.75*width,(1.,.93,.65,.86))):
                cr.set_source_rgba(*color[:3],color[3]*freshness);cr.set_line_width(max(.5*s,width+extra))
                cr.move_to(*start);cr.curve_to(start[0]+2*(a[0]-start[0])/3,start[1]+2*(a[1]-start[1])/3,end[0]+2*(a[0]-end[0])/3,end[1]+2*(a[1]-end[1])/3,*end);cr.stroke()
        if self.pointer is not None:
            cx,cy=self.pointer;cx*=w;cy*=h
            cr.set_source_rgba(.08,.23,.21,.20);cr.arc(cx,cy,11*s,0,math.tau);cr.fill()
            cr.set_source_rgba(.08,.23,.21,.94);cr.arc(cx,cy,6*s,0,math.tau);cr.fill()
            cr.set_source_rgb(1.,.93,.65);cr.arc(cx,cy,2.4*s,0,math.tau);cr.fill()
            if self.progress>0:
                cr.set_source_rgb(.66,.20,.14);cr.set_line_width(3*s);cr.arc(cx,cy,14*s,-math.pi/2,-math.pi/2+math.tau*self.progress);cr.stroke()
