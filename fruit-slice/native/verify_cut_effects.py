import cairo
from cut_effects import CutEffects,COLORS
s=cairo.ImageSurface(cairo.FORMAT_RGB24,960,540);c=cairo.Context(s)
c.set_source_rgb(.97,.95,.88);c.paint()
for i,fruit in enumerate(COLORS):
    f=CutEffects();f.trigger(dict(kind='normal',fruit=fruit,x=160+(i%3)*320,y=135+(i//3)*270,combo=4,cut_angle=-.45))
    f.advance(.09);f.paint(c)
s.write_to_png('cut-effects-preview.png')
