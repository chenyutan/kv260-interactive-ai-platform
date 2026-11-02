"""Native Cairo renderer invariants; run with unittest or pytest on the board."""
from __future__ import annotations
import copy
import io
import math
import os
import random
import sys
import time
import unittest
from unittest.mock import patch

from . import art

try:
    from . import render as rendering
except (ImportError, OSError):
    rendering = None


class PixelArtTests(unittest.TestCase):
    def test_original_sprite_dimensions_and_palette(self):
        for state in ('idle', 'running', 'jumping', 'hurt', 'dead'):
            for frame in range(4):
                for face in (-1, 1):
                    rows = art.player(frame, state, face, True)
                    self.assertEqual((len(rows), len(rows[0])), (24, 16))
                    self.assertTrue(all(len(row) == 16 for row in rows))
                    self.assertTrue(set(''.join(rows)) <= set(art.PALETTE))
        self.assertNotEqual(art.player(0, 'running'), art.player(1, 'running'))
        self.assertEqual(art.player(facing=-1), tuple(row[::-1] for row in art.player()))


@unittest.skipIf(rendering is None, 'Native Cairo/cairocffi unavailable; render verification runs on board')
class RendererTests(unittest.TestCase):
    def setUp(self):
        self.r = rendering.Renderer()

    def surface(self, width=960, height=540):
        return rendering.cairo.ImageSurface(rendering.cairo.FORMAT_ARGB32, width, height)

    @staticmethod
    def pixel(surface, x, y):
        surface.flush()
        data = bytes(surface.get_data())
        offset = y * surface.get_stride() + x * 4
        # RGB24's fourth byte is unused; compare the actual color channels to
        # ARGB32 targets without depending on that padding byte.
        return data[offset:offset + 3]

    def test_opaque_framebuffer_and_presentation_restore_target_state(self):
        self.assertEqual(self.r.buffer.get_format(), rendering.cairo.FORMAT_RGB24)
        target = self.surface()
        cr = rendering.cairo.Context(target)
        cr.set_operator(rendering.cairo.OPERATOR_OVER)
        before = tuple(cr.get_matrix())
        self.r.render(cr, rendering.fixture(dense=True), 960, 540)
        self.assertEqual(cr.get_operator(), rendering.cairo.OPERATOR_OVER)
        self.assertEqual(tuple(cr.get_matrix()), before)
        target.flush()
        data = bytes(target.get_data())
        for x, y in ((0, 0), (959, 539), (153, 96), (237, 441)):
            self.assertEqual(data[y * target.get_stride() + x * 4 + 3], 255)

    def test_bounded_sprite_blit_preserves_transparency_and_clipping(self):
        sprite = self.r.sprites[('player', 'running', 0, 1, False)]
        for x, y in ((7, 9), (-4, -3), (310, 170)):
            expected, actual = self.surface(320, 180), self.surface(320, 180)
            for target, bounded in ((expected, False), (actual, True)):
                cr = rendering.cairo.Context(target)
                cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
                cr.set_source_rgb(.2, .3, .4); cr.paint()
                cr.rectangle(2, 1, 314, 176); cr.clip()
                if bounded:
                    self.r._blit(cr, sprite, x, y)
                else:
                    cr.set_source_surface(sprite, x, y)
                    cr.get_source().set_filter(rendering.cairo.FILTER_NEAREST)
                    cr.paint()
                target.flush()
            self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()))

    def test_particle_batch_preserves_overlapping_color_order_and_bounds(self):
        particles = [{'x': 18.7, 'y': 9.2, 'kind': 'spark'},
                     {'x': 19.4, 'y': 9.9, 'kind': 'coin'},
                     {'x': 19.1, 'y': 10.6, 'kind': 'leaf'},
                     {'x': 20.9, 'y': 10.2, 'kind': 'stomp'},
                     {'x': 329.1, 'y': 179.9, 'kind': 'spark'},
                     {'x': 9.1, 'y': -1., 'kind': 'spark'}]
        particles.extend({'x': 20 + i, 'y': 35, 'kind': 'spark'} for i in range(80))
        expected, actual = self.surface(320, 180), self.surface(320, 180)
        camera = 10
        for target, batched in ((expected, False), (actual, True)):
            cr = rendering.cairo.Context(target)
            cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
            cr.set_source_rgb(.2, .3, .4); cr.paint()
            if batched:
                count = self.r._particles(cr, particles, camera)
            else:
                for particle in particles[:self.r.MAX_PARTICLES]:
                    x, y = particle['x'] - camera, particle['y']
                    if 0 <= x < 320 and 0 <= y < 180:
                        color = '#fff0a1' if particle['kind'] in ('spark', 'coin', 'stomp') else '#73c5a0'
                        rendering._rect(cr, x, y, 2, 2, color)
            target.flush()
        self.assertEqual(count, 63)
        self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()))

    def test_bounded_portal_blit_matches_legacy_transforms_clips_and_updates(self):
        sprite = self.r.sprites['portal']
        cases = ((0, 0, 1, 7, 9), (31, 88, 1, 0, 0),
                 (7.5, 4.25, .75, 13, 17), (24, 32, 2, -4, -3), (31, 88, 1, 0, 0))
        for index, (tx, ty, scale, x, y) in enumerate(cases):
            if index == len(cases) - 1:
                changed = rendering.cairo.Context(sprite)
                changed.set_source_rgba(.51, .12, .03, .75)
                changed.rectangle(7, 10, 5, 5); changed.fill()
            expected, actual = self.surface(320, 180), self.surface(320, 180)
            for target, bounded in ((expected, False), (actual, True)):
                cr = rendering.cairo.Context(target)
                cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
                cr.set_source_rgb(.2, .3, .4); cr.paint()
                cr.rectangle(5, 6, 303, 165); cr.clip()
                cr.translate(tx, ty); cr.scale(scale, scale)
                if bounded:
                    self.r._blit(cr, sprite, x, y)
                else:
                    cr.set_source_surface(sprite, int(x), int(y))
                    cr.get_source().set_filter(rendering.cairo.FILTER_NEAREST)
                    cr.paint()
                target.flush()
            self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()))

    def test_dynamic_blits_update_pixels_without_creating_cache_entries(self):
        before = self.r.stats()
        dynamic = self.surface(16, 16)
        for index in range(10):
            changed = rendering.cairo.Context(dynamic)
            changed.set_operator(rendering.cairo.OPERATOR_SOURCE)
            changed.set_source_rgba(index / 10, .2, .4, .7); changed.paint()
            expected, actual = self.surface(320, 180), self.surface(320, 180)
            x, y = index - 4, index + .75
            for target, bounded in ((expected, False), (actual, True)):
                cr = rendering.cairo.Context(target)
                cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
                cr.set_source_rgb(.2, .3, .4); cr.paint()
                cr.rectangle(2, 1, 314, 176); cr.clip()
                if bounded:
                    self.r._blit(cr, dynamic, x, y)
                else:
                    cr.set_source_surface(dynamic, int(x), int(y))
                    cr.get_source().set_filter(rendering.cairo.FILTER_NEAREST); cr.paint()
                target.flush()
            self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()))
        after = self.r.stats()
        self.assertEqual(after['cache_entries'], before['cache_entries'])
        self.assertEqual(after['cached_surface_bytes'], before['cached_surface_bytes'])
        self.assertNotIn(dynamic, self.r.__dict__.values())

    def test_backdrop_cache_matches_original_layers_and_integer_offsets(self):
        for theme in ('bamboo', 'temple', 'moon'):
            for camera in tuple(range(25)) + (50, 639, 1441, 2909, 6400):
                expected = self.surface(320, 180)
                cr = rendering.cairo.Context(expected)
                cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
                sky, far, clouds, mid = self.r._background(theme)
                cr.set_operator(rendering.cairo.OPERATOR_SOURCE); self.r._blit(cr, sky, 0, 0)
                cr.set_operator(rendering.cairo.OPERATOR_OVER)
                self.r._wrap(cr, far, camera, .22); self.r._wrap(cr, clouds, camera, .10)
                self.r._wrap(cr, mid, camera, .46)
                cached = self.r._backdrop(theme, camera)
                for x, y in ((0, 0), (51, 47), (122, 102), (79, 147), (319, 179)):
                    self.assertEqual(self.pixel(cached, x, y), self.pixel(expected, x, y))
                expected.flush(); cached.flush()
                # RGB24 padding has no visible meaning; compare every RGB byte.
                self.assertEqual(bytes(cached.get_data())[0::4], bytes(expected.get_data())[0::4])
                self.assertEqual(bytes(cached.get_data())[1::4], bytes(expected.get_data())[1::4])
                self.assertEqual(bytes(cached.get_data())[2::4], bytes(expected.get_data())[2::4])
                self.assertLessEqual(len(self.r.backdrops.data), 8)
                self.assertLessEqual(len(self.r.underlays.data), 8)
        first = self.r._backdrop('moon', 0)
        before = self.r.backdrops.builds
        self.assertIs(self.r._backdrop('moon', 1), first)
        self.assertEqual(self.r.backdrops.builds, before)
        self.assertIsNot(self.r._backdrop('moon', 3), first)

    def test_underlay_reuses_far_cloud_offsets_and_reports_bounded_cost(self):
        first = self.r._underlay('moon', 0)
        backdrop = self.r._backdrop('moon', 0)
        self.assertIs(self.r._underlay('moon', 3), first)
        self.assertIsNot(self.r._backdrop('moon', 3), backdrop)
        self.assertEqual(self.r.underlays.builds, 1)
        self.assertEqual(self.r.backdrops.builds, 2)
        self.assertGreaterEqual(self.r.underlays.hits, 3)
        self.assertIsNot(self.r._underlay('moon', 5), first)
        self.assertIsNot(self.r._underlay('bamboo', 0), first)
        for camera in range(60):
            self.r._backdrop('temple', camera)
        stats = self.r.stats()
        self.assertLessEqual(stats['cache_entries']['underlays'], 8)
        self.assertEqual(stats['underlay_cache_bytes'], stats['cache_entries']['underlays'] * 320 * 180 * 4)
        self.assertEqual(stats['cache_limits']['underlays'], 8)
        self.assertGreater(stats['underlay_build_ms']['count'], 0)
        self.assertGreater(stats['underlay_build_ms']['maximum'], 0)
        self.assertGreater(stats['backdrop_build_ms']['count'], 0)

    def test_direct_backdrop_matches_cached_rgb_during_movement_reversal_and_stops(self):
        reference = rendering.Renderer()
        actual = rendering.cairo.ImageSurface(rendering.cairo.FORMAT_RGB24, 320, 180)
        cr = rendering.cairo.Context(actual)
        cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
        cameras = (0, 1, 3, 5, 6, 9, 193, 256, 511, 640, 1441, 2909, 6400,
                   640, 511, 256, 193, 9, 6, 5, 3, 1, 0, 0, 0, 17.25, -17.25, -17.25)
        for theme in ('bamboo', 'moon', 'temple', 'bamboo'):
            for camera in cameras:
                self.r._paint_backdrop(cr, theme, camera)
                expected = reference._backdrop(theme, camera)
                actual.flush(); expected.flush()
                for channel in range(3):
                    self.assertEqual(bytes(actual.get_data())[channel::4],
                                     bytes(expected.get_data())[channel::4], (theme, camera, channel))
                self.assertLessEqual(len(self.r.backdrops.data), 8)
                self.assertLessEqual(len(self.r.underlays.data), 8)

    def test_direct_backdrop_builds_on_second_equal_key_and_reuses_cached_positions(self):
        target = rendering.cairo.ImageSurface(rendering.cairo.FORMAT_RGB24, 320, 180)
        cr = rendering.cairo.Context(target); cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
        self.r._paint_backdrop(cr, 'moon', 0)
        self.assertEqual(self.r.backdrop_direct_composites, 1)
        self.assertEqual(self.r.backdrops.builds, 0)
        self.assertEqual(len(self.r.backdrops.data), 0)
        self.r._paint_backdrop(cr, 'moon', 1)  # Same integer offsets as camera 0.
        self.assertEqual(self.r.backdrops.builds, 1)
        self.r._paint_backdrop(cr, 'moon', 1)
        self.assertEqual(self.r.backdrops.hits, 1)
        self.r._paint_backdrop(cr, 'moon', 3)
        self.assertEqual(self.r.backdrop_direct_composites, 2)
        self.assertEqual(self.r.backdrops.builds, 1)
        self.r._paint_backdrop(cr, 'moon', 3)
        self.assertEqual(self.r.backdrops.builds, 2)
        self.r._paint_backdrop(cr, 'moon', 0)  # Reversal finds an earlier cached key.
        self.assertEqual(self.r.backdrops.hits, 2)
        for camera in range(20, 180, 10):
            self.r._paint_backdrop(cr, 'temple', camera)
            self.r._paint_backdrop(cr, 'temple', camera)
        stats = self.r.stats()
        self.assertEqual(stats['cache_entries']['backdrops'], 8)
        self.assertEqual(stats['cache_limits']['backdrops'], 8)
        self.assertEqual(stats['backdrop_direct_composites'], 18)
        self.assertEqual(stats['backdrop_builds'], 18)
        self.assertEqual(stats['backdrop_hits'], 2)

    def test_direct_backdrop_full_render_matches_cached_path_at_fractional_outputs(self):
        class CachedBackdrop(rendering.Renderer):
            def _paint_backdrop(self, cr, theme, camera):
                cr.set_operator(rendering.cairo.OPERATOR_SOURCE)
                self._blit(cr, self._backdrop(theme, camera), 0, 0)
        reference = CachedBackdrop()
        scene = rendering.fixture(dense=True)
        sequence = (('bamboo', 0), ('bamboo', 3), ('bamboo', 3), ('bamboo', 5),
                    ('bamboo', 1), ('moon', 640), ('moon', 640), ('temple', 193))
        for frame, (theme, camera) in enumerate(sequence):
            scene['stage']['theme'] = theme
            scene['camera_x'] = camera; scene['elapsed'] = frame / 60
            for width, height in ((960, 540), (853, 479), (641, 481)):
                pixels = []
                for renderer in (self.r, reference):
                    target = self.surface(width, height)
                    cr = rendering.cairo.Context(target)
                    cr.set_source_rgb(.17, .29, .43); cr.paint()
                    cr.rectangle(2, 1, width - 4, height - 2); cr.clip()
                    renderer.render(cr, scene, width, height)
                    target.flush(); pixels.append(bytes(target.get_data()))
                self.assertEqual(pixels[0], pixels[1], (theme, camera, width, height))
        self.assertGreater(self.r.backdrop_direct_composites, 0)
        self.assertGreater(self.r.backdrops.hits, 0)

    def test_draw_never_advances_or_mutates_snapshot(self):
        scene = rendering.fixture(dense=True)
        original = copy.deepcopy(scene)
        target = self.surface()
        self.r.render(rendering.cairo.Context(target), scene, 960, 540)
        self.assertEqual(scene, original)
        self.assertEqual(self.r.last_layout['internal'], [320, 180])

    def test_nearest_threefold_pixels_and_letterbox(self):
        target = self.surface()
        self.r.render(rendering.cairo.Context(target), rendering.fixture(), 960, 540)
        for x, y in ((51, 32), (122, 102), (79, 147), (250, 162)):
            reference = self.pixel(self.r.buffer, x, y)
            for dx, dy in ((0, 0), (1, 1), (2, 2)):
                self.assertEqual(reference, self.pixel(target, x * 3 + dx, y * 3 + dy))
        tall = self.surface(960, 720)
        self.r.render(rendering.cairo.Context(tall), rendering.fixture(), 960, 720)
        self.assertEqual(self.r.last_layout['content'], [0.0, 90.0, 960.0, 540.0])
        self.assertEqual(self.pixel(tall, 0, 0), self.pixel(tall, 959, 719))
        self.assertNotEqual(self.pixel(tall, 0, 0), self.pixel(tall, 80, 100))

    def test_static_frame_reuses_terrain_hud_and_internal_surface(self):
        scene = rendering.fixture(); target = self.surface(); cr = rendering.cairo.Context(target)
        self.r.render(cr, scene, 960, 540)
        before = self.r.stats(); identity = id(self.r.buffer)
        for i in range(10):
            scene['elapsed'] = i / 60
            self.r.render(cr, scene, 960, 540)
        after = self.r.stats()
        self.assertEqual(before['chunk_builds'], after['chunk_builds'])
        self.assertEqual(before['hud_builds'], after['hud_builds'])
        self.assertEqual(before['text_builds'], after['text_builds'])
        self.assertEqual(id(self.r.buffer), identity)
        self.assertGreater(after['chunk_hits'], before['chunk_hits'])
        small = self.surface(640, 480)
        self.r.render(rendering.cairo.Context(small), scene, 640, 480)
        self.assertEqual(id(self.r.buffer), identity)

    def test_changed_chest_rebuilds_only_changed_chunk(self):
        scene = rendering.fixture(); target = self.surface(); cr = rendering.cairo.Context(target)
        self.r.render(cr, scene, 960, 540)
        builds = self.r.stats()['chunk_builds']
        next(tile for tile in scene['tiles'] if tile['kind'] == 'question')['kind'] = 'used'
        self.r.render(cr, scene, 960, 540)
        self.assertEqual(self.r.stats()['chunk_builds'], builds + 1)

    def test_all_actual_level_holes_show_dark_depth_at_exact_solid_boundaries(self):
        from .engine import Game
        from .levels import LEVELS

        # The visual hazard must agree with actual solids, including the castle
        # hall hole. Do not use an independent handwritten list of pit widths.
        checked = 0
        for index, level in enumerate(LEVELS):
            game = Game(); game.start(0); game._load_level(index); game.state = 'playing'
            scene = game.snapshot()
            scene.update(player={}, enemies=[], collectibles=[], particles=[])
            solids = sorted((int(t['x']), int(t['x']+t['w'])) for t in scene['tiles']
                            if t['y'] == 160 and t['h'] >= 16)
            gaps = [(left[1], right[0]) for left, right in zip(solids, solids[1:])
                    if left[1] < right[0]]
            for left, right in gaps:
                for camera in (left-144, left-145, left-17):
                    scene['camera_x'] = camera
                    original = copy.deepcopy(scene)
                    target = self.surface(320, 180)
                    self.r.render(rendering.cairo.Context(target), scene, 320, 180, draw_text=False)
                    self.assertEqual(scene, original)
                    for world_x in range(left, right):
                        for y in (160, 166, 179):
                            blue, green, red = self.pixel(target, world_x-camera, y)
                            # These are void pixels, not grass/rock or scenery.
                            self.assertLess(max(red, green, blue), 80)
                    for boundary_x in (left-1, right):
                        self.assertNotEqual(self.pixel(target, boundary_x-camera, 160),
                                            self.pixel(target, left-camera, 160))
                    checked += 1
        self.assertEqual(checked, 27)  # Nine genuine gaps, at three camera offsets.

    def test_parallax_cannot_draw_a_false_floor_or_bridge_across_a_hole(self):
        for theme in rendering.THEMES:
            palette = rendering.THEMES[theme]
            expected_haze = bytes(round(v*255) for v in reversed(rendering._rgb(palette['haze'])))
            expected_depth = bytes(round(v*255) for v in reversed(rendering._rgb(palette['abyss'])))
            for camera in (0, 304, 511, 720):
                frames = []
                # First pass scrolls/direct-composites; repeated passes use the
                # warmed backdrop. Both paths must preserve the empty gap.
                for repeat in range(3):
                    target = self.surface(320, 180)
                    self.r._paint_backdrop(rendering.cairo.Context(target), theme, camera)
                    for x in range(320):
                        self.assertEqual(self.pixel(target, x, 155), expected_haze)
                        self.assertEqual(self.pixel(target, x, 160), expected_depth)
                        self.assertEqual(self.pixel(target, x, 179), expected_depth)
                    target.flush(); frames.append(bytes(target.get_data()))
                self.assertEqual(frames[0], frames[1])
                self.assertEqual(frames[1], frames[2])

    def test_hazard_separation_adds_no_per_frame_background_cache_work(self):
        scene = rendering.fixture(); scene['camera_x'] = 304
        target = self.surface(); cr = rendering.cairo.Context(target)
        self.r.render(cr, scene, 960, 540)
        self.r.render(cr, scene, 960, 540)
        before = self.r.stats()
        for frame in range(60):
            scene['elapsed'] = frame/60
            self.r.render(cr, scene, 960, 540)
        after = self.r.stats()
        for key in ('underlay_builds', 'backdrop_builds', 'chunk_builds'):
            self.assertEqual(before[key], after[key])

    def test_terrain_partition_matches_legacy_pixels_counts_and_live_edits(self):
        class LegacyTerrain(rendering.Renderer):
            def _terrain_partitions(self, tiles, start, end):
                result = []
                for index in range(start, end + 1):
                    left = index * self.CHUNK
                    members = [tile for tile in tiles if float(tile['x']) < left + self.CHUNK
                               and float(tile['x']) + float(tile.get('w', rendering.TILE)) > left]
                    result.append([(int(t['x']), int(t['y']), int(t.get('w', rendering.TILE)),
                                    int(t.get('h', rendering.TILE)), str(t.get('kind', 'ground')))
                                   for t in members])
                return result
        reference = LegacyTerrain()
        scene = {'level_index': 0, 'tiles': [
            {'x': -17.7, 'y': 144, 'w': 800.6, 'h': 32, 'kind': 'ground'},
            {'x': 255.9, 'y': 112, 'w': .2, 'h': 16, 'kind': 'question'},
            {'x': 256., 'y': 112, 'w': 16, 'h': 16, 'kind': 'brick'},
            {'x': -.25, 'y': 96, 'w': .5, 'h': 16, 'kind': 'brick'},
            {'x': 300.2, 'y': 80, 'w': -1.1, 'h': 16, 'kind': 'ground'},
            {'x': 330.8, 'y': 80, 'w': 0, 'h': 16, 'kind': 'ground'},
            {'x': 512., 'y': 112, 'w': 32, 'h': 16, 'kind': 'used'},
            {'x': 800.3, 'y': 128, 'w': 64.9, 'h': 16, 'kind': 'question'}]}
        for edit in range(3):
            if edit == 1:
                scene['tiles'][0].update(x=-21.8, w=520.2, kind='brick')
                scene['tiles'][1]['kind'] = 'used'
            elif edit == 2:
                del scene['tiles'][2]
                scene['tiles'].append({'x': 510.4, 'y': 96, 'w': 301.7, 'h': 16, 'kind': 'brick'})
            for camera in (0, 193, 250, 256, 511, -257, -17, 1023):
                start, end = int(camera) // self.r.CHUNK, (int(camera) + 319) // self.r.CHUNK
                self.assertEqual(self.r._terrain_partitions(scene['tiles'], start, end),
                                 reference._terrain_partitions(scene['tiles'], start, end))
                targets, counts = [], []
                for renderer in (self.r, reference):
                    target = self.surface(320, 180); cr = rendering.cairo.Context(target)
                    cr.set_antialias(rendering.cairo.ANTIALIAS_NONE)
                    cr.set_source_rgb(.2, .3, .4); cr.paint()
                    counts.append(renderer._terrain(cr, scene, camera))
                    target.flush(); targets.append(bytes(target.get_data()))
                self.assertEqual(counts[0], counts[1])
                self.assertEqual(targets[0], targets[1])
                self.assertEqual(list(self.r.chunks.data), list(reference.chunks.data))

    def test_caches_and_effects_are_bounded_under_moving_dense_scene(self):
        scene = rendering.fixture(dense=True)
        scene['particles'] *= 20
        target = self.surface(320, 180); cr = rendering.cairo.Context(target)
        for i in range(60):
            scene['camera_x'] = i * 50
            scene['coin_count'] = i; scene['score'] = i * 200
            scene['level_index'] = i % 3
            scene['stage']['theme'] = ('bamboo', 'temple', 'moon')[i % 3]
            self.r.render(cr, scene, 320, 180)
        stats = self.r.stats()
        for key, bound in (('chunks', 16), ('text', 64), ('hud', 8), ('overlay', 12), ('backdrops', 8), ('underlays', 8)):
            self.assertLessEqual(stats['cache_entries'][key], bound)
        self.assertLessEqual(stats['last_counts']['particles'], 64)
        self.assertLess(stats['cached_surface_bytes'], 12 * 1024 * 1024)
        self.assertLessEqual(stats['timing_samples'], 240)

    def test_engine_states_ui_notice_and_ready_menu(self):
        target = self.surface(320, 180); cr = rendering.cairo.Context(target)
        for state in ('countdown', 'playing', 'paused', 'over', 'won'):
            self.r.render(cr, rendering.fixture(state=state), 320, 180)
        scene = rendering.fixture(state='countdown')
        scene['ui'] = {'ready': True, 'tracking': True}
        overlay = self.r._overlay(scene)
        self.assertIsNotNone(overlay)
        scene['state'] = 'playing'; scene['ui'] = {'ready': False, 'tracking': False}
        self.r.render(cr, scene, 320, 180)
        for kind in ('pipe', 'respawn', 'stage'):
            scene['state'] = 'transition'; scene['transition_kind'] = kind
            self.r.render(cr, scene, 320, 180)

    def test_corner_joystick_feedback_is_finite_and_follows_accepted_move(self):
        scene = rendering.fixture()
        scene['ui'] = {'control': dict(calibration='ready', valid=True, move=-1, jump_ready=True,
                                     joystick_dx=-.32, joystick_dy=0.)}
        info = self.r._control_info(scene)
        self.assertEqual((info['move'], info['primary'], info['secondary']),
                         (-1, '右手 左', '左手已准备'))
        scene['ui']['control'].update(move=1, jump_ready=False)
        info = self.r._control_info(scene)
        self.assertEqual((info['move'], info['primary'], info['secondary']),
                         (1, '右手 右', '放低左手准备'))
        for calibration in ('waiting', 'calibrating', 'frozen', 'body_changed'):
            scene['ui']['control'].update(calibration=calibration, calibration_progress=.5)
            info = self.r._control_info(scene)
            self.assertEqual(info['move'], 0)
            self.assertFalse(info['ready'])
            self.assertTrue(info['primary'])
        for move in (float('nan'), float('inf'), 'bad', None):
            scene['ui']['control'].update(calibration='ready', valid=True, move=move,
                                          calibration_progress=float('nan'))
            info = self.r._control_info(scene)
            self.assertEqual(info['move'], 0)
            self.assertEqual(info['progress'], 0)
        scene['ui']['control'].update(valid=False, move=1)
        self.assertEqual(self.r._control_info(scene)['move'], 0)

    def test_gameplay_control_changes_do_not_draw_tutorial_panels(self):
        scene = rendering.fixture()
        scene['ui'] = {'control': dict(calibration='ready', valid=True, move=-1, jump_ready=True,
                                     joystick_dx=-.32, joystick_dy=0.)}
        outputs = []
        for move in (-1, 0, 1):
            scene['ui']['control']['move'] = move
            target = self.surface(320, 180)
            self.r.render(rendering.cairo.Context(target), scene, 320, 180, draw_text=False)
            target.flush(); outputs.append(bytes(target.get_data()))
        stride = 320 * 4
        self.assertEqual(outputs[0], outputs[2])
        for y in range(180):
            for x in range(320):
                if not (6 <= x < 108 and 28 <= y < 59):
                    offset = y * stride + x * 4
                    self.assertEqual(outputs[0][offset:offset + 3], outputs[2][offset:offset + 3])
        self.assertEqual(self.r.huds.builds, 1)
        self.assertEqual(len(self.r.overlays.data), 0)

    def test_geometry_freeze_and_missing_right_have_accurate_independent_feedback(self):
        scene = rendering.fixture()
        control = dict(calibration='frozen', valid=True, move=0, jump_ready=False,
                       joystick_dx=0., joystick_dy=0., reason='body_frozen')
        scene['ui'] = {'control': control}
        info = self.r._control_info(scene)
        self.assertEqual(info['primary'], '面向画面恢复站姿')
        self.assertFalse(info['ready'])
        control.update(calibration='ready', reason='missing_right_arm',
                       joystick_dx=None, joystick_dy=None, jump_ready=True)
        info = self.r._control_info(scene)
        self.assertFalse(info['ready'])
        self.assertEqual(info['primary'], '等待右手入镜')
        self.assertEqual(info['secondary'], '左手已准备')
        control.update(reason='missing_left_arm', joystick_dx=0., joystick_dy=0.,
                       jump_ready=False)
        info = self.r._control_info(scene)
        self.assertTrue(info['ready'])
        self.assertEqual(info['secondary'], '等待左手入镜')

    def test_gameplay_never_reintroduces_instructions_from_old_snapshots(self):
        scene = rendering.fixture()
        scene['stage']['hint'] = '左手过肩跳跃 · 右手回中停步'
        for mode in ('body', 'gamepad'):
            for context in (False, 'pipe'):
                scene['ui'] = {'control_mode': mode, 'confirm_context': context}
                before = copy.deepcopy(scene)
                self.assertEqual(self.r._route_hint(scene), '')
                runs = self.r._ui_text_runs(scene, 'bamboo')
                self.assertEqual(len(runs), 4)  # Essential game HUD only.
                self.assertFalse(any('跳跃' in run[0] or '下压' in run[0] for run in runs))
                self.assertEqual(scene, before)


@unittest.skipIf(rendering is None, 'Native Cairo/cairocffi unavailable')
class NativeTypographyTests(unittest.TestCase):
    def setUp(self):
        self.r = rendering.Renderer()

    @staticmethod
    def surface(width=960, height=540):
        return rendering.cairo.ImageSurface(rendering.cairo.FORMAT_ARGB32, width, height)

    def draw(self, scene, width=960, height=540, draw_text=True, renderer=None):
        target = self.surface(width, height)
        (renderer or self.r).render(rendering.cairo.Context(target), scene, width, height, draw_text=draw_text)
        target.flush()
        return target

    def test_title_entry_feedback_uses_existing_fourth_line_without_layout_changes(self):
        scene=rendering.fixture(state='menu')
        baseline=self.r._ui_text_runs(scene,'bamboo')
        for notice in ('3秒后可启程，请准备入镜','右手已下压，正在确认','左手臂未识别，请完整入镜'):
            scene['ui']={'ready':True,'notice':notice}
            runs=self.r._ui_text_runs(scene,'bamboo')
            self.assertEqual(len(runs),len(baseline))
            changed=[(old,new) for old,new in zip(baseline,runs) if old!=new]
            self.assertEqual(len(changed),1)
            old,new=changed[0]
            self.assertEqual(new[0],notice)
            self.assertEqual(new[1:],old[1:])
            self.assertEqual(new[1:5],(40,117,240,16))
            self.draw(scene)
            found=next(run for run in self.r.last_text_layout if run['text']==notice)
            self.assertGreater(found['ink_box'][2],0)

    @staticmethod
    def legacy_normalize(data, width, height, stride, rgb, byteorder):
        opaque = bytes((rgb[2], rgb[1], rgb[0], 255) if byteorder == 'little'
                       else (255, rgb[0], rgb[1], rgb[2]))
        alpha_index = 3 if byteorder == 'little' else 0
        left, top, right, bottom = width, height, -1, -1
        for yy in range(height):
            for xx in range(width):
                offset = yy * stride + xx * 4
                if data[offset + alpha_index] >= 128:
                    data[offset:offset + 4] = opaque
                    left, top, right, bottom = min(left, xx), min(top, yy), max(right, xx), max(bottom, yy)
                else:
                    data[offset:offset + 4] = b'\0\0\0\0'
        return left, top, right, bottom

    def test_binary_mask_fast_and_fallback_match_legacy_bytes_bounds_and_padding(self):
        if rendering._PillowImage is None:
            self.skipTest('Pillow unavailable; C-path equivalence requires Pillow')
        rng = random.Random(42790)
        for byteorder in ('little', 'big'):
            for width, height in ((1, 1), (7, 3), (32, 17), (361, 52)):
                for padding in (0, 4, 12):
                    stride = width * 4 + padding
                    for mode in ('empty', 'full', 'threshold', 'random'):
                        raw = bytearray(rng.getrandbits(8) for unused in range(stride * height + 11))
                        alpha_index = 3 if byteorder == 'little' else 0
                        for yy in range(height):
                            for xx in range(width):
                                alpha = 0 if mode == 'empty' else 255 if mode == 'full' else (
                                    (0, 127, 128, 255)[(xx + yy) % 4] if mode == 'threshold' else rng.randrange(256))
                                raw[yy * stride + xx * 4 + alpha_index] = alpha
                        rgb = tuple(rng.randrange(256) for unused in range(3))
                        expected = raw[:]
                        bbox = self.legacy_normalize(expected, width, height, stride, rgb, byteorder)
                        fast = raw[:]
                        self.assertEqual(rendering._normalize_glyph_mask(fast, width, height, stride, rgb,
                                                                        byteorder=byteorder), bbox)
                        self.assertEqual(fast, expected, (byteorder, width, height, padding, mode))
                        slow = raw[:]
                        with patch.object(rendering, '_PillowImage', None):
                            self.assertEqual(rendering._normalize_glyph_mask(slow, width, height, stride, rgb,
                                                                            byteorder=byteorder), bbox)
                        self.assertEqual(slow, expected)

    def test_real_font_fast_and_legacy_glyphs_are_byte_identical(self):
        if rendering._PillowImage is None:
            self.skipTest('Pillow unavailable; C-path equivalence requires Pillow')
        legacy = rendering.Renderer()
        for value, size, color, bold, width, height, align in (
            (rendering.GAME_TITLE, 69, '#fff0a1', True, 720, 102, 'center'),
            ('竹风初行', 24, '#fff5cf', False, 228, 42, 'center'),
            ('左手过肩跳跃  右手小幅摇杆', 24, '#79d8bb', False, 720, 54, 'left'),
            ('金币 0123456789', 18, '#729dfe', True, 253, 28, 'right'),
            ('3', 138, '#fa7154', True, 1440, 204, 'center'),
            ('', 9, '#000000', False, 13, 17, 'center')):
            actual = self.r._text(value, size, color, bold, width, height=height, align=align)
            with patch.object(rendering, '_PillowImage', None):
                expected = legacy._text(value, size, color, bold, width, height=height, align=align)
            actual.flush(); expected.flush()
            self.assertEqual(bytes(actual.get_data()), bytes(expected.get_data()), value)
            self.assertEqual(self.r._last_text_info, legacy._last_text_info)

    def test_text_disabled_isolates_identical_world_and_preserves_decorations(self):
        for state in ('playing', 'menu', 'paused', 'countdown', 'over', 'won', 'transition'):
            scene = rendering.fixture(state=state, dense=True)
            scene['ui'] = {'ready': state == 'menu', 'tracking': False}
            plain = self.draw(scene, draw_text=False)
            self.r.buffer.flush(); world = bytes(self.r.buffer.get_data())
            self.assertEqual(self.r.last_text_layout, [])
            labelled = self.draw(scene)
            self.r.buffer.flush()
            self.assertEqual(bytes(self.r.buffer.get_data()), world)
            self.assertNotEqual(bytes(labelled.get_data()), bytes(plain.get_data()))
        # The disabled option performs no text-cache builds. Neither path
        # retains an additional full-resolution output surface.
        fresh = rendering.Renderer()
        self.draw(rendering.fixture(state='menu'), draw_text=False, renderer=fresh)
        self.assertEqual(fresh.texts.builds, 0)
        self.assertFalse(hasattr(fresh, 'output_buffer'))

    def test_glyphs_are_native_antialiased_pixels_instead_of_enlarged_blocks(self):
        surface = self.r._text(rendering.GAME_TITLE, 69, '#fff0a1', True, 720, height=102)
        surface.flush(); alpha = bytes(surface.get_data())[3::4]
        self.assertEqual(set(alpha), {0, 255})
        width, height = surface.get_width(), surface.get_height()
        # The legacy test name stays available to board validation helpers;
        # the new contract is deliberate nearest-neighbour 2x pixel lettering.
        self.assertEqual(self.r._last_text_info['pixel_grid'], 2)
        self.assertGreaterEqual(self.r._last_text_info['source_font_pixels'], 16)
        self.assertTrue(all(len({alpha[(y + dy) * width + x + dx] for dy in range(2) for dx in range(2)}) == 1
                            for y in range(0, height, 2) for x in range(0, width, 2)))
        self.assertEqual((width, height), (720, 102))

    def test_small_cjk_keeps_detail_and_all_sizes_have_binary_alpha(self):
        for value, size, width, height in (('竹风初行', 24, 228, 42), ('等待手臂入镜', 24, 240, 42),
                                          ('竹风初行', 16, 152, 28),
                                          (rendering.GAME_TITLE, 69, 720, 102), ('100', 120, 300, 180)):
            with self.subTest(text=value, size=size):
                surface = self.r._text(value, size, width=width, height=height)
                surface.flush(); alpha = bytes(surface.get_data())[3::4]
                self.assertEqual(set(alpha), {0, 255})
                info = self.r._last_text_info
                if size < 32 and height < 32:
                    self.assertEqual(info['pixel_grid'], 1)
                    self.assertEqual(info['source_font_pixels'], int(size))
                elif size < 32:
                    self.assertEqual(info['pixel_grid'], 2)
                    self.assertEqual(info['source_font_pixels'], 16)
                elif info['pixel_grid'] == 2:
                    self.assertGreaterEqual(info['source_font_pixels'], 16)
                x, y, w, h = info['ink_box']
                self.assertGreater(w, 0); self.assertGreater(h, 0)
                self.assertGreaterEqual(x, 0); self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x+w, width); self.assertLessEqual(y+h, height)

    def test_cached_pixel_glyphs_stay_identical_across_repeated_frames(self):
        scene = rendering.fixture(state='menu')
        first = self.draw(scene); before = bytes(first.get_data())
        layout = self.r.last_text_layout[:]; builds = self.r.texts.builds
        for unused in range(60):
            result = self.draw(scene)
            self.assertEqual(bytes(result.get_data()), before)
            self.assertEqual(self.r.last_text_layout, layout)
        self.assertEqual(self.r.texts.builds, builds)
        # Moving scenery cannot change the cached UI glyphs or their placement.
        glyphs = {key: bytes(value.get_data()) for key, value in self.r.texts.data.items()}
        for index in range(30):
            scene['elapsed'] = index/30.; scene['camera_x'] = index*3
            self.draw(scene)
            self.assertEqual(self.r.last_text_layout, layout)
            for key, pixels in glyphs.items():
                self.assertEqual(bytes(self.r.texts.data[key].get_data()), pixels)

    def test_tight_box_uses_measured_fit_without_truncating_cjk(self):
        value = '等待手臂入镜'
        for align in ('left', 'center', 'right'):
            surface = self.r._text(value, 69, width=132, height=42, align=align)
            info = self.r._last_text_info
            self.assertEqual(info['pixel_grid'], 1)
            self.assertGreaterEqual(info['source_font_pixels'], 16)
            x, y, w, h = info['ink_box']
            self.assertGreater(w, 0); self.assertGreater(h, 0)
            self.assertGreaterEqual(x, 0); self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x+w, 132); self.assertLessEqual(y+h, 42)
            self.assertTrue(any(key[0] == value for key in self.r.texts.data))
            surface.flush()
            self.assertEqual(set(bytes(surface.get_data())[3::4]), {0, 255})

    def test_native_layout_resolution_centering_and_letterbox_clipping(self):
        scene = rendering.fixture(state='menu')
        for width, height in ((960, 540), (1280, 720), (1920, 1080), (641, 481)):
            plain = self.draw(scene, width, height, draw_text=False)
            labelled = self.draw(scene, width, height)
            ox, oy, outw, outh = self.r.last_layout['content']
            scale = self.r.last_layout['scale']
            title = next(run for run in self.r.last_text_layout if run['text'] == rendering.GAME_TITLE)
            self.assertAlmostEqual(title['requested_font_pixels'], 23 * scale)
            self.assertEqual(title['font_pixels'], title['source_font_pixels']*title['pixel_grid'])
            self.assertLessEqual(abs(title['box'][0] + title['box'][2] / 2 - width / 2), 1)
            for run in self.r.last_text_layout:
                x, y, w, h = run['box']
                self.assertEqual(run['surface_pixels'], [w, h])
                self.assertGreaterEqual(x, math.floor(ox)); self.assertGreaterEqual(y, math.floor(oy))
                self.assertLessEqual(x + w, math.ceil(ox + outw)); self.assertLessEqual(y + h, math.ceil(oy + outh))
                self.assertEqual(x % run['pixel_grid'], 0); self.assertEqual(y % run['pixel_grid'], 0)
                ix, iy, iw, ih = run['ink_box']
                if run['align'] == 'center':
                    self.assertLessEqual(abs(ix+iw/2-w/2), run['pixel_grid']*2)
            if oy:
                # Neither glyphs nor their alpha surfaces may enter letterboxing.
                stride = labelled.get_stride()
                a, b = bytes(labelled.get_data()), bytes(plain.get_data())
                self.assertEqual(a[:math.floor(oy) * stride], b[:math.floor(oy) * stride])
                self.assertEqual(a[math.ceil(oy + outh) * stride:], b[math.ceil(oy + outh) * stride:])
            self.assertEqual(self.r.buffer.get_format(), rendering.cairo.FORMAT_RGB24)
        menu = self.r._ui_text_runs(scene, 'bamboo')
        self.assertTrue(any('准备好后确认开始' == run[0] for run in menu))

    def test_control_text_cache_stays_bounded_and_leaves_title_uncovered(self):
        scene = rendering.fixture()
        scene['ui'] = {'tracking': True, 'control': dict(calibration='ready', valid=True,
                         move=0, jump_ready=True, joystick_dx=0., joystick_dy=0.)}
        for index in range(18):
            scene['ui']['control'].update(move=(index % 3) - 1,
                joystick_dx=index / 100, joystick_dy=index / 200,
                jump_ready=bool(index % 2), calibration_progress=index / 18)
            self.draw(scene)
        self.assertLessEqual(self.r.texts.builds, 9)
        self.assertLessEqual(self.r.texts.bytes(), rendering.TEXT_CACHE_BYTES)
        info = self.r._control_info(scene)
        for value in (info['primary'], info['secondary']):
            self.assertFalse(any(run['text'] == value for run in self.r.last_text_layout))
        scene['state'] = 'menu'
        menu = self.r._ui_text_runs(scene, 'bamboo')
        self.assertFalse(any(run[0] == info['primary'] for run in menu))
        for state in ('menu', 'over', 'won'):
            scene['state'] = state
            words = ' '.join(run[0] for run in self.r._ui_text_runs(scene, 'bamboo'))
            self.assertIn('确认', words)
            self.assertNotIn('双手高举', words)
            self.assertNotIn('快速', words)

    def test_menu_pause_notice_and_countdown_leave_no_residue_on_resume(self):
        scene = rendering.fixture(dense=True)
        target = self.surface(); cr = rendering.cairo.Context(target)
        for state in ('menu', 'paused', 'countdown', 'over', 'won'):
            scene['state'] = state
            scene['ui'] = {'ready': state == 'menu', 'tracking': False}
            self.r.render(cr, scene, 960, 540)
        scene['state'] = 'playing'; scene['ui'] = {'ready': False, 'tracking': True}
        self.r.render(cr, scene, 960, 540); target.flush()
        expected = self.draw(scene, renderer=rendering.Renderer())
        self.assertEqual(bytes(target.get_data()), bytes(expected.get_data()))
        texts = [run['text'] for run in self.r.last_text_layout]
        self.assertNotIn(rendering.GAME_TITLE, texts)
        self.assertNotIn('等待手臂入镜', texts)

    def test_native_glyph_cache_is_bounded_reused_without_output_cache(self):
        scene = rendering.fixture(state='menu')
        self.draw(scene)
        builds = self.r.texts.builds
        self.draw(scene)
        self.assertEqual(self.r.texts.builds, builds)
        self.assertFalse(hasattr(self.r, 'output_buffer'))
        self.assertFalse(hasattr(self.r, 'output_context'))
        for index in range(100):
            self.r._text(f'{index:06d}', 32, width=240, height=64)
        self.assertLessEqual(len(self.r.texts.data), 64)
        self.assertLessEqual(len(self.r._text_info), 64)
        for index in range(8):
            self.r._text(f'{rendering.GAME_TITLE}{index}', 138, width=1440, height=204)
            self.assertLessEqual(self.r.texts.bytes(), rendering.TEXT_CACHE_BYTES)
        for width, height in ((1920, 1080), (641, 481), (1280, 720)):
            self.draw(scene, width, height)
            stats = self.r.stats()
            self.assertNotIn('native_output', stats['cache_entries'])
            self.assertNotIn('native_output', stats['cache_limits'])
            self.assertNotIn('native_output_cache_bytes', stats)
            self.assertLessEqual(stats['native_text_cache_bytes'], rendering.TEXT_CACHE_BYTES)
        # A single unusually large label may be returned for immediate paint,
        # but must not be retained past the cache's byte budget.
        oversized = self.r._text(rendering.GAME_TITLE, 138, width=3072, height=576)
        self.assertGreater(oversized.get_stride()*oversized.get_height(), rendering.TEXT_CACHE_BYTES)
        self.assertLessEqual(self.r.texts.bytes(), rendering.TEXT_CACHE_BYTES)
        self.assertEqual(set(self.r._text_info), set(self.r.texts.data))
        self.assertGreater(self.r._last_text_info['ink_box'][2], 0)
        self.assertIn('native pixel', self.r.stats()['font_backend'])

    def test_direct_native_presentation_preserves_caller_clip_and_context(self):
        class RecordingContext:
            def __init__(self, context):
                self.context, self.sources = context, []

            def __getattr__(self, name):
                return getattr(self.context, name)

            def set_source_surface(self, surface, x, y):
                self.sources.append(surface)
                self.context.set_source_surface(surface, x, y)

        scene = rendering.fixture(state='menu')
        for width, height in ((960, 540), (641, 481)):
            target = self.surface(width, height); cr = rendering.cairo.Context(target)
            cr.set_source_rgb(.3, .1, .6); cr.paint(); target.flush()
            before = bytes(target.get_data())
            cr.rectangle(0, 0, width // 2, height); cr.clip(); cr.translate(7, 11)
            cr.set_operator(rendering.cairo.OPERATOR_OVER)
            matrix, clip = tuple(cr.get_matrix()), tuple(cr.clip_extents())
            recorded = RecordingContext(cr)
            self.r.render(recorded, scene, width, height)
            self.assertEqual(tuple(cr.get_matrix()), matrix)
            self.assertEqual(tuple(cr.clip_extents()), clip)
            self.assertEqual(cr.get_operator(), rendering.cairo.OPERATOR_OVER)
            # The first target source is the original pixel framebuffer;
            # native glyph sources follow directly, without a full-screen copy.
            self.assertIs(recorded.sources[0], self.r.buffer)
            self.assertEqual(len(recorded.sources), 1 + len(self.r.last_text_layout))
            self.assertTrue(all(source in self.r.texts.data.values() for source in recorded.sources[1:]))
            reference = self.surface(width, height); rc = rendering.cairo.Context(reference)
            rc.set_source_rgb(.3, .1, .6); rc.paint(); rc.translate(7, 11)
            rendering.Renderer().render(rc, scene, width, height)
            target.flush(); reference.flush()
            after, expected, stride = bytes(target.get_data()), bytes(reference.get_data()), target.get_stride()
            for row in range(height):
                left, split, right = row * stride, row * stride + (width // 2) * 4, (row + 1) * stride
                self.assertEqual(after[left:split], expected[left:split])
                self.assertEqual(after[split:right], before[split:right])

    def test_complete_group_prevents_world_and_partial_text_from_reaching_target(self):
        scene = rendering.fixture(state='menu')
        target = self.surface(); cr = rendering.cairo.Context(target)
        cr.set_source_rgb(.3, .1, .6); cr.paint(); target.flush()
        before = bytes(target.get_data())
        class ObservedContext:
            def __init__(self, context):
                self.context, self.visible_pixels, self.grouped = context, [], False
            def __getattr__(self, name):
                return getattr(self.context, name)
            def push_group_with_content(self, content):
                self.context.push_group_with_content(content); self.grouped = True
            def pop_group_to_source(self):
                self.context.pop_group_to_source(); self.grouped = False
            def capture(self):
                target.flush(); self.visible_pixels.append((self.grouped, bytes(target.get_data())))
            def paint(self):
                self.context.paint(); self.capture()
            def paint_with_alpha(self, alpha):
                self.context.paint_with_alpha(alpha); self.capture()
        observed = ObservedContext(cr)
        self.r.render(observed, scene, 960, 540)
        intermediate = [pixels for grouped, pixels in observed.visible_pixels if grouped]
        commits = [pixels for grouped, pixels in observed.visible_pixels if not grouped]
        self.assertEqual(len(intermediate), 1+len(self.r.last_text_layout))
        self.assertTrue(all(pixels == before for pixels in intermediate))
        self.assertEqual(len(commits), 1)
        reference = self.surface(); renderer = rendering.Renderer()
        renderer._render_frame(rendering.cairo.Context(reference), scene, 960, 540)
        reference.flush()
        self.assertEqual(commits[0], bytes(reference.get_data()))
        self.assertNotEqual(commits[0], before)
        stats = self.r.stats()
        self.assertEqual(stats['presentation'], 'temporary-opaque-cairo-group-single-paint')
        self.assertEqual(stats['group_submission_ms']['count'], 1)
        self.assertGreaterEqual(stats['render_ms_mean'], stats['group_submission_ms']['mean'])

    def test_incomplete_group_is_discarded_and_context_survives_text_error(self):
        scene = rendering.fixture(state='menu')
        target = self.surface(); cr = rendering.cairo.Context(target)
        cr.set_source_rgb(.3, .1, .6); cr.paint()
        cr.rectangle(0, 0, 480, 540); cr.clip(); cr.translate(7, 11)
        cr.set_operator(rendering.cairo.OPERATOR_OVER)
        matrix, clip, source = tuple(cr.get_matrix()), tuple(cr.clip_extents()), cr.get_source()
        target.flush(); before = bytes(target.get_data())
        class FailingContext:
            def __getattr__(self, name):
                return getattr(cr, name)
            def paint_with_alpha(self, alpha):
                cr.paint_with_alpha(alpha)
                raise RuntimeError('intentional partial glyph failure')
        with self.assertRaisesRegex(RuntimeError, 'intentional partial glyph failure'):
            self.r.render(FailingContext(), scene, 960, 540)
        target.flush()
        self.assertEqual(bytes(target.get_data()), before)
        self.assertEqual(tuple(cr.get_matrix()), matrix)
        self.assertEqual(tuple(cr.clip_extents()), clip)
        self.assertEqual(cr.get_operator(), rendering.cairo.OPERATOR_OVER)
        self.assertEqual(cr.get_source().get_rgba(), source.get_rgba())
        restored_target = cr.get_group_target(); restored_target.flush()
        # cairocffi creates a new Python wrapper for each queried surface;
        # compare the restored target pixels rather than wrapper identity.
        self.assertEqual(bytes(restored_target.get_data()), before)
        self.assertEqual(self.r.frames, 0)
        self.assertEqual(len(self.r.frame_times), 0)
        self.assertEqual(len(self.r.submission_times), 0)
        self.r.render(cr, scene, 960, 540)
        self.assertEqual(self.r.frames, 1)


@unittest.skipIf(rendering is None, 'Native Cairo/cairocffi unavailable')
class PangoNativeTypographyTests(unittest.TestCase):
    def test_pango_rasterizes_absolute_native_font_sizes(self):
        renderer = rendering.Renderer()
        if renderer._pango is None:
            self.skipTest('Pango GI is unavailable on this host; required on the board')
        surface = rendering.cairo.ImageSurface(rendering.cairo.FORMAT_ARGB32, 1920, 1080)
        renderer.render(rendering.cairo.Context(surface), rendering.fixture(state='menu'), 1920, 1080)
        self.assertEqual(renderer.stats()['font_backend'], 'Pango native pixel cached')
        title = next(run for run in renderer.last_text_layout if run['text'] == rendering.GAME_TITLE)
        self.assertEqual(title['font_pixels'], 138)
        self.assertEqual(title['source_font_pixels'], 69)
        self.assertEqual(title['pixel_grid'], 2)
        self.assertEqual(title['surface_pixels'], [1440, 204])
        for glyph in renderer.texts.data.values():
            glyph.flush()
            self.assertTrue(set(bytes(glyph.get_data())[3::4]) <= {0, 255})
        self.assertGreater(renderer.texts.builds, 0)


@unittest.skipIf(rendering is None, 'Native Cairo/cairocffi unavailable')
class NativeOutputPolicyTests(unittest.TestCase):
    class Surface:
        def __init__(self, identity=1, width=960, height=540):
            self.identity, self.width, self.height = identity, width, height
            self.device, self.offset, self.scale = 'display-one', (0., 0.), (1., 1.)
            self.finishes = 0
        def __eq__(self, other):
            return isinstance(other, type(self)) and self.identity == other.identity
        def __hash__(self): return hash(self.identity)
        def get_width(self): return self.width
        def get_height(self): return self.height
        def get_device(self): return self.device
        def get_device_offset(self): return self.offset
        def get_device_scale(self): return self.scale
        def finish(self): self.finishes += 1
        def create_similar(self, content, width, height):
            # A native creator may fall back to another backend. That result
            # must be rejected rather than retained as an ImageSurface buffer.
            return rendering.cairo.ImageSurface(rendering.cairo.FORMAT_RGB24, width, height)

    class Context:
        def __init__(self, surface):
            self.surface = self.group = surface
            self.matrix = (1., 0., 0., 1., 0., 0.)
            self.clip = [(0., 0., float(surface.width), float(surface.height))]
        def get_target(self): return self.surface
        def get_group_target(self): return self.group
        def get_matrix(self): return self.matrix
        def copy_clip_rectangle_list(self): return self.clip

    def setUp(self):
        self.r = rendering.Renderer()
        self.native = patch.object(rendering.cairo, 'XlibSurface', self.Surface, create=True)
        self.native.start(); self.addCleanup(self.native.stop)

    def test_native_binding_uses_cairo_identity_device_and_size(self):
        a, b = self.Surface(), self.Surface()
        self.assertIsNot(a, b)
        key, reason = self.r._native_output_key(self.Context(a), 960, 540)
        same_key, same_reason = self.r._native_output_key(self.Context(b), 960, 540)
        self.assertIsNone(reason); self.assertIsNone(same_reason)
        self.assertEqual(key, same_key)
        b.device = 'display-two'
        device_key, unused = self.r._native_output_key(self.Context(b), 960, 540)
        self.assertNotEqual(key, device_key)
        b.identity = 2
        self.assertNotEqual(key, self.r._native_output_key(self.Context(b), 960, 540)[0])

    def test_native_fast_path_rejects_unverified_or_special_contexts(self):
        for case in ('non-xlib-target', 'nested-cairo-group', 'transformed-target', 'device-offset',
                     'device-scale', 'target-size-mismatch', 'partial-or-complex-clip', 'server-cache-byte-budget'):
            with self.subTest(case=case):
                surface = self.Surface(); context = self.Context(surface); width, height = 960, 540
                if case == 'non-xlib-target': context.surface = object()
                elif case == 'nested-cairo-group': context.group = self.Surface(identity=2)
                elif case == 'transformed-target': context.matrix = (1., 0., 0., 1., .5, 0.)
                elif case == 'device-offset': surface.offset = (1., 0.)
                elif case == 'device-scale': surface.scale = (2., 2.)
                elif case == 'target-size-mismatch': surface.width += 1
                elif case == 'partial-or-complex-clip': context.clip = [(0., 0., 480., 540.)]
                elif case == 'server-cache-byte-budget':
                    width, height = 4096, 2160
                    surface.width, surface.height = width, height
                    context.clip = [(0., 0., float(width), float(height))]
                key, reason = self.r._native_output_key(context, width, height)
                self.assertIsNone(key); self.assertEqual(reason, case)

    def test_similar_image_fallback_is_not_kept_as_native_cache(self):
        surface = self.Surface()
        key, unused = self.r._native_output_key(self.Context(surface), 960, 540)
        self.assertFalse(self.r._prepare_native_output(key))
        self.assertIsNone(self.r._native_output)
        self.assertEqual(self.r.stats()['native_server_cache']['items'], 0)
        self.assertEqual(self.r.stats()['native_server_cache']['estimated_bytes'], 0)
        self.assertEqual(self.r._native_fallback_reason, 'similar-surface-not-xlib')

    def test_release_finishes_only_owned_backplate_and_is_idempotent(self):
        owner, backplate = self.Surface(), self.Surface(identity=2)
        self.r._native_output = backplate
        self.r._native_output_context = object()
        self.r._native_output_binding = (owner, owner.device, 960, 540)
        self.r._native_output_estimated_bytes = 960*540*4
        self.assertTrue(self.r.release_native_output())
        self.assertFalse(self.r.release_native_output())
        self.assertEqual(backplate.finishes, 1); self.assertEqual(owner.finishes, 0)
        self.assertIsNone(self.r._native_output_binding)
        self.assertEqual(self.r.stats()['native_server_cache']['estimated_bytes'], 0)
        self.assertEqual(self.r.stats()['native_server_cache']['releases'], 1)


@unittest.skipIf(rendering is None, 'Native Cairo/cairocffi unavailable')
class NativeXlibOutputTests(unittest.TestCase):
    """Actual server-pixmap checks, enabled only in an owned private Xvfb run."""
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'linux' or os.environ.get('INK_NATIVE_XVFB_TEST') != '1':
            raise unittest.SkipTest('Private Xvfb test needs Linux and INK_NATIVE_XVFB_TEST=1')
        if not hasattr(rendering.cairo, 'XlibSurface'):
            raise unittest.SkipTest('Actual pycairo XlibSurface backend is unavailable')
        import gi
        gi.require_version('Gtk', '3.0'); gi.require_version('GdkX11', '3.0')
        from gi.repository import Gtk, Gdk
        if not Gtk.init_check([])[0]:
            raise unittest.SkipTest('Private Xvfb display is unavailable')
        cls.Gtk, cls.Gdk = Gtk, Gdk

    def setUp(self):
        self.r = rendering.Renderer(); self.windows = []

    def tearDown(self):
        self.r.release_native_output()
        for window, unused_canvas in self.windows:
            window.destroy()
        self.drain()

    def drain(self):
        while self.Gtk.events_pending(): self.Gtk.main_iteration_do(False)
        self.Gdk.Display.get_default().sync()

    def wait_allocation(self, canvas, width, height, timeout=2.):
        expected = (width, height)
        deadline = time.monotonic() + timeout
        while True:
            self.Gdk.Display.get_default().sync()
            while self.Gtk.events_pending() and time.monotonic() < deadline:
                self.Gtk.main_iteration_do(False)
            actual = (canvas.get_allocated_width(), canvas.get_allocated_height())
            if actual == expected:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.assertEqual(actual, expected, 'GTK allocation timeout')
            time.sleep(min(.01, remaining))

    def context(self, width=640, height=360):
        window = self.Gtk.Window(); window.set_default_size(width, height)
        canvas = self.Gtk.DrawingArea(); canvas.set_has_window(True)
        window.add(canvas); window.show_all(); self.windows.append((window, canvas))
        self.drain()
        self.assertEqual((canvas.get_allocated_width(), canvas.get_allocated_height()), (width, height))
        self.assertTrue(canvas.get_window().ensure_native())
        cr = self.Gdk.cairo_create(canvas.get_window())
        self.assertIsInstance(cr.get_target(), rendering.cairo.XlibSurface)
        self.assertIsNotNone(self.r._native_output_key(cr, width, height)[0])
        return cr, window, canvas

    @staticmethod
    def pixels(surface):
        surface.flush(); stream = io.BytesIO(); surface.write_to_png(stream); stream.seek(0)
        image = rendering.cairo.ImageSurface.create_from_png(stream); image.flush()
        data = bytes(image.get_data())
        return data[0::4]+data[1::4]+data[2::4]

    def expected(self, scene, width=640, height=360):
        surface = rendering.cairo.ImageSurface(rendering.cairo.FORMAT_RGB24, width, height)
        rendering.Renderer().render(rendering.cairo.Context(surface), scene, width, height)
        return self.pixels(surface)

    def test_actual_native_cache_reuse_has_complete_pixels_and_no_partial_text(self):
        cr, unused_window, unused_canvas = self.context()
        cr.set_source_rgb(.3, .1, .6); cr.paint()
        before = self.pixels(cr.get_target()); observations = []
        original = self.r._paint_native_text
        def inspect(context, *args):
            self.assertNotEqual(context.get_target(), cr.get_target())
            observations.append(self.pixels(cr.get_target()))
            original(context, *args)
            observations.append(self.pixels(cr.get_target()))
        self.r._paint_native_text = inspect
        for state in ('menu', 'playing', 'paused', 'countdown', 'won', 'over'):
            scene = rendering.fixture(state=state)
            self.r.render(cr, scene, 640, 360)
            self.assertEqual(observations[-2:], [before, before])
            before = self.pixels(cr.get_target())
            self.assertEqual(before, self.expected(scene))
            self.assertIsInstance(self.r._native_output, rendering.cairo.XlibSurface)
        stats = self.r.stats()
        self.assertEqual(stats['presentation'], 'cached-native-xlib-single-paint')
        self.assertEqual(stats['native_server_cache']['builds'], 1)
        self.assertEqual(stats['native_server_cache']['hits'], 5)
        self.assertEqual(stats['native_server_cache']['items'], 1)
        self.assertEqual(stats['native_server_cache']['estimated_bytes'], 640*360*4)
        self.assertEqual(stats['native_submission_ms']['count'], 6)

    def test_actual_window_switch_and_resize_replace_only_one_backplate(self):
        scene = rendering.fixture(state='menu')
        a, unused_a, unused_canvas_a = self.context()
        self.r.render(a, scene, 640, 360); first = self.pixels(a.get_target())
        b, window_b, canvas_b = self.context()
        self.r.render(b, scene, 640, 360)
        self.assertEqual(self.pixels(a.get_target()), first)
        self.assertEqual(self.pixels(b.get_target()), self.expected(scene))
        stats = self.r.stats()['native_server_cache']
        self.assertEqual(stats['builds'], 2); self.assertEqual(stats['releases'], 1)
        window_b.resize(960, 540); self.wait_allocation(canvas_b, 960, 540)
        self.assertEqual((canvas_b.get_allocated_width(), canvas_b.get_allocated_height()), (960, 540))
        resized = self.Gdk.cairo_create(canvas_b.get_window())
        self.r.render(resized, scene, 960, 540)
        self.assertEqual(self.pixels(resized.get_target()), self.expected(scene, 960, 540))
        stats = self.r.stats()['native_server_cache']
        self.assertEqual(stats['builds'], 3); self.assertEqual(stats['releases'], 2)
        self.assertEqual(stats['items'], 1); self.assertEqual(stats['estimated_bytes'], 960*540*4)
        self.assertLessEqual(stats['estimated_bytes'], stats['limit_estimated_bytes'])
        self.assertTrue(self.r.release_native_output()); self.assertFalse(self.r.release_native_output())
        self.assertEqual(self.r.stats()['native_server_cache']['items'], 0)

    def test_actual_aborted_native_frame_preserves_target_then_rebuilds(self):
        cr, unused_window, unused_canvas = self.context()
        scene = rendering.fixture()
        self.r.render(cr, scene, 640, 360); before = self.pixels(cr.get_target())
        matrix, clip, operator = tuple(cr.get_matrix()), tuple(cr.clip_extents()), cr.get_operator()
        original = self.r._paint_native_text
        def fail(context, *args):
            original(context, *args)
            raise RuntimeError('intentional native pre-submit failure')
        self.r._paint_native_text = fail
        with self.assertRaisesRegex(RuntimeError, 'intentional native pre-submit failure'):
            self.r.render(cr, rendering.fixture(state='menu'), 640, 360)
        self.assertEqual(self.pixels(cr.get_target()), before)
        self.assertEqual(tuple(cr.get_matrix()), matrix); self.assertEqual(tuple(cr.clip_extents()), clip)
        self.assertEqual(cr.get_operator(), operator)
        self.assertEqual(self.r.stats()['native_server_cache']['items'], 0)
        self.r._paint_native_text = original
        scene = rendering.fixture(state='paused'); self.r.render(cr, scene, 640, 360)
        self.assertEqual(self.pixels(cr.get_target()), self.expected(scene))
        self.assertEqual(self.r.stats()['native_server_cache']['builds'], 2)


if __name__ == '__main__':
    unittest.main()
