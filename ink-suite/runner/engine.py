"""Bounded, rendering-independent three-lane runner simulation.

Coordinates: lane 0 is screen left; z is metres in front of the runner.
Only the engine owns collisions and clocks. Draw with ``player_x`` (0..2)
and ``jump_height`` (0..1), rather than altering game state from rendering.
"""
from collections import deque
from dataclasses import dataclass
import math
import random


@dataclass
class Entity:
    id: int
    kind: str
    lane: int
    z: float
    resolved: bool = False
    collected: bool = False
    wave: int = 0
    contacted: bool = False
    y: float = 0.


class RunnerGame:
    MAX_ENTITIES = 160
    MAX_EVENTS = 128
    MAX_STEP = 1. / 120.
    MAX_FRAME_DT = .25
    LANE_SECONDS = .15
    JUMP_SECONDS = .85
    SLIDE_SECONDS = .75
    START_SPEED = 11.
    MAX_SPEED = 21.
    SPAWN_DISTANCE = 64.
    CART_LENGTH = 7.2
    ROOF_HEIGHT = 2.49
    RAMP_LENGTH = 6.
    JUMP_METRES = 1.35
    BOOTS_JUMP_METRES = 2.1
    BOOTS_JUMP_SECONDS = .95
    COYOTE_SECONDS = .09
    JUMP_BUFFER_SECONDS = .12
    JETPACK_SECONDS = 6.
    JETPACK_HEIGHT = 4.8
    BOOTS_SECONDS = 9.
    COINBOOST_SECONDS = 12.
    TRAIN_GAP = 3.
    WAVE_SPACING = 65.
    POWERUPS = ('magnet', 'springboots', 'shield', 'coinboost', 'jetpack')
    ROUTES = ('并排火车', '车顶连跳', '错列火车', '车顶障碍', '地面跑道', '双线金币')
    GOALS = (("收集金币", 20), ("登上车顶", 16), ("躲避障碍", 4))
    OBSTACLES = ('barrier', 'arch', 'cart')

    def __init__(self, seed=1):
        self.seed = seed
        self.reset()

    def reset(self):
        self.rng = random.Random(self.seed)
        self.state = 'title'
        self.player_lane = 1
        self.player_x = 1.
        self._lane_start = 1.
        self._lane_elapsed = self.LANE_SECONDS
        self.jump_remaining = self.slide_remaining = 0.
        self.distance = self.elapsed = 0.
        self.speed = self.START_SPEED
        self.score = self.coins = 0
        self.lives = 3
        self.shield_time = self.magnet_time = self.invulnerable_time = 0.
        self.jetpack_time = self.boots_time = self.coinboost_time = 0.
        self.entities = []
        self.events = deque(maxlen=self.MAX_EVENTS)
        self._next_id = self._wave_id = 0
        self._next_wave_distance = 18.
        self._score_bonus = 0
        self.dropped_seconds = 0.
        self.counts = {}
        self.player_y = self._jump_origin = self._fall_velocity = 0.
        self._grounded = True
        self._last_grounded = 0.
        self._jump_buffer = 0.
        self._jump_duration = self.JUMP_SECONDS
        self._jump_metres = self.JUMP_METRES
        self._flight_origin = self._flight_elapsed = 0.
        self.route_name = self.ROUTES[0]
        self._wave_spacing = self.WAVE_SPACING
        self.combo = self.best_combo = self.goals_completed = self.goal_index = 0
        self.goal_progress = self.roof_distance = self.combo_time = 0.


    def _event(self, kind, **kwargs):
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.events.append(dict(type=kind, kind=kind, time=self.elapsed, **kwargs))

    def drain_events(self):
        result = list(self.events)
        self.events.clear()
        return result

    def start(self):
        if self.state == 'title':
            self.state = 'running'
            self._event('start')
            return True
        if self.state == 'over':
            return self.restart()
        return False

    def restart(self):
        self.reset()
        self.state = 'running'
        self._event('start')
        return True

    def pause(self):
        if self.state == 'running':
            self.state = 'paused'
            self._event('pause')

    def resume(self):
        if self.state == 'paused':
            self.state = 'running'
            self._event('resume')

    @property
    def jump_phase(self):
        return (1. - self.jump_remaining / self._jump_duration
                if self.jump_remaining > 0 else -1.)

    @property
    def jump_height(self):
        return math.sin(math.pi * self.jump_phase) if self.jump_remaining > 0 else 0.

    @property
    def slide_phase(self):
        return (1. - self.slide_remaining / self.SLIDE_SECONDS
                if self.slide_remaining > 0 else -1.)

    @property
    def jumping(self):
        return self.jump_remaining > 0

    @property
    def sliding(self):
        return self.slide_remaining > 0

    def move(self, direction):
        if self.state != 'running' or isinstance(direction, bool) or direction not in (-1, 1):
            return False
        lane = max(0, min(2, self.player_lane + direction))
        if lane == self.player_lane:
            return False
        self.player_lane = lane
        self._lane_start = self.player_x
        self._lane_elapsed = 0.
        self._event('lane', lane=lane, direction=direction)
        return True

    def set_lane(self, lane):
        """Follow a fresh body-position target, without repeating lane edges.

        Keyboard/gamepad callers keep using relative ``move``. A position
        target may cross two lanes in one observation; interpolate from the
        current rendered position rather than snapping or restarting a held
        target's tween every frame.
        """
        if self.state != 'running' or type(lane) is not int or not 0 <= lane <= 2:
            return False
        if lane == self.player_lane:
            return False
        direction = 1 if lane > self.player_lane else -1
        self.player_lane = lane
        self._lane_start = self.player_x
        self._lane_elapsed = 0.
        self._event('lane', lane=lane, direction=direction)
        return True

    def jump(self):
        if self.state != 'running' or self.jetpack_time > 0:
            return False
        if self.jumping or not (self._grounded or self.elapsed-self._last_grounded <= self.COYOTE_SECONDS):
            # Fresh input just before touchdown is retained briefly. It is
            # never an instruction to auto-jump through the entire airborne arc.
            self._jump_buffer = self.JUMP_BUFFER_SECONDS
            return False
        # A fresh take-off supersedes the preparatory crouch/slide. Cancel
        # only after ground/coyote eligibility, preserving airborne buffers.
        self.slide_remaining = 0.
        return self._start_jump()

    def _start_jump(self):
        self._jump_origin = self.player_y
        self._grounded = False
        self._jump_duration = self.BOOTS_JUMP_SECONDS if self.boots_time > 0 else self.JUMP_SECONDS
        self._jump_metres = self.BOOTS_JUMP_METRES if self.boots_time > 0 else self.JUMP_METRES
        self.jump_remaining = self._jump_duration
        self._jump_buffer = 0.
        self._event('jump')
        return True

    def slide(self):
        if self.state != 'running' or self.jumping or self.sliding or self.jetpack_time > 0:
            return False
        self.slide_remaining = self.SLIDE_SECONDS
        self._event('slide')
        return True

    def spawn(self, kind, lane, z, *, wave=0, y=0.):
        """Also useful for deterministic diagnostics; never grows unbounded."""
        if kind not in self.OBSTACLES + ('coin', 'ramp') + self.POWERUPS:
            raise ValueError('unknown entity kind')
        if lane not in (0, 1, 2) or not math.isfinite(z) or not math.isfinite(y) or y < 0:
            raise ValueError('invalid entity position')
        if len(self.entities) >= self.MAX_ENTITIES:
            return None
        self._next_id += 1
        item = Entity(self._next_id, kind, lane, float(z), wave=wave, y=float(y))
        self.entities.append(item)
        return item

    @property
    def multiplier(self):
        return min(4, 1 + self.combo // 10)

    def _goal(self, index, amount):
        if self.goal_index % 3 != index:
            return
        self.goal_progress += amount
        if self.goal_progress >= self.GOALS[index][1]:
            self._score_bonus += 350
            self.goals_completed += 1
            self._event('goal', goal=self.GOALS[index][0], bonus=350)
            self.goal_index += 1
            self.goal_progress = 0.

    def _spawn_wave(self):
        self._wave_id += 1
        wave = self._wave_id
        lane = (wave + self.seed) % 3
        # Roof transfers always use neighbouring tracks: one deliberate lane
        # input during a jump, never a hidden two-lane dash across the safe lane.
        other = 1 if lane != 1 else (0 if (wave+self.seed) % 2 else 2)
        clear = 3-lane-other
        base = self.SPAWN_DISTANCE
        mode = (wave - 1) % len(self.ROUTES)
        self.route_name = self.ROUTES[mode]
        end = 55.
        trails = []

        def coins(track, start, stop, *, y=0., step=3.):
            count = max(0, int((stop-start)/step)+1)
            for i in range(count):
                trails.append((track, base+start+i*step, y))

        def ramp(track, start=0.):
            self.spawn('ramp', track, base+start, wave=wave)
            for dz in (1.5, 3., 4.5):
                trails.append((track, base+start+dz, self.ROOF_HEIGHT*dz/self.RAMP_LENGTH))

        def train(track, start, cars=2, trail=True):
            for i in range(cars):
                self.spawn('cart', track, base+start+i*self.CART_LENGTH, wave=wave)
            if trail:
                coins(track, start+1.2, start+cars*self.CART_LENGTH-1., y=self.ROOF_HEIGHT)

        def gap_trail(track, edge, landing):
            # Airborne coins describe the actual 3 m empty space. The low
            # departure/arrival coins remain collectable by an ordinary jump.
            for dz, rise in ((-.9, .25), (.8, .9), (2.2, .85), (4.1, .2)):
                trails.append((track if dz<1.5 else landing,
                               base+edge+dz, self.ROOF_HEIGHT+rise))

        if mode == 0:
            # Two parallel trains give a broad first opportunity to jump
            # sideways, then jump back. Each platform is long enough to land.
            ramp(lane)
            ramp(other)
            train(lane, 6., 6, False)
            train(other, 6., 6, False)
            for i in range(14):
                trails.append((lane if i//3 % 2 == 0 else other,
                               base+8.+i*2.8, self.ROOF_HEIGHT+.35))
        elif mode == 1:
            ramp(lane)
            for start in (6., 30.6, 55.2):
                train(lane, start, 3)
            for edge in (27.6, 52.2):gap_trail(lane, edge, lane)
            end = 76.8
            self.spawn('barrier', other, base+14., wave=wave)
            self.spawn('arch', other, base+37., wave=wave)
        elif mode == 2:
            # Three-car landing zones allow even a max-speed boot jump to
            # land and reset before the following 3 m gap (24.6 m cadence).
            ramp(lane)
            for track, start in ((lane, 6.), (other, 30.6),
                                 (lane, 55.2), (other, 79.8)):
                train(track, start, 3)
            for track, edge, landing in ((lane,27.6,other),(other,52.2,lane),(lane,76.8,other)):
                gap_trail(track,edge,landing)
            end = 101.4
        elif mode == 3:
            ramp(lane)
            train(lane, 6., 6)
            self.spawn('barrier', lane, base+15., wave=wave, y=self.ROOF_HEIGHT)
            self.spawn('arch', lane, base+37., wave=wave, y=self.ROOF_HEIGHT)
            train(other, 19., 3)
        elif mode == 4:
            # The ground rhythm is optional: the complete third lane stays
            # empty, while left/right choices alternate jump and slide beats.
            for i, dz in enumerate((0., 12., 24., 36., 48.)):
                track = lane if i % 2 == 0 else other
                self.spawn('barrier' if i % 2 == 0 else 'arch', track, base+dz, wave=wave)
                coins(track, dz-0.5 if dz else 1., dz+4., y=.85 if i % 2 == 0 else 0.)
            train(other, 4., 2)
        else:
            ramp(lane)
            ramp(other, 8.)
            train(lane, 6., 4)
            train(other, 14., 4)
            self.spawn('shield' if wave % 12 else 'coinboost', lane,
                       base+25., wave=wave, y=self.ROOF_HEIGHT)

        # A visible, uninterrupted ground choice accompanies every roof route.
        # Obstacles are generated first so a full optional coin budget cannot
        # truncate a required landing platform.
        coins(clear, 0., end, step=4.)
        power = self.POWERUPS[(wave-1) % len(self.POWERUPS)]
        self.spawn(power, clear, base+15., wave=wave)
        for track, z, y in trails:
            self.spawn('coin', track, z, wave=wave, y=y)
        self._wave_spacing = max(self.WAVE_SPACING, end+11.4)
        self._event('wave', wave=wave, clear_lane=clear, route_lane=lane,
                    route_name=self.route_name, route_span=end,
                    next_spacing=self._wave_spacing)

    def _surface(self, travel):
        support, roof = 0., False
        for item in self.entities:
            if item.contacted or abs(self.player_x-item.lane) >= .43:
                continue
            height = None
            if item.kind == 'ramp' and item.z <= 0. and item.z+self.RAMP_LENGTH >= -.03:
                height = item.y + self.ROOF_HEIGHT * min(1., -item.z/self.RAMP_LENGTH)
            elif item.kind == 'cart' and item.z <= .03 and item.z+self.CART_LENGTH >= -.03:
                height = item.y + self.ROOF_HEIGHT
            # No sideways teleport onto a higher roof/ramp. Ascending ramps
            # may lift feet only by their physical per-substep rise.
            step_up = .18 if item.kind == 'ramp' else max(.04, travel*.44)
            if height is not None and height <= self.player_y + step_up:
                if height >= support:
                    support, roof = height, item.kind == 'cart'
        return support, roof

    def _vertical(self, dt, travel):
        support, on_roof = self._surface(travel)
        previous = self.player_y
        if self.jetpack_time > 0:
            self._flight_elapsed += dt
            lift = min(1., self._flight_elapsed/.4)
            lift = lift*lift*(3.-2.*lift)
            self.player_y = self._flight_origin+(self.JETPACK_HEIGHT-self._flight_origin)*lift
            self._grounded = False
            self._fall_velocity = 0.
        elif self.jumping:
            self.player_y = self._jump_origin + self._jump_metres * self.jump_height
            self._fall_velocity = (self.player_y-previous)/dt
            if self.jump_phase > .5 and self.player_y <= support:
                self.player_y = support
                self.jump_remaining = 0.
                self._grounded = True
                self._fall_velocity = 0.
        elif self._grounded and support >= previous-.08:
            self.player_y = support
        else:
            self._grounded = False
            self._fall_velocity -= 18.*dt
            self.player_y += self._fall_velocity*dt
            if self.player_y <= support:
                self.player_y = support
                self._fall_velocity = 0.
                self._grounded = True
        if on_roof and self._grounded:
            self.roof_distance += travel
            self._goal(1, travel)
        if self._grounded:
            self._last_grounded = self.elapsed
            if self._jump_buffer > 0 and not self.sliding:
                self._start_jump()

    def _collect(self, item):
        item.resolved = item.collected = True
        value = 1
        if item.kind == 'coin':
            value = 2 if self.coinboost_time > 0 else 1
            self.coins += value
            self.combo_time = 3.
            self.combo += 1
            self.best_combo = max(self.best_combo, self.combo)
            self._score_bonus += 20 * self.multiplier * value
            self._goal(0, 1)
        elif item.kind == 'shield':
            self.shield_time = 10.
        elif item.kind == 'magnet':
            self.magnet_time = 9.
        elif item.kind == 'springboots':
            self.boots_time = self.BOOTS_SECONDS
        elif item.kind == 'coinboost':
            self.coinboost_time = self.COINBOOST_SECONDS
        elif item.kind == 'jetpack':
            refreshing = self.jetpack_time > 0
            self.jetpack_time = self.JETPACK_SECONDS
            if not refreshing:
                self._flight_origin = self.player_y
                self._flight_elapsed = 0.
            self.jump_remaining = self.slide_remaining = self._jump_buffer = 0.
            self._grounded = False
            # Flight has its own reachable reward trail, rather than leaving
            # the player looking at ground coins far below their feet.
            for i in range(1, 17):
                self.spawn('coin', self.player_lane, self.speed*.35*i,
                           y=self.JETPACK_HEIGHT)
        self._event('collect', item_kind=item.kind, lane=item.lane, id=item.id,
                    x=float(item.lane), z=item.z, coin_value=value if item.kind == 'coin' else 0)

    def _hit(self, item):
        item.resolved = True
        item.contacted = True
        if self.invulnerable_time > 0:
            return
        if self.shield_time > 0:
            self.shield_time = 0.
            self.invulnerable_time = .65
            self._event('shield_break', lane=item.lane, id=item.id,
                        item_kind=item.kind)
            return
        self.combo = 0
        self.lives -= 1
        self.invulnerable_time = 1.25
        self._event('hit', lane=item.lane, lives=self.lives,
                    item_kind=item.kind, id=item.id)
        if self.lives <= 0:
            self.state = 'over'
            self._event('over', score=self.score, distance=self.distance)

    def _step(self, dt):
        self.elapsed += dt
        self._jump_buffer = max(0., self._jump_buffer-dt)
        self.combo_time = max(0., self.combo_time-dt)
        if self.combo_time <= 0:
            self.combo = 0
        self.speed = min(self.MAX_SPEED, self.START_SPEED + self.distance * .006)
        travel = self.speed * dt
        self.distance += travel
        self.score = int(self.distance * 2.) + self._score_bonus
        self._lane_elapsed = min(self.LANE_SECONDS, self._lane_elapsed + dt)
        t = self._lane_elapsed / self.LANE_SECONDS
        t = t * t * (3. - 2. * t)
        self.player_x = self._lane_start + (self.player_lane - self._lane_start) * t
        self.jump_remaining = max(0., self.jump_remaining - dt)
        self.slide_remaining = max(0., self.slide_remaining - dt)
        self.shield_time = max(0., self.shield_time - dt)
        self.magnet_time = max(0., self.magnet_time - dt)
        self.invulnerable_time = max(0., self.invulnerable_time - dt)
        was_flying = self.jetpack_time > 0
        self.jetpack_time = max(0., self.jetpack_time-dt)
        self.boots_time = max(0., self.boots_time-dt)
        self.coinboost_time = max(0., self.coinboost_time-dt)
        if was_flying and self.jetpack_time <= 0:
            # Expiration is never an unseen unavoidable train collision. The
            # physics still falls/lands on actual roofs; only contact damage
            # gets this short, visible recovery interval.
            self.invulnerable_time = max(self.invulnerable_time, 1.1)
            self._fall_velocity = 0.
            self._event('power_end', item_kind='jetpack')
        if self.distance >= self._next_wave_distance:
            self._spawn_wave()
            self._next_wave_distance += self._wave_spacing + self.rng.uniform(0., 4.)
        for item in self.entities:
            item.z -= travel
        self._vertical(dt, travel)
        for item in self.entities:
            old_z = item.z + travel
            if item.resolved:
                continue
            near_lane = abs(self.player_x - item.lane) < .43
            if item.kind == 'ramp':
                if near_lane and item.z <= 0. and item.z+self.RAMP_LENGTH >= 0.:
                    surface = item.y + self.ROOF_HEIGHT*(-item.z)/self.RAMP_LENGTH
                    if self.player_y < surface-.18:
                        self._hit(item)
                        if self.state == 'over':
                            break
                continue
            if item.kind == 'coin' and self.magnet_time > 0 and -1. < item.z < 7.5:
                self._collect(item)
                continue
            if item.kind not in self.OBSTACLES:
                if near_lane and abs(self.player_y-item.y) < 1.1 and old_z >= -.4 and item.z <= .6:
                    self._collect(item)
                elif item.z < -.8:
                    item.resolved = True

                continue
            if item.kind == 'cart':
                # The front remains at the original collision plane. A missed
                # front does not make the visible 7.2 m carriage intangible:
                # entering its lane before the rear passes is still contact.
                # _hit resolves the entire cart once, including shielded or
                # invulnerable contacts; the renderer consumes that cart.
                ramp_entry = near_lane and -.05 <= item.z <= .6 and any(r.kind == 'ramp' and r.lane == item.lane and abs(r.z+self.RAMP_LENGTH-item.z) < .01 and r.z <= 0 <= r.z+self.RAMP_LENGTH and abs(self.player_y-(r.y+self.ROOF_HEIGHT*(-r.z)/self.RAMP_LENGTH)) < .08 for r in self.entities)
                if near_lane and not ramp_entry and self.player_y < item.y + self.ROOF_HEIGHT - .14 and old_z + self.CART_LENGTH >= -.4 and item.z <= .6:
                    self._hit(item)
                    if self.state == 'over':
                        break
                elif item.z + self.CART_LENGTH < -.8:
                    item.resolved = True
                continue
            if near_lane and old_z >= -.4 and item.z <= .6:
                safe = (item.kind == 'barrier' and self.player_y-item.y > .648
                        or item.kind == 'arch' and (self.sliding or self.player_y-item.y > 1.95))
                if safe:
                    item.resolved = True
                    self._event('dodge', item_kind=item.kind, lane=item.lane)
                    self._goal(2, 1)
                else:
                    self._hit(item)
                    if self.state == 'over':
                        break
            elif item.z < -.8:
                item.resolved = True
        self.entities[:] = [item for item in self.entities
                            if item.z + (self.CART_LENGTH if item.kind == 'cart' else self.RAMP_LENGTH if item.kind == 'ramp' else 0.) > -7.
                            and not item.collected]
        self.score = int(self.distance * 2.) + self._score_bonus

    def update(self, dt, actions=None):
        if not isinstance(dt, (float, int)) or isinstance(dt, bool) or not math.isfinite(dt):
            return
        actions = actions if isinstance(actions, dict) else {}
        if actions.get('confirm') and self.state in ('title', 'over'):
            self.start()
        if self.state != 'running':
            return
        if actions.get('valid', True):
            if 'lane_target' in actions:
                # An explicit missing/rejected body target is neutral input;
                # never replay a legacy relative pulse after losing a pose.
                self.set_lane(actions['lane_target'])
            else:
                direction = actions.get('move', 0)
                if not isinstance(direction, bool) and direction in (-1, 1):
                    self.move(direction)
            if actions.get('jump'):
                self.jump()
            elif actions.get('slide'):
                self.slide()
        if dt <= 0:
            return
        # A delayed GUI frame is not permission to fast-forward through an
        # unseen obstacle. Substeps still prevent collision tunnelling.
        self.dropped_seconds += max(0., dt - self.MAX_FRAME_DT)
        remaining = min(dt, self.MAX_FRAME_DT)
        while remaining > 1e-9 and self.state == 'running':
            step = min(self.MAX_STEP, remaining)
            self._step(step)
            remaining -= step

    def snapshot(self):
        return dict(state=self.state, lane=self.player_lane, player_x=self.player_x,
                    jump_height=self.jump_height, jump_phase=self.jump_phase,
                    player_y=self.player_y, combo=self.combo, best_combo=self.best_combo,
                    multiplier=self.multiplier, roof_distance=self.roof_distance,
                    goal_index=self.goal_index, goal_progress=self.goal_progress,
                    goal_target=self.GOALS[self.goal_index % 3][1],
                    goal_label=self.GOALS[self.goal_index % 3][0],
                    goals_completed=self.goals_completed, district=int(self.distance/240)%3,
                    route_name=self.route_name,
                    slide_phase=self.slide_phase, score=self.score, coins=self.coins,
                    lives=self.lives, distance=self.distance, speed=self.speed,
                    shield_time=self.shield_time, magnet_time=self.magnet_time,
                    jetpack_time=self.jetpack_time, boots_time=self.boots_time,
                    coinboost_time=self.coinboost_time,
                    # Entity contains immutable scalar fields only. A direct
                    # detached copy avoids recursive dataclass/deepcopy work
                    # on every GTK tick when many objects are visible.
                    entities=[e.__dict__.copy() for e in self.entities])

    def report(self):
        return dict(score=int(self.score), coins=int(self.coins), elapsed=self.elapsed,
                    distance=round(self.distance, 2), counts=dict(self.counts),
                    result='over' if self.state == 'over' else 'in_progress',
                    lives=int(self.lives), mode='runner', best_combo=self.best_combo,
                    roof_distance=round(self.roof_distance, 2), goals_completed=self.goals_completed)


Engine = RunnerGame
