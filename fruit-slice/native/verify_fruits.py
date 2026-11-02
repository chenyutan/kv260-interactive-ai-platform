"""Render production sprite cache without opening a window or camera."""
from collections import OrderedDict
from types import SimpleNamespace
import cairo
from main import Window
from engine import ORDINARY_FRUITS
v=SimpleNamespace(rotations=OrderedDict())
Window._load(v);Window.rebuild(v,1280,720)
out=cairo.ImageSurface(cairo.FORMAT_RGB24,1280,720);c=cairo.Context(out)
c.set_source_rgb(.97,.95,.88);c.paint()
for row,name in enumerate(ORDINARY_FRUITS):
    for col,img in enumerate([v.sprites[name]]+v.cuts[name]):
        Window.sprite(v,c,img,400+col*200,60+row*118)
assert len(v.sprites)==9 and len(v.cuts)==8
out.write_to_png('fruit-varieties-preview.png')
print('All six ordinary fruits and paired cut surfaces rendered.')
