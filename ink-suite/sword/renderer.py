"""Cached native Cairo artwork for Yu Jian Shan He; no camera or network IO.

The approved warm landscape, gongbi figures and twelve effect beats are
cached at device scale. Only bounded tile blits and a short trail run per frame. The logical
1280x720 plane matches the application's normalized pointer and hit regions.
"""
from collections import OrderedDict, deque
import math
from pathlib import Path
import cairo
from .alpha_blit import AlphaBlitter
from .engine import MAX_EFFECTS
from .gongbi_art import GongbiArt, EFFECT_STEPS

ROOT = Path(__file__).resolve().parent
W, H = 1280., 720.
INK = (.12, .19, .20)
JADE = (.20, .43, .40)
GOLD = (.78, .65, .40)
PAPER = (.96, .95, .90)
RED = (.68, .20, .15)
START_RECT = (.35, .60, .65, .74)
SCENES = ('竹林', '群山', '首领战')
BULLET_STYLES = {'red':0,'scarlet':0,'aimed':0,'cinnabar':0,'gold':1,'talisman':1,'fan':1,
                 'ink':2,'diamond':2,'ring':2,'violet':3,'purple':3,'spiral':3,'indigo':3}


def clamp(v, lo=0., hi=1.):
    return max(lo, min(hi, v))


class Renderer:
    def __init__(self, assets=None):
        self.blitter = AlphaBlitter()
        self._frame_blitter = None
        assets = Path(assets) if assets else ROOT / 'assets'
        self.gongbi = GongbiArt(assets / 'gongbi')
        self.cache = OrderedDict()
        self.letters = OrderedDict()
        self.background = None
        self.shaded_backgrounds = {}
        self.viewport_key = None
        self.viewport_bytes = None
        self.viewport_surface = None
        self.viewport_surface_allocations = 0
        self.hud_surface = None
        self.hud_surface_allocations = 0
        self.hud_key = None
        self.hud_component_keys = None
        self.hud_component_bounds = None
        self._hud_damage = None
        self._hud_selected_components = None
        self.hud_component_redraws = [0] * 7
        self._painting_hud = False
        self.leaf_surface = None
        self.cloud_cache = {}
        self.fan_cache = {}
        self.size = None
        self.trail = deque(maxlen=24)
        self.last_time = None
        self.last_player = None
        self.gate_cache={}
        self.effect_cache={}
        self.collect_tile_allocations=0
        self.projectile_cache=OrderedDict()
        self.enemy_cache=OrderedDict()
        self._visual_enemy_health={}
        self._visual_hits=OrderedDict()
        self._visual_hit_time=None
        self.projectile_tile_allocations=0
        self.panel_cache=OrderedDict()

    def _text_tile(self, c, text, size, color, serif):
        text = str(text)
        matrix = c.get_matrix()
        sx,sy = abs(matrix.xx),abs(matrix.yy)
        key = (text, round(size, 2), tuple(color[:3]), serif,round(sx,5),round(sy,5))
        if key not in self.letters:
            probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
            probe.select_font_face('Noto Serif CJK SC' if serif else 'Noto Sans CJK SC',
                                   cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            probe.set_font_size(size)
            ext = probe.text_extents(text)
            tw, th = max(1, int(ext[4] + 12)), max(1, int(size * 1.65 + 8))
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                         max(1,math.ceil(tw*sx)),max(1,math.ceil(th*sy)))
            p = cairo.Context(surface)
            p.scale(sx,sy)
            p.select_font_face('Noto Serif CJK SC' if serif else 'Noto Sans CJK SC',
                              cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            p.set_font_size(size)
            p.set_source_rgb(*color[:3])
            p.move_to(5, size * 1.18)
            p.show_text(text)
            self.letters[key] = (surface, ext[4])
            if len(self.letters) > 180:
                self.letters.popitem(last=False)
        surface, advance = self.letters[key]
        self.letters.move_to_end(key)
        return surface, advance

    def _text(self, c, text, x, y, size, color=PAPER, center=False, serif=False):
        surface, advance = self._text_tile(c, text, size, color, serif)
        # Text is cached at the final device scale too. Repeated bilinear
        # resampling of fading labels was another ARM hotspot during bursts.
        dx,dy=c.user_to_device(x-advance/2-5 if center else x-5,y-size*1.18)
        left,top=round(dx),round(dy)
        c.save();c.identity_matrix();c.rectangle(left,top,surface.get_width(),surface.get_height());c.clip()
        self._paint_tile(c,surface,left,top,clamp(color[3]) if len(color)>3 else 1.)
        c.restore()

    def _paint_tile(self,c,surface,left,top,alpha=1.,regions=None):
        # All production sprites have already been rasterized at device scale.
        # The native path preserves Cairo premultiplied OVER arithmetic; any
        # unsupported target, clip or alpha falls back before touching pixels.
        if self._painting_hud and self._paint_hud_tile(c,surface,left,top,alpha):
            return
        frame=self._frame_blitter
        if (alpha==1. and frame is not None and c.get_target()==frame.target
                and frame.paint_tile(surface,left,top,regions=regions)):
            return
        if self.blitter.paint(c,surface,left,top,alpha):
            return
        c.set_source_surface(surface,left,top)
        if alpha>=.999:
            c.paint()
        else:
            c.paint_with_alpha(alpha)

    def _paint_hud_tile(self,c,surface,left,top,alpha):
        # HUD is a Cairo-owned ARGB32 scratch layer, not the frame's opaque
        # destination. Its tiles already have tight integer clips. Let pixman
        # compose them directly instead of rechecking format/alias/clip lists
        # for every glyph and panel through the general native adapter.
        if c.get_target()!=self.hud_surface:return False
        c.set_source_surface(surface,left,top)
        if alpha>=.999:c.paint()
        else:c.paint_with_alpha(alpha)
        return True

    def _rebuild(self, width, height):
        # A quiet, opaque paper landscape is rasterized only on resize.
        # No scrolling image, moving foreground, blur or full-screen alpha pass.
        bh, bw = max(1,height), max(1,width)
        self.gongbi.prepare(bw,bh)
        self.background = self.gongbi.background(bw,bh)
        self.shaded_backgrounds = {}
        self.viewport_key = None
        self.viewport_bytes = None
        self.viewport_surface = None
        self.leaf_surface = None
        self.cloud_cache.clear()
        self.fan_cache.clear()
        self.size = (width, height)
        self.cache.clear()
        self.gate_cache.clear()
        self.effect_cache.clear()
        self.projectile_cache.clear()
        self.enemy_cache.clear()
        self.panel_cache.clear()
        self.hud_component_keys = None
        self.hud_component_bounds = None
        # Build bounded sprite orientations while the start screen loads,
        # rather than hitching on the first collision, spell or boss arrival.
        warm=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
        warm.scale(width/W,height/H)
        for style in range(4):
            for radius in (4,5,6,7,8,9,10,12):self._projectile_tile(style,radius,False)
        for radius in (3,4,5,6):self._projectile_tile(0,radius,True)
        for kind,radius in (('enemy',.032),('elite',.052),('boss',.073),('power',.022),('orb',.016)):
            for style in range(4):self._enemy_tile(kind,radius*H,style)
        for label in ('通过','能量','受到攻击','击破','胜利','清屏','火力升级','擦弹'):
            self._text(warm,label,0,0,22,JADE,serif=True)
        for red in (False,True,'collect'):
            for frame in range(EFFECT_STEPS):self._effect_tile(frame,red)

    def _background(self, c, width, height, snap):
        if self.size != (width, height):
            self._rebuild(width, height)
        t = float(snap.get('time', 0.))
        duration = max(1., float(snap.get('duration', 75.)))
        pan = 0
        phase = snap.get('phase', 'ready')
        shade = None
        panorama = self.shaded_backgrounds.get(shade, self.background)
        target = c.get_target()
        # Production, like FruitSlice, owns a full RGB24 backbuffer. The
        # panorama moves by one integer pixel every several video frames.
        # Reuse that opaque viewport using a contiguous memory copy; do not
        # resample or alpha-composite the full screen again for unchanged pan.
        if (isinstance(target,cairo.ImageSurface) and target.get_format()==cairo.FORMAT_RGB24
                and (target.get_width(),target.get_height())==(width,height)
                and tuple(c.get_matrix())==(1.,0.,0.,1.,0.,0.)
                and c.clip_extents()==(0.,0.,float(width),float(height))):
            key = (self.size,shade,round(pan))
            if key != self.viewport_key:
                if self.viewport_surface is None:
                    self.viewport_surface=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
                    self.viewport_surface_allocations+=1
                    self.viewport_bytes=self.viewport_surface.get_data()
                vc=cairo.Context(self.viewport_surface)
                vc.set_source_surface(panorama,-round(pan),0);vc.paint()
                self.viewport_surface.flush()
                self.viewport_key=key
            target.flush()
            target.get_data()[:] = self.viewport_bytes
            target.mark_dirty()
            return
        c.set_source_surface(panorama, -round(pan), 0)
        c.paint()

    def _device_blit(self,c, surface, x, y):
        """Paint a pre-scaled alpha tile once, without per-frame resampling."""
        dx,dy = c.user_to_device(x,y)
        c.save(); c.identity_matrix()
        left,top = round(dx),round(dy)
        c.rectangle(left,top,surface.get_width(),surface.get_height()); c.clip()
        self._paint_tile(c,surface,left,top); c.restore()

    def _panel(self,c,x,y,w,h,alpha=.88):
        # Overlay panels have a large translucent fill. Rasterize their
        # unchanged frame once and blend the bounded tile with the same
        # native path as the illustrated sprites. Preserve subpixel origin.
        matrix=c.get_matrix()
        if matrix.xy or matrix.yx or matrix.xx<=0 or matrix.yy<=0:
            self._panel_detail(c,x,y,w,h,alpha)
            return
        sx,sy=matrix.xx,matrix.yy
        dx,dy=c.user_to_device(x,y)
        left,top=math.floor(dx)-2,math.floor(dy)-2
        ox,oy=dx-left,dy-top
        key=(w,h,alpha,sx,sy,round(ox,8),round(oy,8),c.get_antialias())
        if key not in self.panel_cache:
            tile=cairo.ImageSurface(cairo.FORMAT_ARGB32,math.ceil(w*sx)+6,math.ceil(h*sy)+6)
            p=cairo.Context(tile);p.set_antialias(c.get_antialias())
            p.translate(ox,oy);p.scale(sx,sy)
            self._panel_detail(p,0,0,w,h,alpha)
            self.panel_cache[key]=tile
            if len(self.panel_cache)>48:self.panel_cache.popitem(last=False)
        self.panel_cache.move_to_end(key)
        tile=self.panel_cache[key]
        c.save();c.identity_matrix();c.rectangle(left,top,tile.get_width(),tile.get_height());c.clip()
        self._paint_tile(c,tile,left,top);c.restore()

    @staticmethod
    def _panel_detail(c, x, y, w, h, alpha=.88):
        c.set_source_rgba(*INK, alpha)
        c.rectangle(x, y, w, h)
        c.fill()
        c.set_source_rgba(*PAPER, .18)
        c.set_line_width(1)
        c.rectangle(x + .5, y + .5, w - 1, h - 1)
        c.stroke()

    @staticmethod
    def _boss(snapshot):
        return next((entity for entity in snapshot.get('entities',())
                     if entity.get('kind')=='boss' and not entity.get('hit')),None)

    def _hud(self, c, snap, ui):
        boss=self._boss(snap)
        boss_ratio=clamp(float(boss.get('health',0))/max(.001,float(boss.get('max_health',1)))) if boss else -1.
        # Two-percent meter steps reduce redraw churn without changing gameplay.
        key=(self.size,int(snap.get('scene',0)),int(snap.get('score',0)),int(snap.get('combo',0)),
             int(snap.get('lives',5)),round(float(snap.get('energy',0))*50),
             math.ceil(float(snap.get('duration',120))-float(snap.get('time',0))),
             int(snap.get('power',1)),int(snap.get('kills',0)),int(snap.get('graze',0)),
             str(snap.get('wave_name','')),str(snap.get('boss_name','首领')),
             int(snap.get('boss_phase',0)),round(boss_ratio*50))
        if self.hud_key!=key:
            # Preserve the original global invalidation cadence. Within an
            # accepted update, use exact rendered values: a score change also
            # refreshes energy/health inside their two-percent meter buckets.
            component_keys = self._hud_component_keys(snap, boss)
            bounds = self._hud_component_rects(snap, boss)
            fresh = (self.hud_key is None or self.hud_surface is None or
                     self.hud_component_keys is None or
                     (self.hud_surface.get_width(),self.hud_surface.get_height())!=self.size)
            changed = range(7) if fresh else [i for i in range(7)
                if component_keys[i] != self.hud_component_keys[i]]
            old_bounds = self.hud_component_bounds or bounds
            selected = set(changed)
            # Repaint complete intersecting components, including old/new
            # text extents. Closure ensures no selected painter touches an
            # uncleared neighbor. Rendering then keeps the original clip:
            # changing its rectangle decomposition can alter Cairo vector
            # antialias rounding even well inside a damaged rectangle.
            while True:
                damage = [rect for i in selected for rect in old_bounds[i]+bounds[i]]
                extra = {i for i in range(7) if i not in selected and any(
                    self._rects_intersect(rect,other)
                    for rect in old_bounds[i]+bounds[i] for other in damage)}
                if not extra:break
                selected.update(extra)
            if self.hud_surface is not None:
                self.blitter.invalidate_source(self.hud_surface)
            if self.hud_surface is None or (self.hud_surface.get_width(),self.hud_surface.get_height())!=self.size:
                self.hud_surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,*self.size)
                self.hud_surface_allocations+=1
            self.hud_component_bounds = bounds
            self._hud_damage = damage
            self._hud_selected_components = None
            p=self._hud_context()
            self._painting_hud=True
            try:self._hud_detail(p,snap,ui)
            finally:
                self._painting_hud=False
                self._hud_damage=None
                self._hud_selected_components=None
            self.hud_surface.flush()
            self.hud_key=key
            self.hud_component_keys=component_keys
        dx,dy=c.user_to_device(0,0)
        c.save();c.identity_matrix()
        for rect in self._hud_region_rects(dx,dy):c.rectangle(*rect)
        c.clip()
        regions=[tuple(rect) for rect in c.copy_clip_rectangle_list()]
        self._paint_tile(c,self.hud_surface,dx,dy,regions=regions);c.restore()

    def _hud_region_rects(self,dx=0.,dy=0.):
        sx,sy=self.size[0]/W,self.size[1]/H
        return [(dx+math.floor(x*sx),dy+math.floor(y*sy),math.ceil(w*sx)+1,math.ceil(h*sy)+1)
                for x,y,w,h in ((23,21,281,82),(313,28,165,73),(479,21,322,69),
                               (979,21,278,82),(359,94,562,45),
                               (403,636,474,66),(23,647,371,55))]

    def _hud_context(self):
        # Only these exact device-pixel regions reach the final frame. Clear
        # and repaint their union, preserving every composited pixel while
        # avoiding a screen-size transparent clear when score/graze changes.
        p=cairo.Context(self.hud_surface)
        for rect in self._hud_region_rects():p.rectangle(*rect)
        p.clip()
        if self._hud_damage is not None:
            p.save()
            for rect in self._hud_damage:p.rectangle(*rect)
            p.clip()
            p.set_operator(cairo.OPERATOR_CLEAR);p.paint();p.restore()
            self._hud_selected_components = [i for i in range(7) if any(
                self._rects_intersect(rect,damage)
                for rect in self.hud_component_bounds[i] for damage in self._hud_damage)]
        else:
            p.set_operator(cairo.OPERATOR_CLEAR);p.paint();p.set_operator(cairo.OPERATOR_OVER)
        p.scale(self.size[0]/W,self.size[1]/H)
        return p

    @staticmethod
    def _hud_component_keys(snap,boss):
        scene=min(2,max(0,int(snap.get('scene',0))))
        seconds=max(0,int(math.ceil(float(snap.get('duration',120))-float(snap.get('time',0)))))
        health=clamp(float(boss.get('health',0))/max(.001,float(boss.get('max_health',1)))) if boss else None
        return (int(snap.get('score',0)),
                (int(snap.get('combo',0)),int(snap.get('graze',0))),
                (str(snap.get('wave_name') or SCENES[scene])[:14],int(snap.get('kills',0)),seconds),
                (int(snap.get('lives',5)),max(1,min(4,int(snap.get('power',1))))),
                (str(snap.get('boss_name') or '首领')[:12],health),
                clamp(float(snap.get('energy',0))),0)

    def _hud_component_rects(self,snap,boss):
        # Base bounds include vector strokes and fixed labels. Add the actual
        # cached text tile rectangles for changing strings, including unusually
        # long scores. A component may therefore damage a neighboring region.
        bounds=[[rect] for rect in self._hud_region_rects()]
        context=cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1,1))
        context.scale(self.size[0]/W,self.size[1]/H)
        scene=min(2,max(0,int(snap.get('scene',0))))
        seconds=max(0,int(math.ceil(float(snap.get('duration',120))-float(snap.get('time',0)))))
        labels=[(0,f"{int(snap.get('score',0)):06d}",99,69,31,GOLD,False,True),
                (1,f"连击 {int(snap.get('combo',0))}",319,53,20,INK,False,True),
                (1,f"擦弹 {int(snap.get('graze',0))}",319,83,17,INK,False,False),
                (2,str(snap.get('wave_name') or SCENES[scene])[:14],640,54,23,PAPER,True,True),
                (2,f"击破 {int(snap.get('kills',0))}  ·  {seconds:02d} 秒",640,77,15,(.73,.81,.76),True,False)]
        if boss:labels.append((4,str(snap.get('boss_name') or '首领')[:12],376,121,18,GOLD,False,True))
        for index,label,x,y,size,color,center,serif in labels:
            tile,advance=self._text_tile(context,label,size,color,serif)
            dx,dy=context.user_to_device(x-advance/2-5 if center else x-5,y-size*1.18)
            bounds[index].append((round(dx),round(dy),tile.get_width(),tile.get_height()))
        return bounds

    @staticmethod
    def _rects_intersect(a,b):
        return (a[0]<b[0]+b[2] and b[0]<a[0]+a[2] and
                a[1]<b[1]+b[3] and b[1]<a[1]+a[3])

    def _hud_detail(self,c,snap,ui):
        # Full-clear diagnostic contexts retain the complete painter. The
        # production clear selects complete components in their original order.
        for index,paint in enumerate((self._hud_score,self._hud_combo,self._hud_wave,
                self._hud_lives,self._hud_boss,self._hud_energy,self._hud_guide)):
            if self._hud_selected_components is None or index in self._hud_selected_components:
                self.hud_component_redraws[index]+=1
                paint(c,snap,ui)

    def _hud_score(self,c,snap,ui):
        self._panel(c,25,23,276,77,.92)
        c.set_source_rgb(*RED);c.rectangle(37,35,47,51);c.fill()
        self._text(c,'剑',61,70,29,center=True,serif=True)
        self._text(c,f"{int(snap.get('score',0)):06d}",99,69,31,GOLD,serif=True)

    def _hud_combo(self,c,snap,ui):
        self._text(c,f"连击 {int(snap.get('combo',0))}",319,53,20,INK,serif=True)
        self._text(c,f"擦弹 {int(snap.get('graze',0))}",319,83,17,INK)

    def _hud_wave(self,c,snap,ui):
        scene=min(2,max(0,int(snap.get('scene',0))))
        self._panel(c,481,23,318,65,.90)
        self._text(c,str(snap.get('wave_name') or SCENES[scene])[:14],640,54,23,PAPER,center=True,serif=True)
        seconds=max(0,int(math.ceil(float(snap.get('duration',120))-float(snap.get('time',0)))))
        self._text(c,f"击破 {int(snap.get('kills',0))}  ·  {seconds:02d} 秒",640,77,15,(.73,.81,.76),center=True)

    def _hud_lives(self,c,snap,ui):
        self._panel(c,981,23,274,77,.92)
        self._text(c,'生命',997,54,18)
        lives=int(snap.get('lives',5))
        for i in range(5):
            x=1061+i*29
            c.set_source_rgba(*(GOLD if i<lives else PAPER),1. if i<lives else .2)
            c.move_to(x,40);c.line_to(x+7,51);c.line_to(x,62);c.line_to(x-7,51);c.close_path();c.fill()
        power=max(1,min(4,int(snap.get('power',1))))
        self._text(c,f'火力等级  {power} / 4',1001,85,17,(.66,.88,.86),serif=True)

    def _hud_boss(self,c,snap,ui):
        boss=self._boss(snap)
        if boss:
            self._panel(c,361,96,558,40,.91)
            self._text(c,str(snap.get('boss_name') or '首领')[:12],376,121,18,GOLD,serif=True)
            health=clamp(float(boss.get('health',0))/max(.001,float(boss.get('max_health',1))))
            c.set_source_rgba(*PAPER,.18);c.rectangle(535,110,365,10);c.fill()
            c.set_source_rgb(*RED);c.rectangle(535,110,365*health,10);c.fill()
            c.set_source_rgb(*GOLD);c.rectangle(535,110,365*health,2);c.fill()

    def _hud_energy(self,c,snap,ui):
        energy=clamp(float(snap.get('energy',0)))
        self._panel(c,405,654,470,36,.92)
        self._text(c,'能量',424,679,19,GOLD,serif=True)
        c.set_source_rgba(1,1,1,.13);c.rectangle(479,667,221,8);c.fill()
        c.set_source_rgb(*(GOLD if energy>=.999 else JADE));c.rectangle(479,667,221*energy,8);c.fill()
        self._text(c,'清屏就绪' if energy>=.999 else f'{energy:.0%}',724,679,17,PAPER,serif=True)

    def _hud_guide(self,c,snap,ui):
        # No permanent tutorial over the playfield. Kept as a no-op HUD
        # component so dirty-region ordering and cache contracts stay stable.
        pass

    def _parallax(self, c, t, scene):
        # Deliberately still: moving leaves/cloud arcs obscured hostile paths.
        pass

    @staticmethod
    def _style(value):
        if isinstance(value,(int,float)):return int(value)%4
        return BULLET_STYLES.get(str(value),0)

    def _projectile_tile(self,style,radius,shot=False):
        # Finite radius/style buckets preserve all projectiles while bounding
        # raster storage. Only decoration is quantized; physics stays exact.
        radius=max(2,min(24,int(round(radius))))
        style=self._style(style)
        key=(bool(shot),style if not shot else 0,radius)
        if key in self.projectile_cache:
            self.projectile_cache.move_to_end(key)
            return self.projectile_cache[key]
        sx,sy=self.size[0]/W,self.size[1]/H
        ex=radius+4;ey=radius*(3.7 if shot else 1.35)+4
        ww,hh=max(2,2*math.ceil(ex*sx)),max(2,2*math.ceil(ey*sy))
        tile=cairo.ImageSurface(cairo.FORMAT_ARGB32,ww,hh)
        p=cairo.Context(tile);p.translate(ww/2,hh/2);p.scale(sx,sy)
        p.set_antialias(cairo.ANTIALIAS_FAST)
        if shot:
            # A closed dark-teal edge survives the bright warm paper. The
            # pale jade core and slender vertical shape distinguish allies.
            p.set_source_rgb(.12,.32,.29)
            p.move_to(0,-radius*3.65);p.line_to(radius*.72,radius*1.9)
            p.line_to(0,radius*3.65);p.line_to(-radius*.72,radius*1.9);p.close_path();p.fill()
            p.set_source_rgb(.76,.92,.83)
            p.move_to(0,-radius*3.4);p.line_to(radius*.46,radius*1.9)
            p.line_to(0,radius*3.4);p.line_to(-radius*.46,radius*1.9);p.close_path();p.fill()
            p.set_source_rgba(*PAPER,.75);p.set_line_width(.8)
            p.move_to(0,-radius*2.4);p.line_to(0,radius*1.7);p.stroke()
        elif style in (1,2):
            col=(.76,.46,.15) if style==1 else (.48,.25,.48)
            p.set_source_rgb(*INK)
            p.move_to(0,-radius*1.32);p.line_to(radius+2,0);p.line_to(0,radius*1.32);p.line_to(-radius-2,0);p.close_path();p.fill()
            p.set_source_rgb(*col)
            p.move_to(0,-radius*1.14);p.line_to(radius,0);p.line_to(0,radius*1.14);p.line_to(-radius,0);p.close_path();p.fill()
            p.set_source_rgb(*INK);p.set_line_width(1.1)
            p.move_to(0,-radius*.60);p.line_to(0,radius*.60);p.stroke()
        else:
            col=RED if style==0 else (.40,.27,.57)
            # Fine ink edge and solid pigment disc, no specular dot or bright rim.
            p.set_source_rgb(*INK);p.arc(0,0,radius+.8,0,math.tau);p.fill()
            p.set_source_rgb(*col);p.arc(0,0,radius,0,math.tau);p.fill()
            p.set_source_rgba(*INK,.65);p.set_line_width(.9)
            p.arc(0,0,radius,0,math.tau);p.stroke()
        tile.flush()
        self.projectile_cache[key]=tile;self.projectile_tile_allocations+=1
        while len(self.projectile_cache)>96:self.projectile_cache.popitem(last=False)
        return tile

    def _projectiles(self,c,snap):
        # Immutable source variants are resolved once per frame/shape. The
        # optional native batch preserves every original OVER operation and
        # its order, but flushes/marks dirty and enters C only once.
        matrix=c.get_matrix();sx,sy=matrix.xx,matrix.yy
        ox,oy=matrix.x0,matrix.y0
        frame=self._frame_blitter
        variants={};commands=[]
        for shot,collection in ((True,snap.get('shots',())),(False,snap.get('bullets',()))):
            for item in collection:
                if item.get('hit'):continue
                style=0 if shot else self._style(item.get('style',0))
                radius=max(2,min(24,int(round(float(item.get('r',.006))*H))))
                key=(shot,style,radius)
                entry=variants.get(key)
                if entry is None:
                    tile=self._projectile_tile(style,radius,shot)
                    entry=(tile,tile.get_width()/2,tile.get_height()/2)
                    variants[key]=entry
                tile,half_width,half_height=entry
                left=round(ox+float(item['x'])*W*sx-half_width)
                top=round(oy+float(item['y'])*H*sy-half_height)
                commands.append((tile,left,top))
        self._tile_commands(c,commands)

    def _tile_commands(self,c,commands):
        if not commands:return
        frame=self._frame_blitter
        use_frame=frame is not None and frame.target==c.get_target()
        batch=getattr(frame,'paint_tiles',None) if use_frame else None
        if len(commands)>1 and batch is not None and batch(commands):return
        c.save();c.identity_matrix()
        for tile,left,top in commands:
            if use_frame and frame.paint_tile(tile,left,top):continue
            c.set_source_surface(tile,left,top)
            c.rectangle(left,top,tile.get_width(),tile.get_height());c.fill()
        c.restore()

    def _enemy_tile(self,kind,r,style=0):
        r=max(8,int(round(r/2))*2)
        key=(kind,r,self._style(style))
        if key in self.enemy_cache:
            self.enemy_cache.move_to_end(key);return self.enemy_cache[key]
        if kind in ('enemy','elite','boss'):
            name=('enemy-red' if self._style(style)%2==0 else 'enemy-blue') if kind=='enemy' else kind
            production={'enemy':.032*H,'elite':.052*H,'boss':.073*H}[kind]
            ratio=r/production
            bucket=0 if ratio<.87 else 1 if ratio<1.10 else 2 if ratio<1.32 else 3
            tile=self.gongbi.enemy(name,bucket)
            self.enemy_cache[key]=tile
            while len(self.enemy_cache)>32:self.enemy_cache.popitem(last=False)
            return tile
        sx,sy=self.size[0]/W,self.size[1]/H
        extent=r*(1.70 if kind=='enemy' else 1.55)+6
        ww,hh=2*math.ceil(extent*sx),2*math.ceil(extent*sy)
        tile=cairo.ImageSurface(cairo.FORMAT_ARGB32,ww,hh)
        p=cairo.Context(tile);p.translate(ww/2,hh/2);p.scale(sx,sy)
        p.set_antialias(cairo.ANTIALIAS_FAST)
        if kind=='power':
            p.set_source_rgba(*INK,.93);p.rectangle(-r*.8,-r*1.2,r*1.6,r*2.4);p.fill()
            p.set_source_rgb(*JADE);p.rectangle(-r*.60,-r,r*1.2,r*2);p.fill()
            p.set_source_rgb(*GOLD);p.set_line_width(2)
            p.move_to(0,-r*.7);p.line_to(0,r*.72);p.move_to(-r*.4,-r*.2);p.line_to(r*.4,-r*.2);p.stroke()
        else:
            p.set_source_rgba(*INK,.85);p.arc(0,0,r+3,0,math.tau);p.fill()
            p.set_source_rgb(*GOLD);p.arc(0,0,r,0,math.tau);p.fill()
            p.set_source_rgb(*INK);p.set_line_width(1.5);p.rectangle(-r*.23,-r*.23,r*.46,r*.46);p.stroke()
        tile.flush();self.enemy_cache[key]=tile
        while len(self.enemy_cache)>32:self.enemy_cache.popitem(last=False)
        return tile

    def _entity(self,c,entity,t):
        x,y=float(entity['x'])*W,float(entity['y'])*H
        r=max(.006,float(entity.get('r',.03)))*H
        kind=entity['kind']
        if kind not in ('enemy','elite','boss','power','orb'):
            # Legacy snapshots remain readable by old diagnostic tools.
            kind={'seal':'elite','rock':'enemy','gate':'orb'}.get(kind,'orb')
        # Engine jade/indigo enemies use the deep blue porcelain silhouette;
        # cinnabar uses red armour. Numeric/legacy styles retain two variants.
        style=entity.get('style',entity.get('id',0))
        if kind=='enemy' and isinstance(style,str):
            style=0 if style in ('cinnabar','red','scarlet') else 1
        tile=self._enemy_tile(kind,r,style)
        dx,dy=c.user_to_device(x,y)
        c.save();c.identity_matrix()
        left,top=round(dx-tile.get_width()/2),round(dy-tile.get_height()/2)
        c.rectangle(left,top,tile.get_width(),tile.get_height());c.clip()
        self._paint_tile(c,tile,left,top);c.restore()
        warning=float(entity.get('warning',0))
        if kind=='boss' and warning>0:
            c.set_source_rgba(*RED,.85);c.set_line_width(2.5)
            c.arc(x,y,r*(1.48+min(1.6,warning)*.35),0,math.tau);c.stroke()
        health=float(entity.get('health',1));maximum=max(.001,float(entity.get('max_health',1)))
        if kind=='elite' and health<maximum:
            c.set_source_rgba(*INK,.7);c.rectangle(x-r,y+r*1.55,r*2,4);c.fill()
            c.set_source_rgb(*RED);c.rectangle(x-r,y+r*1.55,r*2*clamp(health/maximum),4);c.fill()

    def _player(self,c,snap):
        p=snap.get('player') or {'x':.5,'y':.78,'angle':0}
        t=float(snap.get('time',0));x,y=p['x']*W,p['y']*H
        if self.last_time is None or t<self.last_time or (self.last_player and math.hypot(x-self.last_player[0],y-self.last_player[1])>260):self.trail.clear()
        if self.last_time!=t or not self.trail:self.trail.append((x,y,t))
        self.last_time,self.last_player=t,(x,y)
        samples=[q for q in self.trail if t-q[2]<.35]
        c.set_line_cap(cairo.LINE_CAP_ROUND);c.set_line_join(cairo.LINE_JOIN_ROUND)
        path=[(a,b+28) for a,b,_ in samples]+[(x,y+24),(x,y+88)]
        for width,col in ((3.0,(*JADE,.40)),):
            c.set_source_rgba(*col);c.set_line_width(width);c.move_to(*path[0])
            for a,b in path[1:]:c.line_to(a,b)
            c.stroke()
        angle=float(p.get('angle',-math.pi/2))
        bank=clamp(angle+math.pi/2 if abs(angle)>1 else angle,-.14,.14)
        if p.get('invulnerable'):
            c.set_source_rgba(*GOLD,.85);c.set_line_width(2.5);c.arc(x,y,25,0,math.tau);c.stroke()
        bank_name='left' if bank<-.045 else 'right' if bank>.045 else 'neutral'
        tile=self.gongbi.player(bank_name)
        dx,dy=c.user_to_device(x,y)
        self._tile_commands(c,[(tile,round(dx-tile.get_width()/2),round(dy-tile.get_height()/2))])
        # The visible light is the collision point, not the entire sword art.
        radius=float(p.get('r',.012))*H
        c.set_source_rgba(*INK,.84);c.arc(x,y,radius+3,0,math.tau);c.fill()
        c.set_source_rgb(*GOLD);c.set_line_width(1.6);c.arc(x,y,radius+1,0,math.tau);c.stroke()
        c.set_source_rgb(*PAPER);c.arc(x,y,3.3,0,math.tau);c.fill()

    def _effect_tile(self,frame,red=False):
        frame=min(EFFECT_STEPS-1,max(0,int(frame)))
        red='collect' if red=='collect' else bool(red)
        key=(frame,red)
        if key in self.effect_cache:return self.effect_cache[key]
        if red=='collect':
            # Pickup energy is a few small gold-ink flecks and a broken seal,
            # separate from the authored sword-hit and enemy-break artwork.
            # All sixteen compact device tiles are prepared at viewport load.
            sx,sy=self.size[0]/W,self.size[1]/H
            ww,hh=max(1,math.ceil(56*sx)),max(1,math.ceil(56*sy))
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,ww,hh)
            p=cairo.Context(surface);p.translate(ww/2,hh/2);p.scale(sx,sy)
            p.set_antialias(cairo.ANTIALIAS_FAST)
            u=frame/(EFFECT_STEPS-1);alpha=(1-u)**1.4
            radius=4+17*u
            p.set_line_cap(cairo.LINE_CAP_SQUARE)
            for k in range(6):
                a=k*math.tau/6+.23
                inner=radius*(.55+.08*(k%3));outer=radius*(.82+.10*(k%2))
                p.set_source_rgba(*((.55,.39,.17) if k%3==0 else GOLD),alpha)
                p.set_line_width(max(.65,2.4*(1-u)))
                p.move_to(math.cos(a)*inner,math.sin(a)*inner)
                p.line_to(math.cos(a)*outer,math.sin(a)*outer);p.stroke()
            if frame<10:
                # Small discontinuous diamond imprint; no luminous ring.
                mark=3.2+u*2
                p.set_source_rgba(*GOLD,alpha*.9);p.set_line_width(1.2)
                p.move_to(0,-mark);p.line_to(mark,0)
                p.move_to(0,mark);p.line_to(-mark,0);p.stroke()
            surface.flush();self.collect_tile_allocations+=1
        else:
            row=1 if red is True else 0
            surface=self.gongbi.effect(row,frame)
        self.effect_cache[key]=surface
        return surface

    def _observe_hits(self,snap):
        """Render-only health deltas; engine state and timing stay untouched."""
        t=float(snap.get('time',0.))
        if (snap.get('phase','ready')!='playing' or self._visual_hit_time is None
                or t<self._visual_hit_time):
            self._visual_hits.clear()
            self._visual_enemy_health={}
        current={}
        for entity in snap.get('entities',()):
            if entity.get('kind') not in ('enemy','elite','boss') or entity.get('hit'):
                continue
            identity=entity.get('id')
            if identity is None:continue
            health=float(entity.get('health',0))
            previous=self._visual_enemy_health.get(identity)
            if previous is not None and 0<health<previous:
                self._visual_hits[identity]=(float(entity['x']),float(entity['y']),t)
                self._visual_hits.move_to_end(identity)
                while len(self._visual_hits)>12:self._visual_hits.popitem(last=False)
            current[identity]=health
        self._visual_enemy_health=current
        self._visual_hit_time=t
        for identity,(_,_,born) in list(self._visual_hits.items()):
            if t-born>=.22:self._visual_hits.pop(identity,None)

    def _effects(self, c, snap):
        commands=[]
        t=float(snap.get('time',0.))
        for x,y,born in self._visual_hits.values():
            step=min(EFFECT_STEPS-1,int(clamp((t-born)/.22)*EFFECT_STEPS))
            tile=self.gongbi.effect(0,step)
            dx,dy=c.user_to_device(x*W,y*H)
            commands.append((tile,round(dx-tile.get_width()/2),round(dy-tile.get_height()/2)))
        for e in list(snap.get('effects',()))[-MAX_EFFECTS:]:
            age=float(e.get('age',0));ttl=max(.1,float(e.get('ttl',.8)))
            u=clamp(age/ttl);alpha=(1-u)**1.5
            if u>=1.:
                continue
            x=float(e.get('x',.5))*W;y=float(e.get('y',.5))*H
            kind=e.get('kind','spark');seed=int(e.get('seed',0))
            col=RED if kind in ('damage','hurt','hit','collision') else JADE
            if kind in ('collect','orb','power'):
                tile=self._effect_tile(min(EFFECT_STEPS-1,int(u*EFFECT_STEPS)),'collect')
            elif kind=='special':
                tile=self.gongbi.effect(2,min(EFFECT_STEPS-1,int(u*EFFECT_STEPS)))
            else:
                # Engine slash marks an enemy defeat; damage/hurt/spark are
                # compact hit beats. The kill and special rows stay distinct.
                row=1 if kind in ('slash','seal','boss','victory') else 0
                tile=self.gongbi.effect(row,min(EFFECT_STEPS-1,int(u*EFFECT_STEPS)))
            dx,dy=c.user_to_device(x,y)
            left,top=round(dx-tile.get_width()/2),round(dy-tile.get_height()/2)
            commands.append((tile,left,top))
            label={'gate':'通过','collect':'能量','orb':'能量','damage':'受到攻击','hurt':'受到攻击',
                   'hit':'命中','seal':'击破','boss':'首领','victory':'胜利','power':'火力升级'}.get(kind)
            if label and u<.8:
                # Preserve the exact tile/text painter order. Only consecutive
                # unlabeled explosions share a batch; labels never move atop
                # later explosions, and no decorative effect is discarded.
                self._tile_commands(c,commands);commands.clear()
                self._text(c,label,x,y-33-u*36,22,(*col,alpha),center=True,serif=True)
        self._tile_commands(c,commands)
        special=float(snap.get('special_time',0))
        if special>0:
            # The authored special effect is already attached to the engine
            # event. This restrained title does not add a second ink layer.
            self._panel(c,454,122,372,66,.94)
            self._text(c,'清屏',640,167,34,GOLD,center=True,serif=True)

    def _overlay(self, c, snap, ui):
        gamepad=ui.get('control_mode')=='gamepad'
        phase=snap.get('phase','ready')
        if phase in ('ready','won','lost'):
            self._panel(c,309,137,662,439,.94)
            self._text(c,'御 剑 山 河',640,233,62,PAPER,center=True,serif=True)
            c.set_source_rgba(*GOLD,.6);c.set_line_width(1);c.move_to(409,303);c.line_to(871,303);c.stroke()
            if phase=='ready':
                self._text(c,'准备开始',640,348,26,PAPER,center=True)
            else:
                self._text(c,'胜利' if phase=='won' else '游戏结束',640,343,26,GOLD,center=True,serif=True)
                self._text(c,f"本次得分  {int(snap.get('score',0))}",640,385,23,PAPER,center=True)
            a,b,d,e=START_RECT;x,y=a*W,b*H;ww,hh=(d-a)*W,(e-b)*H
            c.set_source_rgb(*RED);c.rectangle(x,y,ww,hh);c.fill()
            c.set_source_rgba(*GOLD,.95);c.set_line_width(1.5);c.rectangle(x+5,y+5,ww-10,hh-10);c.stroke()
            waiting=float(ui.get('resume_countdown',0))
            label=('准备入场 '+str(math.ceil(waiting))) if waiting>0 else ('开始游戏' if phase=='ready' else '重新开始')
            self._text(c,label,640,y+47,29,PAPER,center=True,serif=True)
            self._text(c,'按 A 开始 / 重开' if gamepad else '右手停留确认 / 点击开始',640,y+76,15,PAPER,center=True)
            progress=clamp(float(ui.get('dwell_progress',0)))
            if progress:
                c.set_source_rgb(*GOLD);c.rectangle(x,y+hh-5,ww*progress,5);c.fill()
        elif phase=='celebrating':
            self._panel(c,407,145,466,76,.92)
            self._text(c,'胜利',640,197,37,GOLD,center=True,serif=True)
        elif phase=='countdown' or ui.get('resume_countdown',0)>0:
            countdown=max(float(snap.get('countdown',0)),float(ui.get('resume_countdown',0)))
            self._panel(c,500,245,280,195,.90)
            self._text(c,str(max(1,math.ceil(countdown))),640,363,84,GOLD,center=True,serif=True)
            self._text(c,'准备开始',640,413,20,PAPER,center=True)
        elif (not snap.get('tracking',True) and not ui.get('resume_countdown')
              and float(ui.get('tracking_lost_seconds',1.))>=.60):
            self._panel(c,401,264,478,147,.92)
            self._text(c,'已暂停',640,316,33,GOLD,center=True,serif=True)
            self._text(c,'手柄重新连接后继续' if gamepad else '肩膀与右手入镜后继续',640,362,23,PAPER,center=True)
            self._text(c,'暂停期间不会扣血',640,390,16,(.7,.8,.75),center=True)
        notice=ui.get('notice') or ''
        if ui.get('offline') and str(notice).startswith('离线演示'):
            notice=''  # The persistent lower-right badge already marks preview input.
        if notice and phase not in ('ready','won','lost'):
            self._panel(c,378,146,524,47,.87)
            self._text(c,str(notice)[:31],640,178,20,PAPER,center=True)
        pointer=ui.get('pointer')
        if pointer and phase in ('ready','won','lost'):
            x,y=pointer[0]*W,pointer[1]*H
            c.set_source_rgb(*GOLD);c.set_line_width(3);c.arc(x,y,15,0,math.tau);c.stroke()
            c.set_source_rgb(*INK);c.arc(x,y,5,0,math.tau);c.fill()
        if ui.get('offline'):
            self._panel(c,1070,641,186,43,.91)
            self._text(c,'离线演示 · 非实测',1163,669,15,PAPER,center=True)

    def draw(self, c, width, height, snapshot, ui=None):
        ui=ui or {}
        width,height=max(1,int(width)),max(1,int(height))
        self._frame_blitter=self.blitter.prepare_frame(c)
        self._background(c,width,height,snapshot)
        c.save();c.set_antialias(cairo.ANTIALIAS_FAST);c.scale(width/W,height/H)
        t=float(snapshot.get('time',0.))
        self._observe_hits(snapshot)
        self._parallax(c,t,int(snapshot.get('scene',0)))
        for entity in snapshot.get('entities',()):
            if not entity.get('hit'):
                self._entity(c,entity,t)
        self._effects(c,snapshot)
        self._projectiles(c,snapshot)
        self._player(c,snapshot)
        self._hud(c,snapshot,ui)
        self._overlay(c,snapshot,ui)
        c.restore()
        self._frame_blitter=None
