import cairo
from cut_effects import CutEffects,COLORS
from score_style import ScoreStyle
s=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);c=cairo.Context(s)
c.set_source_rgb(.97,.95,.88);c.paint();letters=ScoreStyle()
letters.paint(c,24,8,'1280',hud=True)
for i,fruit in enumerate(COLORS):
    x=160+(i%3)*320;y=180+(i//3)*230
    f=CutEffects();f.trigger(dict(kind='normal',fruit=fruit,x=x,y=y,combo=4,cut_angle=-.45))
    f.advance(.09);f.paint(c);letters.paint(c,x-60,y-110,'+25 · 4连切')
s.write_to_png('contrast-preview.png')
