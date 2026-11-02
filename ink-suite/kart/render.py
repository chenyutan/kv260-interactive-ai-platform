"""Single-swap GLES2 bridge, cached Chinese HUD and an explicit Cairo fallback."""
from collections import OrderedDict,deque
import ctypes as C
import json
import math
import os
from pathlib import Path
import time
try:
    import cairo
except ImportError:
    import cairocffi as cairo
from .engine import TRACK,ITEM_NAMES,clamp,drift_tier

W,H=960,540
INK=(.08,.16,.18);CREAM=(.98,.94,.82);GOLD=(1.,.76,.31)
SKINS=((.14,.69,.64),(.30,.46,.93),(1.,.70,.17),(.96,.29,.22),(.72,.36,.86),(.50,.74,.22))

def poly(cr,points,color):
    cr.move_to(*points[0])
    for p in points[1:]:cr.line_to(*p)
    cr.close_path();cr.set_source_rgba(*(color if len(color)==4 else (*color,1)));cr.fill()
def round_rect(cr,x,y,w,h,r,color):
    cr.new_sub_path()
    for a,b,start in ((x+w-r,y+r,-math.pi/2),(x+w-r,y+h-r,0),(x+r,y+h-r,math.pi/2),(x+r,y+r,math.pi)):
        cr.arc(a,b,r,start,start+math.pi/2)
    cr.close_path();cr.set_source_rgba(*color);cr.fill()

class TextCache:
    def __init__(self):self.items=OrderedDict();self.builds=0
    def _get(self,text,size,color,bold):
        text=str(text);key=text,size,color,bold
        if key not in self.items:
            scratch=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
            scratch.select_font_face('Noto Sans CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
            scratch.set_font_size(size)
            e=scratch.text_extents(text);width=max(1,math.ceil(e[4])+6);height=math.ceil(size*1.6)
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height);p=cairo.Context(surface)
            p.select_font_face('Noto Sans CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
            p.set_font_size(size);p.set_source_rgb(*color);p.move_to(3,size*1.12);p.show_text(text)
            self.items[key]=(surface,e[4]);self.builds+=1
            if len(self.items)>320:self.items.popitem(last=False)
        result=self.items[key];self.items.move_to_end(key);return result
    def draw(self,cr,text,x,y,size,color=CREAM,bold=False,align='left'):
        text=str(text)
        # Race clocks/speed values use finite reusable glyphs. A new tenth of
        # a second must not open a font and allocate a fresh full-string image.
        numeric=len(text)>1 and all(c in '0123456789:./ ' for c in text)
        parts=[self._get(c,size,color,bold) for c in text] if numeric else [self._get(text,size,color,bold)]
        width=sum(advance for _,advance in parts)+6 if numeric else parts[0][0].get_width()
        if align=='center':x-=width/2
        elif align=='right':x-=width
        for surface,advance in parts:
            cr.set_source_surface(surface,x-3,y-size*1.12);cr.paint();x+=advance

class Hud:
    def __init__(self):
        self.surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,W,H);self.text=TextCache()
        self.last=-1.;self.key=None;self.state=None;self.builds=0;self.full=True;self.overlay=None;self.parts={}
        self.partial=os.environ.get('INK_KART_HUD_DIRTY','1')=='1';self._map_background=None;self._draw_parts={}
        self._running_base=None;self.running_base_builds=0
        self.map_points=[(840+p[0]*.36,462+p[2]*.36) for p in TRACK.points]
    def update(self,snapshot,force=False):
        s=snapshot;now=time.monotonic();ui=s.get('ui',{});state=s['state']
        frozen=state in ('title','finished','paused','countdown') or ui.get('tracking_paused') or ui.get('notice')
        overlay=(state, bool(ui.get('tracking_paused')), bool(ui.get('notice')))
        player=s['karts'][0]
        parts=dict(rank=s['rank'],lap_time=(s['lap'],round(s['elapsed'],1)),progress=s['progress'],
                   notice=s.get('notice',''),speed=(round(s['speed']),round(player['boost'],1),bool(player['drift']),int(player['drift_charge']*8)),
                   item=(s['item'],math.ceil(player['shield'])),
                   minimap=tuple((r['s'],r['id']) for r in s['karts']))
        key=(state,tuple(parts.values()),math.ceil(s['countdown']),ui.get('notice'),ui.get('control_mode'),ui.get('tracking_paused'))
        if not force and (key==self.key or now-self.last<.10):return None
        full=force or self.state!=state or bool(frozen) or self.full or overlay!=self.overlay
        regions=dict(rank=(16,14,164,88),lap_time=(740,14,204,88),progress=(270,20,420,27),
                     notice=(210,105,540,68),speed=(16,410,180,116),
                     item=(345,450,410,78),minimap=(758,396,186,134))
        dirty=[regions[name] for name,value in parts.items() if value!=self.parts.get(name)]
        # The existing redraw key/cadence is retained. At an accepted update,
        # continuous drift fill and minute formatting may change even if the
        # old quantized parts are equal; repaint their existing rectangles.
        draw_parts=dict(lap_time=(int(s['elapsed']//60), '%04.1f'%(s['elapsed']%60)),
                        speed=(player['drift_charge'] if player['drift'] else None, player['boost']>0))
        if self.partial:
            for name,value in draw_parts.items():
                if value!=self._draw_parts.get(name) and regions[name] not in dirty:dirty.append(regions[name])
        cr=cairo.Context(self.surface)
        if self.partial and not full:
            for x,y,w,h in dirty:cr.rectangle(x,y,w,h)
            cr.clip()
        cr.set_operator(cairo.OPERATOR_CLEAR);cr.paint();cr.set_operator(cairo.OPERATOR_OVER)
        self.draw(cr,s);self.surface.flush();self.key=key;self.last=now;self.state=state;self.builds+=1;self.full=False;self.overlay=overlay;self.parts=parts;self._draw_parts=draw_parts
        return [(0,0,W,H)] if full else dirty
    def draw(self,cr,s):
        text=self.text.draw;state=s['state'];ui=s.get('ui',{})
        cached=self.partial and state=='running' and not s.get('notice') and not ui.get('notice') and not ui.get('tracking_paused')
        if cached:self._paint_running_base(cr)
        if state not in ('title','finished'):
            if not cached:round_rect(cr,22,20,150,74,14,(*INK,.88))
            text(cr,str(s['rank']),48,77,47,GOLD,True)
            if not cached:text(cr,'/ 6',91,75,23,CREAM)
            if not cached:text(cr,'名次',109,45,13,(.70,.82,.75))
            if not cached:round_rect(cr,748,20,190,74,14,(*INK,.88))
            text(cr,f"第 {s['lap']} 圈 / 3",922,53,23,CREAM,True,'right')
            text(cr,'%02d:%04.1f'%(int(s['elapsed']//60),s['elapsed']%60),922,80,20,GOLD,False,'right')
            # Finite progress and the three lap checkpoints.
            if not cached:round_rect(cr,278,27,404,11,5,(*INK,.48))
            progress=clamp(s['progress'],0,1)
            if progress>0:round_rect(cr,280,29,max(4,400*progress),7,3,(*GOLD,.98))
            for x in (413,546):
                cr.set_source_rgba(*CREAM,.7);cr.rectangle(x,26,2,13);cr.fill()
            if s.get('notice'):text(cr,s['notice'],480,143,25,GOLD,True,'center')
            if not cached:round_rect(cr,22,434,167,84,14,(*INK,.86))
            text(cr,'%03d'%round(s['speed']),42,490,43,CREAM,True)
            if not cached:text(cr,'km/h',140,492,16,(.70,.82,.75))
            player=s['karts'][0]
            charge=player['drift_charge'];tier=drift_tier(charge)
            if player['drift']:
                colors=((.32,.79,1.),(1.,.75,.25),(.91,.43,1.))
                for i in range(3):
                    round_rect(cr,25+i*55,416,49,8,3,(*INK,.60))
                    fill=clamp((charge-(0.,.65,1.25)[i])/(.65,.60,.70)[i],0,1)
                    if fill:round_rect(cr,25+i*55,416,max(3,49*fill),8,3,(*colors[i],.98))
            if player['boost']>0:text(cr,'加速中',105,513,14,GOLD,True,'center')
            elif player['drift']:
                text(cr,('漂移蓄力','蓝焰 · 松开加速','金焰 · 松开加速','紫焰 · 松开加速')[tier],105,513,13,((.36,.86,.98),(.36,.86,.98),GOLD,(.91,.43,1.))[tier],True,'center')
            if not cached:round_rect(cr,354,458,252,60,15,(*INK,.88))
            item=s.get('item','')
            col=GOLD if item=='boost' else (.35,.84,.94) if item=='shield' else (.98,.40,.29)
            if item:
                self.item_icon(cr,383,488,item,col)
                text(cr,ITEM_NAMES[item],415,495,23,CREAM,True)
            else:
                text(cr,'选择前方彩色道具',480,494,18,(.71,.80,.73),False,'center')
            if player['shield']>0:text(cr,'护盾 %d秒'%math.ceil(player['shield']),612,505,17,(.41,.88,.97),True)
            self.minimap(cr,s)
        if state=='title':
            round_rect(cr,270,147,420,245,25,(*INK,.89))
            text(cr,'赛车',480,217,56,CREAM,True,'center')
            text(cr,'山水环线',480,253,22,GOLD,False,'center')
            text(cr,'3 圈  ·  6 位车手  ·  道具竞速',480,297,19,(.73,.84,.76),False,'center')
            prompt='按 A 开始' if ui.get('control_mode')=='gamepad' else '双手举到肩上，开始比赛'
            if ui.get('control_mode')=='keyboard':prompt='按回车开始'
            text(cr,prompt,480,350,22,CREAM,True,'center')
        if state=='countdown':
            number=str(max(1,math.ceil(s['countdown'])))
            # Countdown owns a full transparent texture until racing starts.
            round_rect(cr,420,199,120,123,30,(*INK,.78));text(cr,number,480,288,82,GOLD,True,'center')
        if state=='finished':
            round_rect(cr,228,79,504,383,25,(*INK,.94))
            text(cr,'比赛完成',480,130,31,CREAM,True,'center')
            text(cr,'第 %d 名'%s['rank'],480,202,55,GOLD,True,'center')
            text(cr,'用时  %02d:%05.2f'%(int(s['elapsed']//60),s['elapsed']%60),480,249,25,CREAM,False,'center')
            if s.get('best_lap'):text(cr,'最快圈  %.2f 秒'%s['best_lap'],480,284,20,(.72,.84,.76),False,'center')
            text(cr,'积分  %d'%s['score'],480,321,24,CREAM,True,'center')
            for i,t in enumerate(s.get('lap_times',[])[:3]):text(cr,'第%d圈 %.2f秒'%(i+1,t),480,350+i*23,16,(.71,.82,.75),False,'center')
            prompt='按 A 再赛一场' if ui.get('control_mode')=='gamepad' else '双手举高，再赛一场'
            if ui.get('control_mode')=='keyboard':prompt='按回车再赛一场'
            text(cr,prompt,480,435,21,GOLD,True,'center')
        notice=ui.get('notice','')
        if ui.get('tracking_paused') or state=='paused':
            round_rect(cr,250,211,460,120,20,(*INK,.91))
            text(cr,'等待控制恢复' if ui.get('tracking_paused') else '已暂停',480,259,30,CREAM,True,'center')
            if notice:text(cr,notice,480,302,17,(.78,.86,.79),False,'center')
        elif notice:
            round_rect(cr,240,395,480,40,12,(*INK,.87));text(cr,notice,480,424,19,CREAM,False,'center')
    def _paint_running_base(self,cr):
        if self._running_base is None:
            # Original device coordinates and order preserve Cairo coverage.
            # The first rank panel is drawn on a clear destination exactly as
            # in draw(); every later panel sees a non-clear destination.
            base=cairo.ImageSurface(cairo.FORMAT_ARGB32,W,H);p=cairo.Context(base);text=self.text.draw
            round_rect(p,22,20,150,74,14,(*INK,.88))
            text(p,'/ 6',91,75,23,CREAM)
            text(p,'名次',109,45,13,(.70,.82,.75))
            round_rect(p,748,20,190,74,14,(*INK,.88))
            round_rect(p,278,27,404,11,5,(*INK,.48))
            round_rect(p,22,434,167,84,14,(*INK,.86))
            text(p,'km/h',140,492,16,(.70,.82,.75))
            round_rect(p,354,458,252,60,15,(*INK,.88))
            base.flush();self._running_base=base;self.running_base_builds+=1
        cr.set_source_surface(self._running_base,0,0);cr.paint()
    def item_icon(self,cr,x,y,item,col):
        if item=='boost':poly(cr,[(x-2,y-18),(x-13,y+2),(x-2,y+2),(x-7,y+18),(x+14,y-6),(x+4,y-6)],col)
        elif item=='shield':poly(cr,[(x-14,y-13),(x,y-19),(x+14,y-13),(x+11,y+7),(x,y+18),(x-11,y+7)],col)
        else:
            cr.set_source_rgb(*col);cr.arc(x,y,12,0,math.tau);cr.fill();poly(cr,[(x-5,y+7),(x-22,y+18),(x-13,y-3)],GOLD)
    def _draw_map_background(self,cr):
        round_rect(cr,764,402,174,122,14,(*INK,.78))
        cr.set_source_rgba(.82,.88,.74,.5);cr.set_line_width(5);cr.move_to(*self.map_points[0])
        for p in self.map_points[1:]:cr.line_to(*p)
        cr.stroke()
    def minimap(self,cr,s):
        if self.partial:
            if self._map_background is None:
                self._map_background=cairo.ImageSurface(cairo.FORMAT_ARGB32,186,134)
                # Render once at the original device coordinates. Translating
                # curved paths before rasterization can alter edge rounding.
                scratch=cairo.ImageSurface(cairo.FORMAT_ARGB32,W,H)
                bg_source=cairo.Context(scratch)
                # Match the non-empty HUD destination's Cairo OVER rounding.
                # The seed lies outside the cached crop and is never displayed.
                bg_source.set_source_rgb(0,0,0);bg_source.rectangle(0,0,1,1);bg_source.fill()
                self._draw_map_background(bg_source);scratch.flush()
                bg=cairo.Context(self._map_background);bg.set_operator(cairo.OPERATOR_SOURCE)
                bg.set_source_surface(scratch,-758,-396);bg.paint();self._map_background.flush()
            cr.set_source_surface(self._map_background,758,396);cr.paint()
        else:self._draw_map_background(cr)
        for r in reversed(s['karts']):
            (x,_,z),_=TRACK.sample(r['s']);px,py=840+x*.36,462+z*.36
            cr.set_source_rgb(*(GOLD if r['id']==0 else SKINS[r['id']]))
            cr.arc(px,py,4.5 if r['id']==0 else 3,0,math.tau);cr.fill()

class Frame(C.Structure):
    _fields_=[(name,C.c_float) for name in ('s','x','speed','clock','steer','boost','shield','drift','slow','countdown')]+[('state',C.c_int)]
class Car(C.Structure):
    _fields_=[(name,C.c_float) for name in ('s','x','speed','steer','boost','shield','drift','slow')]+[('skin',C.c_int)]
class Object(C.Structure):_fields_=[('s',C.c_float),('x',C.c_float),('kind',C.c_int)]

class Renderer:
    def __init__(self,on_status=None):
        self.on_status=on_status;self.library=None;self.context=None;self.size=(W,H)
        self.hud=Hud();self.frames=0;self.hud_uploads=0;self.hud_bytes=0;self.native_ms=deque(maxlen=300)
        self.hud_prepare_ms=deque(maxlen=300);self.hud_upload_ms=deque(maxlen=300)
        self.world_ms=deque(maxlen=300);self.present_ms=deque(maxlen=300)
        self.effects=[];self.latest_snapshot=None
        self.reason='not-attached';self.info='';self.accelerated=False;self.error=C.create_string_buffer(2048)
        self._frame=Frame();self._cars=(Car*6)();self._objects=(Object*96)();self._fallback=None
        if os.environ.get('INK_KART_RENDERER')=='cairo':self.reason='explicit-cairo';return
        try:
            self.library=C.CDLL(str(Path(__file__).with_name('native')/'libracer3d.so'));self._bind()
        except (OSError,AttributeError,RuntimeError) as error:
            self.library=None;self.reason='native-unavailable: '+str(error)
    def _bind(self):
        lib=self.library
        if hasattr(lib,'kart_effective_flags'):
            lib.kart_effective_flags.argtypes,lib.kart_effective_flags.restype=[C.c_void_p],C.c_char_p
        for name,expected in (('kart_abi_version',1),('kart_frame_size',C.sizeof(Frame)),('kart_car_size',C.sizeof(Car)),('kart_object_size',C.sizeof(Object))):
            fn=getattr(lib,name);fn.argtypes=[];fn.restype=C.c_int
            if fn()!=expected:raise RuntimeError('Kart ABI mismatch: '+name)
        signatures={
            'kart_create':([C.c_uint64,C.c_int,C.c_int,C.c_int,C.c_char_p,C.c_int],C.c_void_p),
            'kart_resize':([C.c_void_p,C.c_int,C.c_int],C.c_int),
            'kart_frame':([C.c_void_p,C.POINTER(Frame),C.POINTER(Car),C.c_int,C.POINTER(Object),C.c_int],C.c_int),
            'kart_present':([C.c_void_p],C.c_int),'kart_destroy':([C.c_void_p],None),
            'kart_info':([C.c_void_p],C.c_char_p),
            'kart_set_hud':([C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.c_int,C.c_int],C.c_int),
            'kart_patch_hud':([C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.c_int,C.c_int,C.c_int,C.c_int],C.c_int),
            'kart_vertices':([C.c_void_p],C.c_int),'kart_draws':([C.c_void_p],C.c_int),
            'kart_readback':([C.c_void_p,C.POINTER(C.c_uint8),C.c_int],C.c_int),
        }
        for name,(args,result) in signatures.items():getattr(lib,name).argtypes=args;getattr(lib,name).restype=result
        for name in ('kart_culled','kart_submitted_vertices'):
            if hasattr(lib,name):getattr(lib,name).argtypes=[C.c_void_p];getattr(lib,name).restype=C.c_int
    @property
    def is_native(self):return bool(self.context)
    def configure_depth(self,depth):pass
    def _status(self,native):
        if self.on_status:self.on_status('renderer_ready' if native else 'renderer_fallback',renderer='gles2-kart3d' if native else 'cairo-kart',reason=self.reason,accelerated=self.accelerated)
    def attach_window(self,xid,width,height,offscreen=False):
        if self.context:return True
        if self.library is None:self._status(False);return False
        self.context=self.library.kart_create(xid,width,height,int(offscreen),self.error,len(self.error))
        if not self.context:
            self.reason=self.error.value.decode('utf8','replace');self._status(False);return False
        self.size=(width,height);self.hud.full=True
        self.info=self.library.kart_info(self.context).decode('utf8','replace')
        self.accelerated='Mali' in self.info or not any(x in self.info.lower() for x in ('llvmpipe','softpipe','software'))
        self.reason='native-complete-frame';self._status(True);return True
    def resize(self,width,height):
        if self.context:self.library.kart_resize(self.context,width,height)
        self.size=(width,height)
    def release_native_output(self):
        if self.context:self.library.kart_destroy(self.context);self.context=None
    def warm(self,cr,width,height):self.hud.update(self._default_snapshot(),True)
    def _default_snapshot(self):
        from .engine import KartGame
        return KartGame().snapshot()
    def event(self,event):
        if event.get('kind')=='start':self.effects=[];return
        kinds={'pickup':0,'hit':1,'shield_break':2,'drift_boost':3,'rail':4}
        if event.get('kind') not in kinds or self.latest_snapshot is None:return
        racer=event.get('racer',0);cars=self.latest_snapshot['karts']
        if not 0<=racer<len(cars):return
        r=cars[racer]
        self.effects.append(dict(s=r['s'],x=r['x'],age=0.,kind=kinds[event['kind']]))
        self.effects=self.effects[-24:]
    def update_effects(self,dt):
        for effect in self.effects:effect['age']+=clamp(dt,0,.1)
        self.effects=[effect for effect in self.effects if effect['age']<.8]
    def render(self,cr,s,width,height):
        started=time.monotonic();self.frames+=1;self.latest_snapshot=s
        if not self.context:
            if self._fallback is None:self._fallback=CairoWorld()
            cr.save();cr.scale(width/W,height/H);self._fallback.draw(cr,s)
            self.hud.update(s);cr.set_source_surface(self.hud.surface,0,0);cr.paint();cr.restore();return False
        if self.size!=(width,height):self.resize(width,height)
        p=s['karts'][0];frame=self._frame
        for name in ('s','x','speed','steer','boost','shield','drift','slow'):setattr(frame,name,p[name])
        frame.drift=p['drift']*(1+p['drift_charge'])
        frame.clock=s['clock'];frame.countdown=s['countdown'];frame.state={'title':0,'countdown':1,'running':2,'paused':3,'finished':4}[s['state']]
        for i,r in enumerate(s['karts'][:6]):
            for name in ('s','x','speed','steer','boost','shield','drift','slow'):setattr(self._cars[i],name,r[name])
            self._cars[i].drift=r['drift']*(1+r['drift_charge'])
            self._cars[i].skin=i
        objects=[]
        for item in s['pickups']:
            if item['timer']<=0:objects.append((item['s'],item['x'],{'boost':0,'shield':1,'pulse':2}[item['kind']]))
        objects.extend((p['s'],p['x'],3) for p in s['projectiles'])
        objects.extend((e['s'],e['x'],100+e['kind']*64+min(63,int(e['age']/.8*63))) for e in self.effects)
        for i,obj in enumerate(objects[:96]):self._objects[i]=Object(*obj)
        # Upload before submitting this frame's world. Updating its texture
        # mid-scene can force a tiled Mali driver to synchronize queued draws.
        phase=time.monotonic()
        rectangles=self.hud.update(s)
        self.hud_prepare_ms.append((time.monotonic()-phase)*1000)
        if rectangles:
            phase=time.monotonic()
            data=self.hud.surface.get_data();stride=self.hud.surface.get_stride()
            buffer=(C.c_uint8*len(data)).from_buffer(data)
            for x,y,w,h in rectangles:
                pointer=C.cast(C.byref(buffer,y*stride+x*4),C.POINTER(C.c_uint8))
                ok=self.library.kart_set_hud(self.context,pointer,w,h,stride) if (x,y,w,h)==(0,0,W,H) else self.library.kart_patch_hud(self.context,pointer,x,y,w,h,stride)
                if not ok:self.reason='hud-upload-failed'
                self.hud_uploads+=1;self.hud_bytes+=w*h*4
            self.hud_upload_ms.append((time.monotonic()-phase)*1000)
        phase=time.monotonic()
        if not self.library.kart_frame(self.context,C.byref(frame),self._cars,min(6,len(s['karts'])),self._objects,min(96,len(objects))):
            self.reason='native-frame-failed';self.release_native_output();return self.render(cr,s,width,height)
        self.world_ms.append((time.monotonic()-phase)*1000)
        phase=time.monotonic()
        if not self.library.kart_present(self.context):self.reason='native-present-failed';self.release_native_output();return self.render(cr,s,width,height)
        self.present_ms.append((time.monotonic()-phase)*1000)
        self.native_ms.append((time.monotonic()-started)*1000);return True
    def native_rendering_flags(self):
        # Optional additive ABI; legacy libraries remain usable, final QA requires it.
        library=self.library;context=self.context
        if not context or library is None or not hasattr(library,'kart_effective_flags'):return None
        raw=library.kart_effective_flags(context)
        if not raw:return None
        result=json.loads(raw.decode('utf8'))
        if result.get('schema')!=1 or not isinstance(result.get('effective'),dict):raise RuntimeError('Invalid native rendering flags snapshot')
        result['effective']['INK_KART_HUD_DIRTY']='1' if self.hud.partial else '0'
        result['effective_source']='native-context-and-renderer-state'
        return result

    def metrics(self):
        def p95(values):return sorted(values)[max(0,math.ceil(len(values)*.95)-1)] if values else None
        return dict(renderer='gles2-kart3d' if self.context else 'cairo-kart',accelerated=self.accelerated,
                    native=self.is_native,reason=self.reason,native_rendering_flags=self.native_rendering_flags(),backend_info=self.info,frames=self.frames,
                    hud_uploads=self.hud_uploads,hud_bytes=self.hud_bytes,hud_builds=self.hud.builds,
                    text_builds=self.hud.text.builds,static_vertices=self.library.kart_vertices(self.context) if self.context else 0,
                    draw_calls=self.library.kart_draws(self.context) if self.context else 0,
                    culled_meshes=self.library.kart_culled(self.context) if self.context and hasattr(self.library,'kart_culled') else 0,
                    submitted_vertices=self.library.kart_submitted_vertices(self.context) if self.context and hasattr(self.library,'kart_submitted_vertices') else 0,
                    hud_prepare_ms_p95=p95(self.hud_prepare_ms),hud_upload_ms_p95=p95(self.hud_upload_ms),
                    world_ms_p95=p95(self.world_ms),present_ms_p95=p95(self.present_ms),
                    single_swap=True if self.context else False)

class CairoWorld:
    """Bounded 2.5D emergency route; uses the same curved closed course."""
    def __init__(self):self.cars={};self.trees={};self.background=None
    def project(self,p,eye,forward,right):
        d=(p[0]-eye[0],p[1]-eye[1],p[2]-eye[2]);along=d[0]*forward[0]+d[2]*forward[2]
        # Match the native chase camera's downward pitch. A level camera put
        # the contact patch below the canvas even though its depth was valid.
        sin_pitch=.18;cos_pitch=math.sqrt(1-sin_pitch*sin_pitch)
        z=along*cos_pitch-d[1]*sin_pitch
        if z<=.6:return None
        up=d[1]*cos_pitch+along*sin_pitch
        return 480+(d[0]*right[0]+d[2]*right[2])*580/z,250-up*580/z,580/z
    def kart(self,skin):
        if skin in self.cars:return self.cars[skin]
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,220,210);c=cairo.Context(surface);col=SKINS[skin]
        c.set_source_rgba(.03,.10,.10,.30);c.save();c.translate(110,185);c.scale(1,.23);c.arc(0,0,100,0,math.tau);c.fill();c.restore()
        for x in (30,165):
            round_rect(c,x,108,29,74,12,(.06,.10,.12,1));round_rect(c,x+4,128,21,35,8,(.24,.29,.28,1))
        poly(c,[(42,114),(62,73),(158,73),(180,114),(171,165),(49,165)],col)
        poly(c,[(59,112),(72,99),(148,99),(164,112),(153,157),(68,157)],(.10,.20,.22))
        round_rect(c,77,68,66,57,16,(*CREAM,1));round_rect(c,69,28,82,70,29,(*col,1))
        round_rect(c,104,30,12,33,4,(*CREAM,1));poly(c,[(80,91),(95,97),(57,127),(62,107)],(.83,.23,.17))
        round_rect(c,38,141,144,29,9,(*col,1));round_rect(c,49,146,122,6,3,(*GOLD,1))
        round_rect(c,32,119,156,12,4,(*col,1));round_rect(c,54,168,112,10,3,(*INK,1))
        for x in (66,145):round_rect(c,x,165,12,19,4,(.49,.54,.51,1))
        self.cars[skin]=surface;return surface
    def draw(self,cr,s):
        p=s['karts'][0];at,yaw=TRACK.sample(p['s']);forward=(math.sin(yaw),0,math.cos(yaw));right=(math.cos(yaw),0,-math.sin(yaw))
        eye=(at[0]-forward[0]*10+right[0]*p['x']*.3,at[1]+5.8,at[2]-forward[2]*10+right[2]*p['x']*.3)
        grad=cairo.LinearGradient(0,0,0,350);grad.add_color_stop_rgb(0,.49,.73,.73);grad.add_color_stop_rgb(1,.84,.86,.68)
        cr.set_source(grad);cr.paint();cr.set_source_rgb(.98,.87,.56);cr.arc(771,76,23,0,math.tau);cr.fill()
        for layer in range(3):
            points=[(-20,300)]
            for i in range(34):
                x=i*32-40;h=100+layer*38+42*math.sin(i*1.7+layer)+21*math.sin(i*3.1+yaw)
                points.append((x,h))
            points.extend(((990,540),(-20,540)))
            poly(cr,points,((.61,.72,.61),(.46,.64,.51),(.32,.51,.37))[layer])
        poly(cr,[(0,270),(960,270),(960,540),(0,540)],(.38,.55,.34))
        # Render far-to-near trapezoids sampled from the actual ring geometry.
        for j in range(105,0,-1):
            d0=p['s']+j*2.4;d1=d0+2.4;positions=[]
            for d,x in ((d0,-7),(d1,-7),(d1,7),(d0,7)):
                (x0,y0,z0),a=TRACK.sample(d);q=self.project((x0+math.cos(a)*x,y0,z0-math.sin(a)*x),eye,forward,right)
                if q is None:positions=[];break
                positions.append(q[:2])
            if positions:
                poly(cr,positions,(.28+(j%2)*.014,.32+(j%2)*.014,.30))
                for side in (-1,1):
                    border=[]
                    for d,x in ((d0,side*6.7),(d1,side*6.7),(d1,side*7.25),(d0,side*7.25)):
                        (x0,y0,z0),a=TRACK.sample(d);q=self.project((x0+math.cos(a)*x,y0+.04,z0-math.sin(a)*x),eye,forward,right)
                        if q:border.append(q[:2])
                    if len(border)==4:poly(cr,border,CREAM if j%2 else (.79,.25,.17))
        sprites=[]
        for r in s['karts']:
            relative=(r['s']-p['s']+TRACK.length)%TRACK.length
            if r['id'] and not (relative<210 or relative>TRACK.length-6):continue
            (x,y,z),a=TRACK.sample(r['s']);q=self.project((x+math.cos(a)*r['x'],y,z-math.sin(a)*r['x']),eye,forward,right)
            if q:sprites.append((q[2],r,q))
        for scale,r,q in sorted(sprites,key=lambda item:item[0]):
            cr.save();cr.translate(q[0],q[1]);k=scale*.018
            if r['id']==0:k*=1.10
            cr.scale(k,k);sprite=self.kart(r['id']);cr.set_source_surface(sprite,-110,-190);cr.paint();cr.restore()
