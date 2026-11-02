"""Display-only native predictions. Never feeds actions or retains old coordinates."""
import math


def draw_pose(draw, points, thresholds, edges, width, height):
    positions = []
    reliable = []
    for point, threshold in zip(points, thresholds):
        x, y, score = map(float, point[:3])
        valid = all(map(math.isfinite, (x, y, score))) and 0 <= x <= 1 and 0 <= y <= 1 and score > 0
        positions.append((x * width, y * height) if valid else None)
        reliable.append(valid and score >= float(threshold))
    for a, b in edges:
        start, end = positions[a], positions[b]
        if start is None or end is None:
            continue
        if reliable[a] and reliable[b]:
            draw.line([start, end], fill=(90, 220, 160), width=2)
        else:
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length > 0:
                for offset in range(0, math.ceil(length), 7):
                    t0, t1 = offset / length, min(offset + 3, length) / length
                    draw.line([(start[0]+dx*t0, start[1]+dy*t0),
                               (start[0]+dx*t1, start[1]+dy*t1)], fill=(165, 175, 185), width=1)
    for index, point in enumerate(positions[5:], 5):
        if point is None:
            continue
        x, y = point
        bounds = (x-2, y-2, x+2, y+2)
        if reliable[index]:
            draw.ellipse(bounds, fill=(236, 237, 205))
        else:
            draw.ellipse(bounds, outline=(165, 175, 185), width=1)
    draw.text((4, height-12), 'Solid: reliable   Dashed: uncertain', fill=(236, 237, 205))
