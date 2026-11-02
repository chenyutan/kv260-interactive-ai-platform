"""Offline visual checks using the production effect renderer; no camera."""
from pathlib import Path
import cairo
from item_effects import ItemEffects

root=Path(__file__).resolve().parent
background=cairo.ImageSurface.create_from_png(str(root.parent/'art-v1/sprites/background.png'))
for kind in ('double','ice','bomb'):
    surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1280,720);c=cairo.Context(surface)
    c.save();c.scale(1280/background.get_width(),720/background.get_height());c.set_source_surface(background,0,0);c.paint();c.restore()
    fx=ItemEffects();fx.trigger(dict(kind=kind,x=640,y=380,delta=-30 if kind=='bomb' else 10));fx.advance(.25);fx.paint(c)
    surface.write_to_png(str(root/('effect-'+kind+'.png')))
print('Three production effect renders written; no camera accessed.')
