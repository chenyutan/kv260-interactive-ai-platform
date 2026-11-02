"""CPU-only art/render smoke. Does not load the shared object or open a display."""
import ast
import json
from pathlib import Path
import time
import unittest
try:
    import cairo
except ImportError:
    import cairocffi as cairo
from kart.fixtures import NAMES,snapshot
from kart.render import CairoWorld,Hud,W,H

def main():
    root=Path(__file__).resolve().parent;out=root.parents[2]/'qa'/'kart-local-evidence';out.mkdir(parents=True,exist_ok=True)
    syntax=[]
    for path in root.glob('*.py'):ast.parse(path.read_text(encoding='utf8'));syntax.append(path.name)
    world=CairoWorld();hud=Hud();images=[]
    for name in NAMES:
        s=snapshot(name);surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,W,H);cr=cairo.Context(surface)
        start=time.monotonic();world.draw(cr,s);hud.update(s,True);cr.set_source_surface(hud.surface,0,0);cr.paint()
        surface.write_to_png(str(out/(name+'-cairo.png')))
        images.append(dict(fixture=name,file=name+'-cairo.png',cpu_ms=round((time.monotonic()-start)*1000,3)))
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromName('kart.test_core'))
    receipt=dict(schema=1,syntax_files=syntax,cpu_tests=result.testsRun,passed=result.wasSuccessful(),
                 cairo_fixture_images=images,camera_used=False,gpu_used=False,physical_scanout_measured=False)
    (out/'local-cpu.json').write_text(json.dumps(receipt,indent=2),encoding='utf8');print(json.dumps(receipt))
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
