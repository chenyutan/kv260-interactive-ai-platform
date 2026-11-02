"""Deterministic, event-driven ink painting using NumPy and Pillow only.

The caller supplies a *completed* body action. Pose geometry controls the template;
measured speed controls ink width and opacity. No frame-by-frame fluid simulation
or external images/fonts are needed. Coordinates are the eight normalized body
points used by body_baseline.py: shoulders, elbows, wrists, hips (left/right).
"""
from __future__ import annotations

import copy
import json
import math
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ACTIONS = ("A01", "A02", "A03", "A04", "A05", "A06")
THEMES = ("landscape", "bamboo", "plum", "mixed")
MAPPING = {
    "landscape": ("远山", "主峰", "双峰", "斜坡", "水岸", "云雾"),
    "bamboo": ("竹丛", "主竹干", "双竹", "弯竹", "叶簇", "衬叶"),
    "plum": ("展开枝", "向上主枝", "分枝", "弯枝", "短枝", "梅花"),
}


def _resample(points, count=100):
    q = np.asarray(points, dtype=float)
    travel = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(q, axis=0), axis=1))]
    if travel[-1] < 1e-8:
        return np.repeat(q[:1], count, axis=0)
    keep = np.r_[True, np.diff(travel) > 1e-8]
    return np.stack([np.interp(np.linspace(0, travel[-1], count), travel[keep], q[keep, k])
                     for k in (0, 1)], axis=1)


def _curve(points, count=100):
    """Catmull-Rom interpolation; endpoints are retained for parent attachment."""
    q = np.asarray(points, dtype=float)
    padded = np.vstack((q[0], q, q[-1]))
    out = []
    for i in range(1, len(padded) - 2):
        a, b, c, d = padded[i - 1:i + 3]
        for t in np.linspace(0, 1, max(4, count // max(1, len(q) - 1)), endpoint=False):
            out.append(.5 * ((2*b) + (-a+c)*t + (2*a-5*b+4*c-d)*t*t
                             + (-a+3*b-3*c+d)*t*t*t))
    out.append(q[-1])
    return _resample(out, count)


class InkRenderer:
    def __init__(self, width=1280, height=720, seed=260):
        if width < 320 or height < 240:
            raise ValueError("Canvas must be at least 320 × 240")
        self.width, self.height, self.seed = int(width), int(height), int(seed)
        self._lock = threading.RLock()
        self._records = []
        self._parents = {}
        rng = np.random.default_rng(self.seed)
        grain = rng.normal(0, .40, (self.height, self.width, 1))
        paper = np.clip(np.array([246, 243, 232]) + grain, 0, 255).astype(np.uint8)
        self._paper = Image.fromarray(paper, "RGB")
        self._image = self._paper.copy()

    @property
    def events(self):
        with self._lock:
            return copy.deepcopy(self._records)

    def image(self):
        with self._lock:
            return self._image.copy()

    def clear(self):
        with self._lock:
            self._records.clear()
            self._parents.clear()
            self._image = self._paper.copy()
            return {"accepted": True, "event_count": 0}

    def undo(self):
        with self._lock:
            if not self._records:
                return {"accepted": False, "reason": "画布已经是空白", "event_count": 0}
            records = self._records[:-1]
            self._records = []
            self._parents = {}
            self._image = self._paper.copy()
            for record in records:
                self._paint(record)
                self._records.append(record)
            return {"accepted": True, "event_count": len(self._records)}

    def save(self, path):
        """Write the PNG and reproducible rendering events beside it."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._image.save(path, format="PNG")
            metadata = {"renderer": "bodyink-parametric-v2-bamboo-profile", "width": self.width,
                        "height": self.height, "seed": self.seed, "events": self._records}
            path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False,
                                                          indent=2), encoding="utf-8")
        return {"accepted": True, "path": str(path), "event_count": len(metadata["events"])}

    def _resolve_theme(self, action, theme):
        if theme != "mixed":
            return theme
        if action in ("A01", "A04"):
            return "landscape"
        if action == "A02":
            return "bamboo"
        if action == "A03":
            return "plum"
        if action == "A06":
            return next((x for x in ("plum", "bamboo") if x in self._parents), "landscape")
        parent = max(self._parents.items(), key=lambda pair: pair[1]["event"], default=None)
        return parent[0] if parent else "landscape"

    def add(self, event, theme="mixed"):
        if theme not in THEMES:
            raise ValueError("Unknown theme")
        action = event.get("action")
        if action not in ACTIONS:
            raise ValueError("Expected action A01–A06")
        pose = np.asarray(event["pose"], dtype=float)
        times = np.asarray(event["times"], dtype=float)
        speeds = np.asarray(event["speeds"], dtype=float)
        limits = np.asarray(event["calibration"], dtype=float)
        if (pose.ndim != 3 or pose.shape[1:] != (8, 2) or len(pose) < 2
                or times.shape != (len(pose),) or speeds.shape != times.shape
                or not np.isfinite(pose).all() or not np.isfinite(times).all()
                or not (np.diff(times) > 0).all() or limits.shape != (2,)
                or not np.isfinite(limits).all() or limits[1] <= limits[0]):
            raise ValueError("Malformed pose, timestamps, or speed calibration")
        good = np.isfinite(speeds) & (speeds >= 0)
        if not good.any():
            return {"accepted": False, "reason": "没有有效速度样本", "event_count": len(self.events)}

        # Geometry alignment does not change the measured speed or timestamps.
        # Distance progress maps SF/FS profiles along a template's brush stroke.
        dt = np.r_[0.0, np.diff(times)]
        travel = np.cumsum(np.where(good, speeds, 0.0) * dt)
        progress = travel / travel[-1] if travel[-1] > 1e-8 else (times-times[0])/(times[-1]-times[0])
        unique, first = np.unique(progress[good], return_index=True)
        profile = np.interp(np.linspace(0, 1, 96), unique, speeds[good][first])
        unit = np.clip((profile-limits[0])/(limits[1]-limits[0]), 0, 1)
        widths = 1.4 + 5.6*(1-unit)
        opacity = .18 + .65*(1-unit)
        wrists = pose[:, 4:6]
        wrist_travel = np.linalg.norm(np.diff(wrists, axis=0), axis=2).sum(axis=0)
        active = wrists[:, int(np.argmax(wrist_travel))]
        amplitude = float(np.clip(np.max(np.ptp(wrists, axis=0)), .25, 3.5))
        direction = float(np.clip((active[-1, 0]-active[0, 0])/2.0, -1, 1))
        lean = float(np.clip(np.median(pose[:, :2, 0])-np.median(pose[:, 6:8, 0]), -.8, .8))

        with self._lock:
            resolved = self._resolve_theme(action, theme)
            if action in ("A05", "A06") and resolved in ("bamboo", "plum") and resolved not in self._parents:
                return {"accepted": False, "reason": "请先完成 A01–A04，建立竹干或梅枝，再添叶添花",
                        "needs_parent": resolved, "event_count": len(self._records)}
            record = {"accepted": True, "id": len(self._records)+1, "theme": resolved,
                      "requested_theme": theme, "action": action,
                      "element": MAPPING[resolved][ACTIONS.index(action)],
                      "amplitude": amplitude, "direction": direction, "lean": lean,
                      "duration": float(times[-1]-times[0]),
                      "speed_limits": limits.tolist(), "speed_profile": profile.tolist(),
                      "width_profile": widths.tolist(), "opacity_profile": opacity.tolist(),
                      "speed_mean": float(np.mean(speeds[good])),
                      "width_mean": float(widths.mean()), "opacity_mean": float(opacity.mean()),
                      "seed": self.seed + (len(self._records)+1)*7919}
            self._paint(record)
            self._records.append(record)
            return {**copy.deepcopy(record), "event_count": len(self._records)}

    def _paint(self, event):
        rng = np.random.default_rng(event["seed"])
        layer = Image.new("RGBA", self._image.size)
        draw = ImageDraw.Draw(layer)
        scale = self.width/1280
        widths, alphas = np.array(event["width_profile"])*scale, np.array(event["opacity_profile"])

        def stroke(points, weight=1.0, density=1.0, color=(38, 45, 39), smooth=True, taper=True, dry=True,
                   profile_range=(0.0, 1.0), base_width=0.0, opacity_floor=0.0):
            q = _curve(points, 96) if smooth and len(points) > 2 else _resample(points, 96)
            # A compound stroke may consume only its corresponding part of the
            # original action. Replaying all 96 speeds inside every short bamboo
            # internode squeezed each acceleration into the same few pixels.
            progress = np.linspace(*profile_range, 96)
            brush_widths = np.interp(progress, np.linspace(0, 1, len(widths)), widths)
            brush_alphas = np.interp(progress, np.linspace(0, 1, len(alphas)), alphas)
            original = q.copy()
            pad = float(brush_widths.max())*weight+(base_width+4)*scale
            x0, y0 = max(0, math.floor(q[:, 0].min()-pad)), max(0, math.floor(q[:, 1].min()-pad))
            x1, y1 = min(self.width, math.ceil(q[:, 0].max()+pad)), min(self.height, math.ceil(q[:, 1].max()+pad))
            if x1 <= x0 or y1 <= y0:
                return original
            brush = Image.new("RGBA", (x1-x0, y1-y0))
            brush_draw = ImageDraw.Draw(brush)
            q -= [x0, y0]
            segments = []
            for i in range(95):
                t = (i+.5)/95
                tip = (.23+.77*math.sin(math.pi*t)**.30) if taper else 1.0
                w = max(.65*scale, (float(brush_widths[i])*weight+base_width*scale)*tip)
                ink = opacity_floor+(1-opacity_floor)*brush_alphas[i]
                alpha = int(255*np.clip(ink*density, 0, 1))
                a, b = tuple(q[i]), tuple(q[i+1])
                segments.append((a, b, w, alpha))
            # Paint the feathered edge first, then the whole core. Interleaving
            # these passes would erase each previous core with a pale halo.
            for a, b, w, alpha in segments:
                brush_draw.line((a, b), fill=(*color, max(1, alpha//5)), width=max(1, round(w+2*scale)))
            for a, b, w, alpha in segments:
                brush_draw.line((a, b), fill=(*color, alpha), width=max(1, round(w)))
            for i, (a, b, w, alpha) in enumerate(segments):
                if dry and w > 3*scale and rng.random() < .07:
                    offset = np.array([.8, -.4])*scale
                    brush_draw.line((tuple(q[i]+offset), tuple(q[i+1]+offset)),
                                    fill=(186, 187, 170, max(1, alpha//4)), width=max(1, round(scale)))
            layer.alpha_composite(brush, (x0, y0))
            return original

        def wash(points, strength=.15, color=(79, 90, 79)):
            mask = Image.new("L", self._image.size)
            ImageDraw.Draw(mask).polygon([tuple(p) for p in points], fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(1.8*scale))
            arr = np.asarray(mask).astype(float)
            ys = np.array(points)[:, 1]
            top, bottom = float(ys.min()), max(float(ys.max()), float(ys.min())+1)
            fade = np.clip((bottom-np.arange(self.height))/(bottom-top), 0, 1)
            opacity = np.clip(arr*strength*fade[:, None], 0, 255).astype(np.uint8)
            tint = Image.new("RGBA", self._image.size, (*color, 0))
            tint.putalpha(Image.fromarray(opacity))
            layer.alpha_composite(tint)

        if event["theme"] == "landscape":
            self._landscape(event, rng, stroke, wash)
        elif event["theme"] == "bamboo":
            self._bamboo(event, rng, stroke, draw, scale)
        else:
            self._plum(event, rng, stroke, draw, scale)
        self._image = Image.alpha_composite(self._image.convert("RGBA"), layer).convert("RGB")

    def _landscape(self, e, rng, stroke, wash):
        w, h = self.width, self.height
        a, action = e["amplitude"], e["action"]
        count = sum(x["theme"] == "landscape" and x["action"] in ACTIONS[:4] for x in self._records)
        cx = w*(.51+.055*e["direction"]+.025*((count % 3)-1))
        base = h*(.60+.045*min(count, 4))
        span = w*min(.79, .48+.075*a)
        height = h*(.19+.055*a)
        if action in ACTIONS[:4]:
            peaks = {
                "A01": [(0, 0), (.08, -.12), (.17, -.48), (.24, -.39), (.33, -.73), (.39, -.65), (.5, -.34), (.59, -.62), (.67, -.43), (.76, -.80), (.83, -.57), (.91, -.20), (1, 0)],
                "A02": [(0, 0), (.19, -.18), (.30, -.42), (.39, -.32), (.53, -1), (.58, -.88), (.66, -.46), (.77, -.21), (1, 0)],
                "A03": [(0, 0), (.15, -.29), (.29, -.94), (.34, -.79), (.48, -.28), (.59, -.45), (.71, -.84), (.76, -.65), (.88, -.27), (1, 0)],
                "A04": [(0, 0), (.12, -.26), (.22, -.61), (.35, -.48), (.47, -.51), (.59, -.31), (.75, -.19), (.9, -.10), (1, 0)],
            }[action]
            if action == "A01":
                base -= h*.07
                height *= .76
            points = np.array([(cx+(x-.5)*span, base+y*height) for x, y in peaks])
            points[1:-1, 1] += rng.uniform(-.017, .017, len(points)-2)*height
            wash(points, .16+.09*e["opacity_mean"])
            stroke(points, .65 if action == "A01" else .91, .52 if action == "A01" else .84,
                   smooth=False, taper=False)
            # Rock folds follow each peak, leaving the valleys and water unpainted.
            for i in range(1, len(points)-1):
                if points[i, 1] < points[i-1, 1] and points[i, 1] <= points[i+1, 1]:
                    px, py = points[i]
                    for j in range(4):
                        reach = (base-py)*(.47+.10*j)
                        dx = span*(.024+j*.010)*(1 if i % 2 else -1)
                        fold = [(px+j*w*.002, py+h*.008), (px+dx*.3, py+reach*.25),
                                (px+dx*.78, py+reach*.67), (px+dx, py+reach)]
                        stroke(fold, .23, .22+.06*(3-j), smooth=False)
                    for j in range(3):
                        yy = py+(base-py)*(.40+j*.15)
                        xx = px+(j-1)*w*.006
                        stroke([(xx, yy), (xx+w*.025, yy+h*.008), (xx+w*.045, yy+h*.006)], .18, .20)
            for j in range(3):
                y = base+h*(.012+j*.013)
                stroke([(cx-span*.36, y), (cx-span*.18, y-h*.004), (cx+span*.10, y+h*.003),
                        (cx+span*.27, y-h*.003)], .17, .18-j*.03)
        elif action == "A05":
            base = h*(.78+.035*e["lean"])
            for j in range(7):
                shift = math.sin(j*1.7)*w*.04
                yy = base+j*h*.014
                stroke([(w*.28+shift, yy), (w*.43+shift, yy-h*.007),
                        (w*.58+shift, yy+h*.005), (w*.77-j*w*.014, yy-h*.004)],
                       .28 if j < 2 else .16, .46-j*.04)
        else:
            for j in range(6):
                yy = h*(.47+j*.03)
                stroke([(w*.20, yy), (w*.36, yy-h*.014), (w*.5, yy+h*.005),
                        (w*.67, yy-h*.009), (w*.8, yy)], .42, .12)

    def _bamboo(self, e, rng, stroke, draw, scale):
        w, h = self.width, self.height
        action, amp = e["action"], e["amplitude"]
        if action in ACTIONS[:4]:
            count = sum(x["theme"] == "bamboo" and x["action"] in ACTIONS[:4] for x in self._records)
            root = np.array([w*(.15+.035*(count % 4)), h*.91])
            height = h*min(.74, .43+.065*amp)
            bend = w*(.05*e["direction"]+.06*e["lean"])
            if action == "A04":
                bend += w*.09*(1 if e["direction"] >= 0 else -1)
            stems = {"A01": (-.047, 0, .045), "A02": (0,), "A03": (-.025, .036), "A04": (0,)}[action]
            attachment = []
            for n, offset in enumerate(stems):
                stem_height = height*(1-.12*abs(n-(len(stems)-1)/2))
                tip = root + [bend+offset*w, -stem_height]
                # Keep a small paper break beside each node, independent of
                # stem height; broad blank fractions made the cane look dashed.
                half_gap = 1.5*scale/stem_height
                for j in range(7):
                    t0, t1 = j/7+half_gap, (j+1)/7-half_gap
                    def p(t):
                        return root + np.array([(bend+offset*w)*(t*t if action == "A04" else t), -stem_height*t])
                    p0, p1 = p(t0), p(t1)
                    stem_weight = 1.8*(1-j*.035)
                    stroke([p0, (p0+p1)/2+[1.2*scale, 0], p1], stem_weight, .96,
                           taper=False, dry=False, profile_range=(t0, t1), base_width=4.0,
                           opacity_floor=.18)
                    progress = (j+1)/7
                    node = p(progress)
                    local_width = float(np.interp(progress, np.linspace(0, 1, len(e["width_profile"])),
                                                  e["width_profile"]))
                    half_node = (1.0+(local_width*stem_weight+4.0)/2)*scale
                    stroke([node+[-half_node, .65*scale], node+[0, -.65*scale],
                            node+[half_node, 0]], .30, 1.16, taper=False, dry=False,
                           profile_range=(progress, progress), base_width=.75, opacity_floor=.18)
                    if j in (2, 4, 5):
                        attachment.append(node.tolist())
                # A few tiny living buds explain where later leaf clusters attach.
                stroke([tip, tip+[w*.009, -h*.012]], .32, .6, dry=False,
                       profile_range=(.97, 1.0))
            self._parents["bamboo"] = {"points": attachment, "event": e["id"], "root": root.tolist()}
            e["anchor"] = root.tolist()
        else:
            parent = self._parents["bamboo"]
            e["parent_event"] = parent["event"]
            anchors = parent["points"][-5:] if action == "A05" else parent["points"][:3]
            for k, point in enumerate(anchors):
                origin = np.array(point)
                sign = 1 if k % 2 == 0 else -1
                reach = w*(.045+.011*amp)
                twig = origin + [sign*reach, -h*.025]
                stroke([origin, (origin+twig)/2+[0, -h*.009], twig], .28, .77)
                for j in range(5 if action == "A05" else 3):
                    t = .24+j*.15
                    start = origin+(twig-origin)*t
                    angle = sign*(.30+j*.25)
                    dx = sign*reach*(.37+.18*(j % 2))
                    dy = h*(.052+.007*j)*(1 if j % 2 else -1)
                    tip = start+[dx*math.cos(angle), dy]
                    middle = start+(tip-start)*.55+[sign*3*scale, -3*scale]
                    stroke([start, middle, tip], 1.70 if action == "A05" else .90, .89,
                           color=(33, 49, 38), taper=True, dry=False)

    def _plum(self, e, rng, stroke, draw, scale):
        w, h = self.width, self.height
        action, amp = e["action"], e["amplitude"]
        if action in ACTIONS[:4]:
            count = sum(x["theme"] == "plum" and x["action"] in ACTIONS[:4] for x in self._records)
            root = np.array([w*(.91-.025*(count % 3)), h*.89])
            height = h*(.35+.055*amp)
            spread = w*(.18+.035*amp+.025*abs(e["direction"]))
            if action == "A02":
                spread *= .5
                height *= 1.17
            tip = root+[-spread, -height]
            if action == "A04":
                points = [root, root+[-spread*.1, -height*.33], root+[-spread*.70, -height*.56], tip]
            else:
                points = [root, root+[-spread*.25, -height*.37], root+[-spread*.67, -height*.66], tip]
            stroke(points, 1.85, .88, color=(50, 45, 38))
            for offset in (-2, 2):
                stroke(np.array(points)+[offset*scale, 0], .18, .52, color=(86, 77, 60))
            branches = []
            trunk = _curve(points, 100)
            for j, t in enumerate((.29, .44, .58, .73, .89)):
                start = trunk[int(t*99)]
                sign = -1 if j % 2 else 1
                end = start+[-spread*(.29 if sign < 0 else -.13), -height*(.25+.04*(j % 2))]
                branch = [start, start+(end-start)*.55+[sign*w*.018, -h*.003], end]
                stroke(branch, .6 if j < 3 else .43, .79, color=(49, 44, 37))
                branches.append(end.tolist())
                side = end+[sign*w*.032, -h*.024]
                stroke([branch[1], end*.55+side*.45, side], .24, .74, color=(49, 44, 37))
                branches.append(side.tolist())
            branches.append(tip.tolist())
            if action in ("A01", "A03"):
                start = trunk[42]
                fork = start+[-spread*.52, -height*.27]
                stroke([start, start+(fork-start)*.52+[0, -h*.014], fork], .73, .77, color=(49, 44, 37))
                branches.append(fork.tolist())
            self._parents["plum"] = {"points": branches, "event": e["id"], "root": root.tolist()}
            e["anchor"] = root.tolist()
        elif action == "A05":
            parent = self._parents["plum"]
            e["parent_event"] = parent["event"]
            added = []
            for j, p in enumerate(parent["points"][::3]):
                start = np.array(p)
                end = start+[w*.037*(-1 if j % 2 else 1), -h*(.035+.009*amp)]
                stroke([start, (start+end)/2+[0, -h*.01], end], .42, .79, color=(49, 44, 37))
                added.append(end.tolist())
            parent["points"].extend(added)
        else:
            parent = self._parents["plum"]
            e["parent_event"] = parent["event"]
            density = float(e["opacity_mean"])
            for j, p in enumerate(parent["points"]):
                centre = np.array(p)+rng.uniform(-4, 4, 2)*scale
                radius = scale*(4.7+1.2*amp)*(.8+.35*rng.random())
                rotation = rng.uniform(0, 2*math.pi)
                for k in range(5):
                    angle = rotation+2*math.pi*k/5
                    c = centre+radius*.72*np.array([math.cos(angle), math.sin(angle)])
                    r = radius*.64
                    box = (c[0]-r, c[1]-r, c[0]+r, c[1]+r)
                    draw.ellipse(box, fill=(161, 61, 58, int(55+125*density)),
                                 outline=(113, 46, 45, int(75+135*density)), width=max(1, round(scale)))
                rr = 1.1*scale
                draw.ellipse((centre[0]-rr, centre[1]-rr, centre[0]+rr, centre[1]+rr), fill=(62, 48, 36, 215))
                for k in range(5):
                    angle = rotation+k*2*math.pi/5+.4
                    point = centre+radius*.52*np.array([math.cos(angle), math.sin(angle)])
                    draw.line((tuple(centre), tuple(point)), fill=(96, 57, 38, 130), width=max(1, round(scale)))
