"""Three ink rails and immutable local Cairo feedback tiles.

Effects follow receipt/display time, never alter rhythm judgments, and retain at
most one grade effect per target. All petals, clouds and droplets are rasterized
on resize; normal frames only select bounded tiles and use the native blitter.
"""
import math
from collections import OrderedDict
from pathlib import Path
import cairo
from .chart import TITLE, section
try:
    from sword.alpha_blit import AlphaBlitter
except ImportError:
    AlphaBlitter=None

COLORS={'L':(.77,.16,.10),'R':(.10,.43,.43),'B':(.72,.43,.08),'C':(.55,.28,.43)}
INK=(.16,.22,.21)
SOFT=(.38,.42,.32)
OK_COLOR=(.39,.43,.22)
PRACTICE_LABELS={'full':'完整曲目','intro':'入门慢练','alternate':'交替击鼓','finale':'高潮练习'}
TEXT_CACHE=OrderedDict()
LANE_X={'L':.33,'C':.50,'R':.67}
# 42 immutable grade frames. Per-grade extents keep the 471 px cache under 10 MiB.
FX_SPECS={'Perfect':(1.08,18),'Good':(.74,12),'OK':(.46,8),'Miss':(.24,4)}
FX_TILE_UNITS={'Perfect':304,'Good':248,'OK':176,'Miss':72}
PULSE_SECONDS=.42
PULSE_FRAMES=8


def palm_glyph(cr,x,y,size,color,alpha=1.):
    """Two facing hands with a contact spark; no emoji/font dependency."""
    cr.save();cr.translate(x,y);cr.scale(size/26.,size/26.)
    cr.set_line_width(1.5);cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for mirror in (1,-1):
        cr.save();cr.scale(mirror,1)
        cr.new_path();cr.move_to(-4,16);cr.line_to(-17,12);cr.line_to(-22,0)
        cr.curve_to(-24,-5,-19,-9,-17,-3);cr.line_to(-14,1)
        cr.line_to(-17,-17);cr.curve_to(-17,-21,-13,-22,-12,-18)
        cr.line_to(-10,-6);cr.line_to(-11,-23);cr.curve_to(-11,-27,-7,-27,-6,-23)
        cr.line_to(-5,-6);cr.line_to(-5,-22);cr.curve_to(-5,-26,-1,-25,-1,-21)
        cr.line_to(0,-5);cr.line_to(3,-13);cr.curve_to(5,-17,9,-14,7,-10)
        cr.line_to(3,3);cr.curve_to(1,9,-2,11,-4,16);cr.close_path()
        cr.set_source_rgba(.99,.94,.80,alpha);cr.fill_preserve()
        cr.set_source_rgba(*color,alpha);cr.stroke();cr.restore()
    cr.set_source_rgba(*COLORS['B'],alpha);cr.set_line_width(2.)
    for dx,dy in ((0,-36),(-8,-33),(8,-33)):
        cr.move_to(dx*.66,dy*.79);cr.line_to(dx,dy)
    cr.stroke();cr.restore();cr.new_path()


def fx_vectors(cr,grade,phase,u):
    """Bake brush bursts, lotus and long ink tails; never called by draw()."""
    alpha=max(0.,1-phase)**.56;growth=math.sqrt(phase)
    gold=(.87,.55,.07);verm=(.74,.075,.035);teal=(.025,.34,.32);deep=(.055,.115,.10)
    if grade=='Miss':
        cr.set_source_rgba(*SOFT,.40*alpha);cr.set_line_width(2*u)
        for a in (.12,math.pi+.12):
            cr.arc(0,0,24*u,a,a+1.15)
        cr.stroke();return
    color=gold if grade=='Perfect' else teal if grade=='Good' else deep
    radius=(38+75*growth if grade=='Perfect' else 31+61*growth if grade=='Good' else 25+40*growth)*u
    # Two broad tapered calligraphy sweeps create an immediate, directional hit.
    # Uneven parallel cuts expose the paper like a dry brush, rather than blur.
    if grade in ('Perfect','Good'):
        reach=(67+52*growth if grade=='Perfect' else 55+42*growth)*u
        for i,a in enumerate((-.28,.35) if grade=='Perfect' else (-.42,.14)):
            cr.save();cr.rotate(a);thick=(17 if grade=='Perfect' else 12)*u
            cr.move_to(-reach,-thick*.10);cr.curve_to(-reach*.48,-thick,reach*.45,-thick*.40,reach,thick*.03)
            cr.line_to(reach*.50,thick*.48);cr.curve_to(-reach*.20,thick*.88,-reach*.63,thick*.25,-reach,-thick*.10)
            cr.close_path();cr.set_source_rgba(*(deep if i==0 else verm if grade=='Perfect' else teal),alpha*(.74 if i==0 else .78));cr.fill()
            cr.set_source_rgba(*color,alpha*.9);cr.set_line_width((4.0 if grade=='Perfect' else 2.8)*u)
            cr.move_to(-reach*.83,-thick*.40);cr.curve_to(-reach*.25,-thick*.73,reach*.26,-thick*.38,reach*.88,0);cr.stroke()
            for j in range(3):
                cr.set_source_rgba(*deep,alpha*.72);cr.set_line_width((1+j%2)*u)
                cr.move_to(-reach*.85,(j*3-1)*u);cr.line_to(reach*(.51+j*.13),(j*2+1)*u);cr.stroke()
            cr.restore()
    # Broken, thick expansion arcs accompany the brush burst, not its main body.
    cr.set_source_rgba(*color,alpha*.88);cr.set_line_width((4.5 if grade=='Perfect' else 3.2 if grade=='Good' else 2)*u)
    cr.save();cr.scale(1.,.69)
    for a in (.08,math.pi+.28):cr.arc(0,0,radius,a,a+2.28)
    cr.stroke();cr.restore()
    count=18 if grade=='Perfect' else 8 if grade=='Good' else 4
    for j in range(count):
        a=j*math.tau/count+.17+(j%2)*phase*.20;rr=radius*(.68+(j%3)*.13)
        dx,dy=math.cos(a),math.sin(a)*.67
        ink=verm if grade=='Perfect' and j%3==0 else deep if j%3==1 else color
        cr.set_source_rgba(*ink,alpha*(.90 if grade=='Perfect' else .88))
        if grade=='OK':
            cr.arc(dx*rr,dy*rr,(3+j%2)*u,0,math.tau);cr.fill();continue
        length=(17+(j%4)*4+growth*5 if grade=='Perfect' else 11+(j%3)*4)*u
        thick=(3.4 if grade=='Perfect' else 3.)*u
        cr.move_to(dx*(rr-11*u),dy*(rr-11*u))
        cr.curve_to(dx*rr-dy*thick,dy*rr+dx*thick,
                    dx*(rr+length)-dy*thick,dy*(rr+length)+dx*thick,
                    dx*(rr+length),dy*(rr+length))
        cr.line_to(dx*(rr+length*.50)+dy*thick,dy*(rr+length*.50)-dx*thick)
        cr.close_path();cr.fill()
        circle(cr,dx*(rr+length*.7),dy*(rr+length*.7),(2.5+j%2)*u,ink,alpha*.90)
    if grade!='Perfect':return
    # A full fan of bold vermilion petals sits below the hit center; gold ribs
    # and ink outlines stay visible against the light parchment background.
    bloom=(.78+.30*math.sin(phase*math.pi))*u
    for j in range(9):
        a=(j-4)*.36
        cr.save();cr.translate(0,(55+phase*14)*u);cr.rotate(a);cr.scale(bloom,bloom)
        reach=59-abs(j-4)*3
        cr.move_to(0,5);cr.curve_to(-16,-4,-18,-reach+11,0,-reach)
        cr.curve_to(18,-reach+11,16,-4,0,5);cr.close_path()
        cr.set_source_rgba(*(verm if j%2 else gold),alpha*.52);cr.fill_preserve()
        cr.set_source_rgba(*deep,alpha*.80);cr.set_line_width(2.5);cr.stroke_preserve()
        cr.set_source_rgba(*gold,alpha*.96);cr.set_line_width(1.2);cr.stroke()
        cr.move_to(0,1);cr.line_to(0,-reach*.77);cr.set_line_width(1.4);cr.stroke();cr.restore()
    for mirror in (-1,1):
        cr.save();cr.scale(mirror*u,u);cr.translate(phase*10,-phase*17)
        cr.move_to(40,-47);cr.curve_to(62,-75,106,-65,101,-43)
        cr.curve_to(94,-27,65,-31,73,-45);cr.curve_to(78,-57,95,-53,90,-43)
        cr.move_to(52,-60);cr.curve_to(67,-84,114,-79,124,-53)
        cr.set_source_rgba(*deep,alpha*.74);cr.set_line_width(5);cr.stroke_preserve()
        cr.set_source_rgba(*gold,alpha*.98);cr.set_line_width(2.3);cr.stroke();cr.restore()
    cr.set_source_rgba(*gold,alpha*.97);cr.set_line_width(3*u)
    for j in range(12):
        a=j*math.tau/12+.1;rr=(65+growth*45)*u
        cr.move_to(math.cos(a)*rr,math.sin(a)*rr*.75)
        cr.line_to(math.cos(a)*(rr+12*u),math.sin(a)*(rr+12*u)*.75)
    cr.stroke()


def paint_tile(cr,surface,x,y,frame=None,regions=None):
    left,top=round(x),round(y)
    if frame is not None and cr.get_target()==frame.target and frame.paint_tile(surface,left,top,regions):return
    cr.save();cr.rectangle(left,top,surface.get_width(),surface.get_height());cr.clip()
    if regions is not None:
        cr.new_path()
        for rx,ry,rw,rh in regions:cr.rectangle(rx,ry,rw,rh)
        cr.clip()
    cr.set_source_surface(surface,left,top);cr.paint();cr.restore();cr.new_path()


def text(cr,label,x,y,size=22,color=(.16,.22,.21),center=False,frame=None):
    key=(label,round(size,2),color)
    cached=TEXT_CACHE.get(key)
    if cached is None:
        probe=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
        probe.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
        probe.set_font_size(size);ex=probe.text_extents(label)
        ox,oy=math.floor(ex[0])-2,math.floor(ex[1])-2
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,max(1,math.ceil(ex[2])+5),max(1,math.ceil(ex[3])+5))
        tc=cairo.Context(surface);tc.set_font_face(probe.get_font_face());tc.set_font_size(size)
        tc.set_source_rgb(*color);tc.move_to(-ox,-oy);tc.show_text(label)
        cached=(surface,ox,oy,ex[2]);TEXT_CACHE[key]=cached
        while len(TEXT_CACHE)>256:TEXT_CACHE.popitem(last=False)
    else:TEXT_CACHE.move_to_end(key)
    surface,ox,oy,width=cached
    if center:x-=width/2
    paint_tile(cr,surface,x+ox,y+oy,frame)


def circle(cr,x,y,r,color,alpha=1.):
    cr.new_path();cr.set_source_rgba(*color,alpha);cr.arc(x,y,r,0,math.tau);cr.fill()


def plain_canvas(cr,w,h):
    """Only cache opaque patches when every original OVER fully covers them."""
    try:
        target=cr.get_target()
        return (tuple(cr.get_matrix())==(1.,0.,0.,1.,0.,0.) and
                cr.get_operator()==cairo.OPERATOR_OVER and cr.get_group_target()==target and
                target.get_device_scale()==(1.,1.) and target.get_device_offset()==(0.,0.) and
                list(cr.copy_clip_rectangle_list())==[(0.,0.,float(w),float(h))])
    except (AttributeError,cairo.Error):return False


def font_key(options):
    return (options.get_antialias(),options.get_subpixel_order(),options.get_hint_style(),
            options.get_hint_metrics(),options.get_variations() if hasattr(options,'get_variations') else None)


def stroke_key(cr):
    dash,offset=cr.get_dash()
    return (cr.get_antialias(),cr.get_line_cap(),cr.get_line_join(),cr.get_miter_limit(),
            tuple(dash),offset,cr.get_fill_rule(),cr.get_tolerance())


def copy_stroke(cr,context):
    context.set_antialias(cr.get_antialias());context.set_line_cap(cr.get_line_cap())
    context.set_line_join(cr.get_line_join());context.set_miter_limit(cr.get_miter_limit())
    context.set_dash(*cr.get_dash());context.set_fill_rule(cr.get_fill_rule())
    context.set_tolerance(cr.get_tolerance())


class Renderer:
    def __init__(self):
        root=Path(__file__).with_name('assets')
        self.landscape=cairo.ImageSurface.create_from_png(str(root/'landscape.png'))
        self.drum=cairo.ImageSurface.create_from_png(str(root/'drum.png'))
        self.size=None;self.background=None;self.drum_scaled=None;self.allocations=0
        self.blitter=AlphaBlitter() if AlphaBlitter else None;self.frame=None
        self.tone_level=None;self.toned=None
        self.grade_tiles={};self.pulse_tiles={};self.clap_target=None
        self.note_tiles={}
        self.effect_cache_bytes=0;self.effect_allocations=0
        self.last_effect_targets=0;self.last_effect_particles=0
        self.effect_clip=None
        self.fitted_widths=OrderedDict()
        self.hud_tiles={};self.hud_keys={};self.hud_regions={}
        self.hud_cache_builds=0;self.hud_cache_bytes=0
        self.header_tile=None;self.header_key=None;self.header_cache_builds=0

    def prepare_effects(self,u):
        """Resize-only rasterization: 71 local tiles; under 10 MiB at 1024x471."""
        self.grade_tiles={};self.pulse_tiles={};self.effect_cache_bytes=0
        def tile(size):
            d=max(1,math.ceil(size*u));s=cairo.ImageSurface(cairo.FORMAT_ARGB32,d,d)
            self.effect_cache_bytes+=s.get_stride()*d;self.effect_allocations+=1
            c=cairo.Context(s);c.translate(d/2,d/2);return s,c
        for grade,(_,frames) in FX_SPECS.items():
            sequence=[]
            for i in range(frames):
                s,c=tile(FX_TILE_UNITS[grade]);fx_vectors(c,grade,i/(frames-1),u);sequence.append(s)
            self.grade_tiles[grade]=tuple(sequence)
        for lane in ('L','C','R'):
            sequence=[]
            for i in range(PULSE_FRAMES):
                s,c=tile(174);phase=i/(PULSE_FRAMES-1)*.96;alpha=1-phase
                r=(32+phase*PULSE_SECONDS*110)*u
                c.set_source_rgba(*COLORS[lane],alpha*.67);c.set_line_width(4*u*alpha)
                c.arc(0,0,r,0,math.tau);c.stroke()
                for j in range(12):
                    a=j*math.tau/12+.13;rr=r*(.7+(j%3)*.13)
                    dx,dy=math.cos(a),math.sin(a)*.62
                    c.set_source_rgba(*COLORS[lane],alpha*.65)
                    c.move_to(dx*(rr-4*u),dy*(rr-4*u))
                    c.line_to(dx*(rr+7*u)-dy*2*u,dy*(rr+7*u)+dx*2*u)
                    c.line_to(dx*(rr+3*u)+dy*2*u,dy*(rr+3*u)-dx*2*u)
                    c.close_path();c.fill()
                sequence.append(s)
            self.pulse_tiles[lane]=tuple(sequence)
        self.clap_target,c=tile(74)
        circle(c,0,0,31*u,(.98,.93,.79),.94)
        c.set_source_rgba(*COLORS['C'],.85);c.set_line_width(2.5*u)
        c.arc(0,0,31*u,0,math.tau);c.stroke()
        palm_glyph(c,0,4*u,19*u,COLORS['C'])
        self.note_tiles={}
        for lane in ('L','C','R','B'):
            s,c=tile(48);r=19*u
            circle(c,2*u,3*u,r+2*u,(.15,.23,.20),.20)
            circle(c,0,0,r,COLORS[lane]);circle(c,0,0,r*.72,(.99,.93,.74),.96)
            if lane=='C':palm_glyph(c,0,4*u,12*u,COLORS['C'])
            else:text(c,'合' if lane=='B' else '左' if lane=='L' else '右',0,6*u,17*u,COLORS[lane],True)
            self.note_tiles[lane]=s

    def grade_effects(self,cr,w,h,state):
        """At most three local effects; old/future display timestamps are skipped."""
        self.last_effect_targets=0;self.last_effect_particles=0
        feedback=state.get('feedback',())
        for lane in ('L','C','R'):
            for e in reversed(feedback):
                if e['lane']!=lane and not (lane!='C' and e['lane']=='B'):continue
                grade=e['grade'];spec=FX_SPECS.get(grade)
                if spec is None:break
                age=state['time']-e.get('display_time',e['time'])
                if not 0<=age<spec[0]:break
                sequence=self.grade_tiles[grade]
                frame=sequence[min(len(sequence)-1,int(age/spec[0]*len(sequence)))]
                paint_tile(cr,frame,w*LANE_X[lane]-frame.get_width()/2,
                           h*.70-frame.get_height()/2,self.frame,self.effect_clip)
                self.last_effect_targets+=1
                self.last_effect_particles+={'Perfect':18,'Good':8,'OK':4,'Miss':0}[grade]
                break

    def notes(self,cr,w,h,state,u):
        hit=h*.70;top=h*.24;visible=0
        for note in state['notes']:
            if note['status']!='pending':continue
            remaining=note['time']-state['time'];lane=note['lane']
            if not -.25<remaining<3. or lane not in self.note_tiles:continue
            if visible>=72:break
            visible+=1;y=hit-(hit-top)*remaining/3
            sides=('L','R') if lane=='B' else (lane,)
            if lane=='B':
                cr.set_source_rgba(*COLORS['B'],.75);cr.set_line_width(5*u)
                # Double downward hits connect only the drum rails, clear of C.
                cr.move_to(w*.33,y);cr.line_to(w*.44,y)
                cr.move_to(w*.56,y);cr.line_to(w*.67,y);cr.stroke()
            tile=self.note_tiles[lane]
            for side in sides:
                paint_tile(cr,tile,w*LANE_X[side]-tile.get_width()/2,
                           y-tile.get_height()/2,self.frame)

    def text(self,*args,**kwargs):
        text(*args,**kwargs,frame=self.frame)

    def fitted_text(self,cr,label,x,y,size,max_width,color=INK,center=False):
        """Keep user-facing notices inside their layout region at every size."""
        options=cr.get_font_options()
        key=(label,size,tuple(cr.get_matrix())[:4],cr.get_group_target().get_device_scale(),font_key(options))
        width=self.fitted_widths.get(key)
        if width is None:
            cr.save();cr.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(size);width=cr.text_extents(label)[2];cr.restore()
            self.fitted_widths[key]=width
            while len(self.fitted_widths)>256:self.fitted_widths.popitem(last=False)
        else:self.fitted_widths.move_to_end(key)
        if width>max_width and width>0:size*=max_width/width
        self.text(cr,label,x,y,size,color,center)

    def bar(self,cr,x,y,width,height,progress,color):
        cr.set_source_rgba(*INK,.12);cr.rectangle(x,y,width,height);cr.fill()
        cr.set_source_rgb(*color);cr.rectangle(x,y,width*min(1.,max(0.,progress)),height);cr.fill()

    def seal(self,cr,label,x,y,size,unit):
        cr.set_source_rgba(*COLORS['L'],.09);cr.rectangle(x-size/2,y-size/2,size,size);cr.fill()
        cr.set_source_rgba(*COLORS['L'],.72);cr.set_line_width(1.4*unit)
        cr.rectangle(x-size/2,y-size/2,size,size);cr.stroke()
        self.text(cr,label,x,y+size*.25,size*.66,COLORS['L'],True)

    def prepare(self,w,h):
        if self.size==(w,h):return
        self.size=(w,h);self.allocations+=1;self.tone_level=None
        self.hud_tiles={};self.hud_keys={};self.hud_cache_bytes=0
        self.header_tile=None;self.header_key=None
        top=max(0,math.floor(h*.255)-math.ceil(3*max(.6,min(w/1024,h/560))))
        self.hud_regions={'left':(0,top,math.ceil(w*.22),h-top),
                         'right':(math.floor(w*.78),top,w-math.floor(w*.78),h-top)}
        self.background=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        cr=cairo.Context(self.background)
        cr.save();cr.scale(w/self.landscape.get_width(),h/self.landscape.get_height())
        cr.set_source_surface(self.landscape,0,0);cr.paint();cr.restore()
        self.finale_background=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        fc=cairo.Context(self.finale_background);fc.set_source_surface(self.background,0,0);fc.paint()
        cr.set_source_rgba(.97,.945,.865,.44);cr.paint()
        cr.set_source_rgba(.97,.945,.865,.78);cr.rectangle(w*.02,h*.02,w*.96,h*.15);cr.fill()
        for x in (.02,.78):
            cr.set_source_rgba(.97,.945,.865,.70)
            cr.rectangle(w*x,h*.255,w*.20,h*(.60 if x==.02 else .665));cr.fill()
        # Static ink rails, every frame reuses this surface.
        for x,half in ((.33,.105),(.50,.053),(.67,.105)):
            cr.set_source_rgba(.96,.93,.83,.68);cr.rectangle(w*(x-half),h*.20,w*half*2,h*.50);cr.fill()
            cr.set_source_rgba(.19,.33,.29,.45);cr.set_line_width(2)
            cr.move_to(w*(x-half),h*.24);cr.line_to(w*(x-half),h*.70)
            cr.move_to(w*(x+half),h*.24);cr.line_to(w*(x+half),h*.70);cr.stroke()
        d=max(1,int(h*.30));self.drum_scaled=cairo.ImageSurface(cairo.FORMAT_ARGB32,d,d)
        dc=cairo.Context(self.drum_scaled);dc.scale(d/self.drum.get_width(),d/self.drum.get_height())
        dc.set_source_surface(self.drum,0,0);dc.paint()
        self.toned=cairo.ImageSurface(cairo.FORMAT_RGB24,w,h)
        # Preserve both HUD wings and the action labels even during large bursts.
        self.effect_clip=((round(w*.22),round(h*.24),round(w*.56),round(h*.63)),)
        self.prepare_effects(max(.6,min(w/1024,h/560)))

    def tone(self,t,w,h):
        level=min(55,max(0,int(t)))
        if level!=self.tone_level:
            c=cairo.Context(self.toned);c.set_source_surface(self.background,0,0);c.paint()
            c.set_source_rgba(.04,.37,.34,.08*level/55);c.rectangle(0,h*.55,w,h*.45);c.fill()
            self.tone_level=level
        return self.toned

    def draw(self,cr,w,h,state,ui):
        self.frame=None;self.prepare(w,h)
        if state['finished'] and plain_canvas(cr,w,h):
            # The completed scroll paints an opaque full-canvas background.
            # Build it directly instead of drawing the gameplay it covers.
            self.frame=self.blitter.prepare_frame(cr) if self.blitter else None
            return self.finale(cr,w,h,state,ui,max(.6,min(w/1024,h/560)))
        cr.set_source_surface(self.tone(state['time'],w,h),0,0);cr.paint()
        self.frame=self.blitter.prepare_frame(cr) if self.blitter else None
        t=state['time'];unit=min(w/1024,h/560);unit=max(.6,unit)
        begin=state.get('start_time',0.);duration=max(.001,state['duration']-begin)
        elapsed=min(duration,max(0.,t-begin));practice=state.get('practice','full')
        difficulty=state.get('difficulty_label',{'easy':'简单','normal':'普通','hard':'困难'}.get(state['difficulty'],'普通'))
        fever=state.get('fever_active',False)
        total_seconds=int(round(duration))
        self.header(cr,w,h,unit,(TITLE+' · '+section(t),difficulty,
            PRACTICE_LABELS.get(practice,'分段练习'),'%06d'%state['score'],
            '%02d:%02d / %02d:%02d'%(int(elapsed)//60,int(elapsed)%60,total_seconds//60,total_seconds%60)))
        self.bar(cr,w*.035,h*.18,w*.93,3*unit,elapsed/duration,COLORS['B'])
        self.hud(cr,w,h,state,unit)
        hit=h*.70
        if fever:
            # Local gold rails signal the earned multiplier; never flash the frame.
            cr.set_source_rgba(*COLORS['B'],.68);cr.set_line_width(3*unit)
            for x,half in ((.33,.105),(.50,.053),(.67,.105)):
                cr.rectangle(w*(x-half),h*.24,w*half*2,h*.455);cr.stroke()
        for side in ('L','C','R'):
            x=w*LANE_X[side];pulse=None
            if 'strike_feedback' in state:
                for e in reversed(state['strike_feedback']):
                    if e['hand']==side:pulse=e;break
                recent=pulse['display_time'] if pulse else -100.
            else:
                recent=-100.
                for e in reversed(state['feedback']):
                    if e['grade']!='Miss' and (e['lane']==side or (side!='C' and e['lane']=='B')):
                        recent=e.get('display_time',e['time']);break
            age=t-recent
            drumsize=self.drum_scaled.get_width()
            bounce=math.sin(age/.16*math.pi)*6*unit if 0<=age<.16 else 0
            if side=='C':
                paint_tile(cr,self.clap_target,x-self.clap_target.get_width()/2,
                           hit-self.clap_target.get_height()/2+bounce,self.frame)
                self.text(cr,'双手击掌',x,h*.88,15*unit,COLORS['C'],True)
            else:paint_tile(cr,self.drum_scaled,x-drumsize/2,h*.67+bounce,self.frame)
            cr.set_source_rgba(*COLORS[side],.75);cr.set_line_width(3*unit)
            if side!='C':cr.new_path();cr.arc(x,hit,28*unit,0,math.tau);cr.stroke()
            if 0<=age<PULSE_SECONDS:
                sequence=self.pulse_tiles[side];frame=sequence[min(PULSE_FRAMES-1,int(age/PULSE_SECONDS*PULSE_FRAMES))]
                paint_tile(cr,frame,x-frame.get_width()/2,hit-frame.get_height()/2,self.frame)
            hand=ui.get('hands',{}).get(side)
            if pulse and 0<=t-pulse['display_time']<.60:
                hint={'early':'已击鼓 · 偏早','late':'已击鼓 · 偏晚',
                      'wrong_hand':'已击鼓 · 看左右','waiting_other_hand':'已击鼓 · 等另一手',
                      'double_hand_gap':'已击鼓 · 双手未齐'}.get(pulse.get('reason'),
                          '空击 · 已识别' if pulse.get('outcome')=='empty' else '已击鼓')
                if side=='C':hint=hint.replace('已击鼓','已击掌')
                self.text(cr,hint,x,h*.985,13*unit,COLORS[side],True)
        if not state['finished']:
            self.grade_effects(cr,w,h,state)
            # Notes are the final rail layer: even the gold burst cannot hide them.
            self.notes(cr,w,h,state,unit)
        notice=ui.get('notice','')
        if not state['paused'] and not any(word in notice for word in ('未连接','失败','错误','恢复','暂停')):
            notice=''
        if not state['paused'] and not state['finished'] and t<state.get('lead_in_until',begin):
            notice='准备 · %d'%max(1,math.ceil(state['lead_in_until']-t))
        self.fitted_text(cr,notice,w*.5,h*.225,17*unit,w*.80,(.50,.17,.10),True)
        self.fitted_text(cr,ui.get('audio_notice',''),w*.5,h*.965,12*unit,w*.88,(.35,.34,.28),True)
        if state['paused'] and not state['finished']:
            cr.set_source_rgba(.97,.94,.84,.88);cr.rectangle(w*.17,h*.32,w*.66,h*.30);cr.fill()
            self.fitted_text(cr,ui.get('overlay') or '已暂停',w*.5,h*.435,31*unit,w*.60,center=True)
        if state['finished']:
            self.finale(cr,w,h,state,ui,unit)

    def header(self,cr,w,h,u,labels):
        def vectors(context):
            title,difficulty,practice,score,clock=labels
            self.text(context,'鼓动山河',w*.035,h*.10,31*u)
            self.fitted_text(context,title,w*.035,h*.155,15*u,w*.32,SOFT)
            self.text(context,difficulty,w*.53,h*.092,18*u,center=True)
            self.text(context,practice,w*.53,h*.143,14*u,SOFT,True)
            self.text(context,score,w*.83,h*.10,33*u,center=True)
            self.text(context,clock,w*.83,h*.155,14*u,SOFT,True)
        if self.frame is None and not plain_canvas(cr,w,h):return vectors(cr)
        options=cr.get_font_options();key=(labels,font_key(options));height=math.ceil(h*.17)
        if self.header_key!=key:
            if self.header_tile is None:self.header_tile=cairo.ImageSurface(cairo.FORMAT_RGB24,w,height)
            hc=cairo.Context(self.header_tile);hc.set_font_options(options)
            hc.set_source_surface(self.background,0,0);hc.paint()
            frame=self.frame;self.frame=None
            try:vectors(hc)
            finally:self.frame=frame
            self.header_key=key;self.header_cache_builds+=1
        cr.save();cr.rectangle(0,0,w,height);cr.clip()
        cr.set_source_surface(self.header_tile,0,0);cr.paint();cr.restore();cr.new_path()

    def hud(self,cr,w,h,state,u):
        """Reuse opaque flank patches, retaining the original Cairo rounding.

        The moving fever/groove bar stays live. It cannot overlap a label, so
        drawing it after the cached flank preserves the original layer output.
        Cache only identity device coordinates; other Cairo callers retain the
        original vector path, including transformed/group rendering.
        """
        if self.frame is None and not plain_canvas(cr,w,h):
            return self._hud_vectors(cr,w,h,state,u)
        fever=state.get('fever_active',False);challenge=state.get('challenge')
        options=cr.get_font_options();option_key=(font_key(options),stroke_key(cr))
        windows=state.get('judgment_windows_ms',{'Perfect':110,'Good':220})
        left=(self.tone_level,fever,option_key,
              tuple(challenge.get(k) for k in ('label','status','progress','target','earned')) if challenge else None,
              state.get('judgment_label','严格节奏'),windows['Perfect'],windows['Good'],
              state.get('hit_window_ms',220),state.get('simultaneous_ms',120),state.get('judgment','strict'))
        feedback=state.get('feedback',());active=None
        if feedback:
            e=feedback[-1]
            if 0<=state['time']-e.get('display_time',e['time'])<max(.75,FX_SPECS.get(e['grade'],(.75,0))[0]):
                active=(e['grade'],e['lane'],e.get('timing_ms'))
        right=(self.tone_level,fever,option_key,'%.1f%%'%state.get('accuracy',0.),state.get('rank','--'),
               state['combo'],state['best_combo'],active)
        for side,key in (('left',left),('right',right)):
            x,y,tw,th=self.hud_regions[side]
            if self.hud_keys.get(side)!=key:
                tile=self.hud_tiles.get(side)
                if tile is None:
                    tile=cairo.ImageSurface(cairo.FORMAT_RGB24,tw,th);self.hud_tiles[side]=tile
                    self.hud_cache_bytes+=tile.get_stride()*th
                hc=cairo.Context(tile);hc.set_font_options(options);copy_stroke(cr,hc);hc.translate(-x,-y)
                hc.set_source_surface(self.toned,0,0);hc.paint()
                frame=self.frame;self.frame=None
                try:self._hud_vectors(hc,w,h,state,u,live_bar=False)
                finally:self.frame=frame
                self.hud_keys[side]=key;self.hud_cache_builds+=1
            # RGB patches contain the same already-opaque background and avoid
            # the extra OVER rounding of grouping transparent HUD components.
            cr.save();cr.rectangle(x,y,tw,th);cr.clip()
            cr.set_source_surface(self.hud_tiles[side],x,y);cr.paint();cr.restore();cr.new_path()
        progress=min(1.,state.get('fever_remaining',0.)/4.8) if fever else state.get('groove',0.)/100.
        self.bar(cr,w*.035,h*.329,w*.16,7*u,progress,COLORS['B'] if fever else COLORS['R'])

    def _hud_vectors(self,cr,w,h,state,u,live_bar=True):
        """Flank the note rails so progression never obscures the hit targets."""
        x=w*.035;right=w*.965;wing=w*.16
        fever=state.get('fever_active',False)
        self.text(cr,'双倍得分' if fever else '能量',x,h*.30,18*u,COLORS['B'] if fever else INK)
        progress=min(1.,state.get('fever_remaining',0.)/4.8) if fever else state.get('groove',0.)/100.
        if live_bar:self.bar(cr,x,h*.329,wing,7*u,progress,COLORS['B'] if fever else COLORS['R'])
        self.text(cr,'鼓点双倍 ×2' if fever else '命中积累能量',x,h*.39,14*u,COLORS['B'] if fever else SOFT)
        challenge=state.get('challenge')
        self.text(cr,'乐句挑战',x,h*.49,17*u)
        if challenge:
            self.fitted_text(cr,challenge.get('label',''),x,h*.548,16*u,wing)
            status=challenge.get('status','pending');progress=challenge.get('progress',0);target=max(1,challenge.get('target',1))
            color=COLORS['R'] if status=='complete' else COLORS['B'] if status=='active' else SOFT
            self.bar(cr,x,h*.58,wing,5*u,progress/target,color)
            label={'pending':'即将开始','active':'%d / %d 鼓'%(progress,target),'complete':'达成  +%d'%challenge.get('earned',0),'failed':'未完成'}.get(status,'')
            self.fitted_text(cr,label,x,h*.635,14*u,wing,color)
        else:
            self.text(cr,'跟上鼓点',x,h*.55,15*u,SOFT)
            self.text(cr,'稳住每一次合击',x,h*.603,13*u,SOFT)
        center=right-wing/2
        self.text(cr,'准确率',center,h*.30,17*u,SOFT,True)
        self.text(cr,'%.1f%%'%state.get('accuracy',0.),center,h*.375,27*u,INK,True)
        rank=state.get('rank','--')
        self.seal(cr,rank,center,h*.432,27*u,u)
        self.text(cr,str(state['combo']),center,h*.55,38*u,COLORS['B'] if fever else INK,True)
        self.text(cr,'连 击',center,h*.59,14*u,INK,True)
        self.text(cr,'最高连击',center,h*.69,13*u,SOFT,True)
        self.text(cr,str(state['best_combo']),center,h*.748,23*u,INK,True)
        feedback=state.get('feedback',())
        if feedback:
            e=feedback[-1]
            if 0<=state['time']-e.get('display_time',e['time'])<max(.75,FX_SPECS.get(e['grade'],(.75,0))[0]):
                label={'Perfect':'完美','Good':'良好','OK':'命中','Miss':'未命中'}[e['grade']]
                if e['lane']=='C':label={'Perfect':'完美','Good':'良好','OK':'命中','Miss':'未命中'}[e['grade']]
                strong=e['grade'] in ('Perfect','Good')
                color=(.98,.78,.23) if e['grade']=='Perfect' else (.99,.96,.83) if strong else OK_COLOR if e['grade']=='OK' else SOFT
                cr.set_source_rgba(*((.055,.115,.10) if e['grade']=='Perfect' else (.035,.28,.25) if strong else (.98,.95,.86)),.94)
                cr.rectangle(right-wing,h*.78,wing,h*.136);cr.fill()
                cr.set_source_rgba(*(COLORS['B'] if e['grade']=='Perfect' else COLORS['R'] if strong else SOFT),.88)
                cr.set_line_width(2*u);cr.rectangle(right-wing,h*.78,wing,h*.136);cr.stroke()
                self.fitted_text(cr,label,center,h*.832,25*u,wing*.94,color,True)
                timing=e.get('timing_ms')
                if timing is not None:
                    label='节拍正好' if abs(timing)<=35. else ('稍早 ' if timing<0 else '稍晚 ')+str(round(abs(timing)))+' ms'
                    self.fitted_text(cr,label,center,h*.884,12*u,wing,color if strong else SOFT,True)
        # Use the empty outer wing, outside both note rails and drum feedback.
        # These labels are cached; widening a judgment never hides the targets.
        label=state.get('judgment_label','严格节奏')
        windows=state.get('judgment_windows_ms',{'Perfect':110,'Good':220})
        window=round(state.get('hit_window_ms',220))
        together=round(state.get('simultaneous_ms',120))
        self.fitted_text(cr,label,x,h*.735,15*u,wing,SOFT)
        self.fitted_text(cr,'完美 ±%d · 良好 ±%d ms'%(round(windows['Perfect']),round(windows['Good'])),
                         x,h*.790,13*u,wing,SOFT)
        detail=('入拍 ±%d · 合击 %d ms'%(window,together) if state.get('judgment','strict')=='relaxed'
                else '合击间隔 ≤%d ms'%together)
        self.fitted_text(cr,detail,x,h*.842,13*u,wing,SOFT)

    def finale(self,cr,w,h,state,ui,u):
        # Distinct completed scroll, not a failure screen even at zero score.
        cr.set_source_surface(self.finale_background,0,0);cr.paint()
        age=ui.get('finale_age',0.)
        for j in range(30):
            a=j*2.39996+age*.07;r=(.12+(j%7)*.036)*min(w,h)
            x=w*.5+math.cos(a)*r*1.65;y=h*.45+math.sin(a)*r*.75
            circle(cr,x,y,(2+j%3)*u,[(.71,.31,.16),(.13,.43,.40),(.78,.60,.22)][j%3],.45)
        cr.set_source_rgba(.98,.95,.86,.90);cr.rectangle(w*.16,h*.14,w*.68,h*.75);cr.fill()
        cr.set_source_rgba(*COLORS['B'],.52);cr.set_line_width(1.4*u)
        cr.rectangle(w*.17,h*.157,w*.66,h*.714);cr.stroke()
        title='本曲结束' if state.get('practice','full')=='full' else '练习结束'
        self.text(cr,title,w*.5,h*.245,29*u,center=True)
        self.text(cr,'%06d'%state['score'],w*.405,h*.405,50*u,(.68,.25,.12),True)
        self.text(cr,'本曲得分' if state.get('practice','full')=='full' else '本段得分',w*.405,h*.46,15*u,SOFT,True)
        self.seal(cr,state.get('rank','--'),w*.685,h*.365,64*u,u)
        self.text(cr,'准确率 %.1f%%'%state.get('accuracy',0.),w*.685,h*.48,17*u,INK,True)
        self.text(cr,'最高连击  %d'%state['best_combo'],w*.5,h*.56,22*u,center=True)
        counts=state['counts']
        for pos,label,key,color in ((.27,'完美','Perfect',COLORS['B']),(.42,'良好','Good',COLORS['R']),
                                    (.58,'命中','OK',OK_COLOR),(.73,'未命中','Miss',SOFT)):
            self.text(cr,label,w*pos,h*.63,16*u,color,True)
            self.text(cr,str(counts.get(key,0)),w*pos,h*.698,26*u,color,True)
        challenges=state.get('challenges',[]);wins=sum(c.get('status')=='complete' for c in challenges)
        label='乐句挑战 %d / %d  ·  奖励 +%d'%(wins,len(challenges),state.get('bonus_score',0)) if challenges else '本曲结束'
        self.fitted_text(cr,label,w*.5,h*.785,18*u,w*.61,INK,True)
        judgment=state.get('judgment_label','严格节奏')
        window=round(state.get('hit_window_ms',220));together=round(state.get('simultaneous_ms',120))
        self.fitted_text(cr,'%s · 命中 ±%d ms · 合击 %d ms'%(judgment,window,together),
                         w*.5,h*.840,13*u,w*.61,SOFT,True)
        self.text(cr,'本曲结束',w*.5,h*.936,17*u,INK,True)
