"""Cached 960x540 native Cairo scenery; rendering never alters the simulation."""
from collections import OrderedDict, deque
import math
import time
from .engine import RunnerGame
try:
    import cairo
except ImportError:
    import cairocffi as cairo

W,H=960,540
LANE_METRES=2.4
METRE_PIXELS=105./RunnerGame.JUMP_METRES
NEAR_Z=-2.
FAR_Z=76.
INK=(.10,.18,.22)
GOLD=(.96,.74,.34)
CREAM=(.98,.94,.82)

def color(cr,c):cr.set_source_rgb(*c)
def poly(cr,points,fill,stroke=None,width=1):
    cr.move_to(*points[0])
    for p in points[1:]:cr.line_to(*p)
    cr.close_path();color(cr,fill)
    if stroke:
        cr.fill_preserve();color(cr,stroke);cr.set_line_width(width);cr.stroke()
    else:cr.fill()
def rect(cr,x,y,w,h,c):color(cr,c);cr.rectangle(x,y,w,h);cr.fill()
def circle(cr,x,y,r,c):color(cr,c);cr.arc(x,y,r,0,math.tau);cr.fill()
def line(cr,points,c,width=2):
    color(cr,c);cr.set_line_width(width);cr.move_to(*points[0])
    for p in points[1:]:cr.line_to(*p)
    cr.stroke()
def roof(cr,x,y,w,h):
    poly(cr,[(x-w*.55,y),(x-w*.40,y+h*.18),(x-w*.32,y+h*.37),
             (x+w*.32,y+h*.37),(x+w*.40,y+h*.18),(x+w*.55,y)],(.12,.25,.28),GOLD,1)
    poly(cr,[(x-w*.51,y),(x,y-h*.40),(x+w*.51,y)],(.15,.31,.34),(.08,.17,.22),2)
    line(cr,[(x-w*.55,y),(x,y-h*.40),(x+w*.55,y)],(.85,.61,.35),2)

def make_sprite(kind,phase=0):
    s=cairo.ImageSurface(cairo.FORMAT_ARGB32,160,200);cr=cairo.Context(s)
    if kind=='cart':
        rect(cr,20,52,120,122,(.38,.20,.17));rect(cr,27,60,106,94,(.66,.29,.20))
        for x in (32,72,112):rect(cr,x,63,16,72,(.12,.28,.31))
        for y in (54,145,170):rect(cr,18,y,124,7,(.82,.55,.29))
        for x in (32,128):circle(cr,x,181,12,(.10,.15,.17));circle(cr,x,181,5,(.66,.56,.35))
        roof(cr,80,40,148,48);rect(cr,64,63,32,42,(.95,.81,.48))
        line(cr,[(72,75),(88,93),(72,105),(88,75)],(.37,.20,.16),3)
    elif kind=='barrier':
        rect(cr,19,145,12,45,(.35,.24,.19));rect(cr,129,145,12,45,(.35,.24,.19))
        rect(cr,9,145,142,29,(.72,.24,.15));rect(cr,9,145,142,5,GOLD)
        for x in (20,60,100):poly(cr,[(x,171),(x+18,150),(x+30,150),(x+12,171)],GOLD)
        line(cr,[(23,177),(134,177)],(.50,.33,.20),5)
    elif kind=='arch':
        for x in (12,131):rect(cr,x,34,17,156,(.59,.20,.18));rect(cr,x-5,180,28,12,(.39,.39,.30))
        rect(cr,10,45,140,24,(.65,.20,.18));roof(cr,80,26,156,35)
        for x in (42,67,92,117):
            rect(cr,x,68,3,28,GOLD);circle(cr,x+1,104,11,(.88,.31,.16));line(cr,[(x+1,115),(x+1,128)],GOLD,2)
    elif kind=='coin':
        circle(cr,80,132,24,(.98,.77,.25));circle(cr,80,132,19,(.88,.49,.12))
        circle(cr,80,132,15,(1.,.82,.35));rect(cr,75,126,10,12,(.50,.29,.13))
        line(cr,[(66,121),(71,116)],(1.,.96,.68),3)
    elif kind in ('shield','magnet'):
        if kind=='shield':
            poly(cr,[(80,94),(108,107),(103,146),(80,163),(57,146),(52,107)],(.32,.80,.77),CREAM,3)
            poly(cr,[(80,103),(98,112),(95,141),(80,153),(66,141),(62,112)],(.12,.44,.51),GOLD,2)
        else:
            color(cr,(.83,.22,.24));cr.set_line_width(15);cr.arc(80,122,24,0,math.pi);cr.stroke()
            rect(cr,49,102,15,22,CREAM);rect(cr,96,102,15,22,CREAM)
            for x in (40,120):line(cr,[(x,121),(x-5,131),(x+2,133)],GOLD,3)
    elif kind=='jetpack':
        for flip in (-1,1):
            poly(cr,[(80,126),(80+flip*52,97),(80+flip*41,131),
                     (80+flip*17,145)],(.41,.79,.80),GOLD,3)
        rect(cr,68,112,24,43,(.17,.35,.42));rect(cr,73,118,14,27,GOLD)
        for x in (69,89):poly(cr,[(x,155),(x+6,155),(x+3,175)],(.97,.49,.17))
    elif kind=='springboots':
        for x,y in ((43,115),(84,104)):
            poly(cr,[(x,y),(x+22,y),(x+22,y+27),(x+34,y+33),
                     (x+34,y+44),(x,y+44)],(.23,.61,.53),GOLD,3)
            line(cr,[(x+5,y+38),(x+29,y+38)],CREAM,3)
    elif kind=='coinboost':
        for x,y in ((63,127),(96,141)):
            circle(cr,x,y,23,GOLD);circle(cr,x,y,17,(1.,.85,.38))
            rect(cr,x-5,y-6,10,12,(.38,.25,.12))
    elif kind.startswith('hero'):
        sliding=kind=='hero-slide';jumping=kind=='hero-jump'
        if sliding:
            poly(cr,[(40,155),(75,135),(116,152),(130,178),(46,181)],(.10,.40,.42),GOLD,2)
            circle(cr,105,143,17,(.10,.15,.20));circle(cr,110,141,10,(.77,.50,.35))
            line(cr,[(54,175),(30,188)],(.10,.15,.20),11)
            line(cr,[(80,174),(116,190)],(.10,.15,.20),10)
            poly(cr,[(87,146),(47,132),(17,148),(55,146)],(.80,.21,.18))
        else:
            swing=math.sin(phase*math.tau/8)*13 if not jumping else 8
            line(cr,[(69,157),(66+swing,189)],(.12,.18,.22),12)
            line(cr,[(89,157),(94-swing,190)],(.12,.18,.22),12)
            line(cr,[(62,189),(75,189)],(.20,.18,.16),8)
            line(cr,[(88,190),(103,190)],(.20,.18,.16),8)
            poly(cr,[(59,98),(100,98),(112,163),(81,177),(46,163)],(.12,.43,.44),(.07,.23,.29),3)
            poly(cr,[(62,104),(80,128),(98,103),(99,144),(80,155),(58,145)],(.32,.64,.59))
            rect(cr,51,144,57,9,(.86,.64,.30))
            line(cr,[(57,111),(42,135-swing),(39,154-swing)],(.16,.49,.47),12)
            line(cr,[(102,111),(118,135+swing),(119,154+swing)],(.16,.49,.47),12)
            circle(cr,80,84,23,(.08,.14,.18));circle(cr,81,62,9,(.08,.14,.18))
            line(cr,[(69,78),(95,78)],(.76,.25,.19),6)
            poly(cr,[(71,98),(104,103),(132,89+swing),(122,111),(95,115)],(.82,.23,.18))
            line(cr,[(60,101),(56,160)],GOLD,2)
    return s

class Renderer:
    def __init__(self):
        self.frame=None;self.background=None;self.native_key=None;self.software=True;self.output_buffer=None
        self.pixel_format=cairo.FORMAT_RGB24;self.converted_background=None
        self.sprites={};self.scaled={};self.native={};self.texts=OrderedDict()
        self.fx=deque(maxlen=40);self.clock=0.;self.flash=0.;self.cache_builds=0
        self._build_background()
        for kind in ('cart','barrier','arch','coin','shield','magnet','jetpack',
                     'springboots','coinboost','hero-slide','hero-jump'):
            self.sprites[kind]=make_sprite(kind)
        for phase in range(8):self.sprites['hero-'+str(phase)]=make_sprite('hero',phase)

    def _build_background(self):
        s=cairo.ImageSurface(cairo.FORMAT_RGB24,W,H);cr=cairo.Context(s)
        gradient=cairo.LinearGradient(0,0,0,H)
        gradient.add_color_stop_rgb(0,.10,.25,.32);gradient.add_color_stop_rgb(.53,.43,.65,.63);gradient.add_color_stop_rgb(1,.92,.84,.65)
        cr.set_source(gradient);cr.paint();circle(cr,746,92,45,(.98,.87,.62))
        for layer,(yy,cc) in enumerate(((205,(.26,.43,.45)),(240,(.20,.37,.40)),(275,(.14,.30,.34)))):
            pts=[(-30,H),(-30,yy)]
            for i in range(25):pts.append((i*44,yy-30-40*math.sin(i*.73+layer)-25*math.sin(i*1.7+layer)))
            pts.append((1000,H));poly(cr,pts,cc)
        # Original riverside architecture, cached once; no external game art.
        for side in (-1,1):
            for i in range(5):
                x=480+side*(170+i*82);y=218+i*22;ww=68+i*23;hh=75+i*25
                rect(cr,x-ww*.40,y,ww*.80,hh,(.43,.31,.24))
                rect(cr,x-ww*.33,y+9,ww*.66,hh-9,(.76,.54,.33))
                for xx in (-.22,.13):rect(cr,x+ww*xx,y+20,ww*.12,hh*.27,(.99,.79,.38))
                roof(cr,x,y,ww,hh*.42)
            for i in range(3):
                x=480+side*(265+i*95);y=195+i*45
                line(cr,[(x,y-32),(x,y+43)],(.20,.20,.20),3)
                circle(cr,x,y+16,17,(.86,.28,.16));rect(cr,x-10,y-2,20,4,GOLD)
                line(cr,[(x,y+33),(x,y+45)],GOLD,2)
        # Distant gate and river below the stone bridge.
        rect(cr,453,173,54,51,(.49,.26,.20));roof(cr,480,166,92,37)
        rect(cr,469,194,22,31,(.10,.24,.29));rect(cr,0,398,W,142,(.12,.31,.35))
        for i in range(10):line(cr,[(i*111-28,436+(i%3)*26),(i*111+34,436+(i%3)*26)],(.37,.54,.51),1)
        self.background=s

    def release_native_output(self):
        self.frame=None;self.native.clear();self.native_key=None;self.output_buffer=None

    def configure_depth(self,depth):
        pixel_format=cairo.FORMAT_RGB16_565 if depth==16 else cairo.FORMAT_RGB24
        if pixel_format!=self.pixel_format:
            self.release_native_output();self.pixel_format=pixel_format;self.converted_background=None

    def ensure_frame(self,target,width,height):
        # Xlib command submission introduced periodic stalls, especially with
        # scaling. Compose in RAM, then upload one completed frame, unscaled.
        software=True
        if self.frame is None or self.software!=software:
            self.native.clear();self.software=software
            self.frame=(cairo.ImageSurface(self.pixel_format,W,H) if software else
                        target.get_target().create_similar(cairo.CONTENT_COLOR,W,H))
        if self.converted_background is None:
            self.converted_background=cairo.ImageSurface(self.pixel_format,W,H)
            cc=cairo.Context(self.converted_background);cc.set_source_surface(self.background,0,0);cc.paint()

    def _native(self,key,surface,target):
        if isinstance(target,cairo.ImageSurface):return surface
        if key not in self.native:
            n=target.create_similar(cairo.CONTENT_COLOR_ALPHA,surface.get_width(),surface.get_height())
            c=cairo.Context(n);c.set_source_surface(surface,0,0);c.paint();self.native[key]=n
        return self.native[key]

    def _text(self,cr,value,x,y,size=18,cc=CREAM,center=False,bold=False):
        key=(str(value),size,cc,bold)
        if key not in self.texts:
            tmp=cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1);tc=cairo.Context(tmp)
            tc.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
            tc.set_font_size(size);e=tc.text_extents(str(value));width=max(1,math.ceil(e[4])+6)
            s=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,size+12);c=cairo.Context(s)
            c.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
            c.set_font_size(size);color(c,cc);c.move_to(3,size+1);c.show_text(str(value));self.texts[key]=s
            while len(self.texts)>100:
                old,_=self.texts.popitem(last=False);self.native.pop(('text',old),None)
            self.cache_builds+=1
        self.texts.move_to_end(key);s=self.texts[key]
        cr.set_source_surface(self._native(('text',key),s,cr.get_target()),round(x-s.get_width()/2 if center else x),round(y));cr.paint()

    def event(self,event):
        kind=event.get('kind')
        if kind in ('collect','hit','shield_break','dodge'):
            self.fx.append(dict(kind=kind,life=.6,x=event.get('lane',1),item=event.get('item_kind','coin')))
            if kind=='hit':self.flash=.22

    def digits(self,cr,value,x,y,size=27,cc=GOLD,pad=0):
        # The HUD changes every frame. Cache ten glyphs once rather than
        # creating/destroying a font surface for every score and metre value.
        text=str(max(0,int(value))).zfill(pad)
        for i,char in enumerate(text):self._text(cr,char,x+i*size*.68,y,size,cc,bold=size>20)

    def update_effects(self,dt):
        self.clock+=min(.1,max(0.,dt));self.flash=max(0.,self.flash-dt)
        for fx in self.fx:fx['life']-=dt
        while self.fx and self.fx[0]['life']<=0:self.fx.popleft()

    @staticmethod
    def project(lane,z,height=0.):
        q=12./(12.+max(NEAR_Z,z))
        return 480+(lane-1)*182*q,194+302*q-height*METRE_PIXELS*q,q

    @staticmethod
    def entity_visible(entity):
        if entity.get('collected'):return False
        if entity.get('contacted') and entity['kind'] in ('cart','ramp'):return False
        length=(RunnerGame.CART_LENGTH if entity['kind']=='cart' else
                RunnerGame.RAMP_LENGTH if entity['kind']=='ramp' else 0.)
        return entity['z']<=FAR_Z and entity['z']+length>=(NEAR_Z if length else -1.5)

    @classmethod
    def route_geometry(cls,entity):
        """Projected solid surface, clipped in world space before projection.

        Entity z is the near/front edge, shared with the collision volume.
        A ramp rises toward z+RAMP_LENGTH; cart roofs stay at ROOF_HEIGHT.
        """
        kind=entity['kind']
        if kind not in ('cart','ramp'):raise ValueError('Not a solid route entity')
        length=RunnerGame.CART_LENGTH if kind=='cart' else RunnerGame.RAMP_LENGTH
        origin=entity['z'];near=max(NEAR_Z,origin);far=min(FAR_Z,origin+length)
        if far<near:return None
        base=entity.get('y',0.);lane=entity['lane'];half=.94/LANE_METRES
        def top(z):
            return base+RunnerGame.ROOF_HEIGHT*(1. if kind=='cart' else (z-origin)/length)
        def point(offset,z,y):return cls.project(lane+offset,z,y)[:2]
        nl,nr=point(-half,near,top(near)),point(half,near,top(near))
        fl,fr=point(-half,far,top(far)),point(half,far,top(far))
        nlb,nrb=point(-half,near,base),point(half,near,base)
        flb,frb=point(-half,far,base),point(half,far,base)
        return dict(top=(nl,fl,fr,nr),left=(nlb,flb,fl,nl),
                    right=(nrb,frb,fr,nr),front=(nlb,nl,nr,nrb),
                    near_z=near,far_z=far,near_height=top(near),far_height=top(far))

    @classmethod
    def player_pose(cls,snapshot):
        jump=max(0.,snapshot.get('jump_height',0.))
        height=snapshot.get('player_y',jump*RunnerGame.JUMP_METRES)
        lane=snapshot.get('player_x',1.)
        support=0.
        for entity in snapshot.get('entities',()):
            if entity.get('contacted') or abs(lane-entity['lane'])>=.43:continue
            z=entity['z'];kind=entity['kind'];surface=None
            if kind=='ramp' and z<=0<=z+RunnerGame.RAMP_LENGTH:
                surface=entity.get('y',0.)+RunnerGame.ROOF_HEIGHT*(-z)/RunnerGame.RAMP_LENGTH
            elif kind=='cart' and z<=0<=z+RunnerGame.CART_LENGTH:
                surface=entity.get('y',0.)+RunnerGame.ROOF_HEIGHT
            if surface is not None and surface<=height+.03:support=max(support,surface)
        x,y,_=cls.project(lane,0,height)
        return dict(x=x,foot_y=y,shadow_y=cls.project(lane,0,support)[1],
                    height=height,support=support,airborne=height>support+.03 or jump>0.)

    def route(self,cr,entity):
        geometry=self.route_geometry(entity)
        if geometry is None:return
        ramp=entity['kind']=='ramp'
        poly(cr,geometry['left'],(.13,.28,.31),INK)
        poly(cr,geometry['right'],(.18,.36,.38),INK)
        poly(cr,geometry['top'],(.26,.48,.46) if ramp else (.68,.72,.66),GOLD,2)
        if ramp:
            # Cross-treads lie on the same inclined plane used for walking.
            for offset in (1.,2.,3.,4.,5.):
                z=entity['z']+offset
                if not geometry['near_z']<=z<=geometry['far_z']:continue
                height=entity.get('y',0.)+RunnerGame.ROOF_HEIGHT*offset/RunnerGame.RAMP_LENGTH+.015
                line(cr,[self.project(entity['lane']+side*.34,z,height)[:2] for side in (-1,1)],GOLD,2)
        else:
            poly(cr,geometry['front'],(.27,.48,.49),GOLD,2)
            base=entity.get('y',0.);z=geometry['near_z'];lane=entity['lane']
            for left,right in ((-.29,-.055),(.055,.29)):
                window=[self.project(lane+dx,z,base+yy)[:2]
                        for dx,yy in ((left,.85),(left,1.86),(right,1.86),(right,.85))]
                poly(cr,window,(.10,.24,.29),CREAM)

    def sprite(self,cr,kind,x,y,scale):
        # 24 pre-scaled sizes per sprite kind, bounded across the full run.
        bucket=max(1,min(26,round(scale*24)));key=(kind,bucket)
        if key not in self.scaled:
            src=self.sprites[kind];w=max(1,round(160*bucket/24));h=max(1,round(200*bucket/24))
            s=cairo.ImageSurface(cairo.FORMAT_ARGB32,w,h);cc=cairo.Context(s);cc.scale(w/160,h/200)
            cc.set_source_surface(src,0,0);cc.paint();self.scaled[key]=s
        s=self._native(('sprite',key),self.scaled[key],cr.get_target())
        cr.set_source_surface(s,round(x-s.get_width()/2),round(y-s.get_height()));cr.paint()

    def road(self,cr,distance):
        poly(cr,[(445,194),(515,194),(812,540),(148,540)],(.66,.64,.52))
        poly(cr,[(480,194),(794,540),(166,540)],(.79,.76,.62))
        for j in range(1,14):
            z=j*6.-distance%6
            _,y,q=self.project(1,z)
            line(cr,[(480-273*q,y),(480+273*q,y)],(.59,.59,.49),max(.6,2*q))
        for lane in (.5,1.5):
            x=480+(lane-1)*182*(540-194)/302
            line(cr,[(480,194),(x,540)],(.61,.54,.39),2)
        for side in (-1,1):
            poly(cr,[(480+side*31,194),(480+side*40,194),(480+side*340,540),(480+side*296,540)],(.31,.39,.35))
            line(cr,[(480+side*38,194),(480+side*330,540)],(.88,.79,.55),4)
            for j in range(1,8):
                z=j*10.-distance%10.;_,y,q=self.project(1,z);x=480+side*307*q
                rect(cr,x-5*q,y-45*q,10*q,45*q,(.48,.48,.38));rect(cr,x-8*q,y-47*q,16*q,5*q,(.87,.78,.54))

    def render(self,target,snapshot,width,height):
        self.ensure_frame(target,width,height)
        cr=cairo.Context(self.frame);cr.set_source_surface(self.converted_background,0,0);cr.paint()
        distance=snapshot.get('distance',0);self.road(cr,distance)
        for e in sorted(snapshot.get('entities',()),key=lambda e:-e['z']):
            if not self.entity_visible(e):continue
            if e['kind'] in ('cart','ramp'):
                self.route(cr,e)
            else:
                x,y,q=self.project(e['lane'],e['z'],e.get('y',0.));self.sprite(cr,e['kind'],x,y,q)
        pose=self.player_pose(snapshot);px,py=pose['x'],pose['foot_y']
        cr.save();cr.translate(px,pose['shadow_y']-2);cr.scale(1,.24);circle(cr,0,0,30,(.28,.34,.29));cr.restore()
        slide=snapshot.get('slide_phase',-1)>=0
        phase=int(distance*1.3)%8
        if snapshot.get('shield_time',0)>0:
            color(cr,(.45,.87,.80));cr.set_line_width(3);cr.arc(px,py-76,74,0,math.tau);cr.stroke()
        self.sprite(cr,'hero-slide' if slide else 'hero-jump' if pose['airborne'] else 'hero-'+str(phase),px,py,.72)
        if snapshot.get('jetpack_time',0)>0:
            self.sprite(cr,'jetpack',px,py-34,.62)
        if snapshot.get('magnet_time',0)>0:
            self._text(cr,'磁铁',px,py-142,16,GOLD,True)
        for fx in self.fx:
            xx,yy,_=self.project(fx['x'],0);age=.6-fx['life'];c=GOLD if fx['kind']=='collect' else (.99,.49,.25)
            for j in range(8):
                ang=j*math.tau/8;rr=age*105;circle(cr,xx+math.cos(ang)*rr,yy-70+math.sin(ang)*rr-age*45,max(1,4*(1-age/.6)),c)
        # Opaque, cached HUD avoids blending a full-screen transparent layer.
        rect(cr,20,15,235,62,(.10,.22,.27));rect(cr,20,15,3,62,GOLD)
        self._text(cr,'地 铁 跑 酷',35,21,23,GOLD,bold=True)
        self._text(cr,'御风过桥 · 奔赴灯火',35,51,12,CREAM)
        rect(cr,702,15,238,62,(.10,.22,.27))
        self.digits(cr,snapshot.get('score',0),715,18,27,GOLD,6)
        self._text(cr,'铜钱',715,51,13);self.digits(cr,snapshot.get('coins',0),747,51,13,CREAM)
        self._text(cr,'行程',802,51,13);self.digits(cr,distance,837,51,13,CREAM)
        self._text(cr,'米',918,51,13)
        for i in range(3):
            circle(cr,36+i*24,96,7,(.87,.31,.23) if i<snapshot.get('lives',3) else (.22,.34,.35))
        status_row=0
        for field,label in (('shield_time','护盾'),('magnet_time','磁铁'),
                            ('jetpack_time','喷气背包'),('boots_time','弹跳鞋'),
                            ('coinboost_time','双倍铜钱')):
            seconds=min(99,math.ceil(max(0.,snapshot.get(field,0.))))
            if seconds<=0:continue
            yy=119+status_row*27
            rect(cr,20,yy,180,23,(.10,.22,.27))
            self._text(cr,label,28,yy+1,14,GOLD)
            self.digits(cr,seconds,155,yy+1,14,GOLD,2)
            status_row+=1
        ui=snapshot.get('ui') or {};state=snapshot.get('state','title')
        gamepad=ui.get('control_mode')=='gamepad'
        if state in ('title','over'):
            rect(cr,208,132,544,286,(.09,.21,.27));rect(cr,218,142,524,266,(.94,.88,.71))
            self._text(cr,'地铁跑酷' if state=='title' else '游戏结束',480,155,43,INK,True,True)
            if state=='over':
                self._text(cr,f"得分 {snapshot.get('score',0)}    行程 {int(distance)} 米",480,250,23,INK,True)
                self._text(cr,'按 X 重新开始' if gamepad else '双手高举重新开始',480,305,23,(.61,.23,.17),True,True)
            else:
                self._text(cr,'准备开始',480,265,23,INK,True)
                self._text(cr,'按 X 开始' if gamepad else '双手高举开始',480,324,24,(.61,.23,.17),True,True)
            note=ui.get('notice') or ''
            self._text(cr,note,480,371,13,(.35,.39,.34),True)
        elif state=='paused' or ui.get('tracking_paused'):
            rect(cr,308,215,344,94,(.10,.22,.27));self._text(cr,'已暂停',480,228,23,GOLD,True)
            self._text(cr,ui.get('notice') or ('Menu 键呼出菜单继续' if gamepad else '转两圈呼出菜单继续'),480,268,14,CREAM,True)
        else:
            if ui.get('notice') and not ui['notice'].startswith('手柄：'):
                self._text(cr,ui['notice'],480,90,17,GOLD,True)
        if self.flash>0:
            rect(cr,0,0,960,7,(.85,.23,.19));rect(cr,0,533,960,7,(.85,.23,.19))
        if (width,height)==(W,H):
            target.set_source_surface(self.frame,0,0);target.paint()
        else:
            if (self.output_buffer is None or
                (self.output_buffer.get_width(),self.output_buffer.get_height())!=(width,height)):
                self.output_buffer=cairo.ImageSurface(self.pixel_format,width,height)
            scaled=cairo.Context(self.output_buffer);color(scaled,(.07,.15,.20));scaled.paint()
            scale=min(width/W,height/H);scaled.translate((width-W*scale)/2,(height-H*scale)/2);scaled.scale(scale,scale)
            scaled.set_source_surface(self.frame,0,0);scaled.get_source().set_filter(cairo.FILTER_NEAREST)
            scaled.get_source().set_extend(cairo.EXTEND_PAD);scaled.paint();self.output_buffer.flush()
            target.set_source_surface(self.output_buffer,0,0);target.paint()

    def warm(self,target,width=W,height=H):
        # Complete upload before the host receives ready; size changes during
        # play must not introduce a fresh scale/upload stall in a busy wave.
        self.ensure_frame(target,width,height)
        context=cairo.Context(self.frame)
        self._native('background',self.background,context.get_target())
        for kind in self.sprites:
            for bucket in ((17,) if kind.startswith('hero') else range(1,27)):
                self.sprite(context,kind,-400,-400,bucket/24)
        self._text(context,'地铁跑酷',-400,-400,23,GOLD,bold=True)
        for size,cc in ((27,GOLD),(13,CREAM)):
            self.digits(context,1234567890,-400,-400,size,cc,10)

    def metrics(self):
        return dict(sprite_cache=len(self.scaled),text_cache=len(self.texts),native_cache=len(self.native),
                    cache_builds=self.cache_builds,fx_count=len(self.fx),render_size=[W,H],
                    compatibility_rendering=True,route_geometry='ramp-roof-metres-v1',
                    presentation='ram-nearest-1to1-upload' if self.software else 'native-1to1-blit',
                    pixel_format='RGB565' if self.pixel_format==cairo.FORMAT_RGB16_565 else 'RGB24')
