"""Deterministic, bounded vertical bullet-hell game for 御剑山河.

Positions are normalized; radii/speeds use screen height (x has a 16:9
aspect correction). The simulation runs at 120 Hz independently of drawing.
Tracking loss/pause discard elapsed time. No camera, GUI, dependencies or I/O.
"""
import math
import random
from types import MappingProxyType

ASPECT = 16 / 9
STEP = 1 / 120
PLAYER_R = .009
GRAZE_R = .047
MAX_DT = .25
MAX_BULLETS = 180
MAX_SHOTS = 72
MAX_ENEMIES = 20
MAX_ENTITIES = 32
MAX_EFFECTS = 40
ENEMY_KINDS = ('enemy', 'elite', 'boss')
AUDIO_EVENTS = ('shot', 'hit', 'kill', 'hurt', 'collect', 'special', 'boss', 'win', 'lose')
MAX_AUDIO_COUNT = (1 << 63) - 1


def _clamp(value, lo=0., hi=1.):
    return max(lo, min(hi, value))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sweep_distance2(ax, ay, bx, by):
    """Squared distance from origin to an aspect-corrected segment."""
    ax, bx = ax * ASPECT, bx * ASPECT
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = _clamp(-(ax * dx + ay * dy) / length) if length > 1e-18 else 0.
    x, y = ax + t * dx, ay + t * dy
    return x * x + y * y


def _enemy_grid(bounds, cell_x, cell_y):
    grid, global_mask = {}, 0
    for index, left, right, top, bottom in bounds:
        left, right = math.floor(left * cell_x), math.floor(right * cell_x)
        top, bottom = math.floor(top * cell_y), math.floor(bottom * cell_y)
        bit = 1 << index
        # Huge synthetic/fast sweeps stay bounded and are checked directly.
        if (right - left + 1) * (bottom - top + 1) > 24:
            global_mask |= bit
            continue
        for row in range(top, bottom + 1):
            base = row * 64
            for column in range(left, right + 1):
                key = base + column
                grid[key] = grid.get(key, 0) | bit
    return grid, global_mask


class Game:
    def __init__(self, seed=260, duration=75):
        self.seed = int(seed)
        self.duration = float(duration) if _finite(duration) and duration > 0 else 75.
        self._reset()

    def _reset(self):
        self.rng = random.Random(self.seed)
        self.phase, self.time, self.countdown = 'ready', 0., 0.
        self.score, self.combo, self.energy, self.lives = 0, 0, 0., 5
        self.power, self.kills, self.graze = 1, 0, 0
        self.player = dict(x=.5, y=.78, angle=-math.pi / 2, invulnerable=0., r=PLAYER_R)
        self.entities, self.effects, self.bullets, self.shots = [], [], [], []
        self.special_time = 0.
        self.tracking = self.paused = False
        self.message = '准备开始'
        self.boss_phase, self.boss_name = 0, ''
        self.wave_name = '第一波'
        self._next_id, self._spawn_index = 1, 0
        self._next_spawn, self._next_elite = .7, self.duration * .24
        self._boss_spawned = False
        self._boss_at = self.duration * .60
        self._debt = self._shot_cd = 0.
        self._special_down = self._pending_special = False
        self._target = (.5, .78)
        self._input_target = None
        # Fixed-size, read-only telemetry; no event queue, RNG or audio backend.
        # Keep it outside renderer snapshots and discard counts on every restart.
        self._audio_round = getattr(self, '_audio_round', 0) + 1
        self._audio_counts = dict.fromkeys(AUDIO_EVENTS, 0)
        self._audio_view = MappingProxyType(self._audio_counts)

    @property
    def audio_counts(self):
        """Accepted outcome counters; readers cannot change gameplay or consume them."""
        return self._audio_view

    @property
    def audio_round(self):
        return self._audio_round

    def _audio_event(self, name):
        self._audio_counts[name] = min(MAX_AUDIO_COUNT, self._audio_counts[name] + 1)

    def start(self, countdown=3):
        """Start/restart the same seed, including all score and spell state."""
        self._reset()
        self.countdown = max(0., float(countdown)) if _finite(countdown) else 3.
        self.phase = 'countdown' if self.countdown else 'playing'
        self.message = '游戏进行中'

    def pause(self, value=True):
        self.paused = bool(value)
        self._debt = 0.
        self._pending_special = False
        self._input_target = None
        # A held gesture across pause/resume must be released before recasting.

    def _id(self):
        value = self._next_id
        self._next_id += 1
        return value

    def _effect(self, kind, x, y, ttl=.65):
        self.effects.append(dict(kind=kind, x=_clamp(x), y=_clamp(y), age=0.,
                                 ttl=float(ttl), seed=self.rng.randrange(1 << 24)))
        if len(self.effects) > MAX_EFFECTS:
            del self.effects[:-MAX_EFFECTS]

    def _entity(self, kind, x, y, radius, **extra):
        if len(self.entities) >= MAX_ENTITIES:
            if not any(e['hit'] for e in self.entities):
                return None
            # A defeated foe or collected drop already freed a live slot.
            # Replace the list only at capacity: active collision grids keep
            # their separate enemy references/indices, and tuple traversals
            # remain valid without ever exceeding the entity budget.
            self.entities = [e for e in self.entities if not e['hit']]
        if kind in ENEMY_KINDS and sum(e['kind'] in ENEMY_KINDS and not e['hit']
                                      for e in self.entities) >= MAX_ENEMIES:
            return None
        health = float(extra.pop('health', {'enemy': 8., 'elite': 36., 'boss': 560.}.get(kind, 1.)))
        e = dict(id=self._id(), kind=kind, x=float(x), y=float(y), r=float(radius),
                 hit=False, health=health, max_health=float(extra.pop('max_health', health)),
                 style=extra.pop('style', kind), age=0., warning=0.,
                 _origin_x=float(x), _old_x=float(x), _old_y=float(y),
                 _fire_cd=1.25, _salvo=0, _phase=1 if kind == 'boss' else 0.)
        e.update(extra)
        self.entities.append(e)
        return e

    def _bullet(self, x, y, angle, speed, style='ink', radius=.010):
        if len(self.bullets) >= MAX_BULLETS:
            return None
        b = dict(id=self._id(), x=float(x), y=float(y), r=float(radius), style=style,
                 vx=math.cos(angle) * speed / ASPECT, vy=math.sin(angle) * speed,
                 grazed=False, hit=False)
        self.bullets.append(b)
        return b

    def _shot(self, x, y, angle=-math.pi / 2):
        if len(self.shots) >= MAX_SHOTS:
            return None
        s = dict(id=self._id(), x=x, y=y, r=.008, vx=math.cos(angle) * 1.30 / ASPECT,
                 vy=math.sin(angle) * 1.30, damage=2.8, hit=False)
        self.shots.append(s)
        return s

    def _aim(self, e):
        return math.atan2(self.player['y'] - e['y'], (self.player['x'] - e['x']) * ASPECT)

    def _fan(self, e, count, spread, speed, angle=None, style='ink'):
        center = self._aim(e) if angle is None else angle
        for i in range(count):
            a = center + (i / (count - 1) - .5) * spread if count > 1 else center
            self._bullet(e['x'], e['y'] + .022, a, speed, style)

    def _spawn(self):
        progress = self.time / self.duration
        i = self._spawn_index
        self._spawn_index += 1
        self.wave_name = '第一波' if progress < .18 else '第二波' if progress < .40 else '第三波'
        columns = (.24, .50, .76) if progress < .18 else (.18, .39, .61, .82)
        for j, x in enumerate(columns):
            self._entity('enemy', x, -.055 - abs(j - (len(columns) - 1) / 2) * .065,
                         .032, style=('jade', 'cinnabar', 'indigo')[i % 3],
                         vx=(.026 if i % 2 else -.026), vy=.082 + .015 * (progress > .3),
                         _phase=j * .9 + i, _fire_cd=1.2 + j * .23)
        self._next_spawn += self.duration * (.058 if progress < .18 else .052)

    def _spawn_boss(self):
        self._boss_spawned = True
        # Clear the approach without awarding free kills or points.
        self.entities = [e for e in self.entities if e['kind'] in ('orb', 'power') and not e['hit']][:MAX_ENTITIES - 1]
        self.bullets.clear()
        health = max(35., 560. * self.duration / 75.)
        boss = self._entity('boss', .5, .08, .073, health=health, max_health=health,
                            style='xuanmo', warning=1.6, _fire_cd=1.6, _phase=1)
        if boss is not None:
            self._audio_event('boss')
        self.boss_phase, self.boss_name = 1, '首领'
        self.wave_name = '首领战'
        self.message = '首领出现'

    def _damage(self, e, amount):
        if e['hit'] or e['kind'] not in ENEMY_KINDS:
            return
        before_health = e['health']
        e['health'] = max(0., e['health'] - amount)
        if e['health'] > 0:
            if e['health'] < before_health:
                self._audio_event('hit')
            return
        e['hit'] = True
        self._audio_event('kill')
        self.kills += 1
        self.combo += 1
        points = {'enemy': 100, 'elite': 350, 'boss': 2500}[e['kind']]
        self.score += points + min(10, self.combo // 3) * 5
        self.energy = _clamp(self.energy + (.14 if e['kind'] == 'elite' else .055))
        self._effect('slash', e['x'], e['y'], .48 if e['kind'] == 'enemy' else .8)
        if e['kind'] == 'boss':
            self.phase, self.message = 'won', '胜利'
            self._audio_event('win')
            self._effect('victory', e['x'], e['y'], 2.)
            self.bullets.clear()
        else:
            drop = 'power' if e['kind'] == 'elite' or self.kills % 3 == 0 else 'orb'
            self._entity(drop, e['x'], e['y'], .022 if drop == 'power' else .016,
                         style='gold' if drop == 'power' else 'jade', vy=.13)

    def _collect(self, e):
        if e['hit']:
            return
        e['hit'] = True
        self._audio_event('collect')
        self.score += 60 if e['kind'] == 'power' else 25
        self.energy = _clamp(self.energy + (.10 if e['kind'] == 'power' else .055))
        effect_kind = 'collect'
        if e['kind'] == 'power':
            previous_power = self.power
            self.power = min(4, self.power + 1)
            if self.power > previous_power:
                effect_kind = 'power'
        self._effect(effect_kind, e['x'], e['y'], .5)

    def _hurt(self):
        if self.player['invulnerable'] > 0 or self.special_time > 0:
            return
        self.lives -= 1
        self._audio_event('hurt')
        self.combo = 0
        self.power = max(1, self.power - 1)
        self.player['invulnerable'] = 1.5
        self._effect('hurt', self.player['x'], self.player['y'], .8)
        self.message = '受到攻击'
        if self.lives <= 0:
            self.phase, self.message = 'lost', '游戏结束'
            self._audio_event('lose')

    def _cast(self):
        if self.energy < 1. - 1e-8:
            return
        self.energy = 0.
        self._audio_event('special')
        self.special_time = 1.25
        self.bullets.clear()
        self._effect('special', self.player['x'], self.player['y'], 1.25)
        self.message = '清屏'
        for e in tuple(self.entities):
            if e['kind'] in ENEMY_KINDS and not e['hit']:
                self._damage(e, e['max_health'] * .22 if e['kind'] == 'boss' else 24.)

    def update(self, dt, control):
        """Advance active play, rejecting stale input and dropping catch-up debt."""
        if not isinstance(control, dict):
            control = {}
        valid = bool(control.get('valid')) and control.get('status') not in ('holding', 'lost', 'waiting')
        valid = valid and _finite(control.get('x')) and _finite(control.get('y'))
        self.tracking = bool(valid)
        raw_special = bool(control.get('special'))
        edge = valid and raw_special and not self._special_down
        self._special_down = raw_special
        if self.phase in ('ready', 'won', 'lost'):
            return
        if self.paused:
            self._debt = 0.
            self._pending_special = False
            self._input_target = None
            return
        if not _finite(dt) or dt <= 0:
            return
        if valid:
            self._target = (.07 + .86 * _clamp(float(control['x'])),
                            .22 + .62 * _clamp(float(control['y'])))
            self._input_target = self._target
        else:
            # Missing joints are neutral input, never a world-clock pause.
            # Do not continue towards the last hand location or replay a cast.
            self._target = (self.player['x'],self.player['y'])
            self._input_target = None
            self._pending_special = False
            self.message = '手部暂不可见 · 关卡继续'
        if self.phase == 'playing':
            self._pending_special |= edge
        self._debt += min(MAX_DT, float(dt))
        while self._debt + 1e-12 >= STEP:
            self._debt = max(0., self._debt - STEP)
            if self.phase == 'countdown':
                self.countdown = max(0., self.countdown - STEP)
                if self.countdown < 1e-9:
                    self.countdown = 0.
                    self.phase, self.message = 'playing', '游戏进行中'
                continue
            if self.phase != 'playing':
                self._debt = 0.
                break
            if self._pending_special:
                self._pending_special = False
                self._cast()
                if self.phase != 'playing':
                    self._debt = 0.
                    break
            self._step(STEP)

    def _move_enemies(self, dt):
        for e in tuple(self.entities):
            if e['hit']:
                continue
            e['_old_x'], e['_old_y'] = e['x'], e['y']
            e['age'] += dt
            e['warning'] = max(0., e['warning'] - dt)
            if e['kind'] == 'boss':
                e['x'] = .5 + .19 * math.sin(e['age'] * .54)
                e['y'] = min(.225, e['y'] + dt * .085)
                phase = 1 if e['health'] / e['max_health'] > .68 else 2 if e['health'] / e['max_health'] > .34 else 3
                if phase != e['_phase']:
                    e['_phase'] = self.boss_phase = phase
                    e['warning'], e['_fire_cd'] = 1., 1.
                    self.bullets.clear()
                    self.wave_name = ('首领 · 第一阶段', '首领 · 第二阶段', '首领 · 第三阶段')[phase - 1]
            elif e['kind'] in ('enemy', 'elite'):
                e['x'] = e['_origin_x'] + .036 * math.sin(e['age'] * 1.3 + e.get('_phase', 0.))
                e['y'] += e.get('vy', .08) * dt
                if e['kind'] == 'elite' and e['y'] > .29 and e['age'] < 9.:
                    e['y'] = .29
            else:
                dx, dy = self.player['x'] - e['x'], self.player['y'] - e['y']
                if (dx * ASPECT) ** 2 + dy ** 2 < .17 ** 2:
                    e['x'] += dx * min(1., dt * 5.)
                    e['y'] += dy * min(1., dt * 5.)
                else:
                    e['y'] += e.get('vy', .13) * dt
                continue
            e['_fire_cd'] -= dt
            if e['warning'] > 0 or not .035 < e['y'] < .72 or e['_fire_cd'] > 1e-9:
                continue
            e['_salvo'] += 1
            if e['kind'] == 'enemy':
                self._fan(e, 1 if self.time < self.duration * .25 else 3, .40, .205)
                e['_fire_cd'] += 2.7
            elif e['kind'] == 'elite':
                self._fan(e, 5, 1.3, .195, style='cinnabar')
                e['_fire_cd'] += 1.65
            elif e['_phase'] == 1:
                self._fan(e, 9, 2.15, .215, math.pi / 2 + .20 * math.sin(e['_salvo']), 'cinnabar')
                e['_fire_cd'] += 1.0
            elif e['_phase'] == 2:
                for i in range(16):
                    self._bullet(e['x'], e['y'], i * math.tau / 16 + e['_salvo'] * .22, .175, 'indigo')
                self._fan(e, 3, .36, .24, style='cinnabar')
                e['_fire_cd'] += 1.25
            else:
                self._fan(e, 11, 2.5, .195, math.pi / 2 + .27 * math.sin(e['_salvo'] * .7), 'indigo')
                self._fan(e, 3, .34, .26, style='cinnabar')
                e['_fire_cd'] += .90

    def _collisions(self, dt, old_x, old_y):
        # Sparse swept-AABB grid. Each cell holds an integer enemy bit mask,
        # avoiding sets/list allocations and repeated 72 x 20 Python pairs.
        # A target's old AND new position and radius cover fast crossings;
        # the narrow phase remains the exact relative segment calculation.
        enemies = [e for e in self.entities if e['kind'] in ENEMY_KINDS and not e['hit']]
        all_mask = (1 << len(enemies)) - 1
        grid = None
        global_mask = 0
        cell_x, cell_y = ASPECT / .12, 1. / .12
        use_grid = len(enemies) > 4 and len(self.shots) > 4
        bounds = []
        field_left = field_top = float('inf')
        field_right = field_bottom = -float('inf')
        for index, e in enumerate(enemies):
            if e['warning'] > 0:
                continue
            ex, ey, ox, oy, r = e['x'], e['y'], e['_old_x'], e['_old_y'], e['r']
            rx = r / ASPECT
            left, right = (ox if ox < ex else ex) - rx, (ox if ox > ex else ex) + rx
            top, bottom = (oy if oy < ey else ey) - r, (oy if oy > ey else ey) + r
            if left < field_left:
                field_left = left
            if right > field_right:
                field_right = right
            if top < field_top:
                field_top = top
            if bottom > field_bottom:
                field_bottom = bottom
            if use_grid:
                bounds.append((index, left, right, top, bottom))
        for s in self.shots:
            sx, sy = s['x'], s['y']
            nx, ny = sx + s['vx'] * dt, sy + s['vy'] * dt
            s['x'], s['y'] = nx, ny
            sr = s['r']
            rx = sr / ASPECT
            # Most friendly shots are still between player and the enemy band.
            # Reject that empty space before floor/hash work; construct the
            # sparse grid only when this step actually needs a local lookup.
            if ((sy > field_bottom + sr and ny > field_bottom + sr) or
                    (sy < field_top - sr and ny < field_top - sr) or
                    (sx > field_right + rx and nx > field_right + rx) or
                    (sx < field_left - rx and nx < field_left - rx)):
                continue
            candidates = all_mask
            if use_grid:
                if grid is None:
                    grid, global_mask = _enemy_grid(bounds, cell_x, cell_y)
                left = math.floor(((sx if sx < nx else nx) - rx) * cell_x)
                right = math.floor(((sx if sx > nx else nx) + rx) * cell_x)
                top = math.floor(((sy if sy < ny else ny) - sr) * cell_y)
                bottom = math.floor(((sy if sy > ny else ny) + sr) * cell_y)
                if (right - left + 1) * (bottom - top + 1) <= 24:
                    candidates = global_mask
                    for row in range(top, bottom + 1):
                        base = row * 64
                        for column in range(left, right + 1):
                            candidates |= grid.get(base + column, 0)
            while candidates:
                bit = candidates & -candidates
                candidates ^= bit
                e = enemies[bit.bit_length() - 1]
                if e['hit'] or e['warning'] > 0:
                    continue
                radius = s['r'] + e['r']
                ax, ay = sx - e['_old_x'], sy - e['_old_y']
                bx, by = nx - e['x'], ny - e['y']
                if (ay > radius and by > radius) or (ay < -radius and by < -radius):
                    continue
                rx = radius / ASPECT
                if (ax > rx and bx > rx) or (ax < -rx and bx < -rx):
                    continue
                if _sweep_distance2(ax, ay, bx, by) <= radius * radius:
                    s['hit'] = True
                    self._damage(e, s['damage'])
                    break
            if self.phase != 'playing':
                return
        px, py = self.player['x'], self.player['y']
        for b in self.bullets:
            bx, by = b['x'], b['y']
            b['x'] += b['vx'] * dt
            b['y'] += b['vy'] * dt
            r = b['r'] + GRAZE_R
            ax, ay, cx, cy = bx - old_x, by - old_y, b['x'] - px, b['y'] - py
            if (ay > r and cy > r) or (ay < -r and cy < -r):
                continue
            rx = r / ASPECT
            if (ax > rx and cx > rx) or (ax < -rx and cx < -rx):
                continue
            distance2 = _sweep_distance2(ax, ay, cx, cy)
            if distance2 <= (PLAYER_R + b['r']) ** 2:
                b['hit'] = True
                self._hurt()
            elif not b['grazed'] and self.player['invulnerable'] <= 0 and self.special_time <= 0 and distance2 <= r * r:
                b['grazed'] = True
                self.graze += 1
                self.score += 12
                self.energy = _clamp(self.energy + .035)
            if self.phase != 'playing':
                return
        for e in self.entities:
            if e['hit'] or e['warning'] > 0:
                continue
            pickup = e['kind'] in ('orb', 'power')
            radius = e['r'] + (.036 if pickup else PLAYER_R)
            ax, ay = e['_old_x'] - old_x, e['_old_y'] - old_y
            bx, by = e['x'] - px, e['y'] - py
            if (ay > radius and by > radius) or (ay < -radius and by < -radius):
                continue
            rx = radius / ASPECT
            if (ax > rx and bx > rx) or (ax < -rx and bx < -rx):
                continue
            distance2 = _sweep_distance2(ax, ay, bx, by)
            if pickup:
                if distance2 <= radius * radius:
                    self._collect(e)
            elif distance2 <= (e['r'] + PLAYER_R) ** 2:
                self._hurt()
            if self.phase != 'playing':
                return

    def _step(self, dt):
        self.time = min(self.duration, self.time + dt)
        old_x, old_y = self.player['x'], self.player['y']
        tx, ty = self._target
        self.player['x'] = _clamp(old_x + _clamp((tx - old_x) * 12, -1.25, 1.25) * dt, .07, .93)
        # The bottom HUD starts at logical y=638/720. Keep the hit core,
        # its colliding projectiles and the shield visible above that band.
        self.player['y'] = _clamp(old_y + _clamp((ty - old_y) * 12, -1.4, 1.4) * dt, .22, .84)
        self.player['angle'] = -math.pi / 2 + _clamp((self.player['x'] - old_x) / dt * .17, -.20, .20)
        self.player['invulnerable'] = max(0., self.player['invulnerable'] - dt)
        self.special_time = max(0., self.special_time - dt)
        for effect in self.effects:
            effect['age'] += dt
        self.effects = [e for e in self.effects if e['age'] < e['ttl']]
        if not self._boss_spawned and self.time >= self._boss_at:
            self._spawn_boss()
        if not self._boss_spawned:
            if self.time >= self._next_spawn and self.time < self._boss_at - min(2.5, self.duration * .04):
                self._spawn()
            if self.time >= self._next_elite:
                self._entity('elite', .32 if self._next_elite < self.duration * .3 else .68, -.065,
                             .052, health=36., style='gold', warning=.8, vy=.12)
                self._next_elite += self.duration * .20
            if self.time > self._boss_at - min(2., self.duration * .03):
                self.wave_name = '首领即将出现'
        self._move_enemies(dt)
        self._shot_cd -= dt
        if self._shot_cd <= 1e-9:
            before_shots = len(self.shots)
            for i in range(self.power):
                spread = (i - (self.power - 1) / 2)
                self._shot(self.player['x'] + spread * .011, self.player['y'] - .041,
                           -math.pi / 2 + spread * .052)
            # A four-sword spread is one accepted volley, not four SFX requests.
            # A full shot budget accepts no projectile and emits no shot event.
            if len(self.shots) > before_shots:
                self._audio_event('shot')
            self._shot_cd += .18
        self._collisions(dt, old_x, old_y)
        if self.phase != 'playing':
            return
        self.shots = [s for s in self.shots if not s['hit'] and -.08 < s['y'] < 1.12 and -.08 < s['x'] < 1.08]
        self.bullets = [b for b in self.bullets if not b['hit'] and -.08 < b['y'] < 1.12 and -.08 < b['x'] < 1.08]
        self.entities = [e for e in self.entities if not e['hit'] and e['y'] < 1.12]
        if self.time >= self.duration - 1e-8:
            self.time = self.duration
            self.phase, self.message = 'lost', '游戏结束'
            self._audio_event('lose')
        elif self.energy >= 1. - 1e-8:
            self.message = '清屏就绪'
        elif self.special_time <= 0:
            self.message = '游戏进行中'

    def snapshot(self):
        """Detached JSON-only scalar dictionaries; no recursive frame deepcopy."""
        entities = [{k: v for k, v in e.items() if not k.startswith('_')} for e in self.entities]
        return dict(phase=self.phase, time=self.time, duration=self.duration, countdown=self.countdown,
                    score=self.score, combo=self.combo, energy=self.energy, lives=self.lives,
                    player=self.player.copy(), entities=entities, effects=[e.copy() for e in self.effects],
                    bullets=[b.copy() for b in self.bullets], shots=[s.copy() for s in self.shots],
                    power=self.power, kills=self.kills, graze=self.graze, boss_phase=self.boss_phase,
                    boss_name=self.boss_name, wave_name=self.wave_name,
                    projectile_budget=dict(enemy=MAX_BULLETS, player=MAX_SHOTS, enemies=MAX_ENEMIES,
                                           entities=MAX_ENTITIES, effects=MAX_EFFECTS,
                                           active_enemy=len(self.bullets), active_player=len(self.shots)),
                    special_time=self.special_time, scene=min(2, int(self.time / self.duration * 3)),
                    progress=_clamp(self.time / self.duration), message=self.message,
                    tracking=self.tracking, paused=self.paused)
