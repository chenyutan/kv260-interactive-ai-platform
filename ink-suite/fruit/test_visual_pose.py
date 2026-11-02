"""Portable pure-math/pixel-layout tests; optional actual engine invariance.

For a detached board copy set INK_VISUAL_TEST_ENGINE to its native/engine.py.
No GTK, camera, sound, file edits or PNG modifications are performed.
"""
import copy
import importlib.util
import math
import os
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visual_pose as pose


class Angles(unittest.TestCase):
    def test_range_endpoints_and_all_31_steps(self):
        self.assertEqual(pose.WHOLE_STEPS,31)
        self.assertEqual(pose.HALF_STEPS,24)
        for step in range(31):
            self.assertEqual(pose.whole_step(pose.whole_angle(step)),step)
        self.assertEqual(pose.whole_step(-100),0)
        self.assertEqual(pose.whole_step(100),30)
        self.assertEqual(pose.whole_step(0),15)
        for bad in (-1,31,.5,True):
            with self.assertRaises(ValueError):pose.whole_angle(bad)

    def test_initial_phase_small_periodic_and_world_delta_scaled(self):
        for birth in (0,.4,math.pi/2,math.pi,3*math.pi/2,17.):
            initial=pose.visual_angle(birth,birth)
            self.assertLessEqual(abs(initial),math.radians(6)+1e-12)
            self.assertAlmostEqual(initial,pose.visual_angle(birth+math.tau,birth+math.tau))
            self.assertAlmostEqual(pose.visual_angle(birth+.2,birth)-initial,.03)

    def test_world_turn_boundary_does_not_wrap_or_change_axis(self):
        birth=math.tau-.02
        before=pose.visual_angle(math.tau-.001,birth)
        after=pose.visual_angle(math.tau+.001,birth)
        self.assertAlmostEqual(after-before,.002*.15)
        self.assertAlmostEqual(pose.visual_angle(100,0),math.radians(30))
        self.assertAlmostEqual(pose.visual_angle(-100,0),-math.radians(30))

    def test_quantization_error_and_monotonic_steps(self):
        values=[math.radians(-30+i*.05) for i in range(1201)]
        steps=[pose.whole_step(a) for a in values]
        self.assertEqual(steps,sorted(steps))
        self.assertTrue(all(abs(pose.whole_angle(pose.whole_step(a))-a)<=math.radians(1)+1e-12 for a in values))

    def test_birth_is_written_once_and_cut_dict_inherits_it(self):
        obj=dict(angle=2.,spin=.8,x=100.,y=220.,vx=5.,vy=-200.,score=10)
        original=copy.deepcopy(obj);rng=random.getstate()
        self.assertEqual(pose.ensure_visual_birth(obj),2.)
        self.assertEqual({k:v for k,v in obj.items() if k!=pose.BIRTH_ANGLE_KEY},original)
        obj['angle']=2.5
        self.assertEqual(pose.ensure_visual_birth(obj),2.)
        event=dict(obj,delta=10,combo=2)
        self.assertEqual(event[pose.BIRTH_ANGLE_KEY],2.)
        self.assertEqual(pose.object_visual_angle(event),pose.object_visual_angle(obj))
        self.assertEqual(random.getstate(),rng)

    def test_invalid_angle_does_not_leave_metadata(self):
        for angle in (float('nan'),float('inf'),None,'bad'):
            obj={'angle':angle}
            with self.assertRaises(ValueError):pose.ensure_visual_birth(obj)
            self.assertNotIn(pose.BIRTH_ANGLE_KEY,obj)


def argb(width,height,alphas,order='little',padding=0):
    stride=width*4+padding
    # Deliberately nonzero RGB and padding: neither may affect the pivot.
    data=bytearray([255]*(stride*height))
    for y in range(height):
        for x in range(width):
            data[y*stride+x*4+(3 if order=='little' else 0)]=alphas[y][x]
    return data,stride


class Pivots(unittest.TestCase):
    def test_endian_padding_and_nonzero_transparent_rgb(self):
        for order in ('little','big'):
            for padding in (0,1,12):
                raw,stride=argb(3,2,[[0,0,0],[64,0,192]],order,padding)
                before=bytes(raw)
                self.assertEqual(pose.alpha_centroid_argb32(raw,3,2,stride,order),(2.,1.5))
                self.assertEqual(bytes(raw),before)

    def test_pixel_centres_and_empty_alpha_fallback(self):
        raw,stride=argb(4,3,[[255]*4 for _ in range(3)])
        self.assertEqual(pose.alpha_centroid_argb32(raw,4,3,stride),(2.,1.5))
        raw,stride=argb(4,3,[[0]*4 for _ in range(3)],padding=9)
        self.assertEqual(pose.alpha_centroid_argb32(raw,4,3,stride),(2.,1.5))
        self.assertEqual(pose.alpha_centroid_argb32(b'',0,3,0),(0.,1.5))
        self.assertEqual(pose.alpha_centroid_argb32(b'',4,0,16),(2.,0.))

    def test_visible_span_needs_no_final_row_padding(self):
        raw,stride=argb(2,2,[[255,0],[0,255]],padding=8)
        self.assertEqual(pose.alpha_centroid_argb32(raw[:-8],2,2,stride),(1.,1.))

    def test_invalid_shapes_and_buffers_rejected(self):
        for args in ((b'',1,1,4),(bytes(32),2,2,7),(bytes(32),-1,2,8),
                     (bytes(32),2,2,-8),(bytes(32),2.5,2,12)):
            with self.assertRaises(ValueError):pose.alpha_centroid_argb32(*args)
        with self.assertRaises(ValueError):pose.alpha_centroid_argb32(bytes(4),1,1,4,'other')

    def test_cache_computes_once_per_original_surface_until_clear(self):
        class Surface:
            def __init__(self):self.flushes=0;self.raw,self.stride=argb(2,1,[[255,0]])
            def get_format(self):return 0
            def flush(self):self.flushes+=1
            def get_data(self):return self.raw
            def get_width(self):return 2
            def get_height(self):return 1
            def get_stride(self):return self.stride
        cache=pose.SourcePivotCache();source=Surface()
        self.assertEqual(cache.get(source),(.5,.5))
        for _ in range(50):self.assertEqual(cache.get(source),(.5,.5))
        self.assertEqual(source.flushes,1);self.assertEqual(len(cache),1)
        other=Surface();cache.get(other)
        self.assertEqual(len(cache),2)
        cache.clear();cache.get(source)
        self.assertEqual(source.flushes,2);self.assertEqual(len(cache),1)


def find_engine():
    if os.environ.get('INK_VISUAL_TEST_ENGINE'):
        return Path(os.environ['INK_VISUAL_TEST_ENGINE'])
    for parent in Path(__file__).resolve().parents:
        candidate=parent/'outputs/FruitSlice/native/engine.py'
        if candidate.is_file():return candidate
    return None


ENGINE=find_engine()
@unittest.skipUnless(ENGINE and ENGINE.is_file(),'actual engine.py not available; set INK_VISUAL_TEST_ENGINE')
class ActualEngineInvariance(unittest.TestCase):
    def test_seed_trajectories_rng_score_and_real_cut_metadata(self):
        spec=importlib.util.spec_from_file_location('visual_pose_engine',ENGINE)
        engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
        baseline,candidate=engine.Game(),engine.Game()
        for g in (baseline,candidate):g.start(91);g.advance(3.1)
        for _ in range(40):
            baseline.advance(.04);candidate.advance(.04)
            for obj in candidate.objects:pose.ensure_visual_birth(obj);pose.object_visual_angle(obj)
            self.assertEqual(baseline.random.getstate(),candidate.random.getstate())
            self.assertEqual(baseline.objects,[{k:v for k,v in obj.items() if k!=pose.BIRTH_ANGLE_KEY} for obj in candidate.objects])
            self.assertEqual((baseline.score,baseline.combo,baseline.elapsed),
                             (candidate.score,candidate.combo,candidate.elapsed))
        self.assertTrue(candidate.objects)
        original_object=candidate.objects[0]
        before_angle=pose.object_visual_angle(original_object)
        baseline.cut(baseline.objects[0]);candidate.cut(original_object)
        event=candidate.events[-1]
        self.assertEqual(event[pose.BIRTH_ANGLE_KEY],original_object[pose.BIRTH_ANGLE_KEY])
        self.assertEqual(pose.object_visual_angle(event),before_angle)
        self.assertEqual(baseline.events[-1],{k:v for k,v in event.items() if k!=pose.BIRTH_ANGLE_KEY})
        self.assertEqual((baseline.score,baseline.combo,baseline.fruits_cut,baseline.bombs_hit),
                         (candidate.score,candidate.combo,candidate.fruits_cut,candidate.bombs_hit))
        self.assertEqual(baseline.random.getstate(),candidate.random.getstate())


if __name__=='__main__':unittest.main(verbosity=2)
