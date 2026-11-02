"""Bounded, deterministic ink-combat feedback for an offscreen Cairo context.

This module is visual only. It neither extrapolates gestures nor changes combat
state. ``bounds`` returns logical (x, y, width, height) damage rectangles; callers
should invalidate the previous rectangles too when effects disappear.
"""
import math
from functools import lru_cache

try:
    import cairo
except ImportError:  # Portable geometry tests still use the vector path.
    cairo = None

INK = (.075, .12, .115)
VERMILION = (.65, .13, .10)
JADE = (.10, .40, .34)
GOLD = (.75, .47, .10)
PAPER = (.98, .91, .68)
MAX_EFFECTS = 6
MAX_LIFETIME = .55

# Reused irregular splinter directions, no per-frame randomness or allocations
# of particles. The silhouette is stable while its radius and opacity animate.
_SPLINTERS = tuple((i * 2.39996323, .65 + (i * 7 % 11) / 25,
                    2.3 + (i * 3 % 7) / 2) for i in range(11))


def _number(value, default=0.):
    try:
        n = float(value)
        return n if math.isfinite(n) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _move(value):
    value = str(value or 'left').lower()
    if value in ('kick_left','kick_right','kick'):
        return 'kick'
    if value in ('enemy_sweep','sweep'):
        return 'sweep'
    if 'uppercut' in value or value == 'upper':
        return 'uppercut'
    if value in ('palm', 'enemy_heavy', 'heavy'):
        return 'palm'
    if value in ('special', 'ultimate'):
        return 'special'
    if value == 'hook':
        return 'hook'
    return 'jab'


def _layers(game, player_x, enemy_x, ground):
    """Create immutable drawing descriptions; tolerate old and partial events."""
    player_x = _number(player_x, 450)
    enemy_x = _number(enemy_x, 800)
    ground = _number(ground, 560)
    direction = 1 if enemy_x > player_x else -1
    def actor(side,x,face):
        height=max(0.,_number(getattr(game,'player_y' if side=='player' else 'enemy_y',0.)))
        facing=_number(getattr(game,'facing' if side=='player' else 'enemy_facing',face),face)
        return dict(x=x,face=1 if facing>=0 else -1,height=height,
                    crouch=bool(getattr(game,'crouch' if side=='player' else 'enemy_crouch',False)))
    actors = {'player': actor('player',player_x,direction), 'enemy': actor('enemy',enemy_x,-direction)}
    result = []
    for side,attr in (('player','guard'),('enemy','enemy_guard')):
        if getattr(game,attr,False):
            a=actors[side];x,face=a['x'],a['face']
            y=ground-a['height']-(135 if a['crouch'] else 214)
            result.append(dict(kind='guard', x=x + face * 65, y=y,
                               face=face, age=0., alpha=.43,
                               rect=(x + face * 65 - 74, y-83, 148, 166)))
    for side, attribute in (('player', 'attack'), ('enemy', 'enemy_attack')):
        attack = getattr(game, attribute, None)
        if not isinstance(attack, dict):
            continue
        age = _number(attack.get('age'), -1)
        windup = max(.01, _number(attack.get('windup', attack.get('startup')),
                                   .14 if side == 'player' else .95))
        duration = max(windup + .03, _number(attack.get('duration'),
                                           .38 if side == 'player' else 1.18))
        if age < 0 or age >= duration:
            continue
        actor=actors[side];x=actor['x']
        facing=_number(attack.get('facing'),actor['face']);face=1 if facing>=0 else -1
        target = actors['enemy' if side == 'player' else 'player']['x']
        family = _move(attack.get('move', attack.get('name')))
        level=attack.get('level',{'jab':'high','uppercut':'high','sweep':'low'}.get(family,'mid'))
        y=ground-actor['height']-{'low':60,'mid':170,'high':250,'all':230}.get(level,214)
        budget=max(80.,_number(attack.get('reach'),470 if family=='sweep' else 445))
        reach=min(380.,max(100.,budget-65))
        front_gap=(target-x)*face
        if 110<front_gap<budget:reach=min(reach,max(100.,front_gap-55))
        if age < windup * .64:
            if side == 'enemy':
                result.append(dict(kind='charge', x=x + face * 66, y=y,
                                   face=face, progress=age / windup, move=family,
                                   rect=(x + face * 66 - 60, y-62, 120, 124)))
            continue
        progress = (age - windup * .64) / max(.01, duration - windup * .64)
        extension=min(1.,age/windup) if age<=windup else max(0.,1-(age-windup)/max(.01,duration-windup))
        # Contact events already contain the true pre-knockback point. End a
        # landed flourish there, rather than chasing the recoiling opponent.
        if attack.get('hit') and not attack.get('miss'):
            contact_events=getattr(game,'effects',())
            if not isinstance(contact_events,(tuple,list)):contact_events=()
            for event in reversed(contact_events[-MAX_EFFECTS:]):
                if isinstance(event,dict) and event.get('move')==attack.get('move') and event.get('side')!=side and 'x' in event:
                    reach=min(reach,max(90.,(_number(event['x'],x)-x)*face));break
        # Keep the flourish between the fighters and out of the HUD/footer.
        # Leave only a narrow contact overlap on their forward arms.
        ends=(x+face*30,x+face*(reach+52))
        left,right=min(ends)-18,max(ends)+18
        result.append(dict(kind='attack', move=family, x=x, target=target,
                           y=y, face=face, progress=progress, side=side,reach=reach,extension=extension,
                           rect=(left, y-190, right-left, 307)))
    effects = getattr(game, 'effects', ())
    if not isinstance(effects, (tuple, list)):
        effects = ()
    for effect in effects[-MAX_EFFECTS:]:
        if not isinstance(effect, dict):
            continue
        age = _number(effect.get('age'), -1)
        duration = min(MAX_LIFETIME, max(.01, _number(effect.get('lifetime'), .55)))
        kind = effect.get('kind', 'hit')
        if age < 0 or age >= duration or kind not in ('hit', 'block', 'parry'):
            continue
        side = effect.get('side', 'enemy')
        actor=actors.get(side, actors['enemy']);face=actor['face']
        # New events freeze the world-space contact before engine knockback.
        # Older recordings still render safely from their target's current pose.
        x=_number(effect.get('x'),actor['x']+face*59)
        y=_number(effect.get('y'),ground-actor['height']-(135 if actor['crouch'] else 214))
        face=1 if _number(effect.get('face'),face)>=0 else -1
        result.append(dict(kind='parry' if effect.get('perfect') else kind,
                           move=_move(effect.get('move')), x=x,
                           y=y, face=face, age=age, duration=duration,
                           damage=max(0, _number(effect.get('damage'), 8)),
                           combo=max(0, _number(effect.get('combo'), 0)),
                           rect=(x - 113, y-115, 226, 230)))
    return result


def bounds(game, player_x=450, enemy_x=800, ground=560):
    """Return logical rectangles covering this frame's active visual feedback."""
    return [layer['rect'] for layer in _layers(game, player_x, enemy_x, ground)]


def _color(c, rgb, alpha=1):
    c.set_source_rgba(*rgb, max(0., min(1., alpha)))


def _curve(c, points):
    c.new_path()
    c.move_to(*points[0])
    c.curve_to(*(points[1] + points[2] + points[3]))


def _slash(c, points, alpha, width, accent):
    """Tapered brush belly, colored edge and narrow dry-brush separations.

    Two cubic edges replace the old constant-width neon tube. Only the middle
    control points spread, so the leading tip remains a sharp ink nib. No
    blur, random particles, intermediate surfaces or growing trail history.
    """
    p0,p1,p2,p3=points
    dx,dy=p2[0]-p0[0],p2[1]-p0[1]
    norm=max(1.,math.hypot(dx,dy));nx,ny=-dy/norm,dx/norm
    half=width*.72
    def offset(p,w):return (p[0]+nx*w,p[1]+ny*w)
    c.new_path();c.move_to(*p0)
    c.curve_to(*(offset(p1,-half*.90)+offset(p2,-half*.50)+p3))
    c.curve_to(*(offset(p2,half*.95)+offset(p1,half*1.35)+p0))
    c.close_path();_color(c,INK,alpha*.94);c.fill()
    # Uneven color edge follows the ink rather than washing the silhouette out.
    _curve(c,(offset(p0,width*.05),offset(p1,-width*.12),
              offset(p2,-width*.07),p3))
    _color(c,accent,alpha*.94);c.set_line_width(max(1.,width*.23));c.stroke()
    for shift,weight in ((.16,.075),(.31,.035)):
        _curve(c,(offset(p0,width*shift),offset(p1,width*(shift+.03)),
                  offset(p2,width*shift*.5),p3))
        _color(c,PAPER,alpha*.68);c.set_line_width(max(.6,width*weight));c.stroke()


def _crescent(c,cx,cy,radius,start,sweep,thickness,rgb,alpha):
    """Pointed brush arc from four/six cubics, not a many-edged polygon."""
    count=max(1,math.ceil(abs(sweep)/1.6))
    def point(t,side):
        a=start+sweep*t;co,si=math.cos(a),math.sin(a)
        r=radius+side*thickness*math.sin(math.pi*t)
        dr=side*thickness*math.pi*math.cos(math.pi*t)
        return (cx+co*r,cy+si*r,co*dr-si*r*sweep,si*dr+co*r*sweep)
    c.new_path();p=point(0,.36);c.move_to(p[0],p[1])
    for reverse in (False,True):
        side=-.64 if reverse else .36
        for j in range(count):
            t0=1-j/count if reverse else j/count
            t1=1-(j+1)/count if reverse else (j+1)/count
            p0=point(t0,side);p1=point(t1,side);step=(t1-t0)/3
            c.curve_to(p0[0]+p0[2]*step,p0[1]+p0[3]*step,
                       p1[0]-p1[2]*step,p1[1]-p1[3]*step,p1[0],p1[1])
    c.close_path();_color(c,rgb,alpha);c.fill()


def _wake(c,head,y,envelope,accent,rise=0.):
    """Three short brush fragments separate an attack from the painted arena."""
    for i in range(3):
        _leaf(c,head-22-i*27,y+8+i*9,rise,19+i*5,2.8,
              INK if i!=1 else accent,envelope*(.75-i*.13))


def _dragon_vector(c,center,radius,spin,alpha):
    """Calligraphic coiling dragon: tapered body, muzzle, horns and whiskers."""
    _crescent(c,center,-7,radius,spin,4.45,30,INK,alpha*.94)
    _crescent(c,center,-7,radius-5,spin+.24,3.88,8,GOLD,alpha*.95)
    _crescent(c,center,-7,radius-13,spin+.72,2.85,3,PAPER,alpha*.72)
    # A second wake leaves the coil open, retaining the fighter silhouette.
    _crescent(c,center,-7,radius*.66,spin+1.25,3.20,13,JADE,alpha*.72)
    end=spin+4.45;hx=center+math.cos(end)*radius;hy=-7+math.sin(end)*radius
    c.save();c.translate(hx,hy);c.rotate(end+math.pi/2)
    c.new_path();c.move_to(-21,7);c.line_to(-14,-10)
    c.line_to(-3,-15);c.line_to(7,-11);c.line_to(21,-5)
    c.line_to(26,2);c.line_to(14,6);c.line_to(5,4)
    c.line_to(-4,12);c.close_path();_color(c,INK,alpha);c.fill()
    _slash(c,((-12,-7),(-19,-20),(-5,-21),(1,-31)),alpha,6,GOLD)
    _slash(c,((-3,-10),(-7,-19),(7,-19),(10,-27)),alpha*.9,4,GOLD)
    _slash(c,((17,2),(34,11),(27,25),(8,25)),alpha*.8,3,GOLD)
    _slash(c,((18,4),(30,15),(14,31),(-4,28)),alpha*.65,2,INK)
    _leaf(c,4,-6,0,5,2.5,GOLD,alpha)
    c.restore()
    for i in range(5):
        a=spin+.5+i*.72;r=radius+10+(i%2)*7
        _leaf(c,center+math.cos(a)*r,-7+math.sin(a)*r,a+1.55,
              14+(i%3)*4,3,INK if i%2 else GOLD,alpha*.70)


@lru_cache(maxsize=32)
def _dragon_stamp(scale,phase):
    # Bake rotation too: general affine resampling is expensive on the A53.
    # Fixed 32-frame cache, ~12.5 MiB at 1x / 28.1 MiB at 1.5x. Switching
    # display density evicts the old set rather than retaining both atlases.
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,int(320*scale),int(320*scale))
    c=cairo.Context(surface);c.scale(scale,scale);c.translate(160,167)
    c.set_antialias(cairo.ANTIALIAS_FAST)
    _dragon_vector(c,0,90,-.5+phase*1.45/31,1.)
    surface.flush()
    return surface


def prepare(scale=1.):
    """Bake the single ornate motif at scene setup, outside live drawing."""
    if cairo is not None:
        density=1. if scale<=1. else 1.5 if scale<=1.5 else 2.
        for phase in range(32):_dragon_stamp(density,phase)


def _dragon(c,center,radius,spin,alpha):
    if cairo is None or not isinstance(c,cairo.Context):
        return _dragon_vector(c,center,radius,spin,alpha)
    matrix=c.get_matrix();density=math.hypot(matrix.xx,matrix.yx)
    density=1. if density<=1. else 1.5 if density<=1.5 else 2.
    phase=max(0,min(31,round((spin+.5)*31/1.45)))
    stamp=_dragon_stamp(density,phase)
    c.save();c.translate(center,-7)
    c.scale(radius/90/density,radius/90/density)
    c.set_source_surface(stamp,-160*density,-160*density)
    c.get_source().set_filter(cairo.FILTER_BILINEAR)
    c.paint_with_alpha(max(0.,min(1.,alpha)));c.restore()


def _leaf(c, x, y, angle, length, width, rgb, alpha):
    c.save()
    c.translate(x, y)
    c.rotate(angle)
    c.move_to(-length * .25, 0)
    c.curve_to(length * .1, -width, length * .65, -width * .5, length, 0)
    c.curve_to(length * .38, width * .75, -length * .1, width * .35, -length * .25, 0)
    c.close_path()
    _color(c, rgb, alpha)
    c.fill()
    c.restore()


def _attack(c, layer):
    p = layer['progress']
    envelope = min(1., p * 7 + .12) * min(1., (1 - p) * 3)
    x, y, face = layer['x'], layer['y'], layer['face']
    reach = layer['reach']
    accent = GOLD if layer['side'] == 'player' else VERMILION
    c.translate(x, y)
    c.scale(face, 1)
    move = layer['move']
    if move == 'jab':
        head = 84 + (reach - 84) * min(1., p * 2.5)
        tail = max(55, head - 125 * (1 - p * .55))
        _slash(c, ((tail, 8), (tail + 40, -6), (head - 27, -7), (head, -2)),
               envelope, 22, accent)
        _slash(c, ((tail + 8, 24), (tail + 43, 13), (head - 22, 17), (head - 7, 13)),
               envelope * .62, 3, accent)
        _leaf(c, head - 7, -9, -.06, 16, 4, INK, envelope)
        _wake(c,head,18,envelope,accent,-.08)
    elif move == 'uppercut':
        # A rising hook, visibly different from the horizontal straight punch.
        sway = min(1., p * 1.8)
        end = -35 - 110 * sway
        _slash(c, ((66, 62), (reach * .96, 75), (reach + 4, -95), (reach * .78, end)),
               envelope, 28, accent)
        _slash(c, ((86, 63), (reach * .80, 69), (reach - 20, -58), (reach * .76 - 12, end + 7)),
               envelope * .73, 3.5, GOLD)
        for i in range(4):
            _leaf(c, reach * .7 - i * 12, end + 17 + i * 23, -1.3,
                  14 + i * 2, 3, INK if i % 2 else accent, envelope * .7)
    elif move == 'hook':
        _slash(c, ((57, -63), (reach * .6, -124), (reach + 28, -57), (reach - 8, 46)),
               envelope, 29, accent)
        _slash(c, ((63, -43), (reach * .7, -89), (reach + 5, -25), (reach - 19, 37)),
               envelope * .65, 4, accent)
    elif move == 'kick':
        # A low-to-mid crescent departs the lifted boot. No straight punch pose
        # or chest-height jab effect is substituted for a leg strike.
        head=100+(reach-100)*layer['extension']
        _slash(c,((75,44),(head*.47,42),(head-62,-18),(head,0)),envelope,30,accent)
        _slash(c,((87,58),(head*.49,54),(head-68,3),(head-14,17)),envelope*.60,2.4,accent)
        for i in range(3):
            _leaf(c,head-27-i*25,23+i*5,-.18,17+i*2,3,INK if i%2 else accent,envelope*.7)
        _slash(c,((60,73),(head*.40,65),(head-38,35),(head+7,9)),envelope*.43,8,accent)
    elif move == 'sweep':
        # Skimming brush follows the actual low hit level, not the old hook.
        head=100+(reach-100)*min(1.,p*2.1)
        _slash(c,((55,24),(head*.42,-35),(head*.82,-21),(head,17)),envelope,30,accent)
        _slash(c,((69,39),(head*.45,-8),(head*.80,-1),(head-12,31)),envelope*.62,3.3,accent)
        for i in range(4):
            _leaf(c,head-13-i*27,37+(i%2)*6,-.09,16+i*2,3.2,INK,envelope*.64)
    elif move == 'palm':
        center = 95 + (reach - 95) * min(1., p * 1.7)
        color = JADE if layer['side'] == 'player' else VERMILION
        # Twin palm waves, with an open center so fists remain visible.
        for i in range(3):
            radius = 28 + p * 43 + i * 17
            cx = center - i * 28
            _crescent(c,cx-radius*.7,0,radius,-1.2,2.4,22-i*3,
                      INK,envelope*(.90-i*.15))
            _crescent(c,cx-radius*.7,0,radius-4,-1.1,2.15,7,
                      color,envelope*(.96-i*.18))
            _leaf(c, cx - 46, -radius + 10, -.1, 23, 4, color, envelope * .6)
            _leaf(c, cx - 51, radius - 10, .1, 23, 4, INK, envelope * .5)
    elif move == 'special':
        center = max(110, reach * .64)
        radius = min(91, max(62, reach * .32))
        spin = -.5 + p * 1.45
        _slash(c,((52,62),(reach*.30,3),(reach*.73,58),(reach+10,-10)),
               envelope*.8,28,GOLD)
        _dragon(c,center,radius,spin,envelope)
        _slash(c,((62,79),(reach*.31,43),(reach*.76,86),(reach+12,9)),
               envelope*.55,7,JADE)


def _shield(c, layer, alpha, perfect=False, compact=False):
    c.translate(layer['x'], layer['y'])
    c.scale(layer['face'], 1)
    radius = 57 if layer['kind'] == 'guard' else 57 + layer.get('age', 0) * 30
    color = GOLD if perfect else JADE
    # Open half-shield. The interior is not filled over the player's body.
    _crescent(c,-19,0,radius,-1.2,2.4,16,INK,alpha*.91)
    _crescent(c,-19,0,radius-4,-1.13,2.26,6,color,alpha*.96)
    if not compact:_crescent(c,-19,0,radius-11,-.88,1.76,2,PAPER,alpha*.65)
    for angle in (-.87, 0, .87):
        x = -19 + math.cos(angle) * (radius + 8)
        y = math.sin(angle) * (radius + 8)
        _leaf(c, x, y, angle, 12 if perfect else 8, 3, color, alpha)
    if perfect:
        for i in range(0,7,2 if compact else 1):
            a = -1.35 + i * .45
            d = 33 + layer.get('age', 0) * 82
            _leaf(c, math.cos(a) * d, math.sin(a) * d, a,
                  17 if i % 2 else 25, 4, GOLD if i % 2 else INK, alpha)


def _impact(c, layer, compact=False):
    t = layer['age'] / layer['duration']
    alpha = min(1., (1 - t) * 2)
    if layer['kind'] in ('block', 'parry'):
        _shield(c, layer, alpha, layer['kind'] == 'parry',compact)
        return
    c.translate(layer['x'], layer['y'])
    c.scale(layer['face'], 1)
    strength = min(1.35, .85 + math.sqrt(layer['damage']) * .055)
    # A short, asymmetric ink seal punctuates contact. Never a screen flash.
    if t < .60:
        burst=(1-t/.60)*alpha
        # Single asymmetric seal, not dozens of extra particles per impact.
        c.new_path();c.move_to(-31*strength,25*strength)
        for x,y in ((-7,1),(-24,-21),(-2,-8),(10,-32),(8,-3),
                    (35,19),(4,7),(-4,26),(-5,8)):
            c.line_to(x*strength,y*strength)
        c.close_path();_color(c,INK,burst*.88);c.fill()
        _leaf(c,-16,15,-.70,42*strength,4.5,
              GOLD if layer['move']=='special' else VERMILION,burst)
    # Sparse, crisp brush shards carry the hit without covering the character.
    for i, (angle, spread, width) in enumerate(_SPLINTERS):
        if compact and i%2:continue
        radius = (15 + 52 * t) * spread * strength
        length = (23 - t * 16) * spread * strength
        color = GOLD if layer['move'] == 'special' and i % 3 == 0 else VERMILION if i % 3 else INK
        _leaf(c, math.cos(angle) * radius, math.sin(angle) * radius,
              angle, length, width * strength, color, alpha)
    if t < .42 and not compact:
        size = (1 - t / .42) * 12 + 3
        _slash(c, ((-size, size * .5), (-4, 0), (4, 0), (size, -size * .8)),
               alpha, 5, VERMILION)
    if layer['combo'] >= 3 and not compact:
        _color(c, GOLD, alpha * .75);c.set_line_width(1.8)
        c.new_path();c.arc(0, 0, 35 + t * 51, -.5, 1.1);c.stroke()


def paint(c, game, player_x=450, enemy_x=800, ground=560):
    """Draw locally clipped effects into a Cairo image-surface context.

    Uses no fonts, file I/O, full-screen fills, or game mutation. The dragon
    atlas is bounded to 32 frames and prepared at scene setup.
    Attack geometry follows world position, facing and airborne hit level;
    impacts use the engine's frozen contact, including after knockback.
    """
    layers=_layers(game, player_x, enemy_x, ground)
    # Dense overlaps retain every contact and shield, with fewer tiny chips.
    # This bounds paint cost and keeps simultaneous effects legible.
    compact=sum(x['kind'] in ('hit','block','parry') for x in layers)>3
    for layer in layers:
        c.save()
        if cairo is not None:c.set_antialias(cairo.ANTIALIAS_FAST)
        c.new_path();c.rectangle(*layer['rect']);c.clip()
        c.set_line_cap(1);c.set_line_join(1)
        kind = layer['kind']
        if kind == 'attack':
            _attack(c, layer)
        elif kind == 'guard':
            _shield(c, layer, layer['alpha'])
        elif kind == 'charge':
            p = layer['progress'];r = 19 + 34 * p
            _color(c, VERMILION, .35 + p * .48);c.set_line_width(2 + p * 2)
            c.new_path();c.arc(layer['x'], layer['y'], r, -.85, 1.35);c.stroke()
            for i in range(3):
                a = -.7 + i * .7
                _leaf(c, layer['x'] + math.cos(a) * r,
                      layer['y'] + math.sin(a) * r, a, 9, 2.7, VERMILION, .6)
        else:
            _impact(c, layer,compact)
        c.restore()
