"""Headless Cairo smoke tests; screenshots are generated only on request.

Run with system pycairo: python3 -B -m unittest runner.test_render -v.
"""
import copy
import json
import math
from pathlib import Path
import unittest

try:
    from .render import Renderer, cairo
except (ImportError, OSError) as error:
    Renderer = cairo = None
    CAIRO_ERROR = str(error)
else:
    CAIRO_ERROR = ''
from .engine import RunnerGame


@unittest.skipIf(cairo is None, 'Cairo unavailable: ' + CAIRO_ERROR)
class RenderTests(unittest.TestCase):
    def setUp(self):
        self.renderer = Renderer()
        self.surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 960, 540)
        self.context = cairo.Context(self.surface)

    def test_render_never_mutates_simulation_or_snapshot(self):
        game = RunnerGame(3)
        game.start()
        game.invulnerable_time = 1000.
        for _ in range(600):
            game.update(1 / 60)
        before_game = game.snapshot()
        snapshot = copy.deepcopy(before_game)
        before_snapshot = copy.deepcopy(snapshot)
        self.renderer.render(self.context, snapshot, 960, 540)
        self.renderer.render(self.context, snapshot, 960, 540)
        self.assertEqual(snapshot, before_snapshot)
        self.assertEqual(game.snapshot(), before_game)

    def test_all_states_and_positive_output_sizes(self):
        for width, height in ((960, 540), (1280, 720), (1920, 1080), (1024, 600), (1, 1), (320, 200)):
            output = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
            context = cairo.Context(output)
            for state in ('title', 'running', 'paused', 'over'):
                self.renderer.render(context, {'state': state, 'score': 12345,
                    'distance': 1200., 'coins': 25, 'lives': 2}, width, height)
            self.assertEqual(output.get_width(), width)
            self.assertEqual(output.get_height(), height)

    def test_depth_projection_monotonic_and_collision_footline(self):
        projection = [self.renderer.project(0, z) for z in (-2., 0., .6, 1., 10., 76., 1e20)]
        self.assertTrue(all(all(math.isfinite(v) for v in p) for p in projection))
        self.assertTrue(all(a[1] > b[1] for a, b in zip(projection, projection[1:])))
        self.assertEqual(self.renderer.project(1, 0), (480., 496., 1.))
        self.assertLess(abs(self.renderer.project(1, .6)[1] - 496.), 15.)
        self.assertEqual(self.renderer.project(0, -100), projection[0])

    def test_rgb565_matches_only_a_16_bit_visual_and_resets_buffers(self):
        self.renderer.configure_depth(16)
        self.renderer.render(self.context, {'state':'running'},960,540)
        self.assertEqual(self.renderer.frame.get_format(),cairo.FORMAT_RGB16_565)
        output=cairo.ImageSurface(cairo.FORMAT_RGB24,1280,720)
        self.renderer.render(cairo.Context(output),{'state':'running'},1280,720)
        self.assertEqual(self.renderer.output_buffer.get_format(),cairo.FORMAT_RGB16_565)
        self.renderer.configure_depth(24)
        self.assertIsNone(self.renderer.frame)
        self.assertIsNone(self.renderer.output_buffer)
        self.renderer.render(self.context,{'state':'running'},960,540)
        self.assertEqual(self.renderer.frame.get_format(),cairo.FORMAT_RGB24)

    def test_fx_and_text_lru_cache_bounds(self):
        for i in range(1500):
            self.renderer._text(self.context, '分数 %d' % i, 0, 0, 18)
            self.renderer.event({'kind': 'collect', 'lane': i % 3})
        metrics = self.renderer.metrics()
        self.assertLessEqual(metrics['text_cache'], 100)
        self.assertLessEqual(metrics['fx_count'], 40)
        self.renderer.update_effects(.7)
        self.assertEqual(len(self.renderer.fx), 0)

    def test_sprite_quantization_cache_and_native_release(self):
        kinds = tuple(self.renderer.sprites)
        for kind in kinds:
            for i in range(120):
                self.renderer.sprite(self.context, kind, -300, -300, i / 100)
        self.assertLessEqual(len(self.renderer.scaled), len(kinds) * 26)
        self.renderer.render(self.context, {'state': 'running'}, 960, 540)
        self.assertIsNotNone(self.renderer.frame)
        self.renderer.release_native_output()
        self.assertIsNone(self.renderer.frame)
        self.assertEqual(len(self.renderer.native), 0)
        self.renderer.render(self.context, {'state': 'running'}, 960, 540)

    def test_dense_entities_and_effects(self):
        entities = [{'id': i, 'kind': ('cart', 'barrier', 'arch', 'coin', 'shield', 'magnet')[i % 6],
                     'lane': i % 3, 'z': (i // 3) * 1.4 - 2.0} for i in range(160)]
        for kind in ('collect', 'dodge', 'hit', 'shield_break'):
            for lane in range(3):
                self.renderer.event({'kind': kind, 'lane': lane})
        for height in (0., .2, 1.):
            self.renderer.render(self.context, {'state': 'running', 'entities': entities,
                'jump_height': height, 'shield_time': 10., 'magnet_time': 10.}, 960, 540)
        self.assertLessEqual(len(self.renderer.scaled), 26 * len(self.renderer.sprites))


def make_previews(destination):
    """Write title, actual 60-second snapshot, dense view and collision markers."""
    if cairo is None:
        raise RuntimeError(CAIRO_ERROR)
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    renderer = Renderer()
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 960, 540)
    context = cairo.Context(surface)
    title = RunnerGame(17)
    renderer.render(context, title.snapshot(), 960, 540)
    surface.write_to_png(str(directory / 'title-960x540.png'))
    game = RunnerGame(17)
    game.start()
    game.invulnerable_time = 9999.
    for _ in range(3600):
        game.update(1 / 60)
    snapshot = game.snapshot()
    snapshot['ui'] = {'notice': '预览检查 · 运行 60 秒'}
    renderer.render(context, snapshot, 960, 540)
    surface.write_to_png(str(directory / 'running-60s-960x540.png'))
    snapshot['entities'] = [{'id': i, 'kind': ('cart', 'barrier', 'arch', 'coin', 'shield', 'magnet')[i % 6],
                            'lane': i % 3, 'z': 3.5 + (i // 3) * 2.7}
                           for i in range(60)]
    snapshot['ui'] = {'notice': '密集绘制检查 · 人为摆放 60 个实体'}
    snapshot['jump_height'] = .75
    snapshot['shield_time'] = snapshot['magnet_time'] = 8.
    for i in range(40):
        renderer.event({'kind': ('collect', 'hit', 'dodge')[i % 3], 'lane': i % 3})
    renderer.update_effects(.20)
    renderer.render(context, snapshot, 960, 540)
    surface.write_to_png(str(directory / 'dense-60-entities-960x540.png'))
    renderer.fx.clear()
    snapshot['ui'] = {'notice': '碰撞投影检查：蓝线 z=0，橙线 z=0.6'}
    snapshot['jump_height'] = 0.
    snapshot['shield_time'] = snapshot['magnet_time'] = 0.
    snapshot['entities'] = [dict(id=i, kind=kind, lane=i, z=0.)
                            for i, kind in enumerate(('barrier', 'coin', 'arch'))]
    renderer.render(context, snapshot, 960, 540)
    for z, rgb in ((0., (.1, .5, 1.)), (.6, (1., .3, .1))):
        y = renderer.project(1, z)[1]
        context.set_source_rgb(*rgb)
        context.set_line_width(1.5)
        context.move_to(190, y)
        context.line_to(770, y)
        context.stroke()
    surface.write_to_png(str(directory / 'collision-footline-960x540.png'))
    (directory / 'render-metrics.json').write_text(json.dumps(renderer.metrics(), indent=2), encoding='utf-8')
    return renderer.metrics()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--previews')
    args, remaining = parser.parse_known_args()
    if args.previews:
        print(json.dumps(make_previews(args.previews), ensure_ascii=False))
    else:
        unittest.main(argv=['test_render'] + remaining)
