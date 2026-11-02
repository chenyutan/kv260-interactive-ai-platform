"""Juice, ice, golden-double and explosion feedback for the native fruit game.

Every effect is presentation-only. Particle counts, cached pixels and lifetimes
are bounded, and immutable tiles are rasterized at the final device scale.
There is no camera, file/network IO, shader, or per-frame full-screen blending.
"""
from collections import OrderedDict
import math
import cairo

W,H = 1280,720
JUICE_COLORS = {
    'peach':(.93,.28,.35), 'mandarin':(1.,.43,.055),
    'watermelon':(.91,.12,.23), 'apple':(.81,.16,.08),
    'strawberry':(.85,.08,.28), 'dragonfruit':(.73,.08,.54),
}
COLORS = {'double':(1.,.72,.10),'ice':(.15,.68,1.),'bomb':(1.,.38,.075)}
MAX_JUICE_HITS = 8
DROPS_PER_HIT = 8
MAX_PARTICLES = MAX_JUICE_HITS * DROPS_PER_HIT
MAX_ITEM_HITS = 3
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_JUICE_CACHE_BYTES = 24 * 1024 * 1024
MAX_BACKGROUND_BYTES = 16 * 1024 * 1024
SPLASH_LIFETIME = .72
BURST_FRAMES = 16


def clamp(value,lo=0.,hi=1.):
    return max(lo,min(hi,value))


def star(c,x,y,radius,points=4,angle=0.):
    for i in range(points*2):
        a=angle+i*math.pi/points
        r=radius if i%2==0 else radius*.22
        px,py=x+math.cos(a)*r,y+math.sin(a)*r
        (c.move_to if i==0 else c.line_to)(px,py)
    c.close_path()


class TileCache:
    """Keep a finite byte budget and 192 immutable, final-scale effect tiles."""
    def __init__(self,limit=MAX_CACHE_BYTES):
        self.limit=limit;self.scale=None;self.tiles=OrderedDict();self.bytes=0;self.allocations=0
        self.frame=None;self.regions=None
        try:
            from sword.alpha_blit import AlphaBlitter
            self.blitter=AlphaBlitter()
        except ImportError:
            self.blitter=None
    def prepare(self,scale):
        scale=round(float(scale),5)
        if self.scale!=scale:
            self.scale=scale;self.tiles.clear();self.bytes=0
            if self.blitter:self.blitter.clear_sources()
    def get(self,key,width,height,paint):
        if key in self.tiles:
            self.tiles.move_to_end(key);return self.tiles[key]
        pw,ph=max(1,math.ceil(width*self.scale)),max(1,math.ceil(height*self.scale))
        tile=cairo.ImageSurface(cairo.FORMAT_ARGB32,pw,ph)
        tc=cairo.Context(tile);tc.scale(self.scale,self.scale)
        paint(tc);tile.flush()
        self.allocations+=1;self.tiles[key]=tile
        self.bytes+=tile.get_stride()*tile.get_height()
        while self.bytes>self.limit or len(self.tiles)>192:
            _,old=self.tiles.popitem(last=False)
            self.bytes-=old.get_stride()*old.get_height()
        return tile
    def blit(self,c,tile,x,y,alpha=1.):
        dx,dy=c.user_to_device(x,y);left,top=round(dx),round(dy)
        if (alpha>=.999 and self.frame is not None and c.get_target()==self.frame.target
                and self.frame.paint_tile(tile,left,top,regions=self.regions)):return
        c.save();c.identity_matrix()
        c.rectangle(left,top,tile.get_width(),tile.get_height());c.clip()
        if not self.blitter or not self.blitter.paint(c,tile,left,top,alpha):
            c.set_source_surface(tile,left,top)
            if alpha>=.999:c.paint()
            else:c.paint_with_alpha(clamp(alpha))
        c.restore()
    def blit_many(self,c,tiles):
        frame=self.frame
        if (frame is not None and c.get_target()==frame.target and
                self.regions==[(0.,0.,float(frame.width),float(frame.height))]):
            batch=[]
            for tile,x,y in tiles:
                dx,dy=c.user_to_device(x,y);batch.append((tile,round(dx),round(dy)))
            if frame.paint_tiles(batch):return
        for tile,x,y in tiles:self.blit(c,tile,x,y)


class CutEffects:
    def __init__(self):
        self.items=[];self.cache=TileCache(MAX_JUICE_CACHE_BYTES);self.boxes={}
    def clear(self):
        self.items.clear()
    def prepare(self,scale):
        self.cache.prepare(scale)
        # Small droplets are all prewarmed outside play. Splash frames are
        # shared by all hits of the same fruit, rather than one surface per hit.
        for fruit in JUICE_COLORS:
            for size in range(3):
                self._drop(fruit,size)
            for frame in range(8):
                self._splash(fruit,frame)
            for frame in range(BURST_FRAMES):
                self._burst(fruit,frame)
    def trigger(self,event):
        if event['kind']!='normal':return
        fruit=event.get('fruit','watermelon')
        if fruit not in JUICE_COLORS:fruit='watermelon'
        angle=event.get('cut_angle',0.)
        self.items.append(dict(x=event['x'],y=event['y'],fruit=fruit,
            angle=angle,age=0.,combo=event.get('combo',1)))
        del self.items[:-MAX_JUICE_HITS]
    def advance(self,dt):
        for item in self.items:item['age']+=max(0.,dt)
        self.items[:]=[item for item in self.items if item['age']<SPLASH_LIFETIME]
    def bounds(self):
        result=[]
        for v in self.items:
            frame=min(BURST_FRAMES-1,int(v['age']/SPLASH_LIFETIME*BURST_FRAMES))
            x,y,w,h=self._burst_box(frame);result.append((v['x']+x,v['y']+y,w,h))
        return result
    def _drop(self,fruit,size):
        rgb=JUICE_COLORS[fruit];r=3.+size*1.6
        def paint(c):
            c.translate(12,12)
            c.move_to(-r,0);c.curve_to(-r,-r,r,-r,r*1.65,-r*.12)
            c.curve_to(r*.9,r*.78,-r,r*.85,-r,0)
            c.set_source_rgba(*(v*.61 for v in rgb),.93);c.fill_preserve()
            c.set_source_rgba(*rgb,.95);c.set_line_width(1.6);c.stroke()
            c.set_source_rgba(1.,.96,.73,.79);c.arc(-r*.24,-r*.29,max(.6,r*.24),0,math.tau);c.fill()
        return self.cache.get(('drop',fruit,size),24,24,paint)
    def _splash(self,fruit,frame):
        color=JUICE_COLORS[fruit];t=(frame+.5)/8
        radius=25+42*math.sin(min(1,t*1.6)*math.pi*.5)
        fade=clamp((1-t)*1.8)
        def paint(c):
            c.translate(112,108)
            # Fluid lobes and brush edges retain the ink-paper setting while
            # clearly reading as fruit juice on a light background.
            for layer,rf,alpha in ((0,1.,.24),(1,.84,.75)):
                points=[]
                for i in range(18):
                    a=i*math.tau/18+.12
                    rr=radius*rf*((.78,1.15,.85,.61,1.28,.76,.96)[i%7])
                    px=math.cos(a)*rr*1.18;py=math.sin(a)*rr*.77
                    points.append((px,py))
                start=((points[-1][0]+points[0][0])/2,(points[-1][1]+points[0][1])/2)
                c.move_to(*start)
                for i,point in enumerate(points):
                    nxt=points[(i+1)%len(points)]
                    end=((point[0]+nxt[0])/2,(point[1]+nxt[1])/2)
                    c.curve_to(start[0]+2*(point[0]-start[0])/3,start[1]+2*(point[1]-start[1])/3,
                        end[0]+2*(point[0]-end[0])/3,end[1]+2*(point[1]-end[1])/3,*end)
                    start=end
                c.close_path();c.set_source_rgba(*color,alpha*fade);c.fill()
            c.set_source_rgba(*(v*.5 for v in color),.4*fade);c.set_line_width(1.3)
            c.move_to(-radius*.7,1);c.curve_to(-20,-12,22,13,radius*.8,-2);c.stroke()
            c.set_source_rgba(1.,.98,.75,.83*fade);c.set_line_width(2.2)
            c.move_to(-radius*.44,-6);c.curve_to(-10,-12,14,-3,radius*.52,-7);c.stroke()
        return self.cache.get(('splash',fruit,frame),224,216,paint)
    def _burst_box(self,frame):
        if frame in self.boxes:return self.boxes[frame]
        t=(frame+.5)/BURST_FRAMES*SPLASH_LIFETIME
        splash_frame=min(7,int(frame/BURST_FRAMES*8));st=(splash_frame+.5)/8
        radius=25+42*math.sin(min(1,st*1.6)*math.pi*.5)
        points=[]
        for i in range(18):
            a=i*math.tau/18+.12;rr=radius*((.78,1.15,.85,.61,1.28,.76,.96)[i%7])
            points.append((math.cos(a)*rr*1.18,math.sin(a)*rr*.77))
        for i in range(DROPS_PER_HIT):
            a=i*2.399963+.31;speed=120+(i%3)*54
            dx,dy=math.cos(a)*speed*t,math.sin(a)*speed*t*.77+86*t*t
            points.extend(((dx-12,dy-12),(dx+12,dy+12)))
        left,top=math.floor(min(x for x,y in points))-2,math.floor(min(y for x,y in points))-2
        right,bottom=math.ceil(max(x for x,y in points))+2,math.ceil(max(y for x,y in points))+2
        box=left,top,right-left,bottom-top;self.boxes[frame]=box;return box
    def _burst(self,fruit,frame):
        # One cache tile includes the splash and every flying droplet. The
        # earlier per-droplet draw path cost 64 Python/ctypes calls in an ARM
        # dense frame; aggregate tiles retain all droplets with eight calls.
        t=(frame+.5)/BURST_FRAMES*SPLASH_LIFETIME
        left,top,width,height=self._burst_box(frame)
        splash_frame=min(7,int(frame/BURST_FRAMES*8))
        def paint(c):
            self.cache.blit(c,self._splash(fruit,splash_frame),-left-112,-top-108)
            fade=clamp((SPLASH_LIFETIME-t)*4)
            for i in range(DROPS_PER_HIT):
                a=i*2.399963+.31;speed=120+(i%3)*54
                dx=math.cos(a)*speed*t;dy=math.sin(a)*speed*t*.77+86*t*t
                self.cache.blit(c,self._drop(fruit,i%3),-left+dx-12,-top+dy-12,fade)
        return self.cache.get(('burst',fruit,frame),width,height,paint)
    def paint(self,c):
        if self.cache.scale is None:self.prepare(abs(c.get_matrix().xx) or 1.)
        tiles=[]
        for item in self.items:
            x,y,t=item['x'],item['y'],item['age'];color=item['fruit']
            frame=min(BURST_FRAMES-1,int(t/SPLASH_LIFETIME*BURST_FRAMES))
            left,top,_,_=self._burst_box(frame)
            tiles.append((self._burst(color,frame),x+left,y+top))
        self.cache.blit_many(c,tiles)


class ItemEffects:
    def __init__(self):
        self.items=[];self.cache=TileCache();self.freeze=False;self.double=False;self.clock=0.
        self.backgrounds=OrderedDict();self.background_bytes=0;self.background_allocations=0
    def clear(self):
        self.items.clear();self.freeze=self.double=False
    def prepare(self,scale):
        self.cache.prepare(scale)
        for kind in COLORS:
            for frame in range(12):self._tile(kind,frame)
        for phase in range(6):self._twinkle(phase)
        # All normal bomb deltas are integers in [-30, 0]. At the default
        # 960x540 cap, keep these and the two static labels alongside every
        # animation frame, so the first special hit never rebuilds a tile.
        # Larger explicitly uncapped scales cannot fit the complete warm set;
        # skip extra banner warming there instead of evicting hit animations.
        s=self.cache.scale
        tile_bytes=lambda w,h:math.ceil(w*s)*math.ceil(h*s)*4
        warm_bytes=(36*tile_bytes(392,392)+6*tile_bytes(28,28)
                    +33*tile_bytes(480,78))
        if warm_bytes<=self.cache.limit:
            self._banner('double',0);self._banner('ice',0)
            for delta in range(-30,1):self._banner('bomb',delta)
    def sync(self,game):
        self.freeze=game.state in ('playing','paused') and game.elapsed<game.freeze_until
        self.double=game.state in ('playing','paused') and game.elapsed<game.double_until
        self.clock=game.elapsed
    def background_key(self):
        return bool(self.freeze),bool(self.double)
    def prepare_backgrounds(self,source,width,height):
        self.backgrounds.clear();self.background_bytes=0
        for key in ((True,False),(False,True),(True,True)):
            self.background(source,width,height,key=key)
    def trigger(self,event):
        kind=event['kind']
        if kind not in COLORS:return
        self.items[:]=[v for v in self.items if v['kind']!=kind]
        self.items.append(dict(kind=kind,x=event['x'],y=event['y'],
            delta=event.get('delta',0),age=0.,duration=1.05 if kind=='bomb' else 1.35))
        del self.items[:-MAX_ITEM_HITS]
    def advance(self,dt):
        for item in self.items:item['age']+=max(0.,dt)
        self.items[:]=[v for v in self.items if v['age']<v['duration']]
    def bounds(self):
        result=[(v['x']-196,v['y']-196,392,392) for v in self.items]
        if self.double:result.extend((x-14,y-14,28,28) for x,y in self.twinkle_positions())
        return result+[(390,82,500,92)] if result else []
    def banner_key(self):
        if not self.items:return None
        v=next((v for v in reversed(self.items) if v['kind']=='bomb'),self.items[-1])
        return (id(v),v['kind'],v['delta'])
    def background(self,source,width,height,key=None):
        """One opaque cached background for each persistent item combination."""
        key=self.background_key() if key is None else key
        if key==(False,False):return source
        def paint(c):
            c.identity_matrix();c.set_source_surface(source,0,0);c.paint()
            if key[0]:
                c.set_source_rgba(.10,.42,.98,.25);c.paint()
                # The entire screen is unmistakably blue. Frost lives only at
                # the border, leaving fruit colors and incoming bombs readable.
                grad=cairo.LinearGradient(0,0,width,0)
                for at,alpha in ((0,.63),(.1,.16),(.5,0.),(.9,.16),(1.,.63)):
                    grad.add_color_stop_rgba(at,.12,.59,1.,alpha)
                c.set_source(grad);c.paint()
                c.set_source_rgba(.70,.91,1.,.8);c.set_line_width(2.)
                for i in range(28):
                    px=i*width/27;py=(i%3)*10
                    for sy in (py,height-py):
                        c.move_to(px-12,sy);c.line_to(px+12,sy)
                        c.move_to(px,sy-13);c.line_to(px,sy+13)
                        c.move_to(px-8,sy-8);c.line_to(px+8,sy+8)
                c.stroke()
            if key[1]:
                grad=cairo.LinearGradient(0,0,0,height)
                for at,alpha in ((0,.43),(.16,.08),(.5,0.),(.85,.05),(1.,.36)):
                    grad.add_color_stop_rgba(at,1.,.66,.025,alpha)
                c.set_source(grad);c.paint()
                for i in range(24):
                    px=(i*151+43)%width;py=14+(i%4)*13 if i%2 else height-14-(i%4)*13
                    star(c,px,py,5+i%5,angle=.25)
                c.set_source_rgba(1.,.91,.36,.9);c.fill()
        # Backgrounds have an independent budget. Warming three opaque themes
        # must never evict the pre-rasterized hit animations from the tile cache.
        cache_key=('background',key,width,height,id(source))
        if cache_key not in self.backgrounds:
            tile=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
            paint(cairo.Context(tile));tile.flush()
            self.backgrounds[cache_key]=tile;self.background_bytes+=tile.get_stride()*height
            self.background_allocations+=1
            while (self.background_bytes>MAX_BACKGROUND_BYTES or len(self.backgrounds)>3) and len(self.backgrounds)>1:
                _,old=self.backgrounds.popitem(last=False);self.background_bytes-=old.get_stride()*old.get_height()
            # Very large explicitly uncapped displays may exceed this cache's
            # budget as a single frame. Return the tile without retaining it.
            if self.background_bytes>MAX_BACKGROUND_BYTES:
                self.backgrounds.pop(cache_key);self.background_bytes=0
                return tile
        else:self.backgrounds.move_to_end(cache_key)
        return self.backgrounds[cache_key]
    def _tile(self,kind,frame):
        t=(frame+.45)/12;fade=clamp((1-t)*2.3)
        def paint(c):
            c.translate(196,196)
            if kind=='bomb':
                # Single expanding orange fireball, charcoal smoke and bright
                # fragments. No oscillating full-frame flashes or camera shake.
                r=38+119*math.sqrt(t)
                for i in range(9):
                    a=i*2.399963;rr=(45+65*t)*(1+i%3*.13)
                    c.arc(math.cos(a)*rr,math.sin(a)*rr,17+25*t,0,math.tau)
                c.set_source_rgba(.12,.12,.13,.58*fade);c.fill()
                grad=cairo.RadialGradient(0,0,0,0,0,r)
                grad.add_color_stop_rgba(0,1.,.98,.71,.97*fade)
                grad.add_color_stop_rgba(.26,1.,.66,.08,.9*fade)
                grad.add_color_stop_rgba(.74,.91,.16,.018,.68*fade)
                grad.add_color_stop_rgba(1,.25,.075,.015,0.)
                c.set_source(grad);c.arc(0,0,r,0,math.tau);c.fill()
                c.set_source_rgba(1.,.82,.23,.92*fade);c.set_line_width(3.)
                for i in range(12):
                    a=i*math.tau/12+.15;ri=r*.88;ro=r*1.14
                    c.move_to(math.cos(a)*ri,math.sin(a)*ri)
                    c.line_to(math.cos(a)*ro,math.sin(a)*ro)
                c.stroke()
            elif kind=='double':
                r=34+125*t
                grad=cairo.RadialGradient(0,0,0,0,0,r)
                grad.add_color_stop_rgba(0,1.,.94,.42,.56*fade)
                grad.add_color_stop_rgba(.55,1.,.64,.08,.24*fade)
                grad.add_color_stop_rgba(1,1.,.63,0,0)
                c.set_source(grad);c.arc(0,0,r,0,math.tau);c.fill()
                for i in range(12):
                    a=i*math.tau/12;dist=r*(.63 if i%2 else .95)
                    star(c,math.cos(a)*dist,math.sin(a)*dist,7+i%3*3,angle=a+t)
                c.set_source_rgba(1.,.84,.17,.98*fade);c.fill()
                c.set_source_rgba(1.,.99,.70,.82*fade);c.set_line_width(2.4)
                c.arc(0,0,r*.79,0,math.tau);c.stroke()
            else:
                r=35+115*t
                c.set_source_rgba(.13,.62,1.,.72*fade);c.set_line_width(3.5)
                c.arc(0,0,r,0,math.tau);c.stroke()
                for i in range(9):
                    a=i*math.tau/9;px,py=math.cos(a)*r,math.sin(a)*r
                    for j in range(3):
                        b=j*math.pi/3;dx,dy=math.cos(b)*11,math.sin(b)*11
                        c.move_to(px-dx,py-dy);c.line_to(px+dx,py+dy)
                c.set_source_rgba(.86,.97,1.,.92*fade);c.set_line_width(2.2);c.stroke()
            if t<.86:
                label='×2' if kind=='double' else '冰' if kind=='ice' else '爆'
                c.select_font_face('Noto Serif CJK SC',0,1);c.set_font_size(51)
                ext=c.text_extents(label);c.move_to(-ext[2]/2-ext[0],18)
                c.text_path(label);c.set_line_join(cairo.LINE_JOIN_ROUND)
                c.set_line_width(4.5);c.set_source_rgba(.13,.19,.23,.86*fade);c.stroke_preserve()
                c.set_source_rgba(1.,.96,.71,.99*fade);c.fill()
        return self.cache.get(('item',kind,frame),392,392,paint)
    @staticmethod
    def twinkle_positions():
        return ((26,138),(44,312),(28,554),(1252,182),(1232,402),
            (1248,592),(298,22),(986,696))
    def _twinkle(self,phase):
        brightness=(.17,.45,.78,1.,.7,.32)[phase]
        def paint(c):
            star(c,14,14,11,angle=.17)
            c.set_source_rgba(1.,.78,.10,brightness);c.fill()
            star(c,14,14,6,angle=.17)
            c.set_source_rgba(1.,.98,.74,brightness);c.fill()
        return self.cache.get(('twinkle',phase),28,28,paint)
    def _banner(self,kind,delta=0):
        # Ice/gold labels do not contain score deltas. Share their exact tile
        # across combo values; bomb labels retain their displayed score.
        key=('banner',kind,delta if kind=='bomb' else 0)
        label={'double':'金梨 ×2  ·  得分翻倍','ice':'冰封全场  ·  水果减速','bomb':f'炸弹爆发  {delta} 分'}[kind]
        def paint(c):
            c.set_source_rgba(.08,.15,.18,.94);c.rectangle(0,0,480,78);c.fill()
            c.set_source_rgba(*COLORS[kind],.98);c.set_line_width(3.)
            c.rectangle(3,3,474,72);c.stroke()
            c.select_font_face('Noto Serif CJK SC',0,1);c.set_font_size(32)
            ext=c.text_extents(label);c.move_to(240-ext[2]/2-ext[0],51)
            c.set_source_rgba(1.,.95,.71,1.);c.show_text(label)
        return self.cache.get(key,480,78,paint)
    def paint(self,c):
        if self.cache.scale is None:self.prepare(abs(c.get_matrix().xx) or 1.)
        # Preserve exact OVER order. Batch only existing immutable tiles;
        # blit_many retains the original partial-clip and Cairo fallbacks.
        tiles=[]
        if self.double:
            phase=int(self.clock*8)
            for i,(x,y) in enumerate(self.twinkle_positions()):
                tiles.append((self._twinkle((phase+i*2)%6),x-14,y-14))
        for v in self.items:
            frame=min(11,int(v['age']/v['duration']*12))
            tiles.append((self._tile(v['kind'],frame),v['x']-196,v['y']-196))
        if self.items:
            v=next((v for v in reversed(self.items) if v['kind']=='bomb'),self.items[-1])
            tiles.append((self._banner(v['kind'],v['delta']),400,88))
        self.cache.blit_many(c,tiles)
