"""Deterministic 120 Hz platform game for 超级玛丽, with no GUI or I/O.

World coordinates are pixels, with y downward. Inputs are level-triggered
movement and edge-triggered jump/confirm. Camera and audio are observers:
accepted outcome counters are monotonic within a round, never a sound queue.
Long frames have bounded catch-up; pause/resume discards all simulation debt.
"""
import math
from types import MappingProxyType

from .levels import LEVELS, TILE

W, H = 320, 180
PLAYER_W, PLAYER_H = 12., 22.
GROUND_Y = 10 * TILE
STEP = 1 / 120
MAX_DT = .1
MAX_STEPS = 12
RUN_SPEED = 118.
GRAVITY = 840.
JUMP_SPEED = 292.
MAX_FALL_SPEED = 420.
COYOTE_TIME = .15
JUMP_BUFFER = .18
MAX_PARTICLES = 40
AUDIO_EVENTS = ("jump", "coin", "bump", "break", "power", "stomp", "hurt",
                "die", "pipe", "checkpoint", "clear", "win")
MAX_AUDIO_COUNT = (1 << 63) - 1
EPS = 1e-7


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _overlap(a, b):
    return (a["x"] < b["x"] + b["w"] - EPS and
            a["x"] + a["w"] > b["x"] + EPS and
            a["y"] < b["y"] + b["h"] - EPS and
            a["y"] + a["h"] > b["y"] + EPS)


def _approach(value, target, amount):
    return min(value + amount, target) if value < target else max(value - amount, target)


class Game:
    """Three handcrafted stages. ``start(0)`` skips the opening countdown.

    Public player/entities are dictionaries for simple independent renderers;
    snapshots are copies, and audio_counts is read-only. Optional LevelSpecs
    make physics scenarios directly testable without GUI or board dependencies.
    """
    def __init__(self, levels=LEVELS):
        self.levels = tuple(levels)
        if not self.levels:
            raise ValueError("at least one level is required")
        self.round_id = 0
        self.start()

    @property
    def phase(self):
        return self.state

    @property
    def time(self):
        return self.elapsed

    @property
    def paused(self):
        return self.state == "paused"

    @property
    def audio_counts(self):
        return self._audio_view

    @property
    def audio_round(self):
        return self.round_id

    def _audio(self, kind):
        self._audio_counts[kind] = min(MAX_AUDIO_COUNT, self._audio_counts[kind] + 1)

    def start(self, countdown=1.2):
        self.round_id += 1
        self.lives, self.score, self.coins = 3, 0, 0
        self.elapsed, self.deaths, self.stomps = 0., 0, 0
        self.stage_clears, self.pipes_used = 0, 0
        self.secrets_found = 0
        self.rescued = False
        self._audio_counts = dict.fromkeys(AUDIO_EVENTS, 0)
        self._audio_view = MappingProxyType(self._audio_counts)
        self._accumulator = 0.
        self._jump_down = self._confirm_down = False
        self._jump_buffer = self._coyote = 0.
        self._move = 0
        self._confirm_pending = False
        self._paused_state = None
        self._transition_time = 0.
        self.transition_kind = ""
        self.message = "出发！"
        self._load_level(0)
        self.countdown = max(0., float(countdown)) if _finite(countdown) else 1.2
        self.state = "countdown" if self.countdown > 0 else "playing"
        return self.snapshot()

    def restart(self, countdown=1.2):
        return self.start(countdown)

    def pause(self, value=True):
        if not value:
            return self.resume()
        if self.state not in ("paused", "won", "over"):
            self._paused_state = self.state
            self.state = "paused"
        self._reset_input()
        return self.snapshot()

    def resume(self):
        if self.state == "paused":
            self.state = self._paused_state or "playing"
            self._paused_state = None
        self._reset_input()
        return self.snapshot()

    def _reset_input(self):
        self._accumulator = self._jump_buffer = self._coyote = 0.
        self._move = 0
        self._confirm_pending = False
        # Held gestures must be released after a pause before a new edge is
        # accepted. This avoids an unintended jump or pipe on tracking recovery.
        self._jump_down = self._confirm_down = True

    def _load_level(self, index):
        self.level_index = index
        self.level = self.levels[index]
        self.tiles = []
        self._tile_grid = {}
        for ident, t in enumerate(self.level.tiles):
            tile = dict(id=ident, x=t.col*TILE, y=t.row*TILE, w=TILE,
                        h=TILE, kind=t.kind, content=t.content,
                        active=True, solid=True, bump=0.)
            self.tiles.append(tile)
            self._tile_grid[t.col, t.row] = tile
        self.collectibles = [dict(id=i, x=c*TILE+4, y=r*TILE+3, w=8, h=10,
                                  active=True, collected=False)
                             for i, (c, r) in enumerate(self.level.coins)]
        self.enemies = [dict(id=i, x=e.col*TILE, y=e.row*TILE, w=12, h=12,
                             vx=e.direction*(29. if e.kind == "stone" else 22.),
                             vy=0., facing=e.direction, kind=e.kind, alive=True)
                        for i, e in enumerate(self.level.enemies)]
        self.pipes = [dict(id=i, x=p.col*TILE, y=p.row*TILE, w=32,
                           h=(10-p.row)*TILE, target_x=p.target_col*TILE+10,
                           target_y=p.target_row*TILE-PLAYER_H, label=p.label)
                      for i, p in enumerate(self.level.pipes)]
        self.powerups, self.particles = [], []
        self.checkpoint = False
        self._spawn_x = self.level.spawn_col*TILE
        # Renderer uses a 16x24 sprite anchored at the same feet: a 12x22
        # collider gives a two-pixel visual margin at the head and either side.
        self._spawn_y = (self.level.spawn_row+1)*TILE-PLAYER_H
        self.player = dict(x=float(self._spawn_x), y=float(self._spawn_y),
                           w=PLAYER_W, h=PLAYER_H, vx=0., vy=0., facing=1,
                           grounded=False, powered=False, invincible=0., dead=False)
        self.camera_x = 0.
        self._jump_buffer = self._coyote = 0.
        self._confirm_pending = False
        self.message = self.level.hint

    def _near_tiles(self, x0, y0, x1, y1):
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        left, right = math.floor(x0/TILE), math.floor(x1/TILE)
        top, bottom = math.floor(y0/TILE), math.floor(y1/TILE)
        for row in range(top, bottom+1):
            for col in range(left, right+1):
                tile = self._tile_grid.get((col, row))
                if tile is not None and tile["solid"]:
                    yield tile

    def _solid_at(self, x, y):
        """Point query equivalent to a one-cell _near_tiles rectangle."""
        tile = self._tile_grid.get((math.floor(x/TILE), math.floor(y/TILE)))
        return tile is not None and tile["solid"]

    def _move_x(self, obj, dx):
        old, target = obj["x"], obj["x"] + dx
        hit = False
        if dx == 0:
            obj["x"] = max(0., min(self.level.width-obj["w"], target))
            return hit
        for tile in self._near_tiles(min(old, target), obj["y"]+EPS,
                                     max(old+obj["w"], target+obj["w"]),
                                     obj["y"]+obj["h"]-EPS):
            if obj["y"]+obj["h"] <= tile["y"]+EPS or obj["y"] >= tile["y"]+tile["h"]-EPS:
                continue
            if dx > 0 and old+obj["w"] <= tile["x"]+EPS and target+obj["w"] > tile["x"]:
                target, hit = min(target, tile["x"]-obj["w"]), True
            elif dx < 0 and old >= tile["x"]+tile["w"]-EPS and target < tile["x"]+tile["w"]:
                target, hit = max(target, tile["x"]+tile["w"]), True
        target = max(0., min(self.level.width-obj["w"], target))
        obj["x"] = target
        return hit

    def _move_y(self, obj, dy, head=False):
        old, target = obj["y"], obj["y"] + dy
        landed, bumped = False, []
        for tile in self._near_tiles(obj["x"]+EPS, min(old, target),
                                     obj["x"]+obj["w"]-EPS,
                                     max(old+obj["h"], target+obj["h"])):
            if obj["x"]+obj["w"] <= tile["x"]+EPS or obj["x"] >= tile["x"]+tile["w"]-EPS:
                continue
            if dy > 0 and old+obj["h"] <= tile["y"]+EPS and target+obj["h"] >= tile["y"]:
                target, landed = min(target, tile["y"]-obj["h"]), True
            elif dy < 0 and old >= tile["y"]+tile["h"]-EPS and target < tile["y"]+tile["h"]:
                face = tile["y"]+tile["h"]
                if face > target + EPS:
                    target, bumped = face, [tile]
                elif abs(face-target) <= EPS:
                    bumped.append(tile)
        obj["y"] = target
        if landed or bumped:
            obj["vy"] = 0.
        if head and bumped:
            # A single accepted head hit grants one outcome, even across a seam.
            tile = min(bumped, key=lambda t: abs(t["x"]+8-(obj["x"]+obj["w"]/2)))
            self._head_hit(tile)
        return landed

    def _effect(self, kind, x, y):
        for index in range(5 if kind in ("break", "win") else 3):
            self.particles.append(dict(kind=kind, x=x, y=y,
                                       vx=(index-2)*18., vy=-48.-index*9.,
                                       age=0., ttl=.45))
        del self.particles[:-MAX_PARTICLES]

    def _head_hit(self, tile):
        tile["bump"] = .16
        kind = tile["kind"]
        if kind == "question":
            tile["kind"] = "used"
            if tile["content"] == "power":
                self.powerups.append(dict(x=tile["x"]+2., y=tile["y"]-12.,
                                          w=12., h=12., vx=28., vy=-60.,
                                          active=True, kind="leaf"))
                self._audio("bump")
                self.message = "护盾出现了"
            else:
                self._collect_coin(tile["x"]+8, tile["y"]-4)
        elif kind == "brick" and tile["content"] == "secret":
            tile["kind"], tile["content"] = "used", ""
            self.secrets_found += 1
            for _ in range(5):
                self._collect_coin(tile["x"]+8, tile["y"]-4)
            self.message = "找到隐藏金币！"
        elif kind == "brick" and self.player["powered"]:
            tile["active"] = tile["solid"] = False
            self.score += 50
            self._audio("break")
            self._effect("break", tile["x"]+8, tile["y"]+8)
        else:
            self._audio("bump")

    def _collect_coin(self, x, y):
        self.coins += 1
        self.score += 100
        self._audio("coin")
        self._effect("coin", x, y)
        if self.coins % 30 == 0 and self.lives < 5:
            self.lives += 1
            self.message = "收集30枚金币，增加1条命"

    def can_enter_pipe(self, *, require_stopped=False):
        """Share exact pipe geometry with the contextual input/UI adapter."""
        p = self.player
        if self.state != 'playing' or not p['grounded']:
            return False
        if require_stopped and abs(p['vx']) > 8.:
            return False
        center = p['x'] + p['w'] / 2
        return any(abs(center - (pipe['x'] + 16)) <= 11
                   and abs(p['y'] + p['h'] - pipe['y']) <= .5
                   for pipe in self.pipes)

    def _jump(self):
        self.player["vy"] = -JUMP_SPEED
        self.player["grounded"] = False
        self._jump_buffer = self._coyote = 0.
        self._audio("jump")

    def advance(self, dt, input=None, *, snapshot=True, **kwargs):
        """Advance bounded simulation time and return a detached render snapshot.

        A large elapsed interval represents a delayed GUI frame, never a demand
        to fast-forward a visitor through obstacles. Invalid dt has no effect.
        Edge input is remembered even if this frame has less than one step.
        A caller that takes its own snapshot can pass ``snapshot=False`` to
        avoid allocating a second, unused scene; that call returns None.
        """
        if not _finite(dt) or dt < 0:
            return self.snapshot() if snapshot else None
        controls = input if isinstance(input, dict) else {}
        if kwargs:
            controls = dict(controls, **kwargs)
        move = controls.get("move", 0)
        self._move = (1 if move > .18 else -1 if move < -.18 else 0) if _finite(move) else 0
        jump, confirm = bool(controls.get("jump", False)), bool(controls.get("confirm", False))
        if self.state == "playing":
            if jump and not self._jump_down:
                self._jump_buffer = JUMP_BUFFER
            if confirm and not self._confirm_down:
                self._confirm_pending = True
        self._jump_down, self._confirm_down = jump, confirm
        if self.state in ("paused", "won", "over"):
            self._accumulator = 0.
            return self.snapshot() if snapshot else None
        self._accumulator = min(MAX_DT, self._accumulator + min(float(dt), MAX_DT))
        count = min(MAX_STEPS, int((self._accumulator+1e-10)/STEP))
        for _ in range(count):
            self._accumulator = max(0., self._accumulator-STEP)
            self._step()
            if self.state in ("won", "over", "paused"):
                self._accumulator = 0.
                break
        return self.snapshot() if snapshot else None

    def _step(self):
        if self.state == "countdown":
            self.countdown = max(0., self.countdown-STEP)
            if self.countdown <= 1e-9:
                self.countdown, self.state = 0., "playing"
                self._jump_buffer = 0.
            return
        if self.state == "transition":
            self._transition_time -= STEP
            self._animate()
            if self._transition_time <= 1e-9:
                self._finish_transition()
            return
        if self.state != "playing":
            return
        self.elapsed += STEP
        p = self.player
        p["invincible"] = max(0., p["invincible"]-STEP)
        self._coyote = COYOTE_TIME if p["grounded"] else max(0., self._coyote-STEP)
        if self._jump_buffer > 0 and (p["grounded"] or self._coyote > 0):
            self._jump()
        self._jump_buffer = max(0., self._jump_buffer-STEP)
        old_bottom = p["y"]+p["h"]
        p["vx"] = _approach(p["vx"], self._move*RUN_SPEED,
                             (930. if self._move else 1200.)*STEP)
        if self._move:
            p["facing"] = self._move
        if self._move_x(p, p["vx"]*STEP):
            p["vx"] = 0.
        p["vy"] = min(MAX_FALL_SPEED, p["vy"]+GRAVITY*STEP)
        p["grounded"] = self._move_y(p, p["vy"]*STEP, head=True)
        if p["grounded"] and self._jump_buffer > 0:
            self._jump()
        for enemy in self.enemies:
            if not enemy["alive"]:
                continue
            enemy["old_x"], enemy["old_y"] = enemy["x"], enemy["y"]
            # Patrol turns at ledges as well as walls, keeping hazards readable.
            ahead = enemy["x"] + (enemy["w"]+2 if enemy["vx"] > 0 else -2)
            below = enemy["y"]+enemy["h"]+3
            # Ground foes turn before a platform with too little clearance
            # for the hero and a readable stomp.
            ceiling_ahead = ahead + (16 if enemy["vx"] > 0 else -16)
            ceiling_y = enemy["y"]+enemy["h"]-28
            if enemy["vy"] == 0 and (self._solid_at(ceiling_ahead, ceiling_y)
                                     or not self._solid_at(ahead, below)):
                enemy["vx"] *= -1
            if self._move_x(enemy, enemy["vx"]*STEP):
                enemy["vx"] *= -1
            enemy["facing"] = 1 if enemy["vx"] > 0 else -1
            enemy["vy"] = min(MAX_FALL_SPEED, enemy["vy"]+GRAVITY*STEP)
            self._move_y(enemy, enemy["vy"]*STEP)
            if enemy["y"] > self.level.height+32:
                enemy["alive"] = False
                continue
            # The player's downward sweep crossing the top is a stomp even
            # if a fast fall ends below a short enemy's top in this step.
            top_before = enemy["old_y"]
            top_after = enemy["y"]
            horizontal = (p["x"]+p["w"] > enemy["x"]+EPS and
                          p["x"] < enemy["x"]+enemy["w"]-EPS)
            stomp = (horizontal and p["vy"] > 0 and old_bottom <= top_before+2 and
                     p["y"]+p["h"] >= top_after-EPS)
            if stomp:
                enemy["alive"] = False
                p["y"], p["vy"] = top_after-p["h"], -225.
                p["grounded"] = False
                self._coyote = 0.
                self.stomps += 1
                self.score += 200
                self._audio("stomp")
                self._effect("stomp", enemy["x"]+6, enemy["y"]+6)
            elif _overlap(p, enemy) and p["invincible"] <= 0:
                self._hurt()
                if self.state != "playing":
                    break
        if self.state != "playing":
            return
        for coin in self.collectibles:
            if coin["active"] and _overlap(p, coin):
                coin["active"], coin["collected"] = False, True
                self._collect_coin(coin["x"]+4, coin["y"]+5)
        for power in self.powerups:
            if not power["active"]:
                continue
            if self._move_x(power, power["vx"]*STEP):
                power["vx"] *= -1
            power["vy"] = min(MAX_FALL_SPEED, power["vy"]+GRAVITY*STEP)
            self._move_y(power, power["vy"]*STEP)
            if _overlap(p, power):
                power["active"] = False
                p["powered"] = True
                p["invincible"] = max(p["invincible"], .6)
                self.score += 250
                self._audio("power")
                self._effect("power", p["x"]+6, p["y"]+7)
                self.message = "已获得护盾"
            elif power["y"] > self.level.height+32:
                power["active"] = False
        if p["y"] > self.level.height+40:
            self._die()
            return
        if not self.checkpoint and p["x"] >= self.level.checkpoint_col*TILE and p["grounded"]:
            self.checkpoint = True
            self._spawn_x = self.level.checkpoint_col*TILE
            self._spawn_y = GROUND_Y-PLAYER_H
            self._audio("checkpoint")
            self.score += 100
            self.message = "检查点已保存"
        if self._confirm_pending:
            self._confirm_pending = False
            for pipe in self.pipes:
                center = p["x"]+p["w"]/2
                if p["grounded"] and abs(center-(pipe["x"]+16)) <= 11 and abs(p["y"]+p["h"]-pipe["y"]) <= .5:
                    self._begin_transition("pipe", .45)
                    self._pipe_target = pipe
                    self._audio("pipe")
                    self.pipes_used += 1
                    self.message = "进入管道"
                    break
        # The real final stage ends only beside the princess at floor height.
        # Merely crossing the castle entrance (or flying over her) is not a win.
        reached_goal = p["x"]+p["w"] >= self.level.goal_col*TILE and p["grounded"]
        if self.level.princess_col:
            reached_goal = (p["grounded"] and abs(p["y"]+p["h"]-GROUND_Y) < .5
                            and _overlap(p, dict(x=self.level.princess_col*TILE,
                                                y=GROUND_Y-24, w=16, h=24)))
        if self.state == "playing" and reached_goal:
            self.stage_clears += 1
            self.score += 1000
            if self.level_index+1 == len(self.levels):
                self.state = "won"
                self.rescued = bool(self.level.princess_col)
                self._audio("win")
                self.message = "找到公主了！"
                self._effect("win", p["x"]+6, p["y"])
            else:
                self._begin_transition("stage", .8)
                self._audio("clear")
                self.message = "过关！前往下一关"
        self.camera_x = max(0., min(self.level.width-W, p["x"]-W*.38))
        self._animate()

    def _animate(self):
        for tile in self.tiles:
            # Most tiles never move. Keep public tile dictionaries writable,
            # but avoid a subtraction, max call and assignment for every zero.
            bump = tile["bump"]
            if bump:
                tile["bump"] = max(0., bump-STEP)
        for effect in self.particles:
            effect["age"] += STEP
            effect["x"] += effect["vx"]*STEP
            effect["y"] += effect["vy"]*STEP
            effect["vy"] += 220.*STEP
        self.particles[:] = [p for p in self.particles if p["age"] < p["ttl"]]

    def _hurt(self):
        if self.player["powered"]:
            self.player["powered"] = False
            self.player["invincible"] = 1.5
            self._audio("hurt")
            self._effect("hurt", self.player["x"]+6, self.player["y"]+7)
            self.message = "护盾抵挡了一次碰撞"
        else:
            self._die()

    def _die(self):
        if self.player["dead"] or self.state != "playing":
            return
        self.player["dead"] = True
        self.player["vx"] = self.player["vy"] = 0.
        self.lives -= 1
        self.deaths += 1
        self._audio("die")
        self._jump_buffer = self._coyote = 0.
        self._confirm_pending = False
        if self.lives <= 0:
            self.state = "over"
            self.message = "游戏结束"
        else:
            self._begin_transition("respawn", .85)
            self.message = "正在返回检查点"

    def _begin_transition(self, kind, duration):
        self.state, self.transition_kind = "transition", kind
        self._transition_time = duration
        self._jump_buffer = self._coyote = 0.
        self._confirm_pending = False

    def _finish_transition(self):
        kind = self.transition_kind
        if kind == "stage":
            powered = self.player["powered"]
            self._load_level(self.level_index+1)
            self.player["powered"] = powered
            self.countdown, self.state = .8, "countdown"
        elif kind == "respawn":
            self.player.update(x=float(self._spawn_x), y=float(self._spawn_y),
                               vx=0., vy=0., powered=False, invincible=2.,
                               grounded=False, dead=False)
            self.camera_x = max(0., min(self.level.width-W, self.player["x"]-W*.38))
            self.countdown, self.state = .6, "countdown"
        elif kind == "pipe":
            pipe = self._pipe_target
            self.player.update(x=float(pipe["target_x"]), y=float(pipe["target_y"]),
                               vx=0., vy=0., grounded=True,
                               invincible=max(1., self.player["invincible"]))
            self.camera_x = max(0., min(self.level.width-W, self.player["x"]-W*.38))
            self.state = "playing"
            self.message = "到达奖励路线"
        self.transition_kind = ""
        self._jump_buffer = self._coyote = 0.
        self._confirm_pending = False

    def snapshot(self):
        p = self.player.copy()
        p["invuln"] = p["invincible"]
        p["state"] = ("dead" if p["dead"] else "jumping" if not p["grounded"] else
                      "running" if abs(p["vx"]) > 2 else "idle")
        coins = [c.copy() for c in self.collectibles]
        return dict(state=self.state, phase=self.state, countdown=self.countdown,
                    lives=self.lives, score=self.score, coins=self.coins,
                    coin_count=self.coins, level_index=self.level_index,
                    elapsed=self.elapsed, time=self.elapsed, round_id=self.round_id,
                    audio_counts=self._audio_counts.copy(), camera_x=self.camera_x,
                    camera_y=0., player=p,
                    stage=dict(name=self.level.name, theme=self.level.theme,
                               width=self.level.width, height=self.level.height,
                               goal_x=self.level.goal_col*TILE,
                               checkpoint_x=self.level.checkpoint_col*TILE,
                               hint=self.level.hint,
                               castle_x=self.level.castle_col*TILE,
                               princess_x=self.level.princess_col*TILE),
                    tiles=[t.copy() for t in self.tiles if t["active"]],
                    collectibles=coins, coin_entities=coins,
                    enemies=[e.copy() for e in self.enemies],
                    powerups=[e.copy() for e in self.powerups],
                    pipes=[e.copy() for e in self.pipes],
                    particles=[e.copy() for e in self.particles],
                    checkpoint=self.checkpoint, message=self.message,
                    rescued=self.rescued, secrets_found=self.secrets_found,
                    transition_kind=self.transition_kind,
                    transition_time=max(0., self._transition_time))

    def report(self, completed=None):
        """JSON-safe visitor result; completion means reaching the final gate."""
        won = self.state == "won"
        return dict(game="platformer", title="超级玛丽", score=self.score,
                    completed=won if completed is None else bool(completed and won),
                    duration=round(self.elapsed, 2), coins=self.coins,
                    level=self.level_index+1, stages_cleared=self.stage_clears,
                    lives=self.lives, deaths=self.deaths, stomps=self.stomps,
                    pipes_used=self.pipes_used, enemies_defeated=self.stomps,
                    secrets_found=self.secrets_found, princess_rescued=self.rescued,
                    completed_stages=self.stage_clears, round_id=self.round_id,
                    status="won" if won else "over" if self.state == "over" else "ended")
