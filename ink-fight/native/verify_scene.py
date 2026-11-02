"""Offline production rendering of every move, without GTK or camera capture."""
from pathlib import Path
from engine import Fight,MOVES
from scene import Scene
out=Path('v2-previews');out.mkdir(exist_ok=True);s=Scene()
for move in ('left','uppercut_left','palm','special'):
    g=Fight();g.state='playing';g.energy=100;g.perform(move)
    g.advance(MOVES[move]['windup']+.015)
    s.paint(g,1024,576,'国风格斗 · 离线招式预览').write_to_png(str(out/(move+'.png')))
g=Fight();s.paint(g,1024,576).write_to_png(str(out/'menu.png'))
g.state='playing';g.guard=True;s.paint(g,1024,576,'守势 · 举臂格挡').write_to_png(str(out/'guard.png'))
for size in [(1280,720),(1920,1080)]:s.paint(g,*size).write_to_png(str(out/('guard-'+str(size[0])+'.png')))
print('All move and menu renders completed without camera access.')
