"""Pre-scaled raster art with device-aligned Cairo blits and bounded text cache."""
import json,math
from pathlib import Path
from collections import OrderedDict
import cairo
from engine import MOVES
ART=Path(__file__).resolve().parents[1]/'art-v2'
W,H=1280,720
LABELS={'left':'流云拳','right':'破风拳','jab':'流云拳','uppercut_left':'升龙式','uppercut_right':'升龙式','uppercut':'升龙式','palm':'双掌破浪','special':'山河归一','kick_left':'左·穿云腿','kick_right':'右·追风腿'}
RIG_COUNTS={'kick_left':6,'kick_right':6,'sweep':6,'walk':8,'crouch':1,'air':1}
RIG_FLOOR=462.

def rig_geometry(mode,index):
    """Atlas-local articulated poses. Feet, not the old punch atlas, kick.

    Kick index denotes extension, so the last pose occurs at engine windup /
    contact rather than at half the total animation duration. Supporting boots
    stay horizontal with their soles on RIG_FLOOR; lifted boots follow the shin.
    """
    if mode not in RIG_COUNTS or not 0<=index<RIG_COUNTS[mode]:raise ValueError('Invalid rig pose')
    t=index/max(1,RIG_COUNTS[mode]-1)
    rise=105. if mode=='crouch' else 30. if mode=='air' else 32*t if mode=='sweep' else 0.
    front=dict(root=(285.,257.+rise),knee=(343.,339.),ankle=(379.,433.),angle=0.,active=False)
    rear=dict(root=(231.,262.+rise),knee=(177.,346.),ankle=(126.,433.),angle=0.,active=False)
    if mode.startswith('kick'):
        active=rear if mode=='kick_left' else front
        f=min(1.,t/.48);e=max(0.,(t-.48)/.52)
        start=active['ankle'];start_knee=active['knee']
        knee=(start_knee[0]+(348-start_knee[0])*f+62*e,start_knee[1]+(274-start_knee[1])*f+4*e)
        ankle=(start[0]+(345-start[0])*f+218*e,start[1]+(330-start[1])*f-68*e)
        active.update(knee=knee,ankle=ankle,active=True)
        active['angle']=(math.atan2(ankle[1]-knee[1],ankle[0]-knee[0])-math.pi/2)*min(1.,t*3)
    elif mode=='sweep':
        front.update(knee=(343+63*t,339+23*t),ankle=(379+196*t,433-35*t),active=True)
        front['angle']=-1.22*t
    elif mode=='walk':
        a=index*2*math.pi/RIG_COUNTS[mode]
        for leg,phase in ((front,a),(rear,a+math.pi)):
            stride=math.sin(phase);lift=max(0.,math.cos(phase))*18
            rest_x=379 if leg is front else 126
            leg['ankle']=(rest_x+48*stride,433-lift)
            leg['knee']=(leg['root'][0]+(34 if leg is front else -27)+26*stride,343-lift*.4)
    elif mode=='air':
        front.update(knee=(348.,330.),ankle=(301.,389.),angle=.25)
        rear.update(knee=(174.,328.),ankle=(219.,397.),angle=-.20)
    elif mode=='crouch':
        front.update(knee=(357.,389.),ankle=(405.,433.))
        rear.update(knee=(153.,389.),ankle=(105.,433.))
    return dict(front=front,rear=rear,rise=rise,upper=1 if mode in ('crouch','air') else 0,
                robe_scale=.48 if mode=='crouch' else .78 if mode=='air' else .88 if mode=='sweep' else 1.)

class Scene:
    def __init__(self):
        self.atlases={n:cairo.ImageSurface.create_from_png(str(ART/(n+'.png'))) for n in ('hero','rival','arena')}
        self.rects=json.loads((ART/'sprites.json').read_text());self.size=None;self.text_cache=OrderedDict()
    def rebuild(self,w,h):
        self.size=(w,h);self.s=min(w/W,h/H);s=self.s;self.ox=(w-W*s)/2;self.oy=(h-H*s)/2
        import battle_fx
        battle_fx.prepare(s)
        self.surface=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h);c=cairo.Context(self.surface)
        c.set_source_rgb(.09,.13,.12);c.paint();c.translate(self.ox,self.oy);c.scale(s,s)
        a=self.atlases['arena'];c.save();c.scale(W/a.get_width(),H/a.get_height());c.set_source_surface(a,0,0);c.paint();c.restore()
        c.set_source_rgba(.96,.93,.84,.93);c.rectangle(25,15,1230,117);c.fill()
        c.set_source_rgba(.08,.15,.14,.93);c.rectangle(25,626,1230,80);c.fill()
        c.set_source_rgb(.64,.46,.24);c.set_line_width(1);c.rectangle(30,20,1220,107);c.stroke();c.rectangle(30,631,1220,70);c.stroke()
        self.surface.flush();self.background=bytes(self.surface.get_data());self.sprites={};self.text_cache.clear()
        for name in ('hero','rival'):
            for index,rect in enumerate(self.rects[name]):
                x,y,rw,rh=rect;k=.76*s
                img=cairo.ImageSurface(cairo.FORMAT_ARGB32,math.ceil(rw*k),math.ceil(rh*k));p=cairo.Context(img)
                if name=='rival':p.translate(img.get_width(),0);p.scale(-1,1)
                p.scale(k,k);p.set_source_surface(self.atlases[name],-x,-y);p.paint()
                col=index%3;left=(x-col*512-256)*.76
                if name=='rival':left=-(left+rw*.76)
                self.sprites[(name,index)]=(img,left,rh*.76)
        self._build_body_rigs()

    def _build_body_rigs(self):
        """Bake articulated legs in RAM; atlas files are never altered.

        Faces/upper costumes use original art. Pants texture is clipped into
        jointed ink silhouettes, with an actual thigh/shin/boot kick cycle.
        There is no per-frame texture transform or unbounded pose cache.
        """
        self.body_rigs={};self.upper_rigs={};k=.76*self.s
        self.rig_specs={(mode,i):rig_geometry(mode,i) for mode,count in RIG_COUNTS.items() for i in range(count)}
        for name in ('hero','rival'):
            atlas=self.atlases[name]
            texture=cairo.ImageSurface(cairo.FORMAT_ARGB32,40,90)
            tc=cairo.Context(texture);tc.set_source_surface(atlas,-350,-285);tc.paint()
            # Upper art is shared by all frames. Only the lower articulated
            # costume/legs are baked per pose; no 1080p full-body duplication.
            for pose in (0,1):
                # Cut away only the original bent thigh: a full rectangle left
                # a triangular trouser flap beside the new articulated legs.
                # Preserve the trailing silk at the same y on the other side.
                left,top,right,bottom=40,0,476,274
                upper=cairo.ImageSurface(cairo.FORMAT_ARGB32,math.ceil((right-left)*k),math.ceil((bottom-top)*k))
                uc=cairo.Context(upper);uc.scale(k,k)
                uc.move_to(0,0);uc.line_to(right-left,0);uc.line_to(right-left,235)
                uc.line_to(302-left,235);uc.line_to(302-left,bottom);uc.line_to(0,bottom);uc.close_path();uc.clip()
                uc.set_source_surface(atlas,-pose*512-left,-top);uc.paint()
                self.upper_rigs[(name,pose,1)]=(upper,left,top)
                flip=cairo.ImageSurface(cairo.FORMAT_ARGB32,upper.get_width(),upper.get_height())
                fc=cairo.Context(flip);fc.translate(flip.get_width(),0);fc.scale(-1,1);fc.set_source_surface(upper,0,0);fc.paint()
                self.upper_rigs[(name,pose,-1)]=(flip,512-right,top)
            for mode,count in RIG_COUNTS.items():
                for index in range(count):
                    pose=self.rig_specs[(mode,index)];rise=pose['rise'];src_x=512*pose['upper']
                    left,top,right,bottom=40,185,max(477,math.ceil(max(pose[x]['ankle'][0] for x in ('front','rear'))+67)),464
                    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,math.ceil((right-left)*k),math.ceil((bottom-top)*k))
                    c=cairo.Context(surface);c.scale(k,k);c.translate(-left,-top)
                    def leg(data,front):
                        root,knee,ankle=data['root'],data['knee'],data['ankle']
                        def normal(a,b):
                            dx,dy=b[0]-a[0],b[1]-a[1];length=max(1.,math.hypot(dx,dy))
                            return (-dy/length,dx/length)
                        n0,n2=normal(root,knee),normal(knee,ankle)
                        nk=(n0[0]+n2[0],n0[1]+n2[1]);ln=max(.1,math.hypot(*nk));nk=(nk[0]/ln,nk[1]/ln)
                        widths=(69 if front else 63,59 if front else 54,31)
                        def edge(point,n,width,sign):return (point[0]+n[0]*width*sign/2,point[1]+n[1]*width*sign/2)
                        a,b,d=[edge(pt,n,w,1) for pt,n,w in zip((root,knee,ankle),(n0,nk,n2),widths)]
                        aa,bb,dd=[edge(pt,n,w,-1) for pt,n,w in zip((root,knee,ankle),(n0,nk,n2),widths)]
                        def between(a,b,t):return (a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
                        c.new_path();c.move_to(*a)
                        c.curve_to(*between(a,b,.5),*between(a,b,.88),*b)
                        c.curve_to(*between(b,d,.30),*between(b,d,.78),*d)
                        c.line_to(*dd);c.curve_to(*between(dd,bb,.25),*between(dd,bb,.8),*bb)
                        c.curve_to(*between(bb,aa,.35),*between(bb,aa,.78),*aa);c.close_path()
                        c.set_source_rgb(.18,.18,.17);c.fill_preserve()
                        c.save();c.clip()
                        # Stretch one real fabric sample along each bone once.
                        # Repeated 40px tiles made a visible metal-grid texture.
                        for start,end,wide in ((root,knee,widths[0]+12),(knee,ankle,widths[1]+10)):
                            length=math.hypot(end[0]-start[0],end[1]-start[1])
                            angle=math.atan2(end[1]-start[1],end[0]-start[0])-math.pi/2
                            c.save();c.translate(*start);c.rotate(angle)
                            c.scale(wide/40,max(1.,length+8)/90);c.set_source_surface(texture,-20,-4);c.paint();c.restore()
                        c.restore()
                        # Fine ink folds and edges, not a thick vector trouser.
                        c.move_to(*root);c.line_to(*knee);c.line_to(*ankle)
                        c.set_source_rgba(.055,.065,.06,.22);c.set_line_width(2);c.stroke()
                        for sign in (-1,1):
                            e0,e1,e2=[edge(pt,n,w,sign) for pt,n,w in zip((root,knee,ankle),(n0,nk,n2),widths)]
                            c.move_to(*e0);c.line_to(*e1);c.line_to(*e2)
                        c.set_source_rgba(.04,.055,.05,.60);c.set_line_width(1.6);c.stroke()
                        # Original painted boots preserve straps, folds and
                        # paper edges. Only the actual active boot rotates.
                        c.save();c.translate(*ankle);c.rotate(data['angle'])
                        boot_front=front or data['active'];anchor_x=398 if boot_front else 113
                        bx0,bx1=(362,468 if name=='rival' else 455) if boot_front else (72,151)
                        c.scale(.90,.85)
                        if boot_front:
                            c.rectangle(bx0-anchor_x,374-426,bx1-bx0,87)
                        else:
                            # Rectangular rear crops included a disconnected
                            # painted robe tip above the boot. Follow the boot's
                            # slant while retaining the source's natural edge.
                            points=((100,398),(151,398),(137,423),(127,443),
                                    (125,466),(69,466),(70,440),(87,415))
                            c.move_to(points[0][0]-anchor_x,points[0][1]-426)
                            for px,py in points[1:]:c.line_to(px-anchor_x,py-426)
                            c.close_path()
                        c.clip()
                        c.set_source_surface(atlas,-anchor_x,-426);c.paint();c.restore()
                    leg(pose['rear'],False);leg(pose['front'],True)
                    # The original painted robe overlaps both hip joints.
                    # Compress its length in a crouch instead of clipping a
                    # dangling straight cloth edge through the supporting foot.
                    c.save();c.translate(0,250+rise);c.scale(1,pose['robe_scale']);c.translate(0,-250)
                    c.move_to(195,225);c.line_to(304,224);c.line_to(302,309);c.line_to(284,353)
                    c.line_to(253,408);c.line_to(222,419);c.line_to(170,396);c.line_to(129,363)
                    c.line_to(75,376);c.line_to(66,344);c.line_to(82,310);c.line_to(132,269);c.close_path();c.clip()
                    c.set_source_surface(atlas,-src_x,0);c.paint();c.restore()
                    # Device-sized opposite facing is prepared once too.
                    self.body_rigs[(name,mode,index,1)]=(surface,left,top)
                    flip=cairo.ImageSurface(cairo.FORMAT_ARGB32,surface.get_width(),surface.get_height())
                    f=cairo.Context(flip);f.translate(flip.get_width(),0);f.scale(-1,1);f.set_source_surface(surface,0,0);f.paint()
                    self.body_rigs[(name,mode,index,-1)]=(flip,512-right,top)
        self.body_rig_bytes=sum(x[0].get_stride()*x[0].get_height() for x in (*self.body_rigs.values(),*self.upper_rigs.values()))

    def _rig_blit(self,c,name,mode,index,x,ground,facing):
        pose=self.rig_specs[(mode,index)]
        for (img,left,top),rise in ((self.body_rigs[(name,mode,index,facing)],0),
                                  (self.upper_rigs[(name,pose['upper'],facing)],pose['rise'])):
            c.set_source_surface(img,round(self.ox+(x+(left-256)*.76)*self.s),
                                 round(self.oy+(ground+(top+rise-RIG_FLOOR)*.76)*self.s));c.paint()
    def text(self,c,value,x,y,size=24,color=(.14,.20,.19),align='left'):
        s=self.s;key=(str(value),size,color)
        if key not in self.text_cache:
            probe=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1));probe.select_font_face('Noto Serif CJK SC',0,1);probe.set_font_size(size*s)
            xb,yb,w,h,advance,_=probe.text_extents(str(value))
            img=cairo.ImageSurface(cairo.FORMAT_ARGB32,max(1,math.ceil(w)+6),max(1,math.ceil(h)+6));p=cairo.Context(img)
            p.select_font_face('Noto Serif CJK SC',0,1);p.set_font_size(size*s);p.set_source_rgb(*color);p.move_to(3-xb,3-yb);p.show_text(str(value))
            self.text_cache[key]=(img,xb-3,yb-3,advance)
            if len(self.text_cache)>160:self.text_cache.popitem(last=False)
        else:self.text_cache.move_to_end(key)
        img,dx,dy,advance=self.text_cache[key];shift=advance/2 if align=='center' else advance if align=='right' else 0
        c.set_source_surface(img,round(self.ox+x*s+dx-shift),round(self.oy+y*s+dy));c.paint()
    def blit(self,c,name,index,x,ground,alpha=1):
        img,left,height=self.sprites[(name,index)]
        c.set_source_surface(img,round(self.ox+(x+left)*self.s),round(self.oy+(ground-height)*self.s));c.paint_with_alpha(max(0,min(1,alpha)))
    def character(self,c,g,name,x,facing):
        attack=g.attack if name=='hero' else g.enemy_attack;guard=g.guard if name=='hero' else getattr(g,'enemy_guard',False)
        if attack:facing=attack.get('facing',facing)
        pose=1 if guard else 0;weight=0;lunge=0
        if attack:
            move=attack.get('move',attack.get('hand','left'));a=attack['age'];wind=attack.get('windup',.14);duration=attack.get('duration',.36)
            pose=5 if move=='special' else 4 if move in ('palm','enemy_heavy') else 3 if 'uppercut' in move else 2
            if name=='rival' and a<max(0,wind-.2):pose=1;weight=.8
            else:
                wind=max(.08,wind);weight=min(1,a/wind) if a<wind else max(0,1-(a-wind)/max(.08,duration-wind))
                weight=math.sin(weight*math.pi/2)
                lunge=(95 if pose in (2,4) else 55 if pose==3 else 15)*weight
        recoil=max([max(0,1-e['age']/.38) for e in g.effects if e['side']==('player' if name=='hero' else 'enemy') and e['kind']=='hit'] or [0])
        airborne=getattr(g,'player_y' if name=='hero' else 'enemy_y',0.)
        crouch=getattr(g,'crouch' if name=='hero' else 'enemy_crouch',False)
        axis=getattr(g,'move_axis' if name=='hero' else 'enemy_axis',0.)
        walk=getattr(g,'walk_phase' if name=='hero' else 'enemy_walk_phase',0.)
        if attack and (attack.get('move','').startswith('kick_') or attack.get('move')=='enemy_sweep'):
            facing=attack.get('facing',facing);wind=max(.01,attack['windup']);a=attack['age']
            extension=min(1.,a/wind) if a<wind else max(0.,1-(a-wind)/max(.01,attack['duration']-wind))
            mode='sweep' if attack['move']=='enemy_sweep' else attack['move']
            index=min(RIG_COUNTS[mode]-1,round(extension*(RIG_COUNTS[mode]-1)));x+=facing*36*extension
            self._rig_blit(c,name,mode,index,x,590-airborne,facing);return x
        if not attack and (crouch or airborne>1 or abs(axis)>.05):
            mode='crouch' if crouch else 'air' if airborne>1 else 'walk'
            self._rig_blit(c,name,mode,int(walk*2)%RIG_COUNTS[mode] if mode=='walk' else 0,x,590-airborne,facing);return x
        x+=facing*(lunge-recoil*26);ground=590-airborne+math.sin(g.time*2.5)*1.5
        # Existing hero/rival atlases were cached for their original facing.
        reverse=facing!=(1 if name=='hero' else -1)
        if reverse:
            c.save();pivot=self.ox+x*self.s;c.translate(2*pivot,0);c.scale(-1,1)
        if attack and weight>.04:
            # Crossfade windup/recovery; full contact pose at the hit instant.
            if weight<.92:self.blit(c,name,0,x,ground,1-weight)
            self.blit(c,name,pose,x,ground,weight)
        else:self.blit(c,name,pose if guard else 0,x,ground)
        if reverse:c.restore()
        return x
    def paint(self,g,w,h,status='',tracking_pause=False,help_open=False):
        if self.size!=(w,h):self.rebuild(w,h)
        self.surface.flush();self.surface.get_data()[:]=self.background;self.surface.mark_dirty();c=cairo.Context(self.surface)
        c.save();c.rectangle(0,self.oy+134*self.s,w,492*self.s);c.clip()
        px=self.character(c,g,'hero',getattr(g,'player_x',435),getattr(g,'facing',1))
        ex=self.character(c,g,'rival',getattr(g,'enemy_x',845),getattr(g,'enemy_facing',-1))
        import battle_fx
        c.save();c.translate(self.ox,self.oy);c.scale(self.s,self.s);battle_fx.paint(c,g,player_x=px,enemy_x=ex,ground=590);c.restore()
        c.restore()
        gold=(.93,.79,.48);cream=(.95,.91,.80);red=(.62,.16,.10)
        self.text(c,'青 衣',54,53,26);self.text(c,'赤 霄',1226,53,26,align='right');self.text(c,'墨 武',640,62,37,align='center')
        self.text(c,f'{math.ceil(g.remaining):02d}',640,110,29,align='center')
        p=cairo.Context(self.surface);p.translate(self.ox,self.oy);p.scale(self.s,self.s)
        for x,hp,color,mirror in [(54,g.hp,(.18,.39,.32),False),(846,g.enemy_hp,(.58,.21,.15),True)]:
            p.set_source_rgb(.71,.69,.60);p.rectangle(x,69,380,17);p.fill()
            p.set_source_rgb(*color);p.rectangle(x+(380*(1-hp/100) if mirror else 0),69,380*hp/100,17);p.fill()
        energy=getattr(g,'energy',0);p.set_source_rgb(.65,.59,.43);p.rectangle(54,94,245,7);p.fill()
        p.set_source_rgb(.64,.34,.10);p.rectangle(54,94,245*energy/100,7);p.fill()
        self.text(c,'气满 · 双手高举释放绝技' if energy>=100 else f'真气 {round(energy)}/100',310,104,17,red)
        self.text(c,f'{g.enemy_hp} / 100',1208,106,17,align='right')
        # Keep move names in the bottom row of the top panel. Stage-space
        # y=174 covered a jumping fighter's head; this row is below energy /
        # health numbers and leaves the character's entire action readable.
        attack=g.attack
        if attack:self.text(c,attack.get('name',LABELS.get(attack.get('move','left'),'流云拳')),54,124,18,red)
        ea=g.enemy_attack
        if ea and ea['age']<ea.get('windup',.95):
            counter='蹲防或跳起' if ea.get('level')=='low' else '举臂格挡'
            self.text(c,ea.get('name','敌招将至')+' · '+counter,1226,124,18,red,align='right')
        if g.combo>=2:self.text(c,f'{g.combo} 连击',195,239,28,red,align='center')
        for e in g.effects:
            if e['age']>.4:continue
            x=e.get('x',ex if e['side']=='enemy' else px)
            target_height=getattr(g,'enemy_y' if e['side']=='enemy' else 'player_y',0.)
            y=e.get('y',376-target_height)-45-e['age']*40
            label='完美格挡' if e.get('perfect') else '格挡' if e['kind'] in ('block','parry') else f"−{e['damage']}"
            self.text(c,label,x,max(194,y),28,red if e['kind']=='hit' else (.12,.37,.34),align='center')
        self.text(c,'横移进退  ·  抬膝穿云腿  ·  轻蹲起跳  ·  举臂蹲防',640,657,23,gold,align='center')
        self.text(c,status or '收拳再伸拳｜屈肘上挑｜双掌推出｜举臂防御',640,690,19,cream,align='center')
        if g.state not in ('playing','finishing') or help_open:
            p.set_source_rgba(.055,.12,.11,.94);p.rectangle(254,194,772,380);p.fill();p.set_source_rgb(.67,.51,.28);p.set_line_width(2);p.rectangle(263,203,754,362);p.stroke()
            title='招式谱' if help_open else {'menu':'墨武 · 山河对决','countdown':str(max(1,math.ceil(getattr(g,'countdown',0)))),'paused':'入镜后自动继续' if tracking_pause else '已暂停','over':g.result+' · 此局已定'}[g.state]
            self.text(c,title,640,267,38,gold,align='center')
            if g.state=='over' and not help_open:
                self.text(c,f'命中 {g.hits}  ·  格挡 {g.blocks}  ·  完美格挡 {getattr(g,"perfect_blocks",0)}',640,344,24,cream,align='center')
                self.text(c,'点击「开始 / 重来」再战一局',640,430,25,cream,align='center')
            else:
                for j,t in enumerate(['身体左右移动：进退；抬膝：前踢（膝踝需入镜）','小幅下沉再起身：虚拟跳跃，不必真的跳高','举臂下蹲：蹲防；原直拳、上勾、双掌和绝技保留','腿部不可见时，拳法和肩部横移仍可使用']):self.text(c,t,640,325+j*49,20,cream,align='center')
                self.text(c,'点击开始，三秒倒数后交锋',640,538,20,gold,align='center')
        return self.surface
