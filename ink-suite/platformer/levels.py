"""Original, handcrafted maps for 超级玛丽. Coordinates are tile cells.

The first route teaches safe jumps; the second adds staggered platforms;
the third combines the learned moves. All gaps fit the fixed jump arc.
There is no generated terrain and no external asset dependency.
"""
from dataclasses import dataclass

TILE = 16


@dataclass(frozen=True)
class TileSpec:
    col: int
    row: int
    kind: str = "ground"
    content: str = ""


@dataclass(frozen=True)
class EnemySpec:
    col: float
    row: float = 9.25
    kind: str = "lantern"
    direction: int = -1


@dataclass(frozen=True)
class PipeSpec:
    col: int
    target_col: int
    label: str = "奖励路线"
    row: int = 8
    target_row: int = 8


@dataclass(frozen=True)
class LevelSpec:
    name: str
    theme: str
    width: int
    tiles: tuple
    coins: tuple = ()
    enemies: tuple = ()
    pipes: tuple = ()
    checkpoint_col: int = 20
    goal_col: int = 40
    spawn_col: int = 2
    spawn_row: int = 9
    height: int = 192
    hint: str = ""
    castle_col: int = 0
    princess_col: int = 0


def _map(name, theme, columns, gaps, platforms, blocks, coins, enemies,
         pipes, checkpoint, goal, hint="", *, secrets=(), castle=0, princess=0):
    tiles = []
    holes = {c for first, last in gaps for c in range(first, last)}
    for col in range(columns):
        if col not in holes:
            material = "ground" if theme == "bamboo" else "cave_ground" if theme == "temple" else "castle_ground"
            top = "hall_ground" if castle and col >= castle else material
            tiles.extend((TileSpec(col, 10, top), TileSpec(col, 11, material)))
    for first, last, row in platforms:
        tiles.extend(TileSpec(col, row, "brick") for col in range(first, last))
    for col, row, content in blocks:
        tiles.append(TileSpec(col, row, "question", content))
    for col, row in secrets:
        tiles.append(TileSpec(col, row, "brick", "secret"))
    # Two-cell pipes are collision solids. Their entry action is separately
    # represented, so touching a pipe never silently changes the route.
    for pipe in pipes:
        for col in (pipe.col, pipe.col + 1):
            for row in range(pipe.row, 10):
                tiles.append(TileSpec(col, row, "pipe"))
    # Unique cells prevent a duplicated platform from granting duplicate loot.
    unique = {(t.col, t.row): t for t in tiles}
    return LevelSpec(name, theme, columns * TILE, tuple(unique.values()),
                     tuple(coins), tuple(enemies), tuple(pipes), checkpoint,
                     goal, hint=hint, castle_col=castle, princess_col=princess)


# Legacy theme keys keep recorded scenes and renderer callers compatible.
# Each pipe leads to a supported elevated balcony: no mandatory teleport,
# no invisible return action, and every balcony drops back onto the main route.
LEVELS = (
    _map("第一关 · 草地", "bamboo", 80,
         gaps=((28, 30), (54, 56)),
         platforms=((12, 15, 8), (34, 41, 5), (61, 64, 8)),
         blocks=((8, 7, "coin"), (9, 7, "power"), (20, 7, "coin"),
                 (46, 7, "coin"), (63, 5, "coin")),
         coins=((5, 8), (6, 8), (13, 6), (14, 6), (25, 8), (27, 7),
                (29, 6), (31, 7), (34, 3), (35, 3), (36, 3), (37, 3),
                (38, 3), (39, 3), (40, 3), (50, 8), (53, 7),
                (55, 6), (57, 7), (69, 8), (70, 8)),
         enemies=(EnemySpec(18), EnemySpec(44), EnemySpec(67)),
         pipes=(PipeSpec(23, 34, "草地奖励路线", target_row=5),),
         checkpoint=42, goal=76, secrets=((19, 7),)),
    _map("第二关 · 地下通道", "temple", 100,
         gaps=((24, 27), (47, 50), (76, 79)),
         platforms=((10, 13, 8), (15, 18, 6), (32, 35, 8), (38, 41, 6),
                    (58, 65, 5), (69, 72, 8), (84, 87, 8), (88, 91, 6)),
         blocks=((6, 7, "power"), (11, 5, "coin"), (30, 7, "coin"),
                 (66, 7, "power"), (85, 5, "coin")),
         coins=((5, 8), (10, 6), (12, 6), (16, 4), (17, 4), (22, 7),
                (25, 6), (28, 7), (33, 6), (39, 4), (40, 4), (45, 7),
                (48, 6), (51, 7), (58, 3), (59, 3), (60, 3), (61, 3),
                (62, 3), (63, 3), (64, 3), (70, 6), (75, 7), (77, 6),
                (80, 7), (85, 6), (89, 4), (90, 4), (93, 8)),
         enemies=(EnemySpec(20), EnemySpec(36, kind="stone"),
                  EnemySpec(55), EnemySpec(73, kind="stone"), EnemySpec(92)),
         pipes=(PipeSpec(43, 58, "地下奖励路线", target_row=5),),
         checkpoint=66, goal=96, secrets=((31, 5), (67, 5))),
    _map("第三关 · 城堡", "moon", 132,
         gaps=((25, 28), (45, 49), (69, 73), (103, 106)),
         platforms=((11, 14, 8), (17, 20, 6), (33, 37, 8), (53, 56, 8),
                    (59, 62, 6), (79, 82, 8), (83, 85, 9), (85, 87, 8),
                    (87, 89, 7), (97, 100, 8), (112, 115, 8)),
         blocks=((7, 7, "power"), (12, 5, "coin"), (34, 5, "coin"),
                 (54, 5, "power"), (80, 5, "coin"), (98, 5, "power")),
         coins=((5, 8), (6, 8), (12, 6), (18, 4), (19, 4), (24, 7),
                (26, 6), (29, 7), (34, 6), (36, 6), (43, 7), (47, 6),
                (50, 7), (54, 6), (60, 4), (61, 4), (67, 7), (71, 6),
                (74, 7), (80, 6), (85, 6), (88, 5), (98, 6), (102, 7),
                (104, 6), (107, 7), (113, 6), (120, 8), (121, 8)),
         enemies=(EnemySpec(21, kind="stone"), EnemySpec(39),
                  EnemySpec(63, kind="stone"), EnemySpec(76),
                  EnemySpec(109, kind="stone"), EnemySpec(117)),
         pipes=(PipeSpec(40, 59, "城堡外的奖励路线", target_row=6),),
         checkpoint=92, goal=124, secrets=((55, 5),), castle=92, princess=124),
)
