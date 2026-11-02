"""Cached ink-and-vermilion score lettering on transparent local surfaces."""
from collections import OrderedDict
import cairo

class ScoreStyle:
    def __init__(self):self.cache=OrderedDict()
    def paint(self,c,x,y,text,scale=1,hud=False,alpha=1):
        key=(text,round(scale,4),hud)
        if key not in self.cache:
            w,h=(320,76) if hud else (260,76)
            s=cairo.ImageSurface(cairo.FORMAT_ARGB32,max(1,round(w*scale)),max(1,round(h*scale)))
            p=cairo.Context(s);p.scale(scale,scale)
            if hud:
                p.set_source_rgb(.65,.13,.10);p.rectangle(5,13,36,44);p.fill()
                p.select_font_face('Noto Serif CJK SC',0,1);p.set_font_size(23)
                p.set_source_rgb(1,.94,.78);p.move_to(11,44);p.show_text('分')
            p.select_font_face('Noto Serif CJK SC',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD)
            size=40 if hud else 33;p.set_font_size(size)
            available=w-(64 if hud else 20)
            width=p.text_extents(text)[4]
            if width>available:p.set_font_size(size*available/width)
            p.move_to(52 if hud else 9,51)
            p.text_path(text)
            p.set_source_rgba(1,.97,.86,.95);p.set_line_width(4);p.set_line_join(cairo.LINE_JOIN_ROUND);p.stroke_preserve()
            p.set_source_rgb(*((.12,.18,.17) if hud else (.64,.12,.09)));p.fill()
            p.set_source_rgba(.54,.22,.10,.7);p.set_line_width(1.5)
            p.move_to(52 if hud else 10,61);p.line_to(min(w-12,64+len(text)*19),58);p.stroke()
            self.cache[key]=s
            if len(self.cache)>80:self.cache.popitem(last=False)
        else:self.cache.move_to_end(key)
        c.save();c.set_source_surface(self.cache[key],round(x),round(y));c.paint_with_alpha(max(0,min(1,alpha)));c.restore()
