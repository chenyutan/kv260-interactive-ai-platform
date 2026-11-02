"""Native GLES runner bridge with an explicit, measured Cairo fallback.

Only rendering lives here. Simulation, camera input, reports and audio remain
owned by the existing runner. The C ABI uses scalar snapshots, never Python
object pointers. The HUD is one premultiplied texture, updated at most 10 Hz
during play; the scene and text reach the screen in the same EGL swap.
"""
from collections import OrderedDict, deque
import ctypes as C
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time

W, H = 960, 540
MAX_ENTITIES = 160
KINDS = {'cart': 0, 'barrier': 1, 'arch': 2, 'coin': 3, 'shield': 4, 'magnet': 5,
         'ramp': 6, 'jetpack': 7, 'springboots': 8, 'coinboost': 9}
STATES = {'title': 0, 'running': 1, 'paused': 2, 'over': 3}
EVENTS = {'collect': 0, 'hit': 1, 'shield_break': 2, 'dodge': 3}
SCORE_RECT = (706, 18, 232, 79)
LEFT_RECT = (20, 82, 214, 248)
GOAL_RECT = (258, 18, 422, 82)
POWERUPS = (('shield_time', '护盾'), ('magnet_time', '磁铁'),
            ('jetpack_time', '喷气背包'), ('boots_time', '弹跳鞋'),
            ('coinboost_time', '双倍铜钱'))


def load_material_atlas(path=None):
    """Decode one bounded opaque PNG, once per Renderer, without Pillow.

    Material images use straight RGBA; Cairo decoding is premultiplied, so
    reject transparency rather than silently darkening hand-painted colors.
    Uploaded row zero is PNG row zero; the world UV convention is top-left.
    """
    path = Path(path) if path is not None else Path(__file__).with_name('assets')/'material_atlas.png'
    data = path.read_bytes()
    if len(data) > 8*1024*1024 or len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[12:16] != b'IHDR':
        raise ValueError('Invalid material atlas PNG header/size')
    width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if width != height or width not in (512, 1024):
        raise ValueError('Material atlas must be 512 or 1024 square')
    try:
        try:
            import cairo
        except ImportError:
            import cairocffi as cairo
        surface = cairo.ImageSurface.create_from_png(io.BytesIO(data))
        surface.flush()
        if (surface.get_width(), surface.get_height()) != (width, height):
            raise ValueError('Material atlas decoded dimensions changed')
        rgba = cairo_bgra_to_rgba(surface.get_data(), width, height, surface.get_stride())
        if surface.get_format() == cairo.FORMAT_RGB24:
            rgba[3::4] = b'\xff'*(width*height)
        elif surface.get_format() != cairo.FORMAT_ARGB32:
            raise ValueError('Unsupported material atlas surface format')
    except (ImportError, OSError, ValueError):
        raise
    except Exception as error:
        raise ValueError('Cannot decode material atlas PNG: '+str(error)) from error
    if rgba[:4] != b'\xff\xff\xff\xff' or any(a != 255 for a in rgba[3::4]):
        raise ValueError('Material atlas must be opaque with a white origin')
    return dict(pixels=rgba, width=width, height=height, stride=width*4,
                sha256=hashlib.sha256(data).hexdigest(), path=str(path))



def _etc1_block_rgb(block):
    """Tiny independent ETC1 sentinel decoder, not a runtime image codec.

    Bit layout: Khronos OES_compressed_ETC1_RGB8_texture. Output rows stay
    top-left. Differential overflow is rejected as ETC2/invalid ETC1.
    """
    if len(block) != 8:raise ValueError('ETC1 block must be8 bytes')
    high=int.from_bytes(block[:4],'big');low=int.from_bytes(block[4:],'big')
    signed=lambda value:(value&7)-8 if value&4 else value&7
    colors=[[],[]]
    for channel in range(3):
        if high&2:
            shift=27-channel*8;first=(high>>shift)&31
            second=first+signed(high>>(shift-3))
            if not 0<=second<=31:raise ValueError('Invalid ETC1 differential endpoint')
            colors[0].append((first<<3)|(first>>2));colors[1].append((second<<3)|(second>>2))
        else:
            shift=28-channel*8
            colors[0].append(((high>>shift)&15)*17);colors[1].append(((high>>(shift-4))&15)*17)
    tables=((2,8,-2,-8),(5,17,-5,-17),(9,29,-9,-29),(13,42,-13,-42),
            (18,60,-18,-60),(24,80,-24,-80),(33,106,-33,-106),(47,183,-47,-183))
    pixels=bytearray()
    for y in range(4):
        for x in range(4):
            part=int(y>=2) if high&1 else int(x>=2);bit=x*4+y
            index=((low>>bit)&1)|((low>>(bit+15))&2)
            delta=tables[(high>>(2 if part else 5))&7][index]
            pixels.extend(max(0,min(255,value+delta)) for value in colors[part])
    return pixels


def parse_material_etc1(data, *, container='pkm10', width=None, height=None):
    """Validate PKM1.0/ETC1 format0 or explicit-size raw RGB8 blocks.

    No inferred codec, padded image, mip chain, ETC2, alpha, or trailing data.
    Both formats contain exactly one bounded512/1024 square base level.
    """
    if len(data)>1024*1024//2+16:raise ValueError('ETC1 input exceeds bounded atlas size')
    if container=='pkm10':
        if len(data)<16 or data[:6]!=b'PKM 10':raise ValueError('Expected PKM1.0 header')
        values=[int.from_bytes(data[i:i+2],'big') for i in range(6,16,2)]
        codec,encoded_w,encoded_h,actual_w,actual_h=values
        if codec!=0:raise ValueError('PKM codec is not ETC1 RGB8 format0')
        if (encoded_w,encoded_h)!=(actual_w,actual_h):raise ValueError('Padded PKM atlas rejected')
        if width is not None and width!=actual_w or height is not None and height!=actual_h:
            raise ValueError('PKM dimensions differ from manifest/source PNG')
        width,height=actual_w,actual_h;payload=bytearray(data[16:])
    elif container=='raw':
        if width is None or height is None:raise ValueError('Raw ETC1 requires explicit dimensions')
        payload=bytearray(data)
    else:raise ValueError('Unsupported ETC1 container')
    if isinstance(width,bool) or isinstance(height,bool) or width!=height or width not in (512,1024):
        raise ValueError('ETC1 atlas must be512/1024 square')
    if len(payload)!=width*height//2:raise ValueError('ETC1 payload length mismatch')
    # Reject ETC2-style differential overflow anywhere, not only at sentinel.
    for offset in range(0,len(payload),8):
        if payload[offset+3]&2:
            for component in payload[offset:offset+3]:
                first=component>>3;delta=(component&7)-8 if component&4 else component&7
                if not 0<=first+delta<=31:raise ValueError('Invalid ETC1 differential endpoint')
    for by in range(4):
        for bx in range(4):
            offset=(by*(width//4)+bx)*8
            if _etc1_block_rgb(payload[offset:offset+8])!=b'\xff'*48:
                raise ValueError('ETC1 top-left16x16 sentinel must decode to pure white')
    return dict(blocks=payload,width=width,height=height,format='etc1',storage_bits=4,
                payload_sha256=hashlib.sha256(payload).hexdigest(),container=container)


def load_material_etc1(png, path=None, *, metadata=None):
    """Load one hash-bound pre-encoded atlas; failures permit explicit PNG fallback.

    Metadata lives in the existing assets/manifest.json under `etc1`; no
    sidecar and no encoding, image download or dependency at runtime.
    """
    source=Path(png['path'])
    if metadata is None:
        manifest_path=source.with_name('manifest.json')
        if manifest_path.stat().st_size>64*1024:raise ValueError('Material manifest too large')
        metadata=json.loads(manifest_path.read_text(encoding='utf-8'))['etc1']
    if not isinstance(metadata,dict):raise ValueError('Invalid ETC1 metadata')
    name=metadata.get('file')
    if not isinstance(name,str) or name in ('','.','..') or '/' in name or '\\' in name:
        raise ValueError('ETC1 filename must be a simple local asset name')
    if metadata.get('format')!='ETC1_RGB8':raise ValueError('Unknown compressed material codec')
    container=metadata.get('container','pkm10')
    if container=='pkm10' and metadata.get('pkm_version')!='1.0':raise ValueError('Unknown PKM version')
    if metadata.get('source_sha256')!=png['sha256']:raise ValueError('ETC1 source PNG hash mismatch')
    width,height=metadata.get('width'),metadata.get('height')
    if (width,height)!=(png['width'],png['height']):raise ValueError('ETC1 source dimensions mismatch')
    path=Path(path) if path is not None else source.with_name(name)
    if path.name!=name or path.is_symlink():raise ValueError('ETC1 asset path mismatch/symlink')
    expected_size=width*height//2+(16 if container=='pkm10' else 0)
    if metadata.get('bytes')!=expected_size or path.stat().st_size!=expected_size:raise ValueError('ETC1 file size mismatch')
    data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    if digest!=metadata.get('sha256'):raise ValueError('ETC1 file hash mismatch')
    result=parse_material_etc1(data,container=container,width=width,height=height)
    result.update(sha256=digest,path=str(path),file_bytes=len(data),source_sha256=png['sha256'])
    return result

def powerup_seconds(snapshot):
    """A bounded, one-second HUD key; actual timers never alter native ABI."""
    return tuple(min(99, math.ceil(max(0., _number(snapshot.get(name, 0.)))))
                 for name, _ in POWERUPS)


class Frame(C.Structure):
    _fields_ = [(name, C.c_float) for name in (
        'distance', 'player_x', 'jump_height', 'slide_phase', 'shield_time',
        'magnet_time', 'clock')] + [('state', C.c_int), ('player_y', C.c_float)]


class Entity(C.Structure):
    _fields_ = [('kind', C.c_int), ('lane', C.c_int), ('z', C.c_float),
                ('collected', C.c_int), ('y', C.c_float)]


def _number(value, default=0.):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _entity_id(value):
    try:return max(0,min((1<<29)-1,int(value))) if not isinstance(value,bool) else 0
    except (TypeError,ValueError,OverflowError):return 0


def cairo_bgra_to_rgba(data, width, height, stride):
    """Preserve Cairo premultiplication while swapping channel order once."""
    raw = bytes(data)
    if stride != width * 4:
        raw = b''.join(raw[y * stride:y * stride + width * 4] for y in range(height))
    result = bytearray(width * height * 4)
    if sys.byteorder == 'little':
        result[0::4], result[1::4], result[2::4], result[3::4] = (
            raw[2::4], raw[1::4], raw[0::4], raw[3::4])
    else:
        result[0::4], result[1::4], result[2::4], result[3::4] = (
            raw[1::4], raw[2::4], raw[3::4], raw[0::4])
    return result


def cairo_region_to_rgba(data, x, y, width, height, stride):
    """Copy only dirty rows; never duplicate the full 2 MiB Cairo buffer."""
    start = y*stride+x*4
    packed = b''.join(data[start+row*stride:start+row*stride+width*4] for row in range(height))
    return cairo_bgra_to_rgba(packed, width, height, width*4)


class HudUpdate:
    __slots__ = ('pixels', 'x', 'y', 'width', 'height', 'patch', 'format', '_stride')
    def __init__(self, pixels, x=0, y=0, width=W, height=H, patch=False,format='RGBA',stride=None):
        self.pixels, self.x, self.y = pixels, x, y
        self.width, self.height, self.patch = width, height, patch
        self.format,self._stride=format,width*4 if stride is None else stride
    @property
    def stride(self):return self._stride


class HudLayer:
    """Small, readable Chinese HUD; cached glyphs and bounded texture updates."""
    def __init__(self):
        try:
            import cairo
        except ImportError:
            import cairocffi as cairo
        self.cairo = cairo
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        self.glyphs = OrderedDict()
        self.key = None
        self.last_update = -1.
        self.state = None
        self.uploads = 0
        self.builds = 0
        self.full_builds = 0
        self.patch_builds = 0
        self.use_bgra = False
        # CPU-only immutable Cairo templates. The GPU HUD, patch rectangles,
        # update clock and dynamic values are unchanged. Prewarm before play.
        self._patch_contexts={}
        self._templates=OrderedDict()
        self._template_bytes=0
        self.template_hits=self.template_misses=0
        self.template_limit=3*1024*1024
        # These are the only labels/digit faces introduced by live status
        # changes. Build them before play, so power-ups never open a font.
        cr=cairo.Context(self.surface)
        for size,cc in ((27,(1.,.84,.48)),(17,(1.,.83,.43)),(16,(.78,.87,.91))):
            self.digits(cr,1234567890,-1000,-1000,size,cc,10)
        for value in ('护盾','磁铁','连收','倍数','收集金币','登上车顶',
                      '躲避障碍','河岸','集市','当前目标','米','枚','次'):
            self.text(cr,value,-1000,-1000,14,(1.,.84,.48))
        for value in ('河岸','集市'):
            self.text(cr,value,-1000,-1000,17,(1.,.84,.48),bold=True)
        for value in ('收集金币','登上车顶','躲避障碍'):
            self.text(cr,value,-1000,-1000,16)
        for _, label in POWERUPS:
            self.text(cr,label,-1000,-1000,14,(1.,.84,.48))
        self.text(cr,'秒',-1000,-1000,14,(1.,.84,.48))
        self.digits(cr,1234567890,-1000,-1000,14,(1.,.84,.48),10)
        self.text(cr,'车站',-1000,-1000,17,(1.,.84,.48),bold=True)
        self.text(cr,'连收',-1000,-1000,13,(.78,.87,.91))
        self.text(cr,'×',-1000,-1000,19,(1.,.84,.48),bold=True)
        self.digits(cr,1234567890,-1000,-1000,20,(1.,.84,.48),10)
        self._template(('score',),SCORE_RECT,self._score_prefix)
        for lives in range(4):
            self._template(('left',lives),LEFT_RECT,lambda dst,n=lives:self._left_prefix(dst,n))
        for row in range(len(POWERUPS)):
            y=172+row*29
            for _,label in POWERUPS:
                self._template(('powerup',label,row),(22,y,205,25),
                    lambda dst,label=label,y=y:self._powerup_prefix(dst,label,y))
        for district in range(3):
            for label in ('收集金币','登上车顶','躲避障碍'):
                self._template(('goal',district,label),GOAL_RECT,
                    lambda dst,d=district,label=label:self._goal_prefix(dst,d,label))


    @staticmethod
    def _round(cr, x, y, w, h, r):
        cr.new_sub_path()
        for cx, cy, a in ((x+w-r, y+r, -math.pi/2), (x+w-r, y+h-r, 0),
                          (x+r, y+h-r, math.pi/2), (x+r, y+r, math.pi)):
            cr.arc(cx, cy, r, a, a + math.pi/2)
        cr.close_path()

    def pill(self, cr, x, y, w, h, *, alpha=.86, border=True, radius=17):
        self._round(cr, x, y, w, h, radius)
        cr.set_source_rgba(.035, .09, .145, alpha)
        if border:
            cr.fill_preserve();cr.set_source_rgba(1., .83, .48, .40)
            cr.set_line_width(1.);cr.stroke()
        else:
            cr.fill()

    def text(self, cr, value, x, y, size=18, color=(1., .97, .88), *,
             center=False, bold=False):
        value = str(value)
        key = (value, size, color, bold)
        if key not in self.glyphs:
            cairo = self.cairo
            probe = cairo.Context(self.surface)
            probe.select_font_face('Noto Sans CJK SC', cairo.FONT_SLANT_NORMAL,
                                   cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
            probe.set_font_size(size)
            ext = probe.text_extents(value)
            tw = max(1, math.ceil(ext[4]) + 8)
            th = math.ceil(size * 1.55) + 6
            image = cairo.ImageSurface(cairo.FORMAT_ARGB32, tw, th)
            draw = cairo.Context(image)
            draw.select_font_face('Noto Sans CJK SC', cairo.FONT_SLANT_NORMAL,
                                  cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
            draw.set_font_size(size);draw.set_source_rgb(*color)
            draw.move_to(4, size + 2);draw.show_text(value)
            self.glyphs[key] = image
            while len(self.glyphs) > 192:
                self.glyphs.popitem(last=False)
        self.glyphs.move_to_end(key)
        image = self.glyphs[key]
        cr.set_source_surface(image, round(x-image.get_width()/2 if center else x), round(y))
        cr.paint()

    def digits(self, cr, value, x, y, size=25, color=(1., .83, .43), pad=0):
        text = str(max(0, int(value))).zfill(pad)
        for i, char in enumerate(text):
            self.text(cr, char, x+i*size*.67, y, size, color, bold=True)

    def _template(self,key,rect,draw):
        if key in self._templates:
            self.template_hits+=1;self._templates.move_to_end(key)
            return self._templates[key]
        self.template_misses+=1
        # Two integer pixels preserve the original antialiased border just
        # outside a panel on full draws; dirty draws retain their own clip.
        x,y,w,h=rect;x-=2;y-=2;w+=4;h+=4
        image=self.cairo.ImageSurface(self.cairo.FORMAT_ARGB32,w,h)
        dst=self.cairo.Context(image);dst.translate(-x,-y);draw(dst);image.flush()
        record=(image,x,y,w*h*4);self._templates[key]=record;self._template_bytes+=record[3]
        while self._template_bytes>self.template_limit and len(self._templates)>1:
            _,old=self._templates.popitem(last=False);self._template_bytes-=old[3]
        return record

    def _paint_template(self,cr,key,rect,draw,*,replace=False):
        image,x,y,_=self._template(key,rect,draw)
        cr.save();cr.rectangle(x,y,image.get_width(),image.get_height());cr.clip()
        if replace:cr.set_operator(self.cairo.OPERATOR_SOURCE)
        cr.set_source_surface(image,x,y);cr.paint();cr.restore()

    def _patch_context(self,rect):
        # The HUD surface and dirty rectangles never resize. Retain their
        # clipped contexts instead of allocating/rebuilding clips every tick.
        cr=self._patch_contexts.get(rect)
        if cr is None:
            cr=self.cairo.Context(self.surface);cr.rectangle(*rect);cr.clip()
            self._patch_contexts[rect]=cr
        cr.set_operator(self.cairo.OPERATOR_OVER)
        return cr

    def _score_prefix(self,cr):
        self.pill(cr,*SCORE_RECT)
        self.text(cr,'得分',723,25,12,(.78,.87,.91))

    def _left_prefix(self,cr,lives):
        self.pill(cr,22,85,184,76,alpha=.77,radius=15)
        for i in range(3):
            x,y=43+i*30,102
            cr.set_source_rgb(*((1.,.37,.31) if i<lives else (.28,.38,.44)))
            cr.move_to(x,y+7);cr.curve_to(x-18,y-5,x-5,y-14,x,y-6)
            cr.curve_to(x+5,y-14,x+18,y-5,x,y+7);cr.fill()
        self.text(cr,'连收',34,125,13,(.78,.87,.91))

    def _powerup_prefix(self,cr,label,y):
        self.pill(cr,22,y,205,25,alpha=.76,radius=10)
        self.text(cr,label,34,y+1,14,(1.,.84,.48))

    def _goal_prefix(self,cr,district,label):
        gold=(1.,.84,.48);soft=(.78,.87,.91);x,y,w,h=GOAL_RECT
        self.pill(cr,x,y,w,h,alpha=.76,radius=17)
        self.text(cr,('河岸','集市','车站')[district],x+17,y+8,17,gold,bold=True)
        self.text(cr,'当前目标',x+17,y+37,13,soft)
        self.text(cr,label,x+91,y+34,16)

    def _draw_score(self, cr, snapshot,*,replace=False):
        gold, soft = (1., .84, .48), (.78, .87, .91)
        self._paint_template(cr,('score',),SCORE_RECT,self._score_prefix,replace=replace)
        self.digits(cr, snapshot.get('score', 0), 765, 20, 27, gold, 6)
        cr.set_source_rgb(*gold);cr.arc(729, 76, 8, 0, math.tau);cr.fill()
        cr.set_source_rgb(.22, .14, .06);cr.rectangle(726, 73, 6, 6);cr.fill()
        self.digits(cr, snapshot.get('coins', 0), 743, 61, 17)
        # Reuse ten digit glyphs. Rasterising a new CJK string for every metre
        # caused avoidable work and evicted the otherwise reusable HUD cache.
        self.digits(cr, snapshot.get('distance', 0), 822, 62, 16, soft)
        self.text(cr, '米', 912, 62, 16, soft)

    def _draw_score_patch(self, snapshot):
        cr=self._patch_context(SCORE_RECT)
        # SOURCE of the complete transparent-backed template is bit-identical
        # to CLEAR then OVER; it avoids the redundant first full-region pass.
        self._draw_score(cr,snapshot,replace=True)
        self.surface.flush()
        self.builds += 1
        self.patch_builds += 1

    def _draw_left(self,cr,snapshot,*,replace=False):
        gold=(1.,.84,.48)
        lives=max(0,min(3,int(snapshot.get('lives',3))))
        self._paint_template(cr,('left',lives),LEFT_RECT,lambda dst:self._left_prefix(dst,lives),replace=replace)
        # The multiplier is earned by a visible ten-coin chain. Keep the
        # meter in a bounded dirty rectangle, rather than re-uploading text.
        combo=max(0,int(snapshot.get('combo',0)))
        multiplier=max(1,min(4,int(snapshot.get('multiplier',1))))
        self.digits(cr,combo,70,121,17,gold)
        self.text(cr,'×',145,121,19,gold,bold=True)
        self.digits(cr,multiplier,168,121,20,gold)
        for i in range(10):
            cr.set_source_rgba(*gold,.92 if i<combo%10 or combo>=30 else .16)
            cr.rectangle(34+i*15.7,151,12,3);cr.fill()
        row=0
        for (_,label),seconds in zip(POWERUPS,powerup_seconds(snapshot)):
            if seconds<=0:continue
            y=172+row*29
            self._paint_template(cr,('powerup',label,row),(22,y,205,25),
                lambda dst:self._powerup_prefix(dst,label,y))
            self.digits(cr,seconds,177,y+1,14,gold,2)
            self.text(cr,'秒',201,y+1,14,gold)
            row+=1

    def _draw_goal(self,cr,snapshot,*,replace=False):
        gold=(1.,.84,.48);soft=(.78,.87,.91)
        x,y,w,h=GOAL_RECT
        district=int(snapshot.get('district',0))%3
        label=snapshot.get('goal_label') or '收集金币'
        if label in ('收集金币','登上车顶','躲避障碍'):
            self._paint_template(cr,('goal',district,label),GOAL_RECT,
                lambda dst:self._goal_prefix(dst,district,label),replace=replace)
        else:
            # Preserve arbitrary caller labels and their original full-draw
            # bounds instead of clipping them into a finite template.
            if replace:
                cr.save();cr.set_operator(self.cairo.OPERATOR_CLEAR);cr.paint();cr.restore()
            self._goal_prefix(cr,district,label)
        progress=max(0,int(snapshot.get('goal_progress',0)))
        target=max(1,int(snapshot.get('goal_target',20)))
        self.digits(cr,progress,x+293,y+33,17,gold)
        self.text(cr,'/',x+329,y+34,16,soft)
        self.digits(cr,target,x+347,y+33,17,soft)
        cr.set_source_rgba(*gold,.17);cr.rectangle(x+17,y+66,w-34,3);cr.fill()
        cr.set_source_rgba(*gold,.9)
        cr.rectangle(x+17,y+66,(w-34)*min(1.,progress/target),3);cr.fill()

    def _draw_goal_patch(self,snapshot):
        cr=self._patch_context(GOAL_RECT)
        self._draw_goal(cr,snapshot,replace=True);self.surface.flush()
        self.builds+=1;self.patch_builds+=1

    def _draw_left_patch(self,snapshot):
        cr=self._patch_context(LEFT_RECT)
        self._draw_left(cr,snapshot,replace=True);self.surface.flush()
        self.builds+=1;self.patch_builds+=1

    def _draw(self, snapshot):
        cairo = self.cairo
        cr = cairo.Context(self.surface)
        cr.set_operator(cairo.OPERATOR_CLEAR);cr.paint();cr.set_operator(cairo.OPERATOR_OVER)
        state = snapshot.get('state', 'title')
        ui = snapshot.get('ui') or {}
        gamepad = ui.get('control_mode') == 'gamepad'
        prompt = None
        gold = (1., .84, .48)
        soft = (.78, .87, .91)
        self.pill(cr, 22, 18, 196, 58)
        cr.set_source_rgb(*gold);cr.move_to(41, 34);cr.line_to(48, 43)
        cr.line_to(41, 52);cr.line_to(34, 43);cr.close_path();cr.fill()
        self.text(cr, '地铁跑酷', 57, 27, 23, gold, bold=True)
        self._draw_left(cr,snapshot)
        self._draw_score(cr, snapshot)
        self._draw_goal(cr,snapshot)

        if state in ('title', 'over'):
            self.pill(cr, 198, 145, 564, 291, alpha=.94, radius=25)
            # Fine brass rules, generous typography, transparent world behind.
            cr.set_source_rgba(*gold, .7);cr.set_line_width(1.)
            cr.move_to(233, 166);cr.line_to(727, 166);cr.stroke()
            self.text(cr, '地铁跑酷' if state == 'title' else '游戏结束',
                      480, 176, 37, gold, center=True, bold=True)
            if state == 'over':
                self.text(cr, f"得分 {snapshot.get('score', 0)}    铜钱 {snapshot.get('coins', 0)}",
                          480, 279, 22, center=True, bold=True)
                self.text(cr, f"奔行 {int(snapshot.get('distance', 0))} 米  ·  最高连收 {int(snapshot.get('best_combo',0))}",
                          480, 313, 18, soft, center=True)
                prompt = '按 X 重新开始' if gamepad else '双手高举重新开始'
            else:
                self.text(cr,'准备开始',480,285,23,center=True)
                prompt = '按 X 开始' if gamepad else '双手高举开始'
            self.text(cr, prompt, 480, 376, 23, gold, center=True, bold=True)
        elif state == 'paused' or ui.get('tracking_paused'):
            self.pill(cr, 275, 194, 410, 110, alpha=.93, radius=21)
            self.text(cr, '已暂停', 480, 210, 27, gold, center=True, bold=True)
            self.text(cr, ui.get('notice') or ('Menu 键呼出菜单继续' if gamepad else '转两圈呼出菜单继续'),
                      480, 258, 15, soft, center=True)

        # Keep the running playfield clear; controls are reviewed before entry.
        notice = ui.get('notice', '')
        ordinary_gamepad_help = notice.startswith('手柄：')
        if (notice and notice != prompt and not ordinary_gamepad_help
                and state != 'paused' and not ui.get('tracking_paused')):
            self.pill(cr, 230, 438, 500, 35, alpha=.87, radius=15)
            self.text(cr, notice, 480, 442, 16, gold, center=True)
        self.surface.flush()
        self.builds += 1
        self.full_builds += 1

    def full_upload(self):
        """Compatibility path for a native library predating dirty uploads."""
        return self._surface_update((0,0,W,H),False)

    def _surface_update(self,rect,patch=True):
        x,y,width,height=rect;stride=self.surface.get_stride();data=self.surface.get_data()
        if self.use_bgra:
            start=y*stride+x*4;span=(height-1)*stride+width*4
            # Flat writable span retains original row pitch and Cairo owner.
            # ctypes borrows its address; neither channels nor rows are copied.
            pixels=memoryview(data)[start:start+span]
            return HudUpdate(pixels,x,y,width,height,patch,'BGRA',stride)
        pixels=cairo_region_to_rgba(data,x,y,width,height,stride)
        return HudUpdate(pixels,x,y,width,height,patch)

    def update(self, snapshot, now):
        ui = snapshot.get('ui') or {}
        key = (snapshot.get('state', 'title'), int(snapshot.get('score', 0)),
               int(snapshot.get('coins', 0)), int(snapshot.get('distance', 0)),
               int(snapshot.get('lives', 3)), bool(snapshot.get('shield_time', 0) > 0),
               bool(snapshot.get('magnet_time', 0) > 0), ui.get('control_mode'),
               ui.get('notice', ''), bool(ui.get('tracking_paused')),
               int(snapshot.get('combo',0)),int(snapshot.get('multiplier',1)),
               int(snapshot.get('goal_index',0)),int(snapshot.get('goal_progress',0)),
               int(snapshot.get('goal_target',20)),snapshot.get('goal_label','集齐铜钱'),
               int(snapshot.get('district',0)),int(snapshot.get('goals_completed',0)),
               *powerup_seconds(snapshot))
        # Layout changes need a complete rebuild. Running life/power changes
        # touch the small left status patch; the scoreboard is independent.
        state = (key[0], *key[7:10])
        if key == self.key:return None
        urgent=self.key is not None and (key[4:7]!=self.key[4:7] or
                tuple(v>0 for v in key[18:])!=tuple(v>0 for v in self.key[18:]))
        status_changed=self.key is not None and (urgent or key[10:12]!=self.key[10:12]
                                                or key[18:]!=self.key[18:])
        if self.state == state and not urgent and self.last_update >= 0 and now-self.last_update < .1:return None
        patch = self.key is not None and self.state == state and key[0] == 'running' and not key[9]
        updates=[]
        if patch:
            if key[1:4]!=self.key[1:4]:
                self._draw_score_patch(snapshot);updates.append(self._surface_update(SCORE_RECT))
            if status_changed:
                self._draw_left_patch(snapshot);updates.append(self._surface_update(LEFT_RECT))
            if key[12:18]!=self.key[12:18]:
                self._draw_goal_patch(snapshot);updates.append(self._surface_update(GOAL_RECT))
        else:self._draw(snapshot)
        self.key, self.state, self.last_update = key, state, now
        if patch:return updates[0] if len(updates)==1 else updates
        return self.full_upload()


class Renderer:
    def __init__(self, on_status=None, *, library=None, fallback_factory=None, hud_factory=HudLayer,
                 material_loader=load_material_atlas, etc1_loader=load_material_etc1):
        self.on_status = on_status
        self._fallback_factory = fallback_factory
        self._hud_factory = hud_factory
        self._material_loader = material_loader
        self._material = None
        self._etc1_loader=etc1_loader
        self._material_etc1=None
        self._material_etc1_attempted=False
        self._etc1_api=False
        self._mips_api=False
        self._material_mips_requested=os.environ.get('INK_GL_MATERIAL_MIPS','1')=='1'
        self.material_mips_levels=self.material_mips_gpu_bytes=0
        self.material_mips_upload_attempts=self.material_mips_gpu_bytes_uploaded=0
        self.material_mips_fallback_reason='disabled by INK_GL_MATERIAL_MIPS' if not self._material_mips_requested else None
        self._material_preference=os.environ.get('INK_GL_MATERIAL_FORMAT','auto')
        self.material_format=None
        self.material_fallback_reason=None
        self.material_etc1_loads=0
        self.material_gpu_bytes_uploaded=0
        self.material_source_rgba_bytes=0
        self.material_gpu_bytes=0
        self.material_storage_bits=0
        self.material_etc1_upload_attempts=0
        self.material_decodes = self.material_uploads = self.material_bytes_uploaded = 0
        self.material_loaded = False
        self._fallback = None
        self._library = None
        self._context = None
        self._hud = None
        self._size = None
        self._depth = None
        self._reason = None
        self._reported = set()
        self._backend_info = None
        self._accelerated = False
        # Some EGL wrappers retain their creation error sink in the context.
        # Keep it alive until after native context destruction, not a stack
        # local which disappears once attach_window returns.
        self._error_buffer = C.create_string_buffer(2048)
        self.context_generation = 0
        self._frame = Frame()
        self._entities = (Entity * MAX_ENTITIES)()
        self._hit_ids = OrderedDict()
        self._last_distance = None
        self.clock = 0.
        self.frames = 0
        self.hud_uploads = 0
        # Diagnostic A/B switch; production defaults to dirty uploads.
        self.force_full_hud = os.environ.get('INK_RUNNER_HUD_FULL_UPLOAD') == '1'
        self.hud_patch_uploads = 0
        self.hud_full_uploads = 0
        self.hud_bytes_uploaded = 0
        self.hud_prepare_ms = deque(maxlen=128)
        self.hud_upload_ms = deque(maxlen=128)
        self._hud_format = None
        self.events_forwarded = 0
        self.event_error = None
        self.last_native_ms = 0.
        self.library_path = Path(__file__).with_name('native') / 'librunner3d.so'
        try:
            if self._material_preference not in ('auto','etc1','rgb565','rgba8'):
                raise RuntimeError('Invalid INK_GL_MATERIAL_FORMAT')
            if os.environ.get('INK_RUNNER_RENDERER', '').lower() == 'cairo':
                raise RuntimeError('Cairo renderer explicitly selected')
            self._library = library if library is not None else C.CDLL(str(self.library_path))
            self._bind()
        except (OSError, AttributeError, TypeError, RuntimeError) as error:
            self._fallback_to_cairo('native-library-unavailable: ' + str(error))

    def _bind(self):
        lib = self._library
        if hasattr(lib,'rv_effective_flags'):
            lib.rv_effective_flags.argtypes,lib.rv_effective_flags.restype=[C.c_void_p],C.c_char_p
        # Entity stride changed when real elevated routes were introduced.
        # Refuse stale libraries before context creation; otherwise an old
        # renderer would reinterpret every entity after the first one.
        abi={}
        for name in ('rv_abi_version','rv_frame_size','rv_entity_size'):
            function=getattr(lib,name)
            function.argtypes,function.restype=[],C.c_int
            abi[name]=function()
        if (abi['rv_abi_version'],abi['rv_frame_size'],abi['rv_entity_size'])!=(2,C.sizeof(Frame),C.sizeof(Entity)):
            raise RuntimeError('Incompatible native runner ABI: '+str(abi))
        material_api = getattr(lib, 'rv_material_api_version')
        material_api.argtypes, material_api.restype = [], C.c_int
        if material_api() != 1:
            raise RuntimeError('Incompatible native material atlas API')
        signatures = {
            'rv_create': ([C.c_uint64, C.c_int, C.c_int, C.c_int, C.c_char_p, C.c_int], C.c_void_p),
            'rv_frame': ([C.c_void_p, C.POINTER(Frame), C.POINTER(Entity), C.c_int], C.c_int),
            'rv_set_hud': ([C.c_void_p, C.POINTER(C.c_uint8), C.c_int, C.c_int, C.c_int], C.c_int),
            'rv_resize': ([C.c_void_p, C.c_int, C.c_int], C.c_int),
            'rv_destroy': ([C.c_void_p], None),
            'rv_set_material_atlas': ([C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.c_int,C.c_int],C.c_int),
            # ABI2 stride alone cannot identify the new content: the old
            # library silently skips pickups 7..9. Require this capability.
            'rv_set_powerups': ([C.c_void_p,C.c_float,C.c_float,C.c_float], C.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(lib, name)
            function.argtypes, function.restype = args, result
        # Compressed upload is optional and cannot make old RGBA libraries
        # pretend they support a new codec. A broken optional set uses PNG.
        if hasattr(lib,'rv_material_etc1_api_version'):
            capability=lib.rv_material_etc1_api_version
            capability.argtypes,capability.restype=[],C.c_int
            names=('rv_set_material_atlas_etc1','rv_material_etc1_supported','rv_material_storage_bits')
            if capability()==1 and all(hasattr(lib,name) for name in names):
                lib.rv_set_material_atlas_etc1.argtypes=[C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.c_int,C.c_int]
                lib.rv_set_material_atlas_etc1.restype=C.c_int
                for name in names[1:]:
                    function=getattr(lib,name);function.argtypes,function.restype=[C.c_void_p],C.c_int
                self._etc1_api=True
            else:self.material_fallback_reason='ETC1 optional API incomplete/incompatible'
        mip_names=('rv_material_mips_base_sha256','rv_material_mips_source_sha256','rv_set_material_mips',
                   'rv_material_mips_levels','rv_material_mips_gpu_bytes','rv_material_mips_upload_bytes','rv_material_mips_reason')
        if hasattr(lib,'rv_material_mips_api_version') and all(hasattr(lib,name) for name in mip_names):
            lib.rv_material_mips_api_version.argtypes,lib.rv_material_mips_api_version.restype=[],C.c_int
            if lib.rv_material_mips_api_version()==1:
                for name in mip_names[:2]:
                    fn=getattr(lib,name);fn.argtypes,fn.restype=[],C.c_char_p
                lib.rv_set_material_mips.argtypes,lib.rv_set_material_mips.restype=[C.c_void_p,C.c_char_p,C.c_char_p],C.c_int
                for name in mip_names[3:6]:
                    fn=getattr(lib,name);fn.argtypes,fn.restype=[C.c_void_p],C.c_int
                lib.rv_material_mips_reason.argtypes,lib.rv_material_mips_reason.restype=[C.c_void_p],C.c_char_p
                self._mips_api=True
        if hasattr(lib, 'rv_info'):
            lib.rv_info.argtypes, lib.rv_info.restype = [C.c_void_p], C.c_char_p
        if hasattr(lib, 'rv_readback'):
            lib.rv_readback.argtypes, lib.rv_readback.restype = [C.c_void_p, C.POINTER(C.c_uint8), C.c_int], C.c_int
        if hasattr(lib, 'rv_patch_hud'):
            lib.rv_patch_hud.argtypes, lib.rv_patch_hud.restype = [C.c_void_p,C.POINTER(C.c_uint8),
                C.c_int,C.c_int,C.c_int,C.c_int,C.c_int], C.c_int
        if hasattr(lib,'rv_set_hud_bgra'):
            lib.rv_set_hud_bgra.argtypes,lib.rv_set_hud_bgra.restype=[C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.c_int,C.c_int],C.c_int
        if hasattr(lib,'rv_patch_hud_bgra'):
            lib.rv_patch_hud_bgra.argtypes,lib.rv_patch_hud_bgra.restype=[C.c_void_p,C.POINTER(C.c_uint8),C.c_int,C.c_int,C.c_int,C.c_int,C.c_int],C.c_int
        if hasattr(lib, 'rv_event'):
            lib.rv_event.argtypes, lib.rv_event.restype = [C.c_void_p,C.c_int,C.c_int], None

    @property
    def is_native(self):
        return self._context is not None

    def _status(self, status, **fields):
        key = (status, fields.get('reason'))
        if key in self._reported:return
        self._reported.add(key)
        packet = dict(renderer=status, **fields)
        if self.on_status:
            self.on_status('renderer_ready' if status == 'gles3d' else 'renderer_fallback', **packet)
        else:
            print(json.dumps(dict(event='renderer_ready' if status == 'gles3d' else 'renderer_fallback',
                                  mode='runner', **packet), ensure_ascii=False), flush=True)

    def _get_fallback(self):
        if self._fallback is None:
            factory = self._fallback_factory
            if factory is None:
                from runner.render import Renderer as CairoRenderer
                factory = CairoRenderer
            self._fallback = factory()
            if self._depth is not None:self._fallback.configure_depth(self._depth)
        return self._fallback

    def _fallback_to_cairo(self, reason):
        self.release_native_output()
        self._library = None
        self._reason = reason[:500]
        self._get_fallback()
        self._status('cairo', reason=self._reason, accelerated=False)

    def attach_window(self, xid, width, height, offscreen=False):
        if self._context is not None:return True
        if self._library is None:return False
        try:
            if not offscreen and (isinstance(xid, bool) or int(xid) <= 0):
                raise ValueError('A native X11 window is required')
            width, height = max(1, int(width)), max(1, int(height))
            error = self._error_buffer
            error.value = b''
            context = self._library.rv_create(int(xid), width, height, int(offscreen), error, len(error))
            if not context:
                raise RuntimeError(error.value.decode('utf-8', 'replace') or 'EGL context creation failed')
            self._context, self._size = context, (width, height)
            self.context_generation += 1
            # Context recreation needs a fresh GPU upload, never a per-frame
            # decode/upload. Keep the decoded CPU buffer for subsequent attach.
            if self._material is None:
                self._material = self._material_loader()
                self.material_decodes += 1
            material = self._material
            mw, mh, stride = material['width'], material['height'], material['stride']
            pixels = material['pixels']
            if mw != mh or mw not in (1,512,1024) or stride < mw*4 or stride > 8192 or len(pixels) != (mh-1)*stride+mw*4:
                raise ValueError('Invalid decoded material atlas layout')
            self.material_source_rgba_bytes=mw*mh*4
            uploaded=False
            if self._material_preference in ('auto','etc1'):
                if not self._etc1_api:
                    self.material_fallback_reason=self.material_fallback_reason or 'ETC1 optional API absent'
                elif not self._library.rv_material_etc1_supported(context):
                    self.material_fallback_reason='ETC1 driver capability unavailable'
                else:
                    if not self._material_etc1_attempted:
                        self._material_etc1_attempted=True
                        try:
                            self._material_etc1=self._etc1_loader(material)
                            self.material_etc1_loads+=1
                        except (OSError,ValueError,TypeError,KeyError) as failure:
                            self.material_fallback_reason='ETC1 asset rejected: '+str(failure)
                    compressed=self._material_etc1
                    if compressed is not None:
                        blocks=compressed['blocks'];size=len(blocks)
                        if (compressed['width'],compressed['height'])!=(mw,mh) or mw not in (512,1024) or size!=mw*mh//2:
                            raise ValueError('Invalid validated ETC1 atlas layout')
                        buffer=(C.c_uint8*size).from_buffer(blocks)
                        self.material_etc1_upload_attempts+=1
                        if self._library.rv_set_material_atlas_etc1(context,buffer,size,mw,mh)>0 and self._library.rv_material_storage_bits(context)==4:
                            uploaded=True;self.material_format='etc1';self.material_fallback_reason=None
                            self.material_storage_bits=4;self.material_gpu_bytes=size
                            self.material_bytes_uploaded+=size;self.material_gpu_bytes_uploaded+=size
                        else:self.material_fallback_reason='ETC1 native upload rejected; using validated PNG'
            if not uploaded:
                buffer = (C.c_uint8*len(pixels)).from_buffer(pixels)
                if self._library.rv_set_material_atlas(context,buffer,mw,mh,stride) <= 0:
                    raise RuntimeError('Native material atlas upload failed')
                self.material_format='rgba8' if self._material_preference=='rgba8' else 'rgb565'
                self.material_storage_bits=32 if self.material_format=='rgba8' else 16
                self.material_gpu_bytes=mw*mh*self.material_storage_bits//8
                self.material_bytes_uploaded+=mw*mh*4
                self.material_gpu_bytes_uploaded+=mw*mh*(4 if self.material_format=='rgba8' else 2)
            self._try_material_mips(context,material)
            self.material_uploads += 1
            self.material_loaded = True
            self._hud = self._hud_factory()
            self._hud.use_bgra=(sys.byteorder=='little' and hasattr(self._library,'rv_set_hud_bgra')
                                and hasattr(self._library,'rv_patch_hud_bgra'))
            self._hud_format=None
            detail = self._library.rv_info(context) if hasattr(self._library, 'rv_info') else None
            if isinstance(detail, bytes):detail = detail.decode('utf-8', 'replace')
            try:self._backend_info = json.loads(detail) if detail else None
            except (ValueError,TypeError):self._backend_info = detail
            description = str(detail or '').lower()
            software = any(name in description for name in ('llvmpipe','softpipe','swiftshader','swrast','software rasterizer'))
            hardware = any(name in description for name in ('mali','panfrost','nvidia','radeon','intel','adreno','vivante','powervr'))
            self._accelerated = hardware and not software
            self._status('gles3d', detail=self._backend_info, library=str(self.library_path), accelerated=self._accelerated, material_format=self.material_format, material_fallback_reason=self.material_fallback_reason)
            return True
        except (OSError, ValueError, TypeError, RuntimeError, ImportError) as error:
            self._fallback_to_cairo('native-context-unavailable: ' + str(error))
            return False

    def _try_material_mips(self,context,material):
        """Optional enhancement failure never discards a valid base texture."""
        self.material_mips_levels=self.material_mips_gpu_bytes=0
        if not self._material_mips_requested:
            self.material_mips_fallback_reason='disabled by INK_GL_MATERIAL_MIPS';return
        self.material_mips_fallback_reason=None
        if self.material_format!='etc1' or self._material_etc1 is None:
            self.material_mips_fallback_reason='mips require validated ETC1 base';return
        if not self._mips_api:
            self.material_mips_fallback_reason='compiled mip optional API absent/incompatible';return
        try:
            base=self._material_etc1.get('sha256');png=material.get('sha256')
            compiled_base=self._library.rv_material_mips_base_sha256()
            compiled_png=self._library.rv_material_mips_source_sha256()
            if not (isinstance(base,str) and isinstance(png,str) and len(base)==len(png)==64
                    and all(c in '0123456789abcdef' for c in base+png)
                    and compiled_base==base.encode('ascii') and compiled_png==png.encode('ascii')):
                self.material_mips_fallback_reason='compiled mip PNG/PKM SHA mismatch';return
            before=self._library.rv_material_mips_upload_bytes(context)
            self.material_mips_upload_attempts+=1
            okay=self._library.rv_set_material_mips(context,base.encode('ascii'),png.encode('ascii'))
            transferred=max(0,self._library.rv_material_mips_upload_bytes(context)-before)
            self.material_mips_gpu_bytes_uploaded+=transferred
            self.material_gpu_bytes_uploaded+=transferred;self.material_bytes_uploaded+=transferred
            levels=self._library.rv_material_mips_levels(context);size=self._library.rv_material_mips_gpu_bytes(context)
            if okay>0 and levels==10 and size==174776:
                self.material_mips_levels=levels;self.material_mips_gpu_bytes=size;self.material_gpu_bytes+=size
            else:
                reason=self._library.rv_material_mips_reason(context)
                self.material_mips_fallback_reason=reason.decode('utf8','replace') if isinstance(reason,bytes) else 'compiled mip upload rejected; base retained'
        except (AttributeError,OSError,ValueError,TypeError,UnicodeError) as error:
            self.material_mips_fallback_reason='compiled mip optional API failed: '+str(error)

    def configure_depth(self, depth):
        self._depth = depth
        if self._fallback:self._fallback.configure_depth(depth)

    def warm(self, target, width=W, height=H):
        if not self.is_native:self._get_fallback().warm(target, width, height)

    def event(self, event):
        if self._fallback:self._fallback.event(event)
        if event.get('kind')=='start':
            self._hit_ids.clear();self._last_distance=None
        elif event.get('kind') in ('hit','shield_break') and event.get('item_kind') in ('cart','ramp'):
            entity_id=_entity_id(event.get('id'))
            if entity_id:
                self._hit_ids[entity_id]=True;self._hit_ids.move_to_end(entity_id)
                while len(self._hit_ids)>MAX_ENTITIES:self._hit_ids.popitem(last=False)
        kind = EVENTS.get(event.get('kind'))
        if kind == 0 and event.get('item_kind') in ('shield','magnet','jetpack','springboots','coinboost'):
            kind = 4
        lane = event.get('lane',1)
        if lane not in (0,1,2):lane = 1
        if self._context is not None and kind is not None and hasattr(self._library,'rv_event'):
            try:
                self._library.rv_event(self._context,kind,lane)
                self.events_forwarded += 1
            except (OSError,ValueError,TypeError,RuntimeError) as error:
                self.event_error = str(error)[:240]
                if self.on_status:self.on_status('renderer_warning',renderer='gles3d',
                    operation='event',reason=self.event_error,context_generation=self.context_generation)

    def update_effects(self, dt):
        self.clock += min(.1, max(0., _number(dt)))
        if self._fallback:self._fallback.update_effects(dt)

    def resize(self, width, height):
        size = (max(1, int(width)), max(1, int(height)))
        if self._context is not None and size != self._size:
            if self._library.rv_resize(self._context, *size) <= 0:
                self._fallback_to_cairo('native-resize-failed')
                return False
            self._size = size
        return self.is_native

    def render(self, target, snapshot, width, height):
        if self._context is None:
            self._get_fallback().render(target, snapshot, width, height)
            return False
        begin = time.monotonic()
        try:
            if not self.resize(width, height):
                self._get_fallback().render(target, snapshot, width, height)
                return False
            for name, default in (('distance', 0.), ('player_x', 1.), ('jump_height', 0.),
                                  ('slide_phase', -1.), ('shield_time', 0.), ('magnet_time', 0.),
                                  ('player_y',0.)):
                setattr(self._frame, name, _number(snapshot.get(name, default), default))
            self._frame.clock = self.clock
            self._frame.state = STATES.get(snapshot.get('state'), 0)
            if self._last_distance is not None and self._frame.distance+1e-4<self._last_distance:
                self._hit_ids.clear()
            self._last_distance=self._frame.distance
            # Explicit state channel; do not hide new timers in old Frame fields.
            times=tuple(min(99.,max(0.,_number(snapshot.get(name,0.))))
                        for name in ('jetpack_time','boots_time','coinboost_time'))
            if self._library.rv_set_powerups(self._context,*times)<=0:
                raise RuntimeError('Native power-up state rejected')
            count = 0
            for item in snapshot.get('entities', ()):
                kind = KINDS.get(item.get('kind'))
                if kind is None or item.get('collected'):continue
                lane = item.get('lane')
                if lane not in (0, 1, 2):continue
                entity_id=_entity_id(item.get('id'))
                # Bit 0 is the original collected flag (collected entities
                # are skipped above); bit 1 denotes an actual train impact.
                # Bits 2..30 carry stable engine identity, never loop order.
                impact=kind in (0,6) and (bool(item.get('contacted')) or entity_id in self._hit_ids)
                flags=(entity_id<<2)|(2 if impact else 0)
                self._entities[count] = Entity(kind, lane, _number(item.get('z')), flags,_number(item.get('y')))
                count += 1
                if count == MAX_ENTITIES:break
            hud_begin = time.monotonic()
            update = self._hud.update(snapshot, begin)
            if update is not None:
                updates=update if isinstance(update,(list,tuple)) else [update]
                if any(item.patch and (self.force_full_hud or not hasattr(self._library,
                        'rv_patch_hud_bgra' if item.format=='BGRA' else 'rv_patch_hud')) for item in updates):
                    updates=[self._hud.full_upload()]
                self.hud_prepare_ms.append((time.monotonic()-hud_begin)*1000.)
                for item in updates:
                    pixels=item.pixels;span=(item.height-1)*item.stride+item.width*4
                    if item.format not in ('RGBA','BGRA') or item.stride<item.width*4 or len(pixels)!=span:
                        raise RuntimeError('Invalid HUD texture layout/byte count')
                    if self._hud_format is not None and self._hud_format!=item.format:
                        raise RuntimeError('HUD texture format changed within native context')
                    buffer=(C.c_uint8*len(pixels)).from_buffer(pixels)
                    suffix='_bgra' if item.format=='BGRA' else ''
                    upload_begin=time.monotonic()
                    if item.patch:
                        okay=getattr(self._library,'rv_patch_hud'+suffix)(self._context,buffer,item.x,item.y,
                            item.width,item.height,item.stride)
                    else:
                        okay=getattr(self._library,'rv_set_hud'+suffix)(self._context,buffer,item.width,item.height,item.stride)
                    if okay<=0:raise RuntimeError('HUD texture patch failed' if item.patch else 'HUD texture upload failed')
                    self._hud_format=item.format
                    self.hud_upload_ms.append((time.monotonic()-upload_begin)*1000.)
                    self.hud_uploads+=1;self.hud_patch_uploads+=int(item.patch);self.hud_full_uploads+=int(not item.patch)
                    self.hud_bytes_uploaded+=item.width*item.height*4
            if self._library.rv_frame(self._context, C.byref(self._frame), self._entities, count) <= 0:
                raise RuntimeError('EGL frame submission failed')
            self.frames += 1
            self.last_native_ms = (time.monotonic()-begin)*1000.
            return True
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            self._fallback_to_cairo('native-frame-failed: ' + str(error))
            self._get_fallback().render(target, snapshot, width, height)
            return False

    def release_native_output(self):
        context, self._context = self._context, None
        if context is not None and self._library is not None:
            self._library.rv_destroy(context)
        self._size, self._hud = None, None
        self.material_loaded = False
        self.material_mips_levels=self.material_mips_gpu_bytes=0
        self.material_gpu_bytes=self.material_storage_bits=0
        if self._fallback:self._fallback.release_native_output()

    def readback(self):
        """Explicit diagnostics only; normal frames never read GPU pixels."""
        if self._context is None or not hasattr(self._library, 'rv_readback'):
            raise RuntimeError('Native frame readback is unavailable')
        width, height = self._size
        pixels = bytearray(width*height*4)
        buffer = (C.c_uint8*len(pixels)).from_buffer(pixels)
        if self._library.rv_readback(self._context, buffer, width*4) <= 0:
            raise RuntimeError('Native frame readback failed')
        return pixels

    def native_rendering_flags(self):
        # Optional additive ABI; legacy libraries remain usable, final QA requires it.
        library=self._library;context=self._context
        if not context or library is None or not hasattr(library,'rv_effective_flags'):return None
        raw=library.rv_effective_flags(context)
        if not raw:return None
        result=json.loads(raw.decode('utf8'))
        if result.get('schema')!=1 or not isinstance(result.get('effective'),dict):raise RuntimeError('Invalid native rendering flags snapshot')
        result['effective']['INK_RUNNER_HUD_FULL_UPLOAD']='1' if self.force_full_hud else '0'
        result['effective_source']='native-context-and-renderer-state'
        return result

    def metrics(self):
        if self.is_native:
            def p95(values):
                return sorted(values)[max(0,math.ceil(len(values)*.95)-1)] if values else None
            return dict(presentation='egl-gles3d-single-swap', accelerated=self._accelerated,
                        native_abi=2,native_rendering_flags=self.native_rendering_flags(),
                        material_api=1,material_loaded=self.material_loaded,
                        material_uploads=self.material_uploads,material_decodes=self.material_decodes,
                        material_bytes_uploaded=self.material_bytes_uploaded,
                        material_gpu_bytes_uploaded=self.material_gpu_bytes_uploaded,
                        material_gpu_bytes=self.material_gpu_bytes,material_storage_bits=self.material_storage_bits,
                        material_etc1_upload_attempts=self.material_etc1_upload_attempts,
                        material_source_rgba_bytes=self.material_source_rgba_bytes,
                        material_format=self.material_format,material_fallback_reason=self.material_fallback_reason,
                        material_etc1_api=self._etc1_api,material_etc1_loads=self.material_etc1_loads,
                        material_mips_requested=self._material_mips_requested,material_mips_api=self._mips_api,
                        material_mips_levels=self.material_mips_levels,material_mips_gpu_bytes=self.material_mips_gpu_bytes,
                        material_mips_upload_attempts=self.material_mips_upload_attempts,
                        material_mips_gpu_bytes_uploaded=self.material_mips_gpu_bytes_uploaded,
                        material_mips_fallback_reason=self.material_mips_fallback_reason,
                        material_etc1_sha256=self._material_etc1.get('sha256') if self._material_etc1 else None,
                        material_etc1_file_bytes=self._material_etc1.get('file_bytes') if self._material_etc1 else None,
                        material_etc1_payload_bytes=len(self._material_etc1['blocks']) if self._material_etc1 else None,
                        material_size=[self._material['width'],self._material['height']] if self._material else None,
                        material_sha256=self._material.get('sha256') if self._material else None,
                        backend_info=self._backend_info,
                        frames=self.frames, hud_uploads=self.hud_uploads,
                        hud_full_uploads=self.hud_full_uploads,hud_patch_uploads=self.hud_patch_uploads,
                        hud_bytes_uploaded=self.hud_bytes_uploaded,
                        hud_patch_supported=hasattr(self._library,'rv_patch_hud'),
                        hud_force_full_upload=self.force_full_hud,
                        hud_texture_format=self._hud_format,hud_zero_copy_bgra=bool(self._hud.use_bgra),
                        hud_prepare_ms_p95=p95(self.hud_prepare_ms),hud_upload_ms_p95=p95(self.hud_upload_ms),
                        native_events_forwarded=self.events_forwarded,native_event_error=self.event_error,
                        impacted_train_ids=len(self._hit_ids),
                        context_generation=self.context_generation,
                        hud_max_hz=10, hud_size=[W, H], output_size=list(self._size),
                        last_native_ms=self.last_native_ms,
                        glyph_cache=len(self._hud.glyphs) if self._hud else 0,
                        hud_template_hits=getattr(self._hud,'template_hits',0),
                        hud_template_misses=getattr(self._hud,'template_misses',0),
                        hud_template_bytes=getattr(self._hud,'_template_bytes',0),
                        native_library=str(self.library_path))
        result = dict(self._get_fallback().metrics())
        result.update(accelerated=False, fallback_reason=self._reason or 'native-window-not-attached',
                      material_loaded=False,material_uploads=self.material_uploads,
                      material_decodes=self.material_decodes,material_bytes_uploaded=self.material_bytes_uploaded)
        return result
