"""Saved keypoint replay and native Cairo QA. Never accesses a camera/display.

Run from InkSuite: python3 -m avatar.verify_offline --out avatar/qa
The host report explicitly does not claim KV260 or live latency measurements.
"""
import argparse
import copy
import json
import platform
from pathlib import Path
import statistics
import time
import unittest
import io
import cairo
from .rig import Rig, neutral_pose
from .renderer import Renderer


HERE = Path(__file__).resolve().parent


def extract_samples():
    folder = HERE.parents[1] / 'InkFight' / 'native' / 'fixtures'
    candidates = []
    for path in sorted(folder.glob('front*round.json')):
        obj = json.loads(path.read_text(encoding='utf-8'))
        for index, state in enumerate(obj['poses']):
            p = state.get('points17')
            if p and all(p[i][2] >= .28 for i in (5, 6, 7, 8, 9, 10)):
                width = abs(p[5][0] - p[6][0])
                if width >= .055:
                    candidates.append((path.name, index, state, width))
    if not candidates:
        raise ValueError('No saved keypoint fixtures available')
    def up(row):
        p, width = row[2]['points17'], row[3]
        return min(p[5][1] - p[9][1], p[6][1] - p[10][1]) / width
    def spread(row):
        p, width = row[2]['points17'], row[3]
        return ((p[9][0] - p[10][0]) / width
                - (abs(p[9][1] - p[5][1]) + abs(p[10][1] - p[6][1])) / width * .5)
    def crossed(row):
        p, width = row[2]['points17'], row[3]
        return (p[10][0] - p[9][0]) / width
    def resting(row):
        p, width = row[2]['points17'], row[3]
        return min(p[9][1] - p[5][1], p[10][1] - p[6][1]) / width
    result = dict(provenance='Unmodified saved COCO-17 observations. Selected by numeric pose metrics; no new camera capture.',
                  frame_size=[1920, 1080], samples={})
    for name, metric in (('resting', resting), ('raised', up), ('spread', spread), ('crossed', crossed)):
        name_src, index, state, width = max(candidates, key=metric)
        result['samples'][name] = dict(source_file='outputs/InkFight/native/fixtures/' + name_src,
                                       source_index=index, metric=metric((name_src, index, state, width)), state=state)
    return result


def settle(state):
    rig = Rig()
    for i in range(50):
        value = dict(state, sequence=i, timestamp_seconds=100. + i / 30.)
        pose = rig.update(value, 100. + i / 30.)
    return pose


def background(c):
    # Plain neutral QA paper only; production backgrounds are independent PNGs.
    c.set_source_rgb(.94, .925, .87); c.paint()


def stats(items):
    return dict(mean_ms=statistics.mean(items), p50_ms=statistics.median(items),
                p95_ms=sorted(items)[int(len(items) * .95) - 1], max_ms=max(items))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=HERE / 'qa')
    parser.add_argument('--frames', type=int, default=240)
    parser.add_argument('--background', type=Path, default=HERE / 'assets' / 'bamboo.png')
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    fixture_file = HERE / 'fixtures' / 'recorded_poses.json'
    fixtures = json.loads(fixture_file.read_text(encoding='utf-8')) if fixture_file.exists() else extract_samples()
    (HERE / 'fixtures').mkdir(exist_ok=True)
    (HERE / 'fixtures' / 'recorded_poses.json').write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding='utf-8')
    renderer = Renderer()
    art = cairo.ImageSurface.create_from_png(str(args.background)) if args.background.exists() else None
    poses = {'neutral': neutral_pose()}
    poses.update({name: settle(item['state']) for name, item in fixtures['samples'].items()})
    for name, pose in poses.items():
        image = cairo.ImageSurface(cairo.FORMAT_RGB24, 1280, 720)
        c = cairo.Context(image); background(c); renderer.render(c, 1280, 720, pose)
        image.write_to_png(str(args.out / (name + '-1280x720.png')))
        if art is not None:
            c.save(); c.scale(1280 / art.get_width(), 720 / art.get_height())
            c.set_source_surface(art); c.paint(); c.restore()
            renderer.render(c, 1280, 720, pose)
            image.write_to_png(str(args.out / (name + '-bamboo-1280x720.png')))
    # Directly verify the corrected folded sleeves contain the complete arm,
    # not just a plausible outside contour concealing self-intersection holes.
    sleeve_samples = 0
    for pose in poses.values():
        for sh, el, wr, side in ((5, 7, 9, -1), (6, 8, 10, 1)):
            layer = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 720)
            lc = cairo.Context(layer)
            renderer._sleeve(lc, pose.joints[sh], pose.joints[el], pose.joints[wr], side)
            layer.flush(); pixels = bytes(layer.get_data()); stride = layer.get_stride()
            for a, b in ((sh, el), (el, wr)):
                start, end = pose.joints[a], pose.joints[b]
                for i in range(19):
                    t = i / 20.
                    x, y = round(start[0] * (1-t) + end[0] * t), round(start[1] * (1-t) + end[1] * t)
                    assert pixels[y * stride + x * 4 + 3] > 200, (sh, a, b, t, 'transparent arm hole')
                    sleeve_samples += 1
    # Replay all actual observations in timestamp order, independent of host clock.
    source = HERE.parents[1] / 'InkFight' / 'native' / 'fixtures' / 'front_round.json'
    observed = json.loads(source.read_text(encoding='utf-8'))['poses']
    rig = Rig(); wall = []; cpu = []; rig_wall = []; status = {}; max_step = 0.; previous = None
    image = cairo.ImageSurface(cairo.FORMAT_RGB24, 1280, 720); c = cairo.Context(image)
    for state in observed:
        started = time.perf_counter()
        pose = rig.update(state, state['timestamp_seconds'])
        rig_wall.append((time.perf_counter() - started) * 1000.)
        status[pose.status] = status.get(pose.status, 0) + 1
        if previous:
            for key in (7, 8, 9, 10):
                a, b = previous.joints[key], pose.joints[key]
                max_step = max(max_step, ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** .5)
        previous = pose
    for n in range(12):
        background(c); renderer.render(c, 1280, 720, tuple(poses.values())[n % len(poses)])
    for n in range(max(30, args.frames)):
        pose = tuple(poses.values())[n % len(poses)]
        background(c); start = time.perf_counter(); cpu_start = time.thread_time()
        renderer.render(c, 1280, 720, pose); image.flush()
        cpu.append((time.thread_time() - cpu_start) * 1000.)
        wall.append((time.perf_counter() - start) * 1000.)
    report = dict(platform=platform.platform(), machine=platform.machine(), python=platform.python_version(),
                  cairo=cairo.cairo_version_string(), environment='Host WSL Linux x86_64; not KV260 board',
                  camera_used=False, display_opened=False, new_human_capture=False,
                  source=fixtures['provenance'],
                  size=[1280, 720], measured_frames=len(wall), static_cache_builds=renderer.cache_builds,
                  render_wall=stats(wall), render_thread_cpu=stats(cpu), rig_wall=stats(rig_wall),
                  replay_frames=len(observed), replay_status_counts=status,
                  replay_max_wrist_or_elbow_step_px=max_step,
                  sleeve_opaque_centerline_samples=sleeve_samples,
                  notes=['Render timer excludes background paint and PNG encoding.',
                         'Pose previews hold an existing recorded observation to show converged retargeting.',
                         'Renderer is 2-D: foreshortening and depth cannot be reconstructed from 17 image points.'])
    (args.out / 'benchmark-host.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    suite = unittest.defaultTestLoader.loadTestsFromName('avatar.test_rig')
    log = io.StringIO(); tests = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    (args.out / 'test-rig-host.txt').write_text(log.getvalue(), encoding='utf-8')
    (args.out / 'test-rig-host.json').write_text(json.dumps(dict(tests=tests.testsRun, failures=len(tests.failures), errors=len(tests.errors),
          success=tests.wasSuccessful(), source_fixtures={k: {'source_file':v['source_file'], 'source_index':v['source_index']} for k,v in fixtures['samples'].items()},
          camera_used=False, environment=report['environment']), indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(log.getvalue())
    if not tests.wasSuccessful(): raise SystemExit(1)


if __name__ == '__main__':
    main()
