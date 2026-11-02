"""Complete HUD repaint reference, overlap and painter-order regressions.

No camera, network, GTK window or learned model is used. The native variant
must run on AArch64 with the production helper; Cairo covers fallback targets.
"""
from copy import deepcopy
import math
import os
import unittest
from unittest.mock import patch

try:
    import cairo
    from sword.renderer import Renderer, W, H, clamp
    from sword.test_renderer_cache import RendererCacheTests
except ModuleNotFoundError as error:
    if error.name != 'cairo':
        raise
    cairo = None


if cairo is not None:
    class CompleteHudRenderer(Renderer):
        """Previous global key/full repaint, independent of dirty selection."""
        def _hud(self, context, snap, ui):
            boss = self._boss(snap)
            ratio = clamp(float(boss.get('health', 0)) /
                max(.001, float(boss.get('max_health', 1)))) if boss else -1.
            key = (self.size, int(snap.get('scene', 0)), int(snap.get('score', 0)),
                int(snap.get('combo', 0)), int(snap.get('lives', 5)),
                round(float(snap.get('energy', 0))*50),
                math.ceil(float(snap.get('duration', 120))-float(snap.get('time', 0))),
                int(snap.get('power', 1)), int(snap.get('kills', 0)), int(snap.get('graze', 0)),
                str(snap.get('wave_name', '')), str(snap.get('boss_name', '镇山印')),
                int(snap.get('boss_phase', 0)), round(ratio*50))
            if self.hud_key != key:
                if self.hud_surface is not None:
                    self.blitter.invalidate_source(self.hud_surface)
                if self.hud_surface is None or (
                        self.hud_surface.get_width(), self.hud_surface.get_height()) != self.size:
                    self.hud_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, *self.size)
                painter = cairo.Context(self.hud_surface)
                for rect in self._hud_region_rects():
                    painter.rectangle(*rect)
                painter.clip()
                painter.set_operator(cairo.OPERATOR_CLEAR)
                painter.paint()
                painter.set_operator(cairo.OPERATOR_OVER)
                painter.scale(self.size[0]/W, self.size[1]/H)
                self._painting_hud = True
                try:
                    # Explicit original order; bypass the dirty dispatcher.
                    for paint in (self._hud_score, self._hud_combo, self._hud_wave,
                            self._hud_lives, self._hud_boss, self._hud_energy, self._hud_guide):
                        paint(painter, snap, ui)
                finally:
                    self._painting_hud = False
                self.hud_surface.flush()
                self.hud_key = key
            dx, dy = context.user_to_device(0, 0)
            context.save()
            context.identity_matrix()
            for rect in self._hud_region_rects(dx, dy):
                context.rectangle(*rect)
            context.clip()
            regions = [tuple(rect) for rect in context.copy_clip_rectangle_list()]
            self._paint_tile(context, self.hud_surface, dx, dy, regions=regions)
            context.restore()


@unittest.skipIf(cairo is None, 'Requires Cairo; no X display is necessary')
class DirtyHudTests(unittest.TestCase):
    @staticmethod
    def pixels(surface):
        surface.flush()
        data = bytes(surface.get_data())
        if surface.get_format() == cairo.FORMAT_RGB24:
            return data[0::4]+data[1::4]+data[2::4]
        return data

    @staticmethod
    def snapshots():
        original = RendererCacheTests.scene(0)
        original.update(score=1000, combo=1, graze=1, time=3., energy=.431,
                        entities=[], scene=0, wave_name='竹海纸鸢')
        yield deepcopy(original)
        changes = [('score', 1001), ('combo', 2), ('graze', 3),
            ('entities', [dict(kind='boss', x=.5, y=.2, r=.073,
                               health=43.1, max_health=100)]),
            ('combo', 91), ('graze', 200), ('boss_name', '天门镇山印'),
            ('lives', 0), ('power', 4), ('energy', 1.), ('energy', 0.),
            ('wave_name', '一二三四五六七八九十甲乙丙丁'), ('kills', 987654321),
            ('score', 9876543210123456789), ('combo', 987654321), ('graze', 987654321),
            ('score', 0), ('combo', 0), ('graze', 0), ('kills', 0),
            ('entities', []), ('time', 10.99), ('scene', 2), ('wave_name', ''),
            ('lives', 5), ('power', 1), ('boss_phase', 2)]
        for field, value in changes:
            original[field] = value
            yield deepcopy(original)

    def compare_complete_reference(self, native):
        with patch.dict(os.environ, {'INK_SWORD_DISABLE_NEON': '0' if native else '1'}):
            actual, reference = Renderer(), CompleteHudRenderer()
        if native and not actual.blitter.enabled:
            self.skipTest('Requires compiled AArch64 helper')
        for width, height in ((800, 432), (1024, 471), (1024, 556), (901, 503), (800, 432)):
            for fmt in (cairo.FORMAT_RGB24, cairo.FORMAT_ARGB32):
                first = cairo.ImageSurface(fmt, width, height)
                second = cairo.ImageSurface(fmt, width, height)
                for index, snapshot in enumerate(self.snapshots()):
                    actual.draw(cairo.Context(first), width, height, deepcopy(snapshot))
                    reference.draw(cairo.Context(second), width, height, deepcopy(snapshot))
                    self.assertEqual(self.pixels(first), self.pixels(second),
                                     (native, width, height, fmt, index))

    def test_dirty_components_match_complete_cairo_reference(self):
        self.compare_complete_reference(False)

    def test_dirty_components_match_complete_native_reference(self):
        self.compare_complete_reference(True)

    def test_score_updates_keep_static_guide_and_lives(self):
        renderer = Renderer()
        target = cairo.ImageSurface(cairo.FORMAT_RGB24, 1024, 556)
        snapshot = next(self.snapshots())
        renderer.draw(cairo.Context(target), 1024, 556, snapshot)
        before = renderer.hud_component_redraws[:]
        for index in range(10):
            snapshot['score'] += 1
            renderer.draw(cairo.Context(target), 1024, 556, snapshot)
        self.assertEqual(renderer.hud_component_redraws[0], before[0]+10)
        for component in (1, 2, 3, 4, 5, 6):
            self.assertEqual(renderer.hud_component_redraws[component], before[component])
        self.assertEqual(renderer.hud_surface_allocations, 1)

    def test_combo_damage_also_repaints_intersecting_boss(self):
        renderer = Renderer()
        target = cairo.ImageSurface(cairo.FORMAT_RGB24, 1024, 556)
        snapshot = next(self.snapshots())
        snapshot['entities'] = [dict(kind='boss', x=.5, y=.2, r=.073,
                                    health=50, max_health=100)]
        renderer.draw(cairo.Context(target), 1024, 556, snapshot)
        before = renderer.hud_component_redraws[:]
        snapshot['combo'] += 1
        renderer.draw(cairo.Context(target), 1024, 556, snapshot)
        self.assertEqual(renderer.hud_component_redraws[1], before[1]+1)
        self.assertEqual(renderer.hud_component_redraws[4], before[4]+1)

    def test_exact_meter_values_refresh_only_on_previous_global_cadence(self):
        actual, reference = Renderer(), CompleteHudRenderer()
        first = cairo.ImageSurface(cairo.FORMAT_RGB24, 800, 432)
        second = cairo.ImageSurface(cairo.FORMAT_RGB24, 800, 432)
        snapshot = next(self.snapshots())
        snapshot['entities'] = [dict(kind='boss', x=.5, y=.2, r=.073,
                                    health=43.1, max_health=100)]
        for index in range(4):
            snapshot['energy'] = .431+index*.001
            snapshot['entities'][0]['health'] = 43.1+index*.1
            if index == 2:
                snapshot['score'] += 1
            actual.draw(cairo.Context(first), 800, 432, deepcopy(snapshot))
            reference.draw(cairo.Context(second), 800, 432, deepcopy(snapshot))
            self.assertEqual(self.pixels(first), self.pixels(second), index)

    def test_parallax_batch_keeps_all_tiles_in_original_order(self):
        renderer = Renderer()
        renderer._rebuild(1024, 556)
        target = cairo.ImageSurface(cairo.FORMAT_RGB24, 1024, 556)
        context = cairo.Context(target)
        context.scale(1024/W, 556/H)
        batches = []
        with patch.object(renderer, '_tile_commands',
                          side_effect=lambda c, commands: batches.append(commands[:])):
            renderer._parallax(context, 5.7, 0)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 12)
        for index, (tile, _, _) in enumerate(batches[0][:8]):
            self.assertIs(tile, renderer.cloud_cache[3+(index%3)*2])
        for tile, _, _ in batches[0][8:]:
            self.assertIs(tile, renderer.leaf_surface)


if __name__ == '__main__':
    unittest.main()
