"""Offline PNG contact sheet; run with board /usr/bin/python3 (pycairo).

Usage: python3 verify_battle_fx.py --output battle-fx-preview.png
Does not import GTK, open any camera, or touch the running application.
"""
import argparse
from pathlib import Path
from types import SimpleNamespace
import cairo
import battle_fx


def render(output):
    w, h = 960, 1080
    image = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    c = cairo.Context(image)
    c.set_source_rgb(.96, .935, .865);c.paint()
    entries = [('寸拳 · 直线劲风', 'left'), ('挑云 · 上勾弧光', 'uppercut_left'),
               ('推山 · 双掌气浪', 'palm'), ('墨龙 · 金墨回旋', 'special'),
               ('朱墨命中', 'hit'), ('青玉格挡 / 金芒破招', 'parry')]
    for index, (title, move) in enumerate(entries):
        x, y = (index % 2) * 480, (index // 2) * 360
        c.save();c.rectangle(x, y, 480, 360);c.clip();c.translate(x, y)
        c.select_font_face('Noto Serif CJK SC', 0, 1);c.set_font_size(23)
        c.set_source_rgb(.09, .17, .15);c.move_to(22, 36);c.show_text(title)
        c.set_source_rgba(.12, .22, .18, .18);c.set_line_width(1)
        c.move_to(20, 320);c.line_to(460, 320);c.stroke()
        c.translate(0, 40);c.scale(.84, .84)
        g = SimpleNamespace(guard=False, attack=None, enemy_attack=None, effects=[])
        if move in ('hit', 'parry'):
            g.effects = [dict(side='enemy', kind='hit' if move == 'hit' else 'block',
                              age=.10, damage=20, perfect=move == 'parry', combo=3)]
            if move == 'parry':
                g.effects.append(dict(side='player', kind='block', age=.10))
        else:
            g.attack = dict(move=move, age=.21, windup=.14, duration=.5)
        battle_fx.paint(c, g, player_x=55, enemy_x=510, ground=370)
        c.restore()
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    image.write_to_png(str(output))
    return output


if __name__ == '__main__':
    p = argparse.ArgumentParser();p.add_argument('--output', default='battle-fx-preview.png')
    print(render(p.parse_args().output))
